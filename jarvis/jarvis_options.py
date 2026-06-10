"""
jarvis_options.py — Jarvis Options Trading Bot (FIXED)
Fixes: range_hit KeyError, safer brain reads, better error handling
"""
import requests, json, time, logging, os
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("jarvis_options")

from jarvis_secrets import CLAUDE_API_KEY
ALPACA_KEY     = "PKTHANGUNVFDSLLR3VXPETXRQF"
ALPACA_SECRET  = "GRTDDfkCGWbZMoNSWms6uJSGvw72rHaAk1N1fvLi8EAP"
ALPACA_BASE    = "https://paper-api.alpaca.markets"
ALPACA_DATA    = "https://data.alpaca.markets"
TG_TOKEN       = __import__("jarvis_secrets").TG_TOKEN_INTEL
TG_CHAT_ID     = "7534553840"
MEMORY_FILE    = "/root/jarvis/options_memory.json"
MAX_RISK       = 500
MAX_TRADES     = 3
INTERVAL       = 1800
WATCHLIST      = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA"]

def tg(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": str(msg)[:4000]}, timeout=10)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def alpaca(method, path, data=None):
    hdrs = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET, "Content-Type": "application/json"}
    for attempt in range(3):
        try:
            if method == "GET":      r = requests.get(ALPACA_BASE+path, headers=hdrs, timeout=15)
            elif method == "POST":   r = requests.post(ALPACA_BASE+path, headers=hdrs, json=data, timeout=15)
            elif method == "DELETE": r = requests.delete(ALPACA_BASE+path, headers=hdrs, timeout=15)
            if r.status_code in [200, 201]: return r.json()
            log.warning(f"Alpaca {r.status_code}: {r.text[:100]}")
        except Exception as e:
            log.error(f"Alpaca {attempt+1}: {e}")
        time.sleep(1)
    return None

def is_market_open():
    c = alpaca("GET", "/v2/clock")
    return c and c.get("is_open", False)

def get_positions():
    return alpaca("GET", "/v2/positions") or []

def get_options_positions():
    return [p for p in get_positions() if p.get("asset_class") == "us_option"]

def get_quote(symbol):
    # Try Alpaca first, fall back to Yahoo Finance
    try:
        r = requests.get(f"{ALPACA_DATA}/v2/stocks/{symbol}/quotes/latest",
            headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
            params={"feed": "sip"}, timeout=10)
        if r.status_code == 200:
            q = r.json().get("quote", {})
            price = (q.get("ap", 0) + q.get("bp", 0)) / 2
            if price > 0: return price
    except: pass
    # Yahoo Finance fallback
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

def get_options_chain(symbol):
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/options/contracts",
            headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
            params={"underlying_symbols": symbol,
                    "expiration_date_gte": (datetime.now()+timedelta(days=5)).strftime("%Y-%m-%d"),
                    "expiration_date_lte": (datetime.now()+timedelta(days=30)).strftime("%Y-%m-%d"),
                    "status": "active", "limit": 50}, timeout=15)
        if r.status_code == 200: return r.json().get("option_contracts", [])
    except Exception as e:
        log.error(f"Chain error: {e}")
    return []

def place_option_order(symbol, qty=1):
    return alpaca("POST", "/v2/orders", {
        "symbol": symbol, "qty": str(qty), "side": "buy",
        "type": "market", "time_in_force": "day"
    })

def close_position(symbol):
    import urllib.parse
    return alpaca("DELETE", f"/v2/positions/{urllib.parse.quote(symbol)}")

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE) as f: return json.load(f)
        except: pass
    return {"trades": [], "stats": {
        "total_trades": 0, "winners": 0, "losers": 0,
        "total_pnl": 0.0, "best_trade": 0.0, "worst_trade": 0.0
    }}

def save_memory(mem):
    with open(MEMORY_FILE, "w") as f: json.dump(mem, f, indent=2)

def log_trade(mem, symbol, direction, strike, expiry, entry, qty, reason, contract):
    t = {
        "id": datetime.utcnow().strftime("%Y%m%d%H%M"),
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        "symbol": symbol, "direction": direction, "strike": strike,
        "expiry": expiry, "contract": contract, "entry_price": entry,
        "qty": qty, "reason": reason, "exit_price": None, "pnl": None, "status": "OPEN"
    }
    mem["trades"].append(t)
    mem["stats"]["total_trades"] += 1
    save_memory(mem)
    return t

def update_exit(mem, contract, exit_price):
    for t in mem["trades"]:
        if t["contract"] == contract and t["status"] == "OPEN":
            t["exit_price"] = exit_price
            t["pnl"] = round((exit_price - t["entry_price"]) * t["qty"] * 100, 2)
            t["status"] = "CLOSED"
            s = mem["stats"]
            s["total_pnl"] = round(s["total_pnl"] + t["pnl"], 2)
            if t["pnl"] > 0:
                s["winners"] += 1
                s["best_trade"] = max(s["best_trade"], t["pnl"])
            else:
                s["losers"] += 1
                s["worst_trade"] = min(s["worst_trade"], t["pnl"])
            save_memory(mem)
            return t
    return None

def get_btc_context():
    """Get BTC context without importing btc_memory — read file directly"""
    try:
        mem_file = "/root/jarvis/btc_memory.json"
        if os.path.exists(mem_file):
            data = json.load(open(mem_file))
            prices = data.get("prices", [])
            if prices:
                last = prices[-1]
                rsi = last.get("rsi", 50)
                price = last.get("price", 0)
                mom_1h = last.get("1h", 0)
                return f"BTC ${price:,.0f} RSI:{rsi} 1h:{mom_1h:+.2f}%", rsi
    except: pass
    return "BTC data unavailable", 50

def get_shared_brain():
    """Safely read jarvis_brain without crashing on missing keys"""
    try:
        import jarvis_brain
        brain = jarvis_brain.read_brain()
        return {
            "market_mood": brain.get("market_mood", "neutral"),
            "btc_signal":  brain.get("btc_signal", "neutral"),
            "risk_level":  brain.get("risk_level", "NORMAL"),
            "range_hit":   brain.get("range_hit", False),      # ← THE FIX
            "trend":       brain.get("trend", "sideways"),
        }
    except Exception as e:
        log.warning(f"Brain read failed: {e}")
        return {
            "market_mood": "neutral", "btc_signal": "neutral",
            "risk_level": "NORMAL", "range_hit": False, "trend": "sideways"
        }

def ask_claude(symbol, price, bias, btc_ctx, mkt_ctx, open_count):
    try:
        prompt = f"""You are Jarvis, a sharp options trader. Make a precise trade decision.
SYMBOL: {symbol} @ ${price:.2f}
BIAS: {bias}
OPEN TRADES: {open_count}/{MAX_TRADES}
BTC CONTEXT: {btc_ctx}
MARKET: {mkt_ctx}
RULES: Only trade >65% conviction. Max risk ${MAX_RISK}. Prefer 7-21 DTE. If open>={MAX_TRADES} reply SKIP.
Reply ONLY: ACTION|DIRECTION|STRIKE_OFFSET|EXPIRY_DAYS|CONFIDENCE|REASON
ACTION=BUY or SKIP, DIRECTION=CALL or PUT, STRIKE_OFFSET=dollars OTM, EXPIRY_DAYS=7-21
Example: BUY|CALL|5|14|72%|RSI oversold bounce confirmed"""
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": 150,
                  "messages": [{"role": "user", "content": prompt}]}, timeout=20)
        resp = r.json()
        if "error" in resp: log.error(f"Claude: {resp['error']}"); return None
        text = resp["content"][0]["text"].strip()
        parts = text.split("|")
        if len(parts) < 6: return None
        return {
            "action": parts[0].strip().upper(),
            "direction": parts[1].strip().upper(),
            "strike_offset": float(parts[2].strip()),
            "expiry_days": int(parts[3].strip()),
            "confidence": parts[4].strip(),
            "reason": parts[5].strip()
        }
    except Exception as e:
        log.error(f"Claude error: {e}")
        return None

def find_contract(symbol, direction, price, offset, days):
    contracts = get_options_chain(symbol)
    if not contracts: return None
    target_strike = price + offset if direction == "CALL" else price - offset
    target_expiry = datetime.now() + timedelta(days=days)
    opt_type = "call" if direction == "CALL" else "put"
    best, best_score = None, float("inf")
    for c in contracts:
        if c.get("type") != opt_type: continue
        try:
            strike = float(c.get("strike_price", 0))
            exp_dt = datetime.strptime(c.get("expiration_date", ""), "%Y-%m-%d")
            score = abs(strike - target_strike) + abs((exp_dt - target_expiry).days) * 0.5
            if score < best_score: best_score = score; best = c
        except: continue
    return best

def check_exits(mem):
    for pos in get_options_positions():
        contract = pos.get("symbol")
        cur = float(pos.get("current_price", 0))
        pnl_pct = float(pos.get("unrealized_plpc", 0)) * 100
        pnl_usd = float(pos.get("unrealized_pl", 0))
        if pnl_pct >= 50:
            if close_position(contract):
                update_exit(mem, contract, cur)
                tg(f"✅ JARVIS OPTIONS — PROFIT\n{contract}\n+${pnl_usd:.2f} (+{pnl_pct:.1f}%)")
        elif pnl_pct <= -40:
            if close_position(contract):
                update_exit(mem, contract, cur)
                tg(f"🔴 JARVIS OPTIONS — STOP LOSS\n{contract}\n-${abs(pnl_usd):.2f} ({pnl_pct:.1f}%)")

def run_cycle():
    if not is_market_open():
        log.info("Market closed")
        return

    mem = load_memory()
    shared = get_shared_brain()   # ← Safe read, never crashes
    btc_ctx, rsi = get_btc_context()

    check_exits(mem)

    open_count = len(get_options_positions())
    if open_count >= MAX_TRADES:
        log.info(f"Max trades {open_count}/{MAX_TRADES}")
        return

    mood     = shared["market_mood"]
    btc_sig  = shared["btc_signal"]
    risk     = shared["risk_level"]
    range_hit = shared["range_hit"]  # ← Safe, never KeyError

    if risk == "EXTREME":
        log.info("EXTREME risk — skip")
        return

    if range_hit:
        log.info("Range hit — skip options")
        return

    mkt_ctx = f"Mood:{mood} BTC:{btc_sig} Risk:{risk}"

    if rsi < 32:        bias = "BULLISH — RSI oversold"
    elif rsi > 70:      bias = "BEARISH — RSI overbought"
    elif btc_sig == "bullish": bias = "BULLISH"
    elif btc_sig == "bearish": bias = "BEARISH"
    else:               bias = "NEUTRAL"

    for symbol in WATCHLIST:
        if open_count >= MAX_TRADES: break
        price = get_quote(symbol)
        if not price:
            log.warning(f"No price for {symbol}")
            continue
        log.info(f"Scanning {symbol} @ ${price:.2f}")
        sig = ask_claude(symbol, price, bias, btc_ctx, mkt_ctx, open_count)
        if not sig or sig["action"] == "SKIP":
            log.info(f"{symbol} SKIP")
            continue
        contract = find_contract(symbol, sig["direction"], price, sig["strike_offset"], sig["expiry_days"])
        if not contract:
            log.warning(f"No contract for {symbol}")
            continue
        csym   = contract.get("symbol")
        strike = float(contract.get("strike_price", 0))
        expiry = contract.get("expiration_date", "")
        qty    = min(5, max(1, int(MAX_RISK / 200)))
        result = place_option_order(csym, qty=qty)
        if result:
            log_trade(mem, symbol, sig["direction"], strike, expiry, 2.0, qty, sig["reason"], csym)
            open_count += 1
            tg(f"🎯 JARVIS OPTIONS\n{symbol} {sig['direction']} ${strike} exp {expiry}\nQty:{qty} Conf:{sig['confidence']}\n{sig['reason']}\nOpen:{open_count}/{MAX_TRADES}")
            log.info(f"Placed {csym}")
        time.sleep(2)

if __name__ == "__main__":
    log.info("JARVIS OPTIONS BOT ONLINE")
    tg("🎯 Jarvis Options online. Scanning SPY/QQQ/AAPL/NVDA/TSLA.")
    last_report = datetime.now().date()
    while True:
        try:
            now = datetime.now()
            if now.hour == 16 and now.minute < 30 and now.date() != last_report:
                mem = load_memory()
                s = mem["stats"]
                tg(f"📊 OPTIONS DAILY\nTrades:{s['total_trades']} W:{s['winners']} L:{s['losers']}\nP&L:${s['total_pnl']:.2f}")
                last_report = now.date()
            run_cycle()
        except Exception as e:
            log.error(f"Cycle error: {e}")
        log.info(f"Sleeping {INTERVAL}s")
        time.sleep(INTERVAL)
