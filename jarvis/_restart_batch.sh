#!/bin/bash
# Controlled rolling restart — one bot at a time, onto the new jarvis_secrets path.
# Supervisors (watchdog, briefing) are stopped first so they can't double-restart
# workers mid-roll, then brought up LAST. Kills by numeric PID; relaunches detached.
cd /root/jarvis || exit 1
JD=/root/jarvis

# pat: bare name + [.]py so this script's pgrep never self-matches and never
# catches the *_v2 / *_1 / *_brain siblings.
pids_of(){ pgrep -f "/${1}[.]py"; }

stop_bot(){
  local name="$1" p
  for p in $(pids_of "$name"); do kill "$p" 2>/dev/null; done
  sleep 1
  for p in $(pids_of "$name"); do kill -9 "$p" 2>/dev/null; done
  sleep 1
}

restart(){              # restart <name> <flag>
  local name="$1" flag="$2"
  stop_bot "$name"
  setsid python3 $flag "${JD}/${name}.py" >> "${JD}/${name}.log" 2>&1 < /dev/null &
  disown
  sleep 2
  local n; n=$(pids_of "$name" | wc -l)
  if [ "$n" -ge 1 ]; then echo "OK   $name (flag='${flag:- }', ${n} proc)"; else echo "FAIL $name"; fi
}

echo "=== 1) pause continuous supervisors ==="
stop_bot jarvis_watchdog; echo "stopped jarvis_watchdog"
stop_bot jarvis_briefing; echo "stopped jarvis_briefing"

echo "=== 2) rolling restart of workers ==="
restart jarvis_master        "-B"
restart jarvis_api           "-B"
restart jarvis_level5        "-B"
restart jarvis_stocks_v2     "-B"
restart jarvis_beast         "-B"
restart jarvis_congress      "-B"
restart jarvis_options_brain "-B"
restart jarvis_intelligence  "-B"
restart lenny_trader_bot     ""
restart kalshi_grader        "-B"
restart options_grader       "-B"
restart jarvis_futures       "-B"
restart jarvis_premium       "-B"
restart jarvis_trader        ""
restart jarvis_trump_monitor ""
restart lenny_predictions    "-B"

echo "=== 3) cascade (tmux) ==="
tmux kill-session -t cascade 2>/dev/null
tmux new-session -d -s cascade "python3 -B ${JD}/jarvis_cascade.py >> ${JD}/jarvis_cascade.log 2>&1"
sleep 2
if pgrep -f "/jarvis_cascade[.]py" >/dev/null; then echo "OK   jarvis_cascade (tmux)"; else echo "FAIL jarvis_cascade"; fi

echo "=== 4) bring supervisors back (briefing, then watchdog last) ==="
restart jarvis_briefing      "-B"
restart jarvis_watchdog      "-B"

echo "=== DONE ==="
