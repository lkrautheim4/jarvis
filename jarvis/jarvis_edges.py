#!/usr/bin/env python3
"""
JARVIS EDGE ENGINE — Trading session detection and per-condition edge analysis
Imported by jarvis_master.py
"""
from datetime import datetime

def get_trading_session():
    now = datetime.utcnow()
    utc_hour = now.hour
    edt_hour = (utc_hour - 4) % 24
    london_hour = (utc_hour + 1) % 24
    tokyo_hour = (utc_hour + 9) % 24

    if 0 <= utc_hour < 2:
        session="LATE_NYC"; volume="LOW"; reliability="POOR"
        note="NYC winding down, Asia not active. Low volume, erratic. AVOID betting."
        dead_zone=True
    elif 2 <= utc_hour < 6:
        session="ASIA_PEAK"; volume="MEDIUM"; reliability="MODERATE"
        note="Tokyo/Singapore active. BTC can trend hard in one direction."
        dead_zone=False
    elif 6 <= utc_hour < 8:
        session="LONDON_PRE"; volume="LOW-MEDIUM"; reliability="MODERATE"
        note="Asia closing, London not open. Often consolidation and tight ranges."
        dead_zone=False
    elif 8 <= utc_hour < 12:
        session="LONDON_OPEN"; volume="HIGH"; reliability="GOOD"
        note="London active. Big institutional moves. Strong trends possible."
        dead_zone=False
    elif 12 <= utc_hour < 13:
        session="NYSE_PRE_OVERLAP"; volume="HIGH"; reliability="VERY GOOD"
        note="London + NYSE overlap approaching. Highest liquidity window."
        dead_zone=False
    elif 13 <= utc_hour < 17:
        session="NYSE_PEAK"; volume="VERY HIGH"; reliability="BEST"
        note="London + NYSE fully overlapping. Maximum liquidity. Best time to bet."
        dead_zone=False
    elif 17 <= utc_hour < 21:
        session="NYSE_CLOSE"; volume="MEDIUM-HIGH"; reliability="GOOD"
        note="NYSE closing. End-of-day positioning. Watch for reversals."
        dead_zone=False
    else:
        session="DEAD_ZONE"; volume="VERY LOW"; reliability="POOR"
        note="Late US / pre-Asia. Very low volume. Wide spreads. Skip betting."
        dead_zone=True

    return {
        "session": session, "volume": volume, "reliability": reliability,
        "note": note, "dead_zone": dead_zone,
        "utc": f"{utc_hour:02d}:00", "edt": f"{edt_hour:02d}:00",
        "london": f"{london_hour:02d}:00", "tokyo": f"{tokyo_hour:02d}:00",
    }

def analyze_condition_edges(trades):
    rsi_stats = {}; hour_stats = {}; funding_stats = {}
    for t in trades:
        rsi = t.get("rsi", 50)
        won = t.get("pnl", 0) > 0
        rsi_zone = "oversold" if rsi < 35 else "overbought" if rsi > 65 else "bearish" if rsi < 45 else "bullish" if rsi > 55 else "neutral"
        if rsi_zone not in rsi_stats: rsi_stats[rsi_zone] = {"wins":0,"total":0}
        rsi_stats[rsi_zone]["total"] += 1
        if won: rsi_stats[rsi_zone]["wins"] += 1
        try:
            h = t.get("ts","00:00")[11:13]
            if h not in hour_stats: hour_stats[h] = {"wins":0,"total":0}
            hour_stats[h]["total"] += 1
            if won: hour_stats[h]["wins"] += 1
        except: pass
        funding = t.get("funding", 0)
        f_zone = "high_positive" if funding > 0.001 else "low_positive" if funding > 0 else "negative"
        if f_zone not in funding_stats: funding_stats[f_zone] = {"wins":0,"total":0}
        funding_stats[f_zone]["total"] += 1
        if won: funding_stats[f_zone]["wins"] += 1
    return rsi_stats, hour_stats, funding_stats

def get_edge_alerts(rsi_stats, hour_stats, funding_stats):
    alerts = []
    for zone, d in rsi_stats.items():
        if d["total"] >= 3:
            wr = d["wins"]/d["total"]
            if wr >= 0.75:
                alerts.append({"type":"rsi","condition":zone,"wr":round(wr*100),"total":d["total"],"edge":"HIGH"})
            elif wr <= 0.25:
                alerts.append({"type":"rsi","condition":zone,"wr":round(wr*100),"total":d["total"],"edge":"AVOID"})
    best_hours = [(h,round(d["wins"]/d["total"]*100),d["total"]) for h,d in hour_stats.items() if d["total"]>=3 and d["wins"]/d["total"]>=0.70]
    if best_hours:
        best_hours.sort(key=lambda x: x[1], reverse=True)
        alerts.append({"type":"hour","condition":f"Hour {best_hours[0][0]}:00 UTC","wr":best_hours[0][1],"total":best_hours[0][2],"edge":"HIGH"})
    for regime, d in funding_stats.items():
        if d["total"] >= 3 and d["wins"]/d["total"] >= 0.75:
            alerts.append({"type":"funding","condition":regime,"wr":round(d["wins"]/d["total"]*100),"total":d["total"],"edge":"HIGH"})
    return alerts

def check_active_edges(rsi_current, funding_current, alerts):
    active = []
    for alert in alerts:
        if alert["edge"] != "HIGH": continue
        if alert["type"] == "rsi":
            zone = "oversold" if rsi_current < 35 else "overbought" if rsi_current > 65 else "bearish" if rsi_current < 45 else "bullish" if rsi_current > 55 else "neutral"
            if zone == alert["condition"]:
                active.append(f"RSI {zone} — WR {alert['wr']}% ({alert['total']} trades)")
        elif alert["type"] == "funding":
            f_zone = "high_positive" if funding_current > 0.001 else "low_positive" if funding_current > 0 else "negative"
            if f_zone == alert["condition"]:
                active.append(f"Funding {f_zone} — WR {alert['wr']}% ({alert['total']} trades)")
    return active
