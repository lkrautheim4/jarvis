#!/usr/bin/env python3
"""
JARVIS MARKET FLAGS — Shared Risk Management Bridge
Level 5 writes flags, all trading bots read them before every trade
Coordinates risk across all 4 bots automatically
"""

import json, os
from datetime import datetime

FLAGS_FILE = "jarvis_market_flags.json"

# ─────────────────────────────────────────
# FLAG SCHEMA
# ─────────────────────────────────────────
DEFAULT_FLAGS = {
    "trading_paused":    False,
    "pause_reason":      "",
    "pause_until":       "",
    "size_reduction":    1.0,    # multiplier: 0.5 = half size, 1.0 = normal
    "risk_level":        "NORMAL",  # NORMAL, ELEVATED, HIGH, EXTREME
    "avoid_tickers":     [],     # tickers to avoid right now
    "avoid_sectors":     [],     # sectors to avoid
    "bias":              "NEUTRAL",  # BULLISH, BEARISH, NEUTRAL
    "bias_reason":       "",
    "macro_event":       "",     # current macro event if any
    "btc_signal":        "NEUTRAL",  # BTC direction signal
    "spy_regime":        "UNKNOWN",  # market regime from Level 5
    "hot_tickers":       [],     # tickers with strong signals
    "last_updated":      "",
    "updated_by":        ""
}

def load_flags():
    try:
        if os.path.exists(FLAGS_FILE):
            with open(FLAGS_FILE, 'r') as f:
                flags = json.load(f)
                # Merge with defaults for any missing keys
                for k, v in DEFAULT_FLAGS.items():
                    if k not in flags:
                        flags[k] = v
                return flags
    except: pass
    return DEFAULT_FLAGS.copy()

def save_flags(flags):
    flags["last_updated"] = datetime.now().isoformat()
    try:
        with open(FLAGS_FILE, 'w') as f:
            json.dump(flags, f, indent=2)
        return True
    except Exception as e:
        print(f"Flag save error: {e}")
        return False

def set_flag(key, value, reason="", updated_by="system"):
    flags = load_flags()
    flags[key] = value
    flags["updated_by"] = updated_by
    if reason:
        flags["bias_reason"] = reason
    save_flags(flags)

def pause_trading(reason, duration_minutes=120, size_reduction=0.5, risk_level="HIGH"):
    flags = load_flags()
    flags["trading_paused"] = True
    flags["pause_reason"] = reason
    flags["size_reduction"] = size_reduction
    flags["risk_level"] = risk_level
    flags["updated_by"] = "jarvis_level5"
    # Set pause until time
    from datetime import timedelta
    pause_until = (datetime.now() + timedelta(minutes=duration_minutes)).isoformat()
    flags["pause_until"] = pause_until
    save_flags(flags)
    print(f"TRADING PAUSED: {reason} until {pause_until}")

def resume_trading(reason="All clear"):
    flags = load_flags()
    flags["trading_paused"] = False
    flags["pause_reason"] = ""
    flags["pause_until"] = ""
    flags["size_reduction"] = 1.0
    flags["risk_level"] = "NORMAL"
    flags["macro_event"] = ""
    flags["updated_by"] = "jarvis_level5"
    save_flags(flags)
    print(f"TRADING RESUMED: {reason}")

def check_auto_resume():
    """Auto-resume trading if pause time has passed"""
    flags = load_flags()
    if flags["trading_paused"] and flags.get("pause_until"):
        try:
            pause_until = datetime.fromisoformat(flags["pause_until"])
            if datetime.now() > pause_until:
                resume_trading("Pause period expired")
                return True
        except: pass
    return False

def should_trade(ticker="", sector="", size=0):
    """
    Called by trading bots before every trade
    Returns (should_trade, adjusted_size, reason)
    """
    flags = load_flags()

    # Auto-resume check
    check_auto_resume()
    flags = load_flags()  # reload after potential resume

    # Hard pause
    if flags["trading_paused"]:
        return False, 0, f"PAUSED: {flags['pause_reason']}"

    # Ticker block
    if ticker and ticker in flags.get("avoid_tickers", []):
        return False, 0, f"Ticker {ticker} flagged as avoid"

    # Sector block
    if sector and sector in flags.get("avoid_sectors", []):
        return False, 0, f"Sector {sector} flagged as avoid"

    # Size adjustment
    adjusted_size = size * flags.get("size_reduction", 1.0)

    reason = ""
    if flags["size_reduction"] < 1.0:
        reason = f"Size reduced to {flags['size_reduction']*100:.0f}% — {flags['risk_level']} risk"

    return True, adjusted_size, reason

def get_bias():
    """Get current market bias for position sizing"""
    flags = load_flags()
    return flags.get("bias", "NEUTRAL"), flags.get("bias_reason", "")

def get_risk_level():
    flags = load_flags()
    return flags.get("risk_level", "NORMAL")

def print_status():
    flags = load_flags()
    print("\n=== JARVIS MARKET FLAGS ===")
    print(f"Trading paused: {flags['trading_paused']}")
    if flags['trading_paused']:
        print(f"Reason: {flags['pause_reason']}")
        print(f"Until: {flags['pause_until']}")
    print(f"Risk level: {flags['risk_level']}")
    print(f"Size reduction: {flags['size_reduction']*100:.0f}%")
    print(f"Bias: {flags['bias']}")
    print(f"Macro event: {flags['macro_event'] or 'none'}")
    print(f"Avoid tickers: {flags['avoid_tickers']}")
    print(f"Avoid sectors: {flags['avoid_sectors']}")
    print(f"Last updated: {flags['last_updated']}")
    print(f"Updated by: {flags['updated_by']}")
    print("===========================\n")

if __name__ == "__main__":
    print_status()
