#!/usr/bin/env python3
"""
JARVIS DATA ENGINE v2
Replaces all Binance calls with Kraken + Coinbase.
Binance is geo-blocked on DigitalOcean VPS (HTTP 451).
"""
import requests, time, math, logging
log = logging.getLogger("JARVIS_DATA")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "JarvisBot/2.0"})

def safe_get(url, params=None, timeout=10):
    for attempt in range(2):
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
            if r.status_code == 200: return r.json()
            log.warning(f"HTTP {r.status_code}: {url[:60]}")
        except Exception as e:
            log.warning(f"Request failed: {e}")
        time.sleep(0.5)
    return None

def get_btc_price():
    """Get BTC price from Coinbase"""
    try:
        d = safe_get("https://api.coinbase.com/v2/prices/BTC-USD/spot")
        if d: return float(d["data"]["amount"])
    except: pass
    try:
        d = safe_get("https://api.kraken.com/0/public/Ticker", params={"pair":"XBTUSD"})
        if d: return float(d["result"]["XXBTZUSD"]["c"][0])
    except: pass
    return None

def get_ohlcv_kraken(pair="XBTUSD", interval=60, limit=100):
    """
    Get OHLCV data from Kraken.
    interval: 1=1m, 5=5m, 15=15m, 60=1h, 240=4h, 1440=1d
    Returns list of [time, open, high, low, close, vwap, volume, count]
    """
    try:
        d = safe_get("https://api.kraken.com/0/public/OHLC",
            params={"pair": pair, "interval": interval})
        if d and "result" in d:
            key = list(d["result"].keys())[0]
            candles = d["result"][key]
            return candles[-limit:]
    except Exception as e:
        log.error(f"Kraken OHLC: {e}")
    return []

def get_closes(interval=60, limit=50):
    """Get closing prices from Kraken"""
    candles = get_ohlcv_kraken(interval=interval, limit=limit)
    return [float(c[4]) for c in candles] if candles else []

def get_highs_lows(interval=60, limit=30):
    candles = get_ohlcv_kraken(interval=interval, limit=limit)
    highs  = [float(c[2]) for c in candles]
    lows   = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]
    opens  = [float(c[1]) for c in candles]
    vols   = [float(c[6]) for c in candles]
    return highs, lows, closes, opens, vols

def calc_ema(data, period):
    if len(data) < period: return data[-1] if data else 0
    k = 2/(period+1)
    e = [data[0]]
    for p in data[1:]: e.append(p*k+e[-1]*(1-k))
    return e[-1]

def get_rsi(period=14):
    """RSI from Kraken 1h candles"""
    try:
        closes = get_closes(interval=60, limit=period+5)
        if len(closes) < period+1: return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i-1]
            gains.append(max(d,0)); losses.append(max(-d,0))
        ag = sum(gains[-period:])/period
        al = sum(losses[-period:])/period
        if al == 0: return 100.0
        rs = ag/al
        return round(100 - 100/(1+rs), 1)
    except Exception as e:
        log.error(f"RSI: {e}"); return 50.0

def get_macd(closes=None):
    """MACD from Kraken 1h candles"""
    try:
        if closes is None:
            closes = get_closes(interval=60, limit=40)
        if len(closes) < 26: return 0.0, 0.0, 0.0
        ema12 = calc_ema(closes, 12)
        ema26 = calc_ema(closes, 26)
        macd_line = ema12 - ema26
        # Signal line needs series
        def ema_series(data, period):
            k = 2/(period+1); e = [data[0]]
            for p in data[1:]: e.append(p*k+e[-1]*(1-k))
            return e
        ema12s = ema_series(closes, 12)
        ema26s = ema_series(closes, 26)
        macd_series = [f-s for f,s in zip(ema12s[13:], ema26s[13:])]
        if len(macd_series) >= 9:
            signal = calc_ema(macd_series, 9)
            hist = round(macd_line - signal, 2)
        else:
            signal = 0; hist = 0
        return round(macd_line, 2), round(signal, 2), hist
    except Exception as e:
        log.error(f"MACD: {e}"); return 0.0, 0.0, 0.0

def get_bollinger(closes=None, period=20):
    """Bollinger Bands from Kraken"""
    try:
        if closes is None:
            closes = get_closes(interval=60, limit=25)
        if len(closes) < period: return 0,0,0,0.5
        recent = closes[-period:]
        mid = sum(recent)/period
        std = math.sqrt(sum((p-mid)**2 for p in recent)/period)
        upper = mid + 2*std; lower = mid - 2*std
        current = closes[-1]
        pct_b = (current-lower)/(upper-lower) if upper!=lower else 0.5
        return round(upper,2), round(mid,2), round(lower,2), round(pct_b,3)
    except Exception as e:
        log.error(f"BB: {e}"); return 0,0,0,0.5

def get_momentum():
    """Price momentum from Kraken"""
    try:
        closes_1h = get_closes(interval=60, limit=25)
        closes_1d = get_closes(interval=1440, limit=8)
        m1h = round((closes_1h[-1]-closes_1h[-2])/closes_1h[-2]*100, 2) if len(closes_1h)>=2 else 0
        m24h = round((closes_1h[-1]-closes_1h[-25])/closes_1h[-25]*100, 2) if len(closes_1h)>=25 else 0
        m7d = round((closes_1d[-1]-closes_1d[-8])/closes_1d[-8]*100, 2) if len(closes_1d)>=8 else 0
        return {"1h": m1h, "24h": m24h, "7d": m7d}
    except: return {"1h":0.0,"24h":0.0,"7d":0.0}

def get_4h_momentum():
    """4H trend from Kraken"""
    try:
        closes = get_closes(interval=240, limit=6)
        if len(closes) < 2: return 0.0, "NEUTRAL"
        move = (closes[-1]-closes[0])/closes[0]*100
        if move > 2.0:    trend = "STRONG_UP"
        elif move > 0.5:  trend = "WEAK_UP"
        elif move < -2.0: trend = "STRONG_DOWN"
        elif move < -0.5: trend = "WEAK_DOWN"
        else:             trend = "NEUTRAL"
        return round(move, 2), trend
    except: return 0.0, "NEUTRAL"

def get_short_term_trend():
    """1m and 5m price action from Kraken"""
    try:
        highs, lows, closes, opens, vols = get_highs_lows(interval=1, limit=30)
        if len(closes) < 10: return "Short term data unavailable"
        current = closes[-1]

        # EMA9
        def ema_s(data, p):
            k=2/(p+1); e=[data[0]]
            for x in data[1:]: e.append(x*k+e[-1]*(1-k))
            return e
        ema9 = ema_s(closes, 9)[-1]
        price_vs_ema = "ABOVE EMA9" if current > ema9 else "BELOW EMA9"

        # Volume dominance
        down_vol = sum(vols[i] for i in range(-10,0) if closes[i] < opens[i])
        up_vol   = sum(vols[i] for i in range(-10,0) if closes[i] >= opens[i])
        total_vol = down_vol + up_vol
        sell_pct = round(down_vol/total_vol*100) if total_vol > 0 else 50
        if sell_pct > 65:   vol_bias = f"SELLERS DOMINANT ({sell_pct}%)"
        elif sell_pct < 35: vol_bias = f"BUYERS DOMINANT ({100-sell_pct}%)"
        else:               vol_bias = f"BALANCED ({sell_pct}% sell)"

        # Trend direction
        slope = sum(closes[-5:])/5 - sum(closes[:5])/5
        if slope > 50:    mt = "STRONG UP"
        elif slope > 15:  mt = "WEAK UP"
        elif slope < -50: mt = "STRONG DOWN"
        elif slope < -15: mt = "WEAK DOWN"
        else:             mt = "FLAT"

        # Lower highs
        rh = highs[-5:]
        lower_highs = all(rh[i]>rh[i+1] for i in range(len(rh)-1))

        # Best NO strike
        suggested_no = round(current/100)*100 + 100
        strike_note = f"Best NO strike: ${suggested_no:,.0f} (${suggested_no-current:+.0f} above)"

        # Trend duration
        trend_candles = 0
        for i in range(len(highs)-1, 0, -1):
            if highs[i] < highs[i-1]: trend_candles += 1
            else: break

        lines = [
            "── SHORT TERM PRICE ACTION ──",
            f"1m: {mt} (${slope:+.0f})",
            f"EMA9: ${ema9:,.0f} — {price_vs_ema}",
            f"Lower highs: {'YES' if lower_highs else 'NO'}",
            f"Volume: {vol_bias}",
            f"Trend: {trend_candles}min",
            strike_note
        ]
        return "\n".join(lines)
    except Exception as e:
        log.error(f"Short term: {e}")
        return "Short term data unavailable"

def get_atr(periods=24):
    """ATR from Kraken"""
    try:
        candles = get_ohlcv_kraken(interval=60, limit=periods)
        if len(candles) < 2: return 400.0
        trs = []
        for i in range(1, len(candles)):
            h = float(candles[i][2]); l = float(candles[i][3])
            pc = float(candles[i-1][4])
            trs.append(max(h-l, abs(h-pc), abs(l-pc)))
        return round(sum(trs)/len(trs), 2)
    except: return 400.0

def get_volume_ratio():
    """Volume ratio vs 20-period average"""
    try:
        candles = get_ohlcv_kraken(interval=60, limit=22)
        if len(candles) < 2: return 1.0
        vols = [float(c[6]) for c in candles]
        current = vols[-1]
        avg = sum(vols[:-1])/len(vols[:-1])
        return round(current/avg, 2) if avg > 0 else 1.0
    except: return 1.0

def get_funding_rate():
    """Funding rate — Kraken doesn't have perps, return 0"""
    return 0.0

def get_fear_greed():
    try:
        d = safe_get("https://api.alternative.me/fng/?limit=1")
        if d: return int(d["data"][0]["value"]), d["data"][0]["value_classification"]
    except: pass
    return 50, "Neutral"

def get_support_resistance(lookback_hours=168):
    """S/R from Kraken weekly range"""
    try:
        candles = get_ohlcv_kraken(interval=60, limit=lookback_hours)
        if not candles: return {}
        highs  = [float(c[2]) for c in candles]
        lows   = [float(c[3]) for c in candles]
        closes = [float(c[4]) for c in candles]
        return {
            "resistance": max(highs),
            "support":    min(lows),
            "avg":        round(sum(closes)/len(closes), 2)
        }
    except: return {}
