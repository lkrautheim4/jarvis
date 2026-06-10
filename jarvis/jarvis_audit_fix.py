
import subprocess, re, py_compile, json, os, sys

print("=== JARVIS FULL CODE AUDIT & FIX ===")

files_to_check = [
    "/root/jarvis/jarvis_master.py",
    "/root/jarvis/jarvis_options.py", 
    "/root/jarvis/jarvis_stocks_v2.py",
    "/root/jarvis/jarvis_level5.py",
    "/root/jarvis/jarvis_intelligence.py",
    "/root/jarvis/jarvis_briefing.py",
    "/root/jarvis/jarvis_api.py",
    "/root/jarvis/jarvis_beast.py",
    "/root/jarvis/jarvis_congress.py",
    "/root/jarvis/jarvis_macro.py",
    "/root/jarvis/jarvis_earnings.py",
]

issues_fixed = 0
issues_found = 0

for fpath in files_to_check:
    if not os.path.exists(fpath): continue
    fname = os.path.basename(fpath)
    
    with open(fpath) as f:
        content = f.read()
    original = content
    
    issues = []
    fixes = []
    
    # 1. Syntax check
    try:
        py_compile.compile(fpath, doraise=True)
    except py_compile.PyCompileError as e:
        issues.append(f"SYNTAX ERROR: {e}")
    
    # 2. Binance calls (blocked on VPS)
    binance_count = content.count("api.binance.com")
    if binance_count > 0:
        issues.append(f"Binance calls: {binance_count} (blocked HTTP 451)")
    
    # 3. CoinGecko calls (rate limited)
    cg_count = content.count("coingecko.com")
    if cg_count > 0:
        issues.append(f"CoinGecko calls: {cg_count} (rate limited)")
    
    # 4. Unterminated f-strings
    for i, line in enumerate(content.split("\n"), 1):
        if "f\"" in line and line.count("\"") % 2 != 0 and not line.strip().startswith("#"):
            if "\n" not in line.replace("\\n", ""):
                issues.append(f"Possible unterminated f-string line {i}")
    
    # 5. Missing imports check
    if "jarvis_master" in fname:
        for mod in ["jarvis_data", "jarvis_learning", "jarvis_rules", "jarvis_edges", "jarvis_position_monitor"]:
            if mod not in content:
                issues.append(f"Missing import: {mod}")
    
    # 6. Stale .pyc check
    pyc = fpath.replace(".py", ".cpython-312.pyc").replace("/root/jarvis/", "/root/jarvis/__pycache__/")
    if os.path.exists(pyc):
        src_mtime = os.path.getmtime(fpath)
        pyc_mtime = os.path.getmtime(pyc)
        if pyc_mtime < src_mtime:
            issues.append("Stale .pyc cache")
            os.remove(pyc)
            fixes.append("Deleted stale .pyc")
    
    # Print results
    if issues:
        print(f"\n❌ {fname}:")
        for issue in issues:
            print(f"   {issue}")
        issues_found += len(issues)
    else:
        print(f"✅ {fname} — clean")

# Check all processes
print("\n=== BOT PROCESSES ===")
bots = ["jarvis_master","jarvis_briefing","jarvis_api","jarvis_level5",
        "jarvis_intelligence","jarvis_stocks_v2","jarvis_options","jarvis_watchdog"]
for bot in bots:
    r = subprocess.run(["pgrep","-f",bot+".py"], capture_output=True, text=True)
    status = "✅ running" if r.returncode==0 else "❌ DOWN"
    print(f"  {status} {bot}")

# Check data files
print("\n=== DATA FILES ===")
import time
data_files = ["btc_memory.json","kalshi_brain.json","jarvis_central_brain.json",
              "jarvis_patterns.json","jarvis_level5.json","jarvis_intel.json",
              "jarvis_rules.py","jarvis_data.py","jarvis_learning.py",
              "jarvis_position_monitor.py","jarvis_edges.py"]
for f in data_files:
    path = f"/root/jarvis/{f}"
    if os.path.exists(path):
        age = round((time.time()-os.path.getmtime(path))/60, 0)
        size = round(os.path.getsize(path)/1024, 1)
        print(f"  ✅ {f} ({size}KB, {age:.0f}min old)")
    else:
        print(f"  ❌ {f} MISSING")

# Live data check
print("\n=== LIVE DATA CHECK ===")
sys.path.insert(0, "/root/jarvis")
try:
    from jarvis_data import get_rsi, get_macd, get_bollinger, get_momentum, get_4h_momentum, get_closes
    closes = get_closes()
    rsi = get_rsi()
    _,_,mh = get_macd(closes)
    _,_,_,pctb = get_bollinger(closes)
    mom = get_momentum()
    _,t4h = get_4h_momentum()
    ok = rsi != 50.0 and mh != 0
    print(f"  {'✅' if ok else '❌'} RSI={rsi} MACD={mh:.1f} BB={pctb:.2f} 1h={mom['1h']}% 4H={t4h}")
except Exception as e:
    print(f"  ❌ Data error: {e}")

# Kalshi stats
try:
    kb = json.load(open("/root/jarvis/kalshi_brain.json"))
    s = kb["stats"]
    total = s.get("total",0); wins = s.get("wins",0)
    wr = round(wins/total*100,1) if total>0 else 0
    print(f"  ✅ Kalshi: {total} bets | {wr}% WR | ${s.get('profit',0):+.0f} P&L")
except Exception as e:
    print(f"  ❌ Kalshi: {e}")

print(f"\n=== SUMMARY ===")
print(f"Issues found: {issues_found}")
print(f"Run: pkill -f jarvis_master && nohup python3 -B /root/jarvis/jarvis_master.py > /root/jarvis/jarvis_master.log 2>&1 &")
