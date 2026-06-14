import sqlite3, time
from datetime import datetime, timezone

DB = "/root/jarvis/jarvis_memory.db"
MAX_QUOTE_AGE_SEC = 300          # 5 min during market hours
MAX_CASH_PCT = 0.15              # max 15% of account per trade
SHORT_PREMIUM_STRATEGIES = {"SELL_PUT", "SELL_CALL", "CSP", "COVERED_CALL", "CREDIT_SPREAD"}
BULLISH_STRATEGIES = {"SELL_PUT", "CSP", "BUY_CALL", "CREDIT_PUT_SPREAD"}

def get_market_mode():
    con = sqlite3.connect(DB)
    row = con.execute("SELECT value FROM brain WHERE key='market_mode'").fetchone()
    con.close()
    return row[0] if row else "UNKNOWN"

def gate_signal(sig):
    """sig dict: strategy, ticker, quote_price, quote_ts (epoch),
    iv_ratio, cash_required, account_value, week_change_pct"""
    reasons = []

    age = time.time() - sig.get("quote_ts", time.time())
    if age > MAX_QUOTE_AGE_SEC:
        reasons.append(f"STALE QUOTE: {age/60:.0f} min old")

    mode = get_market_mode()
    if mode == "PROTECTION" and sig["strategy"] in BULLISH_STRATEGIES:
        reasons.append(f"MODE VIOLATION: {sig['strategy']} blocked in PROTECTION")

    if sig["strategy"] in SHORT_PREMIUM_STRATEGIES and sig.get("iv_ratio", 0) < 130:
        reasons.append(f"IV RATIO {sig.get('iv_ratio',0):.0f} < 130 — no premium-selling edge")

    # Don't buy premium (calls or puts) when IV is already deflated — poor reward/risk
    BUY_STRATEGIES = {"BUY_CALL", "call_buy", "BUY_PUT", "put_buy"}
    if sig["strategy"] in BUY_STRATEGIES and sig.get("iv_ratio", 0) > 0 and sig.get("iv_ratio", 0) < 70:
        reasons.append(f"IV RATIO {sig.get('iv_ratio',0):.0f} < 70 — IV too deflated to buy premium")

    # Falling knife: block put-selling AND call-buying on stocks down hard on the week
    if sig["strategy"] in {"SELL_PUT", "CSP"} and sig.get("week_change_pct", 0) <= -5:
        reasons.append(f"DOWNTREND: {sig['week_change_pct']}% on week — falling knife, no put-selling")
    if sig["strategy"] in {"BUY_CALL", "call_buy"} and sig.get("week_change_pct", 0) <= -5:
        reasons.append(f"DOWNTREND: {sig['week_change_pct']}% on week — no call-buying into weakness")

    if sig.get("cash_required", 0) > sig.get("account_value", 0) * MAX_CASH_PCT:
        reasons.append(f"OVERSIZED: ${sig.get('cash_required',0):,} > {MAX_CASH_PCT:.0%} of account")

    if reasons:
        con = sqlite3.connect(DB)
        con.execute("INSERT INTO events (ts, source, detail) VALUES (?,?,?)",
                    (datetime.now(timezone.utc).isoformat(), "options_gate",
                     f"BLOCKED {sig['ticker']} {sig['strategy']}: " + " | ".join(reasons)))
        con.commit()
        con.close()
        return False, reasons
    return True, []
