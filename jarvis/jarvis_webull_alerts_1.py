import yfinance as yf
import time
import logging
import requests
from datetime import datetime, timezone

TELEGRAM_TOKEN = __import__("jarvis_secrets").TG_TOKEN_TRADER
CHAT_ID        = "7534553840"
SCAN_INTERVAL  = 300
WATCHLIST      = ["TSLA", "NVDA", "MSFT", "SPY", "QQQ", "MSTR", "COIN", "AAPL"]
BREAKOUT_PCT   = 0.8
VOLUME_MULT    = 1.5
STOP_PCT       = 1.5
TARGET_MULT    = 2.0
COOLDOWN       = 900

logging.basicConfig(
    filename="/root/jarvis/jarvis_webull_alerts.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger(__name__)
_alerted = {}

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        log.error(f"Telegram error: {e}")

def scan_once():
    now = time.time()
    tickers = yf.Tickers(" ".join(WATCHLIST))
    signals_found = 0

    for symbol in WATCHLIST:
        try:
            ticker = tickers.tickers[symbol]
            info   = ticker.fast_info

            price      = float(info.last_price or 0)
            prev_close = float(info.previous_close or price)
            volume     = float(info.three_month_average_volume or 0)
            day_vol    = float(info.last_volume or 0)

            if price <= 0 or prev_close <= 0:
                continue

            change_pct = (price - prev_close) / prev_close * 100
            vol_ratio  = day_vol / volume if volume > 0 else 1.0

            signal = None
            if change_pct >= BREAKOUT_PCT and vol_ratio >= VOLUME_MULT:
                stop   = round(price * (1 - STOP_PCT / 100), 2)
                target = round(price + (price - stop) * TARGET_MULT, 2)
                signal = {"symbol": symbol, "type": "BREAKOUT_UP", "price": price,
                          "change_pct": round(change_pct, 2), "vol_ratio": round(vol_ratio, 2),
                          "entry": price, "stop": stop, "target": target}

            elif change_pct <= -BREAKOUT_PCT and vol_ratio >= VOLUME_MULT:
                stop   = round(price * (1 + STOP_PCT / 100), 2)
                target = round(price - (stop - price) * TARGET_MULT, 2)
                signal = {"symbol": symbol, "type": "BREAKOUT_DOWN", "price": price,
                          "change_pct": round(change_pct, 2), "vol_ratio": round(vol_ratio, 2),
                          "entry": price, "stop": stop, "target": target}

            if signal and (now - _alerted.get(symbol, 0)) > COOLDOWN:
                direction = "📈 BREAKOUT UP" if signal["type"] == "BREAKOUT_UP" else "📉 BREAKDOWN"
                action    = "BUY CALLS / LONG" if signal["type"] == "BREAKOUT_UP" else "BUY PUTS / SHORT"
                msg = (
                    f"🚨 <b>JARVIS MOMENTUM ALERT</b>\n"
                    f"{'='*28}\n"
                    f"{direction} — <b>{symbol}</b>\n"
                    f"Price:  ${signal['price']}\n"
                    f"Move:   {signal['change_pct']:+.2f}%\n"
                    f"Volume: {signal['vol_ratio']:.1f}x avg\n"
                    f"{'─'*28}\n"
                    f"Action: {action}\n"
                    f"Entry:  ${signal['entry']}\n"
                    f"Stop:   ${signal['stop']} (1.5%)\n"
                    f"Target: ${signal['target']} (2:1 R/R)\n"
                    f"⏰ {datetime.now().strftime('%H:%M ET')}"
                )
                send_telegram(msg)
                _alerted[symbol] = now
                signals_found += 1
                log.info(f"ALERT: {symbol} {signal['type']} {signal['change_pct']:+.2f}% vol={signal['vol_ratio']:.1f}x")

        except Exception as e:
            log.error(f"Error scanning {symbol}: {e}")

    if signals_found == 0:
        log.info("Scan complete — no breakouts detected")

def run():
    log.info("JARVIS Webull Alerts bot started (yfinance)")
    send_telegram("✅ <b>JARVIS Momentum Scanner</b> online\nWatching: " + ", ".join(WATCHLIST))
    while True:
        try:
            hour_utc   = datetime.now(timezone.utc).hour
            minute_utc = datetime.now(timezone.utc).minute
            market_open  = (hour_utc > 13) or (hour_utc == 13 and minute_utc >= 30)
            market_close = hour_utc < 20
            if market_open and market_close:
                scan_once()
            else:
                log.info("Market closed — skipping scan")
        except Exception as e:
            log.error(f"Main loop error: {e}")
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    run()
