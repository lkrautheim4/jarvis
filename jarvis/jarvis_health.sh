#!/bin/bash
cd /root/jarvis
cp btc_memory.json backups/btc_memory_$(date +%Y%m%d).json 2>/dev/null
cp kalshi_brain.json backups/kalshi_brain_$(date +%Y%m%d).json 2>/dev/null
cp jarvis_master_brain.json backups/jarvis_master_brain_$(date +%Y%m%d).json 2>/dev/null
find backups/ -name "*.json" -mtime +7 -delete 2>/dev/null
for bot in jarvis_master jarvis_stocks_v2 jarvis_intelligence jarvis_level5 jarvis_trader jarvis_watchdog; do
    if ! pgrep -f "$bot" > /dev/null; then
        nohup python3 ${bot}.py >> ${bot}.log 2>&1 &
        python3 -c "import requests; requests.post('https://api.telegram.org/bot8917241974:AAFLv5-0lZPAzkic2TEBlxkugxGQxuj9unk/sendMessage',json={'chat_id':'7534553840','text':'JARVIS RESTARTED: $bot'},timeout=5)"
    fi
done
echo "Health check $(date)"
