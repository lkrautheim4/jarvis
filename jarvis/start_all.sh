#!/bin/bash
# JARVIS start_all.sh v3 - Idempotent, safe restart
# - Only starts bots NOT already running (no blanket pkill)
# - Cron-managed bots excluded (macro, capital, earnings, kalshi_grader)
# - Logs each start

cd /root/jarvis

# Nohup-managed bots (the 15 core)
BOTS=(
  "jarvis_master.py"
#KILLED #KILLED   "jarvis_intelligence.py"
#KILLED #KILLED   "jarvis_level5.py"
  "jarvis_stocks_v2.py"
#KILLED #KILLED   "jarvis_options_brain.py"
#KILLED #KILLED   "jarvis_beast.py"
  "jarvis_premium.py"
  "jarvis_trader.py"
  "jarvis_signal_generator.py"
  "jarvis_watchdog.py"
#KILLED #KILLED   "lenny_predictions.py"
  "lenny_trader_bot.py"
  "options_grader.py"
  "btc_ticker.py"
  "jarvis_learning.py"
)

echo "=== JARVIS START_ALL v3 @ $(date) ==="

STARTED=0
SKIPPED=0

for bot in "${BOTS[@]}"; do
  # Idempotency check: skip if already running
  if pgrep -f "[p]ython3.*${bot}" > /dev/null; then
    echo "SKIP (running): $bot"
    SKIPPED=$((SKIPPED+1))
  else
    if [ -f "/root/jarvis/$bot" ]; then
      nohup python3 "/root/jarvis/$bot" >> "/root/jarvis/${bot%.py}.log" 2>&1 &
      echo "START: $bot (pid $!)"
      STARTED=$((STARTED+1))
      sleep 1
    else
      echo "ERROR (missing): $bot"
    fi
  fi
done

echo ""
echo "=== SUMMARY ==="
echo "Started: $STARTED | Skipped (already up): $SKIPPED"
echo "Total target: ${#BOTS[@]}"
echo "Actually running: $(pgrep -f 'python3.*(jarvis_|lenny_|kalshi_|btc_|options_)' | wc -l)"
