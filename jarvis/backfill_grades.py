#!/usr/bin/env python3
"""
backfill_grades.py — Phase B.6 data-integrity backfill.

Finds every ungraded/garbage-graded record in:
  - jarvis_memory.db :: options_trades, kalshi_bets, predictions
  - paper_trades.json

For each record:
  1. Try to determine the correct outcome from available price history
     (btc_ticks for BTC records, yfinance for stock options).
  2. If outcome is determinable and the existing grade is wrong → correct it.
  3. If outcome is undeterminable → set suspect_grade=true, add suspect_reason,
     and append a copy to the matching *_suspect_archive.json.
     The original record is NEVER deleted — only annotated.

Run once manually; safe to re-run (idempotent — already-suspect rows are skipped).
"""
import sys, json, os, sqlite3, logging
sys.path.insert(0, '/root/jarvis')

from datetime import datetime, date, timedelta, timezone
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("backfill_grades")

DB_PATH          = "/root/jarvis/jarvis_memory.db"
PAPER_TRADES     = "/root/jarvis/paper_trades.json"
OPT_SUSPECT_FILE = "/root/jarvis/options_trades_suspect_archive.json"
KAL_SUSPECT_FILE = "/root/jarvis/kalshi_suspect_archive.json"
PT_SUSPECT_FILE  = "/root/jarvis/paper_trades_suspect_archive.json"


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

def _append_suspect(archive_path, record, reason):
    archive = _load_json(archive_path, [])
    rec_copy = dict(record)
    rec_copy["suspect_grade"]  = True
    rec_copy["suspect_reason"] = reason
    rec_copy["archived_at"]    = datetime.now().isoformat()
    archive.append(rec_copy)
    _save_json(archive_path, archive)
    log.info(f"  → archived to {os.path.basename(archive_path)}: {reason}")

def _yf_close_on(ticker, target_date_str):
    """Return closing price for ticker on target_date_str (YYYY-MM-DD).
    Returns None if yfinance unavailable or no data."""
    try:
        import yfinance as yf
        start = target_date_str
        end   = (datetime.strptime(target_date_str, "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")
        hist  = yf.Ticker(ticker).history(start=start, end=end)
        if hist.empty:
            return None
        return float(hist["Close"].iloc[0])
    except Exception as e:
        log.warning(f"yfinance {ticker} {target_date_str}: {e}")
        return None

def _btc_ticks_price_at(conn, ts_str):
    """Price from btc_ticks closest to ts_str. Returns None if no ticks."""
    row = conn.execute(
        "SELECT price FROM btc_ticks WHERE ts >= ? ORDER BY ts ASC LIMIT 1",
        (ts_str,)
    ).fetchone()
    return float(row[0]) if row else None


# ── 1. options_trades audit ───────────────────────────────────────────────────

def fix_options_trades():
    log.info("=== options_trades ===")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 1a. Orphan open rows — signal logs older than 48h with no result.
    #     Skips records from the last 48h (may still be live paper positions).
    cutoff_48h = (datetime.now() - timedelta(hours=48)).isoformat()
    orphans = conn.execute(
        "SELECT * FROM options_trades WHERE status IN ('paper','open') AND result IS NULL AND ts < ?",
        (cutoff_48h,)
    ).fetchall()

    # Load paper_trades_store closed index for matching
    import paper_trades_store as _pts
    pt_data   = _pts.read()
    closed_pt = {}
    for pt in pt_data.get("trades", []):
        if pt.get("status") != "paper_closed" or not pt.get("result"):
            continue
        _k = (pt.get("ticker"), pt.get("strategy"),
              round(float(pt.get("strike") or 0), 2),
              (pt.get("entry_date") or ""))
        if _k not in closed_pt:
            closed_pt[_k] = pt

    for r in orphans:
        r = dict(r)
        notes_val = r.get('notes') or ''
        if notes_val.startswith('suspect') or notes_val.startswith('backfill'):
            continue
        log.info(f"Orphan open: id={r['id']} {r['ticker']} {r['strategy']} ${r['strike']} ts={r['ts']}")

        # Try to match against paper_trades_store closed entries
        entry_date = (r['ts'] or '')[:10]
        match_key  = (r['ticker'], r['strategy'], round(float(r['strike'] or 0), 2), entry_date)
        pt_match   = closed_pt.get(match_key)

        if pt_match:
            result = pt_match.get("result")
            pnl    = float(pt_match.get("pnl") or 0)
            reason = f"Backfill: matched paper_closed entry result={result} pnl={pnl}"
            conn.execute(
                "UPDATE options_trades SET result=?, pnl=?, status='closed', closed_at=?, notes=? WHERE id=?",
                (result, pnl, datetime.now().isoformat(), f"backfill: {reason}", r['id'])
            )
            log.info(f"  GRADED id={r['id']}: {result} pnl={pnl} — matched paper_trades_store")
            continue

        # No paper_trades match — try yfinance expiry inference
        inferred = None
        reason   = (
            f"Open record (id={r['id']}) never linked to paper_trades_store — "
            "no matching paper_closed entry; outcome undeterminable"
        )
        if r.get('ticker') and r.get('strike') and r.get('expiry'):
            stock_price = _yf_close_on(r['ticker'], r['expiry'])
            if stock_price is not None:
                strike   = float(r['strike'])
                is_short = r.get('strategy','') in ('put_sell','call_sell')
                if 'put' in (r.get('strategy') or ''):
                    itm = stock_price < strike
                elif 'call' in (r.get('strategy') or ''):
                    itm = stock_price > strike
                else:
                    itm = None
                if itm is not None:
                    inferred = ("LOSS" if itm else "WIN") if is_short else ("WIN" if itm else "LOSS")
                    reason = (
                        f"Backfill: {r['ticker']} @ expiry ${stock_price:.2f} vs strike ${strike:.2f} "
                        f"({'ITM' if itm else 'OTM'}) → {inferred}"
                    )
        if inferred:
            conn.execute(
                "UPDATE options_trades SET result=?, status='closed', closed_at=?, notes=? WHERE id=?",
                (inferred, datetime.now().isoformat(), f"backfill: {reason}", r['id'])
            )
            log.info(f"  GRADED id={r['id']}: {inferred} — {reason}")
        else:
            conn.execute(
                "UPDATE options_trades SET notes=? WHERE id=?",
                (f"suspect: {reason}", r['id'])
            )
            _append_suspect(OPT_SUSPECT_FILE, r, reason)

    # 1b. Closed rows with pnl=0.0 (bulk-graded by old Alpaca-based grader)
    bad_pnl = conn.execute(
        "SELECT * FROM options_trades WHERE status='closed' AND result IS NOT NULL AND pnl=0.0"
    ).fetchall()
    for r in bad_pnl:
        r = dict(r)
        if (r.get('notes') or '').startswith('suspect'):
            continue
        reason = (
            f"pnl=0.0 on closed row (id={r['id']}) — written by old Alpaca-based grader "
            "that bulk-graded paper trades as LOSS/$0 because Alpaca had no position. "
            "True P&L undeterminable without premium history."
        )
        log.info(f"Bad pnl=0: id={r['id']} {r['ticker']} {r['result']} ts={r['ts']}")
        conn.execute(
            "UPDATE options_trades SET notes=? WHERE id=?",
            (f"suspect: {reason}", r['id'])
        )
        _append_suspect(OPT_SUSPECT_FILE, r, reason)

    conn.commit()
    conn.close()
    log.info("options_trades done")


# ── 2. kalshi_bets audit ─────────────────────────────────────────────────────

def fix_kalshi_bets():
    log.info("=== kalshi_bets ===")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    ungraded = conn.execute(
        "SELECT * FROM kalshi_bets WHERE result IS NULL AND ts < ?",
        (cutoff[:19],)   # strip timezone — stored as naive isoformat
    ).fetchall()

    for r in ungraded:
        r = dict(r)
        if r.get('reason','').startswith('suspect') or r.get('notes','').startswith('suspect'):
            continue
        log.info(f"Ungraded kalshi_bet: id={r['id']} {r['symbol']} {r['bet']} ts={r['ts']}")
        # Try btc_ticks for BTC markets
        btc_price = None
        if 'BTC' in str(r.get('symbol','')).upper():
            ts_plus1h = None
            try:
                dt = datetime.fromisoformat(r['ts'])
                ts_plus1h = (dt + timedelta(hours=1)).isoformat()
            except Exception:
                pass
            if ts_plus1h:
                c2 = sqlite3.connect(DB_PATH)
                btc_price = _btc_ticks_price_at(c2, ts_plus1h)
                c2.close()

        inferred = None
        reason = f"Ungraded since {r['ts']} — no result in DB"
        if btc_price and r.get('strike'):
            try:
                strike = float(r['strike'])
                bet    = str(r.get('bet','')).upper()
                # YES = price will be ABOVE strike; NO = price will be BELOW
                if bet == 'YES':
                    inferred = 'WIN' if btc_price >= strike else 'LOSS'
                elif bet == 'NO':
                    inferred = 'WIN' if btc_price < strike else 'LOSS'
                reason = (
                    f"Backfill: BTC @ +1h price ${btc_price:,.0f} vs strike ${strike:,.0f} → {inferred}"
                )
            except Exception as e:
                log.warning(f"backfill calc error: {e}")

        if inferred:
            pnl = float(r.get('dollars') or r.get('yes_price') or 0) * (1 if inferred == 'WIN' else -1)
            conn.execute(
                "UPDATE kalshi_bets SET result=?, pnl=?, graded_at=?, reason=? WHERE id=?",
                (inferred, pnl, datetime.now().isoformat(),
                 (r.get('reason') or '') + f' | backfill: {reason}', r['id'])
            )
            log.info(f"  GRADED id={r['id']}: {inferred} pnl={pnl} — {reason}")
        else:
            reason_full = reason + " — btc_ticks empty or no strike; outcome undeterminable"
            conn.execute(
                "UPDATE kalshi_bets SET reason=? WHERE id=?",
                ((r.get('reason') or '') + f' | suspect: {reason_full}', r['id'])
            )
            _append_suspect(KAL_SUSPECT_FILE, r, reason_full)

    conn.commit()
    conn.close()
    log.info("kalshi_bets done")


# ── 3. paper_trades.json — wrong-formula put_sell records ────────────────────

def fix_paper_trades():
    log.info("=== paper_trades.json ===")
    import paper_trades_store as pts

    def _mutate(data):
        count = 0
        for t in data.get("trades", []):
            if t.get("suspect_grade"):
                continue  # already processed
            strat = t.get("strategy", "")
            is_short = strat in ("put_sell", "call_sell")
            if not is_short:
                continue
            result = t.get("result")
            pnl    = t.get("pnl")
            exit_reason = t.get("exit_reason", "")
            if result is None or pnl is None:
                continue
            # Detect wrong-formula symptom: WIN + positive pnl but exit_reason says
            # premium INCREASED (TAKE_PROFIT +X% where X > 0 for a short position).
            # With correct formula, TAKE_PROFIT on short = premium DECREASED.
            # The old grader wrote TAKE_PROFIT when (current-entry)/entry >= 0.50,
            # meaning the option price rose — the WRONG direction for a short.
            wrong_tp = (result == "WIN" and float(pnl or 0) > 0
                        and "TAKE_PROFIT" in exit_reason)
            wrong_sl = (result == "LOSS" and float(pnl or 0) < 0
                        and "STOP_LOSS" in exit_reason)
            if wrong_tp or wrong_sl:
                reason = (
                    f"Graded by pre-fix options_grader using long-position formula for "
                    f"short strategy '{strat}': exit_reason='{exit_reason}' result={result} pnl={pnl}. "
                    "With correct formula the result is inverted. Historical option expired — "
                    "true P&L unverifiable without option-chain history."
                )
                log.info(f"Wrong-formula short: {t.get('ticker')} {strat} ${t.get('strike')} — flagging")
                t["suspect_grade"]  = True
                t["suspect_reason"] = reason
                _append_suspect(PT_SUSPECT_FILE, t, reason)
                count += 1
        return count

    flagged = pts.update(_mutate)
    log.info(f"paper_trades: flagged {flagged} wrong-formula short records as suspect")


# ── 4. kalshi_brain.json — ungraded bets ─────────────────────────────────────

def fix_kalshi_brain():
    log.info("=== kalshi_brain.json ===")
    BRAIN_FILE = "/root/jarvis/kalshi_brain.json"
    data = _load_json(BRAIN_FILE, {})
    bets = data.get("bets", [])
    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=24)
    changed = 0

    for b in bets:
        if b.get("suspect_grade") or b.get("result") in ("WIN","LOSS","VOID"):
            continue
        ts_str = b.get("ts", "")
        try:
            dt = datetime.strptime(ts_str[:16], "%Y-%m-%d %H:%M")
            dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if dt >= cutoff_dt:
            continue

        reason = (
            f"Ungraded since {ts_str} — no result recorded. "
            "Outcome undeterminable: no market resolution data available."
        )
        b["suspect_grade"]  = True
        b["suspect_reason"] = reason
        _append_suspect(KAL_SUSPECT_FILE, b, reason)
        changed += 1
        log.info(f"  flagged kalshi_brain bet id={b.get('id')} ts={ts_str} as suspect")

    if changed:
        _save_json(BRAIN_FILE, data)
    log.info(f"kalshi_brain: {changed} bets flagged as suspect")


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Starting backfill_grades — read your audit report before running this")
    fix_options_trades()
    fix_kalshi_bets()
    fix_paper_trades()
    fix_kalshi_brain()
    log.info("backfill_grades complete")
    log.info(f"Suspect archives: {OPT_SUSPECT_FILE}, {KAL_SUSPECT_FILE}, {PT_SUSPECT_FILE}")
