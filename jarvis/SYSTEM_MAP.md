# SYSTEM_MAP.md — Vision Capture (screenshot → options trade logging)

_Last updated: 2026-06-23. Scope: the screenshot-to-DB options logging pipeline and its production-safety layers._

## 1. PURPOSE
jarvis_vision_capture.py is a Telegram bot daemon. You send a screenshot of an options
fill; Claude vision extracts the fields, you confirm, and on LOG it writes a verified row
to options_trades. A `logged:` confirmation means the row provably exists.

## 2. BOT IDENTITY
- Daemon polls token secrets.TG_TOKEN_SCREENSHOT → bot @screen_shot_options_bot.
- Send fill screenshots to @screen_shot_options_bot; reply LOG / CLOSE / CONFIRM / DTE <n>.
- Bot chat: TG_CHAT = 7534553840 (krautdog user id — same across all bots).
- WHY TG_TOKEN_SCREENSHOT not TG_TOKEN_TRADER: jarvis_master, lenny_trader_bot, and
  jarvis_trader all poll getUpdates on TG_TOKEN_TRADER, starving vision_capture of messages.
  TG_TOKEN_SCREENSHOT is exclusively polled by vision_capture — no queue contention.
  Switched 2026-06-23. Confirmed live: getMe ok, startup msg delivered (msg_id 41).

## 3. DATA TARGET
- DB: /root/jarvis/jarvis_memory.db (canonical, WAL mode)
- Table: options_trades ; Write fn: jarvis_memory_db.log_manual_option(...)
- Rows from this path: is_real=1, source='manual_copilot', status='open'
- _MC_DB == /root/jarvis/jarvis_memory.db (writer == reader)
- direction MUST be 'DEBIT' or 'CREDIT' (drives P&L sign).

## 4. STARTUP PATH
@reboot cron -> sleep 60 -> bash start_all.sh -> loop BOTS[] (15 core + vision_capture)
-> per bot: pgrep skip if running, else nohup launch -> vision main(): _acquire_singleton()
(flock) -> poll getUpdates as sole consumer.
- start_all.sh is idempotent (no pkill, no duplicates). vision_capture is in BOTS[].
- start_all logs vision to jarvis_vision_capture.log. Old vision_capture.log deprecated.

## 5. SINGLETON / PID-LOCK
Two layers: (1) pgrep skip in start_all = optimization; (2) flock = guarantee.
- _acquire_singleton() takes non-blocking exclusive flock on vision_capture.lock.
  Second instance prints "holds the lock -- exiting", exits 0, before any getUpdates.
- Lock file is 0 bytes (kernel handle, not content). PID in vision_capture.pid (visibility).
- flock auto-releases on death -> clean restart needs no manual unlock.

## 6. WRITE CONFIRMATION CONTRACT
_save(): calls log_manual_option -> rid, then SELECTs the row by id. Row missing -> raises
-> bot says "save failed". Row present -> bot says "logged: <id>". So logged: means
persisted. (Old bug: _save echoed return value without readback; a rolled-back write
still printed "logged: 5213" persisting nothing.)

## 7. HEALTH CHECK (read-only)
python3 vision_health.py — writes nothing to options_trades.
[1] liveness (PID+cmdline) HARD; [2] singleton ==1 HARD; [3] INFO other token-sharers up;
[4] INFO last write timestamp. Exit non-zero on any HARD fail -> drives alerter.

## 8. MANUAL CANARY (run by hand only, never scheduled)
python3 vision_canary.py — writes sentinel __CANARY__ row, reads back, deletes it
(guarded: only that rowid AND symbol='__CANARY__'). Leaves table clean. Only true test
of the write path. If cleanup ever fails:
sqlite3 jarvis_memory.db "DELETE FROM options_trades WHERE symbol='__CANARY__'"

## 9. FAILURE ALERTING
vision_alert.py — cron */15: */15 * * * * cd /root/jarvis && python3 vision_alert.py
- Channel: TG_TOKEN_INTEL -> @Jarvis_Stocks_Bot -> chat 7534553840 (independent of
  vision/trader token, so a vision failure can't break the alarm).
- Alert-on-change only: pass->fail 🔴 once; still-down 🔴 daily reminder; fail->pass 🟢 once;
  pass->pass silent. State: vision_alert_state.json (atomic).
- tg() swallows send errors (never crashes) -> a broken channel is SILENT; re-verify per §11.

## 10. TOKEN / NAMING DEBT (cleanup backlog, confirmed 2026-06-23 via getMe)
TG_TOKEN_TRADER     -> screenshottrader
TG_TOKEN_ADVISOR    -> LennyTraderBot (aliased to TRADER's bot)
TG_TOKEN_INTEL      -> Jarvis_Stocks_Bot
TG_TOKEN_LENNY      -> Lenny_predictions_bot
TG_TOKEN_PRED       -> Lenny_predictions_bot (aliased to LENNY's bot)
TG_TOKEN_SCREENSHOT -> screen_shot_options_bot (NOT the bot vision uses)
PASTE_YOUR_NEW_TOKEN-> unfilled placeholder, do not use
Latent risk: 6 scripts reference the trader token family (intelligence, master, trader,
lenny_predictions, lenny_trader_bot, vision_capture). flock protects vision_capture only;
if another starts polling the same bot, the duplicate-poller bug returns.

## 11. RECOVERY RUNBOOK ("nothing is logging")
1. python3 vision_health.py; echo $?   (0=alive; FAIL[1]/[2]=dead/dup -> step 2)
2. pgrep -af "[p]ython3.*jarvis_vision_capture.py"
   0 procs -> bash start_all.sh 2>&1 | grep vision
   >1 proc -> kill all, relaunch one
3. Launch says "holds the lock" but nothing alive (stale lock):
   rm -f vision_capture.lock && bash start_all.sh 2>&1 | grep vision
4. Sending screenshots, no reply? Confirm you're texting screenshottrader (§2). Check no
   other script drains the queue:
   ps aux | grep -v grep | grep -iE 'lenny_trader|intelligence|master|trader|predictions'
   (history: lenny_trader_bot stole the queue and starved vision.)
5. Prove write path: python3 vision_canary.py
6. Token sanity (no value printed):
   TOK=$(python3 -c "import jarvis_secrets as s; print(s.TG_TOKEN_TRADER)"); curl -s "https://api.telegram.org/bot${TOK}/getMe"
7. Alerts silent? Re-verify Jarvis_Stocks_Bot reaches you — tg() hides send failures.

## 12. FILE INVENTORY
jarvis_vision_capture.py  daemon (flock + verifying _save)
jarvis_memory_db.py       log_manual_option (write fn)
vision_health.py          read-only health check
vision_canary.py          manual write-path proof (self-cleaning)
vision_alert.py           cron alerter (independent channel)
vision_capture.lock       flock target (0 bytes = normal)
vision_capture.pid        running PID (visibility)
vision_alert_state.json   alert pass/fail state
start_all.sh              idempotent launcher (vision in BOTS[])
