#!/usr/bin/env python3
"""
JARVIS POSITION MONITOR
Watches open Kalshi bets in real time.
Alerts when a bet is in danger. Suggests exit price.
Runs inside jarvis_master 90s loop.
"""
from datetime import datetime
import json, os

BRAIN_FILE = "/root/jarvis/kalshi_brain.json"

# Alert thresholds
YELLOW_BUFFER = 150   # BTC within $150 of strike → warning
RED_BUFFER    = 50    # BTC within $50 of strike → exit now
TIME_RED      = 20    # minutes left for red alert
TIME_YELLOW   = 40    # minutes left for yellow alert

def get_open_bets():
    try:
        kb = json.load(open(BRAIN_FILE))
        return [b for b in kb.get('bets', []) if b.get('result') is None
                and b.get('strike') and b.get('side')]
    except: return []

def get_minutes_to_hour():
    """Minutes until top of next hour (Kalshi resolution)"""
    now = datetime.utcnow()
    return 60 - now.minute

def evaluate_position(bet, btc_price):
    """
    Returns (status, message) for a single open bet.
    status: 'safe' | 'warning' | 'danger' | 'breached'
    """
    side    = bet.get('side', '')
    strike  = float(bet.get('strike', 0))
    dollars = float(bet.get('dollars', 50))
    label   = bet.get('label', '????')
    mins    = get_minutes_to_hour()

    if side == 'NO':
        # NO wins if BTC stays BELOW strike
        buffer = strike - btc_price  # positive = safe, negative = breached

        if buffer < 0:
            # BTC is ABOVE strike — losing
            overage = abs(buffer)
            if mins <= 10:
                return 'breached', (
                    f"🚨 [{label}] NO ${strike:,.0f} BREACHED\n"
                    f"BTC ${btc_price:,.0f} — ${overage:.0f} above strike\n"
                    f"{mins}min left — EXIT NOW, recover what you can"
                )
            else:
                return 'danger', (
                    f"⚠️ [{label}] NO ${strike:,.0f} IN DANGER\n"
                    f"BTC ${btc_price:,.0f} — ${overage:.0f} above strike\n"
                    f"{mins}min left — consider exit to recover ~${round(dollars*0.4)}"
                )
        elif buffer < RED_BUFFER and mins <= TIME_RED:
            return 'warning', (
                f"⚠️ [{label}] NO ${strike:,.0f} TIGHT\n"
                f"BTC ${btc_price:,.0f} — only ${buffer:.0f} cushion\n"
                f"{mins}min left — watch closely, exit at ${round(dollars*0.6)} if moves up"
            )
        elif buffer < YELLOW_BUFFER and mins <= TIME_YELLOW:
            return 'caution', (
                f"👀 [{label}] NO ${strike:,.0f} watch\n"
                f"BTC ${btc_price:,.0f} — ${buffer:.0f} cushion, {mins}min left"
            )
        else:
            return 'safe', None

    elif side == 'YES':
        # YES wins if BTC stays ABOVE strike
        buffer = btc_price - strike  # positive = safe, negative = breached

        if buffer < 0:
            overage = abs(buffer)
            if mins <= 10:
                return 'breached', (
                    f"🚨 [{label}] YES ${strike:,.0f} BREACHED\n"
                    f"BTC ${btc_price:,.0f} — ${overage:.0f} below strike\n"
                    f"{mins}min left — EXIT NOW, recover what you can"
                )
            else:
                return 'danger', (
                    f"⚠️ [{label}] YES ${strike:,.0f} IN DANGER\n"
                    f"BTC ${btc_price:,.0f} — ${overage:.0f} below strike\n"
                    f"{mins}min left — consider exit to recover ~${round(dollars*0.4)}"
                )
        elif buffer < RED_BUFFER and mins <= TIME_RED:
            return 'warning', (
                f"⚠️ [{label}] YES ${strike:,.0f} TIGHT\n"
                f"BTC ${btc_price:,.0f} — only ${buffer:.0f} cushion\n"
                f"{mins}min left — watch closely"
            )
        else:
            return 'safe', None

    return 'unknown', None

def check_positions(btc_price, tg_func):
    """
    Main function — call from jarvis_master every 90s.
    Sends alerts for any bets in danger.
    """
    bets = get_open_bets()
    if not bets: return

    alerts_sent = []
    for bet in bets:
        strike = bet.get('strike')
        if not strike: continue

        status, msg = evaluate_position(bet, btc_price)

        if status in ['breached', 'danger', 'warning'] and msg:
            label = bet.get('label', '????')
            # Avoid spamming — only alert if not already alerted in last 5 min
            alert_key = f"/tmp/jarvis_alert_{label}_{status}"
            try:
                import time
                if os.path.exists(alert_key):
                    age = time.time() - os.path.getmtime(alert_key)
                    if age < 300: continue  # already alerted in last 5 min
                open(alert_key, 'w').close()
            except: pass
            tg_func(msg)
            alerts_sent.append(label)

    return alerts_sent

def get_positions_summary(btc_price):
    """Clean summary of all open positions."""
    bets = get_open_bets()
    if not bets: return None
    mins = get_minutes_to_hour()
    lines = [f"📊 OPEN POSITIONS — {mins}min to resolve"]
    for bet in bets:
        strike = float(bet.get('strike', 0))
        side   = bet.get('side', '')
        label  = bet.get('label', '????')
        dollars = float(bet.get('dollars', 0))
        if side == 'NO':
            buffer = strike - btc_price
            status = "✅" if buffer > 100 else "⚠️" if buffer > 0 else "🚨"
            lines.append(f"{status} [{label}] NO ${strike:,.0f} | buffer ${buffer:+.0f} | ${dollars:.0f}")
        elif side == 'YES':
            buffer = btc_price - strike
            status = "✅" if buffer > 100 else "⚠️" if buffer > 0 else "🚨"
            lines.append(f"{status} [{label}] YES ${strike:,.0f} | buffer ${buffer:+.0f} | ${dollars:.0f}")
    lines.append(f"BTC: ${btc_price:,.0f}")
    return "\n".join(lines)
