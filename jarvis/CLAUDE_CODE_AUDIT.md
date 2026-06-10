# JARVIS — Claude Code Audit & Change Log

**Date:** 2026-06-03
**Scope:** paper-trade data integrity, options grading, and Telegram duplicate-send
hardening across the bot fleet.

---

## 1. What changed today

### Data fixes
- **`paper_trades.json`** — corrected 6 phantom AMD expiries `2026-06-10` → `2026-06-12`
  (AMD has no Wed weekly on yfinance, so the grader silently failed to price them).
  Backed up to `backups/paper_trades.json.bak.20260603_201721`. All 40 open trades now
  fetch a live premium (0 untracked).

### New files
- **`paper_trades_store.py`** — the single lock-protected gateway to `paper_trades.json`:
  `flock` (sidecar `paper_trades.json.lock`) held across each read-modify-write + atomic
  write (`tempfile`+`os.replace`). API: `read()`, `update(mutator)`, `open_exposure()`,
  `would_exceed_cap()`, `is_junk_contract()`. Caps: **3 open / $4,000 per ticker**;
  junk filter: **premium ≥ $0.15, strike within 25% of spot**.
- **`CLAUDE_CODE_AUDIT.md`** — this file.

### Phantom-expiry root cause (issue from the AMD `06-10` pile)
- **`jarvis_options_brain.py`** — replaced the arithmetic expiry fallback
  (`today + dte`, which fabricated invalid dates) with **`_valid_expiry()`** that snaps to
  a real `yf.Ticker(t).options` listing.
- **`jarvis_options_scanner.py`** — added the same `valid_expiry()` guard at its append.

### #1 Per-ticker exposure cap  +  #2 Concurrency / file locking
- **`paper_trades_store.py`** (above) implements both.
- Refactored all 4 writers to go through the store (no more raw `json.load`/`dump`):
  - **`options_grader.py`** — 3-phase `run_grader`: yfinance work on a snapshot *outside*
    the lock, then an identity-matched (`_trade_key`) commit *inside* the lock so concurrent
    appends are never lost; Telegram/log after.
  - **`jarvis_options_scanner.py`**, **`jarvis_options_brain.py`** — appends go through
    `store.update()` with the dup-check + `would_exceed_cap()` enforced atomically; on a
    blocked signal: `CAP:` log + one-time/day deduped Telegram notice.
  - **`grade_paper_trades.py`** — see #3 (deprecated).
- Existing AMD positions are **grandfathered**; the cap only stops *new* pile-ups.

### #3 Consolidated trade-closing (single source of truth)
- **`options_grader.py`** is now the ONLY closer (live-premium ±50% / DTE≤2 / expiry).
- **`grade_paper_trades.py`** → deprecated read-only no-op (was closing on divergent
  *intrinsic value*).
- **`jarvis_options_scanner.py`** — `grade_closed_trades()` replaced by
  **`feed_learning_from_closed()`**: feeds grader-closed trades into the
  `update_signal_weights` learning loop exactly once (via a `learned` flag) and never
  closes/re-grades. (Brain's internal `grade_closed_trades(brain)` grades its *own* ledger
  and was left alone.)

### #4 Junk-contract filter
- **`store.is_junk_contract()`** enforced in scanner + brain — drops deep-OTM /
  near-worthless setups (e.g. a $250 AMD put @ $0.14 on a ~$530 stock) with a `JUNK:` log.

### Telegram duplicate-send hardening
Audited all 29 loop bots. Most were already safe. Gates added:
- **`jarvis_intelligence.py`** — `filter_new_alerts()` + `alerted_signatures` (persisted in
  `jarvis_intel.json`) across 7 streams (options/insider/earnings/darkpool/congress/econ)
  + once-per-day-per-direction crypto Fear&Greed gate.
- **`jarvis_watchdog.py`** — `tg_throttled` (disk-persisted `jarvis_watchdog_alerts.json`):
  bot-DOWN/restart 30 min, RAM/brain/system-health 1 hr.
- **`jarvis_master.py`** — `tg_throttled` (in-memory) on Kalshi DANGER/SAFE (was every 90s).
- **`jarvis_briefing.py`** — `tg_throttled` on the dead-bot health alert; morning-brief
  flag now also written at the call site (closes a re-send window).
- **`jarvis_options_scanner.py`** — `already_alerted_today()` (persisted), once per setup/day.
- **`jarvis_options.py`** — `already_traded_today()` (persisted `jarvis_options_sent.json`),
  once per symbol+direction+strike+expiry/day (was re-entering the same contract every 30m).
- **`jarvis_options_coach.py`** — `already_coached_today()` (persisted), once per
  ticker+strategy/day.
- **`jarvis_alpha.py`** — `tg_throttled` on BUY/SELL **FAILED** alerts (re-fired every poll).

### Two non-duplicate bugs found & fixed along the way
- **`jarvis_futures.py`** — `should_buy()` had a missing-`elif`/indentation bug: the second
  buy branch was **dead code** and `approved` was undefined for `rsi≥35`
  (`UnboundLocalError` → the bot effectively **could not buy**). Both branches now reachable.
- **`jarvis_premium.py`** — the 50%-profit close re-issued the **buy-back order** and
  **double-counted wins/P&L** every 30 min (no removal/guard). Added a per-symbol 2 hr guard,
  marked only on a successful submit (failed closes still retry).

### Bots restarted on new code today (all verified single-instance)
`jarvis_intelligence`, `jarvis_options_brain`, `jarvis_master`, `jarvis_briefing`,
`jarvis_watchdog`, `options_grader`.
The 5 stopped bots above (`jarvis_futures/alpha/premium/options/options_coach`) were edited
but NOT restarted — fixes apply on next launch.

### Verification performed (all manual this session)
- Concurrency: 10 procs × 20 appends → 200/200, no lost updates, JSON valid.
- Cap: cost + count caps fire correctly; tickers independent.
- Grader merge: close applies AND an out-of-band append survives.
- Grader thresholds: +50%/−50%/DTE≤2/expiry all fire at the boundary; +49%/−49% hold.
- Junk filter, learning-feed idempotency, futures branches, dedup gates — all unit-tested.

---

## 2. What's still broken / not done

- **AMD over-concentration is LIVE and unmitigated.** AMD is still **~91% of the book
  ($47k / 20 contracts), ~−16% unrealized, 4 positions near the −50% auto-stop.** The new
  cap only blocks *future* pile-ups; the existing risk remains until those positions exit.
- **Systemic timezone bug.** `edt_hour = (datetime.utcnow().hour - 4) % 24` is hardcoded to
  **EDT** in `options_grader.py`, `jarvis_master.py`, `jarvis_intelligence.py`,
  `jarvis_options_brain.py`. Correct now (summer) but **off by one hour in winter (EST)** —
  the grader's market-hours gate will mis-fire. Also uses deprecated `datetime.utcnow()`.
- **Junk-strike root cause not fully traced.** #4 added a guard at the *logging* point, but
  whatever selection path produced a $250 AMD / $145 AAPL strike (bypassing
  `find_best_contract`'s moneyness window) was not pinned down at the source.
- **The 5 stopped-bot fixes are unverified live** (bots not running).
- **In-memory throttles** (`jarvis_master`, `jarvis_alpha`, `jarvis_premium`) reset on
  restart — acceptable (one extra alert after a restart) but not disk-persisted.
- **No automated tests / CI.** Every fix today was verified by hand. This fleet has many
  long-running stateful bots and just surfaced multiple *silent* bugs (futures couldn't buy;
  premium double-counted P&L) — regressions are easy to reintroduce.
- **Book is 100% puts** (directional bearish) — a strategy choice to review, not a bug.

---

## 3. Top 3 to fix next session (ranked by impact)

1. **Actively de-risk the AMD concentration.** This is the single largest *live* financial
   exposure ($47k, 91% of the book, already −16%, 4 near auto-stop). Decide: trim a portion,
   hedge, or tighten the auto-stop for that cluster. The cap stopped the bleeding from
   getting worse — it didn't undo what's already on.

2. **Fix the systemic timezone / market-hours handling.** Replace the hardcoded
   `datetime.utcnow()-4` with proper US/Eastern tz-aware logic (e.g. `zoneinfo`) in the
   grader, master, intelligence, and brain. Left unfixed, the grader silently mis-grades for
   ~4 months every winter. Low effort, broad correctness win.

3. **Add a lightweight smoke-test / health harness.** A script that: imports every bot
   (catches dead-code / NameError bugs like the futures one), validates `paper_trades.json`
   schema + expiry validity, asserts single-instance per bot, and exercises
   `paper_trades_store` locking. Given how many silent bugs surfaced today, this is the
   highest-leverage way to keep them from recurring.

---

## Reference — key artifacts created
- `paper_trades_store.py` (lock + cap + junk filter — all writers MUST use it)
- State files: `jarvis_watchdog_alerts.json`, `jarvis_scanner_alerts.json`,
  `jarvis_options_sent.json`, `jarvis_options_coach_sent.json`,
  `alerted_signatures` key in `jarvis_intel.json`, `paper_trades.json.lock`
- Memory notes: `jarvis-options-expiry-validity`, `jarvis-telegram-dedup-status`,
  `jarvis-paper-trades-store`
