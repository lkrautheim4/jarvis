# END OF DAY — 2026-06-21

## Current git commit
```
5be8787 Fix jarvis_options_brain: write to bot_heartbeats via update_bot_heartbeat()
```

## Running services (15 processes)

| Bot | PID | bot_heartbeats |
|-----|-----|----------------|
| jarvis_master | 93959 | ✅ 17:52:21 |
| jarvis_intelligence | 66699 | ✅ 17:52:21 |
| lenny_trader_bot | 2182 | ✅ 17:52:17 |
| jarvis_level5 | 60883 | ✅ 17:52:16 |
| lenny_predictions | 77680 | ✅ 17:52:15 |
| jarvis_options_brain | 94376 | ✅ 17:52:11 (just fixed) |
| jarvis_stocks_v2 | 27367 | ✅ 17:51:49 |
| jarvis_beast | 92534 | ✅ 17:51:08 |
| jarvis_watchdog | 92002 | no heartbeat row (monitors others) |
| jarvis_premium | 1494 | no heartbeat row |
| jarvis_signal_generator | 1506 | no heartbeat row |
| jarvis_trader | 2170 | no heartbeat row |
| options_grader | 1511 | no heartbeat row |
| btc_ticker | 3257 | no heartbeat row |
| jarvis_learning | 3394 | no heartbeat row |

Dead/old stack (expected, not running): jarvis_cascade, jarvis_briefing, jarvis_congress,
jarvis_trade_advisor, jarvis_intel, jarvis_webull_alerts, jarvis_range_detector, jarvis_capital, jarvis_macro.

## Fixes completed today

1. **jarvis_manual_bets.py** — new module for real-dollar manual Kalshi bet tracking
   - `log_manual_bet()`, `grade_manual_bet()`, `manual_stats()`
   - `dollars` and `entry_spot` columns added to `kalshi_bets` (safe ALTER TABLE)

2. **jarvis_master.py BET/BET15/WIN/LOSS/MANUAL/CLOSE** — all wired through jmb
   - Plain BET now logs `entry_spot` (BTC spot at bet time) — was missing, now fixed and verified

3. **Secrets centralized** — zero hardcoded secrets remain in any .py or .sh file
   - Alpaca keys (11 files), TG_CHAT_ID (13 files + jarvis_health.sh),
     KALSHI_API_KEY + KALSHI_API_KEY_TRADER (3 files) all moved to secrets.json

4. **jarvis_watchdog.py** — added jarvis_learning to BOTS registry; gutted stale brain check;
   fixed hardcoded Alpaca keys

5. **jarvis_beast.py SIGNAL 7** — wired `learned_ticker_rules` from brain table:
   WR ≥70% +1, WR ≤30% −1, minimum 3 trades

6. **jarvis_options_brain.py** — fixed heartbeat: was writing only to brain key
   `options_brain_heartbeat`, never to `bot_heartbeats` table. Watchdog saw it as dead
   since 2026-06-14. Now calls `jarvis_brain.update_bot_heartbeat()` on same 60s cycle.
   Verified: bot_heartbeats row updated to 17:52:11 within one cycle.

## Verified working components

- BET, BET15, WIN, LOSS, MANUAL, CLOSE commands — smoke tested, DB rows confirmed
- `entry_spot` captured on plain BET (row 324: 64095.29, non-NULL)
- `manual_stats()` aggregates only source='manual_user', never mixes with auto P&L
- `secrets.json` → `jarvis_secrets.py` pipeline — grep confirms zero literals remain
- `jarvis_options_brain` heartbeat writing to `bot_heartbeats` — confirmed live
- `jarvis_watchdog` monitors 14 bots including jarvis_learning
- `learned_ticker_rules` feeding jarvis_beast SIGNAL 7

## Known issues

1. **5 running bots have no bot_heartbeats row** — jarvis_premium, jarvis_trader,
   options_grader, btc_ticker, jarvis_learning never call `update_bot_heartbeat()`.
   Watchdog can detect them by process check (pgrep) but not by heartbeat staleness.

2. **kalshi_bets row 322 has entry_spot=NULL** — smoke test row logged before the
   entry_spot fix. Graded LOSS. Harmless but visible in the table.

3. **Auto Kalshi win rate: 18.2% on 101 bets** — low. Not diagnosed today.

4. **Old bot stack rows in bot_heartbeats** — jarvis_cascade, jarvis_briefing, etc.
   stale since June 1–15. Not harmful, just noise in the table.

## Recommended next task

Wire `update_bot_heartbeat()` into the 5 bots that lack it:
`jarvis_premium`, `jarvis_trader`, `options_grader`, `btc_ticker`, `jarvis_learning`.
Pattern is identical to every other bot — one import + one call in the main loop.
This gives watchdog full heartbeat coverage across all 15 running services.
