#!/usr/bin/env python3
"""vision_health.py - READ-ONLY health check for jarvis_vision_capture.
Hard checks (1,2) drive exit code. Check 3 is INFO. Check 4 is WARN only.
Writes nothing to options_trades. Run: python3 vision_health.py"""
import os, sys, subprocess, sqlite3
from datetime import datetime, timezone

JARVIS = "/root/jarvis"
PID_FILE = f"{JARVIS}/vision_capture.pid"
DB = f"{JARVIS}/jarvis_memory.db"
SCRIPT = "jarvis_vision_capture.py"
KNOWN_POLLERS = ["jarvis_intelligence.py", "jarvis_master.py",
                 "jarvis_trader.py", "lenny_predictions.py", "lenny_trader_bot.py"]

hard_fail = False

def pgrep_list(name):
    r = subprocess.run(["pgrep", "-f", f"[p]ython3.*{name}"],
                       capture_output=True, text=True)
    return [l for l in r.stdout.strip().split("\n") if l]

def check_liveness():
    global hard_fail
    try:
        pid = open(PID_FILE).read().strip()
    except FileNotFoundError:
        print("FAIL  [1] liveness: no PID file"); hard_fail = True; return
    cmd = f"/proc/{pid}/cmdline"
    if not os.path.exists(cmd):
        print(f"FAIL  [1] liveness: PID {pid} not running"); hard_fail = True; return
    if SCRIPT not in open(cmd).read().replace("\x00", " "):
        print(f"FAIL  [1] liveness: PID {pid} not vision_capture"); hard_fail = True; return
    print(f"PASS  [1] liveness: PID {pid} alive and correct")

def check_singleton():
    global hard_fail
    n = len(pgrep_list(SCRIPT))
    if n == 1:
        print("PASS  [2] singleton: exactly 1 instance")
    elif n == 0:
        print("FAIL  [2] singleton: 0 instances (daemon dead)"); hard_fail = True
    else:
        print(f"FAIL  [2] singleton: {n} instances (DUPLICATE - flock breach)"); hard_fail = True

def check_pollers():
    up = [p for p in KNOWN_POLLERS if pgrep_list(p)]
    print(f"INFO  [3] other token-sharing scripts up: {', '.join(up) if up else 'none'}")

def check_freshness():
    try:
        con = sqlite3.connect(DB, timeout=5)
        row = con.execute("SELECT MAX(ts) FROM options_trades").fetchone()
        con.close()
    except Exception as e:
        print(f"WARN  [4] freshness: cannot read DB ({e})"); return
    if not row or not row[0]:
        print("INFO  [4] freshness: no trades logged yet"); return
    print(f"INFO  [4] freshness: last trade write {row[0]}")

print(f"=== vision_health @ {datetime.now(timezone.utc).isoformat()} ===")
check_liveness()
check_singleton()
check_pollers()
check_freshness()
print("=== RESULT:", "FAIL" if hard_fail else "PASS", "===")
sys.exit(1 if hard_fail else 0)
