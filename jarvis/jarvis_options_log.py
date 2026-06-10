#!/usr/bin/env python3
"""
JARVIS Options Trade Logger
Parses Telegram commands and logs options trades to SQLite

Command format:
  TRADE WIN NVDA CALL 225 6/5 entry 6/2 exit +190
  TRADE LOSS AAPL PUT 180 6/20 entry 5/30 exit -85
  TRADES          → show recent trades
  TRADE STATS     → show win rate and P&L summary
"""

import sqlite3
import re
import os
from datetime import datetime, date

DB_PATH = "/root/jarvis/jarvis_memory.db"
LOG_PATH = "/root/jarvis/jarvis_options_log.log"


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_PATH, "a") as f:
        f.write(f"[{ts}] {msg}\n")
    print(f"[{ts}] {msg}")


def init_db():
    """Create options_trades table if not exists"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS options_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,        -- CALL or PUT
            strike REAL NOT NULL,
            expiry TEXT NOT NULL,           -- MM/DD format or full date
            entry_date TEXT,
            exit_date TEXT,
            result TEXT NOT NULL,           -- WIN or LOSS
            pnl REAL NOT NULL,              -- positive or negative dollar amount
            dte_at_entry INTEGER,           -- days to expiry at entry
            notes TEXT,
            logged_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()
    log("DB initialized - options_trades table ready")


def parse_trade_command(text):
    """
    Parse: TRADE WIN NVDA CALL 225 6/5 entry 6/2 exit +190
    Returns dict or None if parse fails
    """
    text = text.strip().upper()

    # Remove TRADE prefix
    if text.startswith("TRADE"):
        text = text[5:].strip()
    else:
        return None

    # STATS or list command
    if text.startswith("STATS") or text.startswith("SUMMARY"):
        return {"command": "stats"}
    if text == "" or text == "LIST" or text == "HISTORY":
        return {"command": "list"}

    # Pattern: WIN/LOSS TICKER CALL/PUT STRIKE EXPIRY [entry DATE] [exit DATE] +/-PNL
    pattern = r"""
        (WIN|LOSS)\s+           # result
        ([A-Z]{1,5})\s+         # ticker
        (CALL|PUT)\s+           # direction
        (\d+\.?\d*)\s+          # strike
        (\d+/\d+)\s+            # expiry MM/DD
        (?:ENTRY\s+(\d+/\d+)\s+)?  # optional entry date
        (?:EXIT\s+(\d+/\d+)\s+)?   # optional exit date
        ([+-]\d+\.?\d*)         # P&L
    """
    m = re.search(pattern, text, re.VERBOSE)
    if not m:
        return None

    result, ticker, direction, strike, expiry, entry_date, exit_date, pnl_str = m.groups()

    # Calculate DTE at entry
    dte = None
    try:
        today = date.today()
        exp_parts = expiry.split("/")
        exp_date = date(today.year, int(exp_parts[0]), int(exp_parts[1]))
        if entry_date:
            ent_parts = entry_date.split("/")
            ent = date(today.year, int(ent_parts[0]), int(ent_parts[1]))
            dte = (exp_date - ent).days
        else:
            dte = (exp_date - today).days
    except Exception:
        pass

    return {
        "command": "log",
        "ticker": ticker,
        "direction": direction,
        "strike": float(strike),
        "expiry": expiry,
        "entry_date": entry_date or date.today().strftime("%m/%d"),
        "exit_date": exit_date or date.today().strftime("%m/%d"),
        "result": result,
        "pnl": float(pnl_str),
        "dte_at_entry": dte
    }


def log_trade(trade):
    """Insert trade into DB"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        INSERT INTO options_trades
        (ticker, direction, strike, expiry, entry_date, exit_date, result, pnl, dte_at_entry)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade["ticker"], trade["direction"], trade["strike"],
        trade["expiry"], trade["entry_date"], trade["exit_date"],
        trade["result"], trade["pnl"], trade.get("dte_at_entry")
    ))
    conn.commit()
    conn.close()
    log(f"Logged trade: {trade['result']} {trade['ticker']} {trade['direction']} ${trade['strike']} exp {trade['expiry']} P&L ${trade['pnl']}")


def get_stats():
    """Return win rate and P&L summary"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM options_trades ORDER BY logged_at DESC").fetchall()
    conn.close()

    if not rows:
        return "📊 No trades logged yet."

    total = len(rows)
    wins = sum(1 for r in rows if r["result"] == "WIN")
    losses = total - wins
    total_pnl = sum(r["pnl"] for r in rows)
    win_pnl = sum(r["pnl"] for r in rows if r["result"] == "WIN")
    loss_pnl = sum(r["pnl"] for r in rows if r["result"] == "LOSS")

    # Best and worst
    best = max(rows, key=lambda r: r["pnl"])
    worst = min(rows, key=lambda r: r["pnl"])

    # By ticker
    tickers = {}
    for r in rows:
        t = r["ticker"]
        if t not in tickers:
            tickers[t] = {"count": 0, "pnl": 0}
        tickers[t]["count"] += 1
        tickers[t]["pnl"] += r["pnl"]

    ticker_summary = "\n".join(
        f"  {t}: {v['count']} trades, ${v['pnl']:+.2f}"
        for t, v in sorted(tickers.items(), key=lambda x: -x[1]["pnl"])
    )

    return f"""
📊 OPTIONS TRADE STATS
──────────────────────
Total Trades : {total}
Win Rate     : {wins}/{total} ({100*wins/total:.0f}%)
Total P&L    : ${total_pnl:+.2f}
Wins P&L     : ${win_pnl:+.2f}
Losses P&L   : ${loss_pnl:+.2f}

🏆 Best Trade : {best['ticker']} {best['direction']} ${best['strike']} → ${best['pnl']:+.2f}
💀 Worst Trade: {worst['ticker']} {worst['direction']} ${worst['strike']} → ${worst['pnl']:+.2f}

By Ticker:
{ticker_summary}
""".strip()


def get_recent_trades(limit=10):
    """Return last N trades formatted"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM options_trades ORDER BY logged_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()

    if not rows:
        return "No trades logged yet."

    lines = ["📋 RECENT OPTIONS TRADES", "─" * 40]
    for r in rows:
        icon = "✅" if r["result"] == "WIN" else "❌"
        dte_str = f" DTE:{r['dte_at_entry']}" if r["dte_at_entry"] is not None else ""
        lines.append(
            f"{icon} {r['ticker']} {r['direction']} ${r['strike']} exp {r['expiry']}{dte_str} → ${r['pnl']:+.2f}"
        )
    return "\n".join(lines)


def handle_command(text):
    """Main entry point — call from your Telegram bot handler"""
    init_db()
    parsed = parse_trade_command(text)

    if parsed is None:
        return (
            "❌ Could not parse trade. Format:\n"
            "TRADE WIN NVDA CALL 225 6/5 entry 6/2 exit +190\n"
            "TRADE LOSS AAPL PUT 180 6/20 entry 5/30 exit -85\n"
            "TRADES or TRADE STATS for history"
        )

    if parsed["command"] == "stats":
        return get_stats()

    if parsed["command"] == "list":
        return get_recent_trades()

    if parsed["command"] == "log":
        log_trade(parsed)
        dte_str = f", {parsed['dte_at_entry']} DTE" if parsed.get("dte_at_entry") is not None else ""
        icon = "✅" if parsed["result"] == "WIN" else "❌"
        return (
            f"{icon} Trade logged!\n"
            f"{parsed['ticker']} {parsed['direction']} ${parsed['strike']} exp {parsed['expiry']}{dte_str}\n"
            f"Entry: {parsed['entry_date']} → Exit: {parsed['exit_date']}\n"
            f"P&L: ${parsed['pnl']:+.2f}"
        )

    return "Unknown command."


# ── Standalone test ──────────────────────────────────────────────
if __name__ == "__main__":
    test_cmds = [
        "TRADE WIN NVDA CALL 225 6/5 entry 6/2 exit +190",
        "TRADE LOSS AAPL PUT 180 6/20 entry 5/30 exit -85",
        "TRADE STATS",
        "TRADES",
    ]
    for cmd in test_cmds:
        print(f"\n>>> {cmd}")
        print(handle_command(cmd))
