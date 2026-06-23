#!/usr/bin/env python3
"""logtrade — interactive CLI for the manual options journal.
Isolated: only calls jarvis_memory_db canonical functions. Touches no bot.
  ./logtrade.py          -> menu
  ./logtrade.py open      -> log a new trade
  ./logtrade.py close     -> close + grade a trade
  ./logtrade.py list      -> show open trades
"""
import sys
import jarvis_memory_db as m
import sqlite3

DB = "/root/jarvis/jarvis_memory.db"

def _ask(prompt, cast=str, required=True, choices=None, allow_blank=False):
    while True:
        raw = input(prompt).strip()
        if raw == "" and (allow_blank or not required):
            return None
        if raw == "":
            print("  ! required"); continue
        if choices and raw.upper() not in choices:
            print(f"  ! must be one of {choices}"); continue
        try:
            return cast(raw.upper() if choices else raw)
        except ValueError:
            print(f"  ! not a valid {cast.__name__}"); continue

def list_open():
    c = sqlite3.connect("file:"+DB+"?mode=ro", uri=True); c.row_factory=sqlite3.Row
    rows = c.execute("SELECT id,symbol,strategy,direction,strike,premium,contracts,entry_ts "
                     "FROM options_trades WHERE source='manual_copilot' AND status='open' "
                     "ORDER BY id").fetchall()
    c.close()
    if not rows:
        print("\n(no open manual trades)\n"); return
    print("\n  OPEN MANUAL TRADES")
    print("  " + "-"*60)
    for r in rows:
        print(f"  #{r['id']}  {r['symbol']} {r['strategy']} {r['direction']} "
              f"${r['strike']} @ ${r['premium']} x{r['contracts']}  ({r['entry_ts']})")
    print()

def do_open():
    print("\n=== LOG NEW OPTION TRADE ===")
    sym   = _ask("Ticker (e.g. SPY): ").upper()
    direc = _ask("Direction [DEBIT=you paid / CREDIT=you got paid]: ", choices=("DEBIT","CREDIT"))
    strat = _ask("Strategy (e.g. long_call, bull_put_spread): ").lower()
    strike= _ask("Strike: ", float)
    dte   = _ask("DTE (days to expiry): ", int)
    prem  = _ask("Entry premium (per contract): ", float)
    ctr   = _ask("Contracts: ", int)
    regime= _ask("Regime [BULLISH/BEARISH/CHOP, blank=skip]: ", required=False, allow_blank=True)
    score = _ask("Confidence 0-1 [blank=skip]: ", float, required=False, allow_blank=True)
    thesis= _ask("Thesis [blank=skip]: ", required=False, allow_blank=True)

    print("\n  ---- CONFIRM ----")
    print(f"  {sym}  {strat}  {direc}")
    print(f"  strike ${strike}   DTE {dte}   premium ${prem}   contracts {ctr}")
    print(f"  regime {regime or '-'}   score {score if score is not None else '-'}")
    print(f"  thesis: {thesis or '-'}")
    est = prem*ctr*100
    print(f"  capital at risk (debit) / credit received: ${est:,.2f}")
    print("  -----------------")
    if input("  Save? [y/N]: ").strip().lower() != "y":
        print("  cancelled, nothing saved.\n"); return
    tid = m.log_manual_option(symbol=sym, strategy=strat, direction=direc, strike=strike,
                              dte=dte, premium=prem, contracts=ctr,
                              regime=regime, score=score, thesis=thesis)
    print(f"\n  ✅ saved trade #{tid}  (status: open)")
    print(f"  to close later:  ./logtrade.py close   then enter id {tid}\n")

def do_close():
    list_open()
    tid  = _ask("Trade id to close: ", int)
    c = sqlite3.connect("file:"+DB+"?mode=ro", uri=True); c.row_factory=sqlite3.Row
    r = c.execute("SELECT id,symbol,strategy,direction,premium,contracts,status,is_real,source "
                  "FROM options_trades WHERE id=?", (tid,)).fetchone()
    c.close()
    if not r:
        print(f"  ! no trade #{tid}\n"); return
    if r["source"] != "manual_copilot":
        print(f"  ! #{tid} is not a manual_copilot trade (source={r['source']}). Refusing.\n"); return
    if r["status"] == "closed":
        print(f"  ! #{tid} already closed.\n"); return
    print(f"\n  closing #{tid}: {r['symbol']} {r['strategy']} {r['direction']} "
          f"entry ${r['premium']} x{r['contracts']}")
    exitp = _ask("Exit premium (per contract): ", float)
    gross = (exitp - r["premium"]) * r["contracts"] * 100
    pnl_preview = gross if r["direction"]=="DEBIT" else -gross
    print(f"\n  ---- CONFIRM CLOSE ----")
    print(f"  entry ${r['premium']} -> exit ${exitp}  x{r['contracts']}  ({r['direction']})")
    print(f"  projected P&L: ${pnl_preview:+,.2f}")
    print("  -----------------------")
    if input("  Close & grade? [y/N]: ").strip().lower() != "y":
        print("  cancelled, trade still open.\n"); return
    res = m.close_manual_option(tid, exit_premium=exitp)
    print(f"\n  ✅ #{res['id']} CLOSED: {res['result']}  ${res['pnl']:+,.2f}  "
          f"({res['direction']}, exit ${exitp})\n")

def menu():
    if len(sys.argv) > 1:
        a = sys.argv[1].lower()
        if a == "open":  return do_open()
        if a == "close": return do_close()
        if a == "list":  return list_open()
    while True:
        print("logtrade —  [1] open  [2] close  [3] list open  [q] quit")
        ch = input("> ").strip().lower()
        if ch in ("1","open"):  do_open()
        elif ch in ("2","close"): do_close()
        elif ch in ("3","list"):  list_open()
        elif ch in ("q","quit",""): print("bye"); break

if __name__ == "__main__":
    try:
        menu()
    except (KeyboardInterrupt, EOFError):
        print("\ncancelled, nothing saved.")
