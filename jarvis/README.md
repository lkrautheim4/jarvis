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
