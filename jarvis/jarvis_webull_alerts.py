"""
jarvis_webull_alerts.py
Monitors TSLA, NVDA, MSFT, SPY, QQQ, MSTR, COIN, AAPL every 5 minutes.
Detects momentum breakouts and fires Telegram alerts with entry/stop/target.
Runs as a background bot on the VPS.
"""

import hmac
import hashlib
import base64
import uuid
import time
import json
import logging
import requests
from datetime import datetime, timezone
from threading import Thread

# ── Config ────────────────────────────────────────────────────────────────────
try:
    from webull_keys import WEBULL_APP_KEY, WEBULL_APP_SECRET
except ImportError:
    raise SystemExit("ERROR: webull_keys.py not found in /root/jarvis/")

TELEGRAM_TOKEN = __import__("jarvis_secrets").TG_TOKEN_TRADER
CHAT_ID        = "7534553840"
BASE_URL       = "https://api.webull.com"
SCAN_INTERVAL  = 300   # 5 minutes

WATCHLIST = ["TSLA", "NVDA", "MSFT", "SPY", "QQQ", "MSTR", "COIN", "AAPL"]

# Momentum thresholds
BREAKOUT_PCT   = 0.8   # % move in last bar to flag breakout
VOLUME_MULT    = 1.5   # volume must be 1.5x avg to confirm
STOP_PCT       = 1.5   # stop loss % below entry
TARGET_MULT    = 2.0   # target = 2x the risk (2:1 R/R)

logging.basicConfig(
    filename="/root/jarvis/jarvis_webull_alerts.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger(__name__)

# ── Webull Auth (HMAC-SHA1) ───────────────────────────────────────────────────

def _build_signature(method: str, uri: str, params: dict, body: str = "") -> dict:
    """Builds Webull HMAC-SHA1 signed headers."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nonce     = uuid.uuid4().hex

    base_headers = {
        "x-app-key":             WEBULL_APP_KEY,
        "x-signature-algorithm": "HMAC-SHA1",
        "x-signature-version":   "1.0",
        "x-signature-nonce":     nonce,
        "x-timestamp":           timestamp,
        "host":                  "api.webull.com",
    }

    # Combine params + headers into sorted map
    sign_map = {**params, **base_headers}
    sorted_pairs = "&".join(f"{k}={v}" for k, v in sorted(sign_map.items()))

    # MD5 body if present
    if body:
        body_md5 = hashlib.md5(body.encode()).hexdigest().upper()
        s3 = f"{uri}&{sorted_pairs}&{body_md5}"
    else:
        s3 = f"{uri}&{sorted_pairs}"

    # HMAC-SHA1
    key    = (WEBULL_APP_SECRET + "&").encode()
    sig    = base64.b64encode(hmac.new(key, s3.encode(), hashlib.sha1).digest()).decode()

    return {**base_headers, "x-signature": sig, "Content-Type": "application/json"}


def webull_get(uri: str, params: dict = None) -> dict | None:
    """Signed GET request to Webull API."""
    params = params or {}
    headers = _build_signature("GET", uri, params)
    url = BASE_URL + uri
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
        log.warning(f"Webull {uri} → {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        log.error(f"Webull request error: {e}")
        return None


# ── Market Data ───────────────────────────────────────────────────────────────

def get_snapshots(symbols: list) -> dict:
    """Fetch latest quote snapshots for a list of symbols."""
    uri    = "/openapi/market-data/stock/snapshot"
    params = {
        "symbols":              ",".join(symbols),
        "category":             "US_STOCK",
        "extend_hour_required": "false",
        "overnight_required":   "false",
    }
    data = webull_get(uri, params)
    if not data:
        return {}

    result = {}
    items = data if isinstance(data, list) else data.get("data", [])
    for item in items:
        sym = item.get("symbol") or item.get("ticker", {}).get("symbol")
        if sym:
            result[sym] = item
    return result


def get_bars(symbol: str, count: int = 20) -> list:
    """Fetch recent 1-minute bars for a symbol."""
    uri    = "/openapi/market-data/stock/bars"
    params = {
        "symbol":   symbol,
        "category": "US_STOCK",
        "type":     "m1",
        "count":    str(count),
    }
    data = webull_get(uri, params)
    if not data:
        return []
    return data if isinstance(data, list) else data.get("data", [])


# ── Momentum Detection ────────────────────────────────────────────────────────

def detect_momentum(symbol: str, snapshot: dict) -> dict | None:
    """
    Returns a signal dict if momentum breakout detected, else None.
    Signal types: BREAKOUT_UP, BREAKOUT_DOWN
    """
    try:
        price     = float(snapshot.get("close") or snapshot.get("lastPrice") or 0)
        open_p    = float(snapshot.get("open") or price)
        high      = float(snapshot.get("high") or price)
        low       = float(snapshot.get("low") or price)
        volume    = float(snapshot.get("volume") or 0)
        avg_vol   = float(snapshot.get("avgVolume10D") or snapshot.get("avgVolume") or volume)
        change_pct = float(snapshot.get("changeRatio") or snapshot.get("pChng") or 0) * 100

        if price <= 0:
            return None

        vol_ratio = volume / avg_vol if avg_vol > 0 else 1.0
        bar_move  = abs(price - open_p) / open_p * 100

        # Breakout up: strong move up + volume confirmation
        if change_pct >= BREAKOUT_PCT and vol_ratio >= VOLUME_MULT:
            stop   = round(price * (1 - STOP_PCT / 100), 2)
            risk   = price - stop
            target = round(price + risk * TARGET_MULT, 2)
            return {
                "symbol":     symbol,
                "type":       "BREAKOUT_UP",
                "price":      price,
                "change_pct": round(change_pct, 2),
                "vol_ratio":  round(vol_ratio, 2),
                "entry":      price,
                "stop":       stop,
                "target":     target,
                "risk_pct":   round(STOP_PCT, 1),
                "rr":         f"1:{TARGET_MULT}",
            }

        # Breakout down: sharp drop + volume (put signal)
        if change_pct <= -BREAKOUT_PCT and vol_ratio >= VOLUME_MULT:
            stop   = round(price * (1 + STOP_PCT / 100), 2)
            risk   = stop - price
            target = round(price - risk * TARGET_MULT, 2)
            return {
                "symbol":     symbol,
                "type":       "BREAKOUT_DOWN",
                "price":      price,
                "change_pct": round(change_pct, 2),
                "vol_ratio":  round(vol_ratio, 2),
                "entry":      price,
                "stop":       stop,
                "target":     target,
                "risk_pct":   round(STOP_PCT, 1),
                "rr":         f"1:{TARGET_MULT}",
            }

    except Exception as e:
        log.error(f"detect_momentum {symbol}: {e}")

    return None


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        log.error(f"Telegram send error: {e}")


def format_alert(sig: dict) -> str:
    direction = "📈 BREAKOUT UP" if sig["type"] == "BREAKOUT_UP" else "📉 BREAKDOWN"
    action    = "BUY CALLS / LONG" if sig["type"] == "BREAKOUT_UP" else "BUY PUTS / SHORT"
    return (
        f"🚨 <b>JARVIS MOMENTUM ALERT</b>\n"
        f"{'='*28}\n"
        f"{direction} — <b>{sig['symbol']}</b>\n"
        f"Price:   ${sig['price']}\n"
        f"Move:    {sig['change_pct']:+.2f}%\n"
        f"Volume:  {sig['vol_ratio']:.1f}x avg\n"
        f"{'─'*28}\n"
        f"Action:  {action}\n"
        f"Entry:   ${sig['entry']}\n"
        f"Stop:    ${sig['stop']} ({sig['risk_pct']}%)\n"
        f"Target:  ${sig['target']} (R/R {sig['rr']})\n"
        f"{'='*28}\n"
        f"⏰ {datetime.now().strftime('%H:%M ET')}"
    )


# ── Main Loop ─────────────────────────────────────────────────────────────────

# Track recently alerted symbols to avoid spam (cool-down 15 min)
_alerted: dict[str, float] = {}
COOLDOWN = 900  # 15 minutes


def scan_once():
    now = time.time()
    snapshots = get_snapshots(WATCHLIST)

    if not snapshots:
        log.warning("No snapshot data returned — market may be closed or auth failed")
        return

    signals_found = 0
    for symbol in WATCHLIST:
        snap = snapshots.get(symbol)
        if not snap:
            continue

        sig = detect_momentum(symbol, snap)
        if not sig:
            continue

        # Cool-down check
        last_alerted = _alerted.get(symbol, 0)
        if now - last_alerted < COOLDOWN:
            continue

        msg = format_alert(sig)
        send_telegram(msg)
        _alerted[symbol] = now
        signals_found += 1
        log.info(f"ALERT sent: {symbol} {sig['type']} {sig['change_pct']:+.2f}%")

    if signals_found == 0:
        log.info(f"Scan complete — no breakouts detected across {len(snapshots)} symbols")


def run():
    log.info("JARVIS Webull Alerts bot started")
    send_telegram("✅ <b>JARVIS Webull Alerts</b> online\nWatching: " + ", ".join(WATCHLIST))

    while True:
        try:
            # Only scan during market hours (9:30am–4pm ET = 13:30–20:00 UTC)
            hour_utc = datetime.now(timezone.utc).hour
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
