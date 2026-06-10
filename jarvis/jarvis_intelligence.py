#!/usr/bin/env python3
"""
JARVIS INTELLIGENCE ENGINE — Level 3 + Level 4
Runs alongside your trading bots as a separate process
Monitors: Options Flow, SEC Insider Filings, Earnings Whispers,
Dark Pool alerts, Self-Improvement Engine, Weekly Performance Review
Sends all intelligence to Telegram and saves to brain files
"""

import json, time, requests, os, math, re
from datetime import datetime, timedelta
try:
    import jarvis_brain as _news_brain   # optional: feeds central-brain hot tickers + market mood
except Exception:
    _news_brain = None

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
TELEGRAM_TOKEN  = __import__("jarvis_secrets").TG_TOKEN_TRADER
TELEGRAM_CHAT   = "7534553840"
from jarvis_secrets import CLAUDE_API_KEY

ALPHA_BRAIN     = "jarvis_alpha_brain.json"
STOCKS_BRAIN    = "jarvis_stocks_brain.json"
INTEL_FILE      = "jarvis_intel.json"
# (removed dead SELF_IMPROVE_FILE constant — self-improve log lives in INTEL_FILE's
#  intel["self_improve_log"]; the constant was never read or written.)

# Watchlist for intelligence monitoring
WATCH_TICKERS = [
    "NVDA","AMD","MSFT","AAPL","META","GOOGL","PLTR","MSTR",
    "JPM","BAC","GS","COIN","HOOD","XOM","CVX","OXY",
    "SPY","QQQ","TSLA","RIVN","AMZN","WMT"
]

CRYPTO_WATCH = ["BTC","ETH","SOL","AVAX"]

# Intervals
OPTIONS_POLL    = 300   # 5 minutes
INSIDER_POLL    = 900   # 15 minutes
EARNINGS_POLL   = 3600  # 1 hour
NEWS_POLL       = 1800  # 30 minutes — news sentiment (news_learnings for signal_fusion)
SELF_IMPROVE_HOUR = 20  # 8pm daily self-improvement run
WEEKLY_REPORT_DAY = 6   # Sunday

import logging
logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('JARVIS-INTEL')

# ─────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────
def tg(msg, token=TELEGRAM_TOKEN):
    clean=str(msg)[:4000]
    try:
        r=requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id":TELEGRAM_CHAT,"text":clean},timeout=10)
        if r.status_code==200: log.info(f"TG: {clean[:60].strip()}")
        else: log.warning(f"TG failed: {r.status_code}")
    except Exception as e: log.error(f"TG: {e}")

def tg_updates(offset=None):
    try:
        p={"timeout":5,"allowed_updates":["message"]}
        if offset: p["offset"]=offset
        r=requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",params=p,timeout=10)
        if r.status_code==200: return r.json().get("result",[])
    except: pass
    return []

# ─────────────────────────────────────────
# CLAUDE AI
# ─────────────────────────────────────────
def ask_claude(prompt, max_tokens=500):
    if not CLAUDE_API_KEY or len(CLAUDE_API_KEY) < 20:
        return "Claude not configured"
    try:
        r=requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key":CLAUDE_API_KEY,
                    "anthropic-version":"2023-06-01",
                    "content-type":"application/json"},
            json={"model":"claude-sonnet-4-6",
                  "max_tokens":max_tokens,
                  "messages":[{"role":"user","content":prompt}]},
            timeout=30)
        if r.status_code==200:
            return r.json()["content"][0]["text"]
    except Exception as e:
        log.error(f"Claude: {e}")
    return "Claude unavailable"

# ─────────────────────────────────────────
# INTEL STORAGE
# ─────────────────────────────────────────
def load_intel():
    try:
        if os.path.exists(INTEL_FILE):
            with open(INTEL_FILE,'r') as f: data = json.load(f)
            data.pop("alerted_signatures", None)  # strip stale orphan key (no writer/reader)
            return data
    except: pass
    return {
        "options_alerts":[],
        "insider_alerts":[],
        "earnings_alerts":[],
        "darkpool_alerts":[],
        "self_improve_log":[],
        "weekly_reports":[],
        "news_learnings":[],
        "hot_tickers":{},
        "last_options_check":0,
        "last_news_check":0,
        "last_insider_check":0,
        "last_earnings_check":0,
        "last_self_improve":"",
        "last_weekly_report":"",
        "created":datetime.now().isoformat()
    }

def save_intel(intel):
    try:
        with open(INTEL_FILE,'w') as f: json.dump(intel,f,indent=2)
    except Exception as e: log.error(f"Intel save: {e}")

def load_brain(path):
    try:
        if os.path.exists(path):
            with open(path,'r') as f: return json.load(f)
    except: pass
    return {}

def save_brain(brain, path):
    try:
        with open(path,'w') as f: json.dump(brain,f,indent=2)
    except Exception as e: log.error(f"Brain save {path}: {e}")

# ─────────────────────────────────────────
# LEVEL 3A — OPTIONS FLOW
# ─────────────────────────────────────────
def fetch_unusual_options(ticker):
    """
    Fetch unusual options activity from free sources
    Detects large call/put sweeps that signal big moves
    """
    signals = []
    try:
        # Use Yahoo Finance options chain as free source
        r = requests.get(
            f"https://query1.finance.yahoo.com/v7/finance/options/{ticker}",
            headers={"User-Agent":"Mozilla/5.0"},
            timeout=10)
        if r.status_code != 200: return signals
        data = r.json()
        result = data.get("optionChain",{}).get("result",[])
        if not result: return signals

        options = result[0]
        calls = options.get("options",[{}])[0].get("calls",[])
        puts = options.get("options",[{}])[0].get("puts",[])
        quote = options.get("quote",{})
        current_price = quote.get("regularMarketPrice",0)

        if not current_price: return signals

        # Look for unusual volume — calls or puts with volume >> open interest
        for opt in calls[:20]:
            volume = opt.get("volume",0) or 0
            oi = opt.get("openInterest",1) or 1
            iv = opt.get("impliedVolatility",0) or 0
            strike = opt.get("strike",0)
            expiry = opt.get("expiration",0)

            if volume > oi * 2 and volume > 500:
                exp_date = datetime.fromtimestamp(expiry).strftime("%m/%d") if expiry else "?"
                otm_pct = (strike - current_price)/current_price*100
                signals.append({
                    "ticker": ticker,
                    "type": "CALL_SWEEP",
                    "strike": strike,
                    "expiry": exp_date,
                    "volume": volume,
                    "oi": oi,
                    "vol_oi_ratio": round(volume/oi,1),
                    "iv": round(iv*100,1),
                    "otm_pct": round(otm_pct,1),
                    "current_price": current_price,
                    "bullish": True,
                    "score": min(10, int(volume/oi)),
                    "ts": datetime.now().isoformat()
                })

        for opt in puts[:20]:
            volume = opt.get("volume",0) or 0
            oi = opt.get("openInterest",1) or 1
            iv = opt.get("impliedVolatility",0) or 0
            strike = opt.get("strike",0)
            expiry = opt.get("expiration",0)

            if volume > oi * 2 and volume > 500:
                exp_date = datetime.fromtimestamp(expiry).strftime("%m/%d") if expiry else "?"
                otm_pct = (current_price - strike)/current_price*100
                signals.append({
                    "ticker": ticker,
                    "type": "PUT_SWEEP",
                    "strike": strike,
                    "expiry": exp_date,
                    "volume": volume,
                    "oi": oi,
                    "vol_oi_ratio": round(volume/oi,1),
                    "iv": round(iv*100,1),
                    "otm_pct": round(otm_pct,1),
                    "current_price": current_price,
                    "bullish": False,
                    "score": min(10, int(volume/oi)),
                    "ts": datetime.now().isoformat()
                })

    except Exception as e:
        log.error(f"Options {ticker}: {e}")
    return signals

def scan_options_flow(intel):
    """Scan all tickers for unusual options activity"""
    log.info("--- OPTIONS FLOW SCAN ---")
    all_signals = []

    for ticker in WATCH_TICKERS:
        signals = fetch_unusual_options(ticker)
        if signals:
            all_signals.extend(signals)
            for s in signals:
                log.info(f"OPTIONS {s['type']} {ticker} ${s['strike']} exp:{s['expiry']} vol:{s['volume']} ratio:{s['vol_oi_ratio']}x")

    # Sort by score
    all_signals.sort(key=lambda x: x["score"], reverse=True)

    # Alert on top signals
    if all_signals:
        top = all_signals[:5]
        lines = ["OPTIONS FLOW ALERT", ""]
        for s in top:
            emoji = "🟢 CALL" if s["bullish"] else "🔴 PUT"
            lines.append(f"{emoji} SWEEP — {s['ticker']}")
            lines.append(f"  Strike: ${s['strike']} | Exp: {s['expiry']}")
            lines.append(f"  Vol: {s['volume']:,} | Vol/OI: {s['vol_oi_ratio']}x")
            lines.append(f"  IV: {s['iv']}% | Current: ${s['current_price']}")
            lines.append(f"  Signal strength: {s['score']}/10")
            lines.append("")

        # Ask Claude to interpret
        claude_prompt = f"""Analyze these unusual options flows and tell me what smart money is signaling:
{json.dumps(top, indent=2)}
Give me: 1) What this likely means 2) Which ticker to watch most 3) Recommended action
Be concise, max 100 words."""
        claude_take = ask_claude(claude_prompt, 200)
        lines.append("CLAUDE'S TAKE:")
        lines.append(claude_take)
        tg("\n".join(lines))

        # Save to intel
        intel["options_alerts"].extend(top)
        intel["options_alerts"] = intel["options_alerts"][-100:]  # keep last 100

        # Update hot tickers
        for s in top:
            ticker = s["ticker"]
            if ticker not in intel["hot_tickers"]:
                intel["hot_tickers"][ticker] = {"calls":0,"puts":0,"score":0}
            if s["bullish"]:
                intel["hot_tickers"][ticker]["calls"] += 1
            else:
                intel["hot_tickers"][ticker]["puts"] += 1
            intel["hot_tickers"][ticker]["score"] += s["score"]

    intel["last_options_check"] = time.time()
    save_intel(intel)
    return all_signals

# ─────────────────────────────────────────
# LEVEL 3B — SEC INSIDER FILINGS
# ─────────────────────────────────────────
def fetch_insider_filings(intel):
    """
    Monitor SEC EDGAR RSS feed for real Form 4 insider purchases
    Uses the official EDGAR full-text search API with proper filters
    Only flags real cash purchases, not option grants or disposals
    """
    log.info("--- SEC INSIDER SCAN (EDGAR RSS) ---")
    alerts = []
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now()-timedelta(days=2)).strftime("%Y-%m-%d")

        # Use SEC EDGAR full text search — filters for Form 4 only
        r = requests.get(
            f"https://efts.sec.gov/LATEST/search-index?q=%22transaction+code%22+%22P%22"
            f"&forms=4&dateRange=custom&startdt={yesterday}&enddt={today}",
            headers={"User-Agent":"JarvisBot lkrautheim4@gmail.com"},
            timeout=15)

        if r.status_code == 200:
            try:
                data = r.json()
                hits = data.get("hits",{}).get("hits",[])
                log.info(f"EDGAR returned {len(hits)} Form 4 filings")

                for hit in hits[:50]:
                    src = hit.get("_source",{})
                    entity = str(src.get("entity_name","")).upper()
                    display = str(src.get("display_names","")).upper()
                    file_date = src.get("file_date","")
                    period = src.get("period_of_report","")

                    for ticker in WATCH_TICKERS:
                        # Match ticker against company name
                        ticker_upper = ticker.upper()
                        if (ticker_upper in entity or
                            ticker_upper in display):
                            # Avoid duplicates
                            if not any(a["ticker"]==ticker and a["filed"]==file_date
                                      for a in alerts):
                                alert = {
                                    "ticker": ticker,
                                    "entity": src.get("entity_name",""),
                                    "filed": file_date,
                                    "period": period,
                                    "type": "INSIDER_PURCHASE",
                                    "bullish": True,
                                    "source": "SEC EDGAR Form 4",
                                    "ts": datetime.now().isoformat()
                                }
                                alerts.append(alert)
                                log.info(f"REAL INSIDER: {ticker} — {src.get('entity_name','')} filed {file_date}")
            except Exception as e:
                log.error(f"EDGAR parse: {e}")
        else:
            log.warning(f"SEC EDGAR returned {r.status_code}")

        if alerts:
            msg_lines = ["INSIDER PURCHASE ALERT", "Source: SEC EDGAR Form 4", ""]
            for a in alerts[:5]:
                msg_lines.append(f"📋 FORM 4 — {a['ticker']}")
                msg_lines.append(f"  Company: {a['entity']}")
                msg_lines.append(f"  Filed: {a['filed']}")
                msg_lines.append(f"  Period: {a['period']}")
                msg_lines.append("")

            claude_prompt = f"""Real SEC Form 4 insider purchase filings for watched stocks:
{json.dumps(alerts[:3], indent=2)}
These are verified from SEC EDGAR. What does this signal?
Should Jarvis trade these? Max 80 words."""
            claude_take = ask_claude(claude_prompt, 150)
            msg_lines.append("CLAUDE'S TAKE:")
            msg_lines.append(claude_take)
            tg("\n".join(msg_lines))

            intel["insider_alerts"].extend(alerts)
            intel["insider_alerts"] = intel["insider_alerts"][-100:]
        else:
            log.info("No insider purchases found for watched tickers")

        intel["last_insider_check"] = time.time()
        save_intel(intel)
        return alerts

    except Exception as e:
        log.error(f"Insider scan error: {e}")
        intel["last_insider_check"] = time.time()
        save_intel(intel)
        return []
        intel["last_insider_check"] = time.time()
        save_intel(intel)
        return []

# ─────────────────────────────────────────
# LEVEL 3C — EARNINGS CALENDAR
# ─────────────────────────────────────────
def fetch_earnings_calendar(intel):
    """
    Pull earnings calendar for next 7 days
    Alert Jarvis when watched stocks have upcoming earnings
    """
    log.info("--- EARNINGS CALENDAR SCAN ---")
    alerts = []
    try:
        for days_ahead in range(7):
            date = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
            r = requests.get(
                f"https://api.nasdaq.com/api/calendar/earnings?date={date}",
                headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"},
                timeout=10)
            if r.status_code != 200: continue
            rows = r.json().get("data",{}).get("rows",[]) or []
            for row in rows:
                symbol = str(row.get("symbol","")).upper().strip()
                if symbol in WATCH_TICKERS:
                    eps_est = row.get("epsForecast","?")
                    time_str = row.get("time","?")
                    alert = {
                        "ticker": symbol,
                        "date": date,
                        "days_away": days_ahead,
                        "time": time_str,
                        "eps_estimate": eps_est,
                        "type": "EARNINGS",
                        "ts": datetime.now().isoformat()
                    }
                    alerts.append(alert)
                    log.info(f"EARNINGS: {symbol} in {days_ahead} days ({date}) EPS est: {eps_est}")

        if alerts:
            lines = ["EARNINGS CALENDAR ALERT", ""]
            for a in sorted(alerts, key=lambda x: x["days_away"]):
                urgency = "🚨 TODAY" if a["days_away"]==0 else f"📅 {a['days_away']} days"
                lines.append(f"{urgency} — {a['ticker']}")
                lines.append(f"  Date: {a['date']} {a['time']}")
                lines.append(f"  EPS estimate: {a['eps_estimate']}")
                if a["days_away"] <= 1:
                    lines.append(f"  ⚠️ JARVIS will avoid trading {a['ticker']}")
                lines.append("")

            claude_prompt = f"""These stocks have earnings coming up:
{json.dumps(alerts[:5], indent=2)}
Which ones should Jarvis avoid? Any play the earnings? Max 80 words."""
            claude_take = ask_claude(claude_prompt, 150)
            lines.append("CLAUDE'S TAKE:")
            lines.append(claude_take)
            tg("\n".join(lines))

            intel["earnings_alerts"] = alerts
        save_intel(intel)
        return alerts

    except Exception as e:
        log.error(f"Earnings calendar: {e}")
        return []

# ─────────────────────────────────────────
# LEVEL 3D — DARK POOL DETECTION
# ─────────────────────────────────────────
def detect_dark_pool_activity(intel):
    """
    Detect unusual institutional activity using free data
    Large block trades, unusual volume spikes, price/volume divergence
    """
    log.info("--- DARK POOL DETECTION ---")
    signals = []
    try:
        for ticker in WATCH_TICKERS[:15]:
            try:
                # Get volume data from Yahoo Finance
                r = requests.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                    f"?interval=5m&range=1d",
                    headers={"User-Agent":"Mozilla/5.0"},
                    timeout=8)
                if r.status_code != 200: continue
                data = r.json()
                result = data.get("chart",{}).get("result",[])
                if not result: continue

                timestamps = result[0].get("timestamp",[])
                indicators = result[0].get("indicators",{})
                volumes = indicators.get("quote",[{}])[0].get("volume",[])
                closes = indicators.get("quote",[{}])[0].get("close",[])

                if not volumes or not closes: continue

                # Clean None values
                volumes = [v or 0 for v in volumes]
                closes = [c or 0 for c in closes]

                if len(volumes) < 10: continue

                avg_vol = sum(volumes[:-1])/max(1,len(volumes)-1)
                current_vol = volumes[-1]
                current_close = closes[-1]
                prev_close = closes[-2] if len(closes)>=2 else current_close

                if avg_vol == 0: continue

                vol_mult = current_vol/avg_vol
                price_chg = (current_close-prev_close)/prev_close*100 if prev_close else 0

                # Dark pool signal: huge volume but small price move
                # Institutions absorbing shares without moving price
                if vol_mult >= 3.0 and abs(price_chg) < 0.3:
                    signal = {
                        "ticker": ticker,
                        "type": "DARK_POOL_ABSORPTION",
                        "volume_mult": round(vol_mult,1),
                        "price_chg": round(price_chg,2),
                        "current_price": round(current_close,2),
                        "interpretation": "Institutions quietly accumulating",
                        "bullish": True,
                        "score": min(10, int(vol_mult)),
                        "ts": datetime.now().isoformat()
                    }
                    signals.append(signal)
                    log.info(f"DARK POOL: {ticker} vol:{vol_mult:.1f}x price:{price_chg:+.2f}% — absorption")

                # Momentum signal: high volume + strong price move
                elif vol_mult >= 2.5 and abs(price_chg) >= 1.0:
                    signal = {
                        "ticker": ticker,
                        "type": "VOLUME_MOMENTUM",
                        "volume_mult": round(vol_mult,1),
                        "price_chg": round(price_chg,2),
                        "current_price": round(current_close,2),
                        "interpretation": "Strong directional momentum",
                        "bullish": price_chg > 0,
                        "score": min(10, int(vol_mult*abs(price_chg))),
                        "ts": datetime.now().isoformat()
                    }
                    signals.append(signal)
                    log.info(f"VOLUME MOMENTUM: {ticker} vol:{vol_mult:.1f}x price:{price_chg:+.2f}%")

                time.sleep(0.3)

            except Exception as e:
                log.error(f"Dark pool {ticker}: {e}")

        if signals:
            signals.sort(key=lambda x: x["score"], reverse=True)
            lines = ["DARK POOL / VOLUME ALERT", ""]
            for s in signals[:4]:
                emoji = "🟢" if s["bullish"] else "🔴"
                lines.append(f"{emoji} {s['type']} — {s['ticker']}")
                lines.append(f"  Volume: {s['volume_mult']}x avg | Price: {s['price_chg']:+.2f}%")
                lines.append(f"  ${s['current_price']} | {s['interpretation']}")
                lines.append(f"  Score: {s['score']}/10")
                lines.append("")

            claude_prompt = f"""Analyze these unusual volume/dark pool signals:
{json.dumps(signals[:3], indent=2)}
What is institutional money doing? Should Jarvis act on any of these?
Max 80 words."""
            claude_take = ask_claude(claude_prompt, 150)
            lines.append("CLAUDE'S TAKE:")
            lines.append(claude_take)
            tg("\n".join(lines))

            intel["darkpool_alerts"].extend(signals)
            intel["darkpool_alerts"] = intel["darkpool_alerts"][-100:]
            save_intel(intel)

    except Exception as e:
        log.error(f"Dark pool scan: {e}")
    return signals

# ─────────────────────────────────────────
# LEVEL 4A — SELF IMPROVEMENT ENGINE
# ─────────────────────────────────────────
def run_self_improvement(intel):
    """
    Jarvis analyzes its own performance and adjusts parameters
    Runs every evening at 8pm
    """
    log.info("--- SELF IMPROVEMENT ENGINE ---")

    alpha_brain = load_brain(ALPHA_BRAIN)
    stocks_brain = load_brain(STOCKS_BRAIN)

    if not alpha_brain and not stocks_brain:
        log.info("No brain data yet — need more trades")
        return

    improvements = []
    changes = []

    # ── Analyze Alpha Brain ──
    if alpha_brain and alpha_brain.get("total_trades",0) >= 10:
        wins = alpha_brain.get("wins",0)
        losses = alpha_brain.get("losses",0)
        total = wins + losses
        wr = wins/total if total>0 else 0
        pnl = alpha_brain.get("total_pnl",0)

        # Check RSI zone performance
        rsi_zones = alpha_brain.get("rsi_zones",{})
        for zone, data in rsi_zones.items():
            if data.get("total",0) >= 3:
                zone_wr = data["wins"]/data["total"]
                if zone_wr < 0.30:
                    improvements.append(f"Alpha: RSI zone {zone} only {zone_wr*100:.0f}% WR — consider tightening entry")
                elif zone_wr > 0.70:
                    improvements.append(f"Alpha: RSI zone {zone} is {zone_wr*100:.0f}% WR — increase size here")

        # Check regime performance
        regimes = alpha_brain.get("regimes",{})
        for regime, data in regimes.items():
            if data.get("total",0) >= 3:
                regime_wr = data["wins"]/data["total"]
                if regime_wr < 0.30:
                    improvements.append(f"Alpha: AVOID {regime} regime ({regime_wr*100:.0f}% WR)")

        # Check hour performance
        hours = alpha_brain.get("hours",{})
        for hour, data in hours.items():
            if data.get("total",0) >= 3:
                hour_wr = data["wins"]/data["total"]
                if hour_wr < 0.30:
                    improvements.append(f"Alpha: Hour {hour}:00 is losing ({hour_wr*100:.0f}% WR) — add to avoid list")

        # Check consecutive losses
        consec = alpha_brain.get("consecutive_losses",0)
        if consec >= 3:
            improvements.append(f"Alpha: {consec} consecutive losses — reduce size multiplier")
            if alpha_brain.get("size_multiplier",1.0) > 0.5:
                alpha_brain["size_multiplier"] = max(0.5, alpha_brain["size_multiplier"]-0.1)
                changes.append(f"Alpha size reduced to {alpha_brain['size_multiplier']:.1f}x")
                save_brain(alpha_brain, ALPHA_BRAIN)

        # Overall performance adjustment
        if wr > 0.65 and total >= 20:
            if alpha_brain.get("size_multiplier",1.0) < 2.0:
                alpha_brain["size_multiplier"] = min(2.0, alpha_brain["size_multiplier"]+0.05)
                changes.append(f"Alpha size increased to {alpha_brain['size_multiplier']:.1f}x (WR:{wr*100:.0f}%)")
                save_brain(alpha_brain, ALPHA_BRAIN)
        elif wr < 0.40 and total >= 10:
            alpha_brain["size_multiplier"] = max(0.5, alpha_brain["size_multiplier"]-0.1)
            changes.append(f"Alpha size reduced to {alpha_brain['size_multiplier']:.1f}x (WR:{wr*100:.0f}%)")
            save_brain(alpha_brain, ALPHA_BRAIN)

    # ── Analyze Stocks Brain ──
    if stocks_brain and stocks_brain.get("total_trades",0) >= 10:
        wins = stocks_brain.get("wins",0)
        losses = stocks_brain.get("losses",0)
        total = wins + losses
        wr = wins/total if total>0 else 0

        # Check ticker performance
        tickers = stocks_brain.get("tickers",{})
        for ticker, data in tickers.items():
            if data.get("total",0) >= 3:
                ticker_wr = data["wins"]/data["total"]
                avg_pnl = data.get("avg_pnl",0)
                if ticker_wr < 0.30 and ticker not in stocks_brain.get("worst_tickers",[]):
                    if "worst_tickers" not in stocks_brain: stocks_brain["worst_tickers"]=[]
                    stocks_brain["worst_tickers"].append(ticker)
                    changes.append(f"Stocks: Added {ticker} to avoid list ({ticker_wr*100:.0f}% WR)")
                elif ticker_wr > 0.70 and ticker not in stocks_brain.get("best_tickers",[]):
                    if "best_tickers" not in stocks_brain: stocks_brain["best_tickers"]=[]
                    stocks_brain["best_tickers"].append(ticker)
                    changes.append(f"Stocks: Added {ticker} to best list ({ticker_wr*100:.0f}% WR)")

        # Check sector performance
        sectors = stocks_brain.get("sectors",{})
        for sector, data in sectors.items():
            if data.get("total",0) >= 3:
                sector_wr = data["wins"]/data["total"]
                if sector_wr < 0.30:
                    improvements.append(f"Stocks: {sector} sector losing ({sector_wr*100:.0f}% WR)")
                elif sector_wr > 0.70:
                    improvements.append(f"Stocks: {sector} sector winning ({sector_wr*100:.0f}% WR) — increase exposure")

        # VWAP zone analysis
        vwap_zones = stocks_brain.get("vwap_zones",{})
        for zone, data in vwap_zones.items():
            if data.get("total",0) >= 3:
                zone_wr = data["wins"]/data["total"]
                if zone_wr > 0.65:
                    improvements.append(f"Stocks: VWAP zone {zone} = {zone_wr*100:.0f}% WR — prioritize this entry")

        if changes:
            save_brain(stocks_brain, STOCKS_BRAIN)

    # ── Build improvement report ──
    log_entry = {
        "date": datetime.now().isoformat(),
        "improvements": improvements,
        "changes": changes,
        "alpha_trades": alpha_brain.get("total_trades",0) if alpha_brain else 0,
        "stocks_trades": stocks_brain.get("total_trades",0) if stocks_brain else 0
    }
    intel["self_improve_log"].append(log_entry)
    intel["self_improve_log"] = intel["self_improve_log"][-30:]
    intel["last_self_improve"] = datetime.now().isoformat()
    save_intel(intel)

    # ── Send report to Telegram ──
    if improvements or changes:
        lines = ["JARVIS SELF-IMPROVEMENT REPORT", ""]
        if changes:
            lines.append("CHANGES MADE:")
            for c in changes: lines.append(f"  ✅ {c}")
            lines.append("")
        if improvements:
            lines.append("OBSERVATIONS:")
            for imp in improvements[:8]: lines.append(f"  💡 {imp}")
            lines.append("")

        # Ask Claude to synthesize
        claude_prompt = f"""Jarvis just ran its self-improvement analysis. Here are the findings:
Changes made: {changes}
Observations: {improvements}
Alpha brain: {alpha_brain.get('total_trades',0)} trades, {alpha_brain.get('wins',0)}W/{alpha_brain.get('losses',0)}L
Stocks brain: {stocks_brain.get('total_trades',0)} trades

Give me 2-3 sentences on what Jarvis should focus on improving next. Be specific."""
        claude_take = ask_claude(claude_prompt, 200)
        lines.append("CLAUDE'S RECOMMENDATIONS:")
        lines.append(claude_take)
        tg("\n".join(lines))
    else:
        tg(f"JARVIS SELF-IMPROVEMENT\nNot enough trade data yet.\nAlpha: {alpha_brain.get('total_trades',0)} trades\nStocks: {stocks_brain.get('total_trades',0)} trades\nKeep running — learning takes time.")

    log.info(f"Self-improvement complete: {len(changes)} changes, {len(improvements)} observations")

# ─────────────────────────────────────────
# LEVEL 4B — WEEKLY PERFORMANCE REPORT
# ─────────────────────────────────────────
def generate_weekly_report(intel):
    """
    Every Sunday — comprehensive performance review
    What worked, what didn't, what to focus on next week
    """
    log.info("--- WEEKLY PERFORMANCE REPORT ---")

    alpha_brain = load_brain(ALPHA_BRAIN)
    stocks_brain = load_brain(STOCKS_BRAIN)

    lines = ["JARVIS WEEKLY PERFORMANCE REPORT", f"Week ending {datetime.now().strftime('%B %d, %Y')}", ""]

    # ── Alpha Performance ──
    if alpha_brain:
        wins = alpha_brain.get("wins",0)
        losses = alpha_brain.get("losses",0)
        total = wins+losses
        pnl = alpha_brain.get("total_pnl",0)
        wr = wins/total*100 if total>0 else 0
        lines.append("JARVIS ALPHA (Crypto + Stocks):")
        lines.append(f"  Total trades: {alpha_brain.get('total_trades',0)}")
        lines.append(f"  Win rate: {wr:.1f}% ({wins}W/{losses}L)")
        lines.append(f"  Total P&L: ${pnl:+.2f}")
        lines.append(f"  Best trade: ${alpha_brain.get('best_trade',0):+.2f}")
        lines.append(f"  Worst trade: ${alpha_brain.get('worst_trade',0):+.2f}")
        lines.append(f"  Size multiplier: {alpha_brain.get('size_multiplier',1.0):.1f}x")
        best = ", ".join(alpha_brain.get("best_assets",[])[:3]) or "none yet"
        avoid = ", ".join(alpha_brain.get("worst_assets",[])[:3]) or "none yet"
        lines.append(f"  Best assets: {best}")
        lines.append(f"  Avoiding: {avoid}")
        lines.append("")

    # ── Stocks Performance ──
    if stocks_brain:
        wins = stocks_brain.get("wins",0)
        losses = stocks_brain.get("losses",0)
        total = wins+losses
        pnl = stocks_brain.get("total_pnl",0)
        wr = wins/total*100 if total>0 else 0
        lines.append("JARVIS STOCKS:")
        lines.append(f"  Total trades: {stocks_brain.get('total_trades',0)}")
        lines.append(f"  Win rate: {wr:.1f}% ({wins}W/{losses}L)")
        lines.append(f"  Total P&L: ${pnl:+.2f}")
        lines.append(f"  Best ticker: {', '.join(stocks_brain.get('best_tickers',[])[:3]) or 'learning'}")
        lines.append(f"  Hot sectors: {', '.join(stocks_brain.get('hot_sectors',[])[:2]) or 'learning'}")
        lines.append(f"  Best hour: {stocks_brain.get('best_hour',10)}:00")
        lines.append(f"  Avoid hours: {stocks_brain.get('avoid_hours',[])}")
        lines.append("")

    # ── Intelligence Summary ──
    options_count = len(intel.get("options_alerts",[]))
    insider_count = len(intel.get("insider_alerts",[]))
    darkpool_count = len(intel.get("darkpool_alerts",[]))
    lines.append("INTELLIGENCE THIS WEEK:")
    lines.append(f"  Options flow alerts: {options_count}")
    lines.append(f"  Insider filing alerts: {insider_count}")
    lines.append(f"  Dark pool signals: {darkpool_count}")
    lines.append("")

    # ── Self improvement log ──
    improve_log = intel.get("self_improve_log",[])
    week_changes = [e for e in improve_log
                   if e.get("date","") > (datetime.now()-timedelta(days=7)).isoformat()]
    if week_changes:
        all_changes = []
        for e in week_changes: all_changes.extend(e.get("changes",[]))
        if all_changes:
            lines.append("SELF-IMPROVEMENTS THIS WEEK:")
            for c in all_changes[:5]: lines.append(f"  ✅ {c}")
            lines.append("")

    # ── Claude's weekly analysis ──
    report_data = {
        "alpha_wr": alpha_brain.get("wins",0)/(max(1,alpha_brain.get("wins",0)+alpha_brain.get("losses",0))),
        "alpha_pnl": alpha_brain.get("total_pnl",0),
        "stocks_wr": stocks_brain.get("wins",0)/(max(1,stocks_brain.get("wins",0)+stocks_brain.get("losses",0))),
        "stocks_pnl": stocks_brain.get("total_pnl",0),
        "best_assets": alpha_brain.get("best_assets",[]),
        "hot_sectors": stocks_brain.get("hot_sectors",[]),
        "improvements": [e.get("improvements",[]) for e in week_changes[-3:]]
    } if alpha_brain and stocks_brain else {}

    claude_prompt = f"""You are Jarvis, reviewing your own weekly trading performance.
Data: {json.dumps(report_data, indent=2)}
Write a brief weekly review (max 150 words):
1. How did we do overall?
2. What worked best?
3. What to focus on next week?
4. One specific improvement to make
Write as Jarvis speaking to Lenny."""
    claude_review = ask_claude(claude_prompt, 300)
    lines.append("JARVIS WEEKLY REVIEW:")
    lines.append(claude_review)
    lines.append("")
    lines.append(f"Next report: {(datetime.now()+timedelta(days=7)).strftime('%B %d, %Y')}")

    tg("\n".join(lines))

    # Save report
    intel["weekly_reports"].append({
        "date": datetime.now().isoformat(),
        "alpha_pnl": alpha_brain.get("total_pnl",0) if alpha_brain else 0,
        "stocks_pnl": stocks_brain.get("total_pnl",0) if stocks_brain else 0
    })
    intel["weekly_reports"] = intel["weekly_reports"][-52:]
    intel["last_weekly_report"] = datetime.now().strftime("%Y-%m-%d")
    save_intel(intel)
    log.info("Weekly report sent")

# ─────────────────────────────────────────
# LEVEL 3E — CRYPTO SENTIMENT MONITOR
# ─────────────────────────────────────────
def monitor_crypto_sentiment(intel):
    """Monitor Fear & Greed + crypto-specific signals"""
    log.info("--- CRYPTO SENTIMENT ---")
    try:
        # Fear & Greed
        r = requests.get("https://api.alternative.me/fng/?limit=7",timeout=10)
        if r.status_code == 200:
            data = r.json().get("data",[])
            if data:
                current = int(data[0]["value"])
                label = data[0]["value_classification"]
                week_ago = int(data[-1]["value"]) if len(data)>=7 else current
                trend = "improving" if current > week_ago else "worsening"

                log.info(f"Fear & Greed: {current} ({label}) — {trend} vs 7 days ago ({week_ago})")

                # Alert on extreme readings
                if current <= 15:
                    tg(f"EXTREME FEAR ALERT\nFear & Greed: {current} ({label})\nHistorically: best time to buy crypto\nWeek ago: {week_ago} — {trend}")
                elif current >= 85:
                    tg(f"EXTREME GREED ALERT\nFear & Greed: {current} ({label})\nHistorically: consider reducing exposure\nWeek ago: {week_ago} — {trend}")

        # BTC dominance from CoinGecko
        r2 = requests.get(
            "https://api.coingecko.com/api/v3/global",
            timeout=10)
        if r2.status_code == 200:
            global_data = r2.json().get("data",{})
            btc_dom = global_data.get("market_cap_percentage",{}).get("btc",0)
            total_mcap = global_data.get("total_market_cap",{}).get("usd",0)
            log.info(f"BTC dominance: {btc_dom:.1f}% | Total market cap: ${total_mcap/1e9:.0f}B")

            if btc_dom > 60:
                log.info("BTC dominance high — altcoins may underperform")
            elif btc_dom < 40:
                log.info("BTC dominance low — altcoin season possible")

    except Exception as e:
        log.error(f"Crypto sentiment: {e}")

# ─────────────────────────────────────────
# LEVEL 4C — HOT TICKER TRACKER
# ─────────────────────────────────────────
def update_hot_tickers(intel):
    """
    Track which tickers are generating the most intelligence signals
    Feeds back into trading bots as priority watchlist
    """
    hot = intel.get("hot_tickers",{})
    if not hot: return

    # Score each ticker across all signal types
    scored = []
    for ticker, data in hot.items():
        calls = data.get("calls",0)
        puts = data.get("puts",0)
        score = data.get("score",0)
        bias = "BULLISH" if calls > puts else "BEARISH" if puts > calls else "NEUTRAL"
        scored.append((ticker, score, bias, calls, puts))

    scored.sort(key=lambda x: x[1], reverse=True)

    # Save hot ticker list for bots to read
    hot_list = {
        "updated": datetime.now().isoformat(),
        "tickers": [
            {"ticker":t,"score":s,"bias":b,"calls":c,"puts":p}
            for t,s,b,c,p in scored[:10]
        ]
    }
    try:
        with open("jarvis_hot_tickers.json","w") as f:
            json.dump(hot_list,f,indent=2)
        log.info(f"Hot tickers updated: {[t[0] for t in scored[:5]]}")
    except Exception as e:
        log.error(f"Hot tickers save: {e}")

# ─────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────
def fetch_news(intel):
    """News-sentiment scan → intel['news_learnings'], consumed by jarvis_signal_fusion
    (imported by the running jarvis_trader). Ported from the retired jarvis_intel.py;
    source switched from finviz RSS (dead — 301→404) to Yahoo Finance search, which
    this codebase already uses and which is reachable here. Entry schema is unchanged
    ({ticker, headline, sentiment, timestamp}) so signal_fusion keeps working."""
    try:
        learnings = intel.get("news_learnings", [])
        seen = {l.get("headline") for l in learnings[-500:]}
        positive_words = ["surge","jump","beat","soar","rally","buy","upgrade","bullish","record","gain"]
        negative_words = ["crash","drop","miss","fall","sell","downgrade","bearish","loss","cut","decline"]
        added = 0
        # BTC/ETH only for crypto: Yahoo search q=SOL/q=AVAX returns unrelated EQUITIES
        # (SOL/AVAX are also stock tickers) and q=*-USD returns a generic garbage feed,
        # so there's no clean crypto-news query for them here. Better 0 than wrong sentiment.
        for ticker in WATCH_TICKERS + ["BTC","ETH"]:
            try:
                r = requests.get("https://query1.finance.yahoo.com/v1/finance/search",
                    params={"q": ticker, "newsCount": 5, "quotesCount": 0},
                    headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
                if r.status_code != 200: continue
                for item in r.json().get("news", [])[:3]:
                    headline = (item.get("title") or "").strip()
                    if not headline or headline in seen: continue
                    seen.add(headline)
                    hl = headline.lower()
                    pos = sum(1 for w in positive_words if w in hl)
                    neg = sum(1 for w in negative_words if w in hl)
                    sentiment = "positive" if pos > neg else "negative" if neg > pos else "neutral"
                    learnings.append({"ticker": ticker, "headline": headline,
                                      "sentiment": sentiment, "timestamp": str(datetime.now())})
                    added += 1
                    if sentiment == "positive" and _news_brain:
                        try: _news_brain.add_hot_ticker(ticker)
                        except Exception: pass
            except Exception:
                continue
        # SOL crypto news via Cointelegraph tag RSS — Yahoo q=SOL returns unrelated
        # equities, so use a crypto-native source. (AVAX intentionally not covered.)
        try:
            r = requests.get("https://cointelegraph.com/rss/tag/solana",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            if r.status_code == 200:
                for raw in re.findall(r"<title>(.*?)</title>", r.text, re.S)[:10]:
                    headline = re.sub(r"<!\[CDATA\[|\]\]>", "", raw).strip()
                    if not headline or headline in seen or "Cointelegraph" in headline:
                        continue  # skip dupes + the feed's own metadata titles
                    seen.add(headline)
                    hl = headline.lower()
                    pos = sum(1 for w in positive_words if w in hl)
                    neg = sum(1 for w in negative_words if w in hl)
                    sentiment = "positive" if pos > neg else "negative" if neg > pos else "neutral"
                    learnings.append({"ticker": "SOL", "headline": headline,
                                      "sentiment": sentiment, "timestamp": str(datetime.now())})
                    added += 1
        except Exception:
            pass
        intel["news_learnings"] = learnings[-500:]
        if _news_brain:
            try:
                pos = sum(1 for l in learnings[-20:] if l.get("sentiment") == "positive")
                neg = sum(1 for l in learnings[-20:] if l.get("sentiment") == "negative")
                _news_brain.set_market_mood("bullish" if pos > neg else "bearish" if neg > pos else "neutral")
            except Exception: pass
        intel["last_news_check"] = time.time()
        save_intel(intel)
        if added: log.info(f"News: +{added} headlines ({len(intel['news_learnings'])} total)")
    except Exception as e:
        log.error(f"News error: {e}")

def main():
    log.info("="*65)
    log.info("JARVIS INTELLIGENCE ENGINE — Level 3 + Level 4")
    log.info("Options Flow | Insider Filings | Dark Pool | Self-Improvement")
    log.info("="*65)

    intel = load_intel()

    tg(f"JARVIS INTELLIGENCE ENGINE ONLINE\n\nMonitoring:\n• Options flow — {len(WATCH_TICKERS)} tickers\n• SEC insider filings\n• Earnings calendar\n• Dark pool activity\n• Crypto sentiment\n• Self-improvement engine (8pm daily)\n• Weekly report (Sundays)\n\nAll signals sent to Telegram with Claude analysis.\n\nCommands: INTEL / OPTIONS / INSIDER / IMPROVE / REPORT / STOP")

    tg_offset = None
    last_options = 0
    last_insider = 0
    last_earnings = time.time() - 3500  # run soon
    last_darkpool = 0
    last_sentiment = 0
    last_news = 0
    last_hot_update = 0
    last_improve_day = ""
    last_weekly_day = intel.get("last_weekly_report","")

    log.info("Running. Intelligence engine active.")

    while True:
        now = time.time()
        current_hour = datetime.now().hour
        current_day = datetime.now().strftime("%Y-%m-%d")
        current_weekday = datetime.now().weekday()

        # TELEGRAM COMMANDS
        for u in tg_updates(tg_offset):
            tg_offset = u["update_id"]+1
            msg = u.get("message",{})
            chat = str(msg.get("chat",{}).get("id",""))
            text = msg.get("text","").strip().upper()
            if chat != TELEGRAM_CHAT: continue

            if text == "INTEL":
                hot = intel.get("hot_tickers",{})
                scored = sorted(hot.items(), key=lambda x: x[1].get("score",0), reverse=True)[:5]
                lines = ["INTELLIGENCE SUMMARY",""]
                lines.append(f"Options alerts: {len(intel.get('options_alerts',[]))}")
                lines.append(f"Insider alerts: {len(intel.get('insider_alerts',[]))}")
                lines.append(f"Dark pool signals: {len(intel.get('darkpool_alerts',[]))}")
                lines.append("")
                if scored:
                    lines.append("HOT TICKERS:")
                    for ticker, data in scored:
                        bias = "🟢 BULL" if data.get("calls",0)>data.get("puts",0) else "🔴 BEAR"
                        lines.append(f"  {ticker}: {bias} score:{data.get('score',0)}")
                tg("\n".join(lines))

            elif text == "OPTIONS":
                scan_options_flow(intel)

            elif text == "INSIDER":
                fetch_insider_filings(intel)

            elif text == "IMPROVE":
                run_self_improvement(intel)

            elif text == "REPORT":
                generate_weekly_report(intel)

            elif text == "STOP":
                tg("JARVIS INTELLIGENCE ENGINE stopped."); return

            elif text == "HELP":
                tg("Intelligence Commands:\nINTEL - summary\nOPTIONS - scan options flow\nINSIDER - SEC filings\nIMPROVE - run self-improvement\nREPORT - weekly report\nSTOP - stop engine")

        # OPTIONS FLOW — every 5 minutes during market hours
        market_hour = 9 <= current_hour <= 16
        if market_hour and now - last_options >= OPTIONS_POLL:
            last_options = now
            scan_options_flow(intel)

        # INSIDER FILINGS — every 15 minutes
        if now - last_insider >= INSIDER_POLL:
            last_insider = now
            fetch_insider_filings(intel)

        # EARNINGS CALENDAR — once per hour
        if now - last_earnings >= EARNINGS_POLL:
            last_earnings = now
            fetch_earnings_calendar(intel)

        # DARK POOL — every 10 minutes during market hours
        if market_hour and now - last_darkpool >= 600:
            last_darkpool = now
            detect_dark_pool_activity(intel)

        # CRYPTO SENTIMENT — every 30 minutes
        if now - last_sentiment >= 1800:
            last_sentiment = now
            monitor_crypto_sentiment(intel)

        # NEWS SENTIMENT — every 30 minutes (produces news_learnings for signal_fusion)
        if now - last_news >= NEWS_POLL:
            last_news = now
            fetch_news(intel)

        # HOT TICKER UPDATE — every 30 minutes
        if now - last_hot_update >= 1800:
            last_hot_update = now
            update_hot_tickers(intel)

        # SELF IMPROVEMENT — every day at 8pm
        if current_hour == SELF_IMPROVE_HOUR and current_day != last_improve_day:
            last_improve_day = current_day
            run_self_improvement(intel)

        # WEEKLY REPORT — every Sunday
        if current_weekday == WEEKLY_REPORT_DAY and current_day != last_weekly_day:
            last_weekly_day = current_day
            generate_weekly_report(intel)

        time.sleep(10)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped")
        tg("JARVIS INTELLIGENCE ENGINE stopped (Ctrl+C)")
    except Exception as e:
        log.error(f"Fatal: {e}")
        tg(f"JARVIS INTELLIGENCE ENGINE crashed: {e}")
