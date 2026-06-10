#!/usr/bin/env python3
"""
jarvis_orb.py — Opening Range Breakout (ORB) levels for the morning brief.

Classic 15-minute ORB: the high and low of SPY's first 15 minutes of trading
(9:30–9:45 ET). The morning brief runs pre-market (7am EDT) and on weekends, when
today's session hasn't formed an opening range — so we compute the ORB of the
LAST TRADING DAY (weekday afternoon → today; weekday pre-9:45 → prior trading day;
Saturday/Sunday → Friday).

Read-only: fetches a public Yahoo intraday chart; writes nothing.
"""
import requests
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

ET        = ZoneInfo("America/New_York")
ORB_START = (9, 30)
ORB_END   = (9, 45)   # 15-minute classic ORB → 9:30, 9:35, 9:40 (5m) candles


def last_trading_day(now=None) -> date:
    """Date of the last trading session whose opening range exists.
    - weekday at/after 9:45 ET  → today
    - weekday before 9:45 ET     → prior trading day (today's ORB hasn't formed)
    - Saturday / Sunday          → the preceding Friday
    `now` may be ET-aware, tz-aware (converted to ET), or naive (treated as ET)."""
    if now is None:
        now = datetime.now(ET)
    elif now.tzinfo is not None:
        now = now.astimezone(ET)
    d = now.date()
    past_orb = now.weekday() < 5 and (now.hour, now.minute) >= ORB_END
    if past_orb:
        return d                      # today's session is past the opening range
    d -= timedelta(days=1)            # step back to the previous trading day
    while d.weekday() >= 5:           # skip Sat(5)/Sun(6)
        d -= timedelta(days=1)
    return d


def _fetch_5m_candles(symbol: str):
    """Last 5 trading days of 5-minute candles as [(et_datetime, high, low), ...].
    Empty list on any failure (caller treats as 'ORB unavailable')."""
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"interval": "5m", "range": "5d"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        res = r.json()["chart"]["result"][0]
        ts  = res.get("timestamp", []) or []
        q   = (res.get("indicators", {}).get("quote", [{}]) or [{}])[0]
        highs, lows = q.get("high", []) or [], q.get("low", []) or []
        out = []
        for i, t in enumerate(ts):
            hi = highs[i] if i < len(highs) else None
            lo = lows[i]  if i < len(lows)  else None
            if hi is None or lo is None:
                continue
            out.append((datetime.fromtimestamp(t, ET), float(hi), float(lo)))
        return out
    except Exception:
        return []


def get_orb_levels(symbol: str = "SPY", now=None, fetch=None):
    """ORB high/low for the last trading day's 9:30–9:45 ET window.
    `fetch` is injectable (returns [(et_dt, high, low), ...]) for offline testing.
    Returns {symbol, date, high, low, range, candles} or None if no data."""
    target  = last_trading_day(now)
    candles = (fetch or _fetch_5m_candles)(symbol)
    window  = [(t, hi, lo) for (t, hi, lo) in candles
               if t.date() == target and ORB_START <= (t.hour, t.minute) < ORB_END]
    if not window:
        return None
    high = max(hi for _, hi, _ in window)
    low  = min(lo for _, _, lo in window)
    return {"symbol": symbol, "date": target.isoformat(),
            "high": round(high, 2), "low": round(low, 2),
            "range": round(high - low, 2), "candles": len(window)}


def format_orb_line(symbol: str = "SPY", now=None) -> str:
    """One-line ORB summary for the morning brief."""
    orb = get_orb_levels(symbol, now)
    if not orb:
        return f"{symbol} ORB: unavailable (no intraday data)"
    md = datetime.fromisoformat(orb["date"]).strftime("%a %-m/%-d")
    return (f"{symbol} ORB ({md}, 9:30–9:45 ET): "
            f"H ${orb['high']:,.2f} / L ${orb['low']:,.2f} · range ${orb['range']:,.2f}")


if __name__ == "__main__":
    print(format_orb_line("SPY"))
