#!/usr/bin/env bash
# JARVIS start_all.sh
# Usage:  ./start_all.sh          — kill all daemons and restart
#         ./start_all.sh status   — print running/dead per bot, no changes
#
# ── CRON-MANAGED (never launched as daemons here) ────────────────────────────
# jarvis_macro.py              every 2h      (0 */2 * * *)
# jarvis_capital.py            hourly        (0 * * * *)
# jarvis_earnings.py           6am M-F       (0 6 * * 1-5)       one-shot
# jarvis_regime_confidence.py  hourly        (0 * * * *)
# jarvis_pred_audit.py         Mon 1pm       (0 13 * * 1)         one-shot
# track_paper_trades.py        1:35pm M-F    (35 13 * * 1-5)      one-shot
# grade_paper_trades.py        8:05pm M-F    (5 20 * * 1-5)       one-shot
# kalshi_grader.py             crash-recovery cron guard only
#                              (cron relaunches if dead; daemon IS started here)
# jarvis_health.sh             hourly shell health-check (not the Python bot)
#
# ── RETIRED / SUPERSEDED ─────────────────────────────────────────────────────
# jarvis_intel.py     → replaced by jarvis_intelligence.py
# jarvis_options.py   → replaced by jarvis_options_brain.py
# ─────────────────────────────────────────────────────────────────────────────

set -uo pipefail
DIR=/root/jarvis
cd "$DIR" || exit 1

# ── Bot registry ─────────────────────────────────────────────────────────────
# Format: "script.py|log_base|flags"   flags="" means no extra flags
# Watchdog is intentionally LAST — it monitors everything above it.
declare -a BOTS=(
  "jarvis_master.py|jarvis_master|-B"
  "jarvis_api.py|jarvis_api|-B"
  "jarvis_briefing.py|jarvis_briefing|-B"
  "jarvis_intelligence.py|jarvis_intelligence|-B"
  "jarvis_options_brain.py|jarvis_options_brain|-B"
  "jarvis_stocks_v2.py|jarvis_stocks_v2|-B"
  "jarvis_beast.py|jarvis_beast|-B"
  "jarvis_congress.py|jarvis_congress|-B"
  "jarvis_level5.py|jarvis_level5|-B"
  "jarvis_cascade.py|jarvis_cascade|-B"
  "jarvis_futures.py|jarvis_futures|-B"
  "jarvis_premium.py|jarvis_premium|-B"
  "lenny_predictions.py|lenny_predictions|-B"
  "lenny_trader_bot.py|lenny_trader_bot|"
  "jarvis_trader.py|jarvis_trader|"
  "jarvis_trump_monitor.py|jarvis_trump_monitor|"
  "options_grader.py|options_grader|-B"
  "kalshi_grader.py|kalshi_grader|-B"
  "btc_ticker.py|jarvis_btc|"
  "jarvis_health.py|jarvis_health|"
  "jarvis_trade_advisor.py|jarvis_trade_advisor|"
  "jarvis_watchdog.py|jarvis_watchdog|-B"
)

# ── Helpers ───────────────────────────────────────────────────────────────────
bot_pid() {
  pgrep -f "python3.*${1}" 2>/dev/null | head -1
}

# ── Status mode ───────────────────────────────────────────────────────────────
if [[ "${1:-}" == "status" ]]; then
  echo "=== JARVIS BOT STATUS $(date '+%Y-%m-%d %H:%M:%S') ==="
  ok=0; dead=0
  for entry in "${BOTS[@]}"; do
    IFS='|' read -r script logname flags <<< "$entry"
    pid=$(bot_pid "$script")
    if [[ -n "$pid" ]]; then
      mem=$(ps -p "$pid" -o %mem= 2>/dev/null | tr -d ' ')
      printf "  ✅ %-36s pid=%-7s %s%%\n" "$script" "$pid" "$mem"
      ok=$((ok + 1))
    else
      printf "  ❌ %-36s DEAD\n" "$script"
      dead=$((dead + 1))
    fi
  done
  echo "============================================"
  printf "  Running: %d / %d\n" "$ok" "$((ok + dead))"
  free -h | grep Mem
  exit 0
fi

# ── Restart mode ──────────────────────────────────────────────────────────────
echo "=== JARVIS OS RESTART — $(date) ==="

# Kill watchdog first so it can't resurrect bots mid-teardown
if pkill -f "python3.*jarvis_watchdog\.py" 2>/dev/null; then
  echo "  Stopped jarvis_watchdog"
fi
sleep 1

# Per-bot precise kill — never a blanket jarvis_ match
for entry in "${BOTS[@]}"; do
  IFS='|' read -r script logname flags <<< "$entry"
  [[ "$script" == "jarvis_watchdog.py" ]] && continue
  pkill -f "python3.*${script}" 2>/dev/null || true
done

# Also kill any leftover cascade tmux session from the old script
tmux kill-session -t cascade 2>/dev/null || true
sleep 3

# Clear stale bytecode
find "$DIR/__pycache__" -name "*.pyc" -delete 2>/dev/null || true

echo "Launching bots..."
started=0; skipped=0; missing=0

for entry in "${BOTS[@]}"; do
  IFS='|' read -r script logname flags <<< "$entry"
  logfile="$DIR/${logname}.log"

  if [[ -n "$(bot_pid "$script")" ]]; then
    printf "  ⚡ SKIP    %-36s (already running)\n" "$script"
    skipped=$((skipped + 1))
    continue
  fi

  if [[ ! -f "$DIR/$script" ]]; then
    printf "  ⚠️  MISSING %-36s (file not found)\n" "$script"
    missing=$((missing + 1))
    continue
  fi

  if [[ -n "$flags" ]]; then
    nohup python3 $flags "$DIR/$script" >> "$logfile" 2>&1 &
  else
    nohup python3 "$DIR/$script" >> "$logfile" 2>&1 &
  fi
  printf "  ✅ START   %-36s → %s\n" "$script" "$(basename "$logfile")"
  started=$((started + 1))
  sleep 0.3
done

echo ""
printf "  Started: %d  Skipped: %d  Missing: %d\n" "$started" "$skipped" "$missing"
echo "  Waiting for processes to stabilize..."
sleep 8

# Final status check
echo ""
echo "=== POST-LAUNCH STATUS ==="
ok=0; dead=0
for entry in "${BOTS[@]}"; do
  IFS='|' read -r script logname flags <<< "$entry"
  if [[ -n "$(bot_pid "$script")" ]]; then
    printf "  ✅ %s\n" "$script"
    ok=$((ok + 1))
  else
    printf "  ❌ %s  ← FAILED TO START\n" "$script"
    dead=$((dead + 1))
  fi
done
echo "========================="
printf "  Running: %d / %d\n" "$ok" "$((ok + dead))"
free -h | grep Mem
