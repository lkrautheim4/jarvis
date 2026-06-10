#!/usr/bin/env python3
"""
JARVIS OPTIONS COACH
Teaches options trading while scanning for real setups.
Combines education + live market scanning + signals from all bots.
Talks to master bot via Telegram.
"""
import requests, json, time, logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("OPTIONS_COACH")

from jarvis_secrets import CLAUDE_API_KEY as CLAUDE_KEY
ALPACA_KEY    = "PKTHANGUNVFDSLLR3VXPETXRQF"
ALPACA_SECRET = "GRTDDfkCGWbZMoNSWms6uJSGvw72rHaAk1N1fvLi8EAP"
ALPACA_BASE   = "https://paper-api.alpaca.markets"
ALPACA_DATA   = "https://data.alpaca.markets"
TG_TOKEN      = __import__("jarvis_secrets").TG_TOKEN_TRADER
CHAT_ID       = "7534553840"
BRAIN_FILE    = "/root/jarvis/jarvis_options_coach.json"
INTERVAL      = 7200  # 2 hours to save API credits

# Learning universe — perfect for $2-5k budget
LEARN_UNIVERSE = {
    # Premium selling targets (low price, high IV)
    "SOFI":  {"price_range": [8,15],  "strategy": "wheel", "reason": "High IV, fintech growth"},
    "PLTR":  {"price_range": [25,40], "strategy": "wheel", "reason": "AI/defense, volatile"},
    "F":     {"price_range": [9,14],  "strategy": "wheel", "reason": "Stable, dividend"},
    "BAC":   {"price_range": [35,45], "strategy": "wheel", "reason": "Banking, Buffett owns"},
    "RIVN":  {"price_range": [8,15],  "strategy": "wheel", "reason": "EV, high premium"},
    # Momentum plays (buy calls when Beast signals)
    "NVDA":  {"price_range": [800,1200], "strategy": "momentum", "reason": "AI leader"},
    "AMD":   {"price_range": [100,200],  "strategy": "momentum", "reason": "Semiconductor"},
    "COIN":  {"price_range": [150,300],  "strategy": "both",     "reason": "Crypto proxy"},
    "TSLA":  {"price_range": [150,300],  "strategy": "momentum", "reason": "EV, volatile"},
    "SPY":   {"price_range": [500,600],  "strategy": "both",     "reason": "Market ETF"},
}

def tg(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg[:4000]}, timeout=10)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

# Once-per-day-per-(ticker,strategy) gate. The scan runs every 2h and the same
# setup stays "VERDICT: TRADE", so without this the identical coach alert re-fires
# multiple times a day. Persisted to disk so a restart can't re-spam.
COACH_SENT_FILE = "/root/jarvis/jarvis_options_coach_sent.json"
def already_coached_today(key):
    import os
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        state = json.load(open(COACH_SENT_FILE)) if os.path.exists(COACH_SENT_FILE) else {}
    except Exception:
        state = {}
    if state.get(key) == today:
        return True
    state[key] = today
    state = {k: v for k, v in state.items() if v == today}
    try:
        json.dump(state, open(COACH_SENT_FILE, "w"))
    except Exception:
        pass
    return False

def alpaca_get(path):
    hdrs = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
    try:
        r = requests.get(ALPACA_BASE+path, headers=hdrs, timeout=10)
        if r.status_code == 200: return r.json()
    except: pass
    return None

def get_stock_price(ticker):
    try:
        r = requests.get(f"{ALPACA_DATA}/v2/stocks/{ticker}/quotes/latest",
            headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
            timeout=8)
        if r.status_code == 200:
            q = r.json().get("quote", {})
            return (q.get("ap", 0) + q.get("bp", 0)) / 2
    except: pass
    return None

def get_options_chain(ticker, option_type="put", min_dte=7, max_dte=45):
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/options/contracts",
            headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
            params={"underlying_symbols": ticker, "type": option_type,
                    "expiration_date_gte": (datetime.now()+timedelta(days=min_dte)).strftime("%Y-%m-%d"),
                    "expiration_date_lte": (datetime.now()+timedelta(days=max_dte)).strftime("%Y-%m-%d"),
                    "status": "active", "limit": 20}, timeout=10)
        if r.status_code == 200: return r.json().get("option_contracts", [])
    except: pass
    return []

def get_iv(ticker):
    """Estimate IV from options chain"""
    try:
        contracts = get_options_chain(ticker, "call", 25, 35)
        if contracts:
            ivs = [float(c.get("implied_volatility", 0)) for c in contracts if c.get("implied_volatility")]
            if ivs: return round(sum(ivs)/len(ivs)*100, 1)
    except: pass
    return None

def load_all_context():
    """Load context from all JARVIS bots"""
    ctx = {}
    files = {
        "macro": "/root/jarvis/jarvis_macro.json",
        "beast": "/root/jarvis/jarvis_beast_brain.json",
        "congress": "/root/jarvis/jarvis_congress.json",
        "earnings": "/root/jarvis/jarvis_earnings.json",
        "brain": "/root/jarvis/jarvis_central_brain.json",
    }
    for name, path in files.items():
        try: ctx[name] = json.load(open(path))
        except: ctx[name] = {}
    return ctx

def analyze_setup(ticker, stock_price, strategy, ctx):
    """Ask Claude to analyze an options setup with full context"""
    try:
        macro = ctx.get("macro", {})
        earnings = ctx.get("earnings", {})
        congress = ctx.get("congress", {})
        beast = ctx.get("beast", {})

        regime = macro.get("regime", "UNKNOWN")
        vix = macro.get("vix", {}).get("value", 0)
        fg = ctx.get("brain", {}).get("fear_greed", 50)

        # Earnings check
        risk_map = earnings.get("risk_map", {})
        earnings_risk = risk_map.get(ticker, {}).get("risk", "LOW")
        earnings_date = risk_map.get(ticker, {}).get("date", "unknown")

        # Congress signal
        hot_tickers = congress.get("hot_tickers", {})
        congress_signal = "YES — politicians buying" if ticker in hot_tickers else "No signal"

        # Beast signal
        beast_trades = beast.get("trades", [])
        beast_signal = next((t for t in reversed(beast_trades) if t.get("ticker")==ticker), None)

        # Get IV
        iv = get_iv(ticker)
        iv_str = f"{iv}% — {'HIGH sell premium' if iv and iv > 40 else 'LOW buy options' if iv and iv < 25 else 'NORMAL'}" if iv else "unknown"

        prompt = f"""You are JARVIS OPTIONS COACH teaching Lenny options trading.
Lenny is a complete beginner with $2,000-5,000 budget. Goal: income + occasional swings.

SETUP TO ANALYZE:
Ticker: {ticker} @ ${stock_price:.2f}
Strategy: {strategy.upper()}
IV: {iv_str}
Earnings: {earnings_risk} risk (date: {earnings_date})
Congress buying: {congress_signal}
Beast signal: {beast_signal.get('reason','None') if beast_signal else 'None'}

MARKET CONTEXT:
Regime: {regime} | VIX: {vix:.1f} | Fear&Greed: {fg}

TASK: Analyze this as both a TEACHER and a TRADER.
1. Is this a good options setup right now?
2. What specific trade would you recommend?
3. Teach one key concept relevant to this setup
4. What's the exact risk/reward?

Reply in this format:
VERDICT: TRADE or SKIP or WATCH
TRADE: [Buy/Sell] [Call/Put] ${strike} exp [date] for $[premium]
TEACHING: [one key concept explained simply]
RISK: $[max loss] | REWARD: $[max gain] | PROB: [%]
REASON: [2 sentences max]"""

        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": 400,
                  "messages": [{"role": "user", "content": prompt}]}, timeout=20)
        d = r.json()
        if "error" in d: return None
        return d["content"][0]["text"].strip()
    except Exception as e:
        log.error(f"Claude options coach: {e}")
        return None

def format_options_alert(ticker, price, analysis, strategy):
    """Format options coaching alert"""
    if not analysis: return None

    strategy_emoji = {
        "wheel": "🎡",
        "momentum": "🚀",
        "both": "⚡"
    }.get(strategy, "📊")

    lines = [
        f"{strategy_emoji} OPTIONS COACH — {ticker}",
        f"{'='*24}",
        f"${price:.2f} | Strategy: {strategy.upper()}",
        f"{'='*24}",
        analysis,
        f"{'='*24}",
        f"Paper trade this on Alpaca to practice",
        f"Text LEARN {ticker} for deeper analysis"
    ]
    return "\n".join(lines)

def daily_lesson():
    """Send a daily options education message"""
    lessons = [
        {
            "title": "📚 LESSON: Theta Decay",
            "content": ("Options lose value every day just from time passing.\n"
                       "This is called Theta decay.\n\n"
                       "Example: You buy a $200 call option\n"
                       "It might lose $5-10 per day in value\n"
                       "Even if stock doesn't move.\n\n"
                       "KEY INSIGHT: Time is your enemy when buying options.\n"
                       "Time is your FRIEND when selling options.\n"
                       "This is why selling premium (the wheel) is so powerful.")
        },
        {
            "title": "📚 LESSON: IV Crush",
            "content": ("IV = Implied Volatility = how expensive options are.\n\n"
                       "Before earnings: IV spikes (options get expensive)\n"
                       "After earnings: IV crashes (options get cheap)\n\n"
                       "Example: NVDA before earnings\n"
                       "Option costs $500 due to high IV\n"
                       "NVDA beats earnings, stock up 5%\n"
                       "Option might still LOSE value due to IV crush!\n\n"
                       "KEY INSIGHT: Never buy options right before earnings.\n"
                       "Sell options before earnings to collect the IV premium.")
        },
        {
            "title": "📚 LESSON: The Wheel Strategy",
            "content": ("Step 1: Pick a stock you'd be happy owning\n"
                       "Step 2: Sell a CASH SECURED PUT below current price\n"
                       "Step 3: Collect premium immediately\n\n"
                       "Two outcomes:\n"
                       "A) Stock stays above strike = keep premium, repeat\n"
                       "B) Stock drops below strike = you buy it at your price\n\n"
                       "If assigned:\n"
                       "Step 4: Sell COVERED CALL above your cost\n"
                       "Step 5: Collect more premium\n"
                       "Step 6: Repeat forever\n\n"
                       "KEY INSIGHT: You get paid to wait for your price.")
        },
        {
            "title": "📚 LESSON: Delta — Your Directional Bet",
            "content": ("Delta tells you how much your option moves\n"
                       "for every $1 the stock moves.\n\n"
                       "Delta 0.50 = option moves $0.50 per $1 stock move\n"
                       "Delta 0.25 = cheaper, less likely to profit\n"
                       "Delta 0.75 = expensive, more likely to profit\n\n"
                       "For buying calls: target Delta 0.40-0.60\n"
                       "Far OTM calls (Delta 0.10) = lottery tickets\n"
                       "Deep ITM calls (Delta 0.90) = expensive, less leverage\n\n"
                       "KEY INSIGHT: ATM options (Delta ~0.50) give best\n"
                       "balance of cost vs probability of profit.")
        },
    ]

    import random
    lesson = random.choice(lessons)
    tg(f"{lesson['title']}\n{'='*24}\n{lesson['content']}")

def run_scan():
    """Scan for options setups and teach"""
    is_market = alpaca_get("/v2/clock")
    if not (is_market and is_market.get("is_open")):
        log.info("Market closed")
        return

    ctx = load_all_context()
    regime = ctx.get("macro", {}).get("regime", "UNKNOWN")
    earnings_blacklist = ctx.get("earnings", {}).get("critical", []) + ctx.get("earnings", {}).get("high_risk", [])

    log.info(f"Scanning options universe | Regime: {regime}")

    best_setups = []
    # Limit to 3 tickers per scan to save API credits
    import random
    tickers_to_scan = random.sample(list(LEARN_UNIVERSE.keys()), min(3, len(LEARN_UNIVERSE)))
    scan_universe = {t: LEARN_UNIVERSE[t] for t in tickers_to_scan}
    for ticker, config in scan_universe.items():
        if ticker in earnings_blacklist:
            log.info(f"SKIP {ticker} — earnings risk")
            continue

        price = get_stock_price(ticker)
        if not price: continue

        strategy = config["strategy"]
        analysis = analyze_setup(ticker, price, strategy, ctx)
        if not analysis: continue

        if "VERDICT: TRADE" in analysis:
            best_setups.append((ticker, price, analysis, strategy))
            log.info(f"TRADE setup found: {ticker}")
        elif "VERDICT: WATCH" in analysis:
            log.info(f"WATCH: {ticker}")

    # Send top setups (once per ticker+strategy per day)
    for ticker, price, analysis, strategy in best_setups[:2]:
        if already_coached_today(f"{ticker}_{strategy}"):
            log.info(f"DEDUP: {ticker} {strategy} already coached today — skipping")
            continue
        msg = format_options_alert(ticker, price, analysis, strategy)
        if msg: tg(msg)
        time.sleep(2)

    if not best_setups:
        log.info("No options setups found this scan")

def main():
    log.info("JARVIS OPTIONS COACH ONLINE")
    tg("🎓 OPTIONS COACH ONLINE\nScanning for setups + teaching as we go\nText LEARN <ticker> for analysis\nDaily lessons firing automatically")

    brain = {"scans": 0, "lessons_sent": 0, "setups_found": 0}
    lesson_hour = -1

    while True:
        try:
            now = datetime.now()
            edt_hour = (datetime.utcnow().hour - 4) % 24

            # Send daily lesson at 8am EDT
            if edt_hour == 8 and now.hour != lesson_hour:
                daily_lesson()
                lesson_hour = now.hour
                brain["lessons_sent"] += 1

            # Scan every 30 min during market hours
            if 9 <= edt_hour <= 16:
                run_scan()
                brain["scans"] += 1

            json.dump(brain, open(BRAIN_FILE, 'w'), indent=2)

        except Exception as e:
            log.error(f"Coach cycle: {e}")
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
