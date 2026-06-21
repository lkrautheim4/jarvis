#!/usr/bin/env python3
"""
jarvis_manual_bets.py
Manual Kalshi bet logging, grading, and real-dollar P&L stats.
Stores bets in kalshi_bets with source='manual_user'.
P&L is REAL DOLLARS — never summed with auto per-contract pnl.
"""
import sqlite3
from datetime import datetime, timezone

DB_PATH = "/root/jarvis/jarvis_memory.db"


def _connect(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_columns(conn):
    """ADD manual-bet columns if missing — each ALTER is a safe no-op if already present."""
    for ddl in (
        "ALTER TABLE kalshi_bets ADD COLUMN dollars REAL",
        "ALTER TABLE kalshi_bets ADD COLUMN entry_spot REAL",
    ):
        try:
            conn.execute(ddl)
            conn.commit()
        except sqlite3.OperationalError:
            pass


def log_manual_bet(side, dollars, strike, yes_price, no_price,
                   reason="manual", entry_spot=None):
    """
    Insert an ungraded manual bet into kalshi_bets.
    side:        'YES' or 'NO'
    dollars:     real-dollar stake
    entry_spot:  live BTC spot price at log time (CLV/edge context; not used for grading)
    Returns the logged row dict.
    """
    side = side.upper()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect()
    _ensure_columns(conn)
    conn.execute(
        """INSERT INTO kalshi_bets
               (ts, symbol, strike, bet, yes_price, no_price, reason,
                result, pnl, source, dollars, entry_spot)
           VALUES (?, 'BTC', ?, ?, ?, ?, ?, NULL, NULL, 'manual_user', ?, ?)""",
        (ts, strike, side, yes_price, no_price, reason, dollars, entry_spot),
    )
    conn.commit()
    conn.close()
    return {
        "ts": ts, "side": side, "dollars": dollars, "strike": strike,
        "yes_price": yes_price, "no_price": no_price, "entry_spot": entry_spot,
    }


def grade_manual_bet(won, actual_payout=None):
    """
    Grade the most-recent ungraded source='manual_user' row by direct UPDATE.
    P&L rules:
      LOSS               → -dollars
      WIN  explicit P    → P - dollars
      WIN  no payout     → dollars * (1 - entry) / entry
                           where entry = yes_price if bet=='YES' else no_price
    Returns graded row dict, or None if no pending bet.
    """
    conn = _connect()
    _ensure_columns(conn)
    row = conn.execute(
        """SELECT id, bet, dollars, yes_price, no_price
           FROM kalshi_bets
           WHERE source='manual_user' AND result IS NULL
           ORDER BY id DESC LIMIT 1"""
    ).fetchone()

    if row is None:
        conn.close()
        return None

    row_id    = row["id"]
    side      = row["bet"]
    dollars   = row["dollars"] or 0.0
    yes_price = row["yes_price"] or 0.0
    no_price  = row["no_price"] or 0.0

    if won:
        if actual_payout is not None:
            pnl = round(actual_payout - dollars, 2)
        else:
            entry = yes_price if side == "YES" else no_price
            pnl = round(dollars * (1 - entry) / entry, 2) if entry > 0 else 0.0
        result = "WIN"
    else:
        pnl    = round(-dollars, 2)
        result = "LOSS"

    graded_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "UPDATE kalshi_bets SET result=?, pnl=?, graded_at=? WHERE id=?",
        (result, pnl, graded_at, row_id),
    )
    conn.commit()
    conn.close()
    return {"id": row_id, "result": result, "pnl": pnl, "dollars": dollars}


def manual_stats():
    """
    Aggregate ONLY source='manual_user' rows — never mixed with auto bets.
    Returns a human-readable string.
    """
    conn = _connect()
    row = conn.execute(
        """SELECT
             COUNT(*) AS total,
             SUM(CASE WHEN result='WIN'  THEN 1 ELSE 0 END) AS wins,
             SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) AS losses,
             ROUND(COALESCE(SUM(pnl), 0), 2)               AS pnl,
             SUM(CASE WHEN bet='YES'               THEN 1 ELSE 0 END) AS yes_total,
             SUM(CASE WHEN bet='YES' AND result='WIN' THEN 1 ELSE 0 END) AS yes_wins,
             SUM(CASE WHEN bet='NO'                THEN 1 ELSE 0 END) AS no_total,
             SUM(CASE WHEN bet='NO'  AND result='WIN' THEN 1 ELSE 0 END) AS no_wins
           FROM kalshi_bets
           WHERE source='manual_user' AND result IN ('WIN','LOSS')"""
    ).fetchone()
    pending = conn.execute(
        "SELECT COUNT(*) FROM kalshi_bets WHERE source='manual_user' AND result IS NULL"
    ).fetchone()[0]
    last5 = conn.execute(
        """SELECT result, pnl FROM kalshi_bets
           WHERE source='manual_user' AND result IN ('WIN','LOSS')
           ORDER BY id DESC LIMIT 5"""
    ).fetchall()
    conn.close()

    total = row["total"] or 0
    if total == 0 and pending == 0:
        return "MANUAL BETS\nNo graded bets yet.\nBET YES/NO <$> to log"

    wins   = row["wins"] or 0
    losses = row["losses"] or 0
    pnl    = row["pnl"] or 0.0
    wr     = round(wins / total * 100) if total else 0
    yt, yw = row["yes_total"] or 0, row["yes_wins"] or 0
    nt, nw = row["no_total"] or 0, row["no_wins"] or 0
    yes_wr = round(yw / yt * 100) if yt else 0
    no_wr  = round(nw / nt * 100) if nt else 0
    last5_str = " ".join(
        (f"W${abs(r['pnl'] or 0):.0f}" if r["result"] == "WIN"
         else f"L${abs(r['pnl'] or 0):.0f}")
        for r in reversed(list(last5))
    ) or "—"

    return (
        f"MANUAL BETS (real $)\n{'='*22}\n"
        f"Graded:{total} | WR:{wr}% | P&L:${pnl:+.2f}\n"
        f"W:{wins} L:{losses}\n"
        f"YES:{yw}/{yt}({yes_wr}%) NO:{nw}/{nt}({no_wr}%)\n"
        f"Pending:{pending}\n"
        f"{'='*22}\nLast 5: {last5_str}"
    )
