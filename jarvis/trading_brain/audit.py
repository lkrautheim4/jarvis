#!/usr/bin/env python3
"""
trading_brain/audit.py  --  READ-ONLY ledger auditor (Phase 1 observer foundation)

WHAT THIS DOES
  - Opens every *.db under JARVIS_ROOT in query_only mode (no writes, ever).
  - Dumps schema + row counts + small samples for every table.
  - Runs heuristic TRUST CHECKS for the two known June-20 problems:
      (A) fabricated P&L  -> rows with a bare WIN/LOSS-style outcome but a
          non-null / non-zero pnl value.
      (B) missing Kalshi implied price on BTC predictions -> coverage of any
          implied-price-like column (null vs populated).

WHAT THIS DOES NOT DO
  - It NEVER writes. Connection is opened with PRAGMA query_only = ON.
  - It does not touch secrets, keys, dashboards, or any bot.
  - It hardcodes NO column names. It discovers them by pattern and reports
    its guesses so you can confirm them. Guesses are labelled [GUESS].

USAGE (on the VPS):
  cp -r trading_brain /root/jarvis/        # new folder, nothing overwritten
  python3 /root/jarvis/trading_brain/audit.py            # scans /root/jarvis
  python3 /root/jarvis/trading_brain/audit.py /root/jarvis/some.db   # one db
"""

import os
import sys
import sqlite3
import json

DEFAULT_ROOT = "/root/jarvis"

# column-name patterns we look for. lowercased substring match.
OUTCOME_HINTS = ("outcome", "result", "status", "grade", "win_loss", "winloss")
PNL_HINTS     = ("pnl", "p_l", "profit", "realized", "net")
IMPLIED_HINTS = ("implied", "kalshi_price", "kalshi_prob", "market_prob",
                 "entry_prob", "close_prob", "yes_price", "no_price")
BTC_TABLE_HINTS = ("btc", "pred")
BARE_WIN_LOSS = {"win", "loss", "won", "lost", "w", "l"}


def connect_ro(path):
    """Open a live (possibly WAL) db without any chance of writing to it.
    query_only=ON blocks writes at the connection level but still allows
    the normal shared-memory reads a WAL db needs while bots write to it."""
    con = sqlite3.connect(path, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON;")
    return con


def list_tables(con):
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name;").fetchall()
    return [r["name"] for r in rows]


def columns(con, table):
    return [(r["name"], r["type"]) for r in
            con.execute(f'PRAGMA table_info("{table}");').fetchall()]


def find_cols(cols, hints):
    return [c for (c, _t) in cols if any(h in c.lower() for h in hints)]


def rowcount(con, table):
    try:
        return con.execute(f'SELECT COUNT(*) AS n FROM "{table}";').fetchone()["n"]
    except sqlite3.Error as e:
        return f"ERR:{e}"


def sample(con, table, n=2):
    try:
        rows = con.execute(f'SELECT * FROM "{table}" LIMIT {n};').fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as e:
        return f"ERR:{e}"


def check_fabricated_pnl(con, table, cols):
    """(A) bare WIN/LOSS outcome but pnl is populated -> suspected fabrication."""
    out_cols = find_cols(cols, OUTCOME_HINTS)
    pnl_cols = find_cols(cols, PNL_HINTS)
    if not out_cols or not pnl_cols:
        return None
    findings = []
    for oc in out_cols:
        for pc in pnl_cols:
            try:
                total = con.execute(
                    f'SELECT COUNT(*) n FROM "{table}" '
                    f'WHERE "{pc}" IS NOT NULL AND "{pc}" <> 0;').fetchone()["n"]
                # bare WIN/LOSS rows that nonetheless carry a pnl
                placeholders = ",".join("?" for _ in BARE_WIN_LOSS)
                suspect = con.execute(
                    f'SELECT COUNT(*) n FROM "{table}" '
                    f'WHERE LOWER(TRIM(CAST("{oc}" AS TEXT))) IN ({placeholders}) '
                    f'AND "{pc}" IS NOT NULL AND "{pc}" <> 0;',
                    tuple(BARE_WIN_LOSS)).fetchone()["n"]
                findings.append({
                    "outcome_col": oc, "pnl_col": pc,
                    "rows_with_nonzero_pnl": total,
                    "bare_winloss_rows_with_pnl_SUSPECT": suspect,
                })
            except sqlite3.Error as e:
                findings.append({"outcome_col": oc, "pnl_col": pc, "error": str(e)})
    return findings


def check_implied_coverage(con, table, cols):
    """(B) implied-price coverage on prediction rows."""
    imp_cols = find_cols(cols, IMPLIED_HINTS)
    if not imp_cols:
        return None
    total = rowcount(con, table)
    findings = []
    for ic in imp_cols:
        try:
            populated = con.execute(
                f'SELECT COUNT(*) n FROM "{table}" '
                f'WHERE "{ic}" IS NOT NULL;').fetchone()["n"]
            pct = (populated / total * 100) if isinstance(total, int) and total else 0
            findings.append({"implied_col": ic, "populated": populated,
                             "total": total, "coverage_pct": round(pct, 1)})
        except sqlite3.Error as e:
            findings.append({"implied_col": ic, "error": str(e)})
    return findings


def audit_db(path):
    print("\n" + "=" * 72)
    print(f"DB: {path}")
    print("=" * 72)
    try:
        con = connect_ro(path)
    except sqlite3.Error as e:
        print(f"  CANNOT OPEN (read-only): {e}")
        return
    try:
        tables = list_tables(con)
        if not tables:
            print("  (no user tables)")
            return
        for t in tables:
            cols = columns(con, t)
            n = rowcount(con, t)
            print(f"\n  TABLE {t}  rows={n}")
            print(f"    cols: {[c for c,_ in cols]}")

            fab = check_fabricated_pnl(con, t, cols)
            if fab:
                print("    [TRUST-CHECK A: fabricated P&L]")
                for f in fab:
                    print(f"      {f}")

            imp = check_implied_coverage(con, t, cols)
            if imp:
                print("    [TRUST-CHECK B: implied-price coverage]")
                for f in imp:
                    print(f"      {f}")

            samp = sample(con, t)
            print(f"    sample: {json.dumps(samp, default=str)[:400]}")
    finally:
        con.close()


def discover_dbs(root):
    hits = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if fn.endswith(".db"):
                hits.append(os.path.join(dirpath, fn))
    return sorted(hits)


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ROOT
    if os.path.isfile(arg):
        targets = [arg]
    else:
        targets = discover_dbs(arg)
    if not targets:
        print(f"No .db files found under {arg}")
        return
    print(f"READ-ONLY AUDIT  ({len(targets)} db files)  root={arg}")
    print("This process opens every db with PRAGMA query_only=ON. It writes nothing.")
    for db in targets:
        audit_db(db)
    print("\nDONE. Nothing was modified. Paste this output back for exact surgery.")


if __name__ == "__main__":
    main()

