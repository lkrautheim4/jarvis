#!/usr/bin/env python3
import sqlite3, json, time, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DB_PATH = "/root/jarvis/jarvis_memory.db"

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn

def get_brain(conn, key):
    cur = conn.execute("SELECT value FROM brain WHERE key=?", (key,))
    row = cur.fetchone()
    if row:
        try:
            return json.loads(row[0])
        except:
            return row[0]
    return None

def set_brain(conn, key, value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value)
    conn.execute("INSERT OR REPLACE INTO brain (key, value, updated_at) VALUES (?, ?, ?)",
        (key, str(value), datetime.now(ZoneInfo("America/New_York")).isoformat()))
    conn.commit()

def write_heartbeat(conn):
    ts = datetime.now(ZoneInfo("America/New_York")).isoformat()
    open_count = conn.execute("SELECT COUNT(*) FROM options_trades WHERE status='paper'").fetchone()[0]
    set_brain(conn, "options_brain_heartbeat", {"ts": ts, "alive": True, "open_positions": open_count})

def check_dedup(conn, ticker, strategy, strike):
    cutoff = datetime.now(ZoneInfo("America/New_York")) - timedelta(seconds=120)
    existing = conn.execute(
        "SELECT COUNT(*) FROM options_trades WHERE ticker=? AND strategy=? AND strike=? AND ts > ? AND status IN ('paper','closed')",
        (ticker, strategy, strike, cutoff.isoformat())).fetchone()[0]
    return existing > 0

def close_expired(conn):
    cutoff = datetime.now(ZoneInfo("America/New_York")) - timedelta(hours=4)
    trades = conn.execute("SELECT id FROM options_trades WHERE status='paper' AND ts < ?", (cutoff.isoformat(),)).fetchall()
    for t in trades:
        conn.execute("UPDATE options_trades SET status='closed', premium=0 WHERE id=?", (t["id"],))
    conn.commit()
    return len(trades)

def main():
    print("[OPTIONS_BRAIN] v2.1 online")
    conn = get_db()
    last_hb = time.time()
    while True:
        try:
            if time.time() - last_hb > 60:
                write_heartbeat(conn)
                last_hb = time.time()
            closed = close_expired(conn)
            if closed > 0:
                print(f"[CLOSED] {closed} aged positions")
            time.sleep(60)
        except Exception as e:
            print(f"[ERROR] {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
