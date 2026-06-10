#!/bin/bash
echo "Starting JARVIS OS — 19 bots..."
# Run from the project dir so bots using relative paths resolve files to
# /root/jarvis/ (not /root/). Without this, several bots silently orphan
# their state files under /root/.
cd /root/jarvis || exit 1
# pkill uses ERE: "\|" is a LITERAL pipe (matches nothing), so the old pattern
# never killed anything. Kill the watchdog first so it can't resurrect bots
# mid-teardown, then the rest. Graders lack the jarvis_/lenny_ prefix.
pkill -f "jarvis_watchdog" 2>/dev/null
sleep 1
pkill -f "jarvis_|lenny_|kalshi_grader|options_grader" 2>/dev/null
sleep 3
rm -f /root/jarvis/__pycache__/*.pyc

nohup python3 -B /root/jarvis/jarvis_master.py        > /root/jarvis/jarvis_master.log 2>&1 &
nohup python3 -B /root/jarvis/jarvis_briefing.py      > /root/jarvis/jarvis_briefing.log 2>&1 &
nohup python3 -B /root/jarvis/jarvis_api.py           > /root/jarvis/jarvis_api.log 2>&1 &
nohup python3 -B /root/jarvis/jarvis_level5.py        > /root/jarvis/jarvis_level5.log 2>&1 &
nohup python3 -B /root/jarvis/jarvis_stocks_v2.py     > /root/jarvis/jarvis_stocks_v2.log 2>&1 &
nohup python3 -B /root/jarvis/jarvis_watchdog.py      > /root/jarvis/jarvis_watchdog.log 2>&1 &
nohup python3 -B /root/jarvis/jarvis_beast.py         > /root/jarvis/jarvis_beast.log 2>&1 &
nohup python3 -B /root/jarvis/jarvis_congress.py      > /root/jarvis/jarvis_congress.log 2>&1 &
nohup python3 -B /root/jarvis/jarvis_options_brain.py > /root/jarvis/jarvis_options_brain.log 2>&1 &
# Unified intelligence engine (replaces the retired jarvis_intel.py — see audit).
nohup python3 -B /root/jarvis/jarvis_intelligence.py  > /root/jarvis/jarvis_intelligence.log 2>&1 &
nohup python3    /root/jarvis/lenny_trader_bot.py     > /root/jarvis/lenny_trader_bot.log 2>&1 &
nohup python3 -B /root/jarvis/kalshi_grader.py        > /root/jarvis/kalshi_grader.log 2>&1 &
nohup python3 -B /root/jarvis/options_grader.py       > /root/jarvis/options_grader.log 2>&1 &
# Previously missing from start_all — the teardown pkill killed them but nothing
# relaunched them. Flags match how each runs (trader/trump_monitor have no -B).
nohup python3 -B /root/jarvis/jarvis_futures.py       > /root/jarvis/jarvis_futures.log 2>&1 &
nohup python3 -B /root/jarvis/jarvis_premium.py       > /root/jarvis/jarvis_premium.log 2>&1 &
nohup python3    /root/jarvis/jarvis_trader.py        > /root/jarvis/jarvis_trader.log 2>&1 &
nohup python3    /root/jarvis/jarvis_trump_monitor.py > /root/jarvis/jarvis_trump_monitor.log 2>&1 &
nohup python3 -B /root/jarvis/lenny_predictions.py    > /root/jarvis/lenny_predictions.log 2>&1 &
# jarvis_cascade runs in a tmux session named 'cascade' (matches the watchdog).
tmux kill-session -t cascade 2>/dev/null; tmux new-session -d -s cascade "python3 -B /root/jarvis/jarvis_cascade.py >> /root/jarvis/jarvis_cascade.log 2>&1"

sleep 8
echo "=== JARVIS OS STATUS ==="
for bot in jarvis_master jarvis_briefing jarvis_api jarvis_level5 jarvis_stocks_v2 \
           jarvis_watchdog jarvis_beast jarvis_congress jarvis_options_brain \
           jarvis_intelligence lenny_trader_bot kalshi_grader options_grader jarvis_cascade \
           jarvis_futures jarvis_premium jarvis_trader jarvis_trump_monitor lenny_predictions; do
    pid=$(pgrep -f "${bot}.py")
    mem=$(ps -p $pid -o %mem= 2>/dev/null | tr -d ' ')
    echo "$([ -n "$pid" ] && echo ✅ || echo ❌) $bot ${mem}%"
done
echo "========================"
free -h | grep Mem
