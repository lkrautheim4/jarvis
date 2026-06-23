#!/usr/bin/env python3
"""
trading_brain/tripwire.py  --  READ-ONLY ledger integrity tripwire (cron)

Runs the same reconciliation logic we validated by hand. Opens jarvis_memory.db
with query_only=ON (cannot write to it). If any graded kalshi_bets row has a
pnl that is FABRICATED (doesn't match a real Kalshi fill) or UNDERIVABLE
(entry price 0/null, stake missing) -> fires ONE Telegram alert. Silent when
clean. This is the instrument that would have caught the 35 June phantoms.

Token/chat are pulled from jarvis_secrets the same way the bots do. This file
contains no secret. It writes nothing to the live DB.

CRON (every 15 min):
  */15 * * * * cd /root/jarvis && /usr/bin/python3 trading_brain/tripwire.py >> /root/jarvis/trading_brain/tripwire.log 2>&1
"""

import sqlite3
import sys
sys.path.insert(0, "/root/jarvis")  # 2026-06-22: cron runs from trading_brain/, jarvis_secrets lives in /root/jarvis

DB = "/root/jarvis/jarvis_memory.db"
TOL = 0.05
WIN = {"win", "won"}
LOSS = {"loss", "lost"}


def classify(r):
    res = (r["result"] or "").strip().lower()
    if not res:
        return "UNGRADED"
    if r["pnl"] is None:
        return "HONEST_NULL"
    bet = (r["bet"] or "").strip().lower()
    e = r["yes_price"] if bet.startswith("y") else r["no_price"]
    d = r["dollars"]
    try:
        e = float(e) if e is not None else None
        d = float(d) if d is not None else None
    except (TypeError, ValueError):
        return "UNDERIVABLE"
    if not e:
        return "UNDERIVABLE"
    if not d:
        # Per-contract auto bet (no dollar stake). pnl is priced off entry alone:
        # WIN pays (1 - e), LOSS costs (-e).
        exp = (1.0 - e) if res in WIN else (-e if res in LOSS else None)
    else:
        exp = d * (1.0 - e) / e if res in WIN else (-d if res in LOSS else None)
    if exp is None:
        return "UNDERIVABLE"
    return "REAL" if abs(float(r["pnl"]) - exp) <= TOL else "FABRICATED"


def send_telegram(msg):
    import requests, sys
    if "/root/jarvis" not in sys.path:
        sys.path.insert(0, "/root/jarvis")
    import jarvis_secrets
    token = jarvis_secrets.TG_TOKEN_TRADER
    chat = jarvis_secrets.TG_CHAT_ID
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": msg[:4000]}, timeout=5)


def main():
    con = sqlite3.connect(DB, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON;")
    bad_fab, bad_und = [], []
    try:
        for r in con.execute(
                "SELECT id, bet, yes_price, no_price, dollars, result, pnl "
                "FROM kalshi_bets"):
            label = classify(r)
            if label == "FABRICATED":
                bad_fab.append(r["id"])
            elif label == "UNDERIVABLE":
                bad_und.append(r["id"])
    finally:
        con.close()

    if not bad_fab and not bad_und:
        print("tripwire OK: ledger clean")
        return 0

    lines = ["\U0001F6A8 JARVIS LEDGER TRIPWIRE", "kalshi_bets pnl integrity FAILED:"]
    if bad_fab:
        lines.append(f"FABRICATED ({len(bad_fab)}): ids {bad_fab[:20]}")
    if bad_und:
        lines.append(f"UNDERIVABLE ({len(bad_und)}): ids {bad_und[:20]}")
    lines.append("These rows have pnl not backed by a real fill. Investigate before trusting any P&L.")
    msg = "\n".join(lines)
    print(msg)
    try:
        send_telegram(msg)
    except Exception as e:
        print(f"tripwire: alert detected but Telegram send failed: {e}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
