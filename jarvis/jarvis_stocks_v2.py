#!/usr/bin/env python3
"""
JARVIS STOCKS V2 — FIXED
Fixes: Alpaca IEX 404 → switched to SIP feed + Yahoo Finance fallback
Upgrades: Better error handling, safe brain reads
"""
import json, time, math, requests, os
from datetime import datetime, timedelta
import logging
try:
    import jarvis_brain as _jb_hb
except Exception:
    _jb_hb = None

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('JARVIS-STOCKS-V2')

ALPACA_KEY     = "PKTHANGUNVFDSLLR3VXPETXRQF"
ALPACA_SECRET  = "GRTDDfkCGWbZMoNSWms6uJSGvw72rHaAk1N1fvLi8EAP"
ALPACA_BASE    = "https://paper-api.alpaca.markets"
ALPACA_DATA    = "https://data.alpaca.markets"
TELEGRAM_TOKEN = __import__("jarvis_secrets").TG_TOKEN_INTEL
TELEGRAM_CHAT  = "7534553840"
from jarvis_secrets import CLAUDE_API_KEY
# Own file — jarvis_stocks_brain.json is the rich learning-brain schema owned by
# jarvis_stocks.py(v1)/jarvis_intelligence (total_trades/total_pnl/…), read by
# check_trading.py + jarvis_intelligence. v2 uses a simple stats/daily_loss schema;
# sharing the file made the two clobber each other (and KeyError'd 'stats').
MEMORY_FILE    = "/root/jarvis/jarvis_stocks_v2_brain.json"

MICRO_SIZE      = 200
SWING_SIZE      = 500
BREAKOUT_SIZE   = 400
MAX_TRADE       = 600
MAX_POSITIONS   = 5
MAX_PER_SECTOR  = 2
BUY_RSI_STRONG  = 28
BUY_RSI_NORMAL  = 35
BUY_RSI_MICRO   = 45
SELL_RSI_HIGH   = 62
PROFIT_MICRO    = 0.5
PROFIT_SWING    = 2.0
PROFIT_BREAKOUT = 1.5
STOP_LOSS       = 1.2
TRAIL_STEP      = 0.3
MIN_HOLD_MICRO  = 8
MIN_HOLD_SWING  = 30
MIN_VOLUME_MULT = 1.3
DAILY_LOSS_LIMIT   = 400.0
CLOSE_MINS_BEFORE  = 15
MAX_CORR_POS       = 2
SCAN_INTERVAL  = 60
REPORT_HOUR    = 7

CORRELATION_GROUPS = {
    "semis":    ["NVDA","AMD","SOXS"],
    "big_tech": ["MSFT","AAPL","META","GOOGL"],
    "broad":    ["SPY","QQQ","IWM","TQQQ"],
    "crypto":   ["COIN","MSTR","HOOD"],
    "ev":       ["TSLA","RIVN","F"],
}

WATCHLIST = {
    "NVDA":"tech","AMD":"tech","MSFT":"tech","AAPL":"tech",
    "META":"tech","GOOGL":"tech","PLTR":"tech","MSTR":"tech",
    "JPM":"finance","BAC":"finance","GS":"finance",
    "COIN":"finance","HOOD":"finance",
    "XOM":"energy","CVX":"energy","OXY":"energy",
    "SPY":"etf","QQQ":"etf","IWM":"etf","TQQQ":"etf",
    "TSLA":"auto","RIVN":"auto","F":"auto","GM":"auto",
    "UNH":"health","JNJ":"health","PFE":"health",
    "AMZN":"consumer","WMT":"consumer","COST":"consumer",
}

# ── TELEGRAM ──────────────────────────────────────────────────────────────────
def tg(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": str(msg)[:4000]}, timeout=10)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

# ── ALPACA ────────────────────────────────────────────────────────────────────
def alpaca(method, path, data=None, base=None):
    b = base or ALPACA_BASE
    hdrs = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET, "Content-Type": "application/json"}
    for attempt in range(3):
        try:
            if method == "GET":      r = requests.get(b+path, headers=hdrs, timeout=15)
            elif method == "POST":   r = requests.post(b+path, headers=hdrs, json=data, timeout=15)
            elif method == "DELETE": r = requests.delete(b+path, headers=hdrs, timeout=15)
            if r.status_code in [200, 201]: return r.json()
            log.warning(f"Alpaca {r.status_code}: {r.text[:80]}")
        except Exception as e:
            log.error(f"Alpaca {attempt+1}: {e}")
        time.sleep(1)
    return None

def get_account():   return alpaca("GET", "/v2/account") or {}
def get_positions(): return alpaca("GET", "/v2/positions") or []
def get_clock():     return alpaca("GET", "/v2/clock") or {}
def is_market_open():
    c = get_clock()
    return c.get("is_open", False)

# ── MARKET DATA (FIXED — SIP feed + Yahoo fallback) ───────────────────────────
def get_quote_alpaca(symbol):
    """Try Alpaca SIP feed (replaces broken IEX feed)"""
    try:
        r = requests.get(f"{ALPACA_DATA}/v2/stocks/{symbol}/quotes/latest",
            headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
            params={"feed": "sip"}, timeout=10)
        if r.status_code == 200:
            q = r.json().get("quote", {})
            price = (q.get("ap", 0) + q.get("bp", 0)) / 2
            if price > 0: return price
    except: pass
    return None

def get_quote_yahoo(symbol):
    """Yahoo Finance fallback — always works"""
    try:
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"interval": "1m", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code == 200:
            result = r.json()["chart"]["result"]
            if result:
                return float(result[0]["meta"]["regularMarketPrice"])
    except: pass
    return None

def get_quote(symbol):
    """Get stock price — Alpaca SIP first, Yahoo Finance fallback"""
    price = get_quote_alpaca(symbol)
    if price: return price
    price = get_quote_yahoo(symbol)
    if price:
        log.info(f"{symbol} price via Yahoo: ${price:.2f}")
        return price
    log.warning(f"No price for {symbol}")
    return None

def get_bars_alpaca(symbol, limit=100):
    """Get hourly bars from Alpaca SIP feed"""
    try:
        r = requests.get(f"{ALPACA_DATA}/v2/stocks/{symbol}/bars",
            headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
            params={"timeframe": "1Hour", "limit": limit, "feed": "sip"}, timeout=15)
        if r.status_code == 200:
            bars = r.json().get("bars", [])
            if bars: return bars
    except: pass
    return None

def get_bars_yahoo(symbol, limit=50):
    """Yahoo Finance bars fallback"""
    try:
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"interval": "1h", "range": "5d"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code == 200:
            result = r.json()["chart"]["result"]
            if result:
                closes = result[0]["indicators"]["quote"][0]["close"]
                volumes = result[0]["indicators"]["quote"][0]["volume"]
                timestamps = result[0]["timestamps"]
                bars = []
                for i, (c, v, t) in enumerate(zip(closes, volumes, timestamps)):
                    if c and v:
                        bars.append({"c": c, "v": v, "t": datetime.fromtimestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ")})
                return bars[-limit:] if bars else None
    except: pass
    return None

def get_bars(symbol, limit=100):
    bars = get_bars_alpaca(symbol, limit)
    if bars: return bars
    bars = get_bars_yahoo(symbol, limit)
    if bars:
        log.info(f"{symbol} bars via Yahoo")
        return bars
    return None

# ── TECHNICAL INDICATORS ──────────────────────────────────────────────────────
def calc_rsi(closes, period=14):
    try:
        if len(closes) < period + 1: return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i-1]
            (gains if d > 0 else losses).append(abs(d))
        if not gains: return 50.0
        ag = sum(gains[-period:]) / period
        al = sum(losses[-period:]) / period if losses else 0.001
        return round(100 - (100 / (1 + ag/al)), 1)
    except: return 50.0

def calc_vwap(bars):
    try:
        total_pv = sum(((b.get("h",0)+b.get("l",0)+b.get("c",0))/3) * b.get("v",0) for b in bars)
        total_v  = sum(b.get("v",0) for b in bars)
        return round(total_pv / total_v, 2) if total_v > 0 else 0
    except: return 0

def calc_volume_ratio(bars):
    try:
        if len(bars) < 5: return 1.0
        current = bars[-1].get("v", 0)
        avg = sum(b.get("v",0) for b in bars[-20:-1]) / min(19, len(bars)-1)
        return round(current / avg, 2) if avg > 0 else 1.0
    except: return 1.0

def analyze_ticker(symbol):
    """Full technical analysis for a ticker"""
    bars = get_bars(symbol)
    if not bars or len(bars) < 15: return None
    closes  = [b.get("c", b.get("close", 0)) for b in bars]
    volumes = [b.get("v", b.get("volume", 0)) for b in bars]
    price   = closes[-1]
    rsi     = calc_rsi(closes)
    vol_ratio = calc_volume_ratio(bars)
    # Simple trend
    ma20 = sum(closes[-20:]) / min(20, len(closes))
    trend = "UP" if price > ma20 else "DOWN"
    return {
        "symbol": symbol, "price": price, "rsi": rsi,
        "vol_ratio": vol_ratio, "trend": trend, "ma20": round(ma20, 2)
    }

# ── CLAUDE VALIDATION ─────────────────────────────────────────────────────────
def claude_validate(symbol, analysis):
    try:
        prompt = f"""Stock trade signal. Be direct.
{symbol} @ ${analysis['price']:.2f}
RSI:{analysis['rsi']} Vol:{analysis['vol_ratio']}x Trend:{analysis['trend']} MA20:${analysis['ma20']}
Should I BUY? Reply: YES|CONFIDENCE%|REASON or NO|REASON"""
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": 100,
                  "messages": [{"role": "user", "content": prompt}]}, timeout=20)
        resp = r.json()
        if "content" in resp:
            text = resp["content"][0]["text"].strip()
            parts = text.split("|")
            if parts[0].strip().upper() == "YES":
                return True, parts[1].strip() if len(parts) > 1 else "65%"
        return False, "Claude says no"
    except Exception as e:
        log.error(f"Claude validate: {e}")
        return False, "Claude unavailable"

# ── BRAIN ─────────────────────────────────────────────────────────────────────
def load_memory():
    defaults = {"trades": [], "stats": {"total":0,"wins":0,"losses":0,"pnl":0.0}, "daily_loss": 0.0}
    if os.path.exists(MEMORY_FILE):
        try:
            data = json.load(open(MEMORY_FILE))
            # Backfill missing top-level keys so mem["stats"]/["daily_loss"] never KeyError
            # (a file written under a different/older schema would otherwise crash the loop).
            for k, v in defaults.items():
                data.setdefault(k, v)
            return data
        except: pass
    return defaults

def save_memory(mem):
    with open(MEMORY_FILE, "w") as f: json.dump(mem, f, indent=2)

# ── TRADING ───────────────────────────────────────────────────────────────────
def place_order(symbol, qty, side):
    return alpaca("POST", "/v2/orders", {
        "symbol": symbol, "qty": str(qty), "side": side,
        "type": "market", "time_in_force": "day"
    })

def close_position(symbol):
    import urllib.parse
    return alpaca("DELETE", f"/v2/positions/{urllib.parse.quote(symbol)}")

def check_exits(mem, positions):
    for pos in positions:
        sym = pos.get("symbol", "")
        if "/" in sym: continue  # skip options
        pnl_pct = float(pos.get("unrealized_plpc", 0)) * 100
        pnl_usd = float(pos.get("unrealized_pl", 0))
        if pnl_pct >= PROFIT_MICRO or pnl_pct <= -STOP_LOSS:
            reason = f"Profit +{pnl_pct:.2f}%" if pnl_pct > 0 else f"Stop {pnl_pct:.2f}%"
            if close_position(sym):
                mem["stats"]["pnl"] = round(mem["stats"]["pnl"] + pnl_usd, 2)
                mem["daily_loss"] = round(mem.get("daily_loss", 0) + pnl_usd, 2)
                if pnl_usd > 0: mem["stats"]["wins"] += 1
                else: mem["stats"]["losses"] += 1
                save_memory(mem)
                emoji = "✅" if pnl_usd > 0 else "🔴"
                tg(f"{emoji} STOCKS: {sym} closed\n{reason}\nP&L: ${pnl_usd:+.2f}\nTotal: ${mem['stats']['pnl']:+.2f}")
                log.info(f"Closed {sym}: {reason} ${pnl_usd:+.2f}")

def run_cycle(mem):
    if not is_market_open():
        log.info("Market closed")
        return

    if mem.get("daily_loss", 0) <= -DAILY_LOSS_LIMIT:
        log.info("Daily loss limit hit")
        return

    positions = get_positions()
    stock_positions = [p for p in positions if "/" not in p.get("symbol","")]
    check_exits(mem, stock_positions)

    if len(stock_positions) >= MAX_POSITIONS:
        log.info(f"Max positions {len(stock_positions)}/{MAX_POSITIONS}")
        return

    acct = get_account()
    equity = float(acct.get("equity", 0))

    for symbol, sector in WATCHLIST.items():
        if len(stock_positions) >= MAX_POSITIONS: break

        # Correlation check
        sector_count = sum(1 for p in stock_positions
            if WATCHLIST.get(p.get("symbol",""), "") == sector)
        if sector_count >= MAX_PER_SECTOR: continue

        analysis = analyze_ticker(symbol)
        if not analysis: continue

        rsi = analysis["rsi"]
        vol = analysis["vol_ratio"]
        price = analysis["price"]
        trend = analysis["trend"]

        # Signal check
        signal = None
        if rsi < BUY_RSI_STRONG and vol > MIN_VOLUME_MULT and trend == "UP":
            signal = "STRONG_BUY"
        elif rsi < BUY_RSI_NORMAL and vol > MIN_VOLUME_MULT:
            signal = "BUY"
        elif rsi < BUY_RSI_MICRO and vol > MIN_VOLUME_MULT * 1.5:
            signal = "MICRO_BUY"

        if not signal:
            log.info(f"{symbol} RSI:{rsi} Vol:{vol}x → no signal")
            continue

        # Claude validation
        ok, conf = claude_validate(symbol, analysis)
        if not ok:
            log.info(f"{symbol} Claude rejected")
            continue

        # Size
        size = MICRO_SIZE if signal == "MICRO_BUY" else SWING_SIZE if signal == "STRONG_BUY" else BREAKOUT_SIZE
        size = min(size, MAX_TRADE)
        qty = max(1, int(size / price))

        result = place_order(symbol, qty, "buy")
        if result:
            mem["stats"]["total"] += 1
            stock_positions.append({"symbol": symbol})
            save_memory(mem)
            tg(f"🎯 STOCKS: BUY {symbol}\n@ ${price:.2f} qty:{qty}\nRSI:{rsi} Vol:{vol}x {signal}\nConf:{conf}")
            log.info(f"Bought {symbol} @ ${price:.2f} qty={qty}")

        time.sleep(1)

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    log.info("JARVIS STOCKS V2 ONLINE — Yahoo Finance fallback active")
    tg("📈 Jarvis Stocks V2 online. SIP feed + Yahoo Finance fallback active.")
    mem = load_memory()
    last_reset = datetime.now().date()
    last_report = datetime.now().date()

    while True:
        try:
            now = datetime.now()

            # Daily reset
            if now.date() != last_reset:
                mem["daily_loss"] = 0.0
                save_memory(mem)
                last_reset = now.date()
                log.info("Daily reset")

            # Morning report
            if now.hour == REPORT_HOUR and now.minute < 5 and now.date() != last_report:
                s = mem["stats"]
                tg(f"📊 STOCKS DAILY\nTrades:{s['total']} W:{s['wins']} L:{s['losses']}\nP&L:${s['pnl']:+.2f}")
                last_report = now.date()

            run_cycle(mem)

        except Exception as e:
            log.error(f"Main loop error: {e}")

        if _jb_hb:
            _jb_hb.update_bot_heartbeat("jarvis_stocks_v2")
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
