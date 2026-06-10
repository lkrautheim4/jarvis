#!/usr/bin/env python3
"""
JARVIS Dip Buy Signal Scanner
==============================
Stage 1: Every 5 min — scan for 3-5% intraday dips from session high
Stage 2: Every 1 min — confirm candidate (RSI still <40, price not recovered)
On confirm: flag BUY_CALL, Kelly-size position, fire Telegram + log to SQLite
"""

import time
import sqlite3
import threading
import requests
import logging
from datetime import datetime, date
from zoneinfo import ZoneInfo

import yfinance as yf
import pandas as pd
import numpy as np

# ─── CONFIG ───────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN  = "YOUR_BOT_TOKEN"          # replace or load from env
TELEGRAM_CHAT   = "7534553840"
DB_PATH         = "/root/jarvis/jarvis_memory.db"
LOG_PATH        = "/root/jarvis/jarvis_dip_scanner.log"
CAPITAL         = 5000                      # trading capital for Kelly sizing
MAX_POSITION    = 500                       # max $ per trade
KELLY_FRACTION  = 0.25                      # fractional Kelly (conservative)
EDT             = ZoneInfo("America/New_York")

# ─── TICKER UNIVERSE ─────────────────────────────────────────────────────────
# Core 16 from options brain + high-beta dip candidates ideal for this strategy
TICKERS = [
    # Options brain universe (16)
    "AAPL", "MSFT", "NVDA", "AMD", "TSLA",
    "META", "AMZN", "GOOGL", "SPY", "QQQ",
    "F", "BAC", "JPM", "XOM", "PLTR", "SOFI",
    # Best dip-buy additions (high beta, liquid options, mean-reverting)
    "MSTR", "COIN", "RIVN", "MARA",         # crypto-correlated / high beta
    "ARKK", "SOXS",                          # sector momentum plays
    "IWM", "GLD",                            # macro regime reads
]

# ─── DIP PARAMETERS ──────────────────────────────────────────────────────────
DIP_MIN          = 0.03    # 3% drop from session high
DIP_MAX          = 0.05    # 5% drop (deeper = likely fundamental, skip)
RSI_THRESHOLD    = 40      # RSI must be below this
RSI_PERIOD       = 14
CONFIRM_WINDOW   = 60      # seconds between confirmation checks
SCAN_INTERVAL    = 300     # seconds between full scans (5 min)

# Fundamental news catalysts — if any of these tags present, SKIP
SKIP_CATALYSTS   = {"earnings", "fda", "legal", "merger", "halt", "guidance"}

# Regimes that allow dip buys
VALID_REGIMES    = {"RISK_ON", "TRENDING"}

# ─── STATE ───────────────────────────────────────────────────────────────────
candidates: dict = {}      # ticker → {flagged_at, dip_pct, session_high, price}
fired_today: set = set()   # tickers already signaled today (one per day)

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [DIP_SCANNER] %(message)s"
)
log = logging.getLogger("dip_scanner")

# ─── DB SETUP ────────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dip_signals (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT,
            ticker       TEXT,
            signal       TEXT,
            dip_pct      REAL,
            session_high REAL,
            current_price REAL,
            rsi          REAL,
            regime       TEXT,
            kelly_size   REAL,
            status       TEXT DEFAULT 'PENDING'
        )
    """)
    conn.commit()
    conn.close()
    log.info("DB initialized")

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def get_regime() -> str:
    """Read current regime from jarvis_memory.db brain table."""
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT value FROM brain WHERE key = 'market_regime' ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            return row[0].upper()
    except Exception as e:
        log.warning(f"Regime read failed: {e}")
    return "UNKNOWN"

def compute_rsi(closes: pd.Series, period: int = RSI_PERIOD) -> float:
    delta = closes.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not rsi.empty else 50.0

def check_fundamental_news(ticker: str) -> bool:
    """
    Lightweight check: look at recent catalyst tags in intel_signals table.
    Returns True if a skip catalyst is present (meaning DO NOT trade).
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("""
            SELECT catalyst_tag FROM intel_signals
            WHERE ticker = ? AND DATE(timestamp) = ?
        """, (ticker, str(date.today()))).fetchall()
        conn.close()
        tags = {r[0].lower() for r in rows if r[0]}
        return bool(tags & SKIP_CATALYSTS)
    except Exception:
        return False   # table may not exist yet — assume clean

def kelly_size(win_rate: float = 0.62, avg_win: float = 1.8, avg_loss: float = 1.0) -> float:
    """
    Fractional Kelly position size in dollars.
    Default win_rate/avg_win based on dip reversal backtests (adjust as data grows).
    """
    edge = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
    kelly = edge / avg_win if avg_win > 0 else 0
    raw   = CAPITAL * KELLY_FRACTION * kelly
    return round(min(max(raw, 25), MAX_POSITION), 2)

def send_telegram(msg: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT, "text": msg, "parse_mode": "HTML"}, timeout=5)
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")

def log_to_db(ticker, dip_pct, session_high, price, rsi, regime, kelly_size_val):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            INSERT INTO dip_signals
            (timestamp, ticker, signal, dip_pct, session_high, current_price, rsi, regime, kelly_size)
            VALUES (?, ?, 'BUY_CALL', ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(EDT).isoformat(),
            ticker, round(dip_pct * 100, 2),
            round(session_high, 2), round(price, 2),
            round(rsi, 1), regime, kelly_size_val
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"DB log failed: {e}")

# ─── MARKET HOURS ─────────────────────────────────────────────────────────────

def is_market_hours() -> bool:
    now = datetime.now(EDT)
    if now.weekday() >= 5:
        return False
    return (now.hour == 9 and now.minute >= 30) or (10 <= now.hour < 16)

# ─── STAGE 1: 5-MIN SCAN ─────────────────────────────────────────────────────

def scan_dips():
    global candidates, fired_today

    # Reset fired_today at market open
    now = datetime.now(EDT)
    if now.hour == 9 and now.minute < 35:
        fired_today = set()

    regime = get_regime()
    if regime not in VALID_REGIMES:
        log.info(f"SKIP SCAN — regime={regime}")
        return

    log.info(f"Running dip scan | regime={regime} | {len(TICKERS)} tickers")

    for ticker in TICKERS:
        if ticker in fired_today:
            continue
        try:
            df = yf.download(ticker, period="1d", interval="5m", progress=False, auto_adjust=True)
            if df is None or len(df) < 20:
                continue

            session_high  = float(df["High"].max())
            current_price = float(df["Close"].iloc[-1])
            dip_pct       = (session_high - current_price) / session_high

            if not (DIP_MIN <= dip_pct <= DIP_MAX):
                continue

            # Fundamental news check
            if check_fundamental_news(ticker):
                log.info(f"{ticker} SKIP — fundamental catalyst present")
                continue

            # RSI check
            rsi = compute_rsi(df["Close"])
            if rsi >= RSI_THRESHOLD:
                log.info(f"{ticker} SKIP — RSI {rsi:.1f} not oversold")
                continue

            # Flag as candidate
            candidates[ticker] = {
                "flagged_at":   time.time(),
                "dip_pct":      dip_pct,
                "session_high": session_high,
                "price":        current_price,
                "rsi":          rsi,
                "regime":       regime,
            }
            log.info(f"CANDIDATE: {ticker} | dip={dip_pct*100:.1f}% | RSI={rsi:.1f} | regime={regime}")

        except Exception as e:
            log.error(f"Scan error {ticker}: {e}")

# ─── STAGE 2: 1-MIN CONFIRMATION ─────────────────────────────────────────────

def confirm_candidates():
    regime = get_regime()
    if regime not in VALID_REGIMES:
        candidates.clear()
        return

    to_remove = []

    for ticker, c in list(candidates.items()):
        if ticker in fired_today:
            to_remove.append(ticker)
            continue

        # Expire candidates older than 15 min without confirmation
        if time.time() - c["flagged_at"] > 900:
            log.info(f"{ticker} candidate EXPIRED")
            to_remove.append(ticker)
            continue

        try:
            df = yf.download(ticker, period="1d", interval="1m", progress=False, auto_adjust=True)
            if df is None or len(df) < 5:
                continue

            current_price = float(df["Close"].iloc[-1])
            rsi           = compute_rsi(df["Close"])
            recovery_pct  = (current_price - c["price"]) / c["price"]

            # If price recovered more than 1.5%, cancel candidate
            if recovery_pct > 0.015:
                log.info(f"{ticker} DIP RECOVERED — cancelling candidate")
                to_remove.append(ticker)
                continue

            # Confirm: RSI still oversold
            if rsi >= RSI_THRESHOLD:
                log.info(f"{ticker} RSI normalized to {rsi:.1f} — cancelling")
                to_remove.append(ticker)
                continue

            # ✅ CONFIRMED — fire signal
            size = kelly_size()
            fire_signal(ticker, c["dip_pct"], c["session_high"], current_price, rsi, regime, size)
            fired_today.add(ticker)
            to_remove.append(ticker)

        except Exception as e:
            log.error(f"Confirm error {ticker}: {e}")

    for t in to_remove:
        candidates.pop(t, None)

def fire_signal(ticker, dip_pct, session_high, price, rsi, regime, size):
    msg = (
        f"🎯 <b>JARVIS DIP BUY SIGNAL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Ticker:       <b>{ticker}</b>\n"
        f"Signal:       <b>BUY_CALL</b>\n"
        f"Dip:          {dip_pct*100:.1f}% from session high\n"
        f"Session High: ${session_high:.2f}\n"
        f"Current:      ${price:.2f}\n"
        f"RSI (1m):     {rsi:.1f}\n"
        f"Regime:       {regime}\n"
        f"Kelly Size:   <b>${size}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ No fundamental catalyst detected\n"
        f"📅 {datetime.now(EDT).strftime('%I:%M %p EDT')}"
    )
    send_telegram(msg)
    log_to_db(ticker, dip_pct, session_high, price, rsi, regime, size)
    log.info(f"SIGNAL FIRED: BUY_CALL {ticker} | size=${size}")

# ─── MAIN LOOP ────────────────────────────────────────────────────────────────

def main():
    init_db()
    log.info("Dip scanner started")
    print("[JARVIS DIP SCANNER] Running. Logs → jarvis_dip_scanner.log")

    last_scan = 0

    while True:
        try:
            if is_market_hours():
                now = time.time()

                # Stage 1: every 5 min
                if now - last_scan >= SCAN_INTERVAL:
                    scan_dips()
                    last_scan = now

                # Stage 2: every 1 min (only if candidates exist)
                if candidates:
                    confirm_candidates()

            time.sleep(CONFIRM_WINDOW)   # 60s base loop

        except KeyboardInterrupt:
            log.info("Dip scanner stopped")
            print("\n[STOPPED]")
            break
        except Exception as e:
            log.error(f"Main loop error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
