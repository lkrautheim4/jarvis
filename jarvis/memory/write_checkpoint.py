#!/usr/bin/env python3
"""Append a new JARVIS checkpoint (the single source of truth for project state).

APPEND-ONLY. Never overwrites a prior checkpoint. Newest seq wins.
Writes a .json (machine) and .md (human) pair.

Usage:
  python3 write_checkpoint.py "Title here" \
      --state "free-text current state" \
      --did "thing done" --did "another thing" \
      --next "next objective" \
      --stale "old assumption now proven false" \
      --assume "new open assumption to carry forward"

Rule of discipline: a working session does not end until this runs.
"""
import json
import sys
import argparse
import datetime as dt
from pathlib import Path

HERE = Path(__file__).resolve().parent
CKPT_DIR = HERE / "checkpoints"


def _utcnow():
    return dt.datetime.now(dt.timezone.utc)


def newest_seq():
    seqs = []
    for f in CKPT_DIR.glob("CKPT_*.json"):
        try:
            seqs.append(json.loads(f.read_text())["seq"])
        except Exception:
            pass
    return max(seqs) if seqs else 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("title")
    p.add_argument("--goal", default="")
    p.add_argument("--state", default="")
    p.add_argument("--did", action="append", default=[])
    p.add_argument("--next", dest="nxt", action="append", default=[])
    p.add_argument("--stale", action="append", default=[])
    p.add_argument("--assume", action="append", default=[])
    a = p.parse_args()

    CKPT_DIR.mkdir(parents=True, exist_ok=True)
    seq = newest_seq() + 1
    now = _utcnow()
    utc = now.isoformat(timespec="seconds")
    stamp = now.strftime("%Y-%m-%dT%H%MZ")
    base = CKPT_DIR / f"CKPT_{stamp}_{seq:03d}"

    if base.with_suffix(".json").exists():
        sys.exit(f"refuse: {base.name}.json exists (append-only)")

    # goal is sticky: if not supplied, inherit the most recent prior goal
    goal = a.goal
    if not goal:
        prev = [json.loads(f.read_text()) for f in CKPT_DIR.glob("CKPT_*.json")]
        prev = [p for p in prev if p.get("goal")]
        if prev:
            goal = max(prev, key=lambda p: p["seq"])["goal"]

    rec = {
        "seq": seq,
        "utc": utc,
        "title": a.title,
        "goal": goal,
        "supersedes": seq - 1 if seq > 1 else None,
        "state": a.state,
        "did": a.did,
        "next": a.nxt,
        "staled_assumptions": a.stale,
        "assumptions": [
            {"claim": c, "made_utc": utc, "status": "open"} for c in a.assume
        ],
    }
    base.with_suffix(".json").write_text(json.dumps(rec, indent=2))

    md = [f"# CHECKPOINT #{seq:03d} - {a.title}",
          f"_written {utc} (UTC) - supersedes #{rec['supersedes']}_", ""]
    if goal:
        md += ["## goal", goal, ""]
    if a.state:
        md += ["## current state", a.state, ""]
    if a.did:
        md += ["## done this session"] + [f"- {x}" for x in a.did] + [""]
    if a.nxt:
        md += ["## next objective"] + [f"- {x}" for x in a.nxt] + [""]
    if a.stale:
        md += ["## assumptions now STALE"] + [f"- ~~{x}~~" for x in a.stale] + [""]
    if a.assume:
        md += ["## open assumptions (unproven)"] + [f"- ? {x}" for x in a.assume] + [""]
    base.with_suffix(".md").write_text("\n".join(md))

    print(f"wrote checkpoint #{seq:03d}: {base.name}.json / .md")


if __name__ == "__main__":
    main()
