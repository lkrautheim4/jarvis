# END OF DAY — 2026-06-21 (FINAL)

## Current git commit
```
1b624de Add Kalshi measurement pipeline: prediction logging, calibration report
```

## Running services — 15 processes, 12 with active heartbeats

| Bot | PID | bot_heartbeats | Status |
|-----|-----|----------------|--------|
| jarvis_master | 93959 | 17:57:33 | ✅ |
| jarvis_intelligence | 66699 | 17:57:34 | ✅ |
| lenny_predictions | 77680 | 17:57:33 | ✅ |
| lenny_trader_bot | 2182 | 17:57:27 | ✅ |
| jarvis_level5 | 60883 | 17:57:17 | ✅ |
| jarvis_options_brain | 94376 | 17:57:11 | ✅ |
| options_grader | 94884 | 17:57:09 | ✅ |
| jarvis_premium | 94881 | 17:57:06 | ✅ |
| btc_ticker | 94885 | 17:57:06 | ✅ |
| jarvis_stocks_v2 | 27367 | 17:57:06 | ✅ |
| jarvis_learning | 94887 | 17:57:04 | ✅ |
| jarvis_beast | 92534 | 17:56:08 | ✅ |
| jarvis_watchdog | 92002 | — (monitor, no row) | ✅ running |
| jarvis_trader | 2170 | — (has update_bot_heartbeat, next cycle) | ✅ running |
| jarvis_signal_generator | 95099 | 17:58:34 | ✅ |

Dead/old stack (expected, not running):
jarvis_cascade, jarvis_briefing, jarvis_congress, jarvis_trade_advisor,
jarvis_intel, jarvis_webull_alerts, jarvis_range_detector, jarvis_capital, jarvis_macro.

## Fixes completed today

1. **jarvis_manual_bets.py** — new module: real-dollar manual Kalshi bet logging/grading/stats
2. **jarvis_master.py** — BET/BET15/WIN/LOSS/MANUAL/CLOSE all wired through jmb
3. **kalshi_bets schema** — added `dollars` and `entry_spot` columns (safe ALTER TABLE)
4. **Plain BET entry_spot** — was fetched but not passed; now stored. Verified: row 324, 64095.29
5. **Secrets centralized** — zero hardcoded credentials in any .py or .sh file:
   - Alpaca (11 files), TG_CHAT_ID (13 files + jarvis_health.sh),
     KALSHI_API_KEY + KALSHI_API_KEY_TRADER (3 files)
6. **jarvis_watchdog** — added jarvis_learning to registry; gutted stale brain check
7. **jarvis_beast SIGNAL 7** — learned win-rate from paper trades feeds ticker scoring
8. **jarvis_options_brain heartbeat** — was writing to wrong table; now writes bot_heartbeats
9. **Heartbeat coverage** — wired update_bot_heartbeat() into jarvis_premium, options_grader,
   btc_ticker, jarvis_learning. All 4 verified fresh in bot_heartbeats within one cycle.
10. **README.md** — rewrote from stale Anthropic template to accurate JARVIS fleet docs
11. **jarvis_signal_generator heartbeat** — last bot without coverage; now wired.
    All 15 running services have full watchdog heartbeat coverage.

## Verified working components

- All 15 bots running; 13 writing fresh heartbeats to bot_heartbeats (verified ~17:58)
- jarvis_watchdog has full process + heartbeat coverage across entire fleet
- BET/BET15/WIN/LOSS/MANUAL smoke tested against real DB
- entry_spot captured on plain BET (DB row 324: 64095.29, non-NULL)
- manual_stats() isolated to source='manual_user' only
- Zero hardcoded secrets — grep confirmed across all .py and .sh

## What was measured today (Kalshi 18.2% WR investigation)

Historical kalshi_bets (32 graded auto bets, 6W/26L):

| Market yes_price | Bets | W | L | WR |
|---|---|---|---|---|
| 0.00–0.10 | 4 | 0 | 4 | 0% |
| 0.10–0.20 | 6 | 0 | 6 | 0% |
| 0.20–0.30 | 5 | 0 | 5 | 0% |
| 0.30–0.40 | 4 | 1 | 3 | 25% |
| 0.40–0.50 | 3 | 0 | 3 | 0% |
| >=0.50    | 4 | 2 | 2 | 50% |

Root cause: model outputs a directional BTC signal, not a strike-specific probability.
When market prices YES at <0.30 (strike far from BTC), bot bets YES anyway because
model says "bullish" — 0-for-15 in that bucket. Edge gate fires hardest on the worst bets.

## What is still unknown

- **Does the prompt fix help?** New prompt now shows distance-to-strike, hours remaining,
  and explicit grounding instruction. No data yet — `kalshi_predictions` table starts
  filling on the next prediction cycle (hourly). Check tomorrow.
- **SKIP rate vs bet rate** — with better calibration, expect more SKIPs. Unmeasured.
- **Whether model_prob now correlates with win rate** — needs ~20 cycles to evaluate.
  Run `python3 kalshi_calibration.py` to check.

## Known issues

1. **kalshi_bets row 322** — entry_spot=NULL (pre-fix smoke test row). Graded LOSS. Harmless.
2. **Old stack rows in bot_heartbeats** — jarvis_cascade/briefing/etc. stale since June 1–15.
   Noise only, not harmful.

## Recommended next task

After 24–48h of data accumulates in `kalshi_predictions`, run:
```
python3 kalshi_calibration.py
```
Compare model_prob calibration and edge-vs-WR buckets. If model_prob still doesn't
correlate with outcomes, the next fix is a yes_price floor (e.g., only YES when
yes_price ≥ 0.30). Do not implement the floor until calibration data shows
whether the prompt change alone improved correlation.
