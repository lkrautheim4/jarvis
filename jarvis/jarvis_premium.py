#!/usr/bin/env python3
"""
JARVIS PREMIUM SELLER
Sells cash-secured puts and covered calls.
The wheel strategy — consistent income from theta decay.

Strategy:
1. Sell CSP (cash secured put) on quality stocks at support
2. If assigned → own stock → sell covered call
3. Repeat forever — collect premium both ways
"""
import requests, json, time, logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("JARVIS_PREMIUM")

from jarvis_secrets import CLAUDE_API_KEY as CLAUDE_KEY
ALPACA_KEY    = "PKTHANGUNVFDSLLR3VXPETXRQF"
ALPACA_SECRET = "GRTDDfkCGWbZMoNSWms6uJSGvw72rHaAk1N1fvLi8EAP"
ALPACA_BASE   = "https://paper-api.alpaca.markets"
ALPACA_DATA   = "https://data.alpaca.markets"
TG_TOKEN      = __import__("jarvis_secrets").TG_TOKEN_TRADER
CHAT_ID       = "7534553840"
BRAIN_FILE    = "/root/jarvis/jarvis_premium_brain.json"
INTERVAL      = 1800  # 30 min during market hours

# Wheel strategy universe — stocks you'd be happy owning
# Key: ticker, Value: max shares to own (based on capital)
WHEEL_UNIVERSE = {
    "SOFI":  {"max_contracts": 2, "reason": "Fintech growth, high IV"},
    "PLTR":  {"max_contracts": 1, "reason": "AI/defense, momentum"},
    "AMD":   {"max_contracts": 1, "reason": "Semiconductor leader"},
    "F":     {"max_contracts": 3, "reason": "Ford, stable dividend"},
    "BAC":   {"max_contracts": 2, "reason": "Banking, Buffett owns"},
    "COIN":  {"max_contracts": 1, "reason": "Crypto exposure"},
    "RIVN":  {"max_contracts": 1, "reason": "EV, high premium"},
    "MSTR":  {"max_contracts": 1, "reason": "BTC proxy"},
}

# Premium selling rules
MAX_CAPITAL_PER_TRADE = 2000  # Max $ to secure per put
MIN_PREMIUM_PCT       = 0.01  # Min 1% premium of strike price
TARGET_DTE            = 21    # 21 days to expiry sweet spot
MAX_DTE               = 45    # Never sell more than 45 DTE
MIN_DTE               = 7     # Never sell less than 7 DTE
PROFIT_CLOSE_PCT      = 0.50  # Close at 50% profit (standard rule)
STOP_LOSS_PCT         = 2.00  # Close if loss = 2x premium collected

def tg(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": f"💰 PREMIUM\n{msg}"}, timeout=10)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def alpaca(method, path, data=None):
    hdrs = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET,
            "Content-Type": "application/json"}
    try:
        if method == "GET": r = requests.get(ALPACA_BASE+path, headers=hdrs, timeout=10)
        elif method == "POST": r = requests.post(ALPACA_BASE+path, headers=hdrs, json=data, timeout=10)
        elif method == "DELETE": r = requests.delete(ALPACA_BASE+path, headers=hdrs, timeout=10)
        if r.status_code in [200,201]: return r.json()
        log.warning(f"Alpaca {r.status_code}: {r.text[:80]}")
    except Exception as e: log.error(f"Alpaca: {e}")
    return None

def load_brain():
    try: return json.load(open(BRAIN_FILE))
    except: return {
        "positions": [],
        "closed_trades": [],
        "total_premium_collected": 0.0,
        "total_pnl": 0.0,
        "wins": 0, "losses": 0,
        "wheel_stocks": {}  # stocks currently owned from assignment
    }

def save_brain(brain):
    with open(BRAIN_FILE, 'w') as f: json.dump(brain, f, indent=2)

def is_market_open():
    c = alpaca("GET", "/v2/clock")
    return c and c.get("is_open", False)

def get_stock_price(ticker):
    try:
        r = requests.get(f"{ALPACA_DATA}/v2/stocks/{ticker}/quotes/latest",
            headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
            timeout=8)
        if r.status_code == 200:
            q = r.json().get("quote", {})
            mid = (q.get("ap", 0) + q.get("bp", 0)) / 2
            return mid if mid > 0 else None
    except: pass
    return None

def get_options_chain(ticker, option_type="put"):
    try:
        exp_min = (datetime.now() + timedelta(days=MIN_DTE)).strftime("%Y-%m-%d")
        exp_max = (datetime.now() + timedelta(days=MAX_DTE)).strftime("%Y-%m-%d")
        r = requests.get(f"{ALPACA_BASE}/v2/options/contracts",
            headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
            params={"underlying_symbols": ticker, "type": option_type,
                    "expiration_date_gte": exp_min, "expiration_date_lte": exp_max,
                    "status": "active", "limit": 50}, timeout=10)
        if r.status_code == 200:
            return r.json().get("option_contracts", [])
    except Exception as e: log.error(f"Chain {ticker}: {e}")
    return []

def find_best_put_to_sell(ticker, stock_price, max_capital):
    """Find best cash secured put — 5-10% OTM, 21 DTE target"""
    contracts = get_options_chain(ticker, "put")
    if not contracts: return None

    target_strike = stock_price * 0.92  # 8% below current price
    target_exp = datetime.now() + timedelta(days=TARGET_DTE)

    best = None
    best_score = 0

    for c in contracts:
        try:
            strike = float(c.get("strike_price", 0))
            exp = datetime.strptime(c.get("expiration_date",""), "%Y-%m-%d")
            dte = (exp - datetime.now()).days

            # Must be below current price (OTM put)
            if strike >= stock_price: continue
            # Must be within capital limit
            if strike * 100 > max_capital: continue
            # DTE filter
            if not (MIN_DTE <= dte <= MAX_DTE): continue

            # Score: closer to target strike + closer to target DTE
            strike_score = 1 - abs(strike - target_strike) / stock_price
            dte_score = 1 - abs(dte - TARGET_DTE) / TARGET_DTE
            score = strike_score * 0.6 + dte_score * 0.4

            if score > best_score:
                best_score = score
                best = {**c, "dte": dte, "strike_f": strike, "score": score}
        except: continue

    return best

def find_best_call_to_sell(ticker, stock_price, avg_cost):
    """Find best covered call — slightly OTM, 14-21 DTE"""
    contracts = get_options_chain(ticker, "call")
    if not contracts: return None

    # Sell call at avg_cost + 5% (profitable if called away)
    target_strike = max(stock_price * 1.05, avg_cost * 1.02)
    target_exp = datetime.now() + timedelta(days=TARGET_DTE)

    best = None
    best_score = 0

    for c in contracts:
        try:
            strike = float(c.get("strike_price", 0))
            exp = datetime.strptime(c.get("expiration_date",""), "%Y-%m-%d")
            dte = (exp - datetime.now()).days

            if strike <= stock_price: continue  # Must be OTM call
            if not (MIN_DTE <= dte <= MAX_DTE): continue
            if strike < avg_cost: continue  # Never sell below cost basis

            strike_score = 1 - abs(strike - target_strike) / stock_price
            dte_score = 1 - abs(dte - TARGET_DTE) / TARGET_DTE
            score = strike_score * 0.6 + dte_score * 0.4

            if score > best_score:
                best_score = score
                best = {**c, "dte": dte, "strike_f": strike, "score": score}
        except: continue

    return best

def get_option_premium(symbol):
    """Get current mid price of option"""
    try:
        r = requests.get(f"{ALPACA_DATA}/v2/options/quotes/latest",
            headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
            params={"symbols": symbol}, timeout=8)
        if r.status_code == 200:
            q = r.json().get("quotes", {}).get(symbol, {})
            ap = float(q.get("ap", 0)); bp = float(q.get("bp", 0))
            if ap > 0 and bp > 0: return (ap + bp) / 2
    except: pass
    return None

def ask_claude_premium(ticker, stock_price, contract, premium, strategy):
    """Ask Claude if this is a good premium selling setup"""
    try:
        strike = float(contract.get("strike_price", 0))
        dte = contract.get("dte", 21)
        pct_otm = abs(stock_price - strike) / stock_price * 100
        annual_yield = (premium / strike) * (365 / dte) * 100

        # Load context
        macro = {}; cb = {}
        try:
            macro = json.load(open("/root/jarvis/jarvis_macro.json"))
            cb = json.load(open("/root/jarvis/jarvis_central_brain.json"))
        except: pass

        regime = macro.get("regime","UNKNOWN")
        fg = cb.get("fear_greed", 50)
        vix = macro.get("vix",{}).get("value",0)

        prompt = f"""You are JARVIS, expert options premium seller. Evaluate this trade.

STRATEGY: {strategy}
TICKER: {ticker} @ ${stock_price:.2f}
CONTRACT: {'PUT' if strategy=='CSP' else 'CALL'} ${strike:.2f} exp {dte}d
PREMIUM: ${premium:.2f}/share = ${premium*100:.0f} total
OTM: {pct_otm:.1f}% out of the money
ANNUALIZED YIELD: {annual_yield:.1f}%

MARKET CONTEXT:
Regime: {regime} | VIX: {vix:.1f} | Fear&Greed: {fg}

PREMIUM SELLING RULES:
- CSP: Good when F&G < 40 (fear = stocks at support, fat premium)
- Covered Call: Good when stock extended, collect premium + potential exit
- Need 1%+ premium of strike price to be worth it
- 21 DTE is sweet spot — enough premium, not too much theta risk
- VIX > 20 = more premium available
- Never sell put if bearish trend, earnings within 14 days

Is this a good trade? Reply:
VERDICT: SELL or SKIP
CONFIDENCE: [%]
REASON: [one sentence]
RISK: [main risk to watch]"""

        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": 150,
                  "messages": [{"role": "user", "content": prompt}]}, timeout=15)
        d = r.json()
        if "error" in d: return None
        text = d["content"][0]["text"].strip()
        lines = {l.split(":")[0].strip(): ":".join(l.split(":")[1:]).strip()
                 for l in text.split("\n") if ":" in l}
        return {
            "verdict": lines.get("VERDICT","SKIP"),
            "confidence": int(lines.get("CONFIDENCE","0").replace("%","")),
            "reason": lines.get("REASON",""),
            "risk": lines.get("RISK","")
        }
    except Exception as e:
        log.error(f"Claude premium: {e}")
        return None

def sell_option(symbol, qty=1, side="sell"):
    """Sell an option contract"""
    return alpaca("POST", "/v2/orders", {
        "symbol": symbol, "qty": str(qty), "side": side,
        "type": "market", "time_in_force": "day"
    })

# Symbols we've already submitted a 50%-profit close for. The Alpaca position
# can linger for a poll or two until the buy-back fills, so without this guard
# the loop re-submits the close order, re-counts the win, and re-sends the alert
# every cycle. Marked only on a SUCCESSFUL submit; a failed close is retried.
_closed_recently = {}

def check_existing_positions(brain):
    """Check open premium positions for profit taking"""
    positions = alpaca("GET", "/v2/positions") or []
    for pos in positions:
        sym = pos.get("symbol","")
        # Only check option positions
        if len(sym) < 10: continue

        pnl_pct = float(pos.get("unrealized_plpc",0)) * 100
        pnl_usd = float(pos.get("unrealized_pl",0))
        cur_price = float(pos.get("current_price",0))

        # For SHORT options (sold), profit shows as positive when option loses value
        # 50% profit close — standard wheel rule
        if pnl_pct >= 50:
            if time.time() - _closed_recently.get(sym, 0) < 7200:
                continue  # close already submitted recently — don't double-close/count/alert
            result = alpaca("POST", "/v2/orders", {
                "symbol": sym, "qty": pos.get("qty","1"),
                "side": "buy",  # buy back to close short
                "type": "market", "time_in_force": "day"
            })
            if result:
                _closed_recently[sym] = time.time()
                brain["wins"] += 1
                brain["total_pnl"] += pnl_usd
                tg(f"✅ CLOSED AT 50% PROFIT\n{sym}\n+${pnl_usd:.0f}")
                log.info(f"Closed {sym} at 50% profit ${pnl_usd:+.0f}")

def scan_wheel_opportunities(brain):
    """Scan for new premium selling opportunities"""
    if not is_market_open():
        log.info("Market closed"); return

    # Load context
    macro = {}
    try: macro = json.load(open("/root/jarvis/jarvis_macro.json"))
    except: pass

    regime = macro.get("regime","UNKNOWN")
    fg = macro.get("fear_greed",{}).get("current",50)
    vix = macro.get("vix",{}).get("value",0)

    # Check earnings blacklist
    blacklist = []
    try:
        earnings = json.load(open("/root/jarvis/jarvis_earnings.json"))
        blacklist = earnings.get("critical",[]) + earnings.get("high_risk",[])
    except: pass

    log.info(f"Scanning wheel universe | Regime:{regime} VIX:{vix:.1f} F&G:{fg}")

    for ticker, config in WHEEL_UNIVERSE.items():
        if ticker in blacklist:
            log.info(f"SKIP {ticker} — earnings risk")
            continue

        # Check if we already own this stock (wheel step 2 — sell covered call)
        owned = brain.get("wheel_stocks",{}).get(ticker)

        stock_price = get_stock_price(ticker)
        if not stock_price:
            log.debug(f"No price for {ticker}")
            continue

        max_capital = MAX_CAPITAL_PER_TRADE * config["max_contracts"]

        if owned:
            # STEP 2: We own stock — sell covered call
            avg_cost = owned.get("avg_cost", stock_price)
            contract = find_best_call_to_sell(ticker, stock_price, avg_cost)
            if not contract: continue

            strategy = "COVERED_CALL"
            premium = get_option_premium(contract.get("symbol",""))
            if not premium: continue

            premium_pct = premium / stock_price * 100
            if premium_pct < MIN_PREMIUM_PCT * 100:
                log.info(f"SKIP {ticker} CC — premium too low {premium_pct:.2f}%")
                continue

        else:
            # STEP 1: Sell cash secured put
            contract = find_best_put_to_sell(ticker, stock_price, max_capital)
            if not contract: continue

            strategy = "CSP"
            premium = get_option_premium(contract.get("symbol",""))
            if not premium:
                log.debug(f"No premium data for {ticker}")
                continue

            strike = float(contract.get("strike_price",0))
            premium_pct = premium / strike * 100
            if premium_pct < MIN_PREMIUM_PCT * 100:
                log.info(f"SKIP {ticker} CSP — premium too low {premium_pct:.2f}%")
                continue

        # Ask Claude
        decision = ask_claude_premium(ticker, stock_price, contract, premium, strategy)
        if not decision or decision["verdict"] != "SELL":
            log.info(f"SKIP {ticker} — Claude: {decision.get('reason','') if decision else 'no response'}")
            continue

        if decision["confidence"] < 65:
            log.info(f"SKIP {ticker} — confidence {decision['confidence']}% too low")
            continue

        # Execute
        contract_sym = contract.get("symbol","")
        result = sell_option(contract_sym)
        if result:
            total_premium = premium * 100
            brain["total_premium_collected"] += total_premium
            brain["positions"].append({
                "ts": datetime.now().isoformat(),
                "ticker": ticker,
                "contract": contract_sym,
                "strategy": strategy,
                "strike": float(contract.get("strike_price",0)),
                "premium": premium,
                "total_premium": total_premium,
                "dte": contract.get("dte",21),
                "stock_price": stock_price,
                "confidence": decision["confidence"]
            })
            save_brain(brain)

            msg = (f"{'📉 SOLD PUT' if strategy=='CSP' else '📈 SOLD CALL'}\n"
                   f"{ticker} @ ${stock_price:.2f}\n"
                   f"Strike: ${float(contract.get('strike_price',0)):.2f} "
                   f"({contract.get('dte',21)}d)\n"
                   f"Premium: ${premium:.2f}/share = ${total_premium:.0f} total\n"
                   f"Confidence: {decision['confidence']}%\n"
                   f"Reason: {decision['reason']}\n"
                   f"Risk: {decision['risk']}\n"
                   f"Total collected: ${brain['total_premium_collected']:+.0f}")
            tg(msg)
            log.info(f"SOLD {strategy} {ticker} ${float(contract.get('strike_price',0)):.2f} "
                    f"premium=${total_premium:.0f}")

def main():
    log.info("JARVIS PREMIUM SELLER ONLINE")
    tg("💰 PREMIUM SELLER ONLINE\nWheel strategy — selling puts + covered calls\nCollecting theta decay every day")

    brain = load_brain()
    while True:
        try:
            check_existing_positions(brain)
            scan_wheel_opportunities(brain)
            save_brain(brain)
        except Exception as e:
            log.error(f"Premium cycle: {e}")
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
