# JARVIS MEMORY ARCHITECTURE

A single source of truth that does **not** create a new data store. It *names*
the one canonical store per domain, quarantines the fossils, and keeps a
timestamped, append-only checkpoint trail for project state.

## The four categories, and where each actually lives

| # | Category               | Lives in                                  | Written by      |
|---|------------------------|-------------------------------------------|-----------------|
| 1 | Permanent knowledge    | `knowledge.md`                            | us (verified)   |
| 2 | Current project state  | `checkpoints/` (newest seq wins)          | us, per session |
| 3 | Market memory          | canonical stores (registered in MANIFEST) | bots, live      |
| 4 | Trade history          | canonical stores (registered in MANIFEST) | bots, live      |

Categories 1-2 get new files here. Categories 3-4 are **registered, not copied** —
re-housing live trade/market data would create a second store that disagrees
with the first. The registry points; it never duplicates.

## Files
- `MANIFEST.json` — the registry. Canonical store per domain + quarantined fossils. Holds no data.
- `load_memory.py` — run at session/bot startup. Prints the STATE banner; newest checkpoint wins. Exposes `assert_canonical(domain, store)`.
- `write_checkpoint.py` — run at session end. Appends a timestamped checkpoint. Append-only.
- `knowledge.md` — permanent verified facts (category 1).
- `checkpoints/` — one CKPT_<utc>_<seq>.json/.md pair per session (category 2).

## The two enforced rules
1. **Newest checkpoint always wins.** `load_memory.py` computes max(seq); anything older is non-authoritative. Banner shows AGE so a stale (>24h) state screams instead of passing silently.
2. **Only canonical stores may be read.** `assert_canonical()` raises on any quarantined fossil or non-canonical path. Wire it into every reader so "read the wrong file" is impossible, not just unlikely.

## Discipline (this is what makes it not rot)
- **Session START:** `python3 memory/load_memory.py` — read the banner first, answer only from it.
- **Session END:** `python3 memory/write_checkpoint.py "..."` — no working session ends without it.
- Checkpoints store claims **with provenance** (store + reader + as-of), never bare derived numbers. A cached number is a future fossil.

## Apply on the box
```bash
cd /root/jarvis
cp -r /path/to/memory ./memory        # drop the folder in (or git pull)
python3 memory/load_memory.py          # see the banner; all stores show UNVERIFIED
```

## Verify the canonical paths (flip UNVERIFIED -> dated)
Run each; if it returns sane live data, set that domain's `verified_utc` in MANIFEST.json to today's UTC.
```bash
cd /root/jarvis
# trades.kalshi / trades.options
sqlite3 jarvis_memory.db "select count(*) from kalshi_bets; select count(*) from options_trades;"
# market.btc  (rows present, not the frozen stats block)
python3 -c "import json;d=json.load(open('btc_memory.json'));print('rows:',len(d.get('rows',d)) if isinstance(d,dict) else len(d))"
# market.fng
python3 -c "import json;print(json.load(open('jarvis_central_brain.json')).get('equity_fear_greed'))"
```

## Follow-ups (not in v1)
- Import `assert_canonical()` into kalshi_grader, get_btc_pred_stats, options analytics readers.
- Add `python3 memory/load_memory.py` to `start_all.sh` so every boot prints the banner.
