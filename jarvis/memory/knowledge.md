# JARVIS PERMANENT KNOWLEDGE  (category 1)

Durable, verified facts. Slow-changing. Each entry carries provenance and a
verified date. "Permanent" is earned by surviving verification, not assumed —
prior "permanent" facts (e.g. canonical WR 69.3%) turned out fabricated.
Anything not yet confirmed on the live box is tagged UNVERIFIED.

Format: `- [verified_utc] STATEMENT  (source)`

## Data architecture
- [UNVERIFIED] `jarvis_memory.db` is the sole canonical live DB writer for trades. (sessions Jun 13-22)
- [UNVERIFIED] `kalshi_grader.py:grade_bets()` is the only trustworthy Kalshi grading path; price-based via `_compute_pnl()` with real entry prices. (Jun 13-22)
- [UNVERIFIED] BTC live grading store is `btc_memory.json`, read per-row via `get_btc_pred_stats()`. (Jun 22)
- [UNVERIFIED] Equity Fear&Greed lives in `jarvis_central_brain.json` key `equity_fear_greed`, sourced from CNN dataviz, 2h staleness guard. (Jun 10-11)

## Quarantined fossils — never read (see MANIFEST.retired)
- [UNVERIFIED] `jarvis_brain.db` frozen ~May-28 — fake +$400.58 / 75% WR.
- [UNVERIFIED] orphaned SQLite `predictions` table — fake 84% BTC accuracy.
- [UNVERIFIED] `kalshi_brain.json` pnl/totals — fabricated even-money WINs (+$2,018).
- [UNVERIFIED] `btc_memory.json` internal `stats` block — frozen ~1%.
- [UNVERIFIED] `options_trades.entry_price` / `exit_price` — uniformly 0.0, never use.

## Laws (operating principles)
- The signature failure mode is GREEN LIGHTS OVER DEAD PIPES: instrumentation healthy while the pipe is broken.
- Every stored aggregator/rollup found so far was frozen and lying. DERIVE LIVE; never trust a cached number.
- "Improve JARVIS" = more trustworthy / measurable / debuggable. Accuracy optimization only AFTER the measurement pipeline is proven sound.
- "Proven sound" = data persisted + queryable, data-quality flags separating clean from degraded, validated against the correct live source-of-truth, checked against a known baseline.

## Hard safety rules
- NEVER erase / overwrite / reset live bot data (`*.json`, `*.db` in `/root/jarvis/`). Backup (`cp f f.bak.$(date +%s)`) before any modify. No clean-slate without explicit confirmation.
- `webull_keys.py` does NOT exist on the box. Never request, view, or display it.
- Secrets live in `jarvis_secrets.py` / `secrets.json`. No secrets hardcoded. Telegram: `TG_TOKEN_TRADER` / `TG_CHAT_ID` via `__import__("jarvis_secrets")`.

## Box facts
- [UNVERIFIED] VPS: DigitalOcean Ubuntu, 68.183.107.46, root at `/root/jarvis/`, ~15 bots (nohup + cron).
- [UNVERIFIED] CVE-2026-31431 ("Copy Fail") affects the kernel — patch + reboot still pending.
- [UNVERIFIED] All bots use model `claude-sonnet-4-6`.
