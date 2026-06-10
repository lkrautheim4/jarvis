#!/bin/bash
# Quiet weekend watcher: sleep until the next weekday 09:30 ET open, then watch
# the first jarvis_options_brain scan and capture any new options_trades rows
# (verifying the source='jarvis_auto' insert path in production). Exits with a
# report ~90 min after open, which re-invokes the assistant to summarize.
LOG=/root/jarvis/jarvis_options_brain.log
DB=/root/jarvis/jarvis_memory.db

BASEID=$(python3 -c "import sqlite3;c=sqlite3.connect('$DB');print(c.execute('SELECT COALESCE(MAX(id),0) FROM options_trades').fetchone()[0])")
TARGET=$(python3 -c "
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
et=datetime.now(ZoneInfo('America/New_York')); d=et
while True:
    c=d.replace(hour=9,minute=30,second=0,microsecond=0)
    if c>et and d.weekday()<5: break
    d=d+timedelta(days=1)
print(int(c.timestamp()))")

# Sleep until market open (poll the clock every 10 min).
while [ "$(date +%s)" -lt "$TARGET" ]; do sleep 600; done

# Market open — watch ~90 min for the first real scan.
SAW_SCAN=no
for i in $(seq 1 18); do
  if tail -n 150 "$LOG" | grep -qE "Scanning [0-9]+ tickers"; then SAW_SCAN=yes; fi
  sleep 300
done

echo "===== OPTIONS SCAN MONITOR REPORT ($(date -u +%Y-%m-%dT%H:%MZ)) ====="
echo "saw_scan_line=$SAW_SCAN  baseline_max_id=$BASEID"
echo "--- recent jarvis_options_brain.log ---"
tail -n 60 "$LOG" | grep -vE "self\._sock|recv_into"
echo "--- NEW options_trades rows since monitor start (id > $BASEID) ---"
python3 -c "
import sqlite3
c=sqlite3.connect('$DB'); c.row_factory=sqlite3.Row
rows=list(c.execute('SELECT id,ts,ticker,strategy,strike,premium,regime,fear_greed,score,source FROM options_trades WHERE id>? ORDER BY id', ($BASEID,)))
print('new_row_count=', len(rows))
for r in rows[:12]: print(dict(r))
"
