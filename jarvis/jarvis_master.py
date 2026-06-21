#!/usr/bin/env python3
"""
JARVIS MASTER — One file to rule them all
BTC scalping + Kalshi predictions + options + memory + learning
Replaces: jarvis_alpha_v2, lenny_predictions, lenny_trader_bot
UPGRADED: MACD + Bollinger + Fear&Greed + Pattern Memory + Oracle Context + Time Bias + Kelly v2
"""
import json, time, math, requests, os, logging
try:
    import jarvis_memory as jmem
    SHARED_MEM = True
except:
    SHARED_MEM = False
try:
    import jarvis_session as jsess
    SESSION_ENABLED = True
except Exception as se:
    SESSION_ENABLED = False
try:
    import jarvis_brain as _jb_hb   # SQLite heartbeat the watchdog reads
except Exception:
    _jb_hb = None
try:
    import jarvis_manual_bets as jmb
except Exception as _jmb_e:
    jmb = None
from datetime import datetime, timedelta, timezone
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("JARVIS")
# ── CONFIG ──────────────────────────────────────────────────────────────────
from jarvis_secrets import CLAUDE_API_KEY as CLAUDE_KEY
from jarvis_secrets import ALPACA_PAPER_KEY as ALPACA_KEY, ALPACA_PAPER_SECRET as ALPACA_SECRET
ALPACA_BASE    = "https://paper-api.alpaca.markets"
TG_TRADER      = __import__("jarvis_secrets").TG_TOKEN_TRADER
TG_PRED        = __import__("jarvis_secrets").TG_TOKEN_PRED
CHAT_ID        = __import__("jarvis_secrets").TG_CHAT_ID
MEMORY_FILE    = "/root/jarvis/btc_memory.json"
BRAIN_FILE         = "/root/jarvis/kalshi_brain.json"
SKIPS_ARCHIVE_FILE = "/root/jarvis/kalshi_skips_archive.json"
SKIP_CAP           = 5000
SCHEMA_VERSION     = 2
MASTER_FILE    = "/root/jarvis/jarvis_master_brain.json"
PATTERN_FILE   = "/root/jarvis/jarvis_patterns.json"
# Scalp settings
SCALP_TARGET   = 0.003
SCALP_STOP     = 0.002
SCALP_SIZE     = 300
MAX_POSITIONS  = 2
DAILY_LOSS_MAX = 400
BANNED         = ["AVAX"]
TRADE_START_EDT = 9
TRADE_END_EDT   = 20

# ── TELEGRAM ────────────────────────────────────────────────────────────────
def tg(msg, token=None):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token or TG_TRADER}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg[:4000]}, timeout=10)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def tg_updates(token, offset=None):
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
            params={"timeout": 8, "offset": offset}, timeout=12)
        return r.json().get("result", [])
    except: return []

# ── CLAUDE ──────────────────────────────────────────────────────────────────
def claude(prompt, max_tokens=200):
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=20)
        resp = r.json()
        if "content" in resp:
            return resp["content"][0]["text"].strip()
    except Exception as e:
        log.error(f"Claude: {e}")
    return None


def claude_tool(prompt, tool, max_tokens=500):
    """Forced structured output: pin tool_choice to `tool` so Claude returns a
    validated input dict (enum-constrained bet, numeric fields) instead of prose
    that breaks the pipe parser. Returns the input dict, or None on error."""
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": max_tokens,
                  "tools": [tool], "tool_choice": {"type": "tool", "name": tool["name"]},
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=20)
        resp = r.json()
        if "error" in resp:
            log.error(f"Claude API error: {resp['error']}")
            return None
        for b in resp.get("content", []):
            if b.get("type") == "tool_use" and b.get("name") == tool["name"]:
                return b.get("input")
        log.error(f"Claude no tool_use (stop={resp.get('stop_reason')})")
    except Exception as e:
        log.error(f"Claude tool: {e}")
    return None


# Forced schema for the hourly Oracle prediction — replaces the fragile
# pipe-format reply that Claude kept ignoring (the "Bad Claude format" errors).
ORACLE_TOOL = {
    "name": "submit_oracle_call",
    "description": "Submit your BTC prediction for the hourly Kalshi target.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "target_prob":     {"type": "integer", "description": "Confidence 0-100 that BTC ends ABOVE the target by the deadline."},
            "predicted_price": {"type": "number",  "description": "Best-guess BTC price (USD) at the deadline."},
            "bet":             {"type": "string", "enum": ["YES", "NO", "SKIP"], "description": "YES = above target; NO = below; SKIP if uncertain."},
            "reason":          {"type": "string", "description": "One brutally specific sentence. No hedging."},
        },
        "required": ["target_prob", "predicted_price", "bet", "reason"],
        "additionalProperties": False,
    },
}

# Forced schema for the BTC/best-trade command (was STRIKE|YES/NO|CONFIDENCE%|REASON).
BEST_TRADE_TOOL = {
    "name": "submit_best_trade",
    "description": "Pick the single best Kalshi BTC market to trade right now.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "strike":     {"type": "number",  "description": "Strike price (USD) of the chosen Kalshi market."},
            "bet":        {"type": "string", "enum": ["YES", "NO", "SKIP"], "description": "YES = above strike; NO = below; SKIP if no good trade."},
            "confidence": {"type": "integer", "description": "Confidence 0-100 in this call."},
            "reason":     {"type": "string", "description": "One concise sentence."},
        },
        "required": ["strike", "bet", "confidence", "reason"],
        "additionalProperties": False,
    },
}

# Forced schema for the PRED command (was ABOVE|BELOW|CONFIDENCE%|REASON).
PRED_DIR_TOOL = {
    "name": "submit_direction",
    "description": "Predict whether BTC will be ABOVE or BELOW the reference price by the deadline.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "direction":  {"type": "string", "enum": ["ABOVE", "BELOW"], "description": "ABOVE or BELOW the reference price by the deadline."},
            "confidence": {"type": "integer", "description": "Confidence 0-100 in this direction."},
            "reason":     {"type": "string", "description": "One concise sentence."},
        },
        "required": ["direction", "confidence", "reason"],
        "additionalProperties": False,
    },
}

# Alert-gating from the 2026-06-10 accuracy audit (btc_memory graded preds): these
# EDT hours each graded >55% with n>=3 (overnight/early edge). Outside these hours,
# and in a flat/ranging 4h regime (graded ~22%, no edge), the hourly Oracle still
# LOGS its prediction (paper) but suppresses the Telegram alert.
EDGE_HOURS = {0, 1, 2, 3, 7, 21, 22}

# ── ALPACA ───────────────────────────────────────────────────────────────────
def alpaca(method, path, data=None):
    hdrs = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET, "Content-Type": "application/json"}
    for attempt in range(3):
        try:
            url = ALPACA_BASE + path
            if method == "GET":      r = requests.get(url, headers=hdrs, timeout=15)
            elif method == "POST":   r = requests.post(url, headers=hdrs, json=data, timeout=15)
            elif method == "DELETE": r = requests.delete(url, headers=hdrs, timeout=15)
            if r.status_code in [200, 201]: return r.json()
            log.warning(f"Alpaca {r.status_code}: {r.text[:100]}")
        except Exception as e:
            log.error(f"Alpaca {attempt+1}: {e}")
        time.sleep(1)
    return None

def get_account():   return alpaca("GET", "/v2/account") or {}
def get_positions(): return alpaca("GET", "/v2/positions") or []
def get_clock():     return alpaca("GET", "/v2/clock") or {}
def place_order(symbol, qty, side, order_type="market"):
    return alpaca("POST", "/v2/orders", {
        "symbol": symbol, "qty": str(qty), "side": side,
        "type": order_type, "time_in_force": "gtc"
    })
def close_position(symbol):
    import urllib.parse
    return alpaca("DELETE", f"/v2/positions/{urllib.parse.quote(symbol)}")

# ── MARKET DATA ──────────────────────────────────────────────────────────────
def get_coingecko_price(symbol):
    try:
        coin = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}.get(symbol)
        r = requests.get("https://api.coingecko.com/api/v3/simple/price",
            params={"ids": coin, "vs_currencies": "usd"}, timeout=10)
        return float(r.json()[coin]["usd"])
    except: return None

def get_funding_rate(symbol):
    # OKX perp funding — Binance futures (fapi) is geo-blocked (HTTP 451) from this
    # host and Binance.US has no futures API, so funding came back 0.0 every cycle.
    try:
        inst = {"BTC": "BTC-USDT-SWAP", "ETH": "ETH-USDT-SWAP",
                "SOL": "SOL-USDT-SWAP"}.get(symbol, f"{symbol}-USDT-SWAP")
        r = requests.get("https://www.okx.com/api/v5/public/funding-rate",
            params={"instId": inst}, timeout=8)
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data: return float(data[0]["fundingRate"])
    except: pass
    return 0.0

def get_volume_ratio(symbol):
    try:
        coin = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}.get(symbol)
        r = requests.get(f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart",
            params={"vs_currency": "usd", "days": "2", "interval": "hourly"}, timeout=12)
        volumes = [v[1] for v in r.json().get("total_volumes", [])]
        if len(volumes) >= 5:
            current = volumes[-1]
            avg = sum(volumes[-20:]) / min(20, len(volumes))
            return round(current / avg, 2) if avg > 0 else 1.0
    except: pass
    return 1.0

def get_momentum_snap(symbol="BTC"):
    try:
        r = requests.get("https://api.kraken.com/0/public/OHLC",
            params={"pair":"XBTUSD","interval":1}, timeout=8)
        candles = r.json()["result"]["XXBTZUSD"][-3:]
        prices = [float(c[4]) for c in candles]
        return round(prices[-1] - prices[0], 2)
    except: return 0.0

def get_strike_proximity(btc_price, strike, atr):
    diff = abs(btc_price - strike)
    if atr <= 0: return 0, "unknown"
    pct = round(diff / atr * 100, 1)
    if pct > 150: return pct, "very far — high confidence"
    elif pct > 80: return pct, "comfortably away"
    elif pct > 40: return pct, "moderate distance"
    else: return pct, "close to strike — low confidence"

def get_consecutive_signal():
    try:
        session = jsess.get_session()
        if session and len(session.get("preds", [])) >= 2:
            last2 = session["preds"][-2:]
            if last2[0]["direction"] == last2[1]["direction"]:
                return last2[-1]["direction"], True
        return None, False
    except: return None, False

def get_kalshi_volume_spike(markets):
    try:
        total_vol = sum(float(m.get("volume_fp","0") or 0) for m in markets)
        return total_vol, total_vol > 500
    except: return 0, False

def get_best_strike(markets, btc_price, atr, direction):
    best = None; best_edge = 0
    for m in markets:
        strike = m["strike"]; yes = m["yes"]; no = m["no"]
        diff = btc_price - strike
        if atr <= 0: continue
        dist_pct = abs(diff) / atr
        prob_above = min(0.95, 0.5 + dist_pct * 0.3) if diff > 0 else max(0.05, 0.5 - dist_pct * 0.3)
        if direction in ["ABOVE","UP"]:
            edge = prob_above - yes
            if edge > best_edge:
                best_edge = edge
                best = {"strike":strike,"action":"YES","price":yes,"edge":round(edge*100,1)}
        else:
            edge = (1-prob_above) - no
            if edge > best_edge:
                best_edge = edge
                best = {"strike":strike,"action":"NO","price":no,"edge":round(edge*100,1)}
    return best

# ── UPGRADED: MACD ───────────────────────────────────────────────────────────
def get_macd(prices, fast=12, slow=26, signal=9):
    """MACD from a list of prices. Returns (macd_line, signal_line, histogram)"""
    try:
        if len(prices) < slow + signal:
            return 0.0, 0.0, 0.0
        def ema(data, period):
            k = 2 / (period + 1)
            result = [data[0]]
            for p in data[1:]:
                result.append(p * k + result[-1] * (1 - k))
            return result
        ema_fast = ema(prices, fast)
        ema_slow = ema(prices, slow)
        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        signal_line = ema(macd_line, signal)
        histogram = macd_line[-1] - signal_line[-1]
        return round(macd_line[-1], 2), round(signal_line[-1], 2), round(histogram, 2)
    except: return 0.0, 0.0, 0.0

# ── UPGRADED: BOLLINGER BANDS ────────────────────────────────────────────────
def get_bollinger(prices, period=20, std_dev=2):
    """Bollinger Bands. Returns (upper, middle, lower, %B position 0-1)"""
    try:
        if len(prices) < period:
            return 0.0, 0.0, 0.0, 0.5
        recent = prices[-period:]
        mid = sum(recent) / period
        variance = sum((p - mid) ** 2 for p in recent) / period
        std = math.sqrt(variance)
        upper = mid + std_dev * std
        lower = mid - std_dev * std
        current = prices[-1]
        pct_b = (current - lower) / (upper - lower) if upper != lower else 0.5
        return round(upper, 2), round(mid, 2), round(lower, 2), round(pct_b, 3)
    except: return 0.0, 0.0, 0.0, 0.5

# ── UPGRADED: FEAR & GREED ───────────────────────────────────────────────────
def get_fear_greed():
    """Crypto Fear & Greed Index from alternative.me — 0=extreme fear, 100=extreme greed"""
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        data = r.json()["data"][0]
        value = int(data["value"])
        label = data["value_classification"]
        return value, label
    except: return 50, "Neutral"

# ── UPGRADED: BINANCE VOLUME SPIKE ───────────────────────────────────────────
def get_volume_spike_binance():
    """Compare last 5min BTC volume vs average — sharp real-time signal"""
    try:
        r = requests.get("https://api.binance.us/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "5m", "limit": 20}, timeout=8)
        candles = r.json()
        volumes = [float(c[5]) for c in candles]
        current = volumes[-1]
        avg = sum(volumes[:-1]) / len(volumes[:-1])
        ratio = round(current / avg, 2) if avg > 0 else 1.0
        spike = ratio > 2.0
        return ratio, spike
    except: return 1.0, False

# ── UPGRADED: BINANCE PRICES FOR INDICATORS ──────────────────────────────────
def get_binance_prices_for_indicators(symbol="BTCUSDT", interval="1h", limit=50):
    """Pull clean OHLCV from Binance for indicator calculations"""
    try:
        r = requests.get("https://api.binance.us/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=10)
        candles = r.json()
        closes = [float(c[4]) for c in candles]
        highs  = [float(c[2]) for c in candles]
        lows   = [float(c[3]) for c in candles]
        vols   = [float(c[5]) for c in candles]
        return closes, highs, lows, vols
    except: return [], [], [], []

# ── UPGRADED: 4H MOMENTUM ────────────────────────────────────────────────────
def get_4h_momentum():
    """4-hour BTC momentum — the big picture trend Jarvis was missing"""
    try:
        r = requests.get("https://api.binance.us/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "4h", "limit": 6}, timeout=10)
        candles = r.json()
        closes = [float(c[4]) for c in candles]
        if len(closes) < 2: return 0.0, "NEUTRAL"
        move = (closes[-1] - closes[0]) / closes[0] * 100
        if move > 2.0:   trend = "STRONG_UP"
        elif move > 0.5: trend = "WEAK_UP"
        elif move < -2.0: trend = "STRONG_DOWN"
        elif move < -0.5: trend = "WEAK_DOWN"
        else: trend = "NEUTRAL"
        return round(move, 2), trend
    except: return 0.0, "NEUTRAL"

# ── SHORT TERM PRICE ACTION (1m + 5m) ────────────────────────────────────────
def get_short_term_trend():
    """
    Pulls 1m and 5m candles from Binance.
    Detects: lower highs, EMA9 position, wick rejections, micro trend slope.
    Returns a plain-English summary for Claude.
    """
    try:
        # Binance klines return a list of candle rows ([open_time, o, h, l, c, ...]).
        # When Binance is geo-blocked/rate-limited it returns a dict instead
        # (e.g. {"code":..,"msg":..}); iterating that yields short string keys and
        # indexing c[4] throws "string index out of range". Validate the shape.
        def _valid_klines(data, need):
            return isinstance(data, list) and len(data) >= need and isinstance(data[0], list)

        # 1-minute candles — last 20
        r1 = requests.get("https://api.binance.us/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "1m", "limit": 20}, timeout=8)
        c1 = r1.json()
        # 5-minute candles — last 12
        r5 = requests.get("https://api.binance.us/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "5m", "limit": 12}, timeout=8)
        c5 = r5.json()

        if not (_valid_klines(c1, 10) and _valid_klines(c5, 4)):
            log.info("Short term trend: Binance klines unavailable (non-candle response)")
            return "Short term data unavailable"

        closes_1m = [float(c[4]) for c in c1]
        highs_1m  = [float(c[2]) for c in c1]
        lows_1m   = [float(c[3]) for c in c1]
        opens_1m  = [float(c[1]) for c in c1]

        closes_5m = [float(c[4]) for c in c5]
        highs_5m  = [float(c[2]) for c in c5]
        lows_5m   = [float(c[3]) for c in c5]

        # EMA9 on 1m closes
        def ema(data, period):
            k = 2 / (period + 1)
            e = [data[0]]
            for p in data[1:]: e.append(p * k + e[-1] * (1 - k))
            return e
        ema9_1m = ema(closes_1m, 9)
        current = closes_1m[-1]
        ema9_now = ema9_1m[-1]
        price_vs_ema = "ABOVE EMA9" if current > ema9_now else "BELOW EMA9"

        # Lower highs detection on 1m (last 5 candle highs)
        recent_highs = highs_1m[-5:]
        lower_highs = all(recent_highs[i] > recent_highs[i+1] for i in range(len(recent_highs)-1))
        higher_lows = all(lows_1m[-5:][i] < lows_1m[-5:][i+1] for i in range(len(lows_1m[-5:])-1))

        # Wick rejection — last 3 candles
        rejections = []
        for i in range(-3, 0):
            o, c, h, l = opens_1m[i], closes_1m[i], highs_1m[i], lows_1m[i]
            body = abs(c - o)
            upper_wick = h - max(o, c)
            lower_wick = min(o, c) - l
            if upper_wick > body * 1.5:
                rejections.append("upper wick rejection (bearish)")
            elif lower_wick > body * 1.5:
                rejections.append("lower wick rejection (bullish)")

        # 1m micro trend — slope of last 10 closes
        first5_avg  = sum(closes_1m[:5]) / 5
        last5_avg   = sum(closes_1m[-5:]) / 5
        slope = last5_avg - first5_avg
        if slope > 50:    micro_trend = "STRONG UP"
        elif slope > 15:  micro_trend = "WEAK UP"
        elif slope < -50: micro_trend = "STRONG DOWN"
        elif slope < -15: micro_trend = "WEAK DOWN"
        else:             micro_trend = "FLAT"

        # 5m trend — same logic
        f5 = sum(closes_5m[:3]) / 3
        l5 = sum(closes_5m[-3:]) / 3
        slope5 = l5 - f5
        if slope5 > 100:    trend5 = "STRONG UP"
        elif slope5 > 30:   trend5 = "WEAK UP"
        elif slope5 < -100: trend5 = "STRONG DOWN"
        elif slope5 < -30:  trend5 = "WEAK DOWN"
        else:               trend5 = "FLAT"

        # 5m lower highs
        h5_recent = highs_5m[-4:]
        lower_highs_5m = all(h5_recent[i] > h5_recent[i+1] for i in range(len(h5_recent)-1))

        lines = [
            f"── SHORT TERM PRICE ACTION ──",
            f"1m micro trend: {micro_trend} (slope ${slope:+.0f})",
            f"5m trend: {trend5} (slope ${slope5:+.0f})",
            f"EMA9(1m): ${ema9_now:,.0f} — price is {price_vs_ema}",
            f"1m Lower highs: {'YES — bearish structure' if lower_highs else 'NO'}",
            f"1m Higher lows: {'YES — bullish structure' if higher_lows else 'NO'}",
            f"5m Lower highs: {'YES — bearish structure' if lower_highs_5m else 'NO'}",
        ]
        if rejections:
            lines.append(f"Wick rejections (last 3 candles): {', '.join(rejections)}")
        lines.append(f"Current: ${current:,.0f} | EMA9: ${ema9_now:,.0f} | Diff: ${current-ema9_now:+.0f}")

        return "\n".join(lines)
    except Exception as e:
        log.error(f"Short term trend: {e}")
        return "Short term data unavailable"

# ── UPGRADED: PATTERN MEMORY ─────────────────────────────────────────────────
def load_patterns():
    if os.path.exists(PATTERN_FILE):
        try: return json.load(open(PATTERN_FILE))
        except: pass
    return {"patterns": [], "fingerprints": {}}

def save_patterns(p):
    with open(PATTERN_FILE, "w") as f: json.dump(p, f, indent=2)

def build_signal_fingerprint(rsi, macd_hist, pct_b, fear_greed, momentum_4h, hour_edt):
    """Create a hashable fingerprint of current market conditions"""
    rsi_zone = "oversold" if rsi < 35 else "overbought" if rsi > 65 else "neutral"
    macd_zone = "bullish" if macd_hist > 0 else "bearish"
    bb_zone = "bottom" if pct_b < 0.2 else "top" if pct_b > 0.8 else "mid"
    fg_zone = "fear" if fear_greed < 35 else "greed" if fear_greed > 65 else "neutral"
    m4h_zone = "up" if "UP" in momentum_4h else "down" if "DOWN" in momentum_4h else "flat"
    hour_zone = "asia" if 0 <= hour_edt <= 7 else "london" if 8 <= hour_edt <= 12 else "nyc" if 13 <= hour_edt <= 20 else "late"
    return f"{rsi_zone}|{macd_zone}|{bb_zone}|{fg_zone}|{m4h_zone}|{hour_zone}"

def get_pattern_modifier(fingerprint):
    """Look up this exact market fingerprint in pattern history — returns win rate modifier"""
    patterns = load_patterns()
    fp_data = patterns["fingerprints"].get(fingerprint, {})
    total = fp_data.get("total", 0)
    wins  = fp_data.get("wins", 0)
    if total < 3:
        return 0.0, f"New pattern ({total} samples)"
    wr = wins / total
    modifier = (wr - 0.5) * 20  # -10 to +10 confidence adjustment
    return round(modifier, 1), f"Pattern WR:{round(wr*100)}% ({wins}/{total})"

def update_pattern_memory(fingerprint, correct):
    """After grading a prediction, update the pattern fingerprint record"""
    patterns = load_patterns()
    if fingerprint not in patterns["fingerprints"]:
        patterns["fingerprints"][fingerprint] = {"total": 0, "wins": 0}
    patterns["fingerprints"][fingerprint]["total"] += 1
    if correct:
        patterns["fingerprints"][fingerprint]["wins"] += 1
    # Also log to raw pattern list
    patterns["patterns"].append({
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "fingerprint": fingerprint,
        "correct": correct
    })
    patterns["patterns"] = patterns["patterns"][-500:]  # keep last 500
    save_patterns(patterns)

# ── UPGRADED: TIME BIAS ───────────────────────────────────────────────────────
def get_time_bias():
    """Which hours does Jarvis win most? Pull from brain and return bias."""
    try:
        master = load_master()
        hour_stats = master["stats"].get("best_hours", {})
        edt_hour = (datetime.now(timezone.utc).hour - 4) % 24
        h_str = str(edt_hour).zfill(2)
        h_data = hour_stats.get(h_str, {})
        total = h_data.get("total", 0)
        wins  = h_data.get("wins", 0)
        if total < 3:
            return "no bias data yet", 0.0
        wr = wins / total
        bias = f"Hour {edt_hour}:00 EDT — {round(wr*100)}% WR ({wins}/{total})"
        return bias, wr
    except: return "unknown", 0.5

def get_dow_info():
    """Day of week — crypto has strong weekly patterns"""
    dow = datetime.now(timezone.utc).weekday()
    names = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    patterns = {
        0: "Monday — often weak open after weekend",
        1: "Tuesday — historically strong BTC day",
        2: "Wednesday — mid-week, neutral",
        3: "Thursday — watch for options expiry moves",
        4: "Friday — often volatile into weekend",
        5: "Saturday — lower volume, wider spreads",
        6: "Sunday — weekend low volume"
    }
    return names[dow], patterns.get(dow, "")

# ── UPGRADED: KELLY BET SIZING v2 ────────────────────────────────────────────
def kelly_bet_size(confidence_pct, kalshi_price, bankroll=500, pattern_modifier=0.0):
    """
    Full Kelly with Kalshi market price as the true odds.
    confidence_pct = Jarvis estimated probability (0-100)
    kalshi_price   = market YES price (0-1), e.g. 0.42
    pattern_modifier = historical edge adjustment from pattern memory
    """
    try:
        # Adjust confidence by pattern memory
        adjusted_conf = min(95, max(5, confidence_pct + pattern_modifier))
        p = adjusted_conf / 100
        q = 1 - p
        # Kalshi pays (1/price - 1) on a win
        if kalshi_price <= 0 or kalshi_price >= 1:
            return 25.0
        b = (1 / kalshi_price) - 1  # net odds on a YES win
        kelly_fraction = (p * b - q) / b
        # Use quarter-Kelly for safety
        kelly_fraction = max(0, min(kelly_fraction * 0.25, 0.15))
        bet = round(kelly_fraction * bankroll, 0)
        return max(10.0, bet)
    except: return 25.0

# ── UPGRADED: ORACLE CONTEXT BUILDER ─────────────────────────────────────────
def build_oracle_context(price, rsi, momentum, funding, vol_ratio):
    """
    Build the richest possible context block for Claude.
    Pulls ALL indicators and pattern memory into one structured brief.
    """
    # Pull all indicators
    closes, highs, lows, vols = get_binance_prices_for_indicators()
    macd_line, macd_sig, macd_hist = get_macd(closes) if closes else (0, 0, 0)
    bb_upper, bb_mid, bb_lower, pct_b = get_bollinger(closes) if closes else (0, 0, 0, 0.5)
    fear_val, fear_label = get_fear_greed()
    vol_spike_ratio, vol_spike = get_volume_spike_binance()
    move_4h, trend_4h = get_4h_momentum()
    hour_edt = (datetime.now(timezone.utc).hour - 4) % 24
    time_bias, time_wr = get_time_bias()
    dow_name, dow_pattern = get_dow_info()

    # Pattern fingerprint
    fingerprint = build_signal_fingerprint(rsi, macd_hist, pct_b, fear_val, trend_4h, hour_edt)
    pat_modifier, pat_note = get_pattern_modifier(fingerprint)

    # ATR
    atr = get_atr()

    # Regime
    mem = load_memory()
    recent_prices = mem["prices"][-12:] if mem.get("prices") else []
    regime = detect_regime(recent_prices)

    # Support/Resistance
    sr = get_support_resistance()
    sr_line = f"S:${sr.get('support',0):,.0f} / R:${sr.get('resistance',0):,.0f}" if sr else "N/A"

    # Short term price action — 1m + 5m candles
    short_term = get_short_term_trend()

    # Build context string
    lines = [
        f"=== JARVIS ORACLE BRIEF ===",
        f"BTC: ${price:,.2f} | ATR: ${atr:,.0f}",
        f"Regime: {regime} | 4H Trend: {trend_4h} ({move_4h:+.2f}%)",
        f"",
        f"── MOMENTUM ──",
        f"RSI: {rsi} | 1h: {momentum.get('1h',0):+.2f}% | 24h: {momentum.get('24h',0):+.2f}%",
        f"MACD: {macd_line} | Signal: {macd_sig} | Hist: {macd_hist} ({'BULL' if macd_hist > 0 else 'BEAR'})",
        f"Bollinger: Upper ${bb_upper:,.0f} / Mid ${bb_mid:,.0f} / Lower ${bb_lower:,.0f}",
        f"BB %B: {pct_b:.2f} ({'NEAR TOP' if pct_b > 0.8 else 'NEAR BOTTOM' if pct_b < 0.2 else 'MID RANGE'})",
        f"",
        short_term,
        f"",
        f"── SENTIMENT ──",
        f"Fear & Greed: {fear_val}/100 — {fear_label}",
        f"Funding Rate: {funding:.4f} ({'bearish' if funding > 0 else 'bullish'} signal)",
        f"Volume Ratio: {vol_ratio}x | 5min Binance spike: {vol_spike_ratio}x {'⚡SPIKE' if vol_spike else ''}",
        f"",
        f"── LEVELS ──",
        f"Support/Resistance (7d): {sr_line}",
        f"",
        f"── PATTERN MEMORY ──",
        f"Fingerprint: {fingerprint}",
        f"Historical edge: {pat_note} (modifier: {pat_modifier:+.1f}%)",
        f"",
        f"── TIME CONTEXT ──",
        f"{dow_name} — {dow_pattern}",
        f"Hour bias: {time_bias}",
        f"=========================",
    ]
    return "\n".join(lines), fingerprint, pat_modifier

# ── LEGACY HELPERS ────────────────────────────────────────────────────────────
def get_atr(hours=24):
    try:
        r = requests.get('https://api.kraken.com/0/public/OHLC',
            params={'pair':'XBTUSD','interval':60}, timeout=8)
        data = r.json()['result']['XXBTZUSD']
        highs = [float(d[2]) for d in data[-hours:]]
        lows  = [float(d[3]) for d in data[-hours:]]
        atr = round(sum(h-l for h,l in zip(highs,lows))/len(highs), 2)
        return atr
    except: return 400.0

def get_btc_dominance():
    try:
        r = requests.get('https://api.coingecko.com/api/v3/global', timeout=8)
        return round(float(r.json()['data']['market_cap_percentage']['btc']), 1)
    except: return 50.0

def get_kalshi_intelligence(markets):
    if not markets: return {}
    try:
        total_vol = sum(float(m.get('volume_fp','0') or 0) for m in markets)
        total_oi  = sum(float(m.get('open_interest_fp','0') or 0) for m in markets)
        total_liq = sum(float(m.get('liquidity_dollars','0') or 0) for m in markets)
        yes_bid_size = sum(float(m.get('yes_bid_size_fp','0') or 0) for m in markets)
        yes_ask_size = sum(float(m.get('yes_ask_size_fp','0') or 0) for m in markets)
        imbalance = round((yes_bid_size - yes_ask_size) / max(yes_bid_size + yes_ask_size, 1) * 100, 1)
        bias = "BUYERS_DOMINANT" if imbalance > 10 else "SELLERS_DOMINANT" if imbalance < -10 else "BALANCED"
        return {"volume": round(total_vol,0), "open_interest": round(total_oi,0),
                "liquidity": round(total_liq,2), "imbalance": imbalance, "bias": bias}
    except: return {}

def get_session_time():
    edt = (datetime.now(timezone.utc).hour - 4) % 24
    if edt in range(9, 10):  return "NYSE_OPEN", "High volatility — market open"
    if edt in range(13, 14): return "OPTIONS_EXPIRY", "Options pinning possible"
    if edt in range(15, 16): return "CLOSE", "End of day positioning"
    if edt in range(20, 22): return "ASIA_OPEN", "Asia session starting"
    if edt in range(0, 4):   return "ASIA_PEAK", "Peak Asia volume"
    return "NORMAL", f"EDT {edt}:00"

def get_pred_streak():
    try:
        if SHARED_MEM:
            stats = jmem.get_pred_accuracy()
            return stats
    except: pass
    return {}

def get_rsi(symbol, period=14):
    try:
        coin = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}.get(symbol)
        r = requests.get(f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart",
            params={"vs_currency": "usd", "days": "2", "interval": "hourly"}, timeout=12)
        prices = [p[1] for p in r.json()["prices"]][-period-1:]
        gains, losses = [], []
        for i in range(1, len(prices)):
            d = prices[i] - prices[i-1]
            (gains if d > 0 else losses).append(abs(d))
        if not gains: return 50.0
        ag = sum(gains)/len(gains)
        al = sum(losses)/len(losses) if losses else 0.001
        return round(100 - (100/(1+ag/al)), 1)
    except: return 50.0

def get_momentum(symbol):
    try:
        coin = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}.get(symbol)
        r = requests.get(f"https://api.coingecko.com/api/v3/coins/{coin}",
            params={"localization":"false","tickers":"false","community_data":"false","developer_data":"false"},
            timeout=10)
        data = r.json()["market_data"]
        return {
            "1h":  round(data["price_change_percentage_1h_in_currency"]["usd"], 2),
            "24h": round(data["price_change_percentage_24h"], 2),
            "7d":  round(data["price_change_percentage_7d"], 2),
        }
    except: return {"1h": 0.0, "24h": 0.0, "7d": 0.0}

# ── KALSHI ────────────────────────────────────────────────────────────────────
def get_kalshi_markets(ref_price=75000):
    try:
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%y%b%d").upper()
        all_hours = list(range((now.hour-4)%24, 24)) + list(range(0, (now.hour-4)%24))
        for edt_hour in all_hours:
            ticker = f"KXBTCD-{date_str}{edt_hour:02d}"
            r = requests.get(
                f"https://api.elections.kalshi.com/trade-api/v2/events/{ticker}", timeout=8)
            if r.status_code == 200:
                markets = r.json().get("markets", [])
                active = []
                for m in markets:
                    strike = float(m.get("floor_strike", 0) or 0)
                    yes = float(m.get("yes_bid_dollars", "0") or 0)
                    no  = float(m.get("no_bid_dollars",  "0") or 0)
                    if (yes > 0.02 or no > 0.05) and abs(strike - ref_price) < 500:
                        active.append({"strike": strike, "yes": yes, "no": no,
                                       "ticker": m.get("ticker",""), "close": m.get("close_time","")[:16]})
                if active:
                    active.sort(key=lambda x: abs(x["yes"] - 0.50))
                    return active, ticker
        return [], ""
    except Exception as e:
        log.error(f"Kalshi: {e}")
        return [], ""

def get_btc_price_from_kalshi():
    # Returns REAL BTC spot — ONE committed anchor per call.
    # Do NOT derive "price" from a Kalshi strike: the old code returned the
    # floor_strike of whichever market was closest to 50/50, which is a betting
    # TARGET, not a price. That strike drifts as the order book moves, so every
    # call returned a different "price"/target and the prediction engine
    # goal-posted (conflicting targets + flipped YES/NO direction). Kalshi
    # strikes are targets only; spot is the price. Coinbase → Kraken → CoinGecko.
    try:
        r = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=8)
        price = float(r.json()["data"]["amount"])
        log.info(f"BTC price Coinbase: ${price:,.0f}")
        return price
    except: pass
    try:
        r = requests.get("https://api.kraken.com/0/public/Ticker?pair=XBTUSD", timeout=8)
        price = float(r.json()["result"]["XXBTZUSD"]["c"][0])
        log.info(f"BTC price Kraken: ${price:,.0f}")
        return price
    except: pass
    try:
        price = get_coingecko_price("BTC")
        if price:
            log.info(f"BTC price CoinGecko: ${price:,.0f}")
            return price
    except: pass
    return None

# ── MASTER BRAIN ─────────────────────────────────────────────────────────────
def load_master():
    if os.path.exists(MASTER_FILE):
        try: return json.load(open(MASTER_FILE))
        except: pass
    return {
        "trades": [], "preds": [], "daily": {},
        "stats": {
            "total_trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0,
            "pred_total": 0, "pred_correct": 0,
            "best_rsi_zones": {}, "best_hours": {}, "best_funding": {},
            "daily_loss": 0.0, "daily_start_equity": 0.0,
            "consecutive_losses": 0, "size_multiplier": 1.0,
        },
        "rules": {
            "min_volume_ratio": 1.0, "max_funding_rate": 0.001,
            "banned_assets": ["AVAX"], "avoid_hours_edt": [],
        }
    }

def save_master(m):
    with open(MASTER_FILE, "w") as f: json.dump(m, f, indent=2)

# ── MEMORY (BTC) ──────────────────────────────────────────────────────────────
def load_memory():
    if os.path.exists(MEMORY_FILE):
        try: return json.load(open(MEMORY_FILE))
        except: pass
    return {"prices": [], "predictions": [], "daily_summaries": {}, "stats": {
        "total_predictions":0,"correct_target":0,"correct_range":0,
        "total_bet_yes":0,"correct_bet_yes":0,"total_bet_no":0,"correct_bet_no":0,
        "best_streak":0,"current_streak":0,"avg_error_dollars":0.0}}

def save_memory(mem):
    with open(MEMORY_FILE, "w") as f: json.dump(mem, f, indent=2)

def log_price_tick(price, rsi, momentum):
    if SHARED_MEM:
        try: jmem.log_price("BTC", price, rsi, momentum.get("1h",0), momentum.get("24h",0))
        except: pass
    mem = load_memory()
    now = datetime.now(timezone.utc)
    entry = {"ts": now.strftime("%Y-%m-%d %H:%M"), "date": now.strftime("%Y-%m-%d"),
             "hour": now.hour, "price": round(price,2), "rsi": rsi,
             "1h": momentum.get("1h",0), "24h": momentum.get("24h",0)}
    mem["prices"].append(entry)
    date = entry["date"]
    ds = mem["daily_summaries"].setdefault(date, {"open":price,"high":price,"low":price,"close":price,"ticks":0,"avg_rsi":0.0})
    ds["high"] = max(ds["high"], price)
    ds["low"]  = min(ds["low"],  price)
    ds["close"] = price
    ds["ticks"] += 1
    ds["avg_rsi"] = round((ds["avg_rsi"]*(ds["ticks"]-1)+rsi)/ds["ticks"],1)
    save_memory(mem)

def build_memory_context():
    if SHARED_MEM:
        try: return jmem.build_context("BTC")
        except: pass
    mem = load_memory()
    prices = mem.get("prices", [])
    preds  = mem.get("predictions", [])
    s      = mem.get("stats", {})
    lines  = []
    graded = len([p for p in preds if p.get("graded")])
    if graded > 0:
        t_acc = round(s["correct_target"]/graded*100)
        lines.append(f"Prediction accuracy: {t_acc}% ({graded} graded)")
        lines.append(f"Streak: {s['current_streak']} | Avg error: ${s['avg_error_dollars']}")
    if prices:
        recent = prices[-12:]
        p_vals = [p["price"] for p in recent]
        lines.append(f"12h range: ${min(p_vals):,.0f}-${max(p_vals):,.0f}")
        lines.append(f"Last price: ${recent[-1]['price']:,.0f} RSI:{recent[-1]['rsi']}")
        ticks = " ".join([f"${p['price']:,.0f}" for p in recent[-6:]])
        lines.append(f"Recent: {ticks}")
    if len(prices) >= 6:
        rsi_vals = [p["rsi"] for p in prices[-6:]]
        trend = "rising" if rsi_vals[-1] > rsi_vals[0] else "falling"
        lines.append(f"RSI trend (6h): {' '.join(map(str,rsi_vals))} ({trend})")
    return "\n".join(lines)

# ── REGIME DETECTION ──────────────────────────────────────────────────────────
def detect_regime(prices_recent):
    if len(prices_recent) < 6: return "UNKNOWN"
    vals = [p["price"] for p in prices_recent]
    high, low = max(vals), min(vals)
    first, last = vals[0], vals[-1]
    range_pct = (high-low)/first if first > 0 else 0
    move_pct  = (last-first)/first if first > 0 else 0
    if range_pct < 0.012: return "RANGING"
    elif move_pct > 0.02:  return "TRENDING_UP"
    elif move_pct < -0.02: return "TRENDING_DOWN"
    else: return "VOLATILE"

def get_trade_mode(regime, rsi, funding_rate, volume_ratio):
    if regime == "TRENDING_DOWN" and funding_rate < 0:
        return "SKIP", "Downtrend + negative funding"
    if volume_ratio < 0.7:
        return "SKIP", f"Low volume {volume_ratio}x"
    if funding_rate > 0.001:
        return "SKIP", f"Funding too high {funding_rate:.4f} — longs overextended"
    if regime == "RANGING":
        if rsi < 30: return "SCALP_LONG", "Oversold in range"
        if rsi > 70: return "SCALP_SHORT", "Overbought in range"
        return "WAIT", "Ranging — wait for RSI extreme"
    if regime == "TRENDING_UP":
        if rsi < 40: return "SCALP_LONG", "Pullback in uptrend"
        return "WAIT", "Uptrend — wait for pullback"
    return "WAIT", "No clear setup"

# ── SCALP ENGINE ──────────────────────────────────────────────────────────────
def check_scalp_exits(master, positions):
    closed = []
    for pos in positions:
        symbol = pos.get("symbol","")
        if "/" not in symbol: continue
        current = float(pos.get("current_price",0))
        entry   = float(pos.get("avg_entry_price",0))
        side    = pos.get("side","long")
        if entry <= 0: continue
        pnl_pct = (current-entry)/entry if side=="long" else (entry-current)/entry
        pnl_usd = float(pos.get("unrealized_pl",0))
        reason = None
        if pnl_pct >= SCALP_TARGET:  reason = f"Profit target +{pnl_pct*100:.2f}%"
        elif pnl_pct <= -SCALP_STOP: reason = f"Stop loss {pnl_pct*100:.2f}%"
        if reason:
            result = close_position(symbol)
            if result:
                log.info(f"Closed {symbol}: {reason} ${pnl_usd:+.2f}")
                master["stats"]["total_pnl"] = round(master["stats"].get("total_pnl",0)+pnl_usd,2)
                master["stats"]["daily_loss"] = round(master["stats"].get("daily_loss",0)+pnl_usd,2)
                if pnl_usd > 0:
                    master["stats"]["wins"] += 1
                    master["stats"]["consecutive_losses"] = 0
                    master["stats"]["size_multiplier"] = min(2.0, master["stats"]["size_multiplier"]+0.05)
                else:
                    master["stats"]["losses"] += 1
                    master["stats"]["consecutive_losses"] += 1
                    if master["stats"]["consecutive_losses"] >= 3:
                        master["stats"]["size_multiplier"] = max(0.3, master["stats"]["size_multiplier"]-0.2)
                save_master(master)
                emoji = "✅" if pnl_usd > 0 else "🔴"
                tg(f"{emoji} {symbol} closed\n{reason}\nP&L: ${pnl_usd:+.2f}\nTotal: ${master['stats']['total_pnl']:+.2f}")
                closed.append(symbol)
    return closed

def attempt_scalp(master, symbol, mode, price, rsi, funding_rate, volume_ratio):
    if symbol in master["rules"]["banned_assets"]: return
    positions = get_positions()
    crypto_positions = [p for p in positions if "/" in p.get("symbol","")]
    if len(crypto_positions) >= MAX_POSITIONS: return
    if master["stats"]["daily_loss"] <= -DAILY_LOSS_MAX:
        log.info("Daily loss limit — no new trades")
        return
    edt_hour = (datetime.now(timezone.utc).hour - 4) % 24
    if edt_hour < TRADE_START_EDT or edt_hour > TRADE_END_EDT:
        log.info(f"Outside trading hours EDT {edt_hour}:00")
        return
    size = SCALP_SIZE * master["stats"]["size_multiplier"]
    side = "buy" if "LONG" in mode else "sell"
    crypto_map = {"BTC": "BTCUSD", "ETH": "ETHUSD", "SOL": "SOLUSD"}
    alpaca_sym = crypto_map.get(symbol, symbol+"USD")
    qty = round(size / price, 6)
    result = place_order(alpaca_sym, qty, side)
    order_status = (result or {}).get("status")
    # Count only orders Alpaca accepted into a fillable state; never count rejected/None.
    if result and order_status not in ("rejected", "canceled", "expired", None):
        master["stats"]["total_trades"] += 1
        trade = {
            "id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "symbol": symbol, "side": side, "price": price,
            "qty": qty, "size": size, "rsi": rsi,
            "funding": funding_rate, "volume": volume_ratio,
            "regime": detect_regime(load_memory()["prices"][-12:] if load_memory()["prices"] else []),
            "mode": mode
        }
        master["trades"].append(trade)
        master["trades"] = master["trades"][-50:]
        save_master(master)
        log.info(f"Opened {side} {symbol} @ ${price:,.0f} qty={qty:.6f} size=${size:.0f}")
        tg(f"🎯 {side.upper()} {symbol}\n@ ${price:,.0f}\nRSI:{rsi} Vol:{volume_ratio}x Fund:{funding_rate:.4f}\n{mode}")

# ── HOURLY PREDICTION (UPGRADED) ──────────────────────────────────────────────
def run_hourly_prediction(price, rsi, momentum):
    """Send hourly BTC Kalshi prediction — now with full Oracle context"""
    log_price_tick(price, rsi, momentum)
    markets, event = get_kalshi_markets(price)
    if not markets:
        log.info("No Kalshi markets for prediction")
        return

    best = min(markets, key=lambda m: abs(m["yes"]-0.50))
    target = best["strike"]
    mkt_lines = "\n".join([f"${m['strike']:,.0f} YES:{m['yes']:.2f} NO:{m['no']:.2f}" for m in markets[:5]])

    funding = get_funding_rate("BTC")
    vol = get_volume_ratio("BTC")
    now_edt = (datetime.now(timezone.utc).hour - 4) % 24
    next_edt = (now_edt + 1) % 24

    # Build Oracle context — the full picture
    oracle_ctx, fingerprint, pat_modifier = build_oracle_context(price, rsi, momentum, funding, vol)
    mem_ctx = build_memory_context()

    # Kelly sizing based on market price
    suggested_bet = kelly_bet_size(60, best["yes"], bankroll=500, pattern_modifier=pat_modifier)

    prompt = f"""You are Jarvis, elite crypto trading AI with pattern memory and deep technical analysis.

{oracle_ctx}

LIVE KALSHI MARKETS (target hour: {next_edt}:00 EDT):
{mkt_lines}

YOUR PREDICTION HISTORY:
{mem_ctx}

TASK: Will BTC be ABOVE ${target:,.0f} by {next_edt}:00 EDT?

Consider ALL signals: MACD direction, Bollinger position, Fear/Greed, 4H trend, pattern memory modifier ({pat_modifier:+.1f}%), time bias.

BET YES if >65% confident above target. BET NO if <35% confident above. SKIP if uncertain.
Suggested Kelly bet size: ${suggested_bet:.0f}

Submit your call via the submit_oracle_call tool. Do not write any prose outside the tool call."""

    data = claude_tool(prompt, ORACLE_TOOL, max_tokens=400)
    if not data:
        tg("Claude unavailable", TG_PRED)
        return

    try:
        prob = f"{int(round(float(data.get('target_prob', 0))))}%"
    except (ValueError, TypeError):
        prob = "?"
    pp      = data.get("predicted_price")
    pred_px = f"{float(pp):.0f}" if isinstance(pp, (int, float)) else "?"   # plain int string for the grader
    bet     = str(data.get("bet", "SKIP")).strip().upper()
    reason  = str(data.get("reason", "")).strip()

    # Edge calculation
    try:
        prob_num = float(prob.replace("%",""))/100
        kalshi_yes = best["yes"]
        edge = abs(prob_num - kalshi_yes)
        edge_str = f"+{round(edge*100)}% EDGE" if edge > 0.10 else "thin edge"
    except: edge_str = ""

    bet_line = {"YES":"🟢 BET YES","NO":"🔴 BET NO","SKIP":"⚪ SKIP"}.get(bet,"⚪ SKIP")
    sr = get_support_resistance()
    sr_line = f"S:${sr['support']:,.0f} R:${sr['resistance']:,.0f}" if sr else ""

    # Pull extra indicators for display
    closes, _, _, _ = get_binance_prices_for_indicators()
    _, _, macd_hist = get_macd(closes) if closes else (0, 0, 0)
    _, _, _, pct_b  = get_bollinger(closes) if closes else (0, 0, 0, 0.5)
    fear_val, fear_label = get_fear_greed()
    _, trend_4h = get_4h_momentum()
    _, pat_note = get_pattern_modifier(fingerprint)

    msg = f"""🤖 JARVIS ORACLE
{'='*24}
BTC @ ${price:,.2f}
Target: ${target:,.0f} by {next_edt}:00 EDT
{'='*24}
{bet_line}
Prob: {prob} | Guess: ${pred_px}
{edge_str} | Kelly: ${suggested_bet:.0f}
{'='*24}
RSI:{rsi} MACD:{'↑' if macd_hist>0 else '↓'}{macd_hist} BB:{pct_b:.2f}
F&G:{fear_val} {fear_label} | 4H:{trend_4h}
Fund:{funding:.4f} Vol:{vol}x
{sr_line}
{'='*24}
Pattern: {pat_note}
{'='*24}
{reason}"""

    # ── Alert gate ── only push the Telegram alert in proven edge hours AND when
    # the 4h regime isn't flat/ranging. Suppressed predictions are still logged
    # (paper-only) just below — we only skip the alert, not the record.
    regime_zone = fingerprint.split("|")[4] if fingerprint and len(fingerprint.split("|")) >= 5 else "?"
    now_edt = (datetime.now(timezone.utc).hour - 4) % 24
    alert_ok = (now_edt in EDGE_HOURS) and regime_zone != "flat"
    if alert_ok:
        tg(msg, TG_PRED)
    else:
        why = "non-edge hour" if now_edt not in EDGE_HOURS else "flat regime"
        log.info(f"Alert suppressed ({why}: hr={now_edt} EDT, regime={regime_zone}) — paper-only")

    # Task 8: market state at decision time
    _yes_p  = best.get("yes", 0.0)
    _no_p   = best.get("no",  0.0)
    _spread = round(abs(_yes_p - _no_p), 4)
    try:
        _edge_pct = round(abs(float(prob.replace("%", "")) / 100 - _yes_p) * 100, 1)
    except Exception:
        _edge_pct = None

    snap = {
        "id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M"),
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "symbol": "BTC", "price_at_pred": round(price, 2),
        "target": target, "target_prob": prob,
        "predicted_price": pred_px, "bet": bet, "reason": reason,
        "fingerprint": fingerprint,
        "kalshi_yes_price": _yes_p, "spread": _spread, "edge_pct": _edge_pct,  # Task 8
        "actual_price": None, "target_hit": None, "graded": False,
        "source": "auto", "schema_version": SCHEMA_VERSION,  # Task 10
    }

    # Log to memory with fingerprint
    mem = load_memory()
    mem["predictions"].append(snap)
    mem["stats"]["total_predictions"] = len(mem["predictions"])
    save_memory(mem)

    # Task 6: log skips to kalshi_brain with cap+rotation
    if bet == "SKIP":
        kb = load_kalshi_brain()
        if "skips" not in kb: kb["skips"] = []
        skip_rec = dict(snap)
        skip_rec["skip_reason"] = reason
        kb["skips"].append(skip_rec)
        if len(kb["skips"]) > SKIP_CAP:
            overflow = kb["skips"][:-SKIP_CAP]
            kb["skips"] = kb["skips"][-SKIP_CAP:]
            try:
                archive = []
                if os.path.exists(SKIPS_ARCHIVE_FILE):
                    with open(SKIPS_ARCHIVE_FILE) as f: archive = json.load(f)
                archive.extend(overflow)
                with open(SKIPS_ARCHIVE_FILE, "w") as f: json.dump(archive, f, indent=2)
            except Exception as e:
                log.warning(f"skips rotate: {e}")
        save_kalshi_brain(kb)

    log.info(f"Prediction {'ALERTED' if alert_ok else 'PAPER-ONLY'} BTC target={target} bet={bet} "
             f"prob={prob} hr={now_edt}EDT regime={regime_zone} fingerprint={fingerprint}")

def get_support_resistance():
    try:
        mem = load_memory()
        prices = [p["price"] for p in mem["prices"][-168:]]
        if len(prices) < 10: return {}
        return {"resistance": max(prices), "support": min(prices), "avg": round(sum(prices)/len(prices),2)}
    except: return {}

# ── GRADE PREDICTIONS (UPGRADED — updates pattern memory) ────────────────────
def grade_predictions(current_price):
    mem = load_memory()
    for pred in reversed(mem["predictions"]):
        if pred.get("graded"): break
        pred_ts = datetime.strptime(pred["ts"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < pred_ts + timedelta(hours=1): break
        pred["actual_price"] = round(current_price, 2)
        pred["target_hit"]   = current_price >= pred["target"]

        # Task 7: resolution context from btc tick history
        try:
            pred_ts_utc = datetime.strptime(pred["ts"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            window_end  = pred_ts_utc + timedelta(hours=1)
            window_prices = [
                p["price"] for p in mem.get("prices", [])
                if isinstance(p.get("ts"), str) and
                   pred_ts_utc <= datetime.strptime(p["ts"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc) <= window_end
            ]
            if window_prices:
                if pred.get("bet") == "YES":
                    pred["max_favorable"] = max(window_prices)
                    pred["max_adverse"]   = min(window_prices)
                elif pred.get("bet") == "NO":
                    pred["max_favorable"] = min(window_prices)
                    pred["max_adverse"]   = max(window_prices)
            pred["final_margin"] = round(current_price - pred["target"], 2)
        except Exception:
            pass

        pred["graded"] = True
        s = mem["stats"]
        if pred["target_hit"]:
            s["correct_target"] = s.get("correct_target",0)+1
            s["current_streak"] = s.get("current_streak",0)+1
            s["best_streak"] = max(s.get("best_streak",0), s["current_streak"])
        else:
            s["current_streak"] = 0
        if pred["bet"] == "YES":
            s["total_bet_yes"] = s.get("total_bet_yes",0)+1
            if pred["target_hit"]: s["correct_bet_yes"] = s.get("correct_bet_yes",0)+1
        elif pred["bet"] == "NO":
            s["total_bet_no"] = s.get("total_bet_no",0)+1
            if not pred["target_hit"]: s["correct_bet_no"] = s.get("correct_bet_no",0)+1

        # ── UPDATE PATTERN MEMORY ──
        fingerprint = pred.get("fingerprint")
        if fingerprint:
            correct = (pred["bet"] == "YES" and pred["target_hit"]) or \
                      (pred["bet"] == "NO" and not pred["target_hit"])
            update_pattern_memory(fingerprint, correct)
            log.info(f"Pattern updated: {fingerprint} correct={correct}")

        save_memory(mem)
        log.info(f"Graded prediction: target_hit={pred['target_hit']}")
        break

# ── KALSHI BRAIN ──────────────────────────────────────────────────────────────
def load_kalshi_brain():
    if os.path.exists(BRAIN_FILE):
        try: return json.load(open(BRAIN_FILE))
        except: pass
    return {"bets":[],"preds":[],"kalshi_manual_bets":[],"skips":[],
            "stats":{"total":0,"wins":0,"losses":0,"profit":0.0,
            "yes_total":0,"yes_wins":0,"no_total":0,"no_wins":0,
            "total_earnings":0,"total_contracts":0,"pred_total":0,"pred_correct":0}}

def save_kalshi_brain(kb):
    with open(BRAIN_FILE,"w") as f: json.dump(kb,f,indent=2)

def recompute_kalshi_stats(kb):
    """Task 9: recompute stats from raw records, filtering archived/suspect."""
    bets  = [b for b in kb.get("bets",  []) if not b.get("archived") and not b.get("suspect")]
    preds = [p for p in kb.get("preds", []) if not p.get("archived") and not p.get("suspect_grade")]
    y = [b for b in bets if b.get("side") == "YES"]
    n = [b for b in bets if b.get("side") == "NO"]
    graded = [p for p in preds if p.get("correct") is not None]
    ex = kb.get("stats", {})
    return {
        "total":           len(bets),
        "wins":            sum(1 for b in bets if b.get("result") == "WIN"),
        "losses":          sum(1 for b in bets if b.get("result") == "LOSS"),
        "profit":          round(sum(b.get("pnl") or 0 for b in bets), 2),
        "yes_total":       len(y),   "yes_wins": sum(1 for b in y if b.get("result") == "WIN"),
        "no_total":        len(n),   "no_wins":  sum(1 for b in n if b.get("result") == "WIN"),
        "pred_total":      len(graded),
        "pred_correct":    sum(1 for p in graded if p.get("correct")),
        "total_earnings":  ex.get("total_earnings",  0),
        "total_contracts": ex.get("total_contracts", 0),
    }

def log_bet(side, dollars, btype="hourly"):
    kb = load_kalshi_brain()
    kb["bets"].append({"id":datetime.now().strftime("%Y%m%d%H%M%S"),
        "ts":datetime.now().strftime("%Y-%m-%d %H:%M"),
        "side":side,"type":btype,"dollars":dollars,"result":None,"pnl":None})
    kb["stats"]["total"] += 1
    if side=="YES": kb["stats"]["yes_total"]+=1
    else: kb["stats"]["no_total"]+=1
    save_kalshi_brain(kb)

def grade_bet(won, actual_payout=None):
    # Stub — manual bets now graded via jmb.grade_manual_bet(); no pnl written here.
    return None, load_kalshi_brain()

def log_pred_call(price, direction, conf, mins):
    """Log PRED call to active session"""
    try:
        with open("/root/jarvis/active_session.json", "r") as f:
            session = json.load(f)
    except:
        session = {"preds": []}
    if "preds" not in session: session["preds"] = []
    session["preds"].append({
        "id": datetime.now().strftime("%Y%m%d%H%M%S"),
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "price": price, "direction": direction, "conf": conf, "mins": mins,
        "result": None, "correct": None
    })
    with open("/root/jarvis/active_session.json", "w") as f:
        json.dump(session, f, indent=2)

def grade_pred_call(actual_price):
    kb = load_kalshi_brain()
    for pred in reversed(kb.get("preds",[])):
        if pred["result"] is None:
            ref = pred["price"]
            direction = pred["direction"]
            actually_above = actual_price > ref
            predicted_above = direction in ["ABOVE","UP"]
            correct = (actually_above == predicted_above)
            pred["result"] = "ABOVE" if actually_above else "BELOW"
            pred["actual_price"] = actual_price
            pred["correct"] = correct
            kb["stats"] = recompute_kalshi_stats(kb)  # Task 9
            save_kalshi_brain(kb)
            return pred, correct
    return None, None

def get_pred_accuracy_context():
    kb = load_kalshi_brain()
    preds = [p for p in kb.get("preds",[]) if p.get("result")]
    if len(preds) < 3: return "Building accuracy history..."
    total = len(preds)
    correct = sum(1 for p in preds if p.get("correct"))
    wr = round(correct/total*100)
    above = [p for p in preds if p["direction"] in ["ABOVE","UP"]]
    below = [p for p in preds if p["direction"] in ["BELOW","DOWN"]]
    a_wr = round(sum(1 for p in above if p.get("correct"))/len(above)*100) if above else 0
    b_wr = round(sum(1 for p in below if p.get("correct"))/len(below)*100) if below else 0
    last5 = "".join(["✓" if p.get("correct") else "✗" for p in preds[-5:]])
    return f"15-min accuracy: {wr}% ({correct}/{total}) | ABOVE:{a_wr}% BELOW:{b_wr}% | Last5:{last5}"

def kalshi_stats_msg():
    # Honest record from jarvis_memory.db (read-only), mirroring kalshi_grader.
    # Old kb["stats"]["profit"] (+$2,018 even-money fabrication) is no longer read.
    import sqlite3
    DB_RO = "file:/root/jarvis/jarvis_memory.db?mode=ro"
    try:
        conn = sqlite3.connect(DB_RO, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("""
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN result='WIN'  THEN 1 ELSE 0 END) AS wins,
              SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) AS losses,
              ROUND(COALESCE(SUM(pnl),0),2) AS pnl,
              SUM(CASE WHEN bet='YES' THEN 1 ELSE 0 END) AS yes_total,
              SUM(CASE WHEN bet='YES' AND result='WIN' THEN 1 ELSE 0 END) AS yes_wins,
              SUM(CASE WHEN bet='NO'  THEN 1 ELSE 0 END) AS no_total,
              SUM(CASE WHEN bet='NO'  AND result='WIN' THEN 1 ELSE 0 END) AS no_wins
            FROM kalshi_bets
            WHERE source='auto' AND result IN ('WIN','LOSS')
        """).fetchone()
        pending = conn.execute(
            "SELECT COUNT(*) FROM kalshi_bets WHERE source='auto' AND result IS NULL"
        ).fetchone()[0]
        last5_rows = conn.execute("""
            SELECT result, pnl FROM kalshi_bets
            WHERE source='auto' AND result IN ('WIN','LOSS')
            ORDER BY id DESC LIMIT 5
        """).fetchall()
        conn.close()
    except Exception as e:
        return f"KALSHI stats unavailable (DB read error: {e})"

    total = row["total"] or 0
    if total == 0:
        return "No graded bets yet.\nBET YES/NO <$> to log"
    wins = row["wins"] or 0
    wr = round(wins / total * 100)
    yt, yw = row["yes_total"] or 0, row["yes_wins"] or 0
    nt, nw = row["no_total"] or 0, row["no_wins"] or 0
    yes_wr = round(yw / yt * 100) if yt else 0
    no_wr  = round(nw / nt * 100) if nt else 0
    pnl = row["pnl"] or 0.0
    last5 = list(reversed(last5_rows))
    last5_str = " ".join(
        (f"W${abs(r['pnl'] or 0):.0f}" if r["result"] == "WIN" else f"L${abs(r['pnl'] or 0):.0f}")
        for r in last5
    ) or "—"

    try:
        kb = load_kalshi_brain()
        s = kb.get("stats", {})
        pt = s.get("pred_total", 0); pc = s.get("pred_correct", 0)
        pred_wr = round(pc / pt * 100) if pt else 0
        total_patterns = len(load_patterns().get("fingerprints", {}))
    except Exception:
        pt = pc = pred_wr = total_patterns = 0

    return (f"KALSHI TRACKER (DB)\n{'='*20}\n"
            f"Bets: {total} | WR:{wr}% | P&L:${pnl:+.2f}\n"
            f"YES:{yw}/{yt}({yes_wr}%) NO:{nw}/{nt}({no_wr}%)\n"
            f"Pending: {pending} awaiting settlement\n"
            f"15-min PRED: {pc}/{pt} ({pred_wr}%)\n"
            f"{'='*20}\nLast 5: {last5_str}\n"
            f"{'='*20}\nPattern library: {total_patterns} fingerprints")

# ── TELEGRAM COMMAND HANDLER ──────────────────────────────────────────────────
def handle_command(text, parts):
    text = text.strip().upper()
    parts = text.split()

    if text == "BTC" or (len(parts)==1 and parts[0]=="BTC"):
        price = get_btc_price_from_kalshi()
        markets, _ = get_kalshi_markets(price or 75000)
        if not markets:
            tg("No Kalshi markets right now")
            return
        mkt_lines = "\n".join([f"${m['strike']:,.0f} YES:{m['yes']:.2f} NO:{m['no']:.2f}" for m in markets[:6]])
        funding = get_funding_rate("BTC")
        rsi = get_rsi("BTC")
        momentum = get_momentum("BTC")
        vol = get_volume_ratio("BTC")
        oracle_ctx, fingerprint, pat_modifier = build_oracle_context(price, rsi, momentum, funding, vol)
        best = markets[0]
        suggested_bet = kelly_bet_size(60, best["yes"], bankroll=500, pattern_modifier=pat_modifier)
        prompt = f"""BTC Kalshi prediction. Sharp and direct.

{oracle_ctx}

Live markets:
{mkt_lines}

Best trade right now? Pick the best Kalshi market.
Kelly suggested: ${suggested_bet:.0f}
Submit your pick via the submit_best_trade tool."""
        data = claude_tool(prompt, BEST_TRADE_TOOL, max_tokens=300)
        if data:
            sp = data.get("strike")
            strike = f"{float(sp):,.0f}" if isinstance(sp, (int, float)) else "?"
            bet = str(data.get("bet", "SKIP")).strip().upper()
            cf = data.get("confidence")
            conf = f"{int(round(float(cf)))}%" if isinstance(cf, (int, float)) else "?"
            reason = str(data.get("reason", "")).strip()
            _, pat_note = get_pattern_modifier(fingerprint)
            tg(f"🤖 JARVIS CALL\nBTC ~${price:,.0f}\n{'='*18}\n{bet} ${strike} — {conf}\n{reason}\n{'='*18}\n{mkt_lines}\n{'='*18}\nKelly: ${suggested_bet:.0f} | {pat_note}\nReply: BET YES/NO <$>")
        else:
            tg(f"Live Kalshi:\n{mkt_lines}")

    elif text.startswith("PRED"):
        try:
            # PRED <mins> — just minutes, fetch live price automatically
            # PRED <price> <mins> — explicit price + minutes
            btc_now = get_btc_price_from_kalshi() or 75000
            # Read watched price from session
            try:
                with open("/root/jarvis/active_session.json") as sf:
                    session = json.load(sf)
                    watch_strike = session.get("strike")
            except:
                watch_strike = None
            
            if len(parts) == 1:
                # PRED alone — use watch strike or live price, 15 min default
                ref_price = watch_strike or btc_now
                mins = 15
            elif len(parts) == 2:
                val = float(parts[1].replace(",",""))
                if val < 1000:
                    # Small number = minutes (e.g. PRED 14)
                    mins = int(val)
                    ref_price = watch_strike or btc_now
                else:
                    # Large number = explicit price override
                    ref_price = val
                    mins = 15
            else:
                # PRED <price> <mins>
                ref_price = float(parts[1].replace(",",""))
                mins = int(parts[2])

            markets, _ = get_kalshi_markets(btc_now)
            mkt_lines = "\n".join([f"${m['strike']:,.0f} YES:{m['yes']:.2f} NO:{m['no']:.2f}" for m in markets[:5]]) if markets else "No markets"
            pred_ctx = get_pred_accuracy_context()
            funding = get_funding_rate("BTC")
            rsi = get_rsi("BTC")
            momentum = get_momentum("BTC")
            vol = get_volume_ratio("BTC")
            oracle_ctx, fingerprint, pat_modifier = build_oracle_context(btc_now, rsi, momentum, funding, vol)
            prompt = f"""Will BTC be ABOVE or BELOW ${ref_price:,.0f} in {mins} minutes?

CONFIRMED LIVE BTC PRICE: ${btc_now:,.0f} — use this exact number in your reasoning, not any other price.

{oracle_ctx}

Kalshi:
{mkt_lines}

Track record: {pred_ctx}
Submit your call via the submit_direction tool (current BTC = ${btc_now:,.0f})."""
            data = claude_tool(prompt, PRED_DIR_TOOL, max_tokens=200)
            if data:
                ab = "ABOVE" if str(data.get("direction", "")).strip().upper() in ["ABOVE", "UP"] else "BELOW"
                cf = data.get("confidence")
                conf = f"{int(round(float(cf)))}%" if isinstance(cf, (int, float)) else "?"
                reason = str(data.get("reason", "")).strip()
                bet_side = "YES" if ab=="ABOVE" else "NO"
                log_pred_call(ref_price, ab, conf, mins)
                # Task 5: manual snapshot with full market context
                _bm = markets[0] if markets else None
                _yp = _bm.get("yes", 0.0) if _bm else 0.0
                _np = _bm.get("no",  0.0) if _bm else 0.0
                try:    _edge = round(abs(float(conf.replace("%",""))/100 - _yp)*100, 1)
                except: _edge = None
                _manual = {
                    "id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
                    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    "symbol": "BTC", "price_at_pred": round(btc_now, 2),
                    "target": ref_price, "target_prob": conf,
                    "predicted_price": None, "bet": bet_side, "reason": reason,
                    "fingerprint": fingerprint, "mins": mins,
                    "kalshi_yes_price": _yp, "spread": round(abs(_yp-_np),4), "edge_pct": _edge,
                    "actual_price": None, "target_hit": None, "graded": False,
                    "source": "manual", "schema_version": SCHEMA_VERSION,
                }
                _kb = load_kalshi_brain()
                if "kalshi_manual_bets" not in _kb: _kb["kalshi_manual_bets"] = []
                _kb["kalshi_manual_bets"].append(_manual)
                save_kalshi_brain(_kb)
                _, pat_note = get_pattern_modifier(fingerprint)
                tg(f"{ab} ${ref_price:,.0f} in {mins}min\n{conf} — BET {bet_side}\n{reason}\n{pat_note}")
            else:
                tg("Claude unavailable")
        except Exception as e:
            tg(f"Format: PRED 15 or PRED 73500 15\nError: {e}")

    elif text.startswith("RESULT"):
        try:
            # RESULT <actual_price> <payout>  — payout is optional, what Kalshi actually paid
            actual = float(parts[1].replace(",",""))
            actual_payout = float(parts[2]) if len(parts) > 2 else None
            pred, correct = grade_pred_call(actual)
            if pred:
                kb = load_kalshi_brain()
                # Update payout on last bet if provided
                if actual_payout is not None:
                    for bet in reversed(kb["bets"]):
                        if bet["result"] in ["WIN", "LOSS"] and bet.get("pnl") is not None:
                            stake = bet.get("dollars", 0)
                            if bet["result"] == "WIN":
                                bet["pnl"] = round(actual_payout - stake, 2)
                                kb["stats"]["profit"] = round(
                                    sum(b.get("pnl",0) for b in kb["bets"] if b.get("pnl") is not None), 2)
                            break
                    save_kalshi_brain(kb)
                pt = kb["stats"].get("pred_total",0)
                pc = kb["stats"].get("pred_correct",0)
                wr = round(pc/pt*100) if pt>0 else 0
                status = "CORRECT ✓" if correct else "WRONG ✗"
                payout_line = f"Payout: ${actual_payout:.0f}" if actual_payout else ""
                tg(f"{status}\nPredicted: {pred['direction']} ${pred['price']:,.0f}\nActual: ${actual:,.0f}\n{payout_line}\nAccuracy: {wr}% ({pc}/{pt})")
            else:
                tg("No pending prediction")
        except Exception as e: tg(f"Format: RESULT 74850 or RESULT 74850 87\nError: {e}")

    elif text.startswith("BET15"):
        try:
            side = parts[1]; dollars = float(parts[2])
            _p15 = get_btc_price_from_kalshi() or 0
            _mkts15, _ = get_kalshi_markets(_p15 or 75000)
            _bm15 = _mkts15[0] if _mkts15 else None
            _strike15 = _bm15["strike"] if _bm15 else 0.0
            _yp15 = _bm15.get("yes", 0.0) if _bm15 else 0.0
            _np15 = _bm15.get("no", 0.0) if _bm15 else 0.0
            jmb.log_manual_bet(side, dollars, _strike15, _yp15, _np15,
                               "manual BET15", entry_spot=round(_p15, 2))
            tg(f"Logged 15-MIN {side} ${dollars:.0f}\nText WIN <payout> or LOSS when done")
        except: tg("Format: BET15 YES 50")

    elif text.upper().startswith("BET"):
        try:
            parts = text.upper().split(); side = parts[1]; dollars = float(parts[2])
            _pb = get_btc_price_from_kalshi() or 0
            _mktsb, _ = get_kalshi_markets(_pb or 75000)
            _bmb = _mktsb[0] if _mktsb else None
            _strike = _bmb["strike"] if _bmb else 0.0
            _ypb = _bmb.get("yes", 0.0) if _bmb else 0.0
            _npb = _bmb.get("no", 0.0) if _bmb else 0.0
            jmb.log_manual_bet(side, dollars, _strike, _ypb, _npb, "manual BET")
            tg(f"Logged {side} ${dollars:.0f}\nText WIN <payout> or LOSS when done")
        except: tg("Format: BET YES 50")

    elif text.startswith("WIN"):
        # WIN or WIN 87 (actual payout amount)
        try:
            actual_payout = float(parts[1]) if len(parts) > 1 else None
            bet = jmb.grade_manual_bet(True, actual_payout)
            if bet:
                tg(f"WIN +${abs(bet['pnl']):.2f}\n{jmb.manual_stats()}")
            else: tg("No pending bet")
        except Exception as e: tg(f"WIN graded but message failed: {e}")

    elif text == "LOSS":
        bet = jmb.grade_manual_bet(False)
        if bet: tg(f"LOSS -${abs(bet['pnl']):.2f}\n{jmb.manual_stats()}")
        else: tg("No pending bet")

    elif text == "KALSHI":
        tg(kalshi_stats_msg())

    elif text == "MANUAL":
        tg(jmb.manual_stats())

    elif text == "PATTERNS":
        patterns = load_patterns()
        fps = patterns["fingerprints"]
        if not fps:
            tg("No patterns learned yet. Keep running predictions.")
            return
        top = sorted(fps.items(), key=lambda x: x[1].get("total",0), reverse=True)[:5]
        lines = ["🧠 TOP PATTERNS\n" + "="*20]
        for fp, data in top:
            total = data.get("total",0)
            wins = data.get("wins",0)
            wr = round(wins/total*100) if total > 0 else 0
            lines.append(f"{fp}\nWR:{wr}% ({wins}/{total})\n")
        tg("\n".join(lines))

    elif text == "STATUS":
        master = load_master()
        s = master["stats"]
        price = get_btc_price_from_kalshi()
        acct = get_account()
        equity = float(acct.get("equity","0"))
        positions = get_positions()
        pos_lines = "\n".join([f"{p['symbol']} P&L:${float(p['unrealized_pl']):+.2f}" for p in positions]) or "No positions"
        fear_val, fear_label = get_fear_greed()
        _, trend_4h = get_4h_momentum()
        patterns = load_patterns()
        pat_count = len(patterns["fingerprints"])
        tg(f"JARVIS STATUS\n{'='*18}\nBTC ~${price:,.0f}\nEquity: ${equity:,.2f}\nF&G: {fear_val} {fear_label}\n4H: {trend_4h}\n{'='*18}\nTrades: {s['total_trades']} | W:{s['wins']} L:{s['losses']}\nTotal P&L: ${s.get('total_pnl',0):+.2f}\nSize mult: {s['size_multiplier']:.1f}x\n{'='*18}\nPattern library: {pat_count} fingerprints\n{'='*18}\nPositions:\n{pos_lines}")

    elif text.startswith("WATCH"):
        try:
            if text in ["WATCH OFF","WATCH STOP"]:
                if SESSION_ENABLED: jsess.clear_session()
                tg("Session cleared")
            else:
                strike = float(parts[1].replace(",",""))
                btc_now = get_btc_price_from_kalshi() or strike
                diff = btc_now - strike
                position = "ABOVE" if diff >= 0 else "BELOW"
                if SESSION_ENABLED: jsess.start_session(strike)
                tg(f"WATCHING ${strike:,.0f}\nBTC: ${btc_now:,.0f} ({position} by ${abs(diff):,.0f})\nText PRED <mins> when ready")
        except Exception as e: tg(f"Format: WATCH 73100\nError:{e}")

    elif text.startswith("CLOSE"):
        try:
            actual = float(parts[1].replace(",",""))
            won = len(parts) > 2 and "WIN" in parts[2]
            if SESSION_ENABLED:
                session, pred_results = jsess.close_session(actual, won)
                if session:
                    strike = session["strike"]
                    correct = sum(1 for r in pred_results if r)
                    total_p = len(pred_results)
                    diff = actual - strike
                    pos = "ABOVE" if diff >= 0 else "BELOW"
                    emoji = "WIN ✅" if won else "LOSS 🔴"
                    tg(f"{emoji}\nStrike:${strike:,.0f} Close:${actual:,.0f} ({pos})\nPred:{correct}/{total_p} correct\n{kalshi_stats_msg()}")
                else:
                    tg("No active session — use WATCH first")
        except Exception as e: tg(f"Format: CLOSE 73050 WIN\nError:{e}")

    elif text.startswith("OPT"):
        # OPT OPEN <ticker> <strategy> <strike> <premium> <expiry>
        # OPT CLOSE <id> <WIN|LOSS> <exit_premium>
        if len(parts) < 2:
            tg("Options commands:\nOPT OPEN <ticker> <strategy> <strike> <premium> <expiry>\nOPT CLOSE <id> <WIN|LOSS> <exit_premium>")
            return
        sub = parts[1]
        if sub == "OPEN":
            try:
                if len(parts) < 7:
                    tg("Usage: OPT OPEN <ticker> <strategy> <strike> <premium> <expiry>\nExample: OPT OPEN SPY bear_call_spread 590 1.40 2026-07-18")
                    return
                _ticker   = parts[2]
                _strategy = parts[3].lower()
                _strike   = float(parts[4])
                _premium  = float(parts[5])
                _expiry   = parts[6]  # YYYY-MM-DD (already upper, dates are numeric so safe)
                from datetime import date as _date
                _dte = (_date.fromisoformat(_expiry) - _date.today()).days
                from jarvis_memory_db import log_options_trade as _lot
                import sqlite3 as _sq
                _tid = _lot(ticker=_ticker, strategy=_strategy, strike=_strike,
                            premium=_premium, dte=_dte, iv=0, score=0,
                            catalyst=f"real fill exp {_expiry}", source="real", is_real=1)
                # persist expiry into its own column (log_options_trade INSERT omits it)
                _cx = _sq.connect("/root/jarvis/jarvis_memory.db", timeout=10)
                _cx.execute("UPDATE options_trades SET expiry=? WHERE id=?", (_expiry, _tid))
                _cx.commit(); _cx.close()
                tg(f"OPT OPEN logged id={_tid} | {_ticker} {_strategy} ${_strike} @ ${_premium} exp {_expiry}")
            except Exception as _e:
                tg(f"OPT OPEN failed: {_e}\nUsage: OPT OPEN <ticker> <strategy> <strike> <premium> <expiry>")
        elif sub == "CLOSE":
            try:
                if len(parts) < 5:
                    tg("Usage: OPT CLOSE <id> <WIN|LOSS> <exit_premium>\nExample: OPT CLOSE 47 WIN 0.60")
                    return
                _tid        = int(parts[2])
                _result     = parts[3]
                _exit_prem  = float(parts[4])
                if _result not in ("WIN", "LOSS"):
                    tg("Result must be WIN or LOSS\nUsage: OPT CLOSE <id> <WIN|LOSS> <exit_premium>")
                    return
                import sqlite3 as _sq
                _cx = _sq.connect("/root/jarvis/jarvis_memory.db", timeout=10)
                _cx.row_factory = _sq.Row
                _row = _cx.execute("SELECT premium, ticker, strategy FROM options_trades WHERE id=?", (_tid,)).fetchone()
                _cx.close()
                if not _row:
                    tg(f"OPT CLOSE: no trade found with id={_tid}")
                    return
                _entry = _row["premium"]
                _pnl   = round((_entry - _exit_prem) * 100, 2)
                from jarvis_memory_db import close_options_trade as _cot
                _cot(_tid, _result, _pnl, exit_premium=_exit_prem)
                _sign = "+" if _pnl >= 0 else ""
                tg(f"OPT CLOSE id={_tid} {_result} ${_sign}{_pnl:.2f} | computed as (entry ${_entry} - exit ${_exit_prem}) x 100")
            except Exception as _e:
                tg(f"OPT CLOSE failed: {_e}\nUsage: OPT CLOSE <id> <WIN|LOSS> <exit_premium>")
        else:
            tg("Options commands:\nOPT OPEN <ticker> <strategy> <strike> <premium> <expiry>\nOPT CLOSE <id> <WIN|LOSS> <exit_premium>")

    elif text == "HELP":
        tg("JARVIS MASTER COMMANDS\n"
           "BTC — live Oracle prediction\n"
           "PRED <price> <mins> — ABOVE/BELOW call\n"
           "RESULT <price> — grade last PRED\n"
           "BET YES/NO <$> — log hourly bet\n"
           "BET15 YES/NO <$> — log 15min bet\n"
           "WIN / LOSS — grade last bet\n"
           "KALSHI — bet stats\n"
           "PATTERNS — top learned patterns\n"
           "STATUS — full system status\n"
           "OPT OPEN <ticker> <strategy> <strike> <premium> <expiry> — log real options fill\n"
           "OPT CLOSE <id> <WIN|LOSS> <exit_premium> — close + grade options trade")

# ── SELF IMPROVEMENT ──────────────────────────────────────────────────────────
def run_self_improvement(master):
    trades = master.get("trades",[])
    if len(trades) < 10: return
    wins = master["stats"]["wins"]
    losses = master["stats"]["losses"]
    total = wins+losses
    if total < 5: return
    wr = wins/total
    changes = []
    if wr > 0.65 and total >= 20:
        master["stats"]["size_multiplier"] = min(2.0, master["stats"]["size_multiplier"]+0.1)
        changes.append(f"Size up to {master['stats']['size_multiplier']:.1f}x (WR:{wr*100:.0f}%)")
    elif wr < 0.40:
        master["stats"]["size_multiplier"] = max(0.3, master["stats"]["size_multiplier"]-0.15)
        changes.append(f"Size down to {master['stats']['size_multiplier']:.1f}x (WR:{wr*100:.0f}%)")
    # Hour analysis
    hour_stats = {}
    for t in trades:
        h = t.get("ts","00:00").split(":")[0][-2:]
        if h not in hour_stats: hour_stats[h] = {"wins":0,"total":0}
        hour_stats[h]["total"] += 1
    master["stats"]["best_hours"] = {h:d for h,d in hour_stats.items() if d["total"]>=3}

    # Pattern memory report
    patterns = load_patterns()
    fps = patterns["fingerprints"]
    best_patterns = [(fp, d) for fp, d in fps.items() if d.get("total",0) >= 5]
    if best_patterns:
        best_patterns.sort(key=lambda x: x[1]["wins"]/x[1]["total"], reverse=True)
        top = best_patterns[0]
        wr_top = round(top[1]["wins"]/top[1]["total"]*100)
        changes.append(f"Best pattern WR:{wr_top}% — {top[0]}")

    save_master(master)
    if changes:
        tg(f"🧠 SELF IMPROVEMENT\n" + "\n".join(changes))
    log.info(f"Self-improvement complete. WR:{wr*100:.0f}% Size:{master['stats']['size_multiplier']:.1f}x Patterns:{len(fps)}")

# ── MAIN LOOP ─────────────────────────────────────────────────────────────────
def main():
    log.info("JARVIS MASTER ONLINE — ORACLE MODE")
    tg("🤖 JARVIS ORACLE ONLINE\nMACD + Bollinger + Fear&Greed + Pattern Memory active.\nText HELP for commands.", TG_TRADER)
    master = load_master()
    tg_offset = None
    last_hourly = 0
    last_scalp_check = 0
    last_self_improve = 0
    last_daily_reset = datetime.now(timezone.utc).date()

    while True:
        try:
            # Heartbeat to the SQLite store the watchdog reads (jarvis_master is
            # a critical bot; without this it's perpetually flagged "no heartbeat").
            if _jb_hb is not None:
                try: _jb_hb.update_bot_heartbeat("jarvis_master")
                except Exception: pass

            now = datetime.now(timezone.utc)
            now_edt = (now.hour - 4) % 24

            # Daily reset
            if now.date() != last_daily_reset:
                master["stats"]["daily_loss"] = 0.0
                acct = get_account()
                master["stats"]["daily_start_equity"] = float(acct.get("equity","0"))
                last_daily_reset = now.date()
                save_master(master)
                log.info("Daily reset")

            # Telegram commands
            for u in tg_updates(TG_TRADER, tg_offset):
                tg_offset = u["update_id"] + 1
                msg = u.get("message",{})
                if str(msg.get("chat",{}).get("id","")) != CHAT_ID: continue
                text = msg.get("text","").strip()
                parts = text.upper().split()
                log.info(f"CMD: {text}")
                handle_command(text.upper(), parts)

            # Hourly prediction
            if time.time() - last_hourly >= 3600:
                price = get_btc_price_from_kalshi()
                if price:
                    rsi = get_rsi("BTC")
                    momentum = get_momentum("BTC")
                    grade_predictions(price)
                    run_hourly_prediction(price, rsi, momentum)
                last_hourly = time.time()

            # Scalp check every 90 seconds
            if time.time() - last_scalp_check >= 90:
                positions = get_positions()
                check_scalp_exits(master, positions)
                for symbol in ["BTC", "ETH", "SOL"]:
                    price = get_coingecko_price(symbol)
                    if not price: continue
                    rsi = get_rsi(symbol)
                    funding = get_funding_rate(symbol)
                    volume = get_volume_ratio(symbol)
                    mem = load_memory()
                    recent = mem["prices"][-12:] if mem["prices"] else []
                    regime = detect_regime(recent)
                    mode, reason = get_trade_mode(regime, rsi, funding, volume)
                    log.info(f"{symbol} RSI:{rsi} Fund:{funding:.4f} Vol:{volume}x Regime:{regime} → {mode}")
                    # Publish a live btc_signal to the central brain every cycle —
                    # the update_btc_state() call its docstring promised but that was
                    # never wired, leaving btc_signal frozen at a stale orphan value.
                    # Cross-bot consumers (regime confidence scorer, options brain,
                    # lenny, beast) read this key. Uses the cycle's fresh rsi/funding/
                    # volume (Binance-klines-independent); macd/4h-trend degrade to
                    # neutral when Binance klines are unavailable.
                    if symbol == "BTC" and _jb_hb is not None:
                        try:
                            _closes, _, _, _ = get_binance_prices_for_indicators()
                            _, _, _macd_hist = get_macd(_closes) if _closes else (0, 0, 0)
                            _, _trend_4h = get_4h_momentum()
                            _fg, _ = get_fear_greed()
                            _jb_hb.update_btc_state(price, rsi, _trend_4h, _macd_hist,
                                                    funding, volume, _fg)
                        except Exception as _be:
                            log.error(f"publish btc_signal: {_be}")
                    if "SCALP" in mode:
                        attempt_scalp(master, symbol, mode, price, rsi, funding, volume)
                last_scalp_check = time.time()

            # Self improvement at 8pm EDT daily
            if now_edt == 20 and time.time() - last_self_improve >= 3600:
                run_self_improvement(master)
                last_self_improve = time.time()

            time.sleep(5)
        except Exception as e:
            log.error(f"Main loop error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
