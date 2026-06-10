#!/usr/bin/env python3
"""
jarvis_health.py — Bot heartbeat monitor.
Reads bot_heartbeats from jarvis_memory.db (READ-ONLY) and posts a GREEN/YELLOW/RED
roll-up to Telegram every 6 hours.
  GREEN  < 5 min   |   YELLOW 5–10 min   |   RED > 10 min (or never)
"""
import sqlite3, time, logging, requests
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('JARVIS-HEALTH')

TELEGRAM_TOKEN = __import__("jarvis_secrets").TG_TOKEN_INTEL
TELEGRAM_CHAT  = "7534553840"
DB_PATH        = "/root/jarvis/jarvis_memory.db"

CHECK_INTERVAL = 6 * 3600
YELLOW_SECS    = 5 * 60
RED_SECS       = 10 * 60

def tg(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": str(msg)[:4000]}, timeout=10)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram error: {e}")

def _age_seconds(last_seen):
    if not last_seen:
        return None
    try:
        ts = datetime.fromisoformat(last_seen)
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
        return (datetime.now(timezone.utc).replace(tzinfo=None) - ts).total_seconds()
    except (TypeError, ValueError):
        return None

def read_heartbeats():
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)  # READ-ONLY
        rows = con.execute("SELECT bot_name, last_seen FROM bot_heartbeats").fetchall()
        con.close()
        return rows
    except Exception as e:
        log.error(f"DB read error: {e}")
        return []

def classify(age):
    if age is None or age > RED_SECS:   return "RED"
    if age > YELLOW_SECS:               return "YELLOW"
    return "GREEN"

def _fmt_age(a):
    if a is None:    return "never"
    if a < 90:       return f"{int(a)}s"
    if a < 5400:     return f"{int(a/60)}m"
    return f"{a/3600:.1f}h"

def build_report():
    buckets = {"GREEN": [], "YELLOW": [], "RED": []}
    for name, last_seen in read_heartbeats():
        age = _age_seconds(last_seen)
        buckets[classify(age)].append((name, age))
    overall = "🔴 RED" if buckets["RED"] else "🟡 YELLOW" if buckets["YELLOW"] else "🟢 GREEN"
    lines = [f"🩺 JARVIS HEALTH — {overall}", "=" * 24,
             f"🟢 {len(buckets['GREEN'])}  🟡 {len(buckets['YELLOW'])}  🔴 {len(buckets['RED'])}"]
    for status, emoji in (("RED", "🔴"), ("YELLOW", "🟡"), ("GREEN", "🟢")):
        for name, age in sorted(buckets[status], key=lambda x: (x[1] is None, -(x[1] or 0))):
            lines.append(f"{emoji} {name} — {_fmt_age(age)}")
    if not any(buckets.values()):
        lines.append("(no heartbeats found)")
    return "\n".join(lines)

def main():
    log.info("JARVIS HEALTH MONITOR online — roll-up every 6h")
    while True:
        try:
            tg(build_report())
            log.info("Health report sent")
        except Exception as e:
            log.error(f"Health loop: {e}")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
