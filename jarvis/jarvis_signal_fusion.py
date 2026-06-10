import json, requests, os
from datetime import datetime
import jarvis_brain

def get_fusion_score(ticker, rsi, regime, volume_mult, patterns, vwap_pct):
    score = 0
    reasons = []
    brain = jarvis_brain.read_brain()

    if rsi < 25: score += 15; reasons.append("RSI extreme oversold " + str(rsi))
    elif rsi < 33: score += 10; reasons.append("RSI oversold " + str(rsi))
    elif rsi < 40: score += 5; reasons.append("RSI bearish " + str(rsi))

    if regime == "UPTREND": score += 10; reasons.append("Uptrend")
    elif regime == "RANGING": score -= 5; reasons.append("Ranging penalty")

    if volume_mult >= 2.0: score += 10; reasons.append("High volume " + str(volume_mult))
    elif volume_mult >= 1.5: score += 5; reasons.append("Good volume " + str(volume_mult))

    if vwap_pct < -1.0: score += 5; reasons.append("Below VWAP")

    bullish_patterns = ["THREE_WHITE_SOLDIERS","HAMMER","MORNING_STAR","ENGULFING"]
    bearish_patterns = ["THREE_BLACK_CROWS","SHOOTING_STAR","DOJI"]
    for p in patterns:
        if p in bullish_patterns: score += 10; reasons.append("Bullish " + p)
        elif p in bearish_patterns: score -= 5; reasons.append("Bearish " + p)

    btc_signal = brain.get("btc_signal","neutral")
    market_mood = brain.get("market_mood","neutral")
    risk_level = brain.get("risk_level","normal")

    if risk_level == "stop": return 0, ["KILL SWITCH"]
    if btc_signal == "bullish": score += 10; reasons.append("BTC bullish")
    elif btc_signal == "active": score += 5; reasons.append("BTC active")
    if market_mood == "bullish": score += 10; reasons.append("Mood bullish")
    elif market_mood == "bearish": score -= 10; reasons.append("Mood bearish")

    hot_tickers = brain.get("hot_tickers",[])
    if ticker in hot_tickers: score += 10; reasons.append("Intel hot ticker")
    if ticker in hot_tickers: score += 15; reasons.append("Breakout predicted")

    try:
        intel = json.load(open("/root/jarvis/jarvis_intel.json"))
        learnings = intel.get("news_learnings",[])
        recent = [l for l in learnings[-50:] if l.get("ticker") == ticker]
        pos = sum(1 for l in recent if l.get("sentiment") == "positive")
        neg = sum(1 for l in recent if l.get("sentiment") == "negative")
        if pos > neg: score += 10; reasons.append("Positive news")
        elif neg > pos: score -= 10; reasons.append("Negative news")
        options = intel.get("options_alerts",[])
        if any(ticker in str(o) for o in options): score += 10; reasons.append("Options flow")
    except: pass

    try:
        ab = json.load(open("/root/jarvis/jarvis_alpha_brain.json"))
        stats = ab.get("assets",{}).get(ticker,{})
        wins = stats.get("wins",0); total = stats.get("total",0)
        if total >= 3:
            wr = wins/total
            if wr >= 0.7: score += 10; reasons.append("Win rate " + str(round(wr*100)) + "%")
            elif wr < 0.4: score -= 10; reasons.append("Poor win rate")
    except: pass

    return max(0, min(100, score)), reasons

def get_position_size(score, base_size):
    if score >= 80: return int(base_size * 3.0), "MAXIMUM", 3.0
    elif score >= 65: return int(base_size * 2.0), "HIGH", 2.0
    elif score >= 50: return int(base_size * 1.5), "MEDIUM", 1.5
    elif score >= 35: return int(base_size * 1.0), "NORMAL", 1.0
    else: return 0, "SKIP", 0.0
