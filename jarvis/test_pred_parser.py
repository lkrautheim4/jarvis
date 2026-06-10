#!/usr/bin/env python3
"""Verify the WATCH -> PRED <hour> flow in lenny_predictions.py:
- PRED 15 reads 15 as an EDT SETTLEMENT HOUR (not minutes, not a $ price)
- it uses the strike from the last WATCH command as the target
- it passes minutes_remaining = minutes-to-that-hourly-close into run_prediction
- bare PRED runs the next-close auto-target prediction
Kalshi BTC markets settle hourly, so PRED resolves at a top-of-hour close.
Fully offline: the network/LLM functions are monkeypatched and we capture the
exact args run_prediction receives."""
import json, tempfile, os
import lenny_predictions as lp

PASS, FAIL = "✅ PASS", "❌ FAIL"
ok = []
def check(name, cond, detail=""):
    ok.append(cond); print(f"{PASS if cond else FAIL}  {name}{(' — ' + detail) if detail else ''}")

# Throwaway watch-state file.
lp.WATCH_FILE = os.path.join(tempfile.mkdtemp(), "lenny_watch.json")

# Capture run_prediction calls instead of hitting the network/LLM.
calls = []
def fake_run_prediction(symbol, target_override=None, minutes_remaining=None, manual=False):
    calls.append({"symbol": symbol, "target_override": target_override,
                  "minutes_remaining": minutes_remaining, "manual": manual})
lp.run_prediction = fake_run_prediction

# Capture outgoing telegram replies.
sent = []
lp.tg = lambda m: sent.append(m)

# Drive the REAL shipped dispatcher so the test fails if the parser diverges.
dispatch = lp.handle_command

print("="*64)
print("TEST — WATCH 75000 then PRED 15")
print("="*64)
dispatch("PRED 15")  # before any WATCH -> must refuse, no prediction
check("PRED before WATCH does not run a prediction", len(calls) == 0)
check("PRED before WATCH tells user to WATCH first", any("WATCH" in m for m in sent))

dispatch("WATCH 75000")
check("WATCH stores the strike", lp.load_watched_strike() == 75000.0,
      f"stored={lp.load_watched_strike()}")

calls.clear()
dispatch("PRED 15")
check("PRED 15 ran exactly one prediction", len(calls) == 1)
c = calls[0] if calls else {}
print(f"   run_prediction received: {c}")
check("target_override = watched strike 75000 (NOT the hour 15)", c.get("target_override") == 75000.0)
check("15 was NOT used as a price/target", c.get("target_override") != 15)
mr = c.get("minutes_remaining")
check("minutes_remaining is the minutes-to-hourly-close (1..1440)",
      isinstance(mr, int) and 1 <= mr <= 1440, f"minutes_remaining={mr}")
# The deadline that PRED 15 implies must land on the 15:00 EDT close.
_, close_hour = lp.minutes_to_hourly_close(15)
check("PRED 15 resolves to the 15:00 EDT close", close_hour == 15, f"close_hour={close_hour}")

print()
print("="*64)
print("TEST — other forms")
print("="*64)
calls.clear(); sent.clear()
dispatch("PRED")
check("bare PRED runs auto-target next-close (no minutes_remaining)",
      len(calls) == 1 and calls[0]["minutes_remaining"] is None)
check("bare PRED is manual (always replies)", calls[0]["manual"] is True)

calls.clear(); sent.clear()
dispatch("PRED abc")
check("'PRED abc' is rejected, no prediction", len(calls) == 0 and any("HOUR" in m for m in sent))

calls.clear(); sent.clear()
dispatch("PRED 25")
check("'PRED 25' rejected (hour out of 0-23 range)", len(calls) == 0 and any("0-23" in m for m in sent))

# deadline_label sanity — always a top-of-hour EDT close, never "X min"
dl = lp.deadline_label(13)
print(f"\n   deadline_label(13) -> {dl!r}")
check("deadline_label phrases an hourly close, not minutes",
      dl.startswith("end of") and dl.endswith("EDT") and "min" not in dl)

print()
print("="*64)
print(f"SUMMARY: {sum(ok)}/{len(ok)} checks passed")
print("="*64)
raise SystemExit(0 if all(ok) else 1)
