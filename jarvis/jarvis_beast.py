#!/usr/bin/env python3
"""
THE BEAST — Jarvis Alpha Stock Trading Engine
Combines: Technical momentum + Congress trades + Insider buying + 
          Sector rotation + BTC sentiment + Options flow + Claude AI
from jarvis_context import get_context
Runs every 5 minutes during market hours. Paper trades on Alpaca.
"""
import requests, json, os, time, logging, math
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import jarvis_brain as _jb_hb

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("THE_BEAST")

# ── CONFIG ──────────────────────────────────────────────
from jarvis_secrets import CLAUDE_API_KEY as CLAUDE_KEY
ALPACA_KEY    = "PKTHANGUNVFDSLLR3VXPETXRQF"
ALPACA_SECRET = "GRTDDfkCGWbZMoNSWms6uJSGvw72rHaAk1N1fvLi8EAP"
ALPACA_BASE   = "https://paper-api.alpaca.markets"
ALPACA_DATA   = "https://data.alpaca.markets"
TG_TOKEN      = __import__("jarvis_secrets").TG_TOKEN_TRADER
CHAT_ID       = "7534553840"
BRAIN_FILE    = "/root/jarvis/jarvis_beast_brain.json"
INTERVAL      = 300   # 5 minutes

# ── RISK RULES ──────────────────────────────────────────
MAX_POSITION_SIZE = 500    # Max $ per trade
MAX_POSITIONS     = 5      # Max concurrent positions
STOP_LOSS_PCT     = 0.05   # 5% stop loss
PROFIT_TARGET_1   = 0.08   # 8% take half
PROFIT_TARGET_2   = 0.15   # 15% take rest
MIN_SIGNALS       = 4      # Need 4 of 6 signals to trade
MIN_CONFIDENCE    = 70     # Claude confidence floor

# ── WATCHLIST ───────────────────────────────────────────
# Core momentum universe — 50 tickers across sectors
UNIVERSE = {
    # Tech
    "AAPL":"tech","MSFT":"tech","NVDA":"tech","META":"tech",
    "GOOGL":"tech","AMZN":"tech","AMD":"tech","CRM":"tech",
    # Finance
    "JPM":"finance","GS":"finance","V":"finance","MA":"finance",
    # Health
    "UNH":"health","JNJ":"health","PFE":"health","ABBV":"health",
    # Energy
    "XOM":"energy","CVX":"energy","SLB":"energy",
    # ETFs (sector momentum)
    "SPY":"etf","QQQ":"etf","IWM":"etf","XLF":"etf",
    "XLE":"etf","XLV":"etf","XLK":"etf","XLI":"etf",
    # High momentum
    "TSLA":"auto","PLTR":"tech","SOFI":"finance",
    "RBLX":"tech","COIN":"crypto","MSTR":"crypto",
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "JarvisBeast/1.0"})

# ── HELPERS ──────────────────────────────────────────────
def tg(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": f"🦁 BEAST\n{msg}"}, timeout=10)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def alpaca(method, path, data=None):
    hdrs = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET, "Content-Type": "application/json"}
    try:
        if method == "GET": r = requests.get(ALPACA_BASE+path, headers=hdrs, timeout=10)
        elif method == "POST": r = requests.post(ALPACA_BASE+path, headers=hdrs, json=data, timeout=10)
        elif method == "DELETE": r = requests.delete(ALPACA_BASE+path, headers=hdrs, timeout=10)
        if r.status_code in [200,201]: return r.json()
        log.warning(f"Alpaca {r.status_code}: {r.text[:80]}")
    except Exception as e: log.error(f"Alpaca: {e}")
    return None

def safe_get(url, params=None, timeout=8):
    try:
        r = SESSION.get(url, params=params, timeout=timeout)
        if r.status_code == 200: return r.json()
    except: pass
    return None

def load_brain():
    try: return json.load(open(BRAIN_FILE))
    except: return {
        "trades": [], "wins": 0, "losses": 0, "total_pnl": 0.0,
        "best_tickers": {}, "worst_tickers": {}, "signal_accuracy": {},
        "congress_wins": 0, "congress_total": 0,
        "sector_wins": {}, "size_multiplier": 1.0,
        "last_scan": "", "total_scans": 0
    }

def save_brain(brain): 
    with open(BRAIN_FILE, 'w') as f: json.dump(brain, f, indent=2)

# ── MARKET DATA ──────────────────────────────────────────
def is_market_open():
    c = alpaca("GET", "/v2/clock")
    return c and c.get("is_open", False)

def get_positions():
    return alpaca("GET", "/v2/positions") or []

def get_account():
    return alpaca("GET", "/v2/account") or {}

def get_price_data(ticker):
    """Get price + basic stats from Alpaca"""
    try:
        r = requests.get(f"{ALPACA_DATA}/v2/stocks/{ticker}/bars",
            headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
            params={"timeframe": "1Hour", "limit": 50, "feed": "iex"}, timeout=10)
        if r.status_code == 200:
            bars = r.json().get("bars", [])
            if bars:
                closes = [b["c"] for b in bars]
                volumes = [b["v"] for b in bars]
                highs = [b["h"] for b in bars]
                lows = [b["l"] for b in bars]
                return {"closes": closes, "volumes": volumes, "highs": highs, "lows": lows,
                        "current": closes[-1], "open": bars[-1]["o"]}
    except Exception as e:
        log.debug(f"Price data {ticker}: {e}")
    return None

def calc_rsi(closes, period=14):
    if len(closes) < period+1: return 50
    gains = losses = []
    gains = [max(closes[i]-closes[i-1],0) for i in range(1,len(closes))]
    losses = [max(closes[i-1]-closes[i],0) for i in range(1,len(closes))]
    ag = sum(gains[-period:])/period
    al = sum(losses[-period:])/period
    if al == 0: return 100
    return round(100 - 100/(1+ag/al), 1)

def calc_macd(closes):
    if len(closes) < 26: return 0, 0, 0
    def ema(data, period):
        k = 2/(period+1); e = [data[0]]
        for p in data[1:]: e.append(p*k+e[-1]*(1-k))
        return e
    e12 = ema(closes, 12); e26 = ema(closes, 26)
    macd = e12[-1] - e26[-1]
    macd_series = [f-s for f,s in zip(e12[13:], e26[13:])]
    signal = ema(macd_series, 9)[-1] if len(macd_series) >= 9 else 0
    return round(macd,3), round(signal,3), round(macd-signal,3)

def calc_ema(closes, period):
    if len(closes) < period: return closes[-1]
    k = 2/(period+1); e = closes[0]
    for p in closes[1:]: e = p*k+e*(1-k)
    return round(e, 2)

def calc_volume_ratio(volumes):
    if len(volumes) < 5: return 1.0
    avg = sum(volumes[:-1])/len(volumes[:-1])
    return round(volumes[-1]/avg, 2) if avg > 0 else 1.0

# ── SIGNAL ENGINE ────────────────────────────────────────
def analyze_ticker(ticker, sector, brain, cb, congress_data, ticker_rules=None):
    """
    Score a ticker across 6 signal sources + learned history modifier.
    Returns (score, signals, data) or None if no data.
    """
    data = get_price_data(ticker)
    if not data or len(data["closes"]) < 26: return None

    closes = data["closes"]
    current = data["current"]
    signals = []
    score = 0

    # SIGNAL 1: Trend (price above EMA20 + EMA50)
    ema20 = calc_ema(closes, 20)
    ema50 = calc_ema(closes, 50) if len(closes) >= 50 else ema20
    if current > ema20 and ema20 > ema50:
        signals.append(f"TREND ✅ Above EMA20({ema20:.0f}) + EMA50({ema50:.0f})")
        score += 1
    elif current < ema20:
        signals.append(f"TREND ❌ Below EMA20({ema20:.0f})")

    # SIGNAL 2: RSI momentum (50-70 = sweet spot)
    rsi = calc_rsi(closes)
    if 50 <= rsi <= 70:
        signals.append(f"RSI ✅ {rsi} — momentum zone")
        score += 1
    elif rsi > 75:
        signals.append(f"RSI ❌ {rsi} — overbought")
    elif rsi < 40:
        signals.append(f"RSI ❌ {rsi} — weak")

    # SIGNAL 3: MACD crossover
    macd_l, macd_s, macd_h = calc_macd(closes)
    if macd_h > 0 and macd_l > 0:
        signals.append(f"MACD ✅ Bullish hist:{macd_h:+.3f}")
        score += 1
    elif macd_h < 0:
        signals.append(f"MACD ❌ Bearish hist:{macd_h:+.3f}")

    # SIGNAL 4: Volume surge
    vol_ratio = calc_volume_ratio(data["volumes"])
    if vol_ratio >= 1.5:
        signals.append(f"VOLUME ✅ {vol_ratio}x average — institutional interest")
        score += 1
    else:
        signals.append(f"VOLUME ❌ {vol_ratio}x — no surge")

    # SIGNAL 5: Congress buying
    congress_hot = congress_data.get("hot_tickers", {})
    if ticker in congress_hot:
        pols = congress_hot[ticker].get("politicians", [])
        cnt = congress_hot[ticker].get("count", 0)
        signals.append(f"CONGRESS ✅ {cnt} politicians buying — {', '.join(pols[:2])}")
        score += 1
    else:
        # Check central brain congress list
        cb_congress = cb.get("congress_hot_tickers", [])
        if ticker in cb_congress:
            signals.append(f"CONGRESS ✅ On congress watchlist")
            score += 1

    # SIGNAL 6: Sector momentum
    # Fresh-or-nothing: stale (weekend/overnight/holiday) sector bars must not
    # bias live trades, so a frozen Friday close reads as "no sector signal".
    import jarvis_freshness as _fresh
    sector_scores = _fresh.fresh_sector_scores()

    sector_score = sector_scores.get(sector, 0)
    if sector_score > 1.0:
        signals.append(f"SECTOR ✅ {sector} momentum {sector_score:+.1f}%")
        score += 1
    elif sector_score < -1.0:
        signals.append(f"SECTOR ❌ {sector} weak {sector_score:+.1f}%")

    # SIGNAL 7: Learned ticker history (from jarvis_learning → brain table)
    if ticker_rules:
        stats = ticker_rules.get(ticker)
        if stats and stats.get("count", 0) >= 3:
            wr = stats["wr"]
            count = stats["count"]
            if wr >= 70.0:
                signals.append(f"LEARNED ✅ {ticker} WR={wr}% ({count} trades)")
                score += 1
            elif wr <= 30.0:
                signals.append(f"LEARNED ❌ {ticker} WR={wr}% ({count} trades)")
                score -= 1

    # BONUS: BTC risk-on check
    btc_signal = cb.get("btc_signal", "neutral")
    fear_greed = cb.get("fear_greed", 50)
    risk_level = cb.get("risk_level", "NORMAL")

    data["rsi"] = rsi
    data["macd_hist"] = macd_h
    data["vol_ratio"] = vol_ratio
    data["ema20"] = ema20
    data["score"] = score
    data["signals"] = signals
    data["sector"] = sector
    data["ticker"] = ticker

    return score, signals, data

def ask_claude_beast(ticker, score, signals, data, brain):
    """Ask Claude for final trade decision with full context"""
    try:
        # Build learning context from brain
        ticker_data = brain.get("best_tickers", {}).get(ticker, {})
        wins = ticker_data.get("wins", 0); losses = ticker_data.get("losses", 0)
        ticker_wr = f"{round(wins/(wins+losses)*100)}% WR ({wins+losses} trades)" if wins+losses > 0 else "No history"

        # Congress signal strength
        congress_signal = next((s for s in signals if "CONGRESS" in s), "No congress signal")

        prompt = f"""THE BEAST stock trader. One decision. No markdown.

TICKER: {ticker} @ ${data['current']:.2f}
SECTOR: {data['sector']}
SIGNALS ({score}/6 confirmed):
{chr(10).join(signals)}

INDICATORS:
RSI: {data['rsi']} | MACD hist: {data['macd_hist']:+.3f} | Volume: {data['vol_ratio']}x
EMA20: ${data['ema20']:.2f} | Price vs EMA20: {((data['current']/data['ema20'])-1)*100:+.1f}%

JARVIS LEARNING:
{ticker} history: {ticker_wr}
Congress: {congress_signal}
Market risk: {data.get('risk_level','NORMAL')} | BTC: {data.get('btc_signal','neutral')}

RULES:
- Need 4+ signals AND 70%+ confidence to BUY
- Max position ${MAX_POSITION_SIZE}
- Only buy with volume surge (institutional backing)
- Congress buys are strong signals — weight heavily
- Avoid if risk=EXTREME or F&G < 20

Reply ONLY: BUY or SKIP | CONFIDENCE% | ENTRY_PRICE | STOP_LOSS | TARGET1 | REASON
Example: BUY|78%|${data['current']:.2f}|${data['current']*0.95:.2f}|${data['current']*1.08:.2f}|RSI momentum + congress buying + volume surge"""

        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": 100,
                  "messages": [{"role": "user", "content": prompt}]}, timeout=15)

        resp = r.json()
        if "error" in resp: return None
        text = resp["content"][0]["text"].strip()
        parts = text.split("|")
        if len(parts) < 6: return None

        action = parts[0].strip().upper()
        conf = int(parts[1].strip().replace("%",""))
        entry = float(parts[2].strip().replace("$","").replace(",",""))
        stop = float(parts[3].strip().replace("$","").replace(",",""))
        target = float(parts[4].strip().replace("$","").replace(",",""))
        reason = parts[5].strip()

        return {"action": action, "confidence": conf, "entry": entry,
                "stop": stop, "target": target, "reason": reason}
    except Exception as e:
        log.error(f"Claude Beast: {e}")
        return None

def place_trade(ticker, entry_price, stop, target, confidence, reason, signals):
    """Place trade on Alpaca"""
    acct = get_account()
    equity = float(acct.get("equity", 0))
    size = min(MAX_POSITION_SIZE, equity * 0.02) * macro_mult  # 2% adjusted by regime
    qty = max(1, int(size / entry_price))

    # HARD global ceiling — veto the entry if a portfolio limit (drawdown /
    # gross / symbol / sector) is breached across all bots (item #2 phase 2).
    try:
        import portfolio_state as _ps
        _ok, _why = _ps.can_open(ticker, qty * entry_price)
        if not _ok:
            log.info(f"GLOBAL CEILING blocked {ticker}: {_why}")
            tg(f"⛔ BEAST {ticker} blocked — {_why}")
            return None
    except Exception:
        pass

    result = alpaca("POST", "/v2/orders", {
        "symbol": ticker, "qty": str(qty), "side": "buy",
        "type": "market", "time_in_force": "day"
    })

    if result:
        cost = qty * entry_price
        msg = (f"📈 BEAST TRADE\n"
               f"{ticker} BUY {qty} shares @ ${entry_price:.2f}\n"
               f"Cost: ${cost:.0f} | Conf: {confidence}%\n"
               f"Stop: ${stop:.2f} | Target: ${target:.2f}\n"
               f"Reason: {reason}\n"
               f"Signals: {sum(1 for s in signals if '✅' in s)}/6")
        tg(msg)
        log.info(f"TRADE: BUY {qty}x {ticker} @ ${entry_price:.2f}")
        return result
    return None

def check_exits(brain):
    """Check open positions for exits"""
    positions = get_positions()
    for pos in positions:
        sym = pos.get("symbol", "")
        # Skip crypto and options
        if "USD" in sym or len(sym) > 6: continue

        pnl_pct = float(pos.get("unrealized_plpc", 0)) * 100
        pnl_usd = float(pos.get("unrealized_pl", 0))
        current = float(pos.get("current_price", 0))
        reason = None

        if pnl_pct >= 15: reason = f"TARGET2 +{pnl_pct:.1f}%"
        elif pnl_pct >= 8: reason = f"TARGET1 +{pnl_pct:.1f}% — taking 50%"
        elif pnl_pct <= -5: reason = f"STOP LOSS {pnl_pct:.1f}%"

        if reason:
            import urllib.parse
            result = alpaca("DELETE", f"/v2/positions/{urllib.parse.quote(sym)}")
            if result:
                emoji = "✅" if pnl_usd >= 0 else "🔴"
                tg(f"{emoji} BEAST EXIT\n{sym} {reason}\nP&L: ${pnl_usd:+.0f}")
                # Update brain
                if pnl_usd > 0:
                    brain["wins"] += 1
                    brain.setdefault("best_tickers", {}).setdefault(sym, {"wins":0,"losses":0,"pnl":0})
                    brain["best_tickers"][sym]["wins"] += 1
                    brain["best_tickers"][sym]["pnl"] += pnl_usd
                else:
                    brain["losses"] += 1
                    brain.setdefault("best_tickers", {}).setdefault(sym, {"wins":0,"losses":0,"pnl":0})
                    brain["best_tickers"][sym]["losses"] += 1
                    brain["best_tickers"][sym]["pnl"] += pnl_usd
                brain["total_pnl"] += pnl_usd

def run_scan(brain):
    """Main scan — check all tickers for setups"""
    if not is_market_open():
        log.info("Market closed — skipping scan")
        return

    # Load shared intelligence
    cb = {}
    congress_data = {}
    try:
        cb = json.load(open("/root/jarvis/jarvis_central_brain.json"))
        congress_data = json.load(open("/root/jarvis/jarvis_congress.json"))
    except: pass

    # Load learned ticker rules from jarvis_learning (brain table)
    ticker_rules = {}
    try:
        conn_lr = sqlite3.connect(DB_PATH, timeout=5)
        row = conn_lr.execute("SELECT value FROM brain WHERE key='learned_ticker_rules'").fetchone()
        conn_lr.close()
        if row:
            ticker_rules = json.loads(row[0]).get("ticker_stats", {})
    except: pass

    # Check risk + macro regime
    if cb.get("risk_level") == "EXTREME":
        log.info("EXTREME risk — no new trades")
        return

    # Market-mode gate: in PROTECTION, no long/buy signals. Beast is long-only,
    # so only SHORT/CASH would be allowed → effectively no new buys this scan.
    try:
        import jarvis_memory_db as _memdb
        if (_memdb.brain_get("market_mode") or "PROFIT") == "PROTECTION":
            log.info("PROTECTION mode — no long/buy signals (SHORT/CASH only)")
            return
    except Exception:
        pass

    # Load macro regime and adjust sizing
    macro_mult = 1.0
    macro_regime = "UNKNOWN"
    try:
        macro = json.load(open("/root/jarvis/jarvis_macro.json"))
        import jarvis_memory_db as _memdb
        macro_regime = _memdb.get_regime("UNKNOWN")  # canonical regime from jarvis_memory.db
        macro_mult = macro.get("size_multiplier", 1.0)
        # Freshness guard: macro is a 2h cron one-shot. If its output is stale
        # (missed cycle) cap size so we don't size up on a stale RISK_ON — a
        # stale RISK_OFF below still halts (fail-safe).
        import jarvis_freshness as _fresh
        _rg, _rg_age, _rg_fresh = _fresh.regime_with_age()
        if not _rg_fresh:
            log.info(f"Macro stale ({_fresh.fmt_age(_rg_age)}) — capping size to 0.5x")
            macro_mult = min(macro_mult, 0.5)
        if macro.get("defensive_mode"):
            log.info(f"Macro defensive mode — halving positions")
            macro_mult = 0.5
        if macro_regime == "RISK_OFF":
            log.info("RISK_OFF regime — no new stock trades")
            return
        log.info(f"Macro: {macro_regime} size_mult:{macro_mult}x")
    except:
        macro_mult = 0.5  # fail-safe: half-size if macro system is unavailable
        log.warning("Macro file unavailable — defaulting to 0.5x sizing")

    open_positions = get_positions()
    stock_positions = [p for p in open_positions if "USD" not in p.get("symbol","") and len(p.get("symbol","")) <= 6]

    if len(stock_positions) >= MAX_POSITIONS:
        log.info(f"Max positions reached {len(stock_positions)}/{MAX_POSITIONS}")
        return

    # Check exits first
    check_exits(brain)

    # Score all tickers concurrently
    log.info(f"Scanning {len(UNIVERSE)} tickers...")
    results = []

    def scan_ticker(ticker_sector):
        ticker, sector = ticker_sector
        try:
            result = analyze_ticker(ticker, sector, brain, cb, congress_data, ticker_rules)
            if result: return result
        except Exception as e:
            log.debug(f"Scan {ticker}: {e}")
        return None

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(scan_ticker, (t, s)) for t, s in UNIVERSE.items()]
        for f in futures:
            r = f.result()
            if r: results.append(r)

    # Filter: need MIN_SIGNALS signals
    qualified = [(score, signals, data) for score, signals, data in results if score >= MIN_SIGNALS]
    qualified.sort(key=lambda x: x[0], reverse=True)

    brain["total_scans"] += 1
    brain["last_scan"] = datetime.now().isoformat()

    if not qualified:
        log.info(f"No setups found ({len(results)} analyzed)")
        return

    log.info(f"Found {len(qualified)} qualified setups")

    # Take top 2 and ask Claude
    for score, signals, data in qualified[:2]:
        ticker = data["ticker"]
        # Skip if already in position
        if any(p.get("symbol") == ticker for p in stock_positions):
            continue

        # Skip if earnings within 3 days
        try:
            earnings_data = json.load(open("/root/jarvis/jarvis_earnings.json"))
            risk_map = earnings_data.get("risk_map", {})
            if ticker in risk_map:
                risk_info = risk_map[ticker]
                if risk_info["risk"] in ["CRITICAL", "HIGH"]:
                    log.info(f"SKIP {ticker} — earnings {risk_info['days_away']}d away [{risk_info['risk']}]")
                    continue
        except: pass

        log.info(f"Analyzing {ticker} — score {score}/6")
        decision = ask_claude_beast(ticker, score, signals, data, brain)

        if decision and decision["action"] == "BUY" and decision["confidence"] >= MIN_CONFIDENCE:
            result = place_trade(
                ticker, decision["entry"], decision["stop"],
                decision["target"], decision["confidence"],
                decision["reason"], signals
            )
            if result:
                brain["trades"].append({
                    "ts": datetime.now().isoformat(),
                    "ticker": ticker, "entry": decision["entry"],
                    "stop": decision["stop"], "target": decision["target"],
                    "confidence": decision["confidence"],
                    "signals": score, "reason": decision["reason"],
                    "congress": any("CONGRESS" in s for s in signals)
                })
        else:
            action = decision.get("action","?") if decision else "no response"
            conf = decision.get("confidence",0) if decision else 0
            log.info(f"SKIP {ticker}: {action} {conf}%")

def main():
    log.info("🦁 THE BEAST ONLINE — Multi-signal stock scanner")
    tg("🦁 THE BEAST ONLINE\nScanning 50 tickers every 5min\nSignals: Technical+Congress+Sector+Volume+BTC\nPaper trading mode")

    brain = load_brain()
    while True:
        try:
            # Quick regime check before full scan
            import json as _json
            try:
                _macro = _json.load(open("/root/jarvis/jarvis_macro.json"))
                import jarvis_memory_db as _memdb
                _regime = _memdb.get_regime("UNKNOWN")  # canonical regime from jarvis_memory.db
                ctx = get_context(); ctx.set_context("macro_regime", regime, "jarvis_beast")
                _cb = _json.load(open("/root/jarvis/jarvis_central_brain.json"))
                _risk = _cb.get("risk_level","NORMAL")
            except:
                _regime = "UNKNOWN"; _risk = "NORMAL"
            if _regime == "RISK_OFF" or _risk == "EXTREME":
                log.info(f"Beast sleeping — {_regime}/{_risk}")
                _jb_hb.update_bot_heartbeat("jarvis_beast")
                time.sleep(1800)  # 30 min sleep when blocked
                continue
            run_scan(brain)
            save_brain(brain)
        except Exception as e:
            log.error(f"Scan error: {e}")
        _jb_hb.update_bot_heartbeat("jarvis_beast")
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
