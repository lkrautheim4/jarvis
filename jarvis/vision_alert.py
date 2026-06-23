#!/usr/bin/env python3
"""vision_alert.py - runs vision_health.py, alerts on STATUS CHANGE only.
Independent channel (TG_TOKEN_INTEL). Daily still-down reminder.
State: vision_alert_state.json. Run by cron every 15 min."""
import subprocess, json, os, sys
from datetime import datetime, timezone
import jarvis_secrets as s

JARVIS = "/root/jarvis"
STATE = f"{JARVIS}/vision_alert_state.json"
CHAT = "7534553840"
TOK = s.TG_TOKEN_INTEL
HEALTH = f"{JARVIS}/vision_health.py"

def tg(text):
    import urllib.request, urllib.parse
    url = f"https://api.telegram.org/bot{TOK}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": CHAT, "text": text}).encode()
    try:
        urllib.request.urlopen(url, data=data, timeout=20)
    except Exception:
        pass  # alert path must never crash the alerter

def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"status": "pass", "last_alert_date": None}

def save_state(st):
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"))
    os.replace(tmp, STATE)  # atomic

# run the read-only health check; exit code is source of truth
r = subprocess.run([sys.executable, HEALTH], capture_output=True, text=True)
now_fail = (r.returncode != 0)
detail = r.stdout.strip().split("\n")
fail_lines = [l for l in detail if l.startswith("FAIL")]
today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

st = load_state()
was_fail = (st.get("status") == "fail")

if now_fail and not was_fail:
    # transition pass -> fail: alert once
    tg("🔴 VISION CAPTURE DOWN\n" + "\n".join(fail_lines or ["health check failed"]))
    st = {"status": "fail", "last_alert_date": today}
    save_state(st)
elif now_fail and was_fail:
    # still down: daily reminder only
    if st.get("last_alert_date") != today:
        tg("🔴 VISION CAPTURE STILL DOWN (daily reminder)\n" + "\n".join(fail_lines or []))
        st["last_alert_date"] = today
        save_state(st)
elif not now_fail and was_fail:
    # recovery: fail -> pass, alert once
    tg("🟢 VISION CAPTURE RECOVERED")
    save_state({"status": "pass", "last_alert_date": None})
# pass -> pass: silent, no state write needed
