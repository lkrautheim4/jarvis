import jarvis_brain as _jb_hb
#!/usr/bin/env python3
"""
JARVIS MACRO ENGINE
Detects market regime, tracks macro events, correlations.
Runs every 30 minutes. Feeds regime signal to all bots.
"""
import requests, json, os, time, logging, math
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("JARVIS_MACRO")

BRAIN_FILE  = "/root/jarvis/jarvis_central_brain.json"
MACRO_FILE  = "/root/jarvis/jarvis_macro.json"
TG_TOKEN    = __import__("jarvis_secrets").TG_TOKEN_TRADER
CHAT_ID     = "7534553840"
INTERVAL    = 1800  # 30 minutes

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

def tg(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg}, timeout=10)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def load(f, default=None):
    try: return json.load(open(f))
    except: return default or {}

def save(f, data):
    with open(f, 'w') as fp: json.dump(data, fp, indent=2)

def safe_get(url, params=None, timeout=8):
    try:
        r = SESSION.get(url, params=params, timeout=timeout)
        if r.status_code == 200: return r.json()
    except: pass
    return None

# ── DATA FETCHERS ─────────────────────────────────────────

def get_vix():
    """VIX from CBOE CSV"""
    try:
        r = SESSION.get("https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv", timeout=8)
        lines = r.text.strip().split('\n')
        # Last two rows for direction
        if len(lines) >= 3:
            last = lines[-1].split(',')
            prev = lines[-2].split(',')
            vix_now = float(last[4])   # Close
            vix_prev = float(prev[4])
            direction = "FALLING" if vix_now < vix_prev else "RISING"
            return {"value": vix_now, "prev": vix_prev, "direction": direction}
    except Exception as e:
        log.warning(f"VIX: {e}")
    return {"value": 20.0, "prev": 20.0, "direction": "STABLE"}

def get_fear_greed_history():
    """Fear & Greed last 7 days (crypto)"""
    try:
        d = safe_get("https://api.alternative.me/fng/?limit=7")
        if d and "data" in d:
            values = [int(x["value"]) for x in d["data"]]
            return {
                "current": values[0],
                "prev_day": values[1] if len(values) > 1 else values[0],
                "week_avg": round(sum(values)/len(values)),
                "label": d["data"][0]["value_classification"],
                "trend": "IMPROVING" if values[0] > values[1] else "DETERIORATING"
            }
    except: pass
    return {"current": 50, "prev_day": 50, "week_avg": 50, "label": "Neutral", "trend": "STABLE"}

def get_equity_fear_greed():
    """Equity Fear & Greed from CNN"""
    try:
        r = SESSION.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            score = data.get("fear_and_greed", {}).get("score")
            if score is not None:
                return {
                    "value": int(score),
                    "ts": datetime.now().isoformat()
                }
    except Exception as e:
        log.warning(f"Equity F&G fetch failed: {e}")
    return None

def get_yahoo_price(symbol):
    """Price + change from Yahoo Finance"""
    try:
        d = safe_get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"interval": "1d", "range": "5d"})
        if d:
            result = d["chart"]["result"][0]
            closes = result["indicators"]["quote"][0]["close"]
            closes = [c for c in closes if c is not None]
            if len(closes) >= 2:
                change_1d = round((closes[-1]-closes[-2])/closes[-2]*100, 2)
                change_5d = round((closes[-1]-closes[0])/closes[0]*100, 2)
                return {"price": round(closes[-1], 2), "change_1d": change_1d, "change_5d": change_5d}
    except: pass
    return {"price": 0, "change_1d": 0, "change_5d": 0}

def get_treasury_yield():
    """10yr yield from Yahoo Finance (^TNX)"""
    try:
        d = safe_get("https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX",
            params={"interval": "1d", "range": "5d"})
        if d:
            closes = d["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            closes = [c for c in closes if c is not None]
            if len(closes) >= 2:
                direction = "RISING" if closes[-1] > closes[-2] else "FALLING"
                return {"value": round(closes[-1], 2), "prev": round(closes[-2], 2), "direction": direction}
    except Exception as e:
        log.warning(f"Treasury yield: {e}")
    return {"value": 4.3, "prev": 4.3, "direction": "STABLE"}

def get_put_call_ratio():
    """Put/Call ratio from Yahoo options data on SPY"""
    try:
        d = safe_get("https://query1.finance.yahoo.com/v7/finance/options/SPY")
        if d:
            result = d["optionChain"]["result"][0]
            calls = result.get("options", [{}])[0].get("calls", [])
            puts  = result.get("options", [{}])[0].get("puts", [])
            total_call_vol = sum(c.get("volume", 0) or 0 for c in calls)
            total_put_vol  = sum(p.get("volume", 0) or 0 for p in puts)
            if total_call_vol > 0:
                pcr = round(total_put_vol / total_call_vol, 2)
                sentiment = "FEAR" if pcr > 1.2 else "GREED" if pcr < 0.7 else "NEUTRAL"
                return {"ratio": pcr, "sentiment": sentiment,
                        "call_vol": total_call_vol, "put_vol": total_put_vol}
    except Exception as e:
        log.warning(f"Put/Call: {e}")
    return {"ratio": 1.0, "sentiment": "NEUTRAL", "call_vol": 0, "put_vol": 0}

def get_macro_events():
    """Scrape upcoming macro events — Fed, CPI, Jobs"""
    events = []
    try:
        # Use Investing.com economic calendar via scraping
        r = SESSION.get(
            "https://www.investing.com/economic-calendar/",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
            timeout=10)
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, 'html.parser')
        rows = soup.find_all('tr', {'class': lambda x: x and 'js-event-item' in x})
        for row in rows[:20]:
            impact = row.find('td', {'class': lambda x: x and 'sentiment' in str(x)})
            name_el = row.find('td', {'class': lambda x: x and 'event' in str(x)})
            time_el = row.find('td', {'class': lambda x: x and 'time' in str(x)})
            if impact and name_el:
                bulls = len(impact.find_all('i', {'class': lambda x: x and 'bull' in str(x)}))
                name = name_el.get_text(strip=True)
                if bulls >= 3 and any(kw in name for kw in ['Fed', 'CPI', 'NFP', 'GDP', 'FOMC', 'Interest']):
                    events.append({
                        "name": name,
                        "impact": bulls,
                        "time": time_el.get_text(strip=True) if time_el else ""
                    })
    except Exception as e:
        log.debug(f"Macro events: {e}")

    # Hardcode known major events as fallback
    if not events:
        now = datetime.now()
        # Check if it's a FOMC week (roughly every 6 weeks)
        events = [{"name": "Monitor manually", "impact": 0, "time": ""}]

    return events[:5]

# ── CORRELATION ENGINE ─────────────────────────────────────

def get_btc_price():
    try:
        d = safe_get("https://api.coinbase.com/v2/prices/BTC-USD/spot")
        if d: return float(d["data"]["amount"])
    except: pass
    return 0

def calculate_correlations():
    """
    Calculate rolling correlations between BTC and key assets.
    Returns correlation scores and cross-asset signals.
    """
    try:
        # Get price changes
        btc_cb = load("/root/jarvis/btc_memory.json", {})
        btc_prices = btc_cb.get("prices", [])[-30:]  # Last 30 hourly

        spy = get_yahoo_price("SPY")
        qqq = get_yahoo_price("QQQ")
        gold = get_yahoo_price("GC=F")
        oil  = get_yahoo_price("CL=F")
        dxy  = get_yahoo_price("DX-Y.NYB")
        coin = get_yahoo_price("COIN")
        mstr = get_yahoo_price("MSTR")

        btc_1d = btc_prices[-1].get("24h", 0) if btc_prices else 0

        correlations = {
            "btc_spy":  "POSITIVE" if (btc_1d > 0) == (spy["change_1d"] > 0) else "NEGATIVE",
            "btc_qqq":  "POSITIVE" if (btc_1d > 0) == (qqq["change_1d"] > 0) else "NEGATIVE",
            "btc_gold": "POSITIVE" if (btc_1d > 0) == (gold["change_1d"] > 0) else "NEGATIVE",
            "btc_dxy":  "NEGATIVE" if (btc_1d > 0) != (dxy["change_1d"] > 0) else "BROKEN",
        }

        # Cross-asset signals
        signals = []

        # BTC leads tech — if BTC up big, buy QQQ
        if btc_1d > 3:
            signals.append(f"BTC +{btc_1d:.1f}% → Tech likely follows — RISK ON")
        elif btc_1d < -3:
            signals.append(f"BTC {btc_1d:.1f}% → Tech headwind — RISK OFF")

        # Dollar inverse — weak dollar = strong stocks/crypto
        if dxy["change_1d"] < -0.5:
            signals.append(f"Dollar weak {dxy['change_1d']:+.1f}% → Boost for stocks/crypto")
        elif dxy["change_1d"] > 0.5:
            signals.append(f"Dollar strong {dxy['change_1d']:+.1f}% → Headwind for risk assets")

        # Gold flight to safety
        if gold["change_1d"] > 1.0 and spy["change_1d"] < -0.5:
            signals.append(f"Gold up {gold['change_1d']:+.1f}% + SPY down → Flight to safety — DEFENSIVE")

        # COIN/MSTR amplify BTC signal
        if coin["change_1d"] > 5 or mstr["change_1d"] > 5:
            signals.append(f"COIN {coin['change_1d']:+.1f}% MSTR {mstr['change_1d']:+.1f}% → Crypto sector hot")

        return {
            "btc_1d": btc_1d,
            "spy_1d": spy["change_1d"],
            "qqq_1d": qqq["change_1d"],
            "gold_1d": gold["change_1d"],
            "oil_1d": oil["change_1d"],
            "dxy_1d": dxy["change_1d"],
            "coin_1d": coin["change_1d"],
            "correlations": correlations,
            "signals": signals
        }
    except Exception as e:
        log.error(f"Correlations: {e}")
        return {}

# ── REGIME DETECTOR ───────────────────────────────────────

def compute_beast_action(regime, confidence, fg, pcr, equity_fg_val=50):
    """
    Derive the beast action string from regime + live macro indicators.
    Replaces the old static regime→string lookup with rule-based gating:
      - 'BUY AGGRESSIVELY' only if confidence >=75, F&G >=40, Put/Call <0.8
      - confidence <65 caps any buy at 'CAUTIOUS BUY — confirm before sizing up'
      - F&G <25 appends 'SENTIMENT EXTREME FEAR — reduce size'
      - Put/Call >0.9 appends 'HEDGING ELEVATED — no size multiplier'
    Uses equity F&G (CNN) for all gates; crypto F&G (fg) is not weighted here.
    """
    fg_val = equity_fg_val   # equity F&G gates buy-aggressively and sentiment warnings
    pcr_val = pcr["ratio"]
    CAUTIOUS = "CAUTIOUS BUY — confirm before sizing up"

    action = {
        "RISK_ON": "BUY AGGRESSIVELY — all signals green",
        "RECOVERY": "BUY SELECTIVELY — improving conditions",
        "STAGFLATION": "REDUCE SIZE — macro headwinds",
        "RISK_OFF": "PAUSE ALL BUYS — defensive mode",
    }.get(regime, "NORMAL")

    # 'BUY AGGRESSIVELY' requires all green gates; otherwise step down to cautious
    if action.startswith("BUY AGGRESSIVELY") and not (
        confidence >= 75 and fg_val >= 40 and pcr_val < 0.8
    ):
        action = CAUTIOUS

    # Low confidence caps any buy-oriented action at cautious
    if confidence < 65 and action.startswith("BUY"):
        action = CAUTIOUS

    # Append sentiment / hedging modifiers
    if fg_val < 25:
        action += " | SENTIMENT EXTREME FEAR — reduce size"
    if pcr_val > 0.9:
        action += " | HEDGING ELEVATED — no size multiplier"

    return action

def detect_regime(vix, fg, yield_data, pcr, correlations, btc_signal, equity_fg_val=50):
    """
    Detect market regime from multiple signals.
    Returns regime + size multiplier + beast action.
    equity_fg_val: CNN equity Fear & Greed (scored). fg (crypto F&G) is passed for
    display/beast-action only and does NOT contribute to the regime score.
    """
    scores = {"RISK_ON": 0, "RISK_OFF": 0, "STAGFLATION": 0, "RECOVERY": 0}

    # VIX scoring
    if vix["value"] < 15:
        scores["RISK_ON"] += 2
    elif vix["value"] < 20:
        scores["RISK_ON"] += 1
    elif vix["value"] > 30:
        scores["RISK_OFF"] += 2
        if vix["direction"] == "RISING":
            scores["RISK_OFF"] += 1
    elif vix["value"] > 25:
        scores["RISK_OFF"] += 1

    # Fear & Greed — equity F&G (CNN) is scored; crypto F&G (fg["current"]) is not weighted
    eq_val = equity_fg_val
    if eq_val > 65:
        scores["RISK_ON"] += 1
    elif eq_val > 50:
        scores["RISK_ON"] += 1
    elif eq_val < 25:
        scores["RISK_OFF"] += 2
    elif eq_val < 40:
        scores["RISK_OFF"] += 1

    # Yield direction
    if yield_data["direction"] == "FALLING":
        scores["RISK_ON"] += 1
        scores["RECOVERY"] += 1
    elif yield_data["direction"] == "RISING" and yield_data["value"] > 4.5:
        scores["STAGFLATION"] += 1
        scores["RISK_OFF"] += 1

    # Put/Call ratio
    pcr_val = pcr["ratio"]
    if pcr_val < 0.7:
        scores["RISK_ON"] += 1
    elif pcr_val > 1.3:
        scores["RISK_OFF"] += 2

    # BTC signal
    if btc_signal == "bullish":
        scores["RISK_ON"] += 1
    elif btc_signal == "bearish":
        scores["RISK_OFF"] += 1

    # Correlations
    corr_signals = correlations.get("signals", [])
    for sig in corr_signals:
        if "RISK ON" in sig: scores["RISK_ON"] += 1
        elif "RISK OFF" in sig: scores["RISK_OFF"] += 1
        elif "DEFENSIVE" in sig: scores["RISK_OFF"] += 1

    # Determine winner
    regime = max(scores, key=scores.get)
    confidence = round(scores[regime] / (sum(scores.values()) or 1) * 100)

    # Size multiplier based on regime
    size_mult = {
        "RISK_ON": 1.5,
        "RECOVERY": 1.25,
        "STAGFLATION": 0.75,
        "RISK_OFF": 0.0
    }.get(regime, 1.0)

    # Beast action — rule-based gating on confidence + macro indicators
    beast_action = compute_beast_action(regime, confidence, fg, pcr,
                                        equity_fg_val=equity_fg_val)

    # Focus sectors
    focus = {
        "RISK_ON": "Tech, Crypto, Growth, QQQ",
        "RECOVERY": "Small caps, Financials, Cyclicals",
        "STAGFLATION": "Energy, Gold, Commodities, XLE",
        "RISK_OFF": "Cash, T-bills, Defensive"
    }.get(regime, "Balanced")

    return {
        "regime": regime,
        "confidence": confidence,
        "scores": scores,
        "size_multiplier": size_mult,
        "beast_action": beast_action,
        "focus_sectors": focus
    }

def check_macro_defense(events):
    """Should bots go defensive for upcoming events?"""
    now = datetime.now()
    for event in events:
        if event.get("impact", 0) >= 3:
            return True, event.get("name", "High impact event")
    return False, None

def format_regime_message(regime_data, vix, fg, yield_data, pcr, correlations,
                          equity_fg_val=50):
    """Format Telegram message for regime change"""
    regime = regime_data["regime"]
    emoji = {"RISK_ON":"🟢","RISK_OFF":"🔴","STAGFLATION":"🟡","RECOVERY":"🔵"}.get(regime,"⚪")

    lines = [
        f"{emoji} MACRO REGIME: {regime}",
        f"Confidence: {regime_data['confidence']}%",
        f"{'='*22}",
        f"VIX: {vix['value']:.1f} ({vix['direction']})",
        f"Equity F&G: {equity_fg_val} (scored)",
        f"Crypto F&G: {fg['current']} ({fg['trend']}) [display only]",
        f"10yr Yield: {yield_data['value']:.2f}% ({yield_data['direction']})",
        f"Put/Call: {pcr['ratio']} ({pcr['sentiment']})",
        f"{'='*22}",
    ]
    for sig in correlations.get("signals", [])[:3]:
        lines.append(f"📊 {sig}")
    lines += [
        f"{'='*22}",
        f"BEAST: {regime_data['beast_action']}",
        f"Focus: {regime_data['focus_sectors']}",
        f"Size mult: {regime_data['size_multiplier']}x"
    ]
    return "\n".join(lines)

def compute_market_mode(f_and_g, vix, regime):
    """Derive the high-level market mode from F&G, VIX and regime, persist it to
    the jarvis_memory.db brain table (key 'market_mode'), and Telegram ONLY when
    the mode flips (not on every read).

    PROFIT  = F&G >= 30 AND VIX < 20 AND regime != RISK_OFF (all must hold).
    PROTECTION = any of F&G < 30, VIX >= 20, regime == RISK_OFF.

    Uses equity F&G (CNN) from jarvis_central_brain.json with a 2-hour staleness
    guard (same guard as jarvis_options_brain). Falls back to the passed f_and_g
    (which callers should also set to equity F&G) if the brain value is stale/missing.
    """
    eq_fg = f_and_g   # fallback
    try:
        cb_data = json.load(open(BRAIN_FILE))
        eq = cb_data.get("equity_fear_greed") or {}
        val, ts_str = eq.get("value"), eq.get("ts", "")
        if val is not None and ts_str:
            age_h = (datetime.now() - datetime.fromisoformat(ts_str)).total_seconds() / 3600
            if age_h <= 2:
                eq_fg = val
    except Exception:
        pass
    profit = (eq_fg >= 30) and (vix < 20) and (regime != "RISK_OFF")
    mode = "PROFIT" if profit else "PROTECTION"
    try:
        import jarvis_memory_db as memdb
        memdb.init_db()  # idempotent — ensure the brain table exists
        prev = memdb.brain_get("market_mode")
        memdb.brain_set("market_mode", mode)
        if prev != mode:
            if mode == "PROFIT":
                tg("✅ MARKET MODE: PROFIT — Calls, momentum plays, longs active")
            else:
                tg("🛡️ MARKET MODE: PROTECTION — Puts, cash, defensives only. No new longs.")
            log.info(f"Market mode change: {prev} → {mode}")
    except Exception as e:
        log.error(f"compute_market_mode: {e}")
    return mode

def run_cycle():
    log.info("Running macro cycle...")

    # Fetch all data concurrently
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=7) as ex:
        f_vix    = ex.submit(get_vix)
        f_fg     = ex.submit(get_fear_greed_history)
        f_eq_fg  = ex.submit(get_equity_fear_greed)
        f_yield  = ex.submit(get_treasury_yield)
        f_pcr    = ex.submit(get_put_call_ratio)
        f_corr   = ex.submit(calculate_correlations)
        f_events = ex.submit(get_macro_events)

    vix         = f_vix.result()
    fg          = f_fg.result()
    eq_fg       = f_eq_fg.result()
    yield_data  = f_yield.result()
    pcr         = f_pcr.result()
    correlations = f_corr.result()
    events      = f_events.result()

    # Get BTC signal from central brain
    cb = load(BRAIN_FILE)
    btc_signal = cb.get("btc_signal", "neutral")

    # Equity F&G value for regime scoring (crypto F&G is logged only)
    equity_fg_val = eq_fg["value"] if eq_fg else 50

    # Detect regime
    regime_data = detect_regime(vix, fg, yield_data, pcr, correlations, btc_signal,
                                equity_fg_val=equity_fg_val)

    # Check macro defense
    defensive, event_name = check_macro_defense(events)
    if defensive:
        regime_data["beast_action"] = f"DEFENSIVE MODE — {event_name} upcoming"
        regime_data["size_multiplier"] = 0.5
        log.info(f"Defensive mode: {event_name}")

    # Save macro state
    macro = {
        "ts": datetime.now().isoformat(),
        "regime": regime_data["regime"],
        "regime_confidence": regime_data["confidence"],
        "size_multiplier": regime_data["size_multiplier"],
        "beast_action": regime_data["beast_action"],
        "focus_sectors": regime_data["focus_sectors"],
        "vix": vix,
        "fear_greed": fg,
        "yield_10yr": yield_data,
        "put_call": pcr,
        "correlations": correlations,
        "macro_events": events,
        "defensive_mode": defensive
    }
    save(MACRO_FILE, macro)

    # Update central brain via write_brain so SQLite + JSON stay in sync.
    # Previously used save(BRAIN_FILE, cb) which only wrote JSON; SQLite kept
    # stale RISK_OFF/0.0 values, which any subsequent write_brain call would
    # read back from SQLite and reinstall over the correct JSON values.
    _macro_updates = {
        "macro_regime":       regime_data["regime"],
        "macro_size_mult":    regime_data["size_multiplier"],
        "macro_beast_action": regime_data["beast_action"],
        "macro_focus":        regime_data["focus_sectors"],
        "vix":                vix["value"],
        "yield_10yr":         yield_data["value"],
        "put_call_ratio":     pcr["ratio"],
        "btc_spy_corr":       correlations.get("btc_spy", "UNKNOWN"),
        "macro_defensive":    defensive,
    }
    if eq_fg:
        _macro_updates["equity_fear_greed"] = eq_fg
    _jb_hb.write_brain(_macro_updates)

    # Canonical regime -> jarvis_memory.db brain table (all bots read it from here).
    try:
        import jarvis_memory_db as _memdb
        _memdb.brain_set("regime", regime_data["regime"])
    except Exception as _re:
        log.error(f"memdb regime write: {_re}")

    # High-level market mode (PROFIT / PROTECTION) -> jarvis_memory.db brain table.
    # Pass equity F&G as primary argument (compute_market_mode also reads it from brain).
    compute_market_mode(equity_fg_val, vix["value"], regime_data["regime"])

    # Alert on regime changes
    macro_old = load(MACRO_FILE + ".prev", {})
    old_regime = macro_old.get("regime", "")
    if old_regime and old_regime != regime_data["regime"]:
        msg = format_regime_message(regime_data, vix, fg, yield_data, pcr, correlations,
                                    equity_fg_val=equity_fg_val)
        tg(f"⚠️ REGIME CHANGE: {old_regime} → {regime_data['regime']}\n{msg}")
        log.info(f"Regime changed: {old_regime} → {regime_data['regime']}")
    save(MACRO_FILE + ".prev", {"regime": regime_data["regime"]})

    # Independent regime-confidence scorer → refreshes jarvis_regime_confidence.json
    # every cycle (and mirrors a snapshot into the central brain) for other bots.
    try:
        import jarvis_regime_confidence as _rc
        _rc.save_confidence_to_brain({
            "macro": {
                "regime":     regime_data["regime"],
                "vix":        vix,
                "yield_10yr": yield_data,
            },
            "brain": {
                "fear_greed": fg.get("current", 50),
                "btc_signal": btc_signal,
            },
        }, brain_file=BRAIN_FILE)
    except Exception as _ce:
        log.error(f"regime confidence save: {_ce}")

    log.info(f"Regime: {regime_data['regime']} ({regime_data['confidence']}%) "
             f"VIX:{vix['value']:.1f} F&G(equity):{equity_fg_val} F&G(crypto):{fg['current']} "
             f"Yield:{yield_data['value']:.2f}% PCR:{pcr['ratio']}")
    return regime_data

def main():
    log.info("JARVIS MACRO ENGINE ONLINE")
    tg("📊 MACRO ENGINE ONLINE\nTracking: VIX, F&G, Yields, Put/Call, Correlations\nRegime detection every 30min")

    if True:  # run once
        try:
            run_cycle()
        except Exception as e:
            log.error(f"Macro cycle error: {e}")

if __name__ == "__main__":
    # Run once and exit (scheduled by cron)
    try:
        main()
    except Exception as e:
        import logging
        logging.getLogger().error(f"Fatal: {e}")
    raise SystemExit(0)
