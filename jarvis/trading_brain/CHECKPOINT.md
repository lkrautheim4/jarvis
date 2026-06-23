# TRADING_BRAIN CHECKPOINT — 2026-06-21

## Source of truth
- LIVE DB: /root/jarvis/jarvis_memory.db (heartbeats current)
- DEAD: jarvis_brain.db (no writers), backups/*.db (snapshots)

## Done + verified
- kalshi_bets: 35 phantom pnl rows nulled, 2 REAL kept. Backup: jarvis_memory.db.bak.*
- grade_bet(won) in jarvis_memory.py:144 NEUTERED — raises RuntimeError, 0 callers. Backup: jarvis_memory.py.bak.*
- Forward grader = kalshi_grader.py grade_bets() — grades only from real settlement (_compute_pnl from real entry prices). Clean.
- Tripwire: trading_brain/tripwire.py, READ-ONLY (query_only=ON), cron 5,20,35,50. Fires Telegram only on FABRICATED/UNDERIVABLE pnl. Verified firing unattended, reports clean.
- Telegram sender pattern: jarvis_secrets.TG_TOKEN_TRADER / TG_CHAT_ID

## OPEN — next session start here
1. #3 CLV: close_yes_price logged only on API-settled rows. Need closing line on EVERY kalshi_predictions row. Find the file that writes kalshi_predictions at bet time — that's the hook.
2. get_bet_stats() in jarvis_memory.py SUMs pnl column — now mostly NULL. Audit callers before trusting any P&L stat.
3. Phase 1 observer (read-only) — not built. Clean ledger now exists to sit on.

## Standing rules honored
- No deletes. Backups before every edit. Read-only checks. Nothing touched: secrets, dashboards, working bots.

## 2026-06-22 PM — get_bet_stats fossil-read fix
- ROOT CAUSE: jarvis_memory.py DB_PATH = jarvis_brain.db (May-28 fossil, 18 rows, +400.58).
  get_bet_stats() read the fossil; feeds build_context() -> live model prompt. Fabricated self-image in decision loop.
- FIX: added KALSHI_RO (jarvis_memory.db, mode=ro); get_bet_stats repointed to canonical, source='auto'.
  DB_PATH unchanged (6 other tables write there live: price_ticks/predictions/pred_calls/trades/kv_store/events).
- HONESTY: pnl now reports pnl_priced + priced_n + note. 35/38 auto bets unpriced (yes_price=0.0 -> pnl NULL).
  Grader live truth: WR 17.5% (7/40), P&L -50.84 (incl 2 manual). Auto-only: 7/38, +0.16 from 3 priced.
- build_context line 292 repointed to new keys (was KeyError on 'pnl').
- Backups: jarvis_memory.py.bak_1782146754 / _933 / _990.

## STILL OPEN (priority order)
1. Tripwire muzzled: cron runs trading_brain/tripwire.py, dies on 'No module named jarvis_secrets' (sys.path). Watchdog blind.
2. Entry-price logging: 35/38 auto bets have no fill price -> P&L structurally uncomputable. THE real build. CLV blocked on this.
3. SUSPECT: build_context "Hourly pred: 84% (16/19)" — unaudited, likely fabricated like every prior pred-accuracy number. Audit before trusting.
4. Kernel CVE-2026-31431 reboot — LAST, after 1-3.

## 2026-06-22 PM (cont) — tripwire muzzle + false-positive fix
- MUZZLE: send_telegram did bare `import jarvis_secrets`; cron runs from trading_brain/ so it failed.
  Fix: sys.path guard at send_telegram call site. Telegram send confirmed live (phone alert received).
  NOTE: earlier "No module" log lines were concurrent cron ticks writing the shared log — diagnose from piped stdout, not the logfile.
- FALSE POSITIVE: classify() flagged 325-328 UNDERIVABLE because it divided by dollars (NULL on auto bets).
  Those rows are per-CONTRACT: WIN=+(1-e), LOSS=-(e). Grader pnl reconciles exactly. NOT fabrication.
  Fix: classify() now handles null-dollars per-contract case. Tripwire returns "ledger clean", silent.
- OPEN QUESTION (strategy, not integrity): auto bets carry NULL dollars = 1-unit nominal sizing.
  Confirm this is intentional vs dollars-never-logged. Numbers are internally consistent either way.
- Backups: tripwire.py.bak_1782152487 / _1782153273.

## 2026-06-22 PM (cont) — stale hourly-pred line disabled
- get_prediction_accuracy() reads orphaned `predictions` table. Both copies frozen:
  jarvis_brain.db = May-28 (19 graded, 16 correct = the fake 84%); jarvis_memory.db = Jun-9 (158 graded).
- Live hourly BTC preds grade into btc_memory.json (350 rows, 294 graded, current to today 14:31).
- INTERIM FIX: removed "Hourly pred: X%" line from build_context (was feeding model May-28 fossil 84%).
- OPEN (#3 now reframed): reconnect hourly-pred accuracy to btc_memory.json live grader.
  DO NOT rewire blind — validate btc_memory schema/filter (graded def, sub-hourly exclusion, win field) first.

## 2026-06-22 PM — Path A COMPLETE: hourly-pred reconnected to live source
- Added get_btc_pred_stats() in jarvis_memory.py: reads btc_memory.json per-row (graded + target_hit not null).
- build_context now renders "BTC hourly pred: 36% (106/294 graded)" — LIVE, was fossil 84%.
- THIRD fabrication found: btc_memory.json's own 'stats' block is frozen (says 5/350 ~1%). Per-row truth = 36%.
  get_btc_pred_stats ignores the stats block; reads rows. Stats rollup is a dead aggregator (NOT fixed, just bypassed).
- Old get_prediction_accuracy() left in place but unused (reads dead SQLite predictions table).
- TRUTH: BTC hourly directional accuracy is 36%, below coin-flip. Real signal about strategy edge.
- Backup: jarvis_memory.py.bak_1782153807.

## 2026-06-22 PM — Path B Step 1 COMPLETE: manual options copilot logging+grading
- Schema: ALTER options_trades ADD contracts INTEGER, screenshot TEXT (46 rows preserved, checksum 135523 unchanged).
- SCHEMA.md written: canonical column map + "NEVER use entry_price/exit_price (uniformly 0.0)" warning.
- jarvis_memory_db.py: added log_manual_option, close_manual_option (direction-aware P&L),
  setup_analytics, time_of_day_analytics (ET hour), regime_analytics, manual_summary.
  Auto-writers (log_options_trade etc) UNTOUCHED.
- Lifecycle proven: DEBIT +750 WIN, CREDIT +300 WIN (sign inversion correct), analytics live, test rows deleted, baseline restored.
- Backups: jarvis_memory_db.py.bak_1782154789/_826; jarvis_memory.db.bak_1782154558.
- OPEN: auto-writers not yet migrated to canonical (deferred, Plan A(b)). No real manual trades logged yet (summary=0, honest).

## 2026-06-22 PM — STOP POINT (options copilot engine done, interface deferred)
- Engine verified live: log_manual_option/close_manual_option/analytics all working, lifecycle proven.
- Journal clean: 46 rows / checksum 135523. Three accidental placeholder SPY trades (5209-5211) were logged then deleted.
- NEXT SESSION (first task): repoint OPT OPEN/OPT CLOSE in jarvis_master.py (line ~1638) to canonical functions.
  Old handler has 3 bugs: DEBIT sign inverted ((entry-exit) wrong for debit), no contracts multiplier, manual WIN/LOSS.
  Diff designed + reviewed. Touches LIVE core bot — do with backup/ast.parse/isolated-test/restart, fresh paste channel.
- Until then: log real trades via direct python call to log_manual_option (be deliberate; placeholder values pollute the journal).

## NEXT OBJECTIVE — screenshot-to-trade logging (decided 2026-06-22)
- Lenny wants: screenshot Webull fill on phone -> send to Telegram -> Claude vision reads fields -> confirm -> log.
- NO manual typing. The `screenshot` column already exists for the image path.
- Build needs: (1) Telegram image-receive in jarvis_master loop, (2) Claude vision API extract
  (ticker/direction/strike/premium/contracts/dte), (3) confirm echo, (4) write via log_manual_option (DONE/verified).
- Touches LIVE core bot -> do in fresh session w/ backup, ast.parse, isolated test, restart.
- logtrade.py CLI exists + works but Lenny finds it too tedious; screenshot flow supersedes it.
- Storage/grading/analytics engine is DONE and verified. Only the capture interface remains.

## KNOWN MINOR — lenny_predictions regime grader (non-urgent)
- lenny_predictions.py logs hourly: "Regime grader failed (non-fatal): No module named 'btc_regime_grader'"
- Bot is HEALTHY (PID up 19h, predictions still sending, edge gate working). Error is cosmetic/non-fatal.
- Cause: leftover import/call to btc_regime_grader (removed/renamed in early-June BTC pipeline migration).
- FIX (next session, low priority): remove the dead regime-grader call OR restore the module. 1 line.
- Watchdog counts these 9 hourly log-lines and fires a health alert — alert is noise, not a real failure.
