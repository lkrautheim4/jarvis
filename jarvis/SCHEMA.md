# options_trades — Canonical Column Spec
_Last updated 2026-06-22. Plan A(a): manual copilot path consolidated. Auto-writers NOT yet migrated._

## Purpose
options_trades accreted 3 generations of overlapping columns across sessions.
This doc designates the CANONICAL column for each concept. The manual copilot
path (log_manual_option / close_manual_option) writes ONLY canonical columns.
Duplicate/legacy columns are preserved for history but must not be read by new code.

## Canonical columns (manual copilot writes these)
| Concept            | CANONICAL col   | Type    | Notes |
|--------------------|-----------------|---------|-------|
| ticker             | symbol          | TEXT    | not `ticker` (legacy, half-filled) |
| setup / strategy   | strategy        | TEXT    | e.g. long_call, bull_put_spread |
| profit direction   | direction       | TEXT    | 'DEBIT' or 'CREDIT' — REQUIRED, drives P&L sign |
| strike             | strike          | REAL    | primary leg strike |
| DTE at entry       | dte_at_entry    | INTEGER | not `dte` (legacy) |
| entry timestamp    | entry_ts        | TEXT    | ISO-8601 **ET** (America/New_York) |
| exit timestamp     | exit_ts         | TEXT    | ISO-8601 **ET** |
| entry price/prem   | premium         | REAL    | per-contract; debit paid OR credit received |
| exit price/prem    | exit_premium    | REAL    | per-contract at close |
| contracts          | contracts       | INTEGER | NEW 2026-06-22 |
| realized P&L ($)   | pnl             | REAL    | computed by grader |
| outcome            | result          | TEXT    | WIN / LOSS / SCRATCH |
| thesis             | notes           | TEXT    | free text rationale |
| market regime      | regime          | TEXT    | e.g. BULLISH/BEARISH/CHOP |
| confidence         | score           | REAL    | 0-1 or 0-100, user-supplied |
| screenshot path    | screenshot      | TEXT    | NEW 2026-06-22, optional |
| is real trade      | is_real         | INTEGER | 1 = real money. Analytics filter on this |
| status             | status          | TEXT    | 'open' / 'closed' |
| source tag         | source          | TEXT    | 'manual_copilot' for this path |

## P&L grading (direction-aware)
gross = (exit_premium - premium) * contracts * 100
pnl   = gross           if direction == 'DEBIT'
pnl   = -gross          if direction == 'CREDIT'   # short premium: profit when premium falls
result = WIN if pnl>0 else LOSS if pnl<0 else SCRATCH

## Analytics = LIVE QUERIES ONLY
Never store rollups (every stored aggregator in this system has frozen and lied).
Always filter: WHERE is_real=1 AND status='closed' AND pnl IS NOT NULL
- setup:       GROUP BY strategy
- time-of-day: GROUP BY substr(entry_ts,12,2)  (ET hour)
- regime:      GROUP BY regime
expectancy = AVG(pnl) per bucket.

## LEGACY columns — DO NOT read in new code (preserved for history)
ticker, dte, ts (entry), premium-as-only-price, side, direction(old use),
entry_price/exit_price (uniformly 0.0 — NEVER use), entry_date, exit_date,
closed_at, exit_premium(legacy rows), iv_ratio, prob, webull_id.
The 46 pre-2026-06-22 rows use legacy cols + is_real=0 (paper). Excluded from analytics.
