#!/usr/bin/env python3
"""
JARVIS LEARNING ENGINE — Level 2-5
Outcome learning loop, pattern memory, confluence detection,
market microstructure tracking.
"""
import json, os
from datetime import datetime

BRAIN_FILE   = "/root/jarvis/jarvis_central_brain.json"
PATTERN_FILE = "/root/jarvis/jarvis_patterns.json"
KALSHI_FILE  = "/root/jarvis/kalshi_brain.json"
MASTER_FILE  = "/root/jarvis/jarvis_master_brain.json"
ODDS_FILE    = "/root/jarvis/jarvis_odds_history.json"

def load(f):
    try: return json.load(open(f))
    except: return {}

def save(f, data):
    with open(f, 'w') as fp: json.dump(data, fp, indent=2)

# ── LEVEL 2: Pattern Memory ──────────────────────────────

def update_pattern_from_bet(bet, rsi, macd_hist, pct_b, fear_greed, trend_4h, edt_hour):
    """Update pattern fingerprint from a graded bet outcome"""
    if bet.get('result') not in ['WIN', 'LOSS']: return
    won = bet['result'] == 'WIN'

    # Build fingerprint
    rsi_zone = "oversold" if rsi < 35 else "overbought" if rsi > 65 else "bearish" if rsi < 45 else "bullish" if rsi > 55 else "neutral"
    macd_zone = "bullish" if macd_hist > 0 else "bearish"
    bb_zone = "bottom" if pct_b < 0.2 else "top" if pct_b > 0.8 else "mid"
    fg_zone = "fear" if fear_greed < 35 else "greed" if fear_greed > 65 else "neutral"
    m4h_zone = "up" if "UP" in trend_4h else "down" if "DOWN" in trend_4h else "flat"
    h_zone = "asia" if 0<=edt_hour<=7 else "london" if 8<=edt_hour<=12 else "nyc" if 13<=edt_hour<=20 else "late"
    side_zone = bet.get('side','?').lower()
    buf_zone = "large" if (bet.get('buffer') or 0) >= 300 else "medium" if (bet.get('buffer') or 0) >= 100 else "small"

    fingerprint = f"{rsi_zone}|{macd_zone}|{bb_zone}|{fg_zone}|{m4h_zone}|{h_zone}|{side_zone}|{buf_zone}"

    pat = load(PATTERN_FILE) if os.path.exists(PATTERN_FILE) else {"patterns":[],"fingerprints":{}}
    if fingerprint not in pat["fingerprints"]:
        pat["fingerprints"][fingerprint] = {"total":0,"wins":0,"no_wins":0,"yes_wins":0}
    fp = pat["fingerprints"][fingerprint]
    fp["total"] += 1
    if won:
        fp["wins"] += 1
        if side_zone == "no": fp["no_wins"] = fp.get("no_wins",0) + 1
        else: fp["yes_wins"] = fp.get("yes_wins",0) + 1
    pat["patterns"].append({
        "ts": datetime.utcnow().isoformat(),
        "fingerprint": fingerprint,
        "won": won,
        "side": side_zone,
        "buffer": bet.get('buffer', 0),
        "edt_hour": edt_hour
    })
    pat["patterns"] = pat["patterns"][-1000:]
    save(PATTERN_FILE, pat)
    return fingerprint

# ── LEVEL 3: Outcome Learning ─────────────────────────────

def update_winning_conditions(bet, rsi, trend_4h, edt_hour):
    """Track which conditions produce wins vs losses"""
    cb = load(BRAIN_FILE)
    won = bet.get('result') == 'WIN'
    side = bet.get('side', '')
    buffer = bet.get('buffer', 0) or 0
    hour = bet.get('edt_hour', edt_hour)

    # Update RSI condition stats
    rsi_key = f"rsi_{int(rsi//10)*10}s"  # e.g. rsi_60s for RSI 60-69
    for cond_dict in ['winning_conditions' if won else 'losing_conditions']:
        conditions = cb.get(cond_dict, {})
        if rsi_key not in conditions: conditions[rsi_key] = {"count":0,"details":[]}
        conditions[rsi_key]["count"] += 1
        conditions[rsi_key]["details"] = conditions[rsi_key]["details"][-20:]
        conditions[rsi_key]["details"].append({"ts":datetime.utcnow().isoformat(),"side":side,"buffer":buffer})
        cb[cond_dict] = conditions

    # Update hour stats
    hour_stats = cb.get('hour_stats', {})
    h_key = str(hour)
    if h_key not in hour_stats: hour_stats[h_key] = {"wins":0,"total":0,"pnl":0}
    hour_stats[h_key]["total"] += 1
    if won: hour_stats[h_key]["wins"] += 1
    hour_stats[h_key]["pnl"] = round(hour_stats[h_key]["pnl"] + (bet.get('pnl',0) or 0), 2)
    cb['hour_stats'] = hour_stats

    # Update buffer stats
    buf_stats = cb.get('buffer_stats', {})
    if buffer >= 300: buf_key = "300+"
    elif buffer >= 150: buf_key = "150-299"
    elif buffer >= 50: buf_key = "50-149"
    else: buf_key = "0-49"
    if buf_key not in buf_stats: buf_stats[buf_key] = {"wins":0,"total":0}
    buf_stats[buf_key]["total"] += 1
    if won: buf_stats[buf_key]["wins"] += 1
    cb['buffer_stats'] = buf_stats

    cb['last_updated'] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    save(BRAIN_FILE, cb)

def get_learning_summary():
    """Build learning context for Claude prompt"""
    cb = load(BRAIN_FILE)
    kb = load(KALSHI_FILE)
    lines = []

    # Hour performance
    hour_stats = cb.get('hour_stats', {})
    if hour_stats:
        best_hours = [(h,d) for h,d in hour_stats.items() if d['total']>=3]
        best_hours.sort(key=lambda x: x[1]['wins']/x[1]['total'], reverse=True)
        if best_hours:
            top = best_hours[:3]
            lines.append("BEST HOURS: " + " | ".join([f"{h}:00 EDT {round(d['wins']/d['total']*100)}% WR ({d['total']} bets)" for h,d in top]))
        worst = [x for x in best_hours if x[1]['wins']/x[1]['total'] < 0.40]
        if worst:
            lines.append("AVOID HOURS: " + " | ".join([f"{h}:00 EDT {round(d['wins']/d['total']*100)}% WR" for h,d in worst]))

    # Buffer performance
    buf_stats = cb.get('buffer_stats', {})
    if buf_stats:
        buf_lines = []
        for k, d in sorted(buf_stats.items()):
            if d['total'] >= 3:
                wr = round(d['wins']/d['total']*100)
                buf_lines.append(f"${k} buffer: {wr}% WR ({d['total']} bets)")
        if buf_lines: lines.append("BUFFER WR: " + " | ".join(buf_lines))

    # Pattern top edge
    pat = load(PATTERN_FILE) if os.path.exists(PATTERN_FILE) else {}
    fps = pat.get('fingerprints', {})
    good_fps = [(fp,d) for fp,d in fps.items() if d.get('total',0)>=5 and d.get('wins',0)/d['total']>=0.70]
    if good_fps:
        good_fps.sort(key=lambda x: x[1]['wins']/x[1]['total'], reverse=True)
        top_fp = good_fps[0]
        wr = round(top_fp[1]['wins']/top_fp[1]['total']*100)
        lines.append(f"TOP PATTERN: {top_fp[0]} → {wr}% WR ({top_fp[1]['total']} samples)")

    # Recent streak
    bets = [b for b in kb.get('bets',[]) if b.get('result') in ['WIN','LOSS']]
    if bets:
        last5 = "".join(["W" if b['result']=='WIN' else "L" for b in bets[-5:]])
        lines.append(f"LAST 5: {last5}")
        # Consecutive losses warning
        consec_l = 0
        for b in reversed(bets):
            if b['result'] == 'LOSS': consec_l += 1
            else: break
        if consec_l >= 2: lines.append(f"WARNING: {consec_l} consecutive losses — consider smaller size")

    return "\n".join(lines) if lines else "Building learning database..."

# ── LEVEL 4: Multi-timeframe Confluence ──────────────────

def get_timeframe_confluence(rsi, macd_hist, trend_4h, short_term_signal):
    """
    Check if multiple timeframes agree.
    Returns (direction, confidence, reason)
    """
    signals = []

    # 4H signal
    if "UP" in trend_4h: signals.append(("UP", "4H trending up"))
    elif "DOWN" in trend_4h: signals.append(("DOWN", "4H trending down"))

    # 1H MACD
    if macd_hist > 50: signals.append(("UP", f"MACD bullish +{macd_hist:.0f}"))
    elif macd_hist < -50: signals.append(("DOWN", f"MACD bearish {macd_hist:.0f}"))

    # RSI
    if rsi < 35: signals.append(("UP", f"RSI oversold {rsi}"))
    elif rsi > 65: signals.append(("DOWN", f"RSI overbought {rsi}"))

    # Short term
    if "STRONG DOWN" in short_term_signal or "SELLERS DOMINANT" in short_term_signal:
        signals.append(("DOWN", "1m sellers dominant"))
    elif "STRONG UP" in short_term_signal or "BUYERS DOMINANT" in short_term_signal:
        signals.append(("UP", "1m buyers dominant"))

    if not signals: return "NEUTRAL", 50, "No clear directional signal"

    ups   = [s for s in signals if s[0]=="UP"]
    downs = [s for s in signals if s[0]=="DOWN"]

    if len(downs) >= 3:
        conf = min(90, 65 + len(downs)*5)
        return "DOWN", conf, " + ".join([s[1] for s in downs[:3]])
    elif len(ups) >= 3:
        conf = min(90, 65 + len(ups)*5)
        return "UP", conf, " + ".join([s[1] for s in ups[:3]])
    elif len(downs) > len(ups):
        conf = 55 + len(downs)*3
        return "DOWN", conf, f"{len(downs)}/{len(signals)} timeframes bearish"
    elif len(ups) > len(downs):
        conf = 55 + len(ups)*3
        return "UP", conf, f"{len(ups)}/{len(signals)} timeframes bullish"
    else:
        return "MIXED", 45, "Conflicting signals — consider skip"

# ── LEVEL 5: Kalshi Market Microstructure ────────────────

def track_odds_movement(strike, yes_price, no_price):
    """Track how odds are moving for a strike over time"""
    odds_hist = load(ODDS_FILE) if os.path.exists(ODDS_FILE) else {"history":{}}
    key = str(int(strike))
    now = datetime.utcnow().strftime("%H:%M")
    if key not in odds_hist["history"]: odds_hist["history"][key] = []
    odds_hist["history"][key].append({"ts":now,"yes":yes_price,"no":no_price})
    odds_hist["history"][key] = odds_hist["history"][key][-20:]
    # Keep only current hour
    if len(odds_hist["history"]) > 20:
        oldest = sorted(odds_hist["history"].keys())[0]
        del odds_hist["history"][oldest]
    save(ODDS_FILE, odds_hist)

def get_odds_momentum(strike):
    """
    Is smart money buying YES or NO on this strike?
    Returns (direction, strength, reason)
    """
    odds_hist = load(ODDS_FILE) if os.path.exists(ODDS_FILE) else {}
    key = str(int(strike))
    history = odds_hist.get("history", {}).get(key, [])
    if len(history) < 3: return "UNKNOWN", 0, "Insufficient odds history"

    first_no = history[0]["no"]
    last_no  = history[-1]["no"]
    change   = last_no - first_no

    if change > 0.10:
        return "NO_RISING", change, f"NO odds rising {first_no:.2f}→{last_no:.2f} — smart money agrees"
    elif change < -0.10:
        return "NO_FALLING", abs(change), f"NO odds falling {first_no:.2f}→{last_no:.2f} — smart money disagrees"
    else:
        return "STABLE", abs(change), f"Odds stable {last_no:.2f}"

def get_best_entry_timing(markets, btc_price):
    """
    Find the optimal entry based on odds value + momentum.
    Returns enhanced market list with momentum data.
    """
    enhanced = []
    for m in markets:
        strike = float(m.get('strike', 0))
        yes_p  = float(m.get('yes', 0))
        no_p   = float(m.get('no', 0))

        # Track this strike's odds
        track_odds_movement(strike, yes_p, no_p)

        # Get momentum
        direction, strength, reason = get_odds_momentum(strike)

        m_enhanced = dict(m)
        m_enhanced['odds_momentum'] = direction
        m_enhanced['odds_reason'] = reason
        enhanced.append(m_enhanced)

    return enhanced
