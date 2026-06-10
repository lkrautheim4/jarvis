#!/usr/bin/env python3
"""
JARVIS FIX — auto-repairs jarvis_intelligence.py before restart
Run this before restarting intel bot
"""
import re, subprocess, sys

file = "/root/jarvis/jarvis_intelligence.py"
content = open(file).read()

main_idx = content.index('\ndef main():')
before_main = content[:main_idx]
main_onwards = content[main_idx:]

# Find stray functions after main
stray_blocks = re.findall(r'\ndef (?!main)\w+[\s\S]*?(?=\ndef |\nif __name__|\Z)', main_onwards)
seen = {}
for block in stray_blocks:
    m = re.match(r'\ndef (\w+)', block)
    if m: seen[m.group(1)] = block

if not seen:
    print("No stray functions found — file is clean")
else:
    print(f"Moving {len(seen)} stray functions: {list(seen.keys())}")
    clean_main = re.sub(r'\ndef (?!main)\w+[\s\S]*?(?=\ndef |\nif __name__|\Z)', '', main_onwards)
    insertion = ''.join(seen.values())
    final = before_main + insertion + clean_main
    open(file, 'w').write(final)
    print("Fixed!")

# Syntax check
result = subprocess.run(["python3", "-m", "py_compile", file], capture_output=True, text=True)
if result.returncode == 0:
    print("Syntax OK")
else:
    print("SYNTAX ERROR:", result.stderr)
    sys.exit(1)
