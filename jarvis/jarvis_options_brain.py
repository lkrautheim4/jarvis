#!/usr/bin/env python3
"""
JARVIS OPTIONS BRAIN
Tracks every options trade with full context.
Learns what works. Gets smarter every trade.
Sends plain English signals to Telegram.
"""
import requests, json, time, logging, math
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import jarvis_brain as _jb_hb


def _valid_expiry(ticker, preferred, dte=14):
    """Return a real yfinance expiry for `ticker`.

    Uses `preferred` (the actual contract expiry) when it is a valid listing.
    Otherwise snaps to the nearest valid expiry at/after today+dte — it never
    fabricates a date by raw arithmetic, which is what produced phantom
    expiries like AMD 2026-06-10 (a Wed with no AMD weekly) that the grader
    could never price.
    """
    try:
        import yfinance as yf
        exps = list(yf.Ticker(ticker).options)
    except Exception:
        exps = []
    if preferred and preferred in exps:
        return preferred
    target = (datetime.now() + timedelta(days=dte)).date()
    if exps:
        future = [e for e in exps if datetime.strptime(e, "%Y-%m-%d").date() >= target]
        pool = future or exps
        return min(pool, key=lambda e: abs((datetime.strptime(e, "%Y-%m-%d").date() - target).days))
    # No chain available — last-resort arithmetic so we still log something
    return preferred or target.strftime("%Y-%m-%d")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("OPTIONS_BRAIN")

ALPACA_KEY    = "PKTHANGUNVFDSLLR3VXPETXRQF"
ALPACA_SECRET = "GRTDDfkCGWbZMoNSWms6uJSGvw72rHaAk1N1fvLi8EAP"
ALPACA_BASE   = "https://paper-api.alpaca.markets"
ALPACA_DATA   = "https://data.alpaca.markets"
TG_TOKEN      = __import__("jarvis_secrets").TG_TOKEN_TRADER
CHAT_ID       = "7534553840"
BRAIN_FILE    = "/root/jarvis/jarvis_options_brain.json"
INTERVAL      = 3600  # scan every hour during market

# ─── VIX THRESHOLDS — single source of truth used by scoring and brief ───────
VIX_LOW      = 15   # below = cheap to buy options
VIX_ELEVATED = 18   # above = elevated premium, decent to sell
VIX_HIGH     = 25   # above = expensive options, ideal to sell premium
# ─────────────────────────────────────────────────────────────────────────────

# The full universe with characteristics
UNIVERSE = {
    # WHEEL candidates — sell puts, want to own
    "SOFI": {"type":"wheel","sector":"fintech","iv_rank":"high","budget":1800,"reason":"Fintech growth, high IV"},
    "F":    {"type":"wheel","sector":"auto","iv_rank":"med","budget":1800,"reason":"Ford, stable, dividend"},
    "RIVN": {"type":"wheel","sector":"ev","iv_rank":"high","budget":800,"reason":"EV, massive premium"},
    "AAL":  {"type":"wheel","sector":"airlines","iv_rank":"high","budget":1500,"reason":"Airlines, always high IV"},
    "MSTR": {"type":"wheel","sector":"crypto","iv_rank":"extreme","budget":7900,"reason":"BTC proxy, wild premium"},
    "BAC":  {"type":"wheel","sector":"banking","iv_rank":"med","budget":5100,"reason":"Buffett stock, stable"},
    # MOMENTUM candidates — buy calls on breakouts
    "SPY":  {"type":"momentum","sector":"etf","iv_rank":"low","budget":37800,"reason":"Market ETF, most liquid"},
    "QQQ":  {"type":"momentum","sector":"etf","iv_rank":"low","budget":73800,"reason":"Tech ETF, liquid"},
    "NVDA": {"type":"momentum","sector":"tech","iv_rank":"med","budget":22100,"reason":"AI leader"},
    "TSLA": {"type":"momentum","sector":"ev","iv_rank":"high","budget":44000,"reason":"Volatile, big moves"},
    "AAPL": {"type":"momentum","sector":"tech","iv_rank":"low","budget":14800,"reason":"Stable, liquid"},
    # BOTH strategies
    "PLTR": {"type":"both","sector":"tech","iv_rank":"high","budget":15700,"reason":"AI/defense"},
    "AMD":  {"type":"both","sector":"tech","iv_rank":"med","budget":51100,"reason":"Semiconductor"},
    "COIN": {"type":"both","sector":"crypto","iv_rank":"extreme","budget":8900,"reason":"Crypto proxy"},
    "META": {"type":"both","sector":"tech","iv_rank":"med","budget":63400,"reason":"Social, momentum"},
    "MSFT": {"type":"momentum","sector":"tech","iv_rank":"low","budget":43000,"reason":"$450 key level, institutional, AI cloud"},
}

hdrs = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

try:
    import jarvis_memory_db as memdb
    memdb.init_db()
    DB_ENABLED = True
except Exception as _e:
    DB_ENABLED = False

try:
    from jarvis_options_brain_upgrade import (
        get_catalyst_tag, compute_theta_per_day,
        find_best_contract_v2, log_trade_v2,
        check_theta_warnings, build_trade_alert_v2
    )
    UPGRADE_ENABLED = True
except Exception as _e:
    UPGRADE_ENABLED = False

def tg(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg[:4000]}, timeout=10)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def get_yf_contracts(ticker, option_type="put", min_dte=30, target_dte=35, max_dte=45):
    """Get real options chain from yfinance, restricted to the [min_dte, max_dte]
    window (issue #1 — never trade short-dated contracts).

    We iterate ALL listed expiries and keep only those at/after min_dte, picking
    the ones closest to target_dte. The old code scanned t.options[:4], i.e. the
    first four expiries — which on weekly-heavy names are all short-dated and
    would be rejected by a 30-day floor, leaving nothing to trade."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        price = t.info.get("regularMarketPrice", 0)
        if not price: return [], None
        import time as _time
        _quote_ts = _time.time()
        try:
            _hist = t.history(period="5d")
            _week_chg = round((_hist["Close"].iloc[-1] / _hist["Close"].iloc[0] - 1) * 100, 2) if len(_hist) >= 2 else 0.0
        except Exception:
            _week_chg = 0.0
        # Rank every listed expiry by how close it is to target_dte, keeping only
        # those inside the [min_dte, max_dte] window.
        qualifying = []
        for exp in t.options:
            try:
                dte = (datetime.strptime(exp, "%Y-%m-%d") - datetime.now()).days
            except Exception:
                continue
            if min_dte <= dte <= max_dte:
                qualifying.append((abs(dte - target_dte), exp))
        qualifying.sort()
        chosen = [e for _, e in qualifying[:4]]
        contracts = []
        last_opts = None
        for exp in chosen:
            chain = t.option_chain(exp)
            opts = chain.puts if option_type == "put" else chain.calls
            last_opts = opts
            for _, row in opts.iterrows():
                contracts.append({
                    "strike_price": str(row["strike"]),
                    "expiration_date": exp,
                    "bid": row.get("bid", 0),
                    "ask": row.get("ask", 0),
                    "volume": row.get("volume", 0),
                    "impliedVolatility": row.get("impliedVolatility", 0),
                    "symbol": row.get("contractSymbol", ""),
                    "quote_ts": _quote_ts,
                    "week_change_pct": _week_chg,
                })
        iv = last_opts["impliedVolatility"].mean() * 100 if last_opts is not None and len(last_opts) > 0 else None
        return contracts, round(iv, 1) if iv else None
    except Exception as e:
        log.error(f"yfinance options error {ticker}: {e}")
        return [], None

def load_brain():
    try: return json.load(open(BRAIN_FILE))
    except: return {
        "trades": [],
        "stats": {
            "total": 0, "wins": 0, "losses": 0, "open": 0,
            "total_pnl": 0.0, "total_premium": 0.0,
            "by_ticker": {}, "by_strategy": {}, "by_signal": {},
            "by_regime": {}, "by_fg_range": {}, "by_iv_level": {}
        },
        "patterns": [],
        "daily_brief_sent": ""
    }

def save_brain(brain):
    with open(BRAIN_FILE, 'w') as f: json.dump(brain, f, indent=2)

# ─────────────────────────────────────────
# SIGNAL LEARNING — closed-trade results feed back into scoring
# ─────────────────────────────────────────
# When a paper trade closes, every signal that triggered it gets +1 weight on a
# WIN and -1 on a LOSS (credit_signal_weights, called by options_grader at
# close). score_setup() reads these net weights back in, so signals that have
# historically led to winners boost a setup's score and losers drag it down.
import re as _re
SIGNAL_WEIGHTS_FILE = "/root/jarvis/jarvis_signal_weights.json"

def norm_signal(sig):
    """Stable learning key: strip magnitudes/%/sign.
    'IV:97%EXTREME+20' -> 'IV:%EXTREME'; 'REGIME:RISK_OFF+25' -> 'REGIME:RISK_OFF'."""
    return _re.sub(r"[0-9.]+", "", str(sig)).replace("+", "").replace("-", "").strip()

def load_signal_weights():
    try:
        return json.load(open(SIGNAL_WEIGHTS_FILE))
    except Exception:
        return {}

def credit_signal_weights(signals, result):
    """WIN -> +1 per signal, LOSS -> -1. Persisted; read back by score_setup."""
    if not signals:
        return
    w = load_signal_weights()
    by = w.setdefault("by_signal", {})
    for s in signals:
        k = norm_signal(s)
        rec = by.setdefault(k, {"wins": 0, "losses": 0, "weight": 0})
        if result == "WIN":
            rec["wins"] += 1
        else:
            rec["losses"] += 1
        rec["weight"] = rec["wins"] - rec["losses"]
    try:
        with open(SIGNAL_WEIGHTS_FILE, "w") as f:
            json.dump(w, f, indent=2)
    except Exception as e:
        log.error(f"signal weights save: {e}")

def get_equity_fear_greed(ctx):
    """Read equity F&G from brain with 2-hour staleness guard.
    Returns (value, warning_msg). Falls back to neutral 50 + warning if stale/missing."""
    brain = ctx.get("brain", {})
    eq_fg = brain.get("equity_fear_greed")

    if not eq_fg or not isinstance(eq_fg, dict):
        return 50, "⚠️ Equity F&G missing from brain"

    try:
        ts = datetime.fromisoformat(eq_fg["ts"])
        age_hours = (datetime.now() - ts).total_seconds() / 3600

        if age_hours > 2:
            return 50, f"⚠️ Equity F&G stale ({age_hours:.1f}h old)"

        return eq_fg.get("value", 50), None
    except Exception as e:
        log.warning(f"Equity F&G parse error: {e}")
        return 50, "⚠️ Equity F&G parse failed"

def get_price(ticker):
    try:
        r = requests.get(f"{ALPACA_DATA}/v2/stocks/{ticker}/quotes/latest",
            headers=hdrs, timeout=8)
        if r.status_code == 200:
            q = r.json().get("quote", {})
            ap = float(q.get("ap", 0) or 0)
            bp = float(q.get("bp", 0) or 0)
            # Only average the two sides when BOTH are present. A one-sided
            # quote (common pre/post-market) used to fall through (ap+bp)/2 and
            # come back HALVED — e.g. AMD ask $517 -> $258.70. That bogus spot
            # then made find_best_contract pick an absurd strike (a $250 AMD
            # put) and defeated the moneyness/junk gates. Use whichever side we
            # actually have instead of silently halving.
            if ap > 0 and bp > 0:
                mid = (ap + bp) / 2
            else:
                mid = ap or bp
            return round(mid, 2) if mid > 0 else None
    except: pass
    return None

def get_day_change_pct(ticker):
    """Intraday % change vs previous close (None if unavailable).

    Used to block bearish put-buys on a stock that is rallying — see the
    momentum guard in scan_and_alert (don't recommend a put when up >1%)."""
    try:
        r = requests.get(f"{ALPACA_DATA}/v2/stocks/{ticker}/snapshot",
            headers=hdrs, timeout=8)
        if r.status_code == 200:
            snap = r.json()
            last = (snap.get("latestTrade", {}) or {}).get("p", 0) \
                or (snap.get("latestQuote", {}) or {}).get("ap", 0)
            prev_close = (snap.get("prevDailyBar", {}) or {}).get("c", 0)
            if last and prev_close:
                return (last - prev_close) / prev_close * 100
    except Exception as e:
        log.warning(f"day change {ticker}: {e}")
    return None

def get_all_context():
    """Load everything JARVIS knows"""
    ctx = {}
    for name, path in [
        ("macro", "/root/jarvis/jarvis_macro.json"),
        ("beast", "/root/jarvis/jarvis_beast_brain.json"),
        ("congress", "/root/jarvis/jarvis_congress.json"),
        ("earnings", "/root/jarvis/jarvis_earnings.json"),
        ("brain", "/root/jarvis/jarvis_central_brain.json"),
        ("intel", "/root/jarvis/jarvis_intel.json"),
    ]:
        try: ctx[name] = json.load(open(path))
        except: ctx[name] = {}
    # Regime comes from the canonical jarvis_memory.db brain (fresh intraday), not
    # the cached macro.json value — every downstream ctx.macro.regime read uses it.
    if DB_ENABLED:
        try:
            if not isinstance(ctx.get("macro"), dict):
                ctx["macro"] = {}
            ctx["macro"]["regime"] = memdb.get_regime(ctx["macro"].get("regime", "UNKNOWN"))
        except Exception:
            pass
    return ctx

def score_setup(ticker, price, iv, option_type, ctx, config):
    """Score a setup 0-100 based on all signals"""
    score = 0
    signals = []
    reasons = []

    macro = ctx.get("macro", {})
    brain = ctx.get("brain", {})
    congress = ctx.get("congress", {})
    earnings = ctx.get("earnings", {})
    beast_brain = ctx.get("beast", {})

    regime = macro.get("regime", "UNKNOWN")
    fg, _ = get_equity_fear_greed(ctx)
    vix = macro.get("vix", {}).get("value", 15)
    btc_signal = brain.get("btc_signal", "neutral")

    # 1. MACRO REGIME (0-20 points)
    if option_type == "put_sell":  # selling puts = bullish
        if regime == "RISK_ON": score += 20; signals.append("REGIME:RISK_ON+20")
        elif regime == "RECOVERY": score += 15; signals.append("REGIME:RECOVERY+15")
        elif regime == "STAGFLATION": score += 5; signals.append("REGIME:STAGFLATION+5")
        elif regime == "RISK_OFF": score -= 10; signals.append("REGIME:RISK_OFF-10")
    elif option_type == "call_buy":  # buying calls = bullish
        if regime == "RISK_ON": score += 20; signals.append("REGIME:RISK_ON+20")
        elif regime == "RISK_OFF": score -= 20; signals.append("REGIME:RISK_OFF-20")
    elif option_type == "put_buy":  # buying puts = bearish
        if regime == "RISK_OFF": score += 25; signals.append("REGIME:RISK_OFF+25")
        elif regime == "RISK_ON": score -= 20; signals.append("REGIME:RISK_ON-20")
    # 2. FEAR & GREED
    if option_type == "put_sell":
        if fg < 25: score += 20; signals.append(f"FG:{fg}EXTREME_FEAR+20")
        elif fg < 40: score += 15; signals.append(f"FG:{fg}FEAR+15")
        elif fg < 55: score += 10; signals.append(f"FG:{fg}NEUTRAL+10")
        elif fg > 70: score += 5; signals.append(f"FG:{fg}GREED+5")
    elif option_type == "call_buy":
        if fg < 25: score += 15; signals.append(f"FG:{fg}FEAR_BOUNCE+15")
        elif fg > 70: score -= 10; signals.append(f"FG:{fg}GREED-10")
        else: score += 10; signals.append(f"FG:{fg}NORMAL+10")
    elif option_type == "put_buy":
        if fg < 25: score += 20; signals.append(f"FG:{fg}EXTREME_FEAR_PUT+20")
        elif fg < 40: score += 10; signals.append(f"FG:{fg}FEAR_PUT+10")
        elif fg > 60: score -= 15; signals.append(f"FG:{fg}GREED-15")
    # 3. IV LEVEL
    if iv:
        if option_type == "put_sell":
            if iv > 60: score += 20; signals.append(f"IV:{iv}%EXTREME+20")
            elif iv > 40: score += 15; signals.append(f"IV:{iv}%HIGH+15")
            elif iv > 25: score += 10; signals.append(f"IV:{iv}%MED+10")
            else: score += 3; signals.append(f"IV:{iv}%LOW+3")
        elif option_type == "call_buy":
            if iv < 25: score += 20; signals.append(f"IV:{iv}%LOW_BUY+20")
            elif iv < 40: score += 10; signals.append(f"IV:{iv}%MED+10")
            else: score -= 10; signals.append(f"IV:{iv}%HIGH-10")
        elif option_type == "put_buy":
            if iv > 40: score += 15; signals.append(f"IV:{iv}%HIGH_PUT+15")
            elif iv > 25: score += 8; signals.append(f"IV:{iv}%MED_PUT+8")
            else: score -= 5; signals.append(f"IV:{iv}%LOW-5")
    # 4. CONGRESS SIGNAL
    hot = congress.get("hot_tickers", {})
    if ticker in hot:
        count = hot[ticker].get("count", 0)
        pols = hot[ticker].get("politicians", [])
        score += min(15, count * 5)
        signals.append(f"CONGRESS:{count}BUYERS+{min(15,count*5)}")
    # 5. EARNINGS PROTECTION
    risk_map = earnings.get("risk_map", {})
    if ticker in risk_map:
        risk = risk_map[ticker].get("risk", "LOW")
        days = risk_map[ticker].get("days_away", 99)
        if risk == "CRITICAL": score -= 30; signals.append("EARNINGS:CRITICAL-30")
        elif risk == "HIGH": score -= 20; signals.append("EARNINGS:HIGH-20")
        elif risk == "MEDIUM": score -= 5; signals.append("EARNINGS:MEDIUM-5")
    # 6. BTC/CRYPTO SIGNAL
    if config.get("sector") in ["crypto", "fintech"]:
        if btc_signal == "bullish": score += 10; signals.append("BTC:BULLISH+10")
        elif btc_signal == "bearish": score -= 10; signals.append("BTC:BEARISH-10")
    # 7. VIX (thresholds from module-level VIX_LOW / VIX_ELEVATED / VIX_HIGH)
    if option_type == "put_sell" and vix > VIX_ELEVATED:
        score += 10; signals.append(f"VIX:{vix:.1f}ELEVATED+10")
    elif option_type == "call_buy" and vix < VIX_LOW:
        score += 5; signals.append(f"VIX:{vix:.1f}LOW+5")
    elif option_type == "put_buy" and vix > VIX_ELEVATED:
        score += 10; signals.append(f"VIX:{vix:.1f}ELEVATED_PUT+10")
    # 8. LEARNED FEEDBACK — net weights from closed-trade outcomes
    try:
        _w = load_signal_weights().get("by_signal", {})
        adj = sum(_w.get(norm_signal(s), {}).get("weight", 0) for s in list(signals))
        adj = max(-20, min(20, adj))
        if adj:
            score += adj
            signals.append(f"LEARN:{adj:+d}")
    except Exception:
        pass
    return max(0, min(100, score)), signals

def find_best_contract(contracts, stock_price, option_type, target_dte=35, min_dte=30):
    """Find the best contract to trade.

    DTE floor is 30 (issue #1) and buy-side strikes are capped at 10% OTM
    (issue #3) so a deep-OTM strike can never be selected here."""
    if not contracts: return None
    best = None; best_score = 0
    for c in contracts:
        try:
            strike = float(c.get("strike_price", 0))
            exp = datetime.strptime(c.get("expiration_date",""), "%Y-%m-%d")
            dte = (exp - datetime.now()).days
            if dte < min_dte or dte > 45: continue
            dte_score = 1 - abs(dte - target_dte) / target_dte
            if option_type == "put_sell":
                pct_otm = (stock_price - strike) / stock_price
                if 0.05 <= pct_otm <= 0.15:  # 5-15% OTM sweet spot
                    strike_score = 1 - abs(pct_otm - 0.08) / 0.08
                    score = dte_score * 0.5 + strike_score * 0.5
                    if score > best_score: best_score = score; best = c
            elif option_type == "call_buy":
                pct_otm = (strike - stock_price) / stock_price
                if 0.02 <= pct_otm <= 0.10:  # 2-10% OTM for calls
                    strike_score = 1 - abs(pct_otm - 0.05) / 0.05
                    score = dte_score * 0.5 + strike_score * 0.5
                    if score > best_score: best_score = score; best = c
            elif option_type == "put_buy":
                pct_otm = (stock_price - strike) / stock_price
                if -0.02 <= pct_otm <= 0.10:  # ATM to 10% OTM puts
                    strike_score = 1 - abs(pct_otm - 0.03) / 0.05
                    score = dte_score * 0.5 + strike_score * 0.5
                    if score > best_score: best_score = score; best = c
        except: continue
    return best

# ─────────────────────────────────────────
# BUY-SIDE SANITY FILTERS (issues #2 and #3)
# ─────────────────────────────────────────
MAX_MONEYNESS_PCT = 0.10   # buy strike must be within 10% of spot
MAX_IV_RANK       = 150.0   # IV/realized ratio gate: skip if IV > 1.5x realized vol
SELL_PREMIUM_IV_RANK = 130.0  # PROTECTION: IV/realized ratio >=1.3x -> sell-premium candidate (not a buy)

def strike_too_far_otm(price, strike, max_pct=MAX_MONEYNESS_PCT):
    """True if `strike` is more than max_pct away from spot (issue #3).

    e.g. an AMD $250 put with AMD at $530 is 53% away -> rejected. Returns
    False (don't reject) only when we genuinely can't evaluate."""
    try:
        price = float(price or 0)
        if price <= 0:
            return False
        return abs(float(strike) - price) / price > max_pct
    except Exception:
        return False

_IV_RANK_CACHE = {}

def get_iv_rank(ticker, current_iv):
    """IV/Realized ratio gate (Option 2 fix).
    Replaces broken rank that compared implied vol against realized range.
    Returns ratio * 100 so existing MAX_IV_RANK threshold still works:
      ratio=1.5 → returns 150 → exceeds MAX_IV_RANK=50 → SKIP (IV too rich)
      ratio=0.9 → returns 90  → under MAX_IV_RANK=50? No — adjust threshold.
    current_iv expected in percent (e.g. 45.0 == 45%)."""
    if current_iv is None:
        return None
    try:
        import math as _math
        now = datetime.now(timezone.utc)
        cached = _IV_RANK_CACHE.get(ticker)
        if cached is not None and (now - cached[3]) < timedelta(hours=24):
            realized = cached[2]
        else:
            import yfinance as yf
            hist = yf.Ticker(ticker).history(period="3mo")
            closes = list(hist["Close"].dropna())
            if len(closes) < 30:
                return None
            rets = [_math.log(closes[i] / closes[i-1])
                    for i in range(1, len(closes)) if closes[i-1] > 0]
            window = 21
            vols = []
            for i in range(window, len(rets) + 1):
                w = rets[i-window:i]
                m = sum(w) / len(w)
                var = sum((x - m) ** 2 for x in w) / (len(w) - 1)
                vols.append(_math.sqrt(var) * _math.sqrt(252) * 100)
            if not vols:
                return None
            realized = sum(vols) / len(vols)
            lo, hi = min(vols), max(vols)
            _IV_RANK_CACHE[ticker] = (lo, hi, realized, now)
        if realized <= 0:
            return None
        ratio = (float(current_iv) / realized) * 100
        return round(ratio, 1)
    except Exception as e:
        log.warning(f"IV ratio {ticker}: {e}")
        return None

def get_option_mid_price(symbol):
    """Get mid price of an option"""
    try:
        r = requests.get(f"{ALPACA_DATA}/v2/options/quotes/latest",
            headers=hdrs, params={"symbols": symbol}, timeout=8)
        if r.status_code == 200:
            q = r.json().get("quotes", {}).get(symbol, {})
            ap = float(q.get("ap", 0)); bp = float(q.get("bp", 0))
            if ap > 0 and bp > 0: return round((ap+bp)/2, 2)
    except: pass
    return None

def log_trade(brain, trade_data):
    """Log a trade with full context"""
    brain["trades"].append(trade_data)
    # Mirror into shared SQLite so the morning brief (get_options_stats) sees it.
    # Skip if the caller already recorded the signal row (db_id set) — avoids dups.
    # BUG FIX: This block now skipped when _db_id is passed from scan_and_alert
    # (line 989-1001), preventing duplicate rows in options_trades table.
    if DB_ENABLED and not trade_data.get("db_id"):
        try:
            trade_data["db_id"] = memdb.log_options_trade(
                ticker=trade_data["ticker"],
                strategy=trade_data["strategy"],
                strike=trade_data["strike"],
                premium=trade_data["premium"],
                dte=trade_data["dte"],
                iv=trade_data.get("iv") or 0,
                score=trade_data.get("score") or 0,
                contract_symbol=trade_data.get("contract_symbol"),
                stock_price=trade_data.get("stock_price"),
                regime=trade_data.get("regime", "?"),
                fear_greed=trade_data.get("fg_at_entry", 50),
                vix=trade_data.get("vix_at_entry"),
                btc_signal=trade_data.get("btc_signal", "neutral"),
            )
        except Exception as _dbe:
            log.error(f"DB log_options_trade failed: {_dbe}")
    s = brain["stats"]
    s["total"] += 1
    s["open"] += 1
    # By ticker
    tk = trade_data["ticker"]
    if tk not in s["by_ticker"]: s["by_ticker"][tk] = {"total":0,"wins":0,"pnl":0}
    s["by_ticker"][tk]["total"] += 1
    # By strategy
    st = trade_data["strategy"]
    if st not in s["by_strategy"]: s["by_strategy"][st] = {"total":0,"wins":0,"pnl":0}
    s["by_strategy"][st]["total"] += 1
    # By regime
    reg = trade_data.get("regime","?")
    if reg not in s["by_regime"]: s["by_regime"][reg] = {"total":0,"wins":0,"pnl":0}
    s["by_regime"][reg]["total"] += 1
    # By F&G range
    fg = trade_data.get("fg_at_entry", 50)
    fg_bucket = "fear" if fg < 40 else "greed" if fg > 60 else "neutral"
    if fg_bucket not in s["by_fg_range"]: s["by_fg_range"][fg_bucket] = {"total":0,"wins":0,"pnl":0}
    s["by_fg_range"][fg_bucket]["total"] += 1
    return brain

def grade_closed_trades(brain):
    """Sync brain JSON trade results from paper_trades_store (not Alpaca).

    For each brain trade still marked open/paper, look for a matching
    paper_closed entry in paper_trades_store. If found, record the real
    result/pnl and close the SQLite row. Never touches Alpaca positions.
    """
    open_trades = [t for t in brain["trades"] if t.get("status") in ("open", "paper")]
    if not open_trades:
        return brain

    try:
        import paper_trades_store as _store
        pt_data = _store.read()
    except Exception as _e:
        log.error(f"grade_closed_trades: cannot read paper_trades_store: {_e}")
        return brain

    # Index paper_closed entries by (ticker, strategy, strike, entry_date)
    closed_index = {}
    for pt in pt_data.get("trades", []):
        if pt.get("status") != "paper_closed" or not pt.get("result"):
            continue
        _key = (
            pt.get("ticker"),
            pt.get("strategy"),
            round(float(pt.get("strike") or 0), 2),
            pt.get("entry_date", ""),
        )
        # Keep first match (earliest close) to avoid overwriting with a later dup
        if _key not in closed_index:
            closed_index[_key] = pt

    graded_count = 0
    for trade in open_trades:
        ts = trade.get("ts", "")
        entry_date = ts[:10] if ts else ""
        _key = (
            trade.get("ticker"),
            trade.get("strategy"),
            round(float(trade.get("strike") or 0), 2),
            entry_date,
        )
        pt = closed_index.get(_key)
        if not pt:
            continue

        pnl = float(pt.get("pnl") or 0)
        result = pt.get("result")  # "WIN" or "LOSS"
        won = (result == "WIN")

        trade["status"] = "closed"
        trade["closed_ts"] = pt.get("exit_date") or datetime.now().strftime("%Y-%m-%d")
        trade["result"] = result
        trade["pnl"] = pnl

        # Sync SQLite row
        if DB_ENABLED and trade.get("db_id"):
            try:
                memdb.close_options_trade(trade["db_id"], result, pnl)
            except Exception as _dbe:
                log.error(f"DB close_options_trade failed: {_dbe}")

        # Update in-memory stats
        s = brain["stats"]
        s["open"] = max(0, s["open"] - 1)
        if won:
            s["wins"] += 1
        else:
            s["losses"] += 1
        s["total_pnl"] += pnl
        for _k, _bucket in [
            ("by_ticker",   trade.get("ticker")),
            ("by_strategy", trade.get("strategy")),
            ("by_regime",   trade.get("regime", "?")),
        ]:
            if _bucket and _bucket in s.get(_k, {}):
                if won:
                    s[_k][_bucket]["wins"] += 1
                s[_k][_bucket]["pnl"] += pnl

        # Feed result back into signal-weight learning
        try:
            credit_signal_weights(trade.get("signals", []), result)
        except Exception:
            pass

        graded_count += 1
        log.info(
            f"GRADED {trade.get('ticker')} {trade.get('strategy')} "
            f"${trade.get('strike')} → {result} ${pnl:+.0f} "
            f"(exit: {pt.get('exit_reason', '?')})"
        )

    if graded_count:
        log.info(f"grade_closed_trades: {graded_count} trade(s) graded this cycle")

    return brain

def build_pattern_insight(brain):
    """Find what's actually working — only counts CLOSED/graded trades from DB."""
    if not DB_ENABLED:
        return None

    try:
        import sqlite3
        conn = sqlite3.connect(memdb.DB_PATH)
        c = conn.cursor()

        # Strategy breakdown
        c.execute(
            "SELECT strategy, COUNT(*), SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END), SUM(COALESCE(pnl,0))"
            " FROM options_trades WHERE status='closed' AND result IS NOT NULL"
            " GROUP BY strategy"
        )
        rows = c.fetchall()
        conn.close()
    except Exception:
        return None

    if not rows:
        return None

    insights = []
    ticker_stats = {}

    for strat, total, wins, pnl in rows:
        wins = wins or 0
        pnl = pnl or 0.0
        if total >= 3:
            wr = round(wins / total * 100)
            avg_pnl = round(pnl / total, 2)
            insights.append(f"{strat}: {wr}% WR avg ${avg_pnl:+.0f}")

    # Best ticker — only if >=5 graded trades on that ticker
    try:
        import sqlite3
        conn2 = sqlite3.connect(memdb.DB_PATH)
        c2 = conn2.cursor()
        c2.execute(
            "SELECT ticker, COUNT(*), SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END)"
            " FROM options_trades WHERE status='closed' AND result IS NOT NULL"
            " GROUP BY ticker HAVING COUNT(*) >= 5"
        )
        ticker_rows = c2.fetchall()
        conn2.close()
        if ticker_rows:
            best = max(ticker_rows, key=lambda r: (r[2] or 0) / r[1])
            wr = round((best[2] or 0) / best[1] * 100)
            insights.append(f"Best ticker: {best[0]} {wr}% WR ({best[1]} trades)")
    except Exception:
        pass

    return "\n".join(insights) if insights else None

def resolve_action(fg, regime, market_mode):
    """Single decision resolver — returns (final_action, sentiment_label, conflict_note).

    Priority: market_mode (PROTECTION) > macro regime > fear/greed sentiment.
    sentiment_label is a context-only descriptor with no verb/trade direction.
    final_action is the ONE authoritative instruction printed in the brief.
    """
    # TIER 3 (lowest): sentiment context label only — no "sell puts" / "buy puts"
    if fg <= 25:
        sentiment_label = "😱 Extreme Fear"
        sentiment_action = "SELL PUTS"
    elif fg <= 40:
        sentiment_label = "😰 Fear"
        sentiment_action = "SELL PUTS"
    elif fg >= 70:
        sentiment_label = "🤑 Greed"
        sentiment_action = "BUY CALLS"
    else:
        sentiment_label = "😐 Neutral"
        sentiment_action = "WAIT"

    regime_is_bearish = regime in ["RISK_OFF"]
    protection = (market_mode == "PROTECTION")

    # TIER 1: PROTECTION mode overrides everything — only puts or wait
    if protection:
        return "BUY PUTS", sentiment_label, f"⚠️ PROTECTION MODE active"

    # TIER 2: regime override
    if regime_is_bearish and sentiment_action == "SELL PUTS":
        return "WAIT", sentiment_label, f"⚠️ Regime={regime} conflicts with sentiment — no edge"
    if regime_is_bearish:
        return "BUY PUTS", sentiment_label, f"Regime={regime} confirmed bearish"

    # TIER 3: sentiment drives action
    return sentiment_action, sentiment_label, None


def morning_brief(brain, ctx):
    """Send plain English options brief every morning"""
    macro = ctx.get("macro", {})
    brain_data = ctx.get("brain", {})

    regime = macro.get("regime", "UNKNOWN")
    fg, fg_warning = get_equity_fear_greed(ctx)
    vix = macro.get("vix", {}).get("value", 15)
    yield_val = macro.get("yield_10yr", {}).get("value", 4.3)
    btc = brain_data.get("btc_price", 0)

    market_mode = "PROFIT"
    if DB_ENABLED:
        try:
            market_mode = memdb.brain_get("market_mode") or "PROFIT"
        except Exception:
            market_mode = "PROFIT"
    protection = (market_mode == "PROTECTION")

    final_action, sentiment_label, conflict_note = resolve_action(fg, regime, market_mode)

    # Regime plain English — context label only, no action verb
    regime_label = {
        "RISK_ON":      "Risk-On",
        "RISK_OFF":     "Risk-Off",
        "STAGFLATION":  "Stagflation",
        "RECOVERY":     "Recovery",
    }.get(regime, regime)
    regime_plain = {
        "RISK_ON":      "Market tilting up",
        "RISK_OFF":     "Market tilting down",
        "STAGFLATION":  "Choppy / no direction",
        "RECOVERY":     "Recovering from dip",
    }.get(regime, "Uncertain")

    # VIX plain English (uses VIX_LOW / VIX_ELEVATED / VIX_HIGH constants)
    if vix > VIX_HIGH:       vix_note = f"VIX {vix:.1f} HIGH — options expensive"
    elif vix > VIX_ELEVATED: vix_note = f"VIX {vix:.1f} ELEVATED — decent premium"
    else:                    vix_note = f"VIX {vix:.1f} LOW — options cheap"

    from options_conflict_detector import MarketInputs, Stance, get_unified_stance
    raw = Stance.SELL_PUTS if final_action == "SELL PUTS" else Stance.BUY_DEBIT_SPREAD if final_action == "BUY PUTS" else Stance.BUY_CALLS if final_action == "BUY CALLS" else Stance.WAIT
    ci = MarketInputs(vix=vix, fear_greed=fg, regime=regime, raw_signal=raw)
    cr = get_unified_stance(ci)
    detector_conflict = f"⚡ CONFLICTS: {chr(44).join(c.value for c in cr.conflicts)}" if cr.conflicts else "✅ No conflicts"
    unified_line = cr.brief_line

    # Find best setup right now
    best_play = find_todays_best_play(ctx, fg, regime, vix, protection)

    # Stats from DB — only count CLOSED/graded trades
    open_count = graded_total = graded_wins = 0
    graded_pnl = 0.0
    if DB_ENABLED:
        try:
            import sqlite3
            _conn = sqlite3.connect(memdb.DB_PATH)
            _c = _conn.cursor()
            _c.execute("SELECT COUNT(*) FROM options_trades WHERE status='paper' AND result IS NULL")
            open_count = _c.fetchone()[0]
            _c.execute(
                "SELECT COUNT(*), SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END), SUM(COALESCE(pnl,0))"
                " FROM options_trades WHERE status='closed' AND result IS NOT NULL"
            )
            row = _c.fetchone()
            graded_total = row[0] or 0
            graded_wins  = int(row[1] or 0)
            graded_pnl   = float(row[2] or 0.0)
            _conn.close()
        except Exception as _se:
            log.error(f"Stats DB query failed: {_se}")

    if graded_total > 0:
        wr_line = f"{graded_wins}/{graded_total} graded ({round(graded_wins/graded_total*100)}% WR) | ${graded_pnl:+.0f}"
    else:
        wr_line = "no graded trades yet"
    stats_line = f"PORTFOLIO: {open_count} open | {wr_line}"

    # Learning insights (based on graded trades only)
    insights = build_pattern_insight(brain)

    lines = [
        f"📊 JARVIS OPTIONS BRIEF",
        f"{'='*26}",
        f"Sentiment: {sentiment_label} (F&G {fg})",
        f"Regime: {regime_label} — {regime_plain}",
        f"{vix_note}",
        f"{'='*26}",
        f"ACTION: {final_action}",
    ]
    if conflict_note:
        lines.append(conflict_note)
    lines += [
        f"{'='*26}",
        f"{detector_conflict}",
        f"{unified_line}",
        f"BTC: ${btc:,.0f} | Yield: {yield_val:.2f}%",
    ]
    if fg_warning:
        lines.append(fg_warning)
    lines.append(f"{'='*26}")

    if best_play:
        lines.append("TODAY'S PLAY:")
        lines.extend(best_play)
    else:
        lines.append("No high-confidence setups today")
        lines.append("Wait for better conditions")

    lines.extend([
        f"{'='*26}",
        stats_line,
    ])

    if insights:
        lines.append(f"LEARNING:")
        lines.append(insights)

    lines.extend([
        f"{'='*26}",
        f"Text LEARN <ticker> for deep analysis",
        f"Text OPTIONS for open positions"
    ])

    tg("\n".join(lines))
    brain["daily_brief_sent"] = datetime.now().strftime("%Y-%m-%d")
    return brain

def find_todays_best_play(ctx, fg, regime, vix, protection=False):
    """Find the single best options play today in plain English"""
    # PROTECTION mode buys puts — never surface a contradictory sell-put wheel play
    if protection:
        return None
    macro = ctx.get("macro", {})
    earnings = ctx.get("earnings", {})
    congress = ctx.get("congress", {})
    blacklist = earnings.get("critical",[]) + earnings.get("high_risk",[])
    hot = congress.get("hot_tickers", {})

    plays = []

    # Scan for wheel candidates
    if fg < 50 and regime in ["RISK_ON","RECOVERY","STAGFLATION"]:
        for ticker, config in UNIVERSE.items():
            if config["type"] not in ["wheel","both"]: continue
            if ticker in blacklist: continue
            price = get_price(ticker)
            if not price: continue
            contracts, iv = get_yf_contracts(ticker, "put")
            if not contracts or not iv: continue
            contract = find_best_contract(contracts, price, "put_sell")
            if not contract: continue
            strike = float(contract.get("strike_price",0))
            dte = (datetime.strptime(contract.get("expiration_date",""), "%Y-%m-%d") - datetime.now()).days
            # Use yfinance bid/ask directly, fallback to Alpaca
            bid = contract.get("bid", 0)
            ask = contract.get("ask", 0)
            premium = round((float(bid) + float(ask)) / 2, 2) if (bid or ask) else get_option_mid_price(contract.get("symbol",""))
            if not premium: continue
            premium_pct = premium/strike*100
            if premium_pct < 1.0: continue
            congress_bonus = "🏛 Congress buying!" if ticker in hot else ""
            score = premium_pct * (iv/30) * (1 if regime=="RISK_ON" else 0.8)
            plays.append((score, ticker, price, strike, premium, dte, iv, "SELL PUT", congress_bonus))

    if not plays: return None

    plays.sort(reverse=True)
    best = plays[0]
    score, ticker, price, strike, premium, dte, iv, strategy, congress_bonus = best

    cash_needed = strike * 100
    monthly_return = round(premium/strike*100 * (30/dte), 1)

    return [
        f"🎡 {strategy}: {ticker}",
        f"   Stock: ${price:.2f} | Strike: ${strike:.0f} | Exp: {dte}d",
        f"   Collect: ${premium:.2f}/share = ${premium*100:.0f} total",
        f"   Cash needed: ${cash_needed:.0f}",
        f"   Monthly return: {monthly_return}%",
        f"   IV: {iv}% | {congress_bonus}",
        f"   If assigned: own {ticker} at ${strike-premium:.2f} (below market)",
    ]

def scan_and_alert(brain, ctx):
    """Full scan — find setups, score them, alert if good"""
    blacklist = ctx.get("earnings",{}).get("critical",[]) + ctx.get("earnings",{}).get("high_risk",[])
    fg, _ = get_equity_fear_greed(ctx)
    regime = ctx.get("macro",{}).get("regime","UNKNOWN")
    vix = ctx.get("macro",{}).get("vix",{}).get("value",15)

    today = datetime.now().strftime("%Y-%m-%d")
    # Market mode (PROFIT / PROTECTION) from the shared brain — drives put-only
    # scanning, SPY/QQQ priority, and sell-premium flagging.
    # market_mode already resolved in brief — this gates scanner separately
    market_mode = "PROFIT"
    if DB_ENABLED:
        try: market_mode = memdb.brain_get("market_mode") or "PROFIT"
        except Exception: market_mode = "PROFIT"
    protection = (market_mode == "PROTECTION")

    log.info(f"Scanning {len(UNIVERSE)} tickers | F&G:{fg} Regime:{regime} Mode:{market_mode}")
    top_setups = []

    # PROTECTION: SPY/QQQ to the front of the queue + one scan-cycle log line.
    universe_items = list(UNIVERSE.items())
    if protection:
        universe_items.sort(key=lambda kv: 0 if kv[0] in ("SPY", "QQQ") else 1)
        log.info(f"PUT_SCAN: mode=PROTECTION, F&G={fg}, VIX={vix}, scanning {[t for t,_ in universe_items]}")

    for ticker, config in universe_items:
        if ticker in blacklist: continue
        price = get_price(ticker)
        if not price: continue

        # Determine what to scan. PROTECTION = puts only (no calls); PROFIT = normal.
        scan_types = []
        if protection:
            if config["type"] in ["wheel", "both"]:
                scan_types.append("put_sell")
            if config["type"] in ["momentum", "both"]:
                scan_types.append("put_buy")
        else:
            if config["type"] in ["wheel","both"] and fg < 55:
                scan_types.append("put_sell")
            if config["type"] in ["momentum","both"] and regime == "RISK_ON":
                scan_types.append("call_buy")
            if config["type"] in ["momentum","both"] and regime in ["RISK_OFF","STAGFLATION"] and fg < 30:
                scan_types.append("put_buy")
            if regime == "RISK_OFF" and fg < 35:
                scan_types.append("put_buy")

        for opt_type in scan_types:
            # Momentum guard: never recommend buying a put on a stock that is
            # rallying (up >1% on the day), regardless of macro regime.
            if opt_type == "put_buy":
                day_chg = get_day_change_pct(ticker)
                if day_chg is not None and day_chg > 1.0:
                    log.info(f"MOMENTUM: skip {ticker} put_buy — stock up {day_chg:+.1f}% on day")
                    continue
            contracts, iv = get_yf_contracts(
                ticker,
                "put" if "put" in opt_type else "call"
            )
            # PROTECTION: rich IV (ratio >= SELL_PREMIUM_IV_RANK) -> sell-premium
            # candidate, NOT a buy. Flag once/ticker/day, log to DB, skip buying.
            if protection:
                _ivr = get_iv_rank(ticker, iv)
                if _ivr is not None and _ivr >= SELL_PREMIUM_IV_RANK:
                    _spk = f"SP_{ticker}_{today}"
                    if brain.get("signals_sent", {}).get(_spk) != today:
                        brain.setdefault("signals_sent", {})[_spk] = today
                        save_brain(brain)
                        tg(f"💰 SELL PREMIUM: {ticker} IV rank {_ivr:.0f}. Consider covered call or cash-secured put.")
                        try: memdb.log_sell_premium_candidate(ticker, _ivr, market_mode="PROTECTION", f_and_g=fg)
                        except Exception as _e: log.error(f"log_sell_premium_candidate: {_e}")
                        log.info(f"SELL PREMIUM: {ticker} IV rank {_ivr:.0f}")
                    log.info(f"SKIP {ticker}: IV too high (rank {_ivr:.0f}) — sell-premium candidate")
                    continue
            score, signals = score_setup(ticker, price, iv, opt_type, ctx, config)
            # Lower threshold for put_buy in RISK_OFF
            threshold = 40 if (opt_type == "put_buy" and regime == "RISK_OFF") else 60
            if score < threshold:
                log.info(f"SKIP {ticker}: no signal (score {score} < {threshold})")
                continue

            contract = find_best_contract(contracts, price, opt_type)
            if not contract: continue

            strike = float(contract.get("strike_price",0))
            dte = (datetime.strptime(contract.get("expiration_date",""), "%Y-%m-%d") - datetime.now()).days

            # (#1) DTE floor — never trade under 30 days to expiry.
            if dte < 30:
                log.info(f"SKIP: {ticker} {opt_type} {dte} DTE — under 30-day floor")
                continue

            # (#3) Moneyness — buy-side strikes must be within 10% of spot.
            if opt_type in ("call_buy", "put_buy") and strike_too_far_otm(price, strike):
                log.info(f"SKIP: strike too far OTM — {ticker} ${strike:.0f} vs spot ${price:.2f}")
                continue

            # (#2) IV Rank — don't BUY premium when implied vol is already rich.
            iv_rank = None
            if opt_type in ("call_buy", "put_buy"):
                iv_rank = get_iv_rank(ticker, iv)
                if iv_rank is not None and iv_rank > MAX_IV_RANK:
                    log.info(f"SKIP: IV Rank {iv_rank:.0f} — too expensive to buy ({ticker} {opt_type})")
                    continue
                elif iv_rank is not None:
                    log.info(f"IV ratio {iv_rank/100:.1f} — OK, proceeding ({ticker} {opt_type})")

            # Use yfinance bid/ask directly, fallback to Alpaca
            bid = contract.get("bid", 0)
            ask = contract.get("ask", 0)
            premium = round((float(bid) + float(ask)) / 2, 2) if (bid or ask) else get_option_mid_price(contract.get("symbol",""))
            if not premium: continue

            top_setups.append({
                "score": score,
                "ticker": ticker,
                "price": price,
                "strategy": opt_type,
                "strike": strike,
                "premium": premium,
                "dte": dte,
                "iv": iv,
                "signals": signals,
                "contract": contract.get("symbol",""),
                "contract_expiry": contract.get("expiration_date",""),
                "config": config,
                "quote_ts": contract.get("quote_ts", 0),
                "week_change_pct": contract.get("week_change_pct", 0),
                "iv_ratio": iv_rank if iv_rank is not None else 0
            })

    top_setups.sort(key=lambda x: x["score"], reverse=True)
    today = datetime.now().strftime("%Y-%m-%d")
    if "signals_sent" not in brain:
        brain["signals_sent"] = {}
    brain["signals_sent"] = {k: v for k, v in brain["signals_sent"].items() if v == today}
    for setup in top_setups[:2]:
        dedup_key = f"{setup['ticker']}_{float(setup['strike']):.1f}_{today}"
        if brain["signals_sent"].get(dedup_key) == today:
            log.info(f"DEDUP: {setup['ticker']} ${setup['strike']} already sent today — skipping")
            continue
        alert = build_trade_alert(setup, ctx)
        if alert:
            # === OPTIONS GATE — block bad signals before send ===
            from options_gate import gate_signal
            import time as _time
            _sig = {
                "strategy": setup.get("strategy", "SELL_PUT"),
                "ticker": setup.get("ticker", ""),
                "quote_price": setup.get("price", 0),
                "quote_ts": setup.get("quote_ts", _time.time()),
                "iv_ratio": setup.get("iv_ratio", 0),
                "cash_required": setup.get("strike", 0) * 100,
                "account_value": brain.get("account_value", 50000),
                "week_change_pct": setup.get("week_change_pct", 0),
            }
            _ok, _reasons = gate_signal(_sig)
            if not _ok:
                log.warning(f"GATE BLOCKED {setup['ticker']}: {_reasons}")
                tg(f"🚫 GATE BLOCKED: {setup['ticker']} {setup.get('strategy','')}\n" + "\n".join(_reasons))
                continue
            # === END GATE ===
            dedup_key = f"{setup['ticker']}_{float(setup['strike']):.1f}_{today}"
            brain["signals_sent"][dedup_key] = today
            save_brain(brain)
            tg(alert)
            # Record the recommended signal in options_trades, tagged via the
            # catalyst column as "jarvis_auto" (log_options_trade has no `source`
            # param/column — catalyst is the row's origin marker). Written BEFORE
            # the paper-trade guards so EVERY recommended setup lands in the DB
            # even when it isn't taken as a paper position.
            _db_id = None
            if DB_ENABLED:
                try:
                    _db_id = memdb.log_options_trade(
                        ticker=setup["ticker"], strategy=setup["strategy"],
                        strike=setup["strike"], premium=setup["premium"],
                        dte=setup.get("dte", 0), iv=setup.get("iv", 0),
                        score=setup.get("score", 0),
                        contract_symbol=setup.get("contract"),
                        stock_price=setup.get("price"),
                        regime=ctx.get("macro", {}).get("regime", "?"),
                        fear_greed=get_equity_fear_greed(ctx)[0],
                        vix=vix,
                        btc_signal=ctx.get("brain", {}).get("btc_signal", "neutral"),
                        catalyst="jarvis_auto",
                    )
                except Exception as _se:
                    log.error(f"DB signal insert failed: {_se}")
            # GUARD: skip if open paper position already exists for this ticker + direction
            try:
                import paper_trades_store as _store
                _data = _store.read()
                _direction = "put" if "put" in setup["strategy"] else "call"
                _existing_open = [
                    t for t in _data.get("trades", [])
                    if t.get("ticker") == setup["ticker"]
                    and t.get("status") == "paper_open"
                    and _direction in t.get("strategy", "").lower()
                ]
                if _existing_open:
                    log.info(f"SKIP {setup['ticker']} {setup['strategy']}: open {_direction} position already exists")
                    continue
            except Exception as _pg:
                log.warning(f"Position guard check failed: {_pg}")
            # Auto-log to paper trades (lock-protected + per-ticker exposure cap)
            try:
                import paper_trades_store as _store
                from datetime import datetime as _dt
                # Junk-contract guard: drop deep-OTM / near-worthless setups (issue #4)
                _junk, _jr = _store.is_junk_contract(setup["price"], setup["strike"], setup["premium"])
                if _junk:
                    log.info(f"JUNK: skipped {setup['ticker']} ${setup['strike']} — {_jr}")
                    continue
                # GUARD 1: market hours only (9:30am-4pm EDT)
                from zoneinfo import ZoneInfo
                _et_hour = _dt.now(ZoneInfo('America/New_York')).hour
                _et_min = _dt.now(ZoneInfo('America/New_York')).minute
                if not ((_et_hour == 9 and _et_min >= 30) or (10 <= _et_hour <= 15) or (_et_hour == 16 and _et_min == 0)):
                    log.info(f"AFTER HOURS: skipped {setup['ticker']} — outside 9:30am-4pm EDT")
                    continue
                # GUARD 2: premium sanity — reject if premium > 12% of stock price
                _prem_pct = setup["premium"] / setup["price"]
                if _prem_pct > 0.12:
                    log.info(f"PREMIUM INSANE: skipped {setup['ticker']} ${setup['strike']} — premium ${setup['premium']:.2f} is {_prem_pct:.1%} of stock price")
                    continue
                # GUARD 3: hard global ceiling across all bots (item #2 phase 2)
                try:
                    import portfolio_state as _ps
                    _gok, _gwhy = _ps.can_open(setup["ticker"], round(setup["premium"]*100, 2))
                    if not _gok:
                        log.info(f"GLOBAL CEILING: skipped {setup['ticker']} ${setup['strike']} — {_gwhy}")
                        continue
                except Exception:
                    pass
                _cost = round(setup["premium"]*100, 2)
                _new = {
                    "ticker": setup["ticker"],
                    "strategy": setup["strategy"],
                    "strike": setup["strike"],
                    "expiry": _valid_expiry(setup["ticker"], setup.get("contract_expiry"), setup.get("dte", 14)),
                    "entry_price": setup["price"],
                    "premium": setup["premium"],
                    "cost_per_contract": _cost,
                    "score": setup["score"],
                    "iv": setup["iv"],
                    "entry_date": _dt.now().strftime("%Y-%m-%d"),
                    "entry_time": _dt.now().strftime("%H:%M"),
                    "source": "jarvis_auto",
                    "signals": setup.get("signals", []),
                    "status": "paper_open",
                    "result": None,
                    "exit_price": None,
                    "pnl": None
                }
                def _append(data):
                    # Exact dedup inside the lock — prevents race where two concurrent
                    # scan_and_alert calls both pass the pre-flight guard above.
                    for _t in data.get("trades", []):
                        if (
                            _t.get("ticker") == setup["ticker"]
                            and _t.get("strategy") == setup["strategy"]
                            and float(_t.get("strike", 0)) == float(setup["strike"])
                            and _t.get("entry_date") == _new["entry_date"]
                            and _t.get("status") == "paper_open"
                        ):
                            return ("dedup", f"already open: {setup['ticker']} {setup['strategy']} ${setup['strike']}")
                    capped, reason = _store.would_exceed_cap(data, setup["ticker"], _cost, trade=_new)
                    if capped:
                        return ("capped", reason)
                    data["trades"].append(_new)
                    return ("logged", None)
                _outcome, _reason = _store.update(_append)
                if _outcome == "dedup":
                    log.info(f"DEDUP: skipped paper log for {setup['ticker']} {setup['strategy']} ${setup['strike']} — {_reason}")
                elif _outcome == "capped":
                    log.info(f"CAP: skipped {setup['ticker']} ${setup['strike']} — {_reason}")
                    _cap_key = f"CAP_{setup['ticker']}_{today}"
                    if brain["signals_sent"].get(_cap_key) != today:
                        brain["signals_sent"][_cap_key] = today
                        save_brain(brain)
                        tg(f"🚧 EXPOSURE CAP hit — skipped {setup['ticker']} {setup['strategy']} ${setup['strike']}\n{_reason}")
            except Exception as _pe:
                log.error(f"Paper log error: {_pe}")
            # Log as potential trade
            trade = {
                "id": f"{setup['ticker']}_{datetime.now().strftime('%Y%m%d%H%M')}",
                "ts": datetime.now().isoformat(),
                "db_id": _db_id,  # reuse the signal row inserted above (no dup)
                "ticker": setup["ticker"],
                "strategy": setup["strategy"],
                "strike": setup["strike"],
                "premium": setup["premium"],
                "dte": setup["dte"],
                "iv": setup["iv"],
                "score": setup["score"],
                "signals": setup["signals"],
                "contract_symbol": setup["contract"],
                "stock_price": setup["price"],
                "regime": ctx.get("macro",{}).get("regime","?"),
                "fg_at_entry": get_equity_fear_greed(ctx)[0],
                "vix_at_entry": vix,
                "btc_signal": ctx.get("brain",{}).get("btc_signal","neutral"),
                "status": "paper",
                "result": None,
                "pnl": None,
                "closed_ts": None,
            }
            brain = log_trade(brain, trade)

    return brain

def build_trade_alert(setup, ctx):
    """Build plain English trade alert"""
    ticker = setup["ticker"]
    strategy = setup["strategy"]
    price = setup["price"]
    strike = setup["strike"]
    premium = setup["premium"]
    dte = setup["dte"]
    iv = setup["iv"] or 0
    score = setup["score"]

    if strategy == "put_sell":
        emoji = "🎡"
        action = "SELL PUT"
        cash_needed = strike * 100
        max_profit = premium * 100
        max_loss = (strike - premium) * 100
        monthly = round(premium/strike*100 * 30/dte, 1)
        plain = (f"You sell someone the right to make you buy {ticker} at ${strike:.0f}\n"
                f"They pay you ${premium:.2f}/share = ${max_profit:.0f} cash upfront\n"
                f"If {ticker} stays above ${strike:.0f} → keep the ${max_profit:.0f}\n"
                f"If {ticker} drops below ${strike:.0f} → you buy 100 shares at ${strike:.0f}\n"
                f"Real cost if assigned: ${strike-premium:.2f}/share (below today's price)")
    else:
        emoji = "🚀"
        action = "BUY PUT" if strategy == "put_buy" else "BUY CALL"
        cash_needed = premium * 100
        max_profit = "unlimited"
        max_loss = premium * 100
        monthly = "N/A"
        if strategy == "put_buy":
            breakeven = strike - premium
            plain = (f"You pay ${premium:.2f}/share = ${cash_needed:.0f} for the right to SELL {ticker} at ${strike:.0f}\n"
                    f"Breakeven: {ticker} must drop to ${breakeven:.2f} by expiration\n"
                    f"If {ticker} drops below ${breakeven:.2f} → you profit\n"
                    f"If {ticker} stays above ${strike:.0f} → you lose ${cash_needed:.0f}\n"
                    f"This is a BEARISH bet — you want {ticker} to fall")
        else:  # call_buy
            breakeven = strike + premium
            plain = (f"You pay ${premium:.2f}/share = ${cash_needed:.0f} for the right to BUY {ticker} at ${strike:.0f}\n"
                    f"Breakeven: {ticker} must rise to ${breakeven:.2f} by expiration\n"
                    f"If {ticker} rises above ${breakeven:.2f} → you profit\n"
                    f"If {ticker} stays below ${strike:.0f} → you lose ${cash_needed:.0f}\n"
                    f"This is a BULLISH bet — you want {ticker} to rise")

    # Key signals in plain English
    signal_plain = []
    for sig in setup["signals"][:4]:
        is_bearish = strategy in ("put_buy",)
        if "REGIME:RISK_ON" in sig:
            signal_plain.append("Market trending up — headwind for puts" if is_bearish else "Market trending up — tailwind for calls")
        elif "REGIME:RISK_OFF" in sig:
            signal_plain.append("Market in fear mode — tailwind for puts" if is_bearish else "Market in fear mode — headwind for calls")
        elif "FG:" in sig and "FEAR" in sig:
            signal_plain.append("Everyone scared — supports bearish thesis" if is_bearish else "Contrarian: fear often precedes bounces")
        elif "IV:" in sig and "HIGH" in sig:
            signal_plain.append("⚠️ IV high — you're paying expensive premium, needs big move to profit")
        elif "CONGRESS" in sig:
            signal_plain.append("Politicians active in this stock — check direction")
        elif "EARNINGS:CRITICAL" in sig:
            signal_plain.append("⚠️ Earnings soon — IV will crush after announcement")

    lines = [
        f"{emoji} JARVIS OPTIONS SIGNAL",
        f"Score: {score}/100",
        f"{'='*24}",
        f"{action}: {ticker} @ ${price:.2f}",
        f"Strike: ${strike:.0f} | Exp: {dte} days | IV: {iv:.0f}%",
        f"{'='*24}",
        f"IN PLAIN ENGLISH:",
        plain,
        f"{'='*24}",
        f"WHY THIS TRADE:",
    ] + signal_plain + [
        f"{'='*24}",
        f"NUMBERS:",
        f"Cash needed: ${cash_needed:,.0f}",
        f"Max profit: ${round(float(max_profit),0):,.0f}" if not isinstance(max_profit,str) else f"Max profit: ${max_profit}",
        f"Max loss: ${max_loss:,.0f}",
        f"Monthly return if works: {monthly}%" if monthly != "N/A" else "",
        f"{'='*24}",
        f"Paper trade this on Alpaca first",
        f"Text LEARN {ticker} for more detail"
    ]

    return "\n".join(l for l in lines if l)

def reload_put_scan():
    """Dead-cat put reload — NVDA/SPY/QQQ puts AT OR ABOVE the cascade trigger
    price (resistance), read from the last cascade market_event's ticker_snapshot
    rather than the depressed current price. Fired when the dead-cat RIP triggers."""
    if not DB_ENABLED:
        return
    try:
        snap = memdb.get_last_cascade_snapshot()
    except Exception:
        snap = {}
    for ticker in ["NVDA", "SPY", "QQQ"]:
        try:
            price = get_price(ticker)
            ref = snap.get(ticker) or price          # cascade trigger price (resistance)
            if not ref or not price:
                continue
            contracts, _iv = get_yf_contracts(ticker, "put")
            above = [c for c in (contracts or []) if float(c.get("strike_price", 0)) >= ref]
            if not above:
                log.info(f"RELOAD {ticker}: no puts at/above ${ref:.2f}")
                continue
            best = min(above, key=lambda c: float(c.get("strike_price", 0)) - ref)
            strike = float(best.get("strike_price", 0))
            bid, ask = best.get("bid", 0), best.get("ask", 0)
            prem = round((float(bid) + float(ask)) / 2, 2) if (bid or ask) else get_option_mid_price(best.get("symbol", ""))
            if not prem:
                continue
            tg(f"🔴 RELOAD WINDOW: {ticker} put at ${strike:.0f} near resistance. Premium: ${prem}")
            log.info(f"RELOAD {ticker}: put ${strike:.0f} prem ${prem} (ref ${ref:.2f})")
        except Exception as e:
            log.error(f"reload_put_scan {ticker}: {e}")

def main():
    log.info("JARVIS OPTIONS BRAIN ONLINE")
    tg(f"🧠 OPTIONS BRAIN ONLINE\n"
       f"Tracking all {len(UNIVERSE)} tickers\n"
       "Learning from every trade\n"
       "Daily brief at 8am EDT\n"
       "Text LEARN <ticker> anytime")

    brain = load_brain()
    last_scan = 0
    last_closed_log = 0   # throttle the "market closed" notice (see below)

    while True:
        try:
            now = datetime.now()
            # Server runs in UTC; EDT = UTC-4. Use a tz-aware UTC clock
            # (the old naive-UTC helper is deprecated in 3.12+).
            edt_hour = (datetime.now(timezone.utc).hour - 4) % 24
            ctx = get_all_context()

            # Daily brief at 8am
            today = datetime.now().strftime("%Y-%m-%d")
            if edt_hour == 8 and brain.get("daily_brief_sent") != today:
                brain = morning_brief(brain, ctx)

            # Scan during market hours every hour
            is_market_day = datetime.now().weekday() < 5
            # Cascade override: jarvis_cascade sets force_put_scan on a CASCADE L1 —
            # run an immediate scan regardless of the hourly timer, then clear it.
            _force = False
            try:
                _force = bool(memdb.brain_get("force_put_scan")) if DB_ENABLED else False
            except Exception:
                _force = False
            # Dead-cat reload (set by jarvis_cascade on RIP): immediate NVDA/SPY/QQQ
            # put reload at/above cascade trigger price, separate from the full scan.
            try:
                if DB_ENABLED and memdb.brain_get("reload_scan") and is_market_day and 9 <= edt_hour <= 16:
                    log.info("Dead-cat reload_scan — NVDA/SPY/QQQ put reload")
                    reload_put_scan()
                    memdb.brain_set("reload_scan", False)
            except Exception as _re:
                log.error(f"reload_scan: {_re}")
            if is_market_day and 9 <= edt_hour <= 16:
                if _force or time.time() - last_scan >= INTERVAL:
                    if _force:
                        log.info("Cascade force_put_scan — running immediate scan")
                        try: memdb.brain_set("force_put_scan", False)
                        except Exception: pass
                    brain = grade_closed_trades(brain)
                    brain = scan_and_alert(brain, ctx)
                    last_scan = time.time()
            else:
                # Market closed — log a heartbeat notice instead of going silent,
                # so it's obvious the loop is alive and intentionally idle.
                # Throttled to once/hour to avoid spamming every 5-min cycle.
                if time.time() - last_closed_log >= 3600:
                    _reason = "weekend" if not is_market_day else f"outside market hours (now ~{edt_hour:02d}:00 EDT)"
                    log.info(f"Market closed, skipping scan — {_reason}")
                    last_closed_log = time.time()

            save_brain(brain)
        except Exception as e:
            import traceback; log.error(f"Brain cycle: {e}\n{traceback.format_exc()}")
        _jb_hb.update_bot_heartbeat("jarvis_options_brain")

        time.sleep(300)  # check every 5 min

if __name__ == "__main__":
    main()
