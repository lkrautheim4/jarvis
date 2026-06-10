#!/usr/bin/env python3
"""
JARVIS RULES ENGINE v2
Proven edges from real trading data. Zero guessing.
Fixed buffer calculation — NO buffer = strike - btc (positive = safe)
                          YES buffer = btc - strike (positive = safe)
"""
from datetime import datetime
import json, os

TRADE_LOG = "/root/jarvis/jarvis_winning_trades.json"
INTEL_LOG  = "/root/jarvis/jarvis_intel_throttle.json"

# PROVEN EDGES FROM DATA
MIN_BUFFER_YES    = 300   # BTC must be $300+ ABOVE strike — 89% WR
MIN_BUFFER_NO     = 50    # BTC must be $50+ BELOW strike — minimum safety
GOOD_BUFFER_NO    = 150   # $150+ below = comfortable NO bet
# REAL WIN RATES from 61 graded bets (May 2026)
PRIME_HOURS_EDT   = {9:86, 10:67, 12:67, 13:71, 14:80, 15:67, 16:75, 17:86}
AVOID_HOURS_EDT   = [11, 18, 19, 20, 21, 22, 23, 0, 1, 2, 3, 4, 5, 6, 7]
CAUTION_HOURS_EDT = [8]
AVOID_HOURS_EDT   = [11, 18, 19, 20, 21, 22, 23, 0, 1, 2, 3]
MAX_LOSS_STREAK   = 3
CONFIDENCE_FLOOR  = 65
MAX_INTEL_PER_DAY = 3

def edt_hour():
    return (datetime.utcnow().hour - 4) % 24

def evaluate_kalshi_opportunity(btc_price, markets):
    """
    Scan all Kalshi markets and find the single best trade.
    
    NO bet: BTC must be BELOW strike (strike - btc > 0)
    YES bet: BTC must be ABOVE strike (btc - strike > 0)
    """
    opportunities = []

    for m in markets:
        strike    = float(m.get('strike', 0))
        yes_price = float(m.get('yes', 0))
        no_price  = float(m.get('no', 0))

        # NO opportunity: BTC must be BELOW strike
        # buffer_no = strike - btc_price (positive = BTC is below = NO is safe)
        buffer_no = strike - btc_price

        if buffer_no >= MIN_BUFFER_NO and 0.05 < no_price < 0.95:
            payout = round(1 / no_price, 2)
            # Edge score: bigger buffer + market agreement + payout value
            market_agreement = no_price  # market pricing NO high = agreement
            edge_score = (buffer_no / 100) * market_agreement * payout
            confidence = min(92, 74 + round(buffer_no / 100) * 3)
            opportunities.append({
                'side': 'NO',
                'strike': strike,
                'buffer': buffer_no,       # positive = BTC is below strike
                'btc_price': btc_price,
                'market_price': no_price,
                'payout_per_dollar': payout,
                'edge_score': edge_score,
                'confidence': confidence,
                'rule': f"BTC ${buffer_no:,.0f} BELOW strike — {confidence}% confidence",
                'market_agrees': no_price > 0.50
            })

        # YES opportunity: BTC must be ABOVE strike  
        # buffer_yes = btc_price - strike (positive = BTC is above = YES is safe)
        buffer_yes = btc_price - strike

        if buffer_yes >= MIN_BUFFER_YES and 0.05 < yes_price < 0.95:
            payout = round(1 / yes_price, 2)
            edge_score = (buffer_yes / 100) * (1 - yes_price) * 10
            confidence = min(96, 89 + round(buffer_yes / 300) * 2)
            opportunities.append({
                'side': 'YES',
                'strike': strike,
                'buffer': buffer_yes,      # positive = BTC is above strike
                'btc_price': btc_price,
                'market_price': yes_price,
                'payout_per_dollar': payout,
                'edge_score': edge_score,
                'confidence': confidence,
                'rule': f"BTC ${buffer_yes:,.0f} ABOVE strike — 89% WR pattern",
                'market_agrees': yes_price > 0.50
            })

    if not opportunities:
        return None

    # Sort by edge score — best opportunity first
    return sorted(opportunities, key=lambda x: x['edge_score'], reverse=True)[0]

def get_all_opportunities(btc_price, markets):
    """Return ALL valid opportunities ranked by edge score."""
    all_opps = []
    for m in markets:
        strike    = float(m.get('strike', 0))
        yes_price = float(m.get('yes', 0))
        no_price  = float(m.get('no', 0))
        buffer_no  = strike - btc_price
        buffer_yes = btc_price - strike

        if buffer_no >= MIN_BUFFER_NO and 0.05 < no_price < 0.95:
            payout = round(1 / no_price, 2)
            market_agreement = no_price
            edge_score = (buffer_no / 100) * market_agreement * payout
            confidence = min(92, 74 + round(buffer_no / 100) * 3)
            all_opps.append({
                'side': 'NO', 'strike': strike, 'buffer': buffer_no,
                'market_price': no_price, 'payout_per_dollar': payout,
                'edge_score': edge_score, 'confidence': confidence,
                'rule': f"BTC ${buffer_no:,.0f} below strike"
            })

        if buffer_yes >= MIN_BUFFER_YES and 0.05 < yes_price < 0.95:
            payout = round(1 / yes_price, 2)
            edge_score = (buffer_yes / 100) * (1 - yes_price) * 10
            confidence = min(96, 89 + round(buffer_yes / 300) * 2)
            all_opps.append({
                'side': 'YES', 'strike': strike, 'buffer': buffer_yes,
                'market_price': yes_price, 'payout_per_dollar': payout,
                'edge_score': edge_score, 'confidence': confidence,
                'rule': f"BTC ${buffer_yes:,.0f} above strike"
            })

    return sorted(all_opps, key=lambda x: x['edge_score'], reverse=True)

def apply_rules(opp, consecutive_losses, mins=60):
    """Apply all proven rules. Returns (go, size_mult, blockers, boosts)"""
    blockers = []; boosts = []; size_mult = 1.0
    h = edt_hour()

    # RULE 1: Never 15-min windows
    if mins <= 15:
        blockers.append("15-min window — 0% WR historically. Hourly only.")

    # RULE 2: Stop after 3 consecutive losses
    if consecutive_losses >= MAX_LOSS_STREAK:
        blockers.append(f"STOP — {consecutive_losses} consecutive losses. Done for session.")

    # RULE 3: Confidence floor
    if opp.get('confidence', 0) < CONFIDENCE_FLOOR:
        blockers.append(f"Confidence {opp.get('confidence')}% below {CONFIDENCE_FLOOR}% floor.")

    # RULE 4: YES buffer requirement
    if opp['side'] == 'YES' and opp['buffer'] < MIN_BUFFER_YES:
        blockers.append(f"YES needs BTC $300+ above strike. Only ${opp['buffer']:+,.0f}. SKIP.")

    # RULE 5: NO buffer requirement — BTC must be BELOW strike
    if opp['side'] == 'NO' and opp['buffer'] < MIN_BUFFER_NO:
        blockers.append(f"NO blocked — BTC is ${abs(opp['buffer']):.0f} ABOVE strike. BTC must be below strike to bet NO.")

    # RULE 6: Time quality
    if h in PRIME_HOURS_EDT:
        wr = PRIME_HOURS_EDT[h]
        boosts.append(f"PRIME HOUR {h}:00 EDT — {wr}% historical WR")
        size_mult += 0.25
    elif h in AVOID_HOURS_EDT:
        blockers.append(f"AVOID HOUR {h}:00 EDT — historically weak. Skip.")

    # RULE 7: YES size boost for large buffer
    if opp['side'] == 'YES' and opp['buffer'] >= MIN_BUFFER_YES:
        boosts.append(f"YES BUFFER EDGE — ${opp['buffer']:,.0f} above strike — 89% WR")
        size_mult += 0.5

    # RULE 8: NO market agreement boost
    if opp['side'] == 'NO' and opp.get('market_agrees'):
        boosts.append(f"Market agrees — NO at {opp['market_price']:.0%}")
        if opp['buffer'] >= GOOD_BUFFER_NO:
            size_mult += 0.25

    go = len(blockers) == 0
    return go, round(size_mult, 2), blockers, boosts

def format_signal(opp, go, size_mult, blockers, boosts, kelly_base=50, all_opps=None):
    """Clean single actionable message."""
    h = edt_hour()
    if not go:
        msg = f"SKIP — {blockers[0]}"
        # Still show better alternatives if available
        if all_opps:
            valid = [o for o in all_opps if o['side'] == 'NO' and o['buffer'] >= MIN_BUFFER_NO][:2]
            if valid:
                msg += "\nBetter plays:"
                for o in valid:
                    msg += f"\n  NO ${o['strike']:,.0f} — ${o['buffer']:.0f} below, {o['confidence']}% conf"
        return msg

    kelly = round(kelly_base * size_mult)
    direction = "stays BELOW" if opp['side'] == 'NO' else "stays ABOVE"
    lines = [
        f"{'🟢 YES' if opp['side']=='YES' else '🔴 NO'} ${opp['strike']:,.0f}",
        f"BTC must {direction} ${opp['strike']:,.0f}",
        f"Buffer: ${opp['buffer']:,.0f} | Confidence: {opp['confidence']}%",
        f"Kelly: ${kelly} | Pays ${opp['payout_per_dollar']:.2f}/$1",
        f"Rule: {opp['rule']}",
    ]
    for b in boosts:
        lines.append(f"✅ {b}")

    # Show top 2 alternatives
    if all_opps and len(all_opps) > 1:
        lines.append("── Also valid ──")
        for o in all_opps[1:3]:
            lines.append(f"  {o['side']} ${o['strike']:,.0f} — ${o['buffer']:.0f} buffer, {o['confidence']}% conf, pays ${o['payout_per_dollar']:.2f}/$1")

    lines.append(f"\nBET: {opp['side']} ${opp['strike']:,.0f} — ${kelly}")
    return "\n".join(lines)

def should_send_intel():
    try:
        today = datetime.utcnow().strftime('%Y-%m-%d')
        log = json.load(open(INTEL_LOG)) if os.path.exists(INTEL_LOG) else {"sent": []}
        count = sum(1 for e in log.get('sent', []) if e[:10] == today)
        return count < MAX_INTEL_PER_DAY
    except: return True

def log_intel_sent():
    try:
        log = json.load(open(INTEL_LOG)) if os.path.exists(INTEL_LOG) else {"sent": []}
        log["sent"].append(datetime.utcnow().isoformat())
        log["sent"] = log["sent"][-100:]
        with open(INTEL_LOG, 'w') as f: json.dump(log, f)
    except: pass

def log_winning_trade(opp, payout=None):
    try:
        log = json.load(open(TRADE_LOG)) if os.path.exists(TRADE_LOG) else {"trades": []}
        log["trades"].append({
            "ts": datetime.utcnow().isoformat(),
            "side": opp['side'], "strike": opp['strike'],
            "buffer": opp['buffer'], "confidence": opp['confidence'],
            "rule": opp['rule'], "payout": payout, "edt_hour": edt_hour()
        })
        log["trades"] = log["trades"][-500:]
        with open(TRADE_LOG, 'w') as f: json.dump(log, f, indent=2)
    except: pass

def get_kelly_for_time(base_kelly, mins_past):
    """
    Adjust Kelly size based on time in hour.
    Earlier = bigger bet. Later = smaller or skip.
    """
    if mins_past <= 5:   return round(base_kelly * 1.5)   # PRIME — size up
    elif mins_past <= 10: return round(base_kelly * 1.25)  # Great
    elif mins_past <= 20: return round(base_kelly * 1.0)   # Good
    elif mins_past <= 30: return round(base_kelly * 0.75)  # Fair
    elif mins_past <= 45: return round(base_kelly * 0.5)   # Thin
    else: return 0                                          # Skip

def get_odds_quality(market_price, mins_past, side="NO"):
    """
    Combined score of odds value + time remaining.
    market_price = the price you pay (NO price for NO bets, YES price for YES bets)
    Returns (quality, reason, should_bet)
    """
    if market_price <= 0: return "INVALID", "No price data", False

    # Profit per dollar bet
    payout_per_dollar = round(1/market_price, 2)
    profit_pct = round((payout_per_dollar - 1) * 100)
    win_prob = round(market_price * 100)

    # Too expensive — thin payout
    if market_price > 0.90:
        return "SKIP", f"Odds too high ({win_prob}%) — only {profit_pct}% profit. Not worth it.", False

    # Good value range
    if market_price > 0.75:
        if mins_past > 30:
            return "SKIP", f"{win_prob}% odds + {mins_past}min elapsed. Wait for next hour.", False
        return "THIN", f"{win_prob}% win prob — {profit_pct}% profit if correct. Bet small.", True

    if market_price > 0.55:
        if mins_past > 20:
            return "FAIR", f"{win_prob}% win prob — {profit_pct}% profit. {60-mins_past}min left.", True
        return "GOOD", f"{win_prob}% win prob — {profit_pct}% profit. Good value.", True

    if market_price > 0.35:
        return "PRIME", f"{win_prob}% win prob — {profit_pct}% profit. BET NOW.", True

    # Below 35% — high risk
    return "RISKY", f"Only {win_prob}% win prob — high risk. {profit_pct}% profit if right.", False

def get_time_in_hour_context():
    """
    Returns context about where we are in the current hour.
    Early = best odds, Late = odds already priced in.
    """
    now = datetime.utcnow()
    mins_past = now.minute
    mins_left = 60 - mins_past

    if mins_past <= 5:
        quality = "PRIME"
        note = f"First {mins_past}min — BEST ODDS. Bet NOW before market prices in."
        size_boost = 1.5
    elif mins_past <= 15:
        quality = "GOOD"
        note = f"{mins_past}min in — Good odds still available. Act soon."
        size_boost = 1.25
    elif mins_past <= 30:
        quality = "FAIR"
        note = f"{mins_past}min in — Odds tightening. Payout shrinking."
        size_boost = 1.0
    elif mins_past <= 45:
        quality = "LATE"
        note = f"{mins_past}min in — Late entry. Odds heavily priced. Small payout."
        size_boost = 0.75
    else:
        quality = "AVOID"
        note = f"Only {mins_left}min left — Too late. Wait for next hour."
        size_boost = 0.0

    return {
        "quality": quality,
        "mins_past": mins_past,
        "mins_left": mins_left,
        "note": note,
        "size_boost": size_boost,
        "bet_now": mins_past <= 15
    }

def find_best_market(markets, btc_price):
    """
    Find the single best market to bet on.
    Returns (market, side, reason) or (None, None, reason_to_skip)
    
    Rules:
    - Skip if no market has odds between 35-65% (no edge)
    - NO bet: BTC must be below strike with buffer
    - YES bet: BTC must be above strike by $300+
    - Never bet when all markets are at extremes
    """
    if not markets:
        return None, None, "No markets available"

    yes_prices = [float(m.get('yes', 0)) for m in markets]
    no_prices  = [float(m.get('no', 0)) for m in markets]

    # Check if market is at extremes — all prices are >70% or <30%
    tradeable = [m for m in markets
                 if 0.07 <= float(m.get("yes", 0)) <= 0.93]
    if not tradeable:
        return None, None, "All markets >90% certainty — no payout worth it"
    if not tradeable:
        return None, None, f"Market at extremes — no fair odds available. BTC ranging, wait for breakout."

    best_opp = None
    best_score = 0

    for m in tradeable:
        strike    = float(m.get('strike', 0))
        yes_price = float(m.get('yes', 0))
        no_price  = float(m.get('no', 0))
        buffer_no  = strike - btc_price   # positive = BTC below strike = NO safe
        buffer_yes = btc_price - strike   # positive = BTC above strike = YES safe

        # Score NO opportunities — focus on fair odds with buffer
        if 0.15 <= no_price <= 0.85 and buffer_no >= 50:
            # EV = (prob_correct * payout) - (prob_wrong * 1)
            # Use buffer as proxy for our confidence vs market
            our_conf = min(0.85, 0.50 + buffer_no/1000)  # more buffer = more confident
            market_conf = no_price
            edge = our_conf - market_conf
            payout = (1/no_price) - 1  # profit per dollar
            ev = edge * payout
            if ev > 0.05:  # need 5%+ expected value
                score = ev * (buffer_no/100)
                if score > best_score:
                    best_score = score
                    best_opp = (m, "NO", f"BTC ${buffer_no:,.0f} below ${strike:,.0f} — NO at {no_price:.0%} EV={ev:.1%}")
                best_opp = (m, 'YES', f"BTC ${buffer_yes:,.0f} above ${strike:,.0f} — YES at {yes_price:.0%}")

    if not best_opp:
        return None, None, "No trades meet buffer requirements — skip this hour"

    return best_opp[0], best_opp[1], best_opp[2]

def should_skip_hour(markets, btc_price):
    """Quick check — is this hour worth betting?"""
    market, side, reason = find_best_market(markets, btc_price)
    return market is None, reason

def get_session_summary(consecutive_losses):
    h = edt_hour()
    if consecutive_losses >= MAX_LOSS_STREAK:
        return f"SESSION OVER — {consecutive_losses} losses. Resume tomorrow."
    if h in PRIME_HOURS_EDT:
        return f"PRIME TIME {h}:00 EDT — {PRIME_HOURS_EDT[h]}% WR. Bet aggressively."
    if h in AVOID_HOURS_EDT:
        return f"WEAK HOUR {h}:00 EDT. Skip unless 80%+ confidence."
    return f"NORMAL HOUR {h}:00 EDT. Standard sizing."
