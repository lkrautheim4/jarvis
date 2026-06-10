#!/usr/bin/env python3
"""
JARVIS BRIEFING BOT
- 7am morning briefing via Telegram
- Bot health watchdog — pings all bots, reports dead ones
- News throttle manager — max 3 alerts/day, rest in briefing
- Self-improvement summary
Runs as a standalone lightweight process
"""
import time, requests, json, os, subprocess
from datetime import datetime, timedelta

TG_TOKEN  = __import__("jarvis_secrets").TG_TOKEN_TRADER
TG_CHAT   = "7534553840"
from jarvis_secrets import CLAUDE_API_KEY as CLAUDE_KEY

import sys
sys.path.insert(0, "/root/jarvis")
import jarvis_central_brain as brain

BOTS = {
    "jarvis_master":       "/root/jarvis/jarvis_master.py",
    "jarvis_stocks_v2":    "/root/jarvis/jarvis_stocks_v2.py",
    "jarvis_options":      "/root/jarvis/jarvis_options.py",
    "jarvis_level5":       "/root/jarvis/jarvis_level5.py",
    "jarvis_intelligence": "/root/jarvis/jarvis_intelligence.py",
}

import logging
logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger("JARVIS-BRIEFING")

def tg(msg, token=None):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token or TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": str(msg)[:4000]}, timeout=10)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def is_bot_running(script_name):
    """Check if a bot process is alive"""
    try:
        result = subprocess.run(
            ["pgrep", "-f", script_name],
            capture_output=True, text=True)
        return result.returncode == 0
    except: return False

def check_all_bots():
    """Check each bot and update central brain"""
    dead_bots = []
    for bot_name, script in BOTS.items():
        alive = is_bot_running(os.path.basename(script))
        if alive:
            brain.update_bot_heartbeat(bot_name)
        else:
            b = brain.read_brain()
            status = b.get("bot_status", {})
            if bot_name in status:
                status[bot_name]["alive"] = False
            brain.write_brain({"bot_status": status})
            dead_bots.append(bot_name)
    return dead_bots

def restart_bot(bot_name, script_path):
    """Restart a dead bot"""
    try:
        log_file = script_path.replace(".py", ".log")
        subprocess.Popen(
            ["nohup", "python3", script_path],
            stdout=open(log_file, "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True
        )
        log.info(f"Restarted {bot_name}")
        return True
    except Exception as e:
        log.error(f"Failed to restart {bot_name}: {e}")
        return False

def run_health_check():
    """Full health check — detect dead bots, alert, optionally restart"""
    dead = check_all_bots()
    if dead:
        msg = f"⚠️ JARVIS HEALTH ALERT\nDead bots detected:\n"
        for bot in dead:
            msg += f"❌ {bot}\n"
            # Auto-restart
            script = BOTS.get(bot)
            if script and os.path.exists(script):
                if restart_bot(bot, script):
                    msg += f"  ↳ Auto-restarted ✅\n"
                else:
                    msg += f"  ↳ Restart failed ❌\n"
        tg(msg)
        log.warning(f"Dead bots: {dead}")
    else:
        log.info("All bots alive")
    return dead

def run_ai_health_analysis():
    """Ask Claude to analyze recent logs and suggest improvements"""
    b = brain.read_brain()
    status = b.get("bot_status", {})
    improvement_log = b.get("improvement_log", [])[-5:]
    kalshi_wr = b.get("kalshi_win_rate", 0)
    consec_losses = b.get("consecutive_losses", 0)
    winning = b.get("winning_conditions", {})
    losing  = b.get("losing_conditions", {})

    # Build error summary
    error_summary = []
    for bot, data in status.items():
        if data.get("errors", 0) > 0:
            error_summary.append(f"{bot}: {data.get('last_error','unknown error')}")

    prompt = f"""You are Jarvis self-improvement engine. Analyze system health and suggest ONE specific improvement.

SYSTEM STATE:
Kalshi win rate: {kalshi_wr}%
Consecutive losses: {consec_losses}
Winning patterns: {len(winning)} fingerprints
Losing patterns: {len(losing)} fingerprints
Recent errors: {chr(10).join(error_summary) if error_summary else 'None'}
Recent improvements: {chr(10).join([i['change'] for i in improvement_log]) if improvement_log else 'None'}

Based on this data, what is the single most impactful improvement Jarvis should make?
Reply in 2-3 sentences max. Be specific and actionable."""

    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": 150,
                  "messages": [{"role": "user", "content": prompt}]}, timeout=20)
        if r.status_code == 200:
            suggestion = r.json()["content"][0]["text"].strip()
            brain.log_improvement("AI analysis run", suggestion)
            return suggestion
    except Exception as e:
        log.error(f"AI health: {e}")
    return None

def send_morning_briefing():
    """Send the full morning briefing"""
    log.info("Sending morning briefing")

    # Get AI insight
    ai_insight = run_ai_health_analysis()

    # Format briefing
    msg = brain.format_morning_briefing()

    # Opening Range Breakout levels — last trading day (weekend/pre-market → Friday)
    try:
        import jarvis_orb
        msg += f"\n{'='*26}\n📐 OPENING RANGE (15-min)\n{jarvis_orb.format_orb_line('SPY')}"
    except Exception as e:
        log.warning(f"ORB section: {e}")

    # Add AI insight
    if ai_insight:
        msg += f"\n{'='*26}\n🧠 AI INSIGHT\n{ai_insight}"

    tg(msg)

    # Mark briefing sent
    brain.write_brain({"briefing_sent_date": datetime.now().strftime("%Y-%m-%d")})
    log.info("Morning briefing sent")

def main():
    log.info("JARVIS BRIEFING BOT ONLINE")
    brain.init_brain()

    last_health_check = 0
    last_briefing_date = ""

    while True:
        try:
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            hour_edt = (now.hour - 4) % 24

            # Health check every 5 minutes
            if time.time() - last_health_check >= 300:
                run_health_check()
                last_health_check = time.time()

            # Morning briefing at 7am EDT
            b = brain.read_brain()
            briefing_sent = b.get("briefing_sent_date", "")
            if hour_edt == 7 and now.minute < 5 and briefing_sent != today:
                send_morning_briefing()
                last_briefing_date = today

            time.sleep(30)

        except Exception as e:
            log.error(f"Main loop: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
