#!/usr/bin/env python3
"""
JARVIS Webull Trade Scraper
Pulls closed options trades from Webull and syncs to jarvis_memory.db

Two modes:
  1. webull-client library (preferred) - uses your saved Webull credentials
  2. Manual CSV import - export from Webull app and run: python jarvis_webull_scraper.py --csv trades.csv

Setup:
  pip install webull
  First run will prompt for Webull login + 2FA (saves token)

Usage:
  python jarvis_webull_scraper.py          # pull last 30 days
  python jarvis_webull_scraper.py --days 7 # pull last 7 days
  python jarvis_webull_scraper.py --csv path/to/export.csv
"""

import sqlite3
import json
import os
import sys
import argparse
import csv
from datetime import datetime, timedelta, date

DB_PATH = "/root/jarvis/jarvis_memory.db"
LOG_PATH = "/root/jarvis/jarvis_webull_scraper.log"
CREDS_PATH = "/root/jarvis/webull_session.json"


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{ts}] {msg}"
    with open(LOG_PATH, "a") as f:
        f.write(entry + "\n")
    print(entry)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    # Add webull_id column to avoid duplicate imports
    conn.execute("""
        CREATE TABLE IF NOT EXISTS options_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            direction TEXT NOT NULL,
            strike REAL NOT NULL,
            expiry TEXT NOT NULL,
            entry_date TEXT,
            exit_date TEXT,
            result TEXT NOT NULL,
            pnl REAL NOT NULL,
            dte_at_entry INTEGER,
            notes TEXT,
            source TEXT DEFAULT 'manual',
            webull_id TEXT UNIQUE,
            logged_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # Try to add columns if table already exists without them
    try:
        conn.execute("ALTER TABLE options_trades ADD COLUMN source TEXT DEFAULT 'manual'")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE options_trades ADD COLUMN webull_id TEXT UNIQUE")
    except Exception:
        pass
    conn.commit()
    conn.close()


def insert_trade(trade_dict):
    """Insert a trade, skip if webull_id already exists"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.execute("""
            INSERT OR IGNORE INTO options_trades
            (ticker, direction, strike, expiry, entry_date, exit_date,
             result, pnl, dte_at_entry, notes, source, webull_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_dict.get("ticker"),
            trade_dict.get("direction"),
            trade_dict.get("strike"),
            trade_dict.get("expiry"),
            trade_dict.get("entry_date"),
            trade_dict.get("exit_date"),
            trade_dict.get("result"),
            trade_dict.get("pnl"),
            trade_dict.get("dte_at_entry"),
            trade_dict.get("notes"),
            trade_dict.get("source", "webull"),
            trade_dict.get("webull_id")
        ))
        conn.commit()
        inserted = conn.execute("SELECT changes()").fetchone()[0]
        conn.close()
        return inserted > 0
    except Exception as e:
        conn.close()
        log(f"Insert error: {e}")
        return False


def parse_option_symbol(symbol):
    """
    Parse OCC option symbol like NVDA240605C00225000
    Returns (ticker, expiry, direction, strike) or None
    """
    import re
    m = re.match(r"([A-Z]{1,5})(\d{6})([CP])(\d{8})", symbol)
    if not m:
        return None
    ticker, exp_str, cp, strike_str = m.groups()
    direction = "CALL" if cp == "C" else "PUT"
    strike = int(strike_str) / 1000
    exp_date = datetime.strptime(exp_str, "%y%m%d").strftime("%-m/%-d/%Y")
    return ticker, exp_date, direction, strike


def calc_dte(entry_date_str, expiry_str):
    """Calculate DTE at entry"""
    try:
        entry = datetime.strptime(entry_date_str, "%m/%d/%Y").date()
        expiry = datetime.strptime(expiry_str, "%m/%d/%Y").date()
        return (expiry - entry).days
    except Exception:
        return None


# ── MODE 1: webull-client library ────────────────────────────────

def pull_from_webull_api(days=30):
    """Pull closed options trades using webull-client library"""
    try:
        from webull import webull
    except ImportError:
        log("webull library not installed. Run: pip install webull")
        return []

    wb = webull()

    # Load saved session or login fresh
    if os.path.exists(CREDS_PATH):
        try:
            wb.api_login(
                access_token=json.load(open(CREDS_PATH)).get("access_token"),
                refresh_token=json.load(open(CREDS_PATH)).get("refresh_token"),
                token_expire=json.load(open(CREDS_PATH)).get("token_expire"),
                uuid=json.load(open(CREDS_PATH)).get("uuid")
            )
            log("Loaded saved Webull session")
        except Exception as e:
            log(f"Session load failed: {e} — logging in fresh")
            _webull_login(wb)
    else:
        _webull_login(wb)

    # Pull order history
    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    log(f"Fetching Webull orders {start_date} → {end_date}")
    try:
        orders = wb.get_history_orders(status="Filled", count=200)
    except Exception as e:
        log(f"Failed to fetch orders: {e}")
        return []

    trades = []
    for order in orders:
        try:
            symbol = order.get("ticker", {}).get("symbol", "")
            if not symbol:
                continue

            parsed = parse_option_symbol(symbol)
            if not parsed:
                continue  # skip stock trades, options only

            ticker, expiry, direction, strike = parsed
            filled_time = order.get("filledTime", "") or order.get("createTime", "")
            exit_date = datetime.strptime(filled_time[:10], "%Y-%m-%d").strftime("%-m/%-d/%Y") if filled_time else ""

            action = order.get("action", "")  # BUY or SELL
            avg_price = float(order.get("avgFilledPrice", 0))
            qty = int(order.get("totalQuantity", 1))

            # We need paired BUY+SELL to calculate P&L
            # For now, store raw order and let pairing logic handle it
            trades.append({
                "ticker": ticker,
                "direction": direction,
                "strike": strike,
                "expiry": expiry,
                "exit_date": exit_date,
                "action": action,
                "avg_price": avg_price,
                "qty": qty,
                "symbol": symbol,
                "webull_order_id": str(order.get("orderId", ""))
            })
        except Exception as e:
            log(f"Order parse error: {e}")
            continue

    # Pair BUY and SELL orders to calculate P&L
    return _pair_orders(trades)


def _pair_orders(raw_orders):
    """Match BUY and SELL orders by symbol to calculate P&L"""
    from collections import defaultdict
    by_symbol = defaultdict(list)
    for o in raw_orders:
        by_symbol[o["symbol"]].append(o)

    paired = []
    for symbol, orders in by_symbol.items():
        buys = [o for o in orders if o["action"] == "BUY"]
        sells = [o for o in orders if o["action"] == "SELL"]
        if not buys or not sells:
            continue

        avg_buy = sum(o["avg_price"] * o["qty"] for o in buys) / sum(o["qty"] for o in buys)
        avg_sell = sum(o["avg_price"] * o["qty"] for o in sells) / sum(o["qty"] for o in sells)
        qty = min(sum(o["qty"] for o in buys), sum(o["qty"] for o in sells))
        pnl = (avg_sell - avg_buy) * qty * 100

        o = orders[0]
        entry_date = min(o["exit_date"] for o in buys)
        exit_date = max(o["exit_date"] for o in sells)
        dte = calc_dte(entry_date, o["expiry"]) if entry_date else None

        paired.append({
            "ticker": o["ticker"],
            "direction": o["direction"],
            "strike": o["strike"],
            "expiry": o["expiry"],
            "entry_date": entry_date,
            "exit_date": exit_date,
            "result": "WIN" if pnl > 0 else "LOSS",
            "pnl": round(pnl, 2),
            "dte_at_entry": dte,
            "source": "webull_api",
            "webull_id": f"{symbol}_{entry_date}_{exit_date}"
        })

    return paired


def _webull_login(wb):
    """Interactive Webull login — saves session to file"""
    email = input("Webull email: ")
    password = input("Webull password: ")
    wb.login(email, password)
    code = input("Enter 2FA code from email/SMS: ")
    wb.get_trade_token(code)

    # Save session
    session = {
        "access_token": wb._access_token,
        "refresh_token": wb._refresh_token,
        "token_expire": wb._token_expire,
        "uuid": wb._uuid
    }
    with open(CREDS_PATH, "w") as f:
        json.dump(session, f)
    log("Webull session saved")


# ── MODE 2: CSV Import ───────────────────────────────────────────

def import_from_csv(csv_path):
    """
    Import from Webull CSV export.
    Webull CSV columns (typical):
    Symbol, Date/Time, Action, Quantity, Price, Fees, Amount
    """
    if not os.path.exists(csv_path):
        log(f"CSV not found: {csv_path}")
        return []

    raw = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            symbol = row.get("Symbol", "").strip()
            parsed = parse_option_symbol(symbol)
            if not parsed:
                continue  # skip non-options rows

            ticker, expiry, direction, strike = parsed
            action = row.get("Action", "").upper()
            qty = abs(int(row.get("Quantity", 1)))
            price = abs(float(row.get("Price", 0)))
            dt_str = row.get("Date/Time", "")[:10]
            try:
                trade_date = datetime.strptime(dt_str, "%Y-%m-%d").strftime("%-m/%-d/%Y")
            except Exception:
                trade_date = dt_str

            raw.append({
                "ticker": ticker,
                "direction": direction,
                "strike": strike,
                "expiry": expiry,
                "exit_date": trade_date,
                "action": action,
                "avg_price": price,
                "qty": qty,
                "symbol": symbol,
                "webull_order_id": f"{symbol}_{dt_str}_{action}"
            })

    return _pair_orders(raw)


# ── Main ─────────────────────────────────────────────────────────

def run_sync(days=30, csv_path=None):
    init_db()

    if csv_path:
        log(f"Importing from CSV: {csv_path}")
        trades = import_from_csv(csv_path)
    else:
        log(f"Pulling from Webull API (last {days} days)")
        trades = pull_from_webull_api(days=days)

    if not trades:
        log("No trades found.")
        return

    new_count = 0
    for t in trades:
        inserted = insert_trade(t)
        if inserted:
            new_count += 1
            log(f"  + {t['result']} {t['ticker']} {t['direction']} ${t['strike']} exp {t['expiry']} P&L ${t['pnl']:+.2f}")
        else:
            log(f"  ~ skipped (already exists): {t.get('webull_id','')}")

    log(f"Sync complete. {new_count} new trades added out of {len(trades)} found.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="JARVIS Webull Trade Scraper")
    parser.add_argument("--days", type=int, default=30, help="Days of history to pull (default 30)")
    parser.add_argument("--csv", type=str, default=None, help="Path to Webull CSV export")
    args = parser.parse_args()
    run_sync(days=args.days, csv_path=args.csv)
