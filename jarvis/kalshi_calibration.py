#!/usr/bin/env python3
"""
Read-only calibration report: model probability vs Kalshi yes_price vs actual outcome.

Queries kalshi_predictions (every cycle including SKIPs) and kalshi_bets (graded bets).
Run standalone: python3 kalshi_calibration.py
"""
import sqlite3
from datetime import datetime

DB_PATH = "/root/jarvis/jarvis_memory.db"


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _pct(n, d):
    return f"{n/d*100:.0f}%" if d else "—"


def report():
    conn = get_db()

    # ── 1. kalshi_predictions table status ───────────────────────────────────
    try:
        total_cycles = conn.execute("SELECT COUNT(*) FROM kalshi_predictions").fetchone()[0]
    except Exception:
        total_cycles = 0

    print(f"\n{'='*56}")
    print(f"  KALSHI CALIBRATION REPORT  —  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*56}")

    if total_cycles == 0:
        print("\nNo data in kalshi_predictions yet.")
        print("Prediction logging begins on the next lenny_predictions cycle.\n")
    else:
        # Decision breakdown
        rows = conn.execute("""
            SELECT decision, COUNT(*) n,
                   SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins,
                   SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) losses,
                   SUM(CASE WHEN result IS NULL THEN 1 ELSE 0 END) pending
            FROM kalshi_predictions
            GROUP BY decision ORDER BY n DESC
        """).fetchall()
        print(f"\n── Decision breakdown ({total_cycles} total cycles) ──")
        print(f"  {'Decision':<8}  {'Count':>6}  {'W':>5}  {'L':>5}  {'Pending':>7}  {'WR':>6}")
        for r in rows:
            print(f"  {r['decision']:<8}  {r['n']:>6}  {r['wins']:>5}  {r['losses']:>5}"
                  f"  {r['pending']:>7}  {_pct(r['wins'], r['wins']+r['losses']):>6}")

        # Model prob vs yes_price buckets (YES decisions only, graded)
        rows = conn.execute("""
            SELECT
                CASE
                    WHEN yes_price < 0.10 THEN '0.00–0.10'
                    WHEN yes_price < 0.20 THEN '0.10–0.20'
                    WHEN yes_price < 0.30 THEN '0.20–0.30'
                    WHEN yes_price < 0.40 THEN '0.30–0.40'
                    WHEN yes_price < 0.50 THEN '0.40–0.50'
                    ELSE                      '>=0.50'
                END bucket,
                COUNT(*) n,
                ROUND(AVG(model_prob), 3) avg_model,
                ROUND(AVG(yes_price), 3)  avg_market,
                ROUND(AVG(edge), 3)       avg_edge,
                SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins,
                SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) losses
            FROM kalshi_predictions
            WHERE decision='YES' AND result IS NOT NULL
            GROUP BY bucket ORDER BY bucket
        """).fetchall()
        if rows:
            print(f"\n── YES decisions by market yes_price bucket ──")
            print(f"  {'Mkt price':<12}  {'N':>4}  {'AvgModel':>9}  {'AvgEdge':>8}  "
                  f"{'W':>4}  {'L':>4}  {'WR':>6}")
            for r in rows:
                print(f"  {r['bucket']:<12}  {r['n']:>4}  {r['avg_model']:>9.3f}"
                      f"  {r['avg_edge']:>8.3f}  {r['wins']:>4}  {r['losses']:>4}"
                      f"  {_pct(r['wins'], r['wins']+r['losses']):>6}")

        # Model calibration: does model_prob correlate with win rate?
        rows = conn.execute("""
            SELECT
                CASE
                    WHEN model_prob < 0.30 THEN '<30%'
                    WHEN model_prob < 0.50 THEN '30-50%'
                    WHEN model_prob < 0.65 THEN '50-65%'
                    WHEN model_prob < 0.80 THEN '65-80%'
                    ELSE                        '>=80%'
                END bucket,
                COUNT(*) n,
                SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins,
                SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) losses
            FROM kalshi_predictions
            WHERE result IS NOT NULL
            GROUP BY bucket ORDER BY bucket
        """).fetchall()
        if rows:
            print(f"\n── Model prob calibration (all decisions, graded) ──")
            print(f"  {'Model prob':<10}  {'N':>4}  {'W':>4}  {'L':>4}  {'Actual WR':>10}")
            for r in rows:
                print(f"  {r['bucket']:<10}  {r['n']:>4}  {r['wins']:>4}  {r['losses']:>4}"
                      f"  {_pct(r['wins'], r['wins']+r['losses']):>10}")

        # Edge analysis: does higher edge → better outcomes?
        rows = conn.execute("""
            SELECT
                CASE
                    WHEN edge < 0.00 THEN 'edge<0 (NO lean)'
                    WHEN edge < 0.10 THEN '0.00–0.10'
                    WHEN edge < 0.20 THEN '0.10–0.20'
                    WHEN edge < 0.40 THEN '0.20–0.40'
                    ELSE                  '>=0.40'
                END bucket,
                COUNT(*) n,
                SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins,
                SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) losses
            FROM kalshi_predictions
            WHERE result IS NOT NULL AND edge IS NOT NULL
            GROUP BY bucket ORDER BY bucket
        """).fetchall()
        if rows:
            print(f"\n── Edge analysis (YES decisions, graded) ──")
            print(f"  {'Edge bucket':<18}  {'N':>4}  {'W':>4}  {'L':>4}  {'WR':>6}")
            for r in rows:
                print(f"  {r['bucket']:<18}  {r['n']:>4}  {r['wins']:>4}  {r['losses']:>4}"
                      f"  {_pct(r['wins'], r['wins']+r['losses']):>6}")

    # ── 2. kalshi_bets summary (existing bets, pre-new-table) ────────────────
    bets = conn.execute("""
        SELECT COUNT(*) total,
               SUM(CASE WHEN result='WIN'  THEN 1 ELSE 0 END) wins,
               SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) losses,
               SUM(CASE WHEN result IS NULL THEN 1 ELSE 0 END) pending,
               ROUND(SUM(pnl), 4) pnl
        FROM kalshi_bets WHERE source='auto'
    """).fetchone()
    print(f"\n── kalshi_bets (historical, source=auto) ──")
    if bets["total"]:
        print(f"  Total: {bets['total']}  |  W/L: {bets['wins']}/{bets['losses']}"
              f"  |  WR: {_pct(bets['wins'], bets['wins']+bets['losses'])}"
              f"  |  Pending: {bets['pending']}  |  P&L: {bets['pnl']:+.4f}")
    else:
        print("  No auto bets found.")

    # YES bets in kalshi_bets by yes_price bucket (historical calibration)
    rows = conn.execute("""
        SELECT
            CASE
                WHEN yes_price < 0.10 THEN '0.00–0.10'
                WHEN yes_price < 0.20 THEN '0.10–0.20'
                WHEN yes_price < 0.30 THEN '0.20–0.30'
                WHEN yes_price < 0.40 THEN '0.30–0.40'
                WHEN yes_price < 0.50 THEN '0.40–0.50'
                ELSE                       '>=0.50'
            END bucket,
            COUNT(*) n,
            SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins,
            SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) losses,
            ROUND(SUM(pnl), 3) pnl
        FROM kalshi_bets
        WHERE source='auto' AND bet='YES' AND result IS NOT NULL
        GROUP BY bucket ORDER BY bucket
    """).fetchall()
    if rows:
        print(f"\n── Historical YES bets by market yes_price (kalshi_bets) ──")
        print(f"  {'Mkt price':<12}  {'N':>4}  {'W':>4}  {'L':>4}  {'WR':>6}  {'P&L':>8}")
        for r in rows:
            print(f"  {r['bucket']:<12}  {r['n']:>4}  {r['wins']:>4}  {r['losses']:>4}"
                  f"  {_pct(r['wins'], r['wins']+r['losses']):>6}  {r['pnl']:>8.3f}")

    print(f"\n{'='*56}\n")
    conn.close()


if __name__ == "__main__":
    report()
