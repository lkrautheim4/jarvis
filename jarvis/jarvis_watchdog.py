#!/usr/bin/env python3
"""
JARVIS WATCHDOG v2 — Hardened system monitor
Upgrades:
- Heartbeat-based dead detection (not just pgrep)
- Fixed restart command (was calling missing jarvis_fix.py)
- Intel signal grading trigger (hourly)
- RAM alert with bot kill recommendation
- Duplicate process detection and cleanup
"""
import subprocess, time, requests, json, os
from datetime import datetime
import logging

log = logging.getLogger("jarvis_watchdog")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

TELEGRAM_TOKEN = __import__("jarvis_secrets").TG_TOKEN_TRADER
ALPACA_KEY     = __import__("jarvis_secrets").ALPACA_PAPER_KEY
ALPACA_SECRET  = __import__("jarvis_secrets").ALPACA_PAPER_SECRET
TELEGRAM_CHAT  = "7534553840"
JARVIS_DIR     = "/root/jarvis"

# ── Bot registry ─────────────────────────────────────────────────────────────
BOTS = [
    {"name": "jarvis_master",        "file": "jarvis_master.py",        "flag": "", "critical": True},
    {"name": "jarvis_intelligence",  "file": "jarvis_intelligence.py",  "flag": "", "critical": False},
    {"name": "jarvis_level5",        "file": "jarvis_level5.py",        "flag": "", "critical": False},
    {"name": "jarvis_stocks_v2",     "file": "jarvis_stocks_v2.py",     "flag": "", "critical": False},
    {"name": "jarvis_options_brain", "file": "jarvis_options_brain.py", "flag": "", "critical": False},
    {"name": "jarvis_beast",         "file": "jarvis_beast.py",         "flag": "", "critical": False},
    {"name": "jarvis_premium",       "file": "jarvis_premium.py",       "flag": "", "critical": False},
    {"name": "jarvis_trader",        "file": "jarvis_trader.py",        "flag": "", "critical": False},
    {"name": "jarvis_signal_generator", "file": "jarvis_signal_generator.py", "flag": "-B", "critical": False},
    {"name": "lenny_predictions",    "file": "lenny_predictions.py",    "flag": "", "critical": False},
    {"name": "lenny_trader_bot",     "file": "lenny_trader_bot.py",     "flag": "", "critical": False},
    {"name": "options_grader",       "file": "options_grader.py",       "flag": "", "critical": False},
    {"name": "btc_ticker",           "file": "btc_ticker.py",           "flag": "", "critical": False},
    {"name": "jarvis_learning",      "file": "jarvis_learning.py",      "flag": "", "critical": False},
]

# Heartbeat timeout — bot considered dead if no heartbeat in N seconds
HEARTBEAT_TIMEOUT = 600   # 10 minutes
HEALTH_INTERVAL   = 300   # check every 5 min
INTEL_GRADE_INTERVAL = 3600  # grade intel signals every hour

def tg(msg: str):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": msg[:4000]}, timeout=5)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

# ── Alert throttle ───────────────────────────────────────────────────────────
# Health conditions (bot down, high RAM, stale brain, API down) persist across
# many 5-min cycles. Without a gate the same alert re-fires every cycle all day.
# tg_throttled() sends at most once per `cooldown` per `key`; state is persisted
# to disk so a watchdog restart can't re-spam a still-standing condition.
_ALERT_STATE_FILE = f"{JARVIS_DIR}/jarvis_watchdog_alerts.json"

def _load_alert_state():
    try:
        with open(_ALERT_STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def tg_throttled(key: str, msg: str, cooldown: int = 3600):
    st = _load_alert_state()
    now = time.time()
    if now - st.get(key, 0) < cooldown:
        return
    st[key] = now
    # prune entries older than a day so the file can't grow unbounded
    st = {k: v for k, v in st.items() if now - v < 86400}
    try:
        with open(_ALERT_STATE_FILE, "w") as f:
            json.dump(st, f)
    except Exception:
        pass
    tg(msg)

def _real_pids(filename: str) -> list:
    """PIDs of actual python processes running <filename>.

    Matches on argv (via /proc/<pid>/cmdline), not on a bare command-line
    substring like `pgrep -f` does, so it ignores shell wrappers, grep/pgrep,
    and this watchdog itself — and won't confuse one bot for another whose
    name is a substring. A match requires a python interpreter (argv[0]) whose
    arguments include the target script by exact basename.
    Returned sorted ascending (highest PID = most recently started).
    """
    self_pid = os.getpid()
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == self_pid:
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv = [a for a in f.read().decode("utf-8", "replace").split("\0") if a]
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
            continue  # process vanished or not readable
        if not argv:
            continue
        if not os.path.basename(argv[0]).startswith("python"):
            continue  # exclude /bin/bash wrappers, pgrep, etc.
        if any(os.path.basename(a) == filename for a in argv[1:]):
            pids.append(pid)
    return sorted(pids)

def is_running(filename: str) -> bool:
    return len(_real_pids(filename)) > 0

def get_pid_count(filename: str) -> int:
    """Count how many instances of a bot are running."""
    return len(_real_pids(filename))

def kill_duplicates(filename: str):
    """Kill all but one instance of a running bot."""
    pids = _real_pids(filename)  # sorted ascending
    if len(pids) <= 1:
        return
    # Keep the most recent (highest PID), kill the rest
    for pid in pids[:-1]:
        try:
            subprocess.run(["kill", str(pid)], capture_output=True)
            log.warning(f"Killed duplicate {filename} PID {pid}")
        except:
            pass

def restart_bot(bot: dict):
    """Properly restart a bot with correct flags."""
    name  = bot["name"]
    fname = bot["file"]
    flag  = bot.get("flag", "")
    log_f = f"{JARVIS_DIR}/{name}.log"

    # Kill any existing instances first (only real python procs, not wrappers)
    for pid in _real_pids(fname):
        subprocess.run(["kill", str(pid)], capture_output=True)
    time.sleep(2)

    # Build command. jarvis_cascade is kept in a tmux session named 'cascade'
    # (kill any stale/empty session first so new-session doesn't collide).
    if name == "jarvis_cascade":
        cmd = (f"tmux kill-session -t cascade 2>/dev/null; "
               f"tmux new-session -d -s cascade "
               f"'python3 {flag} {JARVIS_DIR}/{fname} >> {log_f} 2>&1'")
    else:
        cmd = f"nohup python3 {flag} {JARVIS_DIR}/{fname} >> {log_f} 2>&1 &"
    subprocess.Popen(cmd, shell=True, cwd=JARVIS_DIR)
    time.sleep(3)

    # Verify it started
    if is_running(fname):
        log.info(f"Restarted {name} successfully")
        tg_throttled(f"restarted:{name}", f"✅ WATCHDOG: Restarted {name}", 1800)
    else:
        log.error(f"Failed to restart {name}")
        tg_throttled(f"restart_fail:{name}", f"❌ WATCHDOG: Failed to restart {name} — manual intervention needed", 1800)

def check_heartbeats() -> list:
    """
    Check SQLite heartbeats for bots that support it.
    Returns list of bot names that are dead by heartbeat.
    """
    dead = []
    try:
        import sqlite3
        conn = sqlite3.connect(f"{JARVIS_DIR}/jarvis_memory.db", timeout=5)
        rows = conn.execute("SELECT bot_name, last_seen FROM bot_heartbeats").fetchall()
        conn.close()
        for bot_name, last_seen in rows:
            try:
                age = (datetime.now() - datetime.fromisoformat(last_seen)).total_seconds()
                if age > HEARTBEAT_TIMEOUT:
                    dead.append(bot_name)
            except:
                pass
    except:
        pass
    return dead

def check_ram() -> float:
    try:
        r = subprocess.run(["free", "-m"], capture_output=True, text=True)
        mem   = r.stdout.split("\n")[1].split()
        total = int(mem[1]); used = int(mem[2])
        pct   = round(used / total * 100, 1)
        if pct > 90:
            tg_throttled("ram_critical", f"🚨 RAM CRITICAL: {pct}% ({used}MB/{total}MB)\nConsider killing: jarvis_beast, jarvis_congress")
        elif pct > 85:
            tg_throttled("ram_warning", f"⚠️ RAM WARNING: {pct}% ({used}MB/{total}MB)")
        return pct
    except:
        return 0

def check_log_errors(bot: dict) -> int:
    try:
        log_path = f"{JARVIS_DIR}/{bot['name']}.log"
        if not os.path.exists(log_path): return 0
        if time.time() - os.path.getmtime(log_path) > 300: return 0
        with open(log_path) as f:
            lines = f.readlines()[-50:]
        return sum(1 for l in lines if "ERROR" in l or "Traceback" in l)
    except:
        return 0

def check_brain_freshness() -> bool:
    # jarvis_central_brain.json removed with old bot stack — no-op until a replacement is wired
    return True

def check_external_apis():
    issues = []
    try:
        r = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=5)
        if r.status_code != 200: issues.append("Coinbase API down")
    except:
        issues.append("Coinbase unreachable")
    try:
        r = requests.get("https://paper-api.alpaca.markets/v2/account",
            headers={"APCA-API-KEY-ID": ALPACA_KEY,
                     "APCA-API-SECRET-KEY": ALPACA_SECRET}, timeout=5)
        if r.status_code != 200: issues.append("Alpaca down")
    except:
        issues.append("Alpaca unreachable")
    return issues

def trigger_intel_grading():
    """Trigger intel signal grading — runs in background."""
    try:
        import sys
        sys.path.insert(0, JARVIS_DIR)
        import jarvis_brain as jb
        graded = jb.grade_intel_signals()
        if graded > 0:
            stats = jb.get_intel_signal_stats()
            lines = [f"📡 Intel signals graded: {graded}"]
            for sig_type, data in stats.items():
                if data["total"] >= 5:
                    lines.append(f"  {sig_type}: {data['win_rate']}% WR ({data['total']} signals)")
            if len(lines) > 1:
                tg("\n".join(lines))
            log.info(f"Graded {graded} intel signals")
    except Exception as e:
        log.warning(f"Intel grading error: {e}")

def health_check():
    issues = []

    # RAM check
    ram_pct = check_ram()

    # Duplicate process check
    for bot in BOTS:
        count = get_pid_count(bot["file"])
        if count > 1:
            log.warning(f"Duplicate {bot['name']}: {count} instances — killing extras")
            kill_duplicates(bot["file"])
            issues.append(f"{bot['name']} had {count} instances (fixed)")

    # Process check
    dead_by_heartbeat = check_heartbeats()
    for bot in BOTS:
        running = is_running(bot["file"])
        if not running:
            issues.append(f"{bot['name']} DOWN")
        else:
            errors = check_log_errors(bot)
            if errors >= 5:
                issues.append(f"{bot['name']} — {errors} recent errors")
            # Cross-check heartbeat for critical bots
            if bot["critical"] and bot["name"] in dead_by_heartbeat:
                issues.append(f"{bot['name']} — process running but no heartbeat ({HEARTBEAT_TIMEOUT//60}min)")

    # Brain check
    check_brain_freshness()

    # External APIs
    api_issues = check_external_apis()
    issues.extend(api_issues)

    if issues:
        # Key on the issue set so a standing set of issues doesn't re-alert
        # every cycle, but a newly-appearing issue still fires.
        tg_throttled("health:" + "|".join(sorted(issues)),
                     "🚨 SYSTEM HEALTH ALERT\n" + "\n".join(f"❌ {i}" for i in issues))
        log.warning(f"Health issues: {issues}")
    else:
        log.info(f"Health OK — {len(BOTS)} bots | RAM {ram_pct}%")

def main():
    log.info("JARVIS WATCHDOG v2 ONLINE")
    tg(f"🐕 WATCHDOG v2 ONLINE\nMonitoring {len(BOTS)} bots\nHeartbeat timeout: {HEARTBEAT_TIMEOUT//60}min\nHealth check every {HEALTH_INTERVAL//60}min")

    last_intel_grade = 0

    while True:
        try:
            # Check and restart dead bots
            for bot in BOTS:
                if not is_running(bot["file"]):
                    log.warning(f"{bot['name']} DOWN — restarting")
                    tg_throttled(f"down:{bot['name']}", f"⚠️ WATCHDOG: {bot['name']} DOWN — restarting...", 1800)
                    restart_bot(bot)
                    time.sleep(5)

            # Full health check
            health_check()

            # Hourly intel grading
            if time.time() - last_intel_grade >= INTEL_GRADE_INTERVAL:
                trigger_intel_grading()
                last_intel_grade = time.time()

            time.sleep(HEALTH_INTERVAL)

        except Exception as e:
            log.error(f"Watchdog error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
