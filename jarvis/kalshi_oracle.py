import json
from datetime import datetime
from zoneinfo import ZoneInfo

def validate_bet(side, odds, btc_price, time_utc=None):
    """
    Oracle validator. Returns (approved: bool, reason: str, kelly_size: int)
    """
    if time_utc is None:
        time_utc = datetime.now(ZoneInfo("UTC"))
    
    hour = time_utc.hour
    
    # Prime hours: 9, 10, 14, 17 EDT = 13, 14, 18, 21 UTC
    prime_hours_utc = {13, 14, 18, 21}
    bad_hours_utc = {15, 22, 23, 0, 1, 2, 3}
    
    if hour in bad_hours_utc:
        return False, f"ORACLE_REJECT: Hour {hour} UTC in avoid zone", 0
    
    # Floor rules
    if side.upper() == "YES":
        if odds > 35:
            return False, f"ORACLE_REJECT: YES odds {odds} exceed floor 35 (65%)", 0
        ev_threshold = 0.05  # 5% EV minimum for YES
    elif side.upper() == "NO":
        if odds < 20:
            return False, f"ORACLE_REJECT: NO odds {odds} below floor 20 (80%)", 0
        ev_threshold = 0.05
    else:
        return False, "ORACLE_REJECT: Invalid side", 0
    
    # EV gate (simplified; you tune this)
    # EV = (implied_prob * payout) - stake
    # For now: just check odds are reasonable
    if odds < 1 or odds > 99:
        return False, f"ORACLE_REJECT: Odds {odds} out of range", 0
    
    # Kelly sizing: fixed tiers based on confidence
    is_prime = 1 if hour in prime_hours_utc else 0
    kelly_size = 15 if is_prime else 10  # $15 in prime hours, $10 otherwise
    
    return True, f"ORACLE_APPROVED: {side} @ {odds}, Kelly ${kelly_size}, BTC {btc_price}", kelly_size

def log_prediction(market_id, side, odds, btc_price, time_utc, reason=""):
    """Log a JARVIS prediction to kalshi_brain.json"""
    try:
        with open("/root/jarvis/kalshi_brain.json", "r") as f:
            brain = json.load(f)
    except:
        brain = {"predictions": [], "manual_bets": []}
    
    prediction = {
        "market_id": market_id,
        "side": side,
        "odds": odds,
        "btc_price": btc_price,
        "time_utc": time_utc.isoformat(),
        "approved": True if "APPROVED" in reason else False,
        "oracle_reason": reason
    }
    brain["predictions"].append(prediction)
    
    with open("/root/jarvis/kalshi_brain.json", "w") as f:
        json.dump(brain, f, indent=2)

if __name__ == "__main__":
    # Test
    approved, msg, kelly = validate_bet("YES", 30, 62500)
    print(f"Test YES@30: {approved} - {msg} - Kelly ${kelly}")
    
    approved, msg, kelly = validate_bet("YES", 40, 62500)
    print(f"Test YES@40: {approved} - {msg} - Kelly ${kelly}")
