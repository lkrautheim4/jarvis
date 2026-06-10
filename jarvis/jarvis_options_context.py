#!/usr/bin/env python3
"""
JARVIS OPTIONS CONTEXT ENGINE
Aggregates all bot signals into one options trading context.
Used by jarvis_options.py to make smarter trade decisions.
"""
import json, os, requests
from datetime import datetime

def get_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        d = r.json()["data"][0]
        return int(d["value"]), d["value_classification"]
    except: return 50, "Neutral"

def get_central_brain():
    try:
        return json.load(open("/root/jarvis/jarvis_central_brain.json"))
    except: return {}

def get_level5():
    try:
        return json.load(open("/root/jarvis/jarvis_level5.json"))
    except: return {}

def get_intelligence():
    try:
        return json.load(open("/root/jarvis/jarvis_intel.json"))
    except: return {}

def get_btc_state():
    try:
        mem = json.load(open("/root/jarvis/btc_memory.json"))
        prices = mem.get("prices", [])
        if not prices: return {}
        last = prices[-1]
        # Calculate MACD from last 26 prices
        if len(prices) >= 26:
            closes = [p["price"] for p in prices[-26:]]
            def ema(data, period):
                k = 2/(period+1); e = [data[0]]
                for p in data[1:]: e.append(p*k+e[-1]*(1-k))
                return e
            ema12 = ema(closes, 12)[-1]
            ema26 = ema(closes, 26)[-1]
            macd = round(ema12 - ema26, 2)
        else:
            macd = 0
        return {
            "price": last.get("price", 0),
            "rsi": last.get("rsi", 50),
            "change_1h": last.get("1h", 0),
            "change_24h": last.get("24h", 0),
            "macd": macd,
            "macd_signal": "BULLISH" if macd > 0 else "BEARISH"
        }
    except: return {}

def get_hot_tickers():
    try:
        cb = get_central_brain()
        return cb.get("hot_tickers", [])
    except: return []

def check_earnings_soon(symbol):
    """Check if symbol has earnings in next 7 days — avoid trading before earnings"""
    try:
        l5 = get_level5()
        earnings = l5.get("earnings_calendar", [])
        for e in earnings:
            if e.get("symbol") == symbol:
                from datetime import datetime, timedelta
                earn_date = datetime.strptime(e.get("date",""), "%Y-%m-%d")
                days_away = (earn_date - datetime.now()).days
                if 0 <= days_away <= 7:
                    return True, days_away
        return False, 999
    except: return False, 999

def get_sector_bias(symbol):
    """Get sector momentum for a symbol"""
    try:
        sector_map = {
            "SPY": "MARKET", "QQQ": "TECH", "IWM": "SMALL_CAP",
            "AAPL": "TECH", "NVDA": "TECH", "TSLA": "AUTO",
            "XLF": "FINANCE", "XLE": "ENERGY", "XLV": "HEALTH"
        }
        import jarvis_freshness as _fresh
        l5 = get_level5()
        sector_scores = _fresh.fresh_sector_scores(l5)
        sector = sector_map.get(symbol, "MARKET")
        score = sector_scores.get(sector, 0)
        if score > 1.0: return "BULLISH", score
        elif score < -1.0: return "BEARISH", score
        return "NEUTRAL", score
    except: return "NEUTRAL", 0

def get_dark_pool_signals():
    """Get dark pool and insider signals from intelligence bot"""
    try:
        intel = get_intelligence()
        alerts = intel.get("options_alerts", [])[-3:]
        dark = intel.get("darkpool_alerts", [])[-3:]
        insider = intel.get("insider_alerts", [])[-3:]
        signals = []
        for a in alerts + dark + insider:
            if a.get("summary"): signals.append(a["summary"][:60])
        return signals
    except: return []

def get_time_quality():
    """Options trade quality based on time of day"""
    now = datetime.utcnow()
    edt = (now.hour - 4) % 24
    # Best options trading windows
    if 9 <= edt <= 10: return "PRIME", "Market open — best liquidity"
    elif 10 <= edt <= 11: return "GOOD", "Morning momentum"
    elif 11 <= edt <= 14: return "FAIR", "Mid-day — lower volume"
    elif 14 <= edt <= 15: return "GOOD", "Afternoon momentum"
    elif 15 <= edt <= 16: return "PRIME", "Power hour — best for exits"
    else: return "CLOSED", "Market closed"

def build_full_context(symbol, price):
    """
    Build complete options trading context from ALL bot data.
    Returns context string + structured data for Claude.
    """
    btc = get_btc_state()
    cb = get_central_brain()
    fg_val, fg_label = get_fear_greed()
    sector_bias, sector_score = get_sector_bias(symbol)
    has_earnings, days_to_earnings = check_earnings_soon(symbol)
    hot_tickers = get_hot_tickers()
    dark_pool = get_dark_pool_signals()
    time_quality, time_note = get_time_quality()
    l5 = get_level5()

    is_hot = symbol in hot_tickers

    context = {
        "symbol": symbol,
        "price": price,
        "btc_rsi": btc.get("rsi", 50),
        "btc_macd": btc.get("macd_signal", "NEUTRAL"),
        "btc_change_1h": btc.get("change_1h", 0),
        "btc_change_24h": btc.get("change_24h", 0),
        "fear_greed": fg_val,
        "fear_greed_label": fg_label,
        "sector_bias": sector_bias,
        "sector_score": sector_score,
        "has_earnings": has_earnings,
        "days_to_earnings": days_to_earnings,
        "is_hot_ticker": is_hot,
        "dark_pool_signals": dark_pool,
        "time_quality": time_quality,
        "time_note": time_note,
        "risk_level": cb.get("risk_level", "NORMAL"),
        "market_mood": cb.get("market_mood", "neutral"),
        "market_regime": l5.get("market_regime", "UNKNOWN"),
    }

    # Build trade recommendation
    blockers = []
    boosters = []

    if has_earnings:
        blockers.append(f"EARNINGS IN {days_to_earnings} DAYS — skip to avoid IV crush")
    if fg_val < 20:
        blockers.append(f"EXTREME FEAR ({fg_val}) — high risk, reduce size")
    if context["risk_level"] == "EXTREME":
        blockers.append("EXTREME risk level — no new trades")
    if time_quality == "CLOSED":
        blockers.append("Market closed")

    if is_hot: boosters.append(f"{symbol} is a HOT TICKER")
    if sector_bias == "BULLISH": boosters.append(f"Sector momentum BULLISH ({sector_score:+.1f}%)")
    if btc.get("rsi", 50) < 35: boosters.append("BTC RSI oversold — crypto bounce likely")
    if fg_val > 60: boosters.append(f"Greed ({fg_val}) — risk on environment")
    if dark_pool: boosters.append(f"Dark pool signal: {dark_pool[0]}")
    if time_quality == "PRIME": boosters.append(f"PRIME trading window — {time_note}")

    context["blockers"] = blockers
    context["boosters"] = boosters
    context["should_trade"] = len(blockers) == 0

    # Build prompt context string
    lines = [
        f"=== FULL MARKET CONTEXT ===",
        f"Symbol: {symbol} @ ${price:.2f}",
        f"",
        f"── BTC Intelligence ──",
        f"BTC RSI: {btc.get('rsi',50)} | MACD: {btc.get('macd_signal','?')} | 1h: {btc.get('change_1h',0):+.2f}%",
        f"",
        f"── Market Sentiment ──",
        f"Fear & Greed: {fg_val}/100 ({fg_label})",
        f"Market regime: {context['market_regime']}",
        f"Market mood: {context['market_mood']}",
        f"Risk level: {context['risk_level']}",
        f"",
        f"── {symbol} Context ──",
        f"Sector bias: {sector_bias} ({sector_score:+.1f}%)",
        f"Hot ticker: {'YES' if is_hot else 'NO'}",
        f"Earnings: {'IN ' + str(days_to_earnings) + ' DAYS — AVOID' if has_earnings else 'None soon — safe to trade'}",
        f"",
        f"── Time Quality ──",
        f"{time_quality} — {time_note}",
        f"",
    ]

    if dark_pool:
        lines.append("── Dark Pool / Intel ──")
        for s in dark_pool: lines.append(f"  {s}")
        lines.append("")

    if blockers:
        lines.append("── BLOCKERS ──")
        for b in blockers: lines.append(f"  ❌ {b}")
        lines.append("")

    if boosters:
        lines.append("── BOOSTERS ──")
        for b in boosters: lines.append(f"  ✅ {b}")

    lines.append("=========================")

    return "\n".join(lines), context
