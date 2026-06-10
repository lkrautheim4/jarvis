#!/usr/bin/env python3
"""
inject_heartbeats.py — Add SQLite heartbeat to all JARVIS bots
Run once on VPS: python3 /root/jarvis/inject_heartbeats.py

Adds `jarvis_brain.update_bot_heartbeat(BOT_NAME)` to each bot's main loop.
Safe to run multiple times — checks before patching.
"""
import os, re

JARVIS_DIR = "/root/jarvis"

# Bot name -> file mapping
BOTS = {
    "jarvis_master":        "jarvis_master.py",
    "jarvis_briefing":      "jarvis_briefing.py",
    "jarvis_intelligence":  "jarvis_intelligence.py",
    "jarvis_intel":         "jarvis_intel.py",
    "jarvis_options":       "jarvis_options.py",
    "jarvis_options_brain": "jarvis_options_brain.py",
    "jarvis_stocks_v2":     "jarvis_stocks_v2.py",
    "jarvis_beast":         "jarvis_beast.py",
    "jarvis_macro":         "jarvis_macro.py",
    "jarvis_earnings":      "jarvis_earnings.py",
    "jarvis_congress":      "jarvis_congress.py",
    "jarvis_level5":        "jarvis_level5.py",
    "jarvis_capital":       "jarvis_capital.py",
    "jarvis_range_detector":"jarvis_range_detector.py",
    "jarvis_webull_alerts": "jarvis_webull_alerts.py",
    "jarvis_trader":        "jarvis_trader.py",
    "lenny_trader_bot":     "lenny_trader_bot.py",
    "lenny_predictions":    "lenny_predictions.py",
}

HEARTBEAT_IMPORT = "import jarvis_brain as _jb_hb"
HEARTBEAT_CALL_TEMPLATE = '            _jb_hb.update_bot_heartbeat("{bot_name}")\n'

def patch_bot(bot_name: str, filename: str):
    path = os.path.join(JARVIS_DIR, filename)
    if not os.path.exists(path):
        print(f"  SKIP {filename} — not found")
        return False

    with open(path) as f:
        src = f.read()

    # Skip if already patched
    if f'update_bot_heartbeat("{bot_name}")' in src:
        print(f"  SKIP {filename} — already patched")
        return False

    # Add import if not present
    if HEARTBEAT_IMPORT not in src:
        # Insert after first import block
        lines = src.split("\n")
        insert_at = 0
        for i, line in enumerate(lines):
            if line.startswith("import ") or line.startswith("from "):
                insert_at = i + 1
        lines.insert(insert_at, HEARTBEAT_IMPORT)
        src = "\n".join(lines)

    # Find the main while True loop and inject heartbeat
    # Strategy: find `while True:` inside `def main():` and inject after try:
    heartbeat_call = HEARTBEAT_CALL_TEMPLATE.format(bot_name=bot_name)

    # Pattern 1: while True: / try: (most common)
    pattern1 = "        while True:\n        try:\n"
    replace1 = f"        while True:\n        try:\n{heartbeat_call}"

    pattern2 = "        while True:\n            try:\n"
    replace2 = f"        while True:\n            try:\n{heartbeat_call}"

    patched = False
    if pattern1 in src and heartbeat_call not in src:
        src = src.replace(pattern1, replace1, 1)
        patched = True
    elif pattern2 in src and heartbeat_call not in src:
        src = src.replace(pattern2, replace2, 1)
        patched = True
    else:
        # Fallback: find `time.sleep` in main loop and inject before it
        # Find all `time.sleep` calls and inject heartbeat before the first one
        # inside a while True block
        sleep_pattern = re.compile(r'(\s+)(time\.sleep\(\d+\))')
        match = sleep_pattern.search(src)
        if match and heartbeat_call not in src:
            indent = match.group(1)
            hb_line = f'{indent}_jb_hb.update_bot_heartbeat("{bot_name}")\n'
            src = src[:match.start()] + hb_line + src[match.start():]
            patched = True

    if not patched:
        print(f"  WARN {filename} — could not find injection point (manual patch needed)")
        return False

    # Write back
    tmp = path + ".heartbeat_patch_tmp"
    with open(tmp, "w") as f:
        f.write(src)
    os.replace(tmp, path)
    print(f"  ✅ Patched {filename}")
    return True

def main():
    print("=== JARVIS HEARTBEAT INJECTION ===")
    patched = 0
    skipped = 0
    failed  = 0

    for bot_name, filename in BOTS.items():
        result = patch_bot(bot_name, filename)
        if result:   patched += 1
        elif result is False: skipped += 1
        else:        failed += 1

    print(f"\n=== DONE ===")
    print(f"Patched: {patched} | Skipped (already done): {skipped} | Failed: {failed}")
    print("\nRestart all bots to activate heartbeats:")
    print("  bash /root/jarvis/start_all.sh")

if __name__ == "__main__":
    main()
