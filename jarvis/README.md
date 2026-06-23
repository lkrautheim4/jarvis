# JARVIS — READ THIS FIRST (status as of 2026-06-23)

> If anything here contradicts the live system, THE LIVE SYSTEM WINS. Verify, don't trust.
> This file is only as true as the moment it was written. Re-check with the commands cited.

## CURRENT STATE (one-screen orientation)

**Live & proven (hardened tonight):**
- Vision trade logging: `jarvis_vision_capture.py` — screenshot -> Claude vision -> verified
  row in options_trades. Send fills to bot @screenshottrader, reply LOG/CANCEL/DTE <n>.
  Full details + recovery runbook: see SYSTEM_MAP.md.
- Market watcher: `jarvis_market_watcher.py` — SPY QQQ TSLA NVDA AAPL SPCX. Alerts on
  VWAP cross, EMA9 cross, 3x volume spike, prior-day + premarket level breaks. 2-min poll,
  RTH only, 30-min cooldown, alerts via @Jarvis_Stocks_Bot (TG_TOKEN_INTEL).
- Both daemons: flock singleton (cannot duplicate), in start_all.sh (survive reboot).
- Health/alert tooling for vision: vision_health.py (read-only), vision_canary.py (manual
  write-path proof, self-cleaning), vision_alert.py (cron */15, alerts on status change).

**Broken / unresolved (do not pretend these work):**
- options_trades has 46 rows, ALL paper, ALL is_real=0, last real write NONE.
  Yesterday's real SPCX put was never logged (bot lied "logged:5213", rolled back).
  => Any analytics/dashboard over this table is meaningless until real trades accumulate.
- Trade Journal dashboard: NOT built. Blocked on real trade data (see above). Build it
  only after logging real fills through the vision bot for several sessions.
- Token-naming debt: secrets var names DO NOT match their bots. Confirmed via getMe:
  TG_TOKEN_TRADER->screenshottrader, TG_TOKEN_ADVISOR->LennyTraderBot(alias),
  TG_TOKEN_INTEL->Jarvis_Stocks_Bot, TG_TOKEN_LENNY & TG_TOKEN_PRED->Lenny_predictions_bot,
  TG_TOKEN_SCREENSHOT->screen_shot_options_bot (a DIFFERENT bot than vision uses).
  PASTE_YOUR_NEW_TOKEN = unfilled placeholder, do not use. (Details: SYSTEM_MAP.md §10)
- Market watcher uses Alpaca IEX feed (no paid SIP): VWAP & volume are APPROXIMATE
  (IEX is ~2-3% of total volume). Good for directional heads-up, NOT broker-exact levels.
- Market watcher has NO health-check / alert coverage yet (only vision_capture does).
- README architecture list below may include bots that aren't running (e.g. jarvis_insider.py
  is listed but was NOT in start_all BOTS[] / not in ps). Verify with: pgrep -af python3

**Next priorities (in order):**
1. Use the system: log real fills via screenshottrader; let the watcher run during RTH.
2. After real trades exist: build Trade Journal dashboard (win rate, P/L, ticker & time-of-day
   performance, screenshot history) filtered to is_real=1.
3. Entry+exit matching + automatic P/L (specced, deferred): match an exit screenshot to its
   open by symbol+strike+expiry+right, confirm-match flow, UPDATE row with exit_premium/
   exit_ts/realized_pnl, status='closed'. P/L sign from direction (DEBIT vs CREDIT).
4. Cleanup: rename token vars to match bots; give market_watcher health coverage.

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
