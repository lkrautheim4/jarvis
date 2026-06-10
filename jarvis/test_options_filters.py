#!/usr/bin/env python3
"""Test-mode harness for the options-brain hardening (DTE / IV-Rank / moneyness
/ junk filter). Drives the REAL functions and the REAL scan_and_alert code path
with adversarial inputs, fully offline (network calls are monkeypatched), and
asserts no sub-30-DTE contract and no deep-OTM strike can fire or be logged."""
import logging, tempfile, os, io
import jarvis_options_brain as ob
import paper_trades_store as store

PASS, FAIL = "✅ PASS", "❌ FAIL"
results = []
def check(name, cond, detail=""):
    results.append(cond)
    print(f"{PASS if cond else FAIL}  {name}{(' — ' + detail) if detail else ''}")

print("="*70)
print("TEST 1 — find_best_contract: DTE floor (30) + 10% moneyness on buys")
print("="*70)
# Mixed chain: a sub-30-DTE put, a deep-OTM (53%) put, and a clean 35-DTE one.
chain = [
    {"strike_price":"250.0","expiration_date":"2026-06-17","bid":0.12,"ask":0.16,"symbol":"AMD_250_14d"},  # 14 DTE + 53% OTM
    {"strike_price":"250.0","expiration_date":"2026-07-08","bid":0.18,"ask":0.22,"symbol":"AMD_250_35d"},  # 35 DTE but 53% OTM
    {"strike_price":"510.0","expiration_date":"2026-07-08","bid":20.0,"ask":22.0,"symbol":"AMD_510_35d"},  # 35 DTE, 3.8% OTM  ✔
    {"strike_price":"505.0","expiration_date":"2026-06-17","bid":8.0,"ask":9.0,"symbol":"AMD_505_14d"},     # 14 DTE (too short)
]
spot = 530.0
best = ob.find_best_contract(chain, spot, "put_buy")
print(f"  spot=${spot} -> selected: {best['symbol'] if best else None}")
import datetime as _dt
def dte_of(c): return (_dt.datetime.strptime(c["expiration_date"],"%Y-%m-%d") - _dt.datetime.now()).days
check("never selects a <30 DTE contract", best is not None and dte_of(best) >= 30, f"DTE={dte_of(best) if best else 'n/a'}")
check("never selects a >10% OTM strike", best is not None and abs(float(best['strike_price'])-spot)/spot <= 0.10,
      f"strike=${best['strike_price'] if best else 'n/a'}")
# Adversarial: ONLY junk contracts available -> must return nothing.
junk_only = [c for c in chain if c["symbol"] in ("AMD_250_14d","AMD_250_35d","AMD_505_14d")]
check("returns None when only junk/short contracts exist", ob.find_best_contract(junk_only, spot, "put_buy") is None)

print()
print("="*70)
print("TEST 2 — strike_too_far_otm (#3)")
print("="*70)
check("AMD $250 put @ spot $530 is too far OTM", ob.strike_too_far_otm(530.0, 250.0) is True, "53% away")
check("AMD $510 put @ spot $530 is acceptable", ob.strike_too_far_otm(530.0, 510.0) is False, "3.8% away")
check("AAPL $145 put @ spot $298 is too far OTM", ob.strike_too_far_otm(298.0, 145.0) is True)

print()
print("="*70)
print("TEST 3 — get_iv_rank (#2): realized-vol 52wk proxy")
print("="*70)
# Monkeypatch yfinance history with a synthetic low-then-current-high vol path.
class _FakeHist:
    def __init__(self, closes): self._c = closes
    def __getitem__(self, k):
        import pandas as pd; return pd.Series(self._c)
class _FakeTicker:
    def __init__(self, sym): pass
    def history(self, period="1y"):
        # Calm first half, choppy second half -> a real vol range to rank within.
        import math
        closes = []
        p = 100.0
        for i in range(250):
            step = 0.002 if i < 125 else 0.05*((-1)**i)
            p *= (1+step); closes.append(p)
        return _FakeHist(closes)
import sys
class _FakeYF:
    Ticker = _FakeTicker
sys.modules["yfinance"] = _FakeYF
ob._IV_RANK_CACHE.clear()
rank_lowiv = ob.get_iv_rank("FAKE", 5.0)     # current IV below the realized range -> low rank
ob._IV_RANK_CACHE.clear()
rank_highiv = ob.get_iv_rank("FAKE", 200.0)  # current IV above the range -> clamps to 100
print(f"  IV=5%  -> rank {rank_lowiv}")
print(f"  IV=200% -> rank {rank_highiv}")
check("low current IV -> IV Rank <= 50", rank_lowiv is not None and rank_lowiv <= 50)
check("very high current IV -> IV Rank > 50 (gate trips)", rank_highiv is not None and rank_highiv > 50)
check("IV Rank clamped to 0..100", 0 <= rank_lowiv <= 100 and 0 <= rank_highiv <= 100)

print()
print("="*70)
print("TEST 4 — is_junk_contract FAILS CLOSED (#4)")
print("="*70)
j1,_ = store.is_junk_contract(530.0, 250.0, 0.14); check("AMD $250 put @ $530 is junk", j1)
j2,_ = store.is_junk_contract(298.0, 145.0, 0.01); check("AAPL $145 put @ $298 is junk", j2)
j3,r3 = store.is_junk_contract(None, 250.0, 5.0);  check("missing spot -> junk (fail closed)", j3, r3)
j4,r4 = store.is_junk_contract("oops", "bad", None); check("garbage inputs -> junk (fail closed)", j4, r4)
j5,_ = store.is_junk_contract(530.0, 515.0, 22.0);  check("AMD $515 put @ $530 (valid) -> NOT junk", not j5)

print()
print("="*70)
print("TEST 5 — would_exceed_cap enforces junk centrally (#4)")
print("="*70)
data = {"trades": []}
junk_trade = {"ticker":"AMD","strike":250.0,"entry_price":530.0,"premium":0.14,"cost_per_contract":14.0,"status":"paper_open"}
capped, reason = store.would_exceed_cap(data, "AMD", 14.0, trade=junk_trade)
print(f"  junk AMD $250 -> capped={capped} ({reason})")
check("central guard rejects the junk contract", capped and "junk" in reason.lower())
good_trade = {"ticker":"AMD","strike":515.0,"entry_price":530.0,"premium":22.0,"cost_per_contract":2200.0,"status":"paper_open"}
# 2200 exceeds the $500 training-mode contract cap, so it should cap for a NON-junk reason.
capped2, reason2 = store.would_exceed_cap(data, "AMD", 2200.0, trade=good_trade)
check("valid contract not rejected as junk", "junk" not in reason2.lower(), reason2)

print()
print("="*70)
print("TEST 6 — INTEGRATION: real scan_and_alert over adversarial chain (offline)")
print("="*70)
# Capture log output to prove the SKIP lines fire.
logbuf = io.StringIO()
h = logging.StreamHandler(logbuf); h.setLevel(logging.INFO)
ob.log.addHandler(h); ob.log.setLevel(logging.INFO)

# Point the paper-trades store at a throwaway file so we can assert nothing logs.
tmpdir = tempfile.mkdtemp()
store.PAPER_TRADES_FILE = os.path.join(tmpdir, "paper_trades.json")
store._LOCK_FILE = store.PAPER_TRADES_FILE + ".lock"

sent = []
ob.tg = lambda m: sent.append(m)
ob.get_day_change_pct = lambda t: 0.0
# Only AMD has a price -> only AMD is scanned. Real ~$530.
ob.get_price = lambda t: 530.0 if t == "AMD" else None
# Adversarial chain: deep-OTM + short-DTE junk, plus one clean 35-DTE 3.8%-OTM put.
def fake_contracts(ticker, option_type="put", **kw):
    return ([
        {"strike_price":"250.0","expiration_date":"2026-06-17","bid":0.12,"ask":0.16,"symbol":"AMD250_14"},
        {"strike_price":"510.0","expiration_date":"2026-07-08","bid":20.0,"ask":22.0,"symbol":"AMD510_35"},
    ], 95.0)  # IV 95%
ob.get_yf_contracts = fake_contracts
# Force IV Rank high so the (valid-strike) buy is rejected as too expensive.
ob.get_iv_rank = lambda t, iv: 78.0
# Make every setup score high enough to pass the threshold.
ob.score_setup = lambda *a, **k: (85, ["TEST"])

ctx = {"earnings":{}, "brain":{"fear_greed":28}, "macro":{"regime":"RISK_OFF","vix":{"value":30}}}
brain = {"trades":[], "stats":{"total":0,"wins":0,"losses":0,"open":0,"total_pnl":0.0,"total_premium":0.0,
         "by_ticker":{},"by_strategy":{},"by_regime":{},"by_fg_range":{},"by_iv_level":{}}, "signals_sent":{}}
ob.save_brain = lambda b: None
brain = ob.scan_and_alert(brain, ctx)

logs = logbuf.getvalue()
print("---- captured scan log ----")
for line in logs.splitlines():
    if any(k in line for k in ("SKIP","JUNK","CAP","Scanning")):
        print("   " + line)
print("---- result ----")
logged = store.read().get("trades", [])
print(f"  alerts sent: {len(sent)} | paper trades logged: {len(logged)}")
check("IV-Rank gate fired (logged the SKIP line)", "IV Rank 78 — too expensive to buy" in logs)
check("zero paper trades logged from adversarial chain", len(logged) == 0)
# Whatever (if anything) fired must satisfy both invariants.
import re
deep = re.findall(r"\$(\d+(?:\.\d+)?)\b", "".join(sent))
check("no alert references the deep-OTM $250 strike", "250" not in [s.split('.')[0] for s in deep] or len(sent)==0,
      f"sent={len(sent)}")

print()
print("="*70)
print(f"SUMMARY: {sum(results)}/{len(results)} checks passed")
print("="*70)
raise SystemExit(0 if all(results) else 1)
