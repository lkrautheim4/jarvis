#!/usr/bin/env python3
"""jarvis_vision_capture.py

Screenshot -> Vision -> confirm -> log_manual_option.

Standalone Telegram long-poller. Listens for PHOTO messages on the trader bot,
extracts option-fill fields with Claude vision, replies with what it read, and
logs only after you reply LOG. Does NOT edit jarvis_master.py.

Run:  nohup python3 jarvis_vision_capture.py >> vision_capture.log 2>&1 &

ONE thing to confirm before this works end to end: the _save() adapter below
must match your real jarvis_memory_db.log_manual_option() signature.
"""
import json
import time
import base64
import datetime
import requests

secrets = __import__("jarvis_secrets")

# --- config (confirm these attribute names exist in jarvis_secrets) ----------
TG_TOKEN = secrets.TG_TOKEN_TRADER
TG_CHAT = str(secrets.TG_CHAT_ID)
ANTHROPIC_KEY = secrets.CLAUDE_API_KEY              # canonical name on the box
MODEL = "claude-sonnet-4-6"
API = "https://api.anthropic.com/v1/messages"
TG = f"https://api.telegram.org/bot{TG_TOKEN}"

EXTRACT_PROMPT = (
    "You are reading a brokerage option order-fill screenshot (e.g. Webull). "
    "Return ONLY a JSON object, no prose, no markdown fences, with keys: "
    "ticker (str), right (CALL or PUT), strike (number), expiry (YYYY-MM-DD), "
    "side (DEBIT or CREDIT), contracts (int), fill_price (per-contract premium, number), "
    "confidence (0..1). Use null for any field you cannot read clearly."
)

PENDING = {}  # chat_id -> last extracted dict, awaiting LOG/CANCEL


# --- map extracted fields -> exact log_manual_option kwargs ------------------
def map_to_db(f):
    """Translate vision fields into the real log_manual_option() signature:
    (symbol, strategy, direction, strike, dte, premium, contracts, ...)."""
    dte = None
    exp = None
    raw = str(f.get("expiry") or "").strip()
    for fmt in ("%Y-%m-%d", "%d %b %y", "%d %b %Y", "%b %d %Y", "%b %d, %Y", "%m/%d/%Y", "%m/%d/%y"):
        try:
            exp = datetime.datetime.strptime(raw, fmt).date(); break
        except Exception:
            continue
    if exp:
        dte = (exp - datetime.date.today()).days
    right = (f.get("right") or "").lower()   # call / put
    side = (f.get("side") or "").upper()     # DEBIT / CREDIT
    action = "buy" if side == "DEBIT" else "sell"
    strategy = f"{right}_{action}"           # call_buy / put_sell -- matches existing vocab
    return {
        "symbol": f.get("ticker"),
        "strategy": strategy,
        "direction": "",              # existing rows leave this blank; buy/sell lives in strategy
        "strike": f.get("strike"),
        "dte": dte,
        "premium": f.get("fill_price"),
        "contracts": f.get("contracts"),
        "screenshot": None,
    }


def _save(m):
    """Write the confirmed OPEN. Keys already match the real signature."""
    import jarvis_memory_db as db
    return db.log_manual_option(**m)


# --- telegram helpers --------------------------------------------------------
def tg_send(text):
    requests.post(f"{TG}/sendMessage", json={"chat_id": TG_CHAT, "text": text}, timeout=20)


def tg_file_bytes(file_id):
    r = requests.get(f"{TG}/getFile", params={"file_id": file_id}, timeout=20).json()
    path = r["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{TG_TOKEN}/{path}"
    return requests.get(url, timeout=30).content


# --- vision extraction -------------------------------------------------------
def parse_json(text):
    """Strip optional ```json fences and parse."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
    return json.loads(t.strip())


def vision_extract(img_bytes):
    b64 = base64.b64encode(img_bytes).decode()
    body = {
        "model": MODEL,
        "max_tokens": 400,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": EXTRACT_PROMPT},
            ],
        }],
    }
    h = {"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
         "content-type": "application/json"}
    r = requests.post(API, headers=h, json=body, timeout=60).json()
    text = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
    return parse_json(text)


def confirm_text(m, conf=None):
    lines = ["Will log this option OPEN:"]
    for k in ("symbol", "strategy", "direction", "strike", "dte", "premium", "contracts"):
        lines.append(f"  {k:<10} {m.get(k)}")
    if conf is not None:
        lines.append(f"  (read confidence {conf})")
    lines.append("Reply LOG to write, or CANCEL to drop.")
    return "\n".join(lines)


# --- main loop ---------------------------------------------------------------
def main():
    offset = None
    tg_send("vision capture online: send a fill screenshot.")
    while True:
        try:
            r = requests.get(f"{TG}/getUpdates",
                             params={"offset": offset, "timeout": 30}, timeout=40).json()
        except Exception:
            time.sleep(3)
            continue
        for u in r.get("result", []):
            offset = u["update_id"] + 1
            msg = u.get("message", {})
            chat = str(msg.get("chat", {}).get("id", ""))
            if chat != TG_CHAT:
                continue
            text = (msg.get("text") or "").strip().upper()
            if "photo" in msg:
                try:
                    img = tg_file_bytes(msg["photo"][-1]["file_id"])
                    fields = vision_extract(img)
                    m = map_to_db(fields)
                    PENDING[chat] = m
                    tg_send(confirm_text(m, fields.get("confidence")))
                    if m.get("dte") is None:
                        tg_send("dte unreadable. send 'DTE <days>' to set it, then LOG.")
                except Exception as e:
                    tg_send(f"extract failed: {e}")
            elif text.startswith("DTE ") and chat in PENDING:
                try:
                    PENDING[chat]["dte"] = int(text.split()[1])
                    tg_send(confirm_text(PENDING[chat]))
                except Exception:
                    tg_send("bad DTE. format: DTE 4")
            elif text in ("LOG", "YES") and chat in PENDING:
                m = PENDING[chat]
                if m.get("dte") is None:
                    tg_send("blocked: dte is null. send 'DTE <days>' before LOG.")
                    continue
                try:
                    res = _save(PENDING.pop(chat))
                    tg_send(f"logged: {res}")
                except Exception as e:
                    tg_send(f"save failed: {e}")
            elif text in ("CANCEL", "NO") and chat in PENDING:
                PENDING.pop(chat)
                tg_send("dropped.")


if __name__ == "__main__":
    main()
