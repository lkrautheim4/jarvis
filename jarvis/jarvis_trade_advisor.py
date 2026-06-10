#!/usr/bin/env python3
"""
JARVIS Trade Advisor Bot
Telegram /ask command -> Claude API -> reply + log to jarvis_memory.db
"""

import re
import sqlite3
import logging
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import jarvis_secrets

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [TRADE_ADVISOR] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = __import__("jarvis_secrets").TG_TOKEN_TRADER
CHAT_ID        = 7534553840
ANTHROPIC_KEY  = jarvis_secrets.CLAUDE_API_KEY
DB_PATH        = "/root/jarvis/jarvis_memory.db"
EDT            = ZoneInfo("America/New_York")
BOT_NAME       = "jarvis_trade_advisor"

SYSTEM_PROMPT = """You are JARVIS, Lenny's personal AI trading advisor. Be direct and concise — no hedging, no warm-up sentences. Lead with the verdict.

Lenny's trading rules you must apply in every analysis:
- Kalshi edges: YES bets = 81% WR, NO bets = 74% WR on proven edges
- Prime hours EDT: 9am, 10am, 2pm, 5pm. Avoid: 11am, 6pm, 7pm+
- Kalshi bankroll: $500, max single bet $25, Kelly sizing using actual market price as odds
- Confidence floor: 65% minimum — below that, pass
- Max 3 consecutive losses = stop for the day
- Never bet 15-min Kalshi windows
- Options brain scans 16 tickers with regime gating
- PROFIT MODE = aggressive, take high-EV trades
- PROTECTION MODE = defensive, only A+ setups, smaller size
- YES needs $300+ buffer at 89% WR; NO primary edge at 74% WR
- Kelly v2 uses actual Kalshi market price as odds
- Paper options portfolio at +$1,157; real Kalshi bankroll $500

Tag confidence on key claims: [certain] hard evidence, [likely] strong inference, [guessing] filling gaps.
Verdict first. Keep it tight. If it's a NO-GO say so immediately and why."""

# ── DB ────────────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_consultations (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        TEXT NOT NULL,
            ticker    TEXT,
            question  TEXT NOT NULL,
            response  TEXT NOT NULL,
            bot       TEXT DEFAULT 'jarvis_trade_advisor'
        )
    """)
    conn.commit()
    conn.close()
    log.info("DB ready — trade_consultations table confirmed")

def log_consultation(question: str, response: str, ticker: str = None):
    ts = datetime.now(EDT).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO trade_consultations (ts, ticker, question, response, bot) VALUES (?,?,?,?,?)",
        (ts, ticker, question, response, BOT_NAME)
    )
    conn.commit()
    conn.close()
    log.info(f"Logged consultation — ticker={ticker}")

def inject_heartbeat():
    ts = datetime.now(EDT).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO bot_heartbeats (bot_name, last_seen, status) VALUES (?,?,?)",
        (BOT_NAME, ts, "running")
    )
    conn.commit()
    conn.close()

# ── Ticker extraction ─────────────────────────────────────────────────────────
def extract_ticker(text: str):
    """Best-effort ticker parse from free text."""
    known = ["SPY","QQQ","AAPL","TSLA","NVDA","AMD","META","GOOGL","AMZN",
             "MSFT","IWM","SOXL","TQQQ","BTC","ETH","SPX","VIX","GLD","TLT"]
    upper = text.upper()
    for t in known:
        if t in upper:
            return t
    match = re.search(r'\b([A-Z]{2,5})\b', text.upper())
    return match.group(1) if match else None

# ── Claude API ────────────────────────────────────────────────────────────────
def ask_claude(question: str) -> str:
    if not ANTHROPIC_KEY:
        return "ERROR: ANTHROPIC_API_KEY not set in environment."
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 1000,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": question}]
            },
            timeout=30
        )
        data = resp.json()
        if "content" in data:
            return "".join(b.get("text","") for b in data["content"]).strip()
        return f"API error: {data.get('error',{}).get('message','unknown')}"
    except Exception as e:
        return f"Request failed: {e}"

# ── Telegram helpers ──────────────────────────────────────────────────────────
TG_BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

def tg_send(text: str, chat_id: int = CHAT_ID):
    try:
        requests.post(f"{TG_BASE}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }, timeout=10)
    except Exception as e:
        log.error(f"Telegram send failed: {e}")

def tg_get_updates(offset: int):
    try:
        r = requests.get(f"{TG_BASE}/getUpdates", params={
            "offset": offset, "timeout": 20
        }, timeout=25)
        return r.json().get("result", [])
    except Exception:
        return []

# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    init_db()
    inject_heartbeat()
    log.info("JARVIS Trade Advisor online — waiting for /ask commands")
    tg_send("🟢 *JARVIS Trade Advisor online*\nSend `/ask <your question>` to consult me on any trade.")

    offset = 0
    hb_counter = 0

    while True:
        updates = tg_get_updates(offset)

        for update in updates:
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            text = msg.get("text", "").strip()
            chat_id = msg.get("chat", {}).get("id")

            if not text or chat_id != CHAT_ID:
                continue

            # /ask command
            if text.lower().startswith("/ask"):
                question = text[4:].strip()
                if not question:
                    tg_send("Usage: `/ask should I take this AAPL put at $2.10 with IV rank at 45?`")
                    continue

                ticker = extract_ticker(question)
                log.info(f"Question received — ticker={ticker}: {question[:80]}")
                tg_send(f"⏳ Analyzing{' $' + ticker if ticker else ''}...")

                answer = ask_claude(question)
                log_consultation(question, answer, ticker)

                header = f"*JARVIS // TRADE ADVISOR*{' — $' + ticker if ticker else ''}\n{'─'*30}\n"
                tg_send(header + answer)

            # /tradelog — last 5 consultations
            elif text.lower() == "/tradelog":
                conn = sqlite3.connect(DB_PATH)
                rows = conn.execute(
                    "SELECT ts, ticker, question FROM trade_consultations ORDER BY id DESC LIMIT 5"
                ).fetchall()
                conn.close()
                if not rows:
                    tg_send("No consultations logged yet.")
                else:
                    lines = ["*Last 5 trade consultations:*"]
                    for ts, ticker, q in rows:
                        t = ts[11:16]
                        lines.append(f"`{t}` {'$'+ticker if ticker else '??'} — {q[:50]}...")
                    tg_send("\n".join(lines))

            # /advisorhelp
            elif text.lower() == "/advisorhelp":
                tg_send(
                    "*JARVIS Trade Advisor commands:*\n"
                    "`/ask <question>` — consult JARVIS on any trade\n"
                    "`/tradelog` — last 5 questions you asked\n"
                    "`/advisorhelp` — this menu\n\n"
                    "Examples:\n"
                    "`/ask is this a good time for a SPY put given PROTECTION mode?`\n"
                    "`/ask NVDA calls expiring Friday, IV rank 62, my thesis is earnings run`\n"
                    "`/ask Kalshi YES on BTC above 70k at 68% odds, 2pm window, good bet?`"
                )

        # heartbeat every ~5 min (150 x 2s loops)
        hb_counter += 1
        if hb_counter >= 150:
            inject_heartbeat()
            hb_counter = 0

if __name__ == "__main__":
    main()
