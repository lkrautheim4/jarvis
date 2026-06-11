#!/usr/bin/env python3
"""
regrade_shorts.py — one-shot: correct SOFI put_sell grades + backfill DB + tag pre-gate trade.

Fix 3: In paper_trades.json, recompute every closed short with the correct formula.
Fix 4: Backfill options_trades SQLite rows for all closed paper trades.
Fix 5: Tag the open SOFI $14.5 put_sell as a pre-gate regime violation.
"""
import sys, json, os, sqlite3, logging
sys.path.insert(0, '/root/jarvis')

from datetime import datetime
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("regrade_shorts")

DB_PATH         = "/root/jarvis/jarvis_memory.db"
PT_SUSPECT_FILE = "/root/jarvis/paper_trades_suspect_archive.json"

# ── helpers ──────────────────────────────────────────────────────────────────

def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

def _save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)

# ── Fix 3 + 5: correct paper_trades.json in-place ────────────────────────────

def regrade_paper_trades():
    import paper_trades_store as pts

    corrections = []  # collect summary

    def _mutate(data):
        for t in data["trades"]:
            strat = t.get("strategy", "")
            is_short = strat in ("put_sell", "call_sell")

            # Fix 5: tag open SOFI $14.5 pre-gate violation
            if (t.get("status") == "paper_open"
                    and t.get("ticker") == "SOFI"
                    and t.get("strategy") == "put_sell"
                    and t.get("strike") == 14.5
                    and t.get("entry_date") == "2026-06-11"):
                t["notes"] = "entered pre-gate, regime violation"
                log.info(f"FIX5: tagged open SOFI $14.5 put_sell as pre-gate violation")
                continue

            # Fix 3: regrade suspect closed shorts with correct formula
            if not (is_short and t.get("status") == "paper_closed"
                    and t.get("suspect_grade")):
                continue

            entry_premium = float(t.get("premium") or 0)
            # exit_premium may be stored under exit_price (old field name)
            exit_prem = float(t.get("exit_premium") or t.get("exit_price") or 0)

            if entry_premium == 0 or exit_prem == 0:
                log.warning(f"SKIP: {t.get('ticker')} {strat} — missing premium data")
                continue

            # Correct P&L: seller profits when premium DECAYS
            pnl = round((entry_premium - exit_prem) * 100, 2)
            gain_pct = (entry_premium - exit_prem) / entry_premium
            result = "WIN" if pnl > 0 else "LOSS"
            if pnl > 0:
                exit_reason = f"TAKE_PROFIT (+{round(gain_pct*100)}%)"
            else:
                exit_reason = f"STOP_LOSS ({round(gain_pct*100)}%)"

            old_pnl = t.get("pnl")
            old_result = t.get("result")

            # Rename exit_price → exit_premium, clear exit_price (now = stock price slot)
            t["exit_premium"] = exit_prem
            t["exit_price"] = None  # stock price at exit (not available)
            t["pnl"] = pnl
            t["result"] = result
            t["exit_reason"] = exit_reason
            # Clear suspect flags — trade is now correctly graded
            del t["suspect_grade"]
            del t["suspect_reason"]

            corrections.append({
                "ticker": t["ticker"], "strategy": strat, "strike": t["strike"],
                "entry_date": t.get("entry_date"),
                "old_result": old_result, "new_result": result,
                "old_pnl": old_pnl, "new_pnl": pnl,
                "exit_reason": exit_reason,
                "exit_premium": exit_prem, "entry_premium": entry_premium,
            })
            log.info(
                f"FIX3: {t['ticker']} {strat} ${t['strike']} ({t.get('entry_date')}) "
                f"{old_result} ${old_pnl} → {result} ${pnl} | {exit_reason}"
            )

    pts.update(_mutate)

    # Remove the now-correctly-graded records from the suspect archive
    if corrections:
        archive = _load_json(PT_SUSPECT_FILE, [])
        keyed = {
            (c["ticker"], c["strategy"], c["strike"], c["entry_date"])
            for c in corrections
        }
        before = len(archive)
        archive = [
            a for a in archive
            if (a.get("ticker"), a.get("strategy"), a.get("strike"), a.get("entry_date"))
               not in keyed
        ]
        after = len(archive)
        if before != after:
            _save_json(PT_SUSPECT_FILE, archive)
            log.info(f"Removed {before - after} entries from suspect archive")

    return corrections


# ── Fix 4 backfill: sync paper_closed → options_trades DB ───────────────────

def backfill_db(corrections):
    import paper_trades_store as pts
    import jarvis_memory_db as memdb

    # Run migration to ensure exit_premium column exists
    memdb.init_db()

    data = pts.read()
    closed = [t for t in data["trades"] if t.get("status") == "paper_closed" and t.get("result")]
    log.info(f"Backfill: {len(closed)} closed paper trades to sync")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    synced = 0
    skipped = 0

    for t in closed:
        ticker    = t.get("ticker")
        strategy  = t.get("strategy")
        strike    = float(t.get("strike") or 0)
        entry_date = t.get("entry_date", "")
        result    = t.get("result")
        pnl       = float(t.get("pnl") or 0)
        exit_prem = t.get("exit_premium")  # may be None for old long-strategy closes
        exit_date = t.get("exit_date")

        rows = conn.execute(
            "SELECT id, status, result FROM options_trades "
            "WHERE ticker=? AND strategy=? AND strike=? AND ts LIKE ?",
            (ticker, strategy, strike, f"{entry_date}%")
        ).fetchall()

        if not rows:
            skipped += 1
            continue

        for row in rows:
            if row["status"] == "closed" and row["result"] is not None:
                # Already closed — only backfill exit_premium if missing
                if exit_prem is not None:
                    conn.execute(
                        "UPDATE options_trades SET exit_premium=? WHERE id=? AND exit_premium IS NULL",
                        (exit_prem, row["id"])
                    )
                continue
            conn.execute("""
                UPDATE options_trades SET status='closed', result=?, pnl=?,
                closed_at=?, exit_premium=?, exit_date=? WHERE id=?
            """, (result, pnl, datetime.now().isoformat(), exit_prem, exit_date, row["id"]))
            synced += 1
            log.info(f"  DB synced id={row['id']}: {ticker} {strategy} ${strike} → {result} ${pnl}")

    conn.commit()
    conn.close()
    log.info(f"Backfill complete: {synced} rows updated, {skipped} paper trades had no DB match")
    return synced


# ── verification: print corrected WR table ──────────────────────────────────

def print_wr_table():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT strategy, result, COUNT(*) as cnt, SUM(pnl) as total_pnl
        FROM options_trades
        WHERE result IS NOT NULL AND (notes IS NULL OR notes NOT LIKE 'suspect%')
        GROUP BY strategy, result
        ORDER BY strategy, result
    """).fetchall()
    conn.close()

    stats = {}
    for r in rows:
        s = r["strategy"]
        if s not in stats:
            stats[s] = {"wins": 0, "losses": 0, "pnl": 0.0}
        if r["result"] == "WIN":
            stats[s]["wins"] += r["cnt"]
        else:
            stats[s]["losses"] += r["cnt"]
        stats[s]["pnl"] += r["total_pnl"] or 0

    print("\n── Strategy WR Table (options_trades, non-suspect) ──────────────")
    print(f"{'Strategy':<15} {'W':>4} {'L':>4} {'WR%':>6} {'P&L':>10}")
    print("-" * 45)
    for s, d in sorted(stats.items()):
        total = d["wins"] + d["losses"]
        wr = round(d["wins"] / total * 100) if total else 0
        print(f"{s:<15} {d['wins']:>4} {d['losses']:>4} {wr:>5}%  ${d['pnl']:>+8.0f}")
    print()

    # Show SOFI put_sell rows specifically
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    sofi_rows = conn.execute("""
        SELECT id, ts, strike, premium, exit_premium, result, pnl, status, notes
        FROM options_trades
        WHERE ticker='SOFI' AND strategy='put_sell'
        ORDER BY ts
    """).fetchall()
    conn.close()

    print("── SOFI put_sell rows in options_trades ─────────────────────────")
    for r in sofi_rows:
        print(f"  id={r['id']} ts={r['ts'][:10]} strike=${r['strike']} "
              f"entry_prem={r['premium']} exit_prem={r['exit_premium']} "
              f"result={r['result']} pnl={r['pnl']} status={r['status']}"
              + (f" notes={r['notes'][:40]}" if r['notes'] else ""))


# ── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=== regrade_shorts starting ===")

    log.info("--- Fix 3 + 5: regrade paper_trades.json ---")
    corrections = regrade_paper_trades()
    log.info(f"Corrected {len(corrections)} closed short trade(s)")

    log.info("--- Fix 4: backfill options_trades DB ---")
    synced = backfill_db(corrections)

    print_wr_table()
    log.info("=== regrade_shorts complete ===")
