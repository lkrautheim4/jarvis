#!/usr/bin/env python3
"""vision_canary.py - MANUAL write-path proof. Writes a sentinel row,
reads it back, then DELETES it. Leaves options_trades clean.
Run by hand only - never scheduled. Run: python3 vision_canary.py"""
import sys
import jarvis_memory_db as db

print("=== vision_canary: testing write path (transient, self-cleaning) ===")
rid = None
try:
    rid = db.log_manual_option(symbol="__CANARY__", strategy="call_buy",
                               direction="DEBIT", strike=0, dte=0,
                               premium=0.01, contracts=1)
    con = db._mc_sq.connect(db._MC_DB, timeout=10)
    row = con.execute("SELECT id FROM options_trades WHERE id=?", (rid,)).fetchone()
    if not row:
        print(f"FAIL: write claimed id {rid} but row not found"); con.close(); sys.exit(1)
    print(f"PASS: write persisted (id {rid})")
    con.execute("DELETE FROM options_trades WHERE id=? AND symbol='__CANARY__'", (rid,))
    con.commit()
    gone = con.execute("SELECT id FROM options_trades WHERE id=?", (rid,)).fetchone()
    con.close()
    if gone:
        print(f"FAIL: cleanup failed, id {rid} still present - REMOVE MANUALLY"); sys.exit(1)
    print(f"PASS: canary row {rid} deleted, table clean")
    print("=== RESULT: write path WORKS ===")
except Exception as e:
    print(f"FAIL: {e}")
    if rid:
        print(f"WARNING: id {rid} may remain - check with: SELECT * FROM options_trades WHERE symbol='__CANARY__'")
    sys.exit(1)
