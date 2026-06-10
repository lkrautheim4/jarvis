"""
JARVIS HELP MENU — paste this into your bot's /help or HELP handler
Replace your existing help_text string with this one.
"""

HELP_TEXT = """
🤖 JARVIS COMMAND REFERENCE
══════════════════════════════

📊 MARKET INTELLIGENCE
  BTC          → BTC price, RSI, momentum
  PRED         → BTC 4hr prediction
  MACRO        → macro regime + VIX + yield curve
  STOCKS       → watchlist snapshot
  EARNINGS     → upcoming earnings alerts
  INTEL        → insider flow + dark pool
  INSIDER      → insider buy/sell activity
  CONGRESS     → congress trade tracker

🎯 KALSHI BETTING
  KALSHI       → open positions + edge scan
  BET YES [market] → place YES bet
  BET NO [market]  → place NO bet
  WIN          → log Kalshi win
  LOSS         → log Kalshi loss
  VOID         → void last bet
  PATTERNS     → Kalshi edge patterns

📈 OPTIONS TRADES (NEW)
  TRADE WIN NVDA CALL 225 6/5 entry 6/2 exit +190
  TRADE LOSS AAPL PUT 180 6/20 entry 5/30 exit -85
  TRADES       → show last 10 trades
  TRADE STATS  → win rate + P&L summary

  Format: TRADE [WIN/LOSS] [TICKER] [CALL/PUT] [STRIKE] [EXPIRY M/D]
          entry [M/D] exit [M/D] [+/-AMOUNT]

  Examples:
    TRADE WIN NVDA CALL 225 6/5 entry 6/2 exit +190
    TRADE LOSS F PUT 12 6/20 entry 6/1 exit -45
    TRADE WIN BAC CALL 45 7/18 +320

💼 POSITIONS & EXECUTION
  POSITIONS    → open positions (Alpaca + Kalshi)
  CLOSEPOS     → close all Alpaca positions
  CAPITAL      → available capital summary
  BEAST        → run Beast scalper
  WATCH [TICKER] → add price alert

🧠 SYSTEM & AI
  BRIEF        → morning intelligence brief
  RESULT       → grade last prediction
  IMPROVE      → trigger self-improvement cycle
  REPORT       → full performance report
  STATUS       → bot health check

══════════════════════════════
Quiet hours: 10pm–8am EDT (weekdays)
Options brain target: CSP on F or BAC
Kalshi bankroll: $500 | Max bet: $25
"""

# ── Integration instructions ─────────────────────────────────────
# In your main bot file (jarvis_bot.py or similar), find your
# existing HELP handler and replace the help text, OR add this:
#
# from jarvis_help import HELP_TEXT
#
# Then in your message handler:
# if text in ["HELP", "/help", "?"]:
#     send_telegram(HELP_TEXT)
#     return
