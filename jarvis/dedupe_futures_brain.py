#!/usr/bin/env python3
"""One-off deduplication of jarvis_futures_brain.json.

Backs up the file, removes exact-duplicate trade records
(same asset + entry + open_time), recomputes running totals,
and writes the cleaned file. Safe to re-run — idempotent.
"""
import json, shutil, os
from datetime import datetime

BRAIN_FILE = "/root/jarvis/jarvis_futures_brain.json"
BACKUP_FILE = BRAIN_FILE + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"

# ── Load ──────────────────────────────────────────────────────────────────────
with open(BRAIN_FILE) as f:
    brain = json.load(f)

trades_before = brain["trades"]
total_pnl_before = brain["total_pnl"]
total_trades_before = brain["total_trades"]
wins_before = brain["wins"]
losses_before = brain["losses"]

print(f"BEFORE: {len(trades_before)} trades | total_pnl={total_pnl_before:+.2f} | wins={wins_before} losses={losses_before}")

# ── Backup ────────────────────────────────────────────────────────────────────
shutil.copy2(BRAIN_FILE, BACKUP_FILE)
print(f"Backup written: {BACKUP_FILE}")

# ── Dedupe ────────────────────────────────────────────────────────────────────
seen = set()
deduped = []
removed = 0
for t in trades_before:
    key = (t.get("asset"), t.get("entry"), t.get("open_time"))
    if key in seen:
        print(f"  REMOVING DUPLICATE: {t.get('asset')} entry={t.get('entry')} open_time={t.get('open_time')} pnl={t.get('pnl')}")
        removed += 1
    else:
        seen.add(key)
        deduped.append(t)

# ── Recompute totals from deduped list ────────────────────────────────────────
computed_pnl    = round(sum(t.get("pnl", 0) for t in deduped), 2)
computed_wins   = sum(1 for t in deduped if t.get("won"))
computed_losses = sum(1 for t in deduped if not t.get("won"))
computed_total  = len(deduped)

brain["trades"]       = deduped
brain["total_trades"] = computed_total
brain["wins"]         = computed_wins
brain["losses"]       = computed_losses
brain["total_pnl"]    = computed_pnl

print(f"\nAFTER:  {computed_total} trades | total_pnl={computed_pnl:+.2f} | wins={computed_wins} losses={computed_losses}")
print(f"Duplicates removed: {removed}")
print(f"PnL change: {computed_pnl - total_pnl_before:+.2f}")

# ── Write (only if something changed) ────────────────────────────────────────
if removed == 0:
    print("\nNo duplicates found — file unchanged.")
    os.remove(BACKUP_FILE)
    print(f"Backup removed (not needed): {BACKUP_FILE}")
else:
    with open(BRAIN_FILE, "w") as f:
        json.dump(brain, f, indent=2)
    print(f"\nFile written: {BRAIN_FILE}")
