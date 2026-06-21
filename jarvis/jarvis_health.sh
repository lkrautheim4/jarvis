#!/bin/bash
# JARVIS health check — alerts ONLY when a bot is down. Token from jarvis_secrets.py
cd /root/jarvis

EXPECTED=(
  jarvis_master jarvis_intelligence jarvis_level5 jarvis_stocks_v2 jarvis_options_brain
  jarvis_beast jarvis_premium jarvis_trader jarvis_signal_generator jarvis_watchdog
  lenny_predictions lenny_trader_bot options_grader btc_ticker jarvis_learning
)

DEAD=()
for bot in "${EXPECTED[@]}"; do
  pgrep -f "python3.*${bot}\.py" >/dev/null || DEAD+=("$bot")
done

[ ${#DEAD[@]} -eq 0 ] && exit 0

ALIVE=$(( ${#EXPECTED[@]} - ${#DEAD[@]} ))
export HEALTH_MSG="JARVIS HEALTH ALERT: ${ALIVE}/${#EXPECTED[@]} up. DOWN: ${DEAD[*]} @ $(date)"

python3 <<'PYEOF'
import os, sys
sys.path.insert(0, '/root/jarvis')
from jarvis_secrets import TG_TOKEN_TRADER
import requests
try:
    requests.post(
        f'https://api.telegram.org/bot{TG_TOKEN_TRADER}/sendMessage',
        json={'chat_id': '7534553840', 'text': os.environ['HEALTH_MSG']},
        timeout=5)
except Exception as e:
    print(f'Health alert failed: {e}', file=sys.stderr)
PYEOF
