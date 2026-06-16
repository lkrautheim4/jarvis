#!/usr/bin/env python3
import sqlite3, logging, requests
from datetime import datetime
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

DB_PATH        = "/root/jarvis/jarvis_memory.db"
KALSHI_API_KEY = "f3c367c6-92fe-455f-ae54-2dcef68d07a7"
KALSHI_BASE    = "https://api.elections.kalshi.com/trade-api/v2"


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_market_result(ticker: str) -> str | None:
    """Fetch settlement result from Kalshi API. Returns 'yes', 'no', or None if not yet settled."""
    try:
        r = requests.get(
            f"{KALSHI_BASE}/markets/{ticker}",
            headers={"Authorization": f"Bearer {KALSHI_API_KEY}"},
            timeout=10
        )
        if r.status_code != 200:
            log.warning(f"Kalshi API {r.status_code} for {ticker}")
            return None
        market = r.json().get("market", {})
        result = market.get("result", "")
        return result.lower() if result else None
    except Exception as e:
        log.error(f"Kalshi API error for {ticker}: {e}")
        return None


def _compute_pnl(bet: str, market_result: str, yes_price: float, no_price: float) -> tuple[str, float]:
    """Return (WIN|LOSS, pnl_per_contract) based on bet direction and settlement."""
    if bet == "YES":
        if market_result == "yes":
            return "WIN", round(1.0 - yes_price, 4)
        else:
            return "LOSS", round(-yes_price, 4)
    elif bet == "NO":
        if market_result == "no":
            return "WIN", round(1.0 - no_price, 4)
        else:
            return "LOSS", round(-no_price, 4)
    return "LOSS", 0.0


def grade_bets():
    conn = get_db()
    try:
        graded_at = datetime.now(ZoneInfo("America/New_York")).isoformat()

        # ── Path 1: API settlement for real-market rows (source='auto', has ticker) ──
        api_candidates = conn.execute("""
            SELECT id, symbol, bet, strike, market, yes_price, no_price
            FROM kalshi_bets
            WHERE result IS NULL
              AND source = 'auto'
              AND market IS NOT NULL AND market != ''
        """).fetchall()

        api_graded = 0
        for row in api_candidates:
            market_result = _fetch_market_result(row["market"])
            if not market_result:
                continue   # not yet settled — skip
            outcome, pnl = _compute_pnl(row["bet"], market_result, row["yes_price"], row["no_price"])
            conn.execute(
                "UPDATE kalshi_bets SET result=?, pnl=?, graded_at=? WHERE id=?",
                (outcome, pnl, graded_at, row["id"])
            )
            log.info(f"[API] Graded id={row['id']} {row['market']} bet={row['bet']}"
                     f" → {market_result.upper()} {outcome} pnl={pnl:+.4f}")
            api_graded += 1

        conn.commit()

        # ── Path 2: Manual results fallback (join on symbol/bet/strike, any source) ──
        manual_candidates = conn.execute("""
            SELECT kb.id, kb.symbol, kb.bet, kb.strike, kmr.result, kmr.pnl
            FROM kalshi_bets kb
            LEFT JOIN kalshi_manual_results kmr
              ON kb.symbol=kmr.symbol AND kb.bet=kmr.bet AND kb.strike=kmr.strike
            WHERE kb.result IS NULL
              AND kmr.result IS NOT NULL
              AND (kb.source IS NULL OR kb.source != 'synthetic')
        """).fetchall()

        manual_graded = 0
        for bet in manual_candidates:
            conn.execute(
                "UPDATE kalshi_bets SET result=?, pnl=?, graded_at=? WHERE id=?",
                (bet["result"], bet["pnl"], graded_at, bet["id"])
            )
            log.info(f"[Manual] Graded id={bet['id']} {bet['symbol']} {bet['bet']}"
                     f" → {bet['result']} pnl={bet['pnl']}")
            manual_graded += 1

        conn.commit()

        if api_graded == 0 and manual_graded == 0:
            log.info("No bets newly graded this run")

        # ── Update brain stats (real bets only — exclude synthetic) ──
        stats = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN result IS NOT NULL THEN 1 ELSE 0 END) as graded,
                   SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins,
                   SUM(pnl) as pnl
            FROM kalshi_bets
            WHERE result IS NOT NULL
              AND (source IS NULL OR source != 'synthetic')
        """).fetchone()

        if stats["graded"] and stats["graded"] > 0:
            wr = stats["wins"] / stats["graded"] * 100
            conn.execute("INSERT OR REPLACE INTO brain (key, value, updated_at) VALUES (?, ?, ?)",
                ("kalshi_win_rate", str(round(wr, 1)), graded_at))
            conn.execute("INSERT OR REPLACE INTO brain (key, value, updated_at) VALUES (?, ?, ?)",
                ("kalshi_pnl", str(round(stats["pnl"] or 0, 2)), graded_at))
            conn.commit()
            log.info(f"Stats: WR={wr:.1f}% ({stats['wins']}/{stats['graded']}) | P&L={stats['pnl']:+.2f}")

    except Exception as e:
        log.error(f"Error: {e}")
    finally:
        conn.close()


def print_record():
    """Print honest record split: real bets vs synthetic."""
    conn = get_db()
    try:
        real = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) as losses,
                   SUM(pnl) as pnl
            FROM kalshi_bets
            WHERE source = 'auto' AND result IS NOT NULL
        """).fetchone()
        pending = conn.execute("""
            SELECT COUNT(*) as n FROM kalshi_bets
            WHERE source = 'auto' AND result IS NULL AND bet != 'SKIP'
        """).fetchone()["n"]
        synthetic = conn.execute(
            "SELECT COUNT(*) as n FROM kalshi_bets WHERE source = 'synthetic'"
        ).fetchone()["n"]

        print("\n=== KALSHI BET RECORD ===")
        print(f"REAL (source=auto):  {real['total']} graded | {real['wins']} W / {real['losses']} L"
              f" | WR={real['wins']/real['total']*100:.0f}% | P&L={real['pnl']:+.2f}" if real["total"] else
              "REAL (source=auto): 0 graded bets")
        print(f"Pending (ungraded):  {pending} real bets awaiting settlement")
        print(f"SYNTHETIC (excluded): {synthetic} rows (quarantined, never modified)")
        print("========================\n")
    except Exception as e:
        log.error(f"print_record error: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    log.info("Kalshi grader online")
    grade_bets()
    print_record()
