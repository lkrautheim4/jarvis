#!/usr/bin/env python3
import sqlite3, json, time, logging
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import defaultdict

DB_PATH = "/root/jarvis/jarvis_memory.db"
TRADES_PATH = "/root/jarvis/paper_trades.json"
LOG_FILE = "/root/jarvis/jarvis_learning.log"

logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger()

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def load_trades():
    try:
        with open(TRADES_PATH, 'r') as f:
            return json.load(f).get('trades', [])
    except:
        return []

def compute_ticker_stats(trades):
    stats = defaultdict(lambda: {"wins": 0, "total": 0})
    for t in trades:
        if t.get('status') != 'paper_closed':
            continue
        ticker = t.get('ticker', '?')
        stats[ticker]['total'] += 1
        if t.get('result') == 'WIN':
            stats[ticker]['wins'] += 1
    
    result = {}
    for ticker, data in stats.items():
        if data['total'] > 0:
            wr = (data['wins'] / data['total']) * 100
            result[ticker] = {"wr": round(wr, 1), "count": data['total']}
    return result

def main():
    log.info("[LEARNING] v1 online")
    conn = get_db()
    while True:
        try:
            trades = load_trades()
            closed = len([t for t in trades if t.get('status') == 'paper_closed'])
            if closed < 10:
                time.sleep(300)
                continue
            
            ticker_stats = compute_ticker_stats(trades)
            ts = datetime.now().isoformat()
            rules = {"ticker_stats": ticker_stats, "ts": ts, "total": closed}
            conn.execute("INSERT OR REPLACE INTO brain (key, value, updated_at) VALUES (?, ?, ?)",
                        ("learned_ticker_rules", json.dumps(rules), ts))
            conn.commit()
            log.info(f"Analyzed {closed} trades, {len(ticker_stats)} tickers")
            time.sleep(1800)
        except Exception as e:
            log.error(f"ERROR: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
