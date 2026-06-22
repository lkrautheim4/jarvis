#!/usr/bin/env python3
"""JARVIS single-source-of-truth memory loader.

Run at the START of every session AND at bot startup. Prints the authoritative
STATE banner: newest checkpoint, canonical stores, quarantined fossils, open
assumptions. Stdlib only.

Two hard rules this enforces:
  1. Newest checkpoint always wins. Older project state is never authoritative.
  2. Only canonical stores may be read. assert_canonical() raises on fossils.
"""
import json
import datetime as dt
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "MANIFEST.json"
CKPT_DIR = HERE / "checkpoints"
STALE_HOURS = 24


def _utcnow():
    return dt.datetime.now(dt.timezone.utc)


def _parse_utc(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_manifest():
    return json.loads(MANIFEST.read_text())


def newest_checkpoint():
    """Return (dict, path) of the highest-seq checkpoint, or None."""
    best = None
    for f in CKPT_DIR.glob("CKPT_*.json"):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        if best is None or d.get("seq", -1) > best[0]["seq"]:
            best = (d, f)
    return best


def assert_canonical(domain, store):
    """Raise if `store` is a quarantined fossil or not canonical for `domain`.

    Import this in any reader (kalshi_grader, get_btc_pred_stats, options
    analytics) so 'read the wrong file' becomes impossible, not just unlikely.
    """
    m = load_manifest()
    for r in m["retired"]:
        match = r.get("match")
        if store == r["id"] or (match and match in store):
            raise RuntimeError(f"QUARANTINED STORE refused: {store} -> {r['reason']}")
    can = m["canonical"].get(domain)
    if can and store not in (can["path"], can.get("id")):
        raise RuntimeError(
            f"NON-CANONICAL read for {domain}: {store} (canonical={can['path']})"
        )
    return True


def banner():
    m = load_manifest()
    now = _utcnow()
    nc = newest_checkpoint()
    L = ["=" * 64,
         f"JARVIS MEMORY STATE   loaded {now.isoformat(timespec='seconds')}",
         "=" * 64]

    if nc is None:
        L.append("newest checkpoint: NONE  <-- no project state on record")
    else:
        d, _ = nc
        age_h = (now - _parse_utc(d["utc"])).total_seconds() / 3600
        fresh = "FRESH" if age_h <= STALE_HOURS else "STALE"
        L.append(f"newest checkpoint: #{d['seq']:03d}  {d['title']}")
        L.append(f"  written {d['utc']}  (age {age_h:.1f}h)  [{fresh}]")
        if d.get("goal"):
            L.append(f"  GOAL: {d['goal']}")
        if fresh == "STALE":
            L.append("  >> STALE: re-verify project state before trusting it.")

    L.append("-" * 64)
    L.append("CANONICAL stores (read ONLY these):")
    for dom, c in m["canonical"].items():
        L.append(f"  {dom:<15} {c['path']:<32} [{c.get('verified_utc','?')}]")
        L.append(f"  {'':<15} via {c['reader']}")

    L.append("-" * 64)
    L.append("QUARANTINED (never read):")
    for r in m["retired"]:
        L.append(f"  x {r['id']:<30} {r['reason']}")

    if nc:
        opens = [a for a in nc[0].get("assumptions", []) if a.get("status") == "open"]
        if opens:
            L.append("-" * 64)
            L.append("OPEN assumptions (unproven; do NOT state as fact):")
            for a in opens:
                L.append(f"  ? {a['claim']}  (since {a['made_utc']})")

    L.append("=" * 64)
    L.append("RULE: answer only from the state above. Newer checkpoint always wins.")
    return "\n".join(L)


if __name__ == "__main__":
    print(banner())
