import jarvis_brain as _jb_hb
"""
jarvis_range_detector.py
Monitors ETH and BTC for range-bound conditions.
Fires Telegram alert when asset is consolidating — signal to check Webull range markets.
Runs every 15 minutes.
"""
import time
import requests
import logging
from datetime import datetime, timezone
from collections import deque

TELEGRAM_TOKEN = __import__("jarvis_secrets").TG_TOKEN_TRADER
CHAT_ID        = "7534553840"
SCAN_INTERVAL  = 900   # 15 minutes
RANGE_WINDOW   = 8     # number of readings = 2 hours at 15min intervals
RANGE_TIGHT    = 0.008 # 0.8% range = tight consolidation
RANGE_MEDIUM   = 0.015 # 1.5% range = medium consolidation

logging.basicConfig(
    filename="/root/jarvis/jarvis_range_detector.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger(__name__)

# Price history buffers
eth_prices = deque(maxlen=RANGE_WINDOW)
btc_prices = deque(maxlen=RANGE_WINDOW)

# Cooldown — don't spam same asset
_alerted = {"ETH": 0, "BTC": 0}
COOLDOWN = 3600  # 1 hour

def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10)
    except Exception as e:
        log.error(f"Telegram error: {e}")

def get_prices():
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=ethereum,bitcoin&vs_currencies=usd",
            timeout=10)
        data = r.json()
        eth = float(data["ethereum"]["usd"])
        btc = float(data["bitcoin"]["usd"])
        return eth, btc
    except Exception as e:
        log.error(f"Price fetch error: {e}")
        return None, None

def analyze_range(prices, symbol):
    """Returns range analysis if enough data, else None."""
    if len(prices) < 4:
        return None
    high = max(prices)
    low  = min(prices)
    mid  = (high + low) / 2
    range_pct = (high - low) / mid

    if range_pct <= RANGE_TIGHT:
        tightness = "VERY TIGHT"
        confidence = "HIGH"
    elif range_pct <= RANGE_MEDIUM:
        tightness = "TIGHT"
        confidence = "MEDIUM"
    else:
        return None  # Not ranging

    hours = len(prices) * 15 / 60
    return {
        "symbol":     symbol,
        "high":       round(high, 2),
        "low":        round(low, 2),
        "mid":        round(mid, 2),
        "range_pct":  round(range_pct * 100, 2),
        "tightness":  tightness,
        "confidence": confidence,
        "hours":      round(hours, 1),
    }

def format_alert(r):
    conf_icon = "🔥" if r["confidence"] == "HIGH" else "✅"
    return (
        f"📊 <b>RANGE ALERT — {r['symbol']}</b>\n"
        f"{'='*28}\n"
        f"{conf_icon} {r['tightness']} CONSOLIDATION\n"
        f"Range: ${r['low']:,.2f} — ${r['high']:,.2f}\n"
        f"Width: {r['range_pct']:.2f}% over {r['hours']}hrs\n"
        f"{'─'*28}\n"
        f"🎯 <b>ACTION: Check Webull Predictions</b>\n"
        f"Look for {r['symbol']} range market:\n"
        f"${r['low']:,.0f} — ${r['high']:,.0f}\n"
        f"Bet YES if range market matches current consolidation\n"
        f"{'='*28}\n"
        f"⏰ {datetime.now().strftime('%H:%M ET')}"
    )

def scan():
    eth, btc = get_prices()
    if not eth or not btc:
        return

    eth_prices.append(eth)
    btc_prices.append(btc)

    now = time.time()

    # Check ETH
    eth_range = analyze_range(list(eth_prices), "ETH")
    if eth_range and (now - _alerted["ETH"]) > COOLDOWN:
        msg = format_alert(eth_range)
        send_telegram(msg)
        _alerted["ETH"] = now
        log.info(f"ETH range alert: {eth_range['low']}-{eth_range['high']} ({eth_range['range_pct']}%)")

    # Check BTC
    btc_range = analyze_range(list(btc_prices), "BTC")
    if btc_range and (now - _alerted["BTC"]) > COOLDOWN:
        msg = format_alert(btc_range)
        send_telegram(msg)
        _alerted["BTC"] = now
        log.info(f"BTC range alert: {btc_range['low']}-{btc_range['high']} ({btc_range['range_pct']}%)")

    if not eth_range and not btc_range:
        log.info(f"No range detected — ETH ${eth:,.2f} BTC ${btc:,.2f}")

def run():
    log.info("JARVIS Range Detector started")
    send_telegram("📊 <b>JARVIS Range Detector</b> online\nMonitoring ETH + BTC for consolidation patterns")
    while True:
        try:
            scan()
        except Exception as e:
            log.error(f"Scan error: {e}")
        _jb_hb.update_bot_heartbeat("jarvis_range_detector")
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    run()
