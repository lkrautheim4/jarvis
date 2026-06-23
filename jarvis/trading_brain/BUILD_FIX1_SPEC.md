# BUILD SPEC — Fix #1 (+#2 folded in): screenshot -> trade logging
# APPROVED 2026-06-22. Build ONLY in a fresh clean session. Touches LIVE core bot.

## FLOW
screenshot -> Telegram -> Claude vision extracts fields -> Leonard confirms/corrects
-> log_manual_option() -> options_trades in jarvis_memory.db

## LOCKED DECISIONS
1. Confirmation step EVERY time (no auto-log).
2. DEBIT/CREDIT chosen by button/tap — NEVER guessed from vision.
3. Open positions stay journal-based (no broker sync).
4. NO Webull import.
5. NO new database, NO new table — use existing jarvis_memory.db + options_trades.
6. Write via existing verified log_manual_option() / close_manual_option().
7. Save screenshot file path -> screenshot column.
8. source='manual_copilot'. 9. is_real=1.
10. Do NOT touch secrets/credentials/unrelated bots.

## SCOPE (build these, nothing more)
- Telegram IMAGE receive handler (new update type — photos, not just text commands).
- Vision extraction: send image to Claude API, parse ticker/strategy/strike/dte/premium/contracts.
- Confirmation/correction flow: bot echoes parsed fields + DEBIT/CREDIT buttons; user confirms or edits.
- On confirm: call log_manual_option(...). Save image to /root/jarvis/trade_screenshots/<id>.jpg, path into screenshot col.
- Safe logging + error handling (bad parse -> ask user to re-send or type, never write garbage).
- Test path: one sample screenshot end-to-end, then delete the test row, confirm baseline.

## FOLDED-IN #2
Also repoint existing OPT OPEN/OPT CLOSE (jarvis_master ~line 1638) to canonical functions
(log_manual_option/close_manual_option) — same handler, fixes DEBIT-sign/contracts/manual-grade bugs.

## SAFETY PROCEDURE (mandatory, core bot)
- Backup jarvis_master.py before edit. ast.parse after. Test handler logic in ISOLATION before restart.
- Restart ONLY jarvis_master, verify it comes back + responds to a known command (STATUS).
- Pre-reboot bot snapshot already practiced; same diff discipline.

## DO NOT in this build
No analytics features, no Webull, no dashboards, no new tables. Stop when screenshot->log works.

## STATE AT APPROVAL
- log_manual_option/close_manual_option/analytics: BUILT + VERIFIED (2026-06-22).
- screenshot + contracts columns: ADDED. SCHEMA.md written.
- Journal baseline clean: options_trades 46 rows / checksum 135523.
- Existing OPT handler still uses OLD broken writer — to be repointed.

## ===== PHASE 1 — START HERE NEXT SESSION (screenshot storage only) =====
APPROVED 2026-06-22. New dedicated bot already made: @screen_shot_options_bot
Token in jarvis_secrets.py as TG_TOKEN_SCREENSHOT (verified ok:True). Separate bot = no collision w/ master.

PHASE 1 BOUNDARY: storage only. NO vision, NO database, NO analytics. Just prove screenshots enter + store.

### Build via Claude Code on the box. Paste it this verbatim:
Create /root/jarvis/jarvis_screenshot.py — standalone Telegram bot, Phase 1 only, NO vision/DB/analytics.
- Token: __import__("jarvis_secrets").TG_TOKEN_SCREENSHOT. Never hardcode.
- Long-poll getUpdates, offset tracking (timeout=20, persist offset across loops).
- On a PHOTO message: take highest-res photo size, getFile, download from
  https://api.telegram.org/file/bot<token>/<file_path>.
- Save to /root/jarvis/trade_screenshots/<UTC_timestamp>_<sender_id>.jpg (mkdir if missing).
- Append to /root/jarvis/jarvis_screenshot.log: timestamp, filename, sender id, sender username.
- Reply via sendMessage: "screenshot received and stored: <filename>".
- Non-photo message: reply "Send a screenshot of your trade fill. (Phase 1: storage test only.)"
- try/except around poll loop so one bad update can't crash it; log errors, keep polling.
- Guard with if __name__ == "__main__".
- Do NOT import/touch jarvis_master, jarvis_memory_db, any DB, or any other secret.
- After creating: run  python3 -c "import ast; ast.parse(open('/root/jarvis/jarvis_screenshot.py').read()); print('ast OK')"  and show result.

### TEST PROCEDURE (after ast OK):
1. nohup python3 jarvis_screenshot.py > nohup_screenshot.log 2>&1 &
2. Phone: send a screenshot to @screen_shot_options_bot
3. Confirm 3 things: ls -la trade_screenshots/ (file there) | tail jarvis_screenshot.log (log line) | phone got reply
4. STOP. Phase 1 done = screenshots reliably enter + store.

### PRE-START REMINDERS:
- On phone: message @screen_shot_options_bot once first (Telegram won't deliver to a never-messaged bot).
- Baseline check before starting anything: options_trades should read 46 rows / checksum 135523.
- Phase 2 (vision extract + confirm + log_manual_option) is SEPARATE, do AFTER Phase 1 proven.
