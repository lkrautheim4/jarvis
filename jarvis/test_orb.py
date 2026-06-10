#!/usr/bin/env python3
"""Test jarvis_orb — proves the weekend/last-trading-day logic, especially that a
mocked SATURDAY computes the ORB from FRIDAY's data. Fully offline (fetch mocked)."""
from datetime import datetime
from zoneinfo import ZoneInfo
import jarvis_orb as O

ET = ZoneInfo("America/New_York")
PASS, FAIL = "✅ PASS", "❌ FAIL"
ok = []
def chk(name, cond, detail=""):
    ok.append(cond)
    print(f"{PASS if cond else FAIL}  {name}{(' — ' + detail) if detail else ''}")

# Reference week: Fri 2026-06-12 is the last trading day before the weekend.
FRI = datetime(2026, 6, 12).date()
MON = datetime(2026, 6, 15).date()

# ── last_trading_day across mocked dates (naive = ET wall-clock) ──────────────
print("=" * 60); print("last_trading_day()"); print("=" * 60)
chk("Saturday 10:00 → Friday",        O.last_trading_day(datetime(2026, 6, 13, 10, 0)) == FRI,
    str(O.last_trading_day(datetime(2026, 6, 13, 10, 0))))
chk("Sunday 10:00 → Friday",          O.last_trading_day(datetime(2026, 6, 14, 10, 0)) == FRI)
chk("Monday 7am (pre-open) → Friday", O.last_trading_day(datetime(2026, 6, 15, 7, 0)) == FRI)
chk("Tuesday 7am → Monday",           O.last_trading_day(datetime(2026, 6, 16, 7, 0)) == MON)
chk("Wednesday 2pm (post-ORB) → Wed", O.last_trading_day(datetime(2026, 6, 17, 14, 0)) == datetime(2026, 6, 17).date())
chk("Friday 9:44 (1m pre-ORB) → Thu", O.last_trading_day(datetime(2026, 6, 12, 9, 44)) == datetime(2026, 6, 11).date())
chk("Friday 9:45 (ORB formed) → Fri", O.last_trading_day(datetime(2026, 6, 12, 9, 45)) == FRI)

# ── get_orb_levels with a MOCKED fetcher (deterministic, offline) ─────────────
print("=" * 60); print("get_orb_levels() — Saturday must use Friday's window"); print("=" * 60)
def mock_fetch(symbol):
    c = []
    def add(day, hh, mm, hi, lo): c.append((datetime(2026, 6, day, hh, mm, tzinfo=ET), hi, lo))
    # Thursday 6/11 — must be IGNORED (wrong day)
    add(11, 9, 30, 500, 498); add(11, 9, 35, 503, 499); add(11, 9, 40, 505, 500)
    # Friday 6/12 ORB window 9:30–9:45 — must be USED. high=612, low=605
    add(12, 9, 30, 610, 606); add(12, 9, 35, 612, 607); add(12, 9, 40, 611, 605)
    add(12, 9, 45, 620, 604)  # 9:45 candle — OUTSIDE 9:30–9:45 (must be EXCLUDED)
    add(12, 9, 25, 999, 100)  # pre-open 9:25 — must be EXCLUDED
    return c

sat = datetime(2026, 6, 13, 10, 0)
orb = O.get_orb_levels("SPY", now=sat, fetch=mock_fetch)
print("   result:", orb)
chk("Saturday ORB uses Friday's date", bool(orb) and orb["date"] == FRI.isoformat())
chk("high = 612 (max of Fri 9:30–9:40)", bool(orb) and orb["high"] == 612)
chk("low = 605 (excludes 9:45's 604 + 9:25's 100)", bool(orb) and orb["low"] == 605)
chk("exactly 3 candles in window", bool(orb) and orb["candles"] == 3, str(orb and orb["candles"]))

print()
print("ALL PASS ✅" if all(ok) else "SOME FAILED ❌")
import sys; sys.exit(0 if all(ok) else 1)
