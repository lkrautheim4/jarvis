# JARVIS — READ THIS FIRST (status as of 2026-06-23 evening)

> If anything here contradicts the live system, THE LIVE SYSTEM WINS. Verify, don't trust.
> This file is only as true as the moment it was written. Re-check with the commands cited.

## CURRENT STATE (one-screen orientation)

**Live & proven:**
- Vision trade logging: `jarvis_vision_capture.py` — screenshot -> Claude vision -> verified
  row in options_trades. Send fills to **@screen_shot_options_bot** (TG_TOKEN_SCREENSHOT),
  reply LOG / CLOSE / CONFIRM / DTE <n>.
  DO NOT use @screenshottrader — TG_TOKEN_TRADER is shared with jarvis_master,
  lenny_trader_bot, and jarvis_trader; they race getUpdates and eat your messages.
  Full details + recovery runbook: SYSTEM_MAP.md.
- Exit/close flow: send exit screenshot to @screen_shot_options_bot, reply CLOSE.
  Bot matches by symbol+strike+expiry, shows P/L preview, requires CONFIRM. Multiple
  opens → numbered list, you pick. Zero matches → clear error. No auto-close on ambiguity.
- Market watcher: `jarvis_market_watcher.py` — SPY QQQ TSLA NVDA AAPL SPCX. Alerts on
  VWAP cross, EMA9 cross, 3x volume spike, prior-day + premarket level breaks. 2-min poll,
  RTH only, 30-min cooldown, alerts via @Jarvis_Stocks_Bot (TG_TOKEN_INTEL).
- Both daemons: flock singleton (cannot duplicate), in start_all.sh (survive reboot).
- Health/alert tooling for vision: vision_health.py (read-only), vision_canary.py (manual
  write-path proof, self-cleaning), vision_alert.py (cron */15, alerts on status change).
- First real trade logged + closed: TSLA $380 put_sell (CREDIT) expired worthless,
  WIN +$345. options_trades id=5224, is_real=1, status='closed'.

**Broken / unresolved (do not pretend these work):**
- options_trades: 46 paper rows (is_real=0) + 1 real closed trade. Dashboard is still
  meaningless — need more real trades before win-rate or expectancy numbers mean anything.
- Trade Journal dashboard: NOT built. Build after ~10+ real closed trades accumulate.
- Token-naming debt: secrets var names do not match actual bots (confirmed via getMe):
    TG_TOKEN_TRADER    -> @screenshottrader  (polled by master, lenny_trader, jarvis_trader)
    TG_TOKEN_SCREENSHOT-> @screen_shot_options_bot  (vision_capture ONLY — correct)
    TG_TOKEN_ADVISOR   -> LennyTraderBot (alias for TRADER's token — same bot)
    TG_TOKEN_INTEL     -> @Jarvis_Stocks_Bot
    TG_TOKEN_LENNY/PRED-> Lenny_predictions_bot
    PASTE_YOUR_NEW_TOKEN = unfilled placeholder, never use.
  Risk: any future script that polls TG_TOKEN_TRADER will starve vision again.
- Market watcher uses Alpaca IEX feed (no paid SIP): VWAP & volume are approximate
  (IEX is ~2-3% of total volume). Good for directional heads-up, not broker-exact levels.
- Market watcher has NO health-check / alert coverage (only vision_capture does).
- jarvis_insider.py: --days CLI flag not implemented (always uses hardcoded lookback).
- btc_regime_grader: deferred, do not build yet.

**Next priorities (in order):**
1. Keep logging real fills through @screen_shot_options_bot every session. Let data accumulate.
2. After ~10+ real closed trades: build Trade Journal dashboard (win rate, P/L by ticker/
   time-of-day, screenshot history) filtered to is_real=1 AND status='closed'.
3. Token cleanup: rename TG_TOKEN_* vars in secrets.json to match actual bot usernames;
   update all callers. Eliminates the queue-starvation risk permanently.
4. Market watcher health coverage: vision_health.py equivalent for jarvis_market_watcher.py.
5. jarvis_insider.py: add --days CLI flag so lookback window is configurable at runtime.

**Key operating principle (unchanged):** "green lights over dead pipes" is the enemy. A
success message is not proof. Every write is verified by readback; every daemon proven by
test, not by "it ran." See SYSTEM_MAP.md for the full vision-pipeline runbook.

**Verify-live commands:**
- what's running:   pgrep -af "python3.*jarvis"
- vision health:    python3 vision_health.py; echo $?
- watcher alive:    pgrep -af "[p]ython3.*jarvis_market_watcher.py"
- trade count:      sqlite3 jarvis_memory.db "SELECT COUNT(*),SUM(is_real) FROM options_trades;"

---

# JARVIS — Autonomous Trading & Prediction Bot Fleet

JARVIS is a personal automated trading system that runs 15 Python bots on a Linux server.
It trades Alpaca paper equities, Kalshi prediction markets, and monitors BTC/crypto signals.
All bots communicate via a shared SQLite database (`jarvis_memory.db`) and report to Telegram.

---

## Architecture

```
jarvis_master.py          — Command hub. Receives Telegram commands, runs the main 90s loop,
                            publishes BTC signal to brain, dispatches manual bet commands.
jarvis_intelligence.py    — News + macro signal engine. Feeds intel to beast and level5.
jarvis_insider.py         — SEC EDGAR Form 4 scanner. Finds code-P (open-market purchase) insider buys.
jarvis_level5.py          — Regime detector. Sets RISK_ON/RISK_OFF in brain.
jarvis_beast.py           — Stock scanner. Scores tickers across 7 signals, sends trade alerts.
jarvis_stocks_v2.py       — Alpaca paper equity executor. Buys/sells based on beast signals.
jarvis_trader.py          — Alpaca paper trade manager. Monitors open positions.
jarvis_premium.py         — Options premium scanner. Feeds options_brain.
jarvis_options_brain.py   — Options paper trade logger. Tracks open/closed options positions.
options_grader.py         — Options exit engine. Checks +50%/-50% thresholds every 5 min.
lenny_predictions.py      — Kalshi BTC prediction bot. Auto-bets YES/NO on hourly KXBTCD markets.
lenny_trader_bot.py       — Kalshi manual trade executor. Separate Kalshi account/key.
jarvis_learning.py        — Win-rate learner. Reads paper_trades.json, writes learned_ticker_rules.
btc_ticker.py             — BTC price publisher. Writes spot price to brain every ~30s.
jarvis_signal_generator.py — Generates composite trading signals for beast/stocks.
jarvis_watchdog.py        — Health monitor. Restarts dead bots, fires Telegram alerts.
```

---

## Shared state: jarvis_memory.db (SQLite)

Key tables:
- `brain` — key/value store for all bot state (regime, BTC price, signals, heartbeats)
- `bot_heartbeats` — per-bot last-seen timestamps; watchdog reads this
- `kalshi_bets` — all Kalshi bets (auto + manual). source='manual_user' for manual bets
- `options_trades` — paper options positions
- `paper_trades` — paper equity trades (also mirrored to paper_trades.json)
- `bot_events` — audit log of bot decisions

---

## Secrets

All credentials live in `secrets.json` (gitignored, never committed).
`jarvis_secrets.py` (also gitignored) loads them and exposes named constants.
Every bot imports from `jarvis_secrets` — no hardcoded keys anywhere in .py or .sh files.

Keys in secrets.json:
- `CLAUDE_API_KEY` — Anthropic API
- `TG_TOKEN_TRADER` — Telegram bot (master, watchdog, beast)
- `TG_TOKEN_INTEL` — Telegram bot (intelligence, options grader)
- `TG_TOKEN_LENNY` — Telegram bot (lenny_predictions)
- `TG_CHAT_ID` — shared destination chat
- `ALPACA_PAPER_KEY` / `ALPACA_PAPER_SECRET` — Alpaca paper trading
- `KALSHI_API_KEY` — Kalshi elections API (lenny_predictions, kalshi_grader)
- `KALSHI_API_KEY_TRADER` — Kalshi trading API (lenny_trader_bot, separate account)

---

## Manual Kalshi bet commands (via Telegram to jarvis_master)

```
BET YES 50        — log $50 YES bet on current hourly KXBTCD strike
BET15 YES 50      — log $50 YES bet on current 15-min KXBTCD strike
WIN               — grade most recent bet as WIN (implicit payout)
WIN 87            — grade as WIN with explicit $87 payout
LOSS              — grade most recent bet as LOSS
MANUAL            — show manual bet stats (W/L, total P&L)
```

P&L math: LOSS = −dollars; WIN explicit = payout − dollars; WIN implicit = dollars × (1−entry)/entry.
Manual bets stored with source='manual_user' in kalshi_bets — never mixed with auto bot P&L.

---

## Watchdog coverage

jarvis_watchdog.py monitors 14 bots by process check and restarts dead ones automatically.
8 bots also write to bot_heartbeats for staleness detection:
jarvis_master, jarvis_intelligence, jarvis_level5, jarvis_stocks_v2, jarvis_beast,
jarvis_options_brain, lenny_predictions, lenny_trader_bot.

5 bots are process-checked only (no heartbeat row yet):
jarvis_premium, jarvis_trader, options_grader, btc_ticker, jarvis_learning.

---

## Key fixes (2026-06-21 session)

- All hardcoded secrets removed from codebase (Alpaca, Telegram, Kalshi, Claude API)
- Manual Kalshi bet tracking wired end-to-end (BET/WIN/LOSS/MANUAL commands)
- entry_spot (BTC price at bet time) now logged for both BET and BET15
- jarvis_options_brain heartbeat fixed — was writing to wrong table; watchdog now sees it live
- jarvis_watchdog now covers jarvis_learning (was missing from registry)
- jarvis_beast SIGNAL 7 wired: learned win-rate from paper trade history feeds ticker scoring

## Key fixes (2026-06-22 session)

- jarvis_insider.py rebuilt from scratch — EFTS search + Form 4 XML parse, authoritative ticker match
  via `<issuerTradingSymbol>`, only code-P (open-market purchase) transactions, dedup by accession number
- jarvis_intelligence.py: `import jarvis_insider` made optional (try/except); degradation is visible —
  WARNING on startup, "INSIDER: OFFLINE (module missing)" in INTEL output, Telegram reply on INSIDER command
- Old inline `fetch_insider_filings()` in jarvis_intelligence.py is dead code (never called) — safe to delete

## Key fixes + improvements (2026-06-23 session)

**close_manual_option readback (jarvis_memory_db.py)**
- Was: committed UPDATE but never verified the row flipped to 'closed' — silent failure possible.
- Fixed: post-commit SELECT on a fresh connection (mirrors _save's cross-connection pattern).
  Raises RuntimeError if status != 'closed'. Tested: P/L formula, double-close guard, cleanup.

**Full entry+exit close flow (jarvis_vision_capture.py)**
- Added CLOSE / CONFIRM / digit-picker flow end-to-end.
- Send exit screenshot → reply CLOSE → bot queries open trades by symbol+strike+expiry,
  shows computed P/L, requires CONFIRM before writing. Multiple matches → numbered list.
  Zero matches → clear error. No auto-close on ambiguity — always explicit confirm.
- All match logic unit-tested against real DB rows before daemonizing.

**Expiry capture on entry (jarvis_vision_capture.py + jarvis_memory_db.py)**
- Was: map_to_db dropped the expiry field; log_manual_option always wrote expiry=NULL.
- Fixed: map_to_db now routes vision-extracted expiry into log_manual_option; entry rows
  carry expiry for reliable exit matching. Null-expiry fallback preserved (bot warns on DTE).

**Token queue contention fix (jarvis_vision_capture.py)**
- Root cause: jarvis_master, lenny_trader_bot, and jarvis_trader all race getUpdates on
  TG_TOKEN_TRADER — screenshots were being consumed silently by one of them.
- Fixed: vision_capture now polls TG_TOKEN_SCREENSHOT (@screen_shot_options_bot exclusively).
  No other bot polls that token. Confirmed live: startup message delivered, first real
  screenshot extracted correctly at confidence 0.85.

**First real trade cycle completed**
- TSLA $380 put_sell (CREDIT), 1 contract, 1 DTE, premium $3.45. Logged via vision bot.
  Expired worthless → closed at exit_premium=0 → WIN +$345. id=5224, is_real=1, verified.
  First real row in options_trades with a confirmed P/L.

**Other (committed in same batch, done earlier sessions)**
- jarvis_memory.py: grade_bet() neutered (raises on call); get_bet_stats() repointed to
  canonical jarvis_memory.db (was reading stale jarvis_brain.db fossil); get_btc_pred_stats()
  added (recomputes accuracy from btc_memory.json per-row, ignores frozen aggregator).
- jarvis_trader.py: stray `from watch_function import watch` before shebang removed.
- start_all.sh: bots restored after audit; jarvis_vision_capture + jarvis_market_watcher
  added to BOTS[].
- New tooling: jarvis_market_watcher.py, vision_health.py, vision_canary.py, vision_alert.py,
  logtrade.py, trading_brain/tripwire.py (integrity cron), SYSTEM_MAP.md.

---

## Starting the fleet

Each bot is launched with nohup and logs to its own .log file:
```bash
nohup python3 /root/jarvis/<bot>.py >> /root/jarvis/<bot>.log 2>&1 &
```

jarvis_watchdog auto-restarts any bot that dies. Start watchdog last.

---

## For Claude Code

- Working directory: /root/jarvis
- DB: /root/jarvis/jarvis_memory.db (live — no local backups as of 2026-06-21)
- Secrets: /root/jarvis/secrets.json and /root/jarvis/jarvis_secrets.py (both gitignored)
- Do not hardcode any credentials — always use `__import__("jarvis_secrets").CONSTANT`
- Do not mix manual (real-dollar) and auto (per-contract) P&L — different scales
- NEXT_SESSION.md and END_OF_DAY.md are checkpoint files — update at end of each session

## Ledger integrity (2026-06-21 night session)
- AUDIT: jarvis_memory.db confirmed sole live writer; jarvis_brain.db + backups/*.db are dead.
- kalshi_bets: 35 phantom-pnl rows set to pnl=NULL; 2 REAL kept. Backup: jarvis_memory.db.bak.*
- grade_bet(won) NEUTERED (raises, 0 callers). Backup: jarvis_memory.py.bak.*
- NEW: trading_brain/tripwire.py READ-ONLY integrity check, cron 5,20,35,50. Telegram only on bad pnl. Verified firing.
- WARNING: old implicit-WIN path documented an entry-price formula but actually wrote pnl=+/-dollars (full stake, entry IGNORED). Fabricated 35/37 rows. NEUTERED. Only trustworthy grader: kalshi_grader.grade_bets().
- KNOWN ISSUE: get_bet_stats() SUMs pnl (now mostly NULL) - audit callers before trusting P&L stats.
- OPEN: CLV uncomputable - close_yes_price logged only on API-settled rows.
