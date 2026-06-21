#!/usr/bin/env python3
"""
JARVIS CENTRAL BRAIN v2 — SQLite-backed, race-condition-free
Drop-in compatible with existing jarvis_brain.py API.

Upgrades:
- SQLite as primary store (atomic, concurrent-safe)
- JSON as fallback / legacy read
- Bot heartbeat tracking via DB
- Intel signal grading (feedback loop)
- File lock replaced with SQLite WAL mode
"""
import json, os, time, sqlite3, logging
from datetime import datetime, timedelta
from contextlib import contextmanager

log = logging.getLogger("jarvis_brain")

BRAIN_FILE   = "/root/jarvis/jarvis_central_brain.json"
DB_PATH      = "/root/jarvis/jarvis_memory.db"
LOCK_FILE    = "/root/jarvis/jarvis_brain.lock"
LOCK_TIMEOUT = 5

# ── DB connection ────────────────────────────────────────────────────────────

@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")   # concurrent reads + single write
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()

def _ensure_tables():
    with _db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS brain (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS bot_heartbeats (
            bot_name TEXT PRIMARY KEY,
            last_seen TEXT,
            pid INTEGER,
            errors INTEGER DEFAULT 0,
            last_error TEXT,
            alive INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS intel_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            signal_type TEXT,
            ticker TEXT,
            source TEXT,
            detail TEXT,
            graded INTEGER DEFAULT 0,
            price_at_signal REAL,
            price_30d REAL,
            outcome TEXT,
            graded_at TEXT
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
        CREATE INDEX IF NOT EXISTS idx_brain_key ON brain(key);
        CREATE INDEX IF NOT EXISTS idx_heartbeat_bot ON bot_heartbeats(bot_name);
        CREATE INDEX IF NOT EXISTS idx_intel_ticker ON intel_signals(ticker);
        CREATE INDEX IF NOT EXISTS idx_intel_graded ON intel_signals(graded);
        CREATE INDEX IF NOT EXISTS idx_kalshi_ts ON kalshi_bets(ts);
        CREATE INDEX IF NOT EXISTS idx_predictions_ts ON predictions(ts);
        """)

# ── Brain key-value ──────────────────────────────────────────────────────────

def _brain_set(key: str, value):
    val = json.dumps(value) if not isinstance(value, str) else value
    with _db() as conn:
        conn.execute("""
            INSERT INTO brain(key,value,updated_at) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """, (key, val, datetime.now().isoformat()))

def _brain_get(key: str, default=None):
    try:
        with _db() as conn:
            row = conn.execute("SELECT value FROM brain WHERE key=?", (key,)).fetchone()
            if not row: return default
            try: return json.loads(row["value"])
            except: return row["value"]
    except:
        return default

def _brain_get_all() -> dict:
    try:
        with _db() as conn:
            rows = conn.execute("SELECT key, value FROM brain").fetchall()
            result = {}
            for row in rows:
                try: result[row["key"]] = json.loads(row["value"])
                except: result[row["key"]] = row["value"]
            return result
    except:
        return {}

# ── File lock (legacy fallback) ──────────────────────────────────────────────

def _acquire_lock():
    start = time.time()
    while os.path.exists(LOCK_FILE):
        if time.time() - start > LOCK_TIMEOUT:
            try: os.remove(LOCK_FILE)
            except: pass
            break
        time.sleep(0.05)
    try:
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
    except:
        pass

def _release_lock():
    try: os.remove(LOCK_FILE)
    except: pass

# ── Public API (drop-in compatible) ─────────────────────────────────────────

def read_brain() -> dict:
    """Read full brain state — SQLite primary, JSON fallback."""
    try:
        db_data = _brain_get_all()
        if db_data:
            # Merge with JSON for any keys not yet in DB
            try:
                with open(BRAIN_FILE) as f:
                    json_data = json.load(f)
                for k, v in json_data.items():
                    if k not in db_data:
                        db_data[k] = v
            except:
                pass
            return db_data
    except:
        pass
    # Full fallback to JSON. Guarantee a dict return: json.load() yields None if
    # the file holds literal `null` (and a list/str if otherwise corrupted), which
    # would make every caller's read_brain().get(...) raise 'NoneType'... — the
    # root cause of the morning-briefing crashes. Fall back to defaults instead.
    try:
        with open(BRAIN_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except:
        pass
    return _default_brain()

def write_brain(updates: dict):
    """Write updates atomically to SQLite + sync JSON."""
    # Write each key to SQLite
    for key, value in updates.items():
        try:
            _brain_set(key, value)
        except Exception as e:
            log.warning(f"SQLite write failed for {key}: {e}")

    # Also sync full brain to JSON (for legacy bots still reading JSON)
    _acquire_lock()
    try:
        brain = read_brain()
        brain.update(updates)
        brain["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tmp = BRAIN_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(brain, f, indent=2)
        os.replace(tmp, BRAIN_FILE)  # atomic replace
    except Exception as e:
        log.warning(f"JSON sync failed: {e}")
    finally:
        _release_lock()

# ── Bot heartbeat ────────────────────────────────────────────────────────────

def update_bot_heartbeat(bot_name: str):
    """Call this from each bot's main loop to signal it's alive."""
    try:
        pid = os.getpid()
        with _db() as conn:
            conn.execute("""
                INSERT INTO bot_heartbeats(bot_name,last_seen,pid,alive)
                VALUES(?,?,?,1)
                ON CONFLICT(bot_name) DO UPDATE SET
                    last_seen=excluded.last_seen,
                    pid=excluded.pid,
                    alive=1
            """, (bot_name, datetime.now().isoformat(), pid))
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")

def log_bot_error(bot_name: str, error_msg: str):
    try:
        with _db() as conn:
            conn.execute("""
                INSERT INTO bot_heartbeats(bot_name,last_seen,errors,last_error,alive)
                VALUES(?,?,1,?,1)
                ON CONFLICT(bot_name) DO UPDATE SET
                    errors=errors+1,
                    last_error=excluded.last_error,
                    last_seen=excluded.last_seen
            """, (bot_name, datetime.now().isoformat(), str(error_msg)[:200]))
    except:
        pass

# Canonical bot roster — the full set of long-running processes. Liveness is
# checked per-name via pgrep, so add new bots here (and to jarvis_api LOGS).
BOT_ROSTER = [
    "jarvis_master", "jarvis_api", "jarvis_beast", "jarvis_briefing", "jarvis_congress",
    "jarvis_futures", "jarvis_intelligence", "jarvis_level5", "jarvis_options_brain",
    "jarvis_premium", "jarvis_stocks_v2", "jarvis_trader", "jarvis_trump_monitor",
    "jarvis_watchdog", "lenny_predictions", "lenny_trader_bot", "kalshi_grader",
    "options_grader", "jarvis_cascade",
]

def get_bot_status() -> dict:
    """Liveness for the full bot roster.

    alive = the process is actually running (pgrep) — robust to ANY cycle cadence
    (15-min graders, hourly oracle, weekend-idle bots) instead of false-flagging
    a slow/quiet bot as dead. Heartbeat last_seen/errors from the SQLite table are
    merged in as supplementary telemetry (heartbeat_fresh = beat within 10 min).
    """
    import subprocess
    meta = {}
    try:
        with _db() as conn:
            for row in conn.execute("SELECT * FROM bot_heartbeats").fetchall():
                meta[row["bot_name"]] = row
    except Exception:
        pass
    status = {}
    for name in BOT_ROSTER:
        try:
            proc = subprocess.run(["pgrep", "-f", name + "[.]py"],
                                  capture_output=True).returncode == 0
        except Exception:
            proc = False
        row = meta.get(name)
        last_seen = row["last_seen"] if row else ""
        try:
            age = (datetime.now() - datetime.fromisoformat(last_seen)).total_seconds()
        except Exception:
            age = None
        status[name] = {
            "alive": proc,
            "process": proc,
            "last_seen": last_seen,
            "pid": row["pid"] if row else None,
            "errors": row["errors"] if row else 0,
            "last_error": row["last_error"] if row else None,
            "heartbeat_fresh": age is not None and age < 600,
            "age_min": round(age / 60, 1) if age is not None else None,
        }
    return status

# ── Intel signal grading (feedback loop) ────────────────────────────────────

# Pure-crypto symbols — Alpaca's equity endpoint mis-resolves these (e.g. "BTC"
# matches the Grayscale ETF, not bitcoin), so they cannot be priced or graded
# via the stock quote API. Crypto-adjacent EQUITIES (COIN, MSTR, MARA) are not
# listed here — those grade normally.
CRYPTO_TICKERS = {
    "BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "AVAX", "MATIC", "DOT",
    "LTC", "BCH", "LINK", "SHIB", "TRX", "UNI", "ATOM", "XLM", "ETC",
}

def is_crypto_ticker(ticker: str) -> bool:
    return bool(ticker) and ticker.upper().replace("USD", "").replace("-", "") in CRYPTO_TICKERS

def get_quote_price(ticker: str) -> float:
    """
    Best-effort current mid price for a US equity via Alpaca.
    Returns 0 on any failure (non-equity ticker, network error, etc.).
    Crypto symbols return 0 — the equity endpoint cannot price them.
    """
    if not ticker or is_crypto_ticker(ticker):
        return 0
    try:
        r = __import__("requests").get(
            f"https://data.alpaca.markets/v2/stocks/{ticker}/quotes/latest",
            headers={
                "APCA-API-KEY-ID": __import__("jarvis_secrets").ALPACA_PAPER_KEY,
                "APCA-API-SECRET-KEY": __import__("jarvis_secrets").ALPACA_PAPER_SECRET
            }, timeout=5
        )
        if r.status_code == 200:
            q = r.json().get("quote", {})
            mid = (q.get("ap", 0) + q.get("bp", 0)) / 2
            if mid > 0:
                return mid
    except Exception:
        pass
    return 0

_INTEL_DEDUP: dict = {}

def log_intel_signal(signal_type: str, ticker: str, source: str,
                     detail: str, price_at_signal: float = 0):
    """
    Log an intelligence signal for later grading.
    Deduped: same ticker+signal_type+source skipped within 4 hours.
    """
    import time as _time
    dedup_key = (ticker, signal_type, source)
    now = _time.time()
    if now - _INTEL_DEDUP.get(dedup_key, 0) < 14400:
        return
    _INTEL_DEDUP[dedup_key] = now
    try:
        if not price_at_signal:
            price_at_signal = get_quote_price(ticker)
        with _db() as conn:
            conn.execute("""
                INSERT INTO intel_signals(ts,signal_type,ticker,source,detail,price_at_signal)
                VALUES(?,?,?,?,?,?)
            """, (datetime.now().isoformat(), signal_type, ticker, source, detail, price_at_signal))
    except Exception as e:
        log.warning(f"Intel signal log failed: {e}")

def grade_intel_signals():
    """
    Grade ungraded signals older than 30 days.
    Marks outcome as WIN (price up >5%) or LOSS (price down >5%) or NEUTRAL.
    Call this from jarvis_capital or jarvis_intelligence periodically.
    """
    try:
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        with _db() as conn:
            ungraded = conn.execute("""
                SELECT * FROM intel_signals
                WHERE graded=0 AND ts < ? AND price_at_signal > 0
            """, (cutoff,)).fetchall()

        if not ungraded:
            return 0

        # Get current prices for ungraded tickers (skip crypto — unpriceable here)
        tickers = list(set(row["ticker"] for row in ungraded
                           if not is_crypto_ticker(row["ticker"])))
        current_prices = {}
        for ticker in tickers:
            mid = get_quote_price(ticker)
            if mid > 0:
                current_prices[ticker] = mid

        graded_count = 0
        with _db() as conn:
            for row in ungraded:
                ticker = row["ticker"]
                if ticker not in current_prices:
                    continue
                price_now = current_prices[ticker]
                price_then = row["price_at_signal"]
                pct_change = (price_now - price_then) / price_then * 100

                if pct_change >= 5:
                    outcome = "WIN"
                elif pct_change <= -5:
                    outcome = "LOSS"
                else:
                    outcome = "NEUTRAL"

                conn.execute("""
                    UPDATE intel_signals
                    SET graded=1, price_30d=?, outcome=?, graded_at=?
                    WHERE id=?
                """, (price_now, outcome, datetime.now().isoformat(), row["id"]))
                graded_count += 1

        log.info(f"Graded {graded_count} intel signals")
        return graded_count
    except Exception as e:
        log.error(f"Grade intel signals error: {e}")
        return 0

def get_intel_signal_stats() -> dict:
    """Get win rates by signal type — tells you which signals actually predict moves."""
    try:
        with _db() as conn:
            rows = conn.execute("""
                SELECT signal_type, outcome, COUNT(*) as cnt
                FROM intel_signals
                WHERE graded=1
                GROUP BY signal_type, outcome
            """).fetchall()

            stats = {}
            for row in rows:
                st = row["signal_type"]
                if st not in stats:
                    stats[st] = {"WIN": 0, "LOSS": 0, "NEUTRAL": 0, "total": 0}
                stats[st][row["outcome"]] = row["cnt"]
                stats[st]["total"] += row["cnt"]

            # Add win rates
            for st, data in stats.items():
                total = data["total"]
                if total > 0:
                    data["win_rate"] = round(data["WIN"] / total * 100, 1)
                else:
                    data["win_rate"] = 0

            return stats
    except:
        return {}

# ── P&L aggregation ──────────────────────────────────────────────────────────

def get_kalshi_stats() -> dict:
    try:
        with _db() as conn:
            rows = conn.execute("""
                SELECT bet, result, COUNT(*) as cnt, SUM(pnl) as total_pnl
                FROM kalshi_bets WHERE result IS NOT NULL
                GROUP BY bet, result
            """).fetchall()
            stats = {}
            for row in rows:
                bet = row["bet"]
                # Only YES/NO bets count; VOID = unexecuted (not a loss),
                # and corrupt side values (e.g. "50") are ignored.
                if bet not in ("YES", "NO"): continue
                if row["result"] not in ("WIN", "LOSS"): continue
                if bet not in stats: stats[bet] = {"wins":0,"losses":0,"pnl":0}
                if row["result"] == "WIN": stats[bet]["wins"] += row["cnt"]
                else: stats[bet]["losses"] += row["cnt"]
                stats[bet]["pnl"] += row["total_pnl"] or 0
            return stats
    except:
        return {}

def get_prediction_stats() -> dict:
    try:
        with _db() as conn:
            total    = conn.execute("SELECT COUNT(*) FROM predictions WHERE graded=1").fetchone()[0]
            wins     = conn.execute("SELECT COUNT(*) FROM predictions WHERE graded=1 AND outcome='WIN'").fetchone()[0]
            yes_wins = conn.execute("SELECT COUNT(*) FROM predictions WHERE graded=1 AND bet='YES' AND outcome='WIN'").fetchone()[0]
            yes_tot  = conn.execute("SELECT COUNT(*) FROM predictions WHERE graded=1 AND bet='YES'").fetchone()[0]
            no_wins  = conn.execute("SELECT COUNT(*) FROM predictions WHERE graded=1 AND bet='NO' AND outcome='WIN'").fetchone()[0]
            no_tot   = conn.execute("SELECT COUNT(*) FROM predictions WHERE graded=1 AND bet='NO'").fetchone()[0]
            return {
                "total":   total,
                "wins":    wins,
                "wr":      round(wins/total*100) if total else 0,
                "yes_wr":  round(yes_wins/yes_tot*100) if yes_tot else 0,
                "no_wr":   round(no_wins/no_tot*100) if no_tot else 0,
            }
    except:
        return {"total":0,"wins":0,"wr":0,"yes_wr":0,"no_wr":0}

def get_options_stats() -> dict:
    try:
        with _db() as conn:
            rows = conn.execute("""
                SELECT strategy, result, COUNT(*) as cnt, SUM(pnl) as total_pnl
                FROM options_trades WHERE result IS NOT NULL
                GROUP BY strategy, result
            """).fetchall()
            stats = {}
            for row in rows:
                s = row["strategy"]
                if s not in stats: stats[s] = {"wins":0,"losses":0,"total_pnl":0}
                if row["result"] == "WIN": stats[s]["wins"] += row["cnt"]
                else: stats[s]["losses"] += row["cnt"]
                stats[s]["total_pnl"] += row["total_pnl"] or 0
            return stats
    except:
        return {}

def get_open_options_trades() -> list:
    try:
        with _db() as conn:
            rows = conn.execute("""
                SELECT * FROM options_trades WHERE status IN ('paper','open') ORDER BY ts DESC
            """).fetchall()
            return [dict(r) for r in rows]
    except:
        return []

def get_morning_summary() -> dict:
    pred_stats   = get_prediction_stats()
    kalshi_stats = get_kalshi_stats()
    options_stats = get_options_stats()
    open_trades  = get_open_options_trades()
    kalshi_pnl   = sum(v["pnl"] for v in kalshi_stats.values())
    options_pnl  = sum(v["total_pnl"] for v in options_stats.values())
    theta_warnings = [t for t in open_trades if (t.get("dte") or 99) <= 7]
    return {
        "pred_stats":     pred_stats,
        "kalshi_pnl":     round(kalshi_pnl, 2),
        "yes_wr":         pred_stats.get("yes_wr", 0),
        "no_wr":          pred_stats.get("no_wr", 0),
        "options_pnl":    round(options_pnl, 2),
        "open_trades":    len(open_trades),
        "theta_warnings": theta_warnings,
    }

# ── Legacy-compatible helpers ────────────────────────────────────────────────

def _default_brain():
    return {
        "last_updated": "",
        "btc_signal": "neutral",
        "btc_price": 0.0,
        "btc_rsi": 50.0,
        "btc_trend_4h": "NEUTRAL",
        "btc_macd": "neutral",
        "market_mood": "neutral",
        "risk_level": "NORMAL",
        "fear_greed": 50,
        "funding_rate": 0.0,
        "volume_ratio": 1.0,
        "hot_tickers": [],
        "sector_leader": "",
        "sector_laggard": "",
        "market_context": {},
        "options_flow_bias": "neutral",
        "dark_pool_alerts": [],
        "kalshi_last_bet": None,
        "kalshi_win_rate": 0.0,
        "kalshi_total_bets": 0,
        "kalshi_wins": 0,
        "kalshi_pnl": 0.0,
        "alpha_last_trade": None,
        "stocks_last_trade": None,
        "options_last_trade": None,
        "daily_pnl": 0.0,
        "total_pnl": 0.0,
        "equity": 0.0,
        "blacklist": {},
        "range_hit": False,
        "winning_conditions": {},
        "losing_conditions": {},
        "consecutive_losses": 0,
        "consecutive_wins": 0,
        "news_queue": [],
        "news_sent_today": 0,
        "news_sent_date": "",
        "news_max_per_day": 3,
        "news_seen_ids": [],
        "briefing_sent_date": "",
        "bot_status": {},
        "improvement_log": [],
    }

def set_btc_signal(signal):    write_brain({"btc_signal": signal})
def set_market_mood(mood):     write_brain({"market_mood": mood})
def set_risk_level(level):     write_brain({"risk_level": level})
def get_risk_level():          return _brain_get("risk_level", "NORMAL")
def get_btc_signal():          return _brain_get("btc_signal", "neutral")
def get_market_mood():         return _brain_get("market_mood", "neutral")
def get_market_context():      return (read_brain() or {}).get("market_context", {}) or {}
def log_alpha_trade(trade):    write_brain({"alpha_last_trade": trade})
def log_stocks_trade(trade):   write_brain({"stocks_last_trade": trade})
def set_intel_summary(s):      write_brain({"intel_summary": s})

def add_hot_ticker(ticker: str):
    tickers = _brain_get("hot_tickers", [])
    if ticker not in tickers:
        tickers.append(ticker)
    write_brain({"hot_tickers": tickers[-20:]})

def blacklist_asset(asset: str):
    bl = _brain_get("blacklist", {})
    bl[asset] = datetime.now().isoformat()
    write_brain({"blacklist": bl})

def is_blacklisted(asset: str) -> bool:
    bl = _brain_get("blacklist", {})
    if asset not in bl: return False
    try:
        banned_at = datetime.fromisoformat(bl[asset])
        if (datetime.now() - banned_at).total_seconds() > 86400:
            del bl[asset]
            write_brain({"blacklist": bl})
            return False
    except:
        pass
    return True

def update_btc_state(price, rsi, trend_4h, macd_hist, funding, vol, fear_greed):
    signal = "neutral"
    if rsi < 35 and macd_hist < 0:      signal = "bearish"
    elif rsi > 65 and macd_hist > 0:    signal = "bullish"
    elif trend_4h in ["STRONG_UP","WEAK_UP"]:   signal = "bullish"
    elif trend_4h in ["STRONG_DOWN","WEAK_DOWN"]: signal = "bearish"
    risk = "NORMAL"
    if funding > 0.002: risk = "HIGH"
    if funding > 0.004 or vol < 0.5: risk = "EXTREME"
    write_brain({
        "btc_price": round(price, 2),
        "btc_rsi": rsi,
        "btc_trend_4h": trend_4h,
        "btc_macd": "bullish" if macd_hist > 0 else "bearish",
        "btc_signal": signal,
        "funding_rate": funding,
        "volume_ratio": vol,
        "fear_greed": fear_greed,
        "risk_level": risk,
    })

def update_kalshi_result(bet_side, won, pnl, pattern_fingerprint=None):
    total   = (_brain_get("kalshi_total_bets", 0) or 0) + 1
    cur_pnl = (_brain_get("kalshi_pnl", 0.0) or 0.0) + pnl
    wins    = (_brain_get("kalshi_wins", 0) or 0) + (1 if won else 0)
    wr      = round(wins / total * 100, 1)
    if pattern_fingerprint:
        winning = _brain_get("winning_conditions", {}) or {}
        losing  = _brain_get("losing_conditions", {}) or {}
        if won: winning[pattern_fingerprint] = winning.get(pattern_fingerprint, 0) + 1
        else:   losing[pattern_fingerprint]  = losing.get(pattern_fingerprint, 0) + 1
        write_brain({"winning_conditions": winning, "losing_conditions": losing})
    consec_w = (_brain_get("consecutive_wins", 0) or 0)
    consec_l = (_brain_get("consecutive_losses", 0) or 0)
    if won: consec_w += 1; consec_l = 0
    else:   consec_l += 1; consec_w = 0
    write_brain({
        "kalshi_total_bets": total,
        "kalshi_wins": wins,
        "kalshi_win_rate": wr,
        "kalshi_pnl": round(cur_pnl, 2),
        "consecutive_wins": consec_w,
        "consecutive_losses": consec_l,
    })

def get_cross_bot_context() -> str:
    b = read_brain()
    lines = [
        f"BTC: ${b.get('btc_price',0):,.0f} RSI:{b.get('btc_rsi',50)} Signal:{b.get('btc_signal','neutral')}",
        f"4H:{b.get('btc_trend_4h','?')} MACD:{b.get('btc_macd','?')} F&G:{b.get('fear_greed',50)}",
        f"Funding:{b.get('funding_rate',0):.4f} Vol:{b.get('volume_ratio',1)}x Risk:{b.get('risk_level','NORMAL')}",
        f"Regime:{(b.get('market_context') or {}).get('regime', b.get('macro_regime','?'))} Lead:{b.get('sector_leader','?')} Lag:{b.get('sector_laggard','?')}",
        f"Kalshi WR:{b.get('kalshi_win_rate',0)}% PnL:${b.get('kalshi_pnl',0):+.0f} Bets:{b.get('kalshi_total_bets',0)}",
        f"Hot:{','.join(str(t.get('ticker',t)) if isinstance(t,dict) else str(t) for t in (b.get('hot_tickers') or [])[-5:])} Sector:{b.get('sector_leader','?')}",
        f"Streak W:{b.get('consecutive_wins',0)} L:{b.get('consecutive_losses',0)}",
    ]
    return "\n".join(lines)

import hashlib
def queue_news(title, tickers, sentiment, source, url=""):
    story_id = hashlib.md5(title.encode()).hexdigest()[:8]
    brain = read_brain()
    seen = brain.get("news_seen_ids", [])
    if story_id in seen:
        return False, "already seen"
    score = 3
    title_lower = title.lower()
    high_impact = ["fed ","federal reserve","fomc","cpi","inflation","rate hike","rate cut",
                   "earnings beat","earnings miss","fda approval","sec charges","bankruptcy",
                   "acquisition","merger","crash","record high","record low"]
    if any(k in title_lower for k in high_impact): score += 3
    priority_tickers = ["BTC","NVDA","SPY","QQQ","TSLA","AAPL","MSFT"]
    if any(t in tickers for t in priority_tickers): score += 2
    elif tickers: score += 1
    if sentiment in ["BULLISH","BEARISH"]: score += 1
    score = min(10, score)
    today = datetime.now().strftime("%Y-%m-%d")
    sent_today  = brain.get("news_sent_today", 0)
    sent_date   = brain.get("news_sent_date", "")
    max_per_day = brain.get("news_max_per_day", 3)
    if sent_date != today: sent_today = 0
    story = {"id":story_id,"ts":datetime.now().strftime("%Y-%m-%d %H:%M"),
             "title":title,"tickers":tickers,"sentiment":sentiment,
             "source":source,"url":url,"score":score,"sent":False}
    should_send = score >= 7 and sent_today < max_per_day
    queue = brain.get("news_queue", [])
    queue.append(story)
    queue = queue[-200:]
    seen.append(story_id)
    seen = seen[-1000:]
    updates = {"news_queue":queue,"news_seen_ids":seen}
    if should_send:
        updates["news_sent_today"] = sent_today + 1
        updates["news_sent_date"]  = today
        story["sent"] = True
    write_brain(updates)
    return should_send, score

def get_unsent_news_for_briefing():
    brain = read_brain()
    queue = brain.get("news_queue", [])
    yesterday = (datetime.now()-timedelta(days=1)).strftime("%Y-%m-%d")
    unsent = [s for s in queue if not s.get("sent") and s.get("ts","") >= yesterday]
    unsent.sort(key=lambda x: x.get("score",0), reverse=True)
    return unsent[:10]

def get_live_kalshi_summary() -> dict:
    """Win rate / PnL computed live from the kalshi_bets table (WIN/LOSS only,
    VOID excluded). The legacy brain counters (kalshi_win_rate/kalshi_pnl) are
    NOT updated by anything anymore — update_kalshi_result() has no callers — so
    reading them gives stale, wrong numbers. Use this instead."""
    stats = get_kalshi_stats()
    wins   = sum(v["wins"]   for v in stats.values())
    losses = sum(v["losses"] for v in stats.values())
    total  = wins + losses
    pnl    = sum(v["pnl"]    for v in stats.values())
    return {
        "wins": wins, "losses": losses, "total": total,
        "wr":  round(wins / total * 100, 1) if total else 0.0,
        "pnl": round(pnl, 2),
    }

def format_morning_briefing():
    brain   = read_brain()
    # read_brain() can return None if the brain file is corrupted (e.g. literal
    # `null`) — guard before the brain.get(...) calls below. This runs unguarded
    # inside jarvis_briefing.send_morning_briefing(), so an unhandled NoneType
    # here crashes the whole 7am briefing.
    if not isinstance(brain, dict):
        brain = {}
    news    = get_unsent_news_for_briefing()
    status  = get_bot_status()  # now from SQLite
    kalshi  = get_live_kalshi_summary()  # live from table, not stale brain keys
    now     = datetime.now()
    dow     = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"][now.weekday()]
    btc_icon = "🟢" if brain.get("btc_signal") == "bullish" else "🔴" if brain.get("btc_signal") == "bearish" else "⚪"

    bot_lines = []
    for bot_name, data in status.items():
        if not data.get("alive"):
            bot_lines.append(f"❌ {bot_name} — last seen {data.get('age_min','?')}min ago")
        elif data.get("errors", 0) > 0:
            bot_lines.append(f"⚠️ {bot_name} — {data['errors']} errors")
        else:
            bot_lines.append(f"✅ {bot_name}")

    news_lines = []
    for s in news[:5]:
        icon = "🟢" if "BULL" in s.get("sentiment","") else "🔴" if "BEAR" in s.get("sentiment","") else "📰"
        news_lines.append(f"{icon} {s['title'][:80]}")

    winning = brain.get("winning_conditions", {})
    best_pattern = ""
    if winning and isinstance(winning, dict):
        try:
            best_fp = max(winning, key=lambda k: winning[k] if isinstance(winning[k],(int,float)) else 0)
            best_pattern = f"{best_fp} ({winning[best_fp]} wins)"
        except: best_pattern = "building..."

    # Intel signal stats
    intel_stats = get_intel_signal_stats()
    intel_lines = []
    for sig_type, data in intel_stats.items():
        if data["total"] >= 5:
            intel_lines.append(f"  {sig_type}: {data['win_rate']}% WR ({data['total']} signals)")

    msg = f"""🌅 JARVIS MORNING BRIEFING
{'='*26}
{dow} {now.strftime('%b %d')} — {now.strftime('%I:%M %p')} EDT
{'='*26}
{btc_icon} BTC: ${brain.get('btc_price',0):,.0f} — {brain.get('btc_signal','neutral').upper()}
Fear & Greed: {brain.get('fear_greed',50)}/100 | Risk: {brain.get('risk_level','NORMAL')}
{'='*26}
💰 PERFORMANCE
Kalshi: {kalshi['wr']}% WR | {kalshi['total']} bets | ${kalshi['pnl']:+.2f}
Streak: {brain.get('consecutive_wins',0)}W / {brain.get('consecutive_losses',0)}L
{'='*26}
🤖 BOT HEALTH
{chr(10).join(bot_lines) if bot_lines else 'No heartbeat data yet'}
{'='*26}
📰 OVERNIGHT NEWS
{chr(10).join(news_lines) if news_lines else 'No major news overnight'}
{'='*26}
🧠 BEST PATTERN: {best_pattern or 'building...'}
{'='*26}
📡 INTEL SIGNAL ACCURACY
{chr(10).join(intel_lines) if intel_lines else 'Building signal history...'}
Text BTC for first prediction"""
    return msg

def log_improvement(change, reason):
    brain = read_brain()
    improvement_log = brain.get("improvement_log", [])
    improvement_log.append({
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "change": change,
        "reason": reason
    })
    write_brain({"improvement_log": improvement_log[-50:],
                 "last_self_improve": datetime.now().strftime("%Y-%m-%d %H:%M")})

def init_brain():
    _ensure_tables()
    if not os.path.exists(BRAIN_FILE):
        _acquire_lock()
        try:
            with open(BRAIN_FILE, "w") as f:
                json.dump(_default_brain(), f, indent=2)
        finally:
            _release_lock()
    else:
        brain = read_brain()
        default = _default_brain()
        updates = {k: v for k, v in default.items() if k not in brain}
        if updates:
            write_brain(updates)
    # Seed SQLite from existing JSON
    try:
        with open(BRAIN_FILE) as f:
            existing = json.load(f)
        for k, v in existing.items():
            try:
                _brain_set(k, v)
            except:
                pass
    except:
        pass
    print("Central brain v2 ready — SQLite + JSON dual write active")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_brain()
    print(get_cross_bot_context())
    print("\nBot status:", get_bot_status())
    print("\nIntel signal stats:", get_intel_signal_stats())
