"""
JARVIS Kalshi Auto-Grader
Runs every 15 minutes, grades resolved bets, updates DB
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

def fetch_market(ticker: str) -> dict:
    """Fetch a single market by ticker"""
    try:
        # ticker stored is full: KXBTCD-26JUN0315-T60099.99
        # event ticker is everything before last hyphen+T
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
        # fallback: return first market
        return markets[0] if markets else {}
    except Exception as e:
        log.error(f"fetch_market error: {e}")
        return {}

def grade_bet(bet_id, side, yes_price_paid, no_price_paid, market):
    """Determine WIN/LOSS and pnl from resolved market"""
    result = market.get('result', '').lower()  # 'yes' or 'no'
    if result not in ('yes', 'no'):
        return None, None  # not resolved yet

    # Did our bet win?
    if side.upper() == 'YES':
        won = result == 'yes'
        price_paid = yes_price_paid or 0.50
    else:  # NO bet
        won = result == 'no'
        price_paid = no_price_paid or 0.50

    if won:
        pnl = round(1.0 - price_paid, 4)  # profit per contract dollar
        outcome = 'WIN'
    else:
        pnl = round(-price_paid, 4)  # loss per contract dollar
        outcome = 'LOSS'

    return outcome, pnl

def run_grader():
    log.info("Kalshi grader running...")
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Get ungraded bets that have a market ticker
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

        outcome, pnl = grade_bet(
            bet['id'],
            bet['bet'],
            bet['yes_price'],
            bet['no_price'],
            market_data
        )

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

    # Also handle bets with no ticker but have strike — try to find market
    cur.execute("""
        SELECT id, ts, bet, yes_price, no_price, strike
        FROM kalshi_bets
        WHERE result IS NULL AND (market IS NULL OR market='')
        AND ts >= date('now', '-2 days')
    """)
    no_ticker = cur.fetchall()
    log.info(f"Ungraded bets without ticker: {len(no_ticker)}")

    conn.close()

    # Propagate resolved outcomes to the parallel predictions rows so the
    # morning brief's BTC-prediction WR reflects real betting performance.
    try:
        pred_graded = memdb.grade_predictions_from_kalshi()
        if pred_graded:
            log.info(f"Graded {pred_graded} predictions from kalshi outcomes")
    except Exception as e:
        log.error(f"Prediction grading error: {e}")

    log.info(f"Grading complete. Graded {graded} bets.")
    return graded

def main():
    log.info("KALSHI GRADER ONLINE — checking every 15 minutes")
    while True:
        try:
            run_grader()
        except Exception as e:
            log.error(f"Grader error: {e}")
        time.sleep(900)  # 15 minutes

if __name__ == '__main__':
    main()
