# NEXT SESSION CHECKPOINT — 2026-06-21

## Current system status

Live bots (heartbeat ≤ 10 min ago as of ~17:38):
- jarvis_master
- jarvis_intelligence
- jarvis_level5
- jarvis_stocks_v2
- jarvis_beast
- lenny_predictions
- lenny_trader_bot

Stale / dead bots (last seen 2026-06-14 or older):
- jarvis_options_brain (heartbeat in bot_heartbeats table: 2026-06-14; brain key options_brain_heartbeat shows 2026-06-21T13:38 — discrepancy, worth checking)
- jarvis_cascade, jarvis_briefing, jarvis_congress, jarvis_trade_advisor (old stack, expected dead)

## What is confirmed working

- BET, BET15, WIN, LOSS, MANUAL, CLOSE commands all route through jarvis_manual_bets.py
- kalshi_bets has `dollars` and `entry_spot` columns (verified via PRAGMA)
- BET15 logs entry_spot (BTC spot at prediction time) correctly
- Plain BET fix committed (0bd782b) — entry_spot now passed to log_manual_bet()
- All hardcoded secrets removed from .py and .sh files — zero grep hits
- secrets.json holds: CLAUDE_API_KEY, TG_TOKEN_TRADER/INTEL/LENNY, ALPACA keys, TG_CHAT_ID, KALSHI_API_KEY, KALSHI_API_KEY_TRADER
- jarvis_watchdog monitors 14 bots including jarvis_learning
- learned_ticker_rules wired into jarvis_beast as SIGNAL 7
- manual_stats() aggregates only source='manual_user' rows, never mixed with auto

## What is still broken / unverified

- **entry_spot fix is in code but NOT yet live.** jarvis_master.py was not restarted after commit 0bd782b. The fix will not take effect until master restarts.
- **Smoke test row still in DB.** id=322 (2026-06-21, BET YES $50, LOSS) has entry_spot=NULL — logged before the fix. This is expected but the row is still there.
- **jarvis_options_brain heartbeat discrepancy.** bot_heartbeats table shows 2026-06-14; brain key options_brain_heartbeat shows 2026-06-21T13:38. One of them is stale/wrong.

## Exact files touched today

```
jarvis_master.py          — entry_spot fix (BET branch); BET15/WIN/LOSS/MANUAL/CLOSE wired to jmb
jarvis_manual_bets.py     — new: log_manual_bet, grade_manual_bet, manual_stats
jarvis_watchdog.py        — added jarvis_learning; gutted brain check; secrets for Alpaca
jarvis_beast.py           — SIGNAL 7 (learned_ticker_rules); secrets for Alpaca + TG_CHAT_ID
jarvis_health.sh          — TG_CHAT_ID pulled from jarvis_secrets (was hardcoded)
lenny_predictions.py      — KALSHI_API_KEY and TG_CHAT_ID from jarvis_secrets
kalshi_grader.py          — KALSHI_API_KEY from jarvis_secrets
lenny_trader_bot.py       — KALSHI_API_KEY_TRADER from jarvis_secrets
jarvis_secrets.py         — added KALSHI_API_KEY, KALSHI_API_KEY_TRADER (gitignored, not committed)
secrets.json              — added KALSHI_API_KEY, KALSHI_API_KEY_TRADER (gitignored, not committed)
```

## Current git commit

```
0bd782b Log entry_spot on plain BET (was already fetched, not passed)
```

## One next task: verify entry_spot is captured live after master restart

The fix is shipped but master is running the old code. Steps:

1. Restart master:
   ```
   pkill -f jarvis_master.py; sleep 3; nohup python3 /root/jarvis/jarvis_master.py >> /root/jarvis/jarvis_master.log 2>&1 &
   ```

2. Send a test BET via Telegram:
   ```
   BET YES 1 smoke_entry_spot
   ```

3. Verify entry_spot is not NULL:
   ```python
   python3 -c "
   import sqlite3
   conn = sqlite3.connect('jarvis_memory.db')
   cols = [c[1] for c in conn.execute('PRAGMA table_info(kalshi_bets)').fetchall()]
   rows = conn.execute('SELECT * FROM kalshi_bets WHERE source=\"manual_user\" ORDER BY id DESC LIMIT 3').fetchall()
   for r in rows: print(dict(zip(cols, r)))
   conn.close()
   "
   ```

   Expected: most recent row has entry_spot = current BTC price (non-NULL).

4. Grade it out:
   ```
   LOSS
   ```

5. Only after entry_spot is confirmed non-NULL in the DB: the fix is verified live.
