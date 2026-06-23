#!/usr/bin/env python3
"""jarvis_vision_capture.py

Screenshot -> Vision -> confirm -> log_manual_option (entry) or close_manual_option (exit).

Standalone Telegram long-poller. Listens for PHOTO messages on the trader bot,
extracts option-fill fields with Claude vision, replies with what it read, and
logs only after you reply LOG (entry) or CLOSE+CONFIRM (exit).

Reply keywords:
  LOG      — write extracted fields as a new OPEN entry
  CLOSE    — match exit screenshot against open trades, show P/L, require CONFIRM
  CONFIRM  — execute the matched close (only valid after CLOSE shows a match)
  DTE <n>  — override computed DTE before LOG
  CANCEL   — drop current pending state (works in both entry and exit flows)

Run:  nohup python3 jarvis_vision_capture.py >> vision_capture.log 2>&1 &
"""
import json
import fcntl, os, sys
import time
import base64
import datetime
import requests

secrets = __import__("jarvis_secrets")
import jarvis_memory_db as db

# --- config ------------------------------------------------------------------
TG_TOKEN = secrets.TG_TOKEN_TRADER
TG_CHAT = "7534553840"  # screen_shot_options_bot chat
ANTHROPIC_KEY = secrets.CLAUDE_API_KEY
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

PENDING = {}      # chat_id -> extracted entry fields, awaiting LOG/CLOSE/CANCEL
CLOSE_STATE = {}  # chat_id -> close flow state, awaiting CONFIRM/pick/CANCEL
#   CLOSE_STATE structure:
#   { "exit_premium": float,
#     "trade": dict,          # present when single match selected → awaiting CONFIRM
#     "candidates": [...] }   # present when multiple matches → awaiting number pick


# --- map extracted fields -> exact log_manual_option kwargs ------------------
def map_to_db(f):
    """Translate vision fields into log_manual_option() kwargs."""
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
    right = (f.get("right") or "").lower()           # call / put
    side = (f.get("side") or "").upper()              # DEBIT / CREDIT
    action = "buy" if side == "DEBIT" else "sell"
    strategy = f"{right}_{action}"                    # e.g. put_buy, call_sell
    return {
        "symbol":    f.get("ticker"),
        "strategy":  strategy,
        "direction": side,                            # DEBIT or CREDIT (drives P/L sign)
        "strike":    f.get("strike"),
        "dte":       dte,
        "expiry":    exp.isoformat() if exp else None,  # for exit matching
        "premium":   f.get("fill_price"),
        "contracts": f.get("contracts"),
        "screenshot": None,
    }


def _save(m):
    """Write confirmed OPEN, then confirm the row actually persisted."""
    rid = db.log_manual_option(**m)
    con = db._mc_sq.connect(db._MC_DB, timeout=10)
    row = con.execute("SELECT id FROM options_trades WHERE id=?", (rid,)).fetchone()
    con.close()
    if not row:
        raise RuntimeError("write claimed id %s but row not found -- NOT logged" % rid)
    return rid


# --- close-flow helpers ------------------------------------------------------
def _find_open_matches(symbol, strike, expiry=None):
    """Query DB for open real manual trades matching symbol+strike[+expiry]."""
    con = db._mc_sq.connect(db._MC_DB, timeout=10)
    con.row_factory = db._mc_sq.Row
    try:
        cols = ("id, symbol, strike, expiry, premium, contracts, direction, "
                "strategy, entry_ts")
        if expiry:
            rows = con.execute(
                f"SELECT {cols} FROM options_trades "
                "WHERE symbol=? AND strike=? AND expiry=? "
                "AND status='open' AND is_real=1 ORDER BY ts DESC",
                (symbol, float(strike), expiry)).fetchall()
        else:
            rows = con.execute(
                f"SELECT {cols} FROM options_trades "
                "WHERE symbol=? AND strike=? "
                "AND status='open' AND is_real=1 ORDER BY ts DESC",
                (symbol, float(strike))).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def _compute_pnl(trade, exit_premium):
    """Compute P/L preview (same formula as close_manual_option)."""
    prem = float(trade["premium"] or 0)
    n = int(trade["contracts"] or 1)
    gross = (float(exit_premium) - prem) * n * 100.0
    direction = (trade.get("direction") or "").upper()
    pnl = gross if direction == "DEBIT" else -gross
    result = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "SCRATCH")
    return round(pnl, 2), result


def _handle_close(chat, m):
    """Start the close flow: match exit fields against open trades."""
    symbol = m.get("symbol")
    strike = m.get("strike")
    exit_premium = m.get("premium")
    expiry = m.get("expiry")

    if not symbol or strike is None:
        tg_send("Cannot match: symbol or strike not readable from screenshot.")
        return
    if exit_premium is None:
        tg_send("Cannot match: exit fill price not readable from screenshot.")
        return

    try:
        candidates = _find_open_matches(symbol, strike, expiry)
        if not candidates and expiry:
            # Retry without expiry in case it wasn't set on entry
            candidates = _find_open_matches(symbol, strike, None)
    except Exception as e:
        tg_send(f"match query failed: {e}")
        return

    if not candidates:
        tg_send(f"No open match for {symbol} ${strike}. Check symbol/strike.")
        return

    if len(candidates) == 1:
        trade = candidates[0]
        pnl, result = _compute_pnl(trade, exit_premium)
        CLOSE_STATE[chat] = {"trade": trade, "exit_premium": exit_premium}
        tg_send(
            f"Match: #{trade['id']} {trade['symbol']} ${trade['strike']}\n"
            f"  entry premium: {trade['premium']}\n"
            f"  exit  premium: {exit_premium}\n"
            f"  contracts: {trade['contracts']}\n"
            f"  P/L: {result} ${pnl:+.2f}\n"
            f"Reply CONFIRM to close or CANCEL to abort."
        )
    else:
        CLOSE_STATE[chat] = {"candidates": candidates, "exit_premium": exit_premium}
        lines = [f"Multiple opens match {symbol} ${strike}:"]
        for i, t in enumerate(candidates, 1):
            entry_date = (t.get("entry_ts") or "unknown")[:10]
            lines.append(f"  {i}. #{t['id']} entry={t['premium']} opened={entry_date}")
        lines.append("Reply with the number to pick, or CANCEL to abort.")
        tg_send("\n".join(lines))


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
    for k in ("symbol", "strategy", "direction", "strike", "expiry", "dte", "premium", "contracts"):
        lines.append(f"  {k:<10} {m.get(k)}")
    if conf is not None:
        lines.append(f"  (read confidence {conf})")
    lines.append("Reply LOG to record OPEN, CLOSE to match EXIT, or CANCEL to drop.")
    return "\n".join(lines)


# --- main loop ---------------------------------------------------------------
_LOCK_FH = None
def _acquire_singleton():
    global _LOCK_FH
    _LOCK_FH = open("/root/jarvis/vision_capture.lock", "w")
    try:
        fcntl.flock(_LOCK_FH, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another vision_capture instance holds the lock -- exiting")
        sys.exit(0)
    _LOCK_FH.write(str(os.getpid()))
    _LOCK_FH.flush()
    open("/root/jarvis/vision_capture.pid", "w").write(str(os.getpid()))

def main():
    _acquire_singleton()
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
                CLOSE_STATE.pop(chat, None)  # clear stale close flow on new screenshot
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

            elif text == "CLOSE" and chat in PENDING:
                m = PENDING.pop(chat)
                _handle_close(chat, m)

            elif text == "CONFIRM" and chat in CLOSE_STATE and "trade" in CLOSE_STATE.get(chat, {}):
                state = CLOSE_STATE.pop(chat)
                try:
                    r = db.close_manual_option(state["trade"]["id"], state["exit_premium"])
                    tg_send(f"closed: #{r['id']} {r['result']} P/L ${r['pnl']:+.2f}")
                except Exception as e:
                    tg_send(f"close failed: {e}")

            elif text.isdigit() and chat in CLOSE_STATE and "candidates" in CLOSE_STATE.get(chat, {}):
                state = CLOSE_STATE[chat]
                candidates = state["candidates"]
                idx = int(text) - 1
                if 0 <= idx < len(candidates):
                    trade = candidates[idx]
                    pnl, result = _compute_pnl(trade, state["exit_premium"])
                    state["trade"] = trade
                    del state["candidates"]
                    tg_send(
                        f"Selected: #{trade['id']} {trade['symbol']} ${trade['strike']}\n"
                        f"  P/L: {result} ${pnl:+.2f}\n"
                        f"Reply CONFIRM to close or CANCEL to abort."
                    )
                else:
                    tg_send(f"Invalid pick. Choose 1-{len(candidates)}.")

            elif text in ("CANCEL", "NO"):
                if chat in CLOSE_STATE:
                    CLOSE_STATE.pop(chat)
                    tg_send("close cancelled.")
                elif chat in PENDING:
                    PENDING.pop(chat)
                    tg_send("dropped.")


if __name__ == "__main__":
    main()
