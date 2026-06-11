"""
jarvis_memory_db.py — Shared SQLite memory layer for all JARVIS bots
Level 2 upgrade: single source of truth, all bots read/write here
"""
import sqlite3, json, time, logging
from datetime import datetime, timedelta
from contextlib import contextmanager

DB_PATH = "/root/jarvis/jarvis_memory.db"
log = logging.getLogger("jarvis_memory_db")

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()

def _migrate_sell_premium(conn):
    """Heal pre-existing schema drift: an older DB created sell_premium_candidates
    with a `timestamp` column, but the current schema + INSERTs (and the
    idx_sell_premium_ts index) use `ts`. Rename it in place so init_db's index
    creation and log_sell_premium_candidate() stop failing. Idempotent: no-op on a
    fresh DB (table absent) or one already on the `ts` schema."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sell_premium_candidates'"
    ).fetchone()
    if not exists:
        return
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sell_premium_candidates)").fetchall()]
    if "timestamp" in cols and "ts" not in cols:
        conn.execute("ALTER TABLE sell_premium_candidates RENAME COLUMN timestamp TO ts")
        log.info("Migrated sell_premium_candidates.timestamp -> ts")

def init_db():
    with get_conn() as conn:
        _migrate_sell_premium(conn)   # heal legacy `timestamp` column before index build
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS brain (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS btc_ticks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            price REAL,
            rsi REAL,
            momentum_1h REAL,
            momentum_24h REAL,
            momentum_7d REAL,
            prediction TEXT,
            outcome TEXT
        );

        CREATE TABLE IF NOT EXISTS kalshi_bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            symbol TEXT,
            strike REAL,
            bet TEXT,
            prob TEXT,
            yes_price REAL,
            no_price REAL,
            reason TEXT,
            result TEXT,
            pnl REAL,
            graded_at TEXT
        );

        CREATE TABLE IF NOT EXISTS options_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            ticker TEXT,
            strategy TEXT,
            strike REAL,
            premium REAL,
            dte INTEGER,
            iv REAL,
            score INTEGER,
            contract_symbol TEXT,
            stock_price REAL,
            regime TEXT,
            fear_greed INTEGER,
            vix REAL,
            btc_signal TEXT,
            catalyst TEXT,
            theta_per_day REAL,
            status TEXT DEFAULT 'paper',
            result TEXT,
            pnl REAL,
            closed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            symbol TEXT,
            price REAL,
            target REAL,
            low REAL,
            high REAL,
            target_prob TEXT,
            predicted_price TEXT,
            range_prob TEXT,
            bet TEXT,
            reason TEXT,
            ev REAL,
            kelly_size REAL,
            graded INTEGER DEFAULT 0,
            actual_price REAL,
            outcome TEXT
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            bot TEXT,
            event_type TEXT,
            data TEXT
        );

        CREATE TABLE IF NOT EXISTS sell_premium_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            ticker TEXT,
            iv_rank REAL,
            market_mode TEXT,
            f_and_g INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_btc_ticks_ts ON btc_ticks(ts);
        CREATE INDEX IF NOT EXISTS idx_kalshi_bets_ts ON kalshi_bets(ts);
        CREATE INDEX IF NOT EXISTS idx_predictions_ts ON predictions(ts);
        CREATE INDEX IF NOT EXISTS idx_events_bot ON events(bot);
        CREATE INDEX IF NOT EXISTS idx_sell_premium_ts ON sell_premium_candidates(ts);
        """)
    log.info("JARVIS memory DB initialized")

# ── Brain (shared key-value store) ──────────────────────────────────────────

def brain_set(key: str, value):
    val = json.dumps(value) if not isinstance(value, str) else value
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO brain(key, value, updated_at) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """, (key, val, datetime.now().isoformat()))

def brain_get(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM brain WHERE key=?", (key,)).fetchone()
        if not row: return default
        try: return json.loads(row["value"])
        except: return row["value"]

def get_regime(default="UNKNOWN") -> str:
    """Canonical market regime — the value last written to the brain by macro
    (jarvis_macro.py) / cascade (jarvis_cascade.py) via brain_set("regime", ...).
    Convenience reader so callers don't repeat the key string."""
    return brain_get("regime", default)

def brain_get_all() -> dict:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM brain").fetchall()
        result = {}
        for row in rows:
            try: result[row["key"]] = json.loads(row["value"])
            except: result[row["key"]] = row["value"]
        return result

# ── Kalshi bets ──────────────────────────────────────────────────────────────

def log_kalshi_bet(symbol, strike, bet, prob, yes_price, no_price, reason, ev=None, kelly=None):
    ts = datetime.now().isoformat()
    cutoff = (datetime.now() - timedelta(seconds=60)).isoformat()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM kalshi_bets WHERE symbol=? AND bet=? AND ts >= ?",
            (symbol, bet, cutoff)
        ).fetchone()
        if existing:
            log.warning(f"log_kalshi_bet dedup: {symbol} {bet} already logged id={existing['id']} — skipping")
            return existing["id"]
        cur = conn.execute("""
            INSERT INTO kalshi_bets(ts,symbol,strike,bet,prob,yes_price,no_price,reason)
            VALUES(?,?,?,?,?,?,?,?)
        """, (ts, symbol, strike, bet, prob, yes_price, no_price, reason))
        return cur.lastrowid

def grade_kalshi_bet(bet_id, result, pnl):
    with get_conn() as conn:
        conn.execute("""
            UPDATE kalshi_bets SET result=?, pnl=?, graded_at=? WHERE id=?
        """, (result, pnl, datetime.now().isoformat(), bet_id))

def get_kalshi_stats() -> dict:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT bet, result, COUNT(*) as cnt, SUM(pnl) as total_pnl
            FROM kalshi_bets WHERE result IS NOT NULL
            GROUP BY bet, result
        """).fetchall()
        stats = {"YES": {"wins":0,"losses":0,"pnl":0}, "NO": {"wins":0,"losses":0,"pnl":0}}
        for row in rows:
            bet = row["bet"]
            if bet not in stats: stats[bet] = {"wins":0,"losses":0,"pnl":0}
            if row["result"] == "WIN": stats[bet]["wins"] += row["cnt"]
            else: stats[bet]["losses"] += row["cnt"]
            stats[bet]["pnl"] += row["total_pnl"] or 0
        return stats

def get_recent_kalshi_bets(limit=10) -> list:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM kalshi_bets ORDER BY ts DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

# ── Options trades ───────────────────────────────────────────────────────────

def log_options_trade(ticker, strategy, strike, premium, dte, iv, score,
                      contract_symbol=None, stock_price=None, regime=None, fear_greed=None, vix=None,
                      btc_signal=None, catalyst="", theta_per_day=0, source=None):
    ts = datetime.now().isoformat()
    cutoff = (datetime.now() - timedelta(seconds=60)).isoformat()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM options_trades WHERE ticker=? AND strategy=? AND strike=? AND ts >= ?",
            (ticker, strategy, float(strike), cutoff)
        ).fetchone()
        if existing:
            log.warning(f"log_options_trade dedup: {ticker} {strategy} ${strike} already logged id={existing['id']} — skipping")
            return existing["id"]
        cur = conn.execute("""
            INSERT INTO options_trades(ts,ticker,strategy,strike,premium,dte,iv,score,
            contract_symbol,stock_price,regime,fear_greed,vix,btc_signal,catalyst,theta_per_day)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (ts, ticker, strategy, strike, premium, dte, iv, score,
              contract_symbol, stock_price, regime, fear_greed, vix, btc_signal, catalyst, theta_per_day))
        return cur.lastrowid

def close_options_trade(trade_id, result, pnl):
    with get_conn() as conn:
        conn.execute("""
            UPDATE options_trades SET status='closed', result=?, pnl=?, closed_at=? WHERE id=?
        """, (result, pnl, datetime.now().isoformat(), trade_id))

def get_options_stats() -> dict:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT strategy, result, COUNT(*) as cnt, SUM(pnl) as total_pnl, AVG(pnl) as avg_pnl
            FROM options_trades WHERE result IS NOT NULL
            GROUP BY strategy, result
        """).fetchall()
        stats = {}
        for row in rows:
            s = row["strategy"]
            if s not in stats: stats[s] = {"wins":0,"losses":0,"total_pnl":0,"avg_pnl":0}
            if row["result"] == "WIN": stats[s]["wins"] += row["cnt"]
            else: stats[s]["losses"] += row["cnt"]
            stats[s]["total_pnl"] += row["total_pnl"] or 0
        return stats

def get_open_options_trades() -> list:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM options_trades WHERE status IN ('paper','open') ORDER BY ts DESC
        """).fetchall()
        return [dict(r) for r in rows]

def log_sell_premium_candidate(ticker, iv_rank, market_mode="", f_and_g=None):
    """Record a rich-IV sell-premium candidate flagged by the options brain.
    Self-creates the table so it works against a pre-existing DB that predates
    the schema addition."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sell_premium_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, ticker TEXT, iv_rank REAL, market_mode TEXT, f_and_g INTEGER
            )
        """)
        conn.execute("""
            INSERT INTO sell_premium_candidates(ts,ticker,iv_rank,market_mode,f_and_g)
            VALUES(?,?,?,?,?)
        """, (datetime.now().isoformat(), ticker, iv_rank, market_mode, f_and_g))

# ── Predictions ──────────────────────────────────────────────────────────────

def log_prediction(symbol, price, target, low, high, target_prob, predicted_price,
                   range_prob, bet, reason, ev=0, kelly_size=0):
    ts = datetime.now().isoformat()
    cutoff = (datetime.now() - timedelta(seconds=60)).isoformat()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM predictions WHERE symbol=? AND target=? AND ts >= ?",
            (symbol, float(target), cutoff)
        ).fetchone()
        if existing:
            log.warning(f"log_prediction dedup: {symbol} target={target} already logged id={existing['id']} — skipping")
            return existing["id"]
        cur = conn.execute("""
            INSERT INTO predictions(ts,symbol,price,target,low,high,target_prob,
            predicted_price,range_prob,bet,reason,ev,kelly_size)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (ts, symbol, price, target, low, high, target_prob,
              predicted_price, range_prob, bet, reason, ev, kelly_size))
        return cur.lastrowid

def get_prediction_stats() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM predictions WHERE graded=1").fetchone()[0]
        wins  = conn.execute("SELECT COUNT(*) FROM predictions WHERE graded=1 AND outcome='WIN'").fetchone()[0]
        yes_wins = conn.execute("SELECT COUNT(*) FROM predictions WHERE graded=1 AND bet='YES' AND outcome='WIN'").fetchone()[0]
        yes_total = conn.execute("SELECT COUNT(*) FROM predictions WHERE graded=1 AND bet='YES'").fetchone()[0]
        no_wins = conn.execute("SELECT COUNT(*) FROM predictions WHERE graded=1 AND bet='NO' AND outcome='WIN'").fetchone()[0]
        no_total = conn.execute("SELECT COUNT(*) FROM predictions WHERE graded=1 AND bet='NO'").fetchone()[0]
        return {
            "total": total, "wins": wins,
            "wr": round(wins/total*100) if total else 0,
            "yes_wr": round(yes_wins/yes_total*100) if yes_total else 0,
            "no_wr": round(no_wins/no_total*100) if no_total else 0,
        }

# ── Events log ───────────────────────────────────────────────────────────────

def log_event(bot: str, event_type: str, data: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO events(ts, bot, event_type, data) VALUES(?,?,?,?)
        """, (datetime.now().isoformat(), bot, event_type, json.dumps(data)))

def log_market_event(event_type, spy_price=None, spy_pct_change=None, vix=None,
                     f_and_g=None, market_mode=None, notes="", ticker_snapshot=None,
                     bot="jarvis_cascade"):
    """Structured market event (cascade L0/L1/L2, dead-cat, regime/VIX drift).
    Packs the market context into the events table's JSON `data` column and
    delegates to log_event — there is no separate market_events table."""
    log_event(bot, event_type, {
        "spy_price": spy_price,
        "spy_pct_change": spy_pct_change,
        "vix": vix,
        "f_and_g": f_and_g,
        "market_mode": market_mode,
        "notes": notes,
        "ticker_snapshot": ticker_snapshot,
    })

def get_recent_events(bot=None, limit=20) -> list:
    with get_conn() as conn:
        if bot:
            rows = conn.execute("""
                SELECT * FROM events WHERE bot=? ORDER BY ts DESC LIMIT ?
            """, (bot, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM events ORDER BY ts DESC LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]

# ── Morning briefing data pull ───────────────────────────────────────────────

def get_morning_summary() -> dict:
    pred_stats = get_prediction_stats()
    kalshi_stats = get_kalshi_stats()
    options_stats = get_options_stats()
    open_trades = get_open_options_trades()

    # Kalshi P&L
    kalshi_pnl = sum(v["pnl"] for v in kalshi_stats.values())
    yes_wr = pred_stats.get("yes_wr", 0)
    no_wr  = pred_stats.get("no_wr", 0)

    # Options P&L
    options_pnl = sum(v["total_pnl"] for v in options_stats.values())
    open_count = len(open_trades)

    # Theta warnings — trades within 7 DTE
    theta_warnings = [t for t in open_trades if t.get("dte", 99) <= 7]

    return {
        "pred_stats": pred_stats,
        "kalshi_pnl": round(kalshi_pnl, 2),
        "yes_wr": yes_wr,
        "no_wr": no_wr,
        "options_pnl": round(options_pnl, 2),
        "open_trades": open_count,
        "theta_warnings": theta_warnings,
    }

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("DB initialized at", DB_PATH)
    brain_set("test_key", {"hello": "world"})
    print("Brain get:", brain_get("test_key"))
    print("Morning summary:", get_morning_summary())

def grade_predictions_from_kalshi():
    """Sync resolved kalshi_bets outcomes back to predictions table."""
    import sqlite3
    conn = sqlite3.connect('/root/jarvis/jarvis_memory.db')
    cur = conn.cursor()
    cur.execute("""
        UPDATE predictions SET outcome = (
            SELECT kb.result FROM kalshi_bets kb
            WHERE kb.symbol = predictions.symbol
            AND kb.result IS NOT NULL
            AND date(kb.ts) = date(predictions.ts)
            LIMIT 1
        )
        WHERE outcome IS NULL
        AND EXISTS (
            SELECT 1 FROM kalshi_bets kb
            WHERE kb.symbol = predictions.symbol
            AND kb.result IS NOT NULL
            AND date(kb.ts) = date(predictions.ts)
        )
    """)
    graded = cur.rowcount
    conn.commit()
    conn.close()
    return graded
