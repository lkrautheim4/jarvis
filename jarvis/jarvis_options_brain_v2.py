#!/usr/bin/env python3
"""
JARVIS OPTIONS BRAIN v2
Tracks every options trade with full context.
Learns what works. Gets smarter every trade.
Sends plain English signals to Telegram.

UPGRADES v2:
  1. Strike selection logic (delta 0.35-0.50, OTM%, breakeven)
  2. Catalyst tagging at entry
  3. Real-time WATCH price alert (MSFT $450 etc)
  4. Per-leg P&L tracking with WIN/LOSS labels
  5. Theta decay warning (DTE<=2 + delta<0.20)
  6. Hardcoded exit rules (2x take, 50% stop, roll trigger)
"""
import requests, json, time, logging, math
from datetime import datetime, timedelta
from collections import defaultdict
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("OPTIONS_BRAIN")

from jarvis_secrets import CLAUDE_API_KEY as CLAUDE_KEY
ALPACA_KEY    = "PKTHANGUNVFDSLLR3VXPETXRQF"
ALPACA_SECRET = "GRTDDfkCGWbZMoNSWms6uJSGvw72rHaAk1N1fvLi8EAP"
ALPACA_BASE   = "https://paper-api.alpaca.markets"
ALPACA_DATA   = "https://data.alpaca.markets"
TG_TOKEN      = __import__("jarvis_secrets").TG_TOKEN_TRADER
CHAT_ID       = "7534553840"
BRAIN_FILE    = "/root/jarvis/jarvis_options_brain.json"
WATCH_FILE    = "/root/jarvis/jarvis_options_watch.json"
INTERVAL      = 3600

# ── UPGRADE 6: EXIT RULES (hardcoded) ─────────────────────────────────────────
EXIT_RULES = {
    "take_profit_multiplier": 2.0,    # take 50% off at 2x premium paid
    "stop_loss_pct":          0.50,   # stop at 50% loss of premium
    "roll_delta_threshold":   0.20,   # roll position if delta drops below 0.20
    "theta_warning_dte":      2,      # warn when DTE <= 2
    "theta_warning_delta":    0.20,   # warn when delta also < 0.20
}

# ── UPGRADE 2: CATALYST TYPES ──────────────────────────────────────────────────
CATALYSTS = [
    "technical_breakout",   # price breaking key level (e.g. MSFT $450)
    "earnings_play",        # positioning around earnings
    "macro_event",          # Fed, CPI, jobs report
    "congress_signal",      # politicians buying
    "btc_momentum",         # crypto proxy play
    "sector_rotation",      # money moving into sector
    "trump_mention",        # Truth Social mention — check POLICY vs HYPE
    "manual",               # Lenny called it himself
]

UNIVERSE = {
    "SOFI": {"type":"wheel",    "sector":"fintech", "iv_rank":"high",    "budget":1800,  "reason":"Fintech growth, high IV"},
    "F":    {"type":"wheel",    "sector":"auto",    "iv_rank":"med",     "budget":1800,  "reason":"Ford, stable, dividend"},
    "RIVN": {"type":"wheel",    "sector":"ev",      "iv_rank":"high",    "budget":800,   "reason":"EV, massive premium"},
    "AAL":  {"type":"wheel",    "sector":"airlines","iv_rank":"high",    "budget":1500,  "reason":"Airlines, always high IV"},
    "MSTR": {"type":"wheel",    "sector":"crypto",  "iv_rank":"extreme", "budget":7900,  "reason":"BTC proxy, wild premium"},
    "BAC":  {"type":"wheel",    "sector":"banking", "iv_rank":"med",     "budget":5100,  "reason":"Buffett stock, stable"},
    "SPY":  {"type":"momentum", "sector":"etf",     "iv_rank":"low",     "budget":37800, "reason":"Market ETF, most liquid"},
    "QQQ":  {"type":"momentum", "sector":"etf",     "iv_rank":"low",     "budget":73800, "reason":"Tech ETF, liquid"},
    "NVDA": {"type":"momentum", "sector":"tech",    "iv_rank":"med",     "budget":22100, "reason":"AI leader"},
    "TSLA": {"type":"momentum", "sector":"ev",      "iv_rank":"high",    "budget":44000, "reason":"Volatile, big moves"},
    "AAPL": {"type":"momentum", "sector":"tech",    "iv_rank":"low",     "budget":14800, "reason":"Stable, liquid"},
    "PLTR": {"type":"both",     "sector":"tech",    "iv_rank":"high",    "budget":15700, "reason":"AI/defense"},
    "AMD":  {"type":"both",     "sector":"tech",    "iv_rank":"med",     "budget":51100, "reason":"Semiconductor"},
    "COIN": {"type":"both",     "sector":"crypto",  "iv_rank":"extreme", "budget":8900,  "reason":"Crypto proxy"},
    "META": {"type":"both",     "sector":"tech",    "iv_rank":"med",     "budget":63400, "reason":"Social, momentum"},
    "MSFT": {"type":"momentum", "sector":"tech",    "iv_rank":"low",     "budget":43000, "reason":"$450 key level, institutional, AI cloud"},
}

hdrs = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

def tg(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg[:4000]}, timeout=10)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def load_brain():
    try: return json.load(open(BRAIN_FILE))
    except: return {
        "trades": [],
        "stats": {
            "total": 0, "wins": 0, "losses": 0, "open": 0,
            "total_pnl": 0.0, "total_premium": 0.0,
            "by_ticker": {}, "by_strategy": {}, "by_signal": {},
            "by_regime": {}, "by_fg_range": {}, "by_iv_level": {},
            "by_catalyst": {},   # ADD 2
        },
        "patterns": [],
        "daily_brief_sent": ""
    }

def save_brain(brain):
    with open(BRAIN_FILE, 'w') as f: json.dump(brain, f, indent=2)

# ── UPGRADE 3: WATCH PRICE ALERTS ─────────────────────────────────────────────
def load_watches():
    try: return json.load(open(WATCH_FILE))
    except: return {}   # {"MSFT": {"price": 450, "direction": "above", "label": "breakout"}}

def save_watches(watches):
    with open(WATCH_FILE, 'w') as f: json.dump(watches, f, indent=2)

def add_watch(ticker, price, direction="above", label=""):
    watches = load_watches()
    watches[ticker.upper()] = {
        "price": float(price),
        "direction": direction,
        "label": label,
        "set_ts": datetime.now().isoformat(),
        "triggered": False
    }
    save_watches(watches)
    tg(f"👁 WATCH SET: {ticker.upper()} {direction} ${price}\n"
       f"Label: {label or 'none'}\n"
       f"I'll alert you the moment it crosses.")
    log.info(f"Watch set: {ticker} {direction} ${price}")

def check_watches():
    watches = load_watches()
    if not watches: return
    changed = False
    for ticker, w in watches.items():
        if w.get("triggered"): continue
        price = get_price(ticker)
        if not price: continue
        hit = (w["direction"] == "above" and price >= w["price"]) or \
              (w["direction"] == "below" and price <= w["price"])
        if hit:
            label = w.get("label","")
            tg(f"🔔 PRICE ALERT TRIGGERED\n"
               f"{'='*26}\n"
               f"{ticker} is NOW ${price:.2f}\n"
               f"Watch level: {w['direction']} ${w['price']}\n"
               f"{('Label: ' + label) if label else ''}\n"
               f"{'='*26}\n"
               f"⚡ TIME TO EXECUTE — level crossed\n"
               f"Text OPTIONS to see current setups")
            w["triggered"] = True
            w["triggered_price"] = price
            w["triggered_ts"] = datetime.now().isoformat()
            changed = True
            log.info(f"WATCH TRIGGERED: {ticker} @ ${price}")
    if changed: save_watches(watches)

# ── UPGRADE 1: STRIKE SELECTION LOGIC ─────────────────────────────────────────
def select_best_strike(contracts, stock_price, option_type, target_dte=21):
    """
    Pick the best contract using:
    - Delta sweet spot: 0.35-0.50 for directional calls
    - OTM distance filter
    - Breakeven auto-calculated
    Returns best contract + analysis dict
    """
    if not contracts: return None, {}
    best = None
    best_score = 0
    best_analysis = {}

    for c in contracts:
        try:
            strike = float(c.get("strike_price", 0))
            exp_str = c.get("expiration_date", "")
            exp = datetime.strptime(exp_str, "%Y-%m-%d")
            dte = (exp - datetime.now()).days
            if dte < 7 or dte > 45: continue

            delta = abs(float(c.get("delta", 0) or 0))
            iv    = float(c.get("implied_volatility", 0) or 0) * 100

            # DTE score — prefer target_dte
            dte_score = max(0, 1 - abs(dte - target_dte) / target_dte)

            if option_type == "call_buy":
                pct_otm = (strike - stock_price) / stock_price * 100
                # Delta sweet spot 0.35-0.50
                if delta > 0:
                    delta_in_zone = 0.35 <= delta <= 0.50
                    delta_score   = 1.0 if delta_in_zone else max(0, 1 - abs(delta - 0.42) / 0.42)
                else:
                    # No delta available — use OTM % as proxy (2-8% OTM ≈ 0.35-0.50 delta)
                    delta_score = 1.0 if 2 <= pct_otm <= 8 else max(0, 1 - abs(pct_otm - 5) / 5)

                if pct_otm < 0 or pct_otm > 15: continue  # skip ITM or too far OTM

                score = dte_score * 0.4 + delta_score * 0.6

            elif option_type == "put_sell":
                pct_otm = (stock_price - strike) / stock_price * 100
                if 5 <= pct_otm <= 15:
                    strike_score = max(0, 1 - abs(pct_otm - 8) / 8)
                    score = dte_score * 0.5 + strike_score * 0.5
                else:
                    continue
            else:
                continue

            if score > best_score:
                best_score = score
                best = c
                # ── BREAKEVEN CALCULATION ──────────────────────────────────
                premium_est = get_option_mid_price(c.get("symbol","")) or 0
                if option_type == "call_buy":
                    breakeven = strike + premium_est
                    max_loss  = premium_est * 100
                else:
                    breakeven = strike - premium_est
                    max_loss  = (strike - premium_est) * 100

                best_analysis = {
                    "strike":      strike,
                    "dte":         dte,
                    "exp_date":    exp_str,
                    "delta":       round(delta, 2),
                    "iv_pct":      round(iv, 1),
                    "pct_otm":     round(pct_otm, 1),
                    "breakeven":   round(breakeven, 2),
                    "premium":     round(premium_est, 2),
                    "max_loss":    round(max_loss, 2),
                    "delta_note":  "in sweet spot" if 0.35 <= delta <= 0.50 else f"delta {delta:.2f} outside 0.35-0.50",
                    "contract":    c.get("symbol",""),
                }
        except Exception as e:
            continue

    return best, best_analysis

# ── UPGRADE 4: P&L TRACKING PER LEG ───────────────────────────────────────────
def log_trade_entry(brain, ticker, strategy, contract_symbol, strike,
                    premium, dte, iv, score, signals, stock_price,
                    ctx, catalyst="manual", label="", analysis=None):
    """Log a trade entry with full context and label for WIN/LOSS tracking"""
    trade_id = f"{ticker}_{label or datetime.now().strftime('%H%M%S')}"
    macro  = ctx.get("macro", {})
    brain_data = ctx.get("brain", {})

    trade = {
        "id":              trade_id,
        "label":           label,
        "ts":              datetime.now().isoformat(),
        "ticker":          ticker,
        "strategy":        strategy,
        "contract_symbol": contract_symbol,
        "strike":          strike,
        "entry_premium":   premium,       # price paid/collected per share
        "exit_premium":    None,          # filled on close
        "realized_pnl":    None,          # filled on close (per contract = *100)
        "contracts":       1,             # default 1, update manually
        "dte_at_entry":    dte,
        "iv_at_entry":     iv,
        "score":           score,
        "signals":         signals,
        "catalyst":        catalyst,      # ADD 2
        "stock_price":     stock_price,
        "regime":          macro.get("regime", "?"),
        "fg_at_entry":     brain_data.get("fear_greed", 50),
        "vix_at_entry":    macro.get("vix", {}).get("value", 15),
        "btc_signal":      brain_data.get("btc_signal", "neutral"),
        "status":          "open",
        "result":          None,
        "closed_ts":       None,
        "exit_rule_hit":   None,          # which exit rule triggered
        # Strike analysis (ADD 1)
        "analysis":        analysis or {},
    }

    brain["trades"].append(trade)
    s = brain["stats"]
    s["total"] += 1
    s["open"]  += 1

    # Stats by bucket
    for key, bucket in [
        ("by_ticker",   ticker),
        ("by_strategy", strategy),
        ("by_regime",   trade["regime"]),
        ("by_catalyst", catalyst),        # ADD 2
    ]:
        if bucket not in s[key]: s[key][bucket] = {"total":0,"wins":0,"pnl":0}
        s[key][bucket]["total"] += 1

    fg = trade["fg_at_entry"]
    fg_bucket = "fear" if fg < 40 else "greed" if fg > 60 else "neutral"
    if fg_bucket not in s["by_fg_range"]: s["by_fg_range"][fg_bucket] = {"total":0,"wins":0,"pnl":0}
    s["by_fg_range"][fg_bucket]["total"] += 1

    log.info(f"Trade logged: {trade_id} | catalyst={catalyst}")
    return brain, trade_id

def close_trade(brain, label_or_id, exit_premium, result="WIN"):
    """
    Close a trade by label. Calculates realized P&L.
    result: "WIN" or "LOSS"
    """
    trade = next((t for t in brain["trades"]
                  if (t.get("label") == label_or_id or t.get("id") == label_or_id)
                  and t.get("status") == "open"), None)
    if not trade:
        tg(f"⚠️ No open trade found with label: {label_or_id}")
        return brain

    entry = trade["entry_premium"]
    contracts = trade.get("contracts", 1)

    if trade["strategy"] == "call_buy":
        pnl_per_share = exit_premium - entry
    else:  # put_sell
        pnl_per_share = entry - exit_premium

    realized_pnl = round(pnl_per_share * 100 * contracts, 2)

    trade["exit_premium"]  = exit_premium
    trade["realized_pnl"]  = realized_pnl
    trade["status"]        = "closed"
    trade["result"]        = result
    trade["closed_ts"]     = datetime.now().isoformat()

    s = brain["stats"]
    s["open"]      = max(0, s["open"] - 1)
    s["total_pnl"] = round(s.get("total_pnl", 0) + realized_pnl, 2)
    if result == "WIN": s["wins"] += 1
    else:               s["losses"] += 1

    for key, bucket in [
        ("by_ticker",   trade["ticker"]),
        ("by_strategy", trade["strategy"]),
        ("by_regime",   trade.get("regime","?")),
        ("by_catalyst", trade.get("catalyst","manual")),
    ]:
        if bucket in s.get(key, {}):
            if result == "WIN": s[key][bucket]["wins"] += 1
            s[key][bucket]["pnl"] = round(s[key][bucket].get("pnl",0) + realized_pnl, 2)

    emoji = "✅" if result == "WIN" else "❌"
    tg(f"{emoji} TRADE CLOSED: {trade['ticker']}\n"
       f"Label: {label_or_id}\n"
       f"Entry: ${entry:.2f} | Exit: ${exit_premium:.2f}\n"
       f"P&L: ${realized_pnl:+.0f} ({result})\n"
       f"Catalyst: {trade.get('catalyst','?')}\n"
       f"Held {trade.get('dte_at_entry','?')}d entry DTE")

    log.info(f"Trade closed: {label_or_id} P&L=${realized_pnl:+.2f}")
    return brain

# ── UPGRADE 5: THETA DECAY WARNING ────────────────────────────────────────────
def check_theta_warnings(brain):
    """
    Alert when open trades have DTE <= 2 AND delta < 0.20
    These are dying options — exit or roll NOW
    """
    open_trades = [t for t in brain["trades"] if t.get("status") == "open"]
    if not open_trades: return

    warned = []
    for trade in open_trades:
        try:
            entry_ts  = datetime.fromisoformat(trade["ts"])
            days_held = (datetime.now() - entry_ts).days
        except (ValueError, TypeError):
            log.warning(f"Bad ts in trade {trade.get('id','?')} — skipping theta check")
            continue
        dte_entry = trade.get("dte_at_entry", 30)
        dte_now   = max(0, dte_entry - days_held)

        if dte_now > EXIT_RULES["theta_warning_dte"]: continue

        # Try to get current delta from Alpaca
        delta = 0.0
        contract_sym = trade.get("contract_symbol","")
        if contract_sym:
            try:
                r = requests.get(f"{ALPACA_DATA}/v2/options/quotes/latest",
                    headers=hdrs, params={"symbols": contract_sym}, timeout=8)
                if r.status_code == 200:
                    q = r.json().get("quotes",{}).get(contract_sym,{})
                    delta = abs(float(q.get("delta", 0) or 0))
            except: pass

        theta_danger = (dte_now <= EXIT_RULES["theta_warning_dte"] and
                        (delta < EXIT_RULES["theta_warning_delta"] or delta == 0.0))

        if theta_danger:
            label = trade.get("label") or trade.get("id","?")
            warned.append(trade["ticker"])
            tg(f"⏰ THETA WARNING — ACT NOW\n"
               f"{'='*26}\n"
               f"{trade['ticker']} | Label: {label}\n"
               f"DTE remaining: ~{dte_now} day(s)\n"
               f"Delta: {delta:.2f} (below {EXIT_RULES['theta_warning_delta']} threshold)\n"
               f"{'='*26}\n"
               f"OPTIONS:\n"
               f"1. EXIT now — take whatever you get\n"
               f"2. ROLL to next expiry if still have conviction\n"
               f"3. HOLD only if delta > 0.30 and strong momentum\n"
               f"{'='*26}\n"
               f"Theta is eating this alive. Don't bag-hold.")

    if warned:
        log.info(f"Theta warnings sent: {warned}")

# ── UPGRADE 6: EXIT RULE CHECKER ──────────────────────────────────────────────
def check_exit_rules(brain):
    """
    Check all open trades against exit rules.
    Alerts Lenny — does NOT auto-close (Lenny executes).
    Rules:
      - Take 50% off when current premium >= 2x entry premium
      - Stop loss when current premium <= 50% of entry premium (calls)
      - Roll alert when delta drops below 0.20
    """
    open_trades = [t for t in brain["trades"] if t.get("status") == "open"]
    if not open_trades: return

    for trade in open_trades:
        contract_sym = trade.get("contract_symbol","")
        if not contract_sym: continue

        current_premium = get_option_mid_price(contract_sym)
        if not current_premium: continue

        entry   = trade["entry_premium"]
        label   = trade.get("label") or trade.get("id","?")
        ticker  = trade["ticker"]
        strategy = trade["strategy"]

        # ── RULE 1: Take profit at 2x ──────────────────────────────────────
        if strategy == "call_buy":
            gain_mult = current_premium / entry if entry > 0 else 0
            if gain_mult >= EXIT_RULES["take_profit_multiplier"]:
                if trade.get("exit_rule_hit") != "take_profit":
                    trade["exit_rule_hit"] = "take_profit"
                    tg(f"🎯 TAKE PROFIT SIGNAL\n"
                       f"{'='*26}\n"
                       f"{ticker} | Label: {label}\n"
                       f"Entry: ${entry:.2f} → Now: ${current_premium:.2f}\n"
                       f"That's {gain_mult:.1f}x your money\n"
                       f"{'='*26}\n"
                       f"EXIT RULE: Sell at least 50% of position\n"
                       f"Lock in ${(current_premium-entry)*50:.0f}+ profit\n"
                       f"Let rest ride or set stop at 1.5x\n"
                       f"Text WIN {current_premium:.2f} {label} to log it")

            # ── RULE 2: Stop loss at 50% of premium paid ───────────────────
            loss_pct = (entry - current_premium) / entry if entry > 0 else 0
            if loss_pct >= EXIT_RULES["stop_loss_pct"]:
                if trade.get("exit_rule_hit") != "stop_loss":
                    trade["exit_rule_hit"] = "stop_loss"
                    tg(f"🛑 STOP LOSS SIGNAL\n"
                       f"{'='*26}\n"
                       f"{ticker} | Label: {label}\n"
                       f"Entry: ${entry:.2f} → Now: ${current_premium:.2f}\n"
                       f"Down {loss_pct*100:.0f}% — stop loss triggered\n"
                       f"{'='*26}\n"
                       f"EXIT RULE: Close this position NOW\n"
                       f"Max further loss: ${current_premium*100:.0f}\n"
                       f"Don't hope — protect capital\n"
                       f"Text LOSS {current_premium:.2f} {label} to log it")

        # ── RULE 3: Roll alert (delta too low) ─────────────────────────────
        try:
            r = requests.get(f"{ALPACA_DATA}/v2/options/quotes/latest",
                headers=hdrs, params={"symbols": contract_sym}, timeout=8)
            if r.status_code == 200:
                q = r.json().get("quotes",{}).get(contract_sym,{})
                delta = abs(float(q.get("delta", 0) or 0))
                if 0 < delta < EXIT_RULES["roll_delta_threshold"]:
                    if trade.get("exit_rule_hit") != "roll_alert":
                        trade["exit_rule_hit"] = "roll_alert"
                        tg(f"🔄 ROLL ALERT\n"
                           f"{'='*26}\n"
                           f"{ticker} | Label: {label}\n"
                           f"Delta dropped to {delta:.2f} "
                           f"(below {EXIT_RULES['roll_delta_threshold']} threshold)\n"
                           f"{'='*26}\n"
                           f"EXIT RULE: Roll to higher strike / later expiry\n"
                           f"This position losing its directional power\n"
                           f"Only roll if you still believe the move is coming")
        except: pass

def get_price(ticker):
    try:
        r = requests.get(f"{ALPACA_DATA}/v2/stocks/{ticker}/quotes/latest",
            headers=hdrs, timeout=8)
        if r.status_code == 200:
            q = r.json().get("quote", {})
            mid = (q.get("ap",0) + q.get("bp",0)) / 2
            return round(mid, 2) if mid > 0 else None
    except: pass
    return None

def get_iv_and_contracts(ticker, option_type="put", target_dte=21):
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/options/contracts",
            headers=hdrs,
            params={"underlying_symbols": ticker, "type": option_type,
                    "expiration_date_gte": (datetime.now()+timedelta(days=7)).strftime("%Y-%m-%d"),
                    "expiration_date_lte": (datetime.now()+timedelta(days=45)).strftime("%Y-%m-%d"),
                    "status": "active", "limit": 30}, timeout=10)
        if r.status_code == 200:
            contracts = r.json().get("option_contracts", [])
            ivs = [float(c.get("implied_volatility",0)) for c in contracts if c.get("implied_volatility")]
            avg_iv = round(sum(ivs)/len(ivs)*100, 1) if ivs else None
            return contracts, avg_iv
    except: pass
    return [], None

def get_option_mid_price(symbol):
    try:
        r = requests.get(f"{ALPACA_DATA}/v2/options/quotes/latest",
            headers=hdrs, params={"symbols": symbol}, timeout=8)
        if r.status_code == 200:
            q = r.json().get("quotes", {}).get(symbol, {})
            ap = float(q.get("ap", 0)); bp = float(q.get("bp", 0))
            if ap > 0 and bp > 0: return round((ap+bp)/2, 2)
    except: pass
    return None

def get_all_context():
    ctx = {}
    for name, path in [
        ("macro",    "/root/jarvis/jarvis_macro.json"),
        ("beast",    "/root/jarvis/jarvis_beast_brain.json"),
        ("congress", "/root/jarvis/jarvis_congress.json"),
        ("earnings", "/root/jarvis/jarvis_earnings.json"),
        ("brain",    "/root/jarvis/jarvis_central_brain.json"),
        ("intel",    "/root/jarvis/jarvis_intel.json"),
    ]:
        try: ctx[name] = json.load(open(path))
        except: ctx[name] = {}
    return ctx

def score_setup(ticker, price, iv, option_type, ctx, config):
    score = 0; signals = []
    macro = ctx.get("macro", {}); brain = ctx.get("brain", {})
    congress = ctx.get("congress", {}); earnings = ctx.get("earnings", {})
    regime    = macro.get("regime", "UNKNOWN")
    fg        = brain.get("fear_greed", 50)
    vix       = macro.get("vix", {}).get("value", 15)
    btc_signal = brain.get("btc_signal", "neutral")

    if option_type == "put_sell":
        if regime == "RISK_ON":      score += 20; signals.append("REGIME:RISK_ON+20")
        elif regime == "RECOVERY":   score += 15; signals.append("REGIME:RECOVERY+15")
        elif regime == "STAGFLATION":score += 5;  signals.append("REGIME:STAGFLATION+5")
        elif regime == "RISK_OFF":   score -= 10; signals.append("REGIME:RISK_OFF-10")
    elif option_type == "call_buy":
        if regime == "RISK_ON":  score += 20; signals.append("REGIME:RISK_ON+20")
        elif regime == "RISK_OFF": score -= 20; signals.append("REGIME:RISK_OFF-20")

    if option_type == "put_sell":
        if fg < 25:   score += 20; signals.append(f"FG:{fg}EXTREME_FEAR+20")
        elif fg < 40: score += 15; signals.append(f"FG:{fg}FEAR+15")
        elif fg < 55: score += 10; signals.append(f"FG:{fg}NEUTRAL+10")
        elif fg > 70: score += 5;  signals.append(f"FG:{fg}GREED+5")
    elif option_type == "call_buy":
        if fg < 25:   score += 15; signals.append(f"FG:{fg}FEAR_BOUNCE+15")
        elif fg > 70: score -= 10; signals.append(f"FG:{fg}GREED-10")
        else:         score += 10; signals.append(f"FG:{fg}NORMAL+10")

    if iv:
        if option_type == "put_sell":
            if iv > 60:   score += 20; signals.append(f"IV:{iv}%EXTREME+20")
            elif iv > 40: score += 15; signals.append(f"IV:{iv}%HIGH+15")
            elif iv > 25: score += 10; signals.append(f"IV:{iv}%MED+10")
            else:         score += 3;  signals.append(f"IV:{iv}%LOW+3")
        elif option_type == "call_buy":
            if iv < 25:   score += 20; signals.append(f"IV:{iv}%LOW_BUY+20")
            elif iv < 40: score += 10; signals.append(f"IV:{iv}%MED+10")
            else:         score -= 10; signals.append(f"IV:{iv}%HIGH-10")

    hot = congress.get("hot_tickers", {})
    if ticker in hot:
        count = hot[ticker].get("count", 0)
        score += min(15, count * 5)
        signals.append(f"CONGRESS:{count}BUYERS+{min(15,count*5)}")

    risk_map = earnings.get("risk_map", {})
    if ticker in risk_map:
        risk = risk_map[ticker].get("risk","LOW")
        if risk == "CRITICAL":  score -= 30; signals.append("EARNINGS:CRITICAL-30")
        elif risk == "HIGH":    score -= 20; signals.append("EARNINGS:HIGH-20")
        elif risk == "MEDIUM":  score -= 5;  signals.append("EARNINGS:MEDIUM-5")

    if config.get("sector") in ["crypto","fintech"]:
        if btc_signal == "bullish":  score += 10; signals.append("BTC:BULLISH+10")
        elif btc_signal == "bearish": score -= 10; signals.append("BTC:BEARISH-10")

    if option_type == "put_sell" and vix > 20:
        score += 10; signals.append(f"VIX:{vix:.1f}HIGH+10")
    elif option_type == "call_buy" and vix < 15:
        score += 5; signals.append(f"VIX:{vix:.1f}LOW+5")

    return max(0, min(100, score)), signals

def build_pattern_insight(brain):
    trades = [t for t in brain["trades"] if t.get("result") in ["WIN","LOSS"]]
    if len(trades) < 5: return None
    insights = []
    s = brain["stats"]
    for strat, data in s["by_strategy"].items():
        if data["total"] >= 3:
            wr = round(data["wins"]/data["total"]*100)
            avg_pnl = round(data["pnl"]/data["total"], 2)
            insights.append(f"{strat}: {wr}% WR avg ${avg_pnl:+.0f}")
    # Best catalyst (ADD 2)
    for cat, data in s.get("by_catalyst",{}).items():
        if data["total"] >= 3:
            wr = round(data["wins"]/data["total"]*100)
            insights.append(f"Catalyst {cat}: {wr}% WR")
    best_ticker = max(s["by_ticker"].items(),
        key=lambda x: x[1]["wins"]/max(x[1]["total"],1) if x[1]["total"] >= 3 else 0,
        default=(None,{}))
    if best_ticker[0] and best_ticker[1].get("total",0) >= 3:
        wr = round(best_ticker[1]["wins"]/best_ticker[1]["total"]*100)
        insights.append(f"Best ticker: {best_ticker[0]} {wr}% WR")
    return "\n".join(insights) if insights else None

def morning_brief(brain, ctx):
    macro = ctx.get("macro", {}); brain_data = ctx.get("brain", {})
    regime = macro.get("regime","UNKNOWN")
    fg  = brain_data.get("fear_greed", 50)
    vix = macro.get("vix",{}).get("value", 15)
    yield_val = macro.get("yield_10yr",{}).get("value", 4.3)
    btc = brain_data.get("btc_price", 0)

    if fg < 25:   mood="😱 Extreme Fear — BEST time to sell puts"; action="SELL PUTS"; color="🟢"
    elif fg < 40: mood="😨 Fear — Good time to sell puts";         action="SELL PUTS"; color="🟢"
    elif fg < 60: mood="😐 Neutral — Normal conditions";           action="BE SELECTIVE"; color="🟡"
    elif fg < 75: mood="😊 Greed — Sell calls, take profits";      action="SELL CALLS"; color="🟡"
    else:         mood="🤑 Extreme Greed — Watch out";             action="SIT OUT or BUY PUTS"; color="🔴"

    regime_plain = {
        "RISK_ON":     "Market wants to go up — buy calls, sell puts aggressively",
        "RISK_OFF":    "Market wants to go down — buy puts, avoid selling puts",
        "STAGFLATION": "Choppy — sell premium, avoid directional bets",
        "RECOVERY":    "Recovering — buy calls on dips, sell puts on quality stocks"
    }.get(regime, "Uncertain — wait for clarity")

    if vix > 25:   vix_note = f"VIX {vix:.1f} HIGH — options expensive, great to sell"
    elif vix > 18: vix_note = f"VIX {vix:.1f} ELEVATED — decent premium available"
    else:          vix_note = f"VIX {vix:.1f} LOW — options cheap, better to buy than sell"

    best_play = find_todays_best_play(ctx, fg, regime, vix)
    insights  = build_pattern_insight(brain)
    s = brain["stats"]
    total = s["total"]; wins = s["wins"]
    wr  = round(wins/total*100) if total > 0 else 0
    pnl = s["total_pnl"]

    # Open watches summary
    watches = load_watches()
    active_watches = [(t, w) for t, w in watches.items() if not w.get("triggered")]
    watch_lines = []
    if active_watches:
        watch_lines = ["ACTIVE WATCHES:"]
        for t, w in active_watches:
            watch_lines.append(f"  👁 {t} {w['direction']} ${w['price']} — {w.get('label','')}")

    lines = [
        f"📊 JARVIS OPTIONS BRIEF",
        f"{'='*26}",
        f"{color} Market Mood: {mood}",
        f"Today's Move: {action}",
        f"{'='*26}",
        f"Regime: {regime_plain}",
        f"{vix_note}",
        f"BTC: ${btc:,.0f} | Yield: {yield_val:.2f}%",
        f"{'='*26}",
    ]
    if best_play:
        lines.append("TODAY'S PLAY:")
        lines.extend(best_play)
    else:
        lines.append("No high-confidence setups today")
    lines.extend(watch_lines)
    lines.extend([
        f"{'='*26}",
        f"PORTFOLIO: {total} trades | {wr}% WR | ${pnl:+.0f}",
    ])
    if insights:
        lines.append(f"LEARNING:"); lines.append(insights)
    lines.extend([
        f"{'='*26}",
        f"COMMANDS: OPTIONS · LEARN <ticker> · WATCH <ticker> <price>"
    ])
    tg("\n".join(lines))
    brain["daily_brief_sent"] = datetime.now().strftime("%Y-%m-%d")
    return brain

def find_todays_best_play(ctx, fg, regime, vix):
    macro    = ctx.get("macro", {})
    earnings = ctx.get("earnings", {})
    congress = ctx.get("congress", {})
    blacklist = earnings.get("critical",[]) + earnings.get("high_risk",[])
    hot = congress.get("hot_tickers", {})
    plays = []
    if fg < 50 and regime in ["RISK_ON","RECOVERY","STAGFLATION"]:
        for ticker, config in UNIVERSE.items():
            if config["type"] not in ["wheel","both"]: continue
            if ticker in blacklist: continue
            price = get_price(ticker)
            if not price: continue
            contracts, iv = get_iv_and_contracts(ticker, "put")
            if not contracts or not iv: continue
            contract, analysis = select_best_strike(contracts, price, "put_sell")
            if not contract or not analysis: continue
            strike  = analysis["strike"]
            premium = analysis["premium"]
            dte     = analysis["dte"]
            if premium <= 0: continue
            premium_pct = premium/strike*100
            if premium_pct < 1.0: continue
            congress_bonus = "🏛 Congress buying!" if ticker in hot else ""
            score = premium_pct * (iv/30) * (1 if regime=="RISK_ON" else 0.8)
            plays.append((score, ticker, price, strike, premium, dte, iv, "SELL PUT", congress_bonus, analysis))
    if not plays: return None
    plays.sort(reverse=True)
    best = plays[0]
    score, ticker, price, strike, premium, dte, iv, strategy, congress_bonus, analysis = best
    cash_needed    = strike * 100
    monthly_return = round(premium/strike*100 * (30/dte), 1)
    return [
        f"🎡 {strategy}: {ticker}",
        f"   Stock: ${price:.2f} | Strike: ${strike:.0f} | Exp: {dte}d",
        f"   Collect: ${premium:.2f}/share = ${premium*100:.0f} total",
        f"   Cash needed: ${cash_needed:.0f}",
        f"   Breakeven: ${analysis.get('breakeven', strike-premium):.2f}",
        f"   Delta: {analysis.get('delta',0):.2f} ({analysis.get('delta_note','')})",
        f"   Monthly return: {monthly_return}%",
        f"   IV: {iv}% | {congress_bonus}",
    ]

def scan_and_alert(brain, ctx):
    blacklist = ctx.get("earnings",{}).get("critical",[]) + ctx.get("earnings",{}).get("high_risk",[])
    fg     = ctx.get("brain",{}).get("fear_greed", 50)
    regime = ctx.get("macro",{}).get("regime","UNKNOWN")
    vix    = ctx.get("macro",{}).get("vix",{}).get("value",15)
    log.info(f"Scanning {len(UNIVERSE)} tickers | F&G:{fg} Regime:{regime}")
    top_setups = []
    for ticker, config in UNIVERSE.items():
        if ticker in blacklist: continue
        price = get_price(ticker)
        if not price: continue
        scan_types = []
        if config["type"] in ["wheel","both"] and fg < 55:
            scan_types.append("put_sell")
        if config["type"] in ["momentum","both"] and regime == "RISK_ON":
            scan_types.append("call_buy")
        for opt_type in scan_types:
            contracts, iv = get_iv_and_contracts(ticker, "put" if "put" in opt_type else "call")
            score, signals = score_setup(ticker, price, iv, opt_type, ctx, config)
            if score < 60: continue
            contract, analysis = select_best_strike(contracts, price, opt_type)  # ADD 1
            if not contract or not analysis: continue
            top_setups.append({
                "score": score, "ticker": ticker, "price": price,
                "strategy": opt_type, "signals": signals, "config": config,
                "analysis": analysis, "iv": iv,
            })
    top_setups.sort(key=lambda x: x["score"], reverse=True)
    for setup in top_setups[:2]:
        alert = build_trade_alert(setup, ctx)
        if alert:
            tg(alert)
            brain, _ = log_trade_entry(
                brain,
                ticker          = setup["ticker"],
                strategy        = setup["strategy"],
                contract_symbol = setup["analysis"].get("contract",""),
                strike          = setup["analysis"].get("strike",0),
                premium         = setup["analysis"].get("premium",0),
                dte             = setup["analysis"].get("dte",0),
                iv              = setup["iv"],
                score           = setup["score"],
                signals         = setup["signals"],
                stock_price     = setup["price"],
                ctx             = ctx,
                catalyst        = "technical_breakout",  # default; override manually
                label           = f"{setup['ticker']}_{datetime.now().strftime('%m%d')}",
                analysis        = setup["analysis"],
            )
    return brain

def build_trade_alert(setup, ctx):
    ticker   = setup["ticker"]
    strategy = setup["strategy"]
    price    = setup["price"]
    score    = setup["score"]
    analysis = setup["analysis"]
    iv       = setup.get("iv") or 0
    strike   = analysis.get("strike", 0)
    premium  = analysis.get("premium", 0)
    dte      = analysis.get("dte", 0)
    breakeven= analysis.get("breakeven", 0)
    delta    = analysis.get("delta", 0)
    delta_note = analysis.get("delta_note","")
    pct_otm  = analysis.get("pct_otm", 0)

    if strategy == "put_sell":
        emoji  = "🎡"; action = "SELL PUT"
        cash_needed = strike * 100
        max_profit  = premium * 100
        max_loss    = (strike - premium) * 100
        monthly     = round(premium/strike*100 * 30/dte, 1) if dte > 0 else 0
        plain = (f"You sell the right to make you buy {ticker} at ${strike:.0f}\n"
                 f"They pay you ${premium:.2f}/share = ${max_profit:.0f} upfront\n"
                 f"Breakeven: ${breakeven:.2f} | Delta: {delta:.2f} ({delta_note})")
    else:
        emoji  = "🚀"; action = "BUY CALL"
        cash_needed = premium * 100
        max_profit  = "unlimited"
        max_loss    = premium * 100
        monthly     = "N/A"
        plain = (f"Pay ${premium:.2f}/share = ${cash_needed:.0f} for the right to buy {ticker}\n"
                 f"Breakeven at expiry: ${breakeven:.2f}\n"
                 f"Delta: {delta:.2f} ({delta_note}) | {pct_otm:.1f}% OTM")

    signal_plain = []
    for sig in setup["signals"][:4]:
        if "REGIME:RISK_ON"   in sig: signal_plain.append("Market trending up")
        elif "FG:" in sig and "FEAR" in sig: signal_plain.append("Fear = good entry")
        elif "IV:" in sig and "HIGH" in sig: signal_plain.append("High IV = fat premium")
        elif "CONGRESS"       in sig: signal_plain.append("Politicians buying")
        elif "EARNINGS:CRITICAL" in sig: signal_plain.append("⚠️ Earnings soon!")

    lines = [
        f"{emoji} JARVIS OPTIONS SIGNAL",
        f"Score: {score}/100",
        f"{'='*24}",
        f"{action}: {ticker} @ ${price:.2f}",
        f"Strike: ${strike:.0f} | Exp: {dte}d | IV: {iv:.0f}%",
        f"{'='*24}",
        plain,
        f"{'='*24}",
        f"WHY: {' · '.join(signal_plain)}",
        f"{'='*24}",
        f"Cash needed: ${cash_needed:,.0f}",
        f"Max profit: {max_profit if isinstance(max_profit,str) else f'${max_profit:,.0f}'}",
        f"Max loss: ${max_loss:,.0f}",
        f"Monthly return: {monthly}%" if monthly != "N/A" else "",
        f"{'='*24}",
        f"EXIT RULES:",
        f"  Take 50% off at 2× premium",
        f"  Stop if down 50% of premium paid",
        f"  Roll if delta drops below 0.20",
        f"{'='*24}",
        f"Text LEARN {ticker} for deep analysis"
    ]
    return "\n".join(l for l in lines if l)

def main():
    log.info("JARVIS OPTIONS BRAIN v2 ONLINE")
    tg("🧠 OPTIONS BRAIN v2 ONLINE\n"
       "Upgrades: strike logic · catalyst tags\n"
       "price alerts · P&L tracking\n"
       "theta warnings · exit rules\n"
       "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
       "WATCH MSFT 450 above breakout\n"
       "to set a price alert")

    brain = load_brain()

    # Set MSFT $450 watch automatically on startup
    watches = load_watches()
    if "MSFT" not in watches or watches.get("MSFT",{}).get("triggered"):
        add_watch("MSFT", 450.00, "above", "breakout_call_trigger")

    last_scan = 0

    while True:
        try:
            now      = datetime.now()
            edt_hour = (datetime.utcnow().hour - 4) % 24
            ctx      = get_all_context()
            today    = now.strftime("%Y-%m-%d")

            # Daily brief 8am EDT
            if edt_hour == 8 and brain.get("daily_brief_sent") != today:
                brain = morning_brief(brain, ctx)

            is_market_day = now.weekday() < 5
            if is_market_day and 9 <= edt_hour <= 16:
                # Watch price alerts — check every 5 min
                check_watches()
                # Exit rules — check every 5 min
                check_exit_rules(brain)
                # Theta warnings — check every 5 min
                check_theta_warnings(brain)
                # Full scan — every hour
                if time.time() - last_scan >= INTERVAL:
                    brain = scan_and_alert(brain, ctx)
                    last_scan = time.time()

            save_brain(brain)
        except Exception as e:
            log.error(f"Brain cycle: {e}")
        time.sleep(300)

if __name__ == "__main__":
    main()
