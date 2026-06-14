"""
JARVIS Kalshi Auto-Grader
Runs every 15 minutes, grades resolved bets, updates DB
Accepts manual grading via jarvis_memory.db kalshi_manual_results table
"""
import sys, time, logging
sys.path.insert(0, '/root/jarvis')
import requests
import sqlite3
from datetime import datetime
import jarvis_memory_db as memdb
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)
DB = '/root/jarvis/jarvis_memory.db'

def init_manual_results_table():
    """Create table for manual bet result submissions"""
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS kalshi_manual_results (
            id TEXT PRIMARY KEY,
            bet_id TEXT NOT NULL,
            result TEXT NOT NULL,
            pnl REAL,
            submitted_at TEXT,
            processed INTEGER DEFAULT 0,
            FOREIGN KEY(bet_id) REFERENCES kalshi_bets(id)
        )
    """)
    conn.commit()
    conn.close()
    log.info("Manual results table initialized")

def fetch_market(ticker: str) -> dict:
    """Fetch a single market by ticker"""
    try:
        parts = ticker.rsplit('-T', 1)
        event_ticker = parts[0] if len(parts) == 2 else ticker
        r = requests.get(
            f"https://api.elections.kalshi.com/trade-api/v2/events/{event_ticker}",
            timeout=10
        )
        if r.status_code != 200:
            return {}
        markets = r.json().get('markets', [])
        for m in markets:
            if m.get('ticker') == ticker:
                return m
        return markets[0] if markets else {}
    except Exception as e:
        log.error(f"fetch_market error: {e}")
        return {}

def grade_bet(bet_id, side, yes_price_paid, no_price_paid, market):
    """Determine WIN/LOSS and pnl from resolved market"""
    result = market.get('result', '').lower()
    if result not in ('yes', 'no'):
        return None, None
    if side.upper() == 'YES':
        won = result == 'yes'
        price_paid = yes_price_paid or 0.50
    else:
        won = result == 'no'
        price_paid = no_price_paid or 0.50
    if won:
        pnl = round(1.0 - price_paid, 4)
        outcome = 'WIN'
    else:
        pnl = round(-price_paid, 4)
        outcome = 'LOSS'
    return outcome, pnl

def process_manual_results():
    """Check for manually submitted bet results and apply them"""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT id, bet_id, result, pnl FROM kalshi_manual_results
        WHERE processed=0
    """)
    manual = cur.fetchall()
    if manual:
        log.info(f"Processing {len(manual)} manual results")
    for mr in manual:
        bet_id = mr['bet_id']
        result = mr['result'].upper()
        pnl = mr['pnl']
        if result not in ('WIN', 'LOSS', 'VOID'):
            log.error(f"Manual result {mr['id']}: invalid result '{result}', skipping")
            continue
        cur.execute("""
            UPDATE kalshi_bets
            SET result=?, pnl=?, graded_at=?
            WHERE id=?
        """, (result, pnl, datetime.now().isoformat(), bet_id))
        cur.execute("""
            UPDATE kalshi_manual_results
            SET processed=1
            WHERE id=?
        """, (mr['id'],))
        conn.commit()
        log.info(f"Applied manual result: bet {bet_id} → {result} pnl={pnl}")
    conn.close()

def run_grader():
    log.info("Kalshi grader running...")
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    conn.close()
    process_manual_results()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT id, ts, bet, yes_price, no_price, market, strike
        FROM kalshi_bets
        WHERE result IS NULL AND market IS NOT NULL
    """)
    ungraded = cur.fetchall()
    log.info(f"Ungraded bets with ticker: {len(ungraded)}")
    graded = 0
    for bet in ungraded:
        market_data = fetch_market(bet['market'])
        if not market_data:
            log.info(f"Bet {bet['id']}: market not found for {bet['market']}")
            continue
        status = market_data.get('status', '')
        if status != 'finalized':
            log.info(f"Bet {bet['id']}: still active (status={status})")
            continue
        outcome, pnl = grade_bet(bet['id'], bet['bet'], bet['yes_price'], bet['no_price'], market_data)
        if outcome is None:
            log.info(f"Bet {bet['id']}: no result yet")
            continue
        cur.execute("""
            UPDATE kalshi_bets
            SET result=?, pnl=?, graded_at=?
            WHERE id=?
        """, (outcome, pnl, datetime.now().isoformat(), bet['id']))
        conn.commit()
        log.info(f"Graded bet {bet['id']}: {bet['bet']} → {outcome} pnl={pnl}")
        graded += 1
    cur.execute("""
        SELECT id, ts, bet, yes_price, no_price, strike
        FROM kalshi_bets
        WHERE result IS NULL AND (market IS NULL OR market='')
        AND ts >= date('now', '-30 days')
    """)
    no_ticker = cur.fetchall()
    log.info(f"Ungraded bets without ticker: {len(no_ticker)} (awaiting manual grading)")
    conn.close()
    log.info(f"Grading complete. Auto-graded {graded} bets.")
    return graded

def main():
    init_manual_results_table()
    log.info("KALSHI GRADER ONLINE — checking every 15 minutes")
    while True:
        try:
            run_grader()
        except Exception as e:
            log.error(f"Grader error: {e}")
        time.sleep(900)

if __name__ == '__main__':
    main()
