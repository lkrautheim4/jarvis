#!/usr/bin/env python3
"""
JARVIS CENTRAL BRAIN — Single source of truth for all bots
Replaces jarvis_brain.py — drop-in compatible, adds cross-bot learning
All bots read/write here. One brain. Everything learns from everything.
"""
import json, os, time, sqlite3
from datetime import datetime, timedelta

BRAIN_FILE   = "/root/jarvis/jarvis_central_brain.json"
LOCK_FILE    = "/root/jarvis/jarvis_brain.lock"
LOCK_TIMEOUT = 5  # seconds

# ── SAFE READ/WRITE WITH LOCK ─────────────────────────────────────────────────
def _acquire_lock():
    start = time.time()
    while os.path.exists(LOCK_FILE):
        if time.time() - start > LOCK_TIMEOUT:
            try: os.remove(LOCK_FILE)
            except: pass
            break
        time.sleep(0.05)
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))

def _release_lock():
    try: os.remove(LOCK_FILE)
    except: pass

def read_brain():
    try:
        if os.path.exists(BRAIN_FILE):
            with open(BRAIN_FILE) as f:
                return json.load(f)
    except: pass
    return _default_brain()

def write_brain(updates):
    _acquire_lock()
    try:
        brain = read_brain()
        brain.update(updates)
        brain["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(BRAIN_FILE, "w") as f:
            json.dump(brain, f, indent=2)
    finally:
        _release_lock()

def _default_brain():
    return {
        "last_updated": "",
        # ── MARKET SIGNALS ──
        "btc_signal":   "neutral",    # bullish / bearish / neutral
        "btc_price":    0.0,
        "btc_rsi":      50.0,
        "btc_trend_4h": "NEUTRAL",
        "btc_macd":     "neutral",    # bullish / bearish
        "market_mood":  "neutral",
        "risk_level":   "NORMAL",     # NORMAL / HIGH / EXTREME
        "fear_greed":   50,
        "funding_rate": 0.0,
        "volume_ratio": 1.0,
        # ── CROSS-BOT SIGNALS ──
        "hot_tickers":       [],
        "sector_leader":     "",
        "sector_laggard":    "",
        "options_flow_bias": "neutral",  # bullish / bearish from unusual options
        "dark_pool_alerts":  [],
        "insider_activity":  [],
        # ── KALSHI BRAIN ──
        "kalshi_last_bet":       None,   # YES / NO / SKIP
        "kalshi_last_conf":      0,
        "kalshi_last_target":    0,
        "kalshi_win_rate":       0.0,
        "kalshi_total_bets":     0,
        "kalshi_pnl":            0.0,
        "kalshi_best_pattern":   "",
        "kalshi_blind_spots":    [],     # hours/conditions where we lose
        # ── TRADING STATE ──
        "alpha_last_trade":      None,
        "stocks_last_trade":     None,
        "options_last_trade":    None,
        "daily_pnl":             0.0,
        "daily_trades":          0,
        "total_pnl":             0.0,
        "equity":                0.0,
        "blacklist":             {},
        "range_hit":             False,
        # ── PATTERN LEARNING ──
        "winning_conditions":    {},     # fingerprint → win rate
        "losing_conditions":     {},
        "best_hour_edt":         -1,
        "worst_hour_edt":        -1,
        "best_dow":              -1,     # 0=Mon 6=Sun
        "consecutive_losses":    0,
        "consecutive_wins":      0,
        # ── NEWS THROTTLE ──
        "news_queue":            [],     # scored stories waiting to send
        "news_sent_today":       0,
        "news_sent_date":        "",
        "news_max_per_day":      3,
        "news_seen_ids":         [],
        # ── MORNING BRIEFING ──
        "briefing_sent_date":    "",
        "briefing_data":         {},
        # ── BOT HEALTH ──
        "bot_status": {
            "jarvis_master":       {"alive": False, "last_seen": "", "errors": 0},
            "jarvis_stocks_v2":    {"alive": False, "last_seen": "", "errors": 0},
            "jarvis_options":      {"alive": False, "last_seen": "", "errors": 0},
            "jarvis_level5":       {"alive": False, "last_seen": "", "errors": 0},
            "jarvis_intelligence": {"alive": False, "last_seen": "", "errors": 0},
            "jarvis_watchdog":     {"alive": False, "last_seen": "", "errors": 0},
        },
        # ── SELF IMPROVEMENT LOG ──
        "improvement_log":       [],
        "last_self_improve":     "",
    }

# ── DROP-IN COMPATIBLE API (same as old jarvis_brain.py) ─────────────────────
def set_btc_signal(signal):    write_brain({"btc_signal": signal})
def set_market_mood(mood):     write_brain({"market_mood": mood})
def set_risk_level(level):     write_brain({"risk_level": level})
def get_risk_level():          return read_brain().get("risk_level", "NORMAL")
def get_btc_signal():          return read_brain().get("btc_signal", "neutral")
def get_market_mood():         return read_brain().get("market_mood", "neutral")
def log_alpha_trade(trade):    write_brain({"alpha_last_trade": trade})
def log_stocks_trade(trade):   write_brain({"stocks_last_trade": trade})
def set_intel_summary(s):      write_brain({"intel_summary": s})

def add_hot_ticker(ticker):
    brain = read_brain()
    tickers = brain.get("hot_tickers", [])
    if ticker not in tickers:
        tickers.append(ticker)
    write_brain({"hot_tickers": tickers[-20:]})  # keep last 20

def blacklist_asset(asset):
    brain = read_brain()
    bl = brain.get("blacklist", {})
    bl[asset] = datetime.now().isoformat()
    write_brain({"blacklist": bl})

def is_blacklisted(asset):
    brain = read_brain()
    bl = brain.get("blacklist", {})
    if asset not in bl: return False
    banned_at = datetime.fromisoformat(bl[asset])
    if (datetime.now() - banned_at).total_seconds() > 86400:
        del bl[asset]
        write_brain({"blacklist": bl})
        return False
    return True

# ── NEW: CROSS-BOT SIGNAL UPDATES ─────────────────────────────────────────────
def update_btc_state(price, rsi, trend_4h, macd_hist, funding, vol, fear_greed):
    """Called by jarvis_master every cycle — gives all bots live BTC state"""
    signal = "neutral"
    if rsi < 35 and macd_hist < 0: signal = "bearish"
    elif rsi > 65 and macd_hist > 0: signal = "bullish"
    elif trend_4h in ["STRONG_UP", "WEAK_UP"]: signal = "bullish"
    elif trend_4h in ["STRONG_DOWN", "WEAK_DOWN"]: signal = "bearish"

    risk = "NORMAL"
    if funding > 0.002: risk = "HIGH"
    if funding > 0.004 or vol < 0.5: risk = "EXTREME"

    write_brain({
        "btc_price":    round(price, 2),
        "btc_rsi":      rsi,
        "btc_trend_4h": trend_4h,
        "btc_macd":     "bullish" if macd_hist > 0 else "bearish",
        "btc_signal":   signal,
        "funding_rate": funding,
        "volume_ratio": vol,
        "fear_greed":   fear_greed,
        "risk_level":   risk,
    })

def update_kalshi_result(bet_side, won, pnl, pattern_fingerprint=None):
    """Called after every Kalshi bet result — cross-references patterns"""
    brain = read_brain()
    total = brain.get("kalshi_total_bets", 0) + 1
    current_pnl = brain.get("kalshi_pnl", 0.0) + pnl
    wins = brain.get("kalshi_wins", 0) + (1 if won else 0)
    wr = round(wins / total * 100, 1)

    # Update pattern learning
    if pattern_fingerprint:
        winning = brain.get("winning_conditions", {})
        losing  = brain.get("losing_conditions", {})
        if won:
            winning[pattern_fingerprint] = winning.get(pattern_fingerprint, 0) + 1
        else:
            losing[pattern_fingerprint] = losing.get(pattern_fingerprint, 0) + 1
        write_brain({
            "winning_conditions": winning,
            "losing_conditions":  losing,
        })

    # Streak tracking
    consec_w = brain.get("consecutive_wins", 0)
    consec_l = brain.get("consecutive_losses", 0)
    if won:
        consec_w += 1; consec_l = 0
    else:
        consec_l += 1; consec_w = 0

    write_brain({
        "kalshi_total_bets":  total,
        "kalshi_wins":        wins,
        "kalshi_win_rate":    wr,
        "kalshi_pnl":         round(current_pnl, 2),
        "consecutive_wins":   consec_w,
        "consecutive_losses": consec_l,
    })

def update_bot_heartbeat(bot_name):
    """Each bot calls this every cycle so watchdog knows it's alive"""
    brain = read_brain()
    status = brain.get("bot_status", {})
    if bot_name not in status:
        status[bot_name] = {"alive": False, "last_seen": "", "errors": 0}
    status[bot_name]["alive"] = True
    status[bot_name]["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    write_brain({"bot_status": status})

def log_bot_error(bot_name, error_msg):
    """Log errors per bot so morning briefing can report them"""
    brain = read_brain()
    status = brain.get("bot_status", {})
    if bot_name not in status:
        status[bot_name] = {"alive": True, "last_seen": "", "errors": 0}
    status[bot_name]["errors"] = status[bot_name].get("errors", 0) + 1
    status[bot_name]["last_error"] = str(error_msg)[:200]
    write_brain({"bot_status": status})

def get_cross_bot_context():
    """Returns a rich context string for Claude — all bots' combined knowledge"""
    brain = read_brain()
    lines = [
        f"BTC: ${brain.get('btc_price',0):,.0f} RSI:{brain.get('btc_rsi',50)} Signal:{brain.get('btc_signal','neutral')}",
        f"4H:{brain.get('btc_trend_4h','?')} MACD:{brain.get('btc_macd','?')} F&G:{brain.get('fear_greed',50)}",
        f"Funding:{brain.get('funding_rate',0):.4f} Vol:{brain.get('volume_ratio',1)}x Risk:{brain.get('risk_level','NORMAL')}",
        f"Mood:{brain.get('market_mood','neutral')} Sector leader:{brain.get('sector_leader','?')}",
        f"Kalshi WR:{brain.get('kalshi_win_rate',0)}% P&L:${brain.get('kalshi_pnl',0):+.0f} Bets:{brain.get('kalshi_total_bets',0)}",
        f"Hot tickers:{','.join(brain.get('hot_tickers',[])[-5:])}",
        f"Options flow:{brain.get('options_flow_bias','neutral')}",
        f"Consec wins:{brain.get('consecutive_wins',0)} losses:{brain.get('consecutive_losses',0)}",
    ]
    return "\n".join(lines)

# ── NEWS THROTTLE ─────────────────────────────────────────────────────────────
def score_news(title, tickers_mentioned, sentiment, source):
    """
    Score a news story 1-10 for importance.
    Only stories scoring 7+ get sent immediately.
    Rest go into morning briefing.
    """
    score = 3  # base
    title_lower = title.lower()

    # High impact keywords
    high_impact = ["fed ", "federal reserve", "fomc", "cpi", "inflation", "rate hike",
                   "rate cut", "earnings beat", "earnings miss", "fda approval",
                   "sec charges", "bankruptcy", "acquisition", "merger", "crash",
                   "circuit breaker", "halted", "record high", "record low"]
    if any(k in title_lower for k in high_impact): score += 3

    # Ticker relevance
    priority_tickers = ["BTC", "NVDA", "SPY", "QQQ", "TSLA", "AAPL", "MSFT"]
    if any(t in tickers_mentioned for t in priority_tickers): score += 2
    elif tickers_mentioned: score += 1

    # Sentiment clarity
    if sentiment in ["🟢 BULLISH", "🔴 BEARISH"]: score += 1

    # Source credibility
    top_sources = ["Yahoo Finance", "CNBC Markets", "CoinDesk", "MarketWatch"]
    if source in top_sources: score += 1

    return min(10, score)

def queue_news(title, tickers, sentiment, source, url=""):
    """Add a news story to the queue. Send immediately if score >= 7 and under daily limit."""
    import hashlib
    story_id = hashlib.md5(title.encode()).hexdigest()[:8]

    brain = read_brain()
    seen = brain.get("news_seen_ids", [])
    if story_id in seen:
        return False, "already seen"

    score = score_news(title, tickers, sentiment, source)
    today = datetime.now().strftime("%Y-%m-%d")
    sent_today = brain.get("news_sent_today", 0)
    sent_date  = brain.get("news_sent_date", "")
    max_per_day = brain.get("news_max_per_day", 3)

    if sent_date != today:
        sent_today = 0  # reset daily counter

    story = {
        "id": story_id,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "title": title,
        "tickers": tickers,
        "sentiment": sentiment,
        "source": source,
        "url": url,
        "score": score,
        "sent": False,
    }

    # Send immediately if high score and under limit
    should_send = score >= 7 and sent_today < max_per_day

    # Update brain
    queue = brain.get("news_queue", [])
    queue.append(story)
    queue = queue[-200:]  # keep last 200
    seen.append(story_id)
    seen = seen[-1000:]

    updates = {
        "news_queue": queue,
        "news_seen_ids": seen,
    }

    if should_send:
        updates["news_sent_today"] = sent_today + 1
        updates["news_sent_date"] = today
        story["sent"] = True

    write_brain(updates)
    return should_send, score

def get_unsent_news_for_briefing():
    """Pull all unset news stories for morning briefing"""
    brain = read_brain()
    queue = brain.get("news_queue", [])
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    unsent = [s for s in queue if not s.get("sent") and s.get("ts","") >= yesterday]
    unsent.sort(key=lambda x: x.get("score", 0), reverse=True)
    return unsent[:10]

# ── MORNING BRIEFING DATA ─────────────────────────────────────────────────────

# Canonical 19-bot roster (jarvis_health + jarvis_watchdog are monitors, not included)
_BOT_ROSTER = [
    "jarvis_master", "jarvis_api", "jarvis_briefing", "jarvis_intelligence",
    "jarvis_options_brain", "jarvis_stocks_v2", "jarvis_beast", "jarvis_congress",
    "jarvis_level5", "jarvis_cascade", "jarvis_futures", "jarvis_premium",
    "lenny_predictions", "lenny_trader_bot", "jarvis_trader", "jarvis_trump_monitor",
    "options_grader", "kalshi_grader", "btc_ticker",
]

_DB_PATH = "/root/jarvis/jarvis_memory.db"
_STALE_SECS = 600  # 10 min — matches jarvis_health RED threshold

def _read_bot_heartbeats():
    """Read bot_heartbeats from sqlite. Returns {bot_name: last_seen_str}."""
    try:
        con = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True, timeout=5)
        rows = con.execute("SELECT bot_name, last_seen FROM bot_heartbeats").fetchall()
        con.close()
        return {name: ts for name, ts in rows}
    except Exception:
        return {}

def _hb_age(ts_str):
    """Age in seconds of a last_seen timestamp, or None if unparseable."""
    if not ts_str:
        return None
    try:
        ts = datetime.fromisoformat(ts_str)
        return (datetime.now() - ts.replace(tzinfo=None)).total_seconds()
    except Exception:
        return None

def build_briefing_data():
    """Compile everything for the morning briefing"""
    brain = read_brain()
    news = get_unsent_news_for_briefing()

    # Bot health — enumerate full 19-bot roster against bot_heartbeats
    hb = _read_bot_heartbeats()
    bot_lines = []
    for bot in _BOT_ROSTER:
        ts = hb.get(bot)
        age = _hb_age(ts)
        if age is None or age > _STALE_SECS:
            bot_lines.append(f"❌ {bot} — DOWN")
        elif age > 300:
            bot_lines.append(f"⚠️ {bot} — {int(age//60)}m ago")
        else:
            bot_lines.append(f"✅ {bot}")

    # News summary
    news_lines = []
    for story in news[:5]:
        sentiment_icon = "🟢" if "BULL" in story.get("sentiment","") else "🔴" if "BEAR" in story.get("sentiment","") else "📰"
        news_lines.append(f"{sentiment_icon} {story['title'][:80]}")

    # Pattern insights
    winning = brain.get("winning_conditions", {})
    losing  = brain.get("losing_conditions", {})
    best_pattern = ""
    if winning:
        # winning_conditions values are {"count": N, "details": [...]} (written by
        # jarvis_learning.update_winning_conditions), so rank by the count, not the
        # dict itself. Tolerate a bare-int shape too so a schema drift can't crash
        # the whole brief.
        def _wins(v):
            return v.get("count", 0) if isinstance(v, dict) else (v or 0)
        try:
            best_fp = max(winning, key=lambda fp: _wins(winning[fp]))
            best_pattern = f"{best_fp} ({_wins(winning[best_fp])} wins)"
        except Exception:
            best_pattern = ""

    # Equity F&G — read equity_fear_greed (CNN, written by jarvis_macro), not
    # the crypto fear_greed field written by update_btc_state.  Guard 2h staleness.
    eq_fg_data = brain.get("equity_fear_greed") or {}
    eq_fg_val = eq_fg_data.get("value", 50)
    eq_fg_ts  = eq_fg_data.get("ts", "")
    if eq_fg_ts:
        try:
            age = (datetime.now() - datetime.fromisoformat(eq_fg_ts)).total_seconds()
            if age > 7200:
                eq_fg_val = 50
        except Exception:
            eq_fg_val = 50

    # Equity split: baseline (paper start) → current → delta
    _EQUITY_BASELINE = 100_000
    equity_now   = brain.get("equity", 0)
    equity_delta = round(equity_now - _EQUITY_BASELINE, 2)

    return {
        "btc_price":      brain.get("btc_price", 0),
        "btc_signal":     brain.get("btc_signal", "neutral"),
        "kalshi_wr":      brain.get("kalshi_win_rate", 0),
        "kalshi_pnl":     brain.get("kalshi_pnl", 0),
        "kalshi_bets":    brain.get("kalshi_total_bets", 0),
        "total_pnl":      brain.get("total_pnl", 0),
        "equity":         equity_now,
        "equity_baseline": _EQUITY_BASELINE,
        "equity_delta":   equity_delta,
        "risk_level":     brain.get("risk_level", "NORMAL"),
        "fear_greed":     eq_fg_val,
        "bot_health":     "\n".join(bot_lines),
        "news_summary":   "\n".join(news_lines) if news_lines else "No major news overnight",
        "best_pattern":   best_pattern,
        "consec_wins":    brain.get("consecutive_wins", 0),
        "consec_losses":  brain.get("consecutive_losses", 0),
        "hot_tickers":    ", ".join(brain.get("hot_tickers", [])[-5:]),
        "sector_leader":  brain.get("sector_leader", "unknown"),
    }

def format_morning_briefing():
    """Format the full morning briefing message for Telegram"""
    d = build_briefing_data()
    now = datetime.now()
    dow = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"][now.weekday()]
    btc_icon = "🟢" if d["btc_signal"] == "bullish" else "🔴" if d["btc_signal"] == "bearish" else "⚪"

    msg = f"""🌅 JARVIS MORNING BRIEFING
{'='*26}
{dow} {now.strftime('%b %d, %Y')} — {now.strftime('%I:%M %p')} EDT
{'='*26}
{btc_icon} BTC: ${d['btc_price']:,.0f} — {d['btc_signal'].upper()}
Fear & Greed: {d['fear_greed']}/100 | Risk: {d['risk_level']}
{'='*26}
💰 PERFORMANCE
Kalshi: {d['kalshi_wr']}% WR | {d['kalshi_bets']} bets | ${d['kalshi_pnl']:+.2f}
Streak: {d['consec_wins']}W / {d['consec_losses']}L
Equity: ${d['equity_baseline']:,.0f} → ${d['equity']:,.0f} ({d['equity_delta']:+,.0f})
Total P&L: ${d['total_pnl']:+.0f}
{'='*26}
🤖 BOT HEALTH
{d['bot_health']}
{'='*26}
📰 OVERNIGHT NEWS
{d['news_summary']}
{'='*26}
🔥 HOT TICKERS: {d['hot_tickers'] or 'none'}
📊 LEADING SECTOR: {d['sector_leader']}
🧠 BEST PATTERN: {d['best_pattern'] or 'building...'}
{'='*26}
Text BTC for first prediction"""

    return msg

# ── SELF IMPROVEMENT LOG ──────────────────────────────────────────────────────
def log_improvement(change, reason):
    brain = read_brain()
    log = brain.get("improvement_log", [])
    log.append({
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "change": change,
        "reason": reason
    })
    write_brain({
        "improvement_log": log[-50:],
        "last_self_improve": datetime.now().strftime("%Y-%m-%d %H:%M")
    })

# ── INIT ──────────────────────────────────────────────────────────────────────
def init_brain():
    """Initialize brain file if it doesn't exist"""
    if not os.path.exists(BRAIN_FILE):
        _acquire_lock()
        try:
            with open(BRAIN_FILE, "w") as f:
                json.dump(_default_brain(), f, indent=2)
        finally:
            _release_lock()
        print(f"Central brain initialized: {BRAIN_FILE}")
    else:
        # Merge any missing keys into existing brain
        brain = read_brain()
        default = _default_brain()
        updated = False
        for key, val in default.items():
            if key not in brain:
                brain[key] = val
                updated = True
        if updated:
            _acquire_lock()
            try:
                with open(BRAIN_FILE, "w") as f:
                    json.dump(brain, f, indent=2)
            finally:
                _release_lock()
        print(f"Central brain loaded: {len(brain)} keys")

if __name__ == "__main__":
    init_brain()
    print(get_cross_bot_context())
