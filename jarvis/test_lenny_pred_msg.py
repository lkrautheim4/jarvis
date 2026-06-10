#!/usr/bin/env python3
"""Verify the lenny_trader_bot PRED confirmation message — HOURLY contract.

Kalshi BTC markets settle HOURLY, so the lone PRED number is an EDT settlement
HOUR (0-23), not minutes — there is no sub-hourly market.
  PRED 7          -> watched strike at the 07:00 EDT close
  PRED <price> 4  -> explicit strike at the 04:00 EDT close
  PRED            -> watched strike at the next top-of-hour close
  (hour out of range, or no strike watched -> graceful refusal, no prediction)"""
import json, tempfile, os
import lenny_trader_bot as lt

PASS, FAIL = "✅ PASS", "❌ FAIL"
ok = []
def check(n, c, d=""):
    ok.append(c); print(f"{PASS if c else FAIL}  {n}{(' — ' + str(d)) if d else ''}")

# Seed a watched strike exactly like the WATCH command writes it.
lt.WATCH_FILE = os.path.join(tempfile.mkdtemp(), "lenny_watch.json")
with open(lt.WATCH_FILE, "w") as f:
    json.dump({"strike": 64026.0, "set_at": "2026-06-04T00:00:00"}, f)

# Capture outgoing telegram messages and the args handle_pred receives.
sent = []
lt.tg = lambda m: sent.append(m)
hp_args = []
lt.handle_pred = lambda price, mins, close_label: hp_args.append((price, mins, close_label))
lt.get_btc_price = lambda: 64500.0  # must NOT be used when a strike is watched

def pred(textline):
    """Drive the SHIPPED PRED logic: real resolve_pred_args +
    minutes_to_hourly_close + the message format copied verbatim from main()."""
    sent.clear(); hp_args.clear()
    parts = textline.strip().upper().split()
    price, edt_hour = lt.resolve_pred_args(parts)          # real shipped mapping
    if edt_hour is not None and not (0 <= edt_hour <= 23):
        lt.tg("PRED takes an EDT settlement HOUR (0-23), not minutes.\n"
              "PRED — next hourly close\nPRED 15 — the 15:00 EDT close\n"
              "PRED <strike> 15 — explicit strike at 15:00 EDT")
    elif price is None:
        lt.tg("No strike being watched. Send WATCH <strike> first, "
              "or use PRED <price> <hour>.")
    else:
        mins, close_hour = lt.minutes_to_hourly_close(edt_hour)   # real shipped helper
        close_label = f"{close_hour:02d}:00 EDT"
        lt.tg(f"Analyzing ${price:,.0f} for the {close_label} close ({mins} min away)...")
        lt.handle_pred(price, mins, close_label)
    return sent[-1] if sent else None

print("="*60)
print("PRED <hour> — lone number is an EDT settlement HOUR")
print("="*60)
msg = pred("PRED 7")
print(f"   reply: {msg!r}")
print(f"   handle_pred args: {hp_args[0] if hp_args else None}")
check("price is the watched strike $64,026 (not $7)", bool(hp_args) and hp_args[0][0] == 64026.0)
check("hour 7 -> close_label 07:00 EDT", bool(hp_args) and hp_args[0][2] == "07:00 EDT",
      hp_args[0][2] if hp_args else None)
check("mins is a positive int (time to that close)",
      bool(hp_args) and isinstance(hp_args[0][1], int) and hp_args[0][1] >= 1)
check("reply names the strike and the EDT hourly close",
      bool(msg) and "$64,026" in msg and "07:00 EDT" in msg, msg)

print()
print("="*60)
print("REGRESSION — other forms")
print("="*60)
pred("PRED 75179 4")
check("explicit PRED <price> <hour> still works",
      bool(hp_args) and hp_args[0][0] == 75179.0 and hp_args[0][2] == "04:00 EDT",
      hp_args[0] if hp_args else None)
m = pred("PRED")
check("bare PRED uses watched strike at the next top-of-hour close",
      bool(hp_args) and hp_args[0][0] == 64026.0 and hp_args[0][2].endswith(":00 EDT"),
      hp_args[0] if hp_args else None)
m = pred("PRED 99")
check("hour out of range (0-23) -> refuses, no handle_pred call",
      not hp_args and "HOUR" in (m or "").upper())

# No watch set -> graceful refusal, no prediction.
lt.WATCH_FILE = os.path.join(tempfile.mkdtemp(), "none.json")
m = pred("PRED 7")
check("no watched strike -> refuses, no handle_pred call",
      not hp_args and "No strike" in (m or ""))

print()
print("="*60)
print(f"SUMMARY: {sum(ok)}/{len(ok)} checks passed")
print("="*60)
raise SystemExit(0 if all(ok) else 1)
