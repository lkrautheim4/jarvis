# Proposed Changes: Fix Equity Fear & Greed Index

## Summary
The options system currently uses the crypto F&G from alternative.me (crypto sentiment) for equity trading decisions. This diff adds a separate CNN equity F&G fetch and updates all options brain reads to use the correct equity index.

## Changes

### 1. jarvis_macro.py

#### Add new function after `get_fear_greed_history()` (after line 79):

```python
def get_equity_fear_greed():
    """CNN equity Fear & Greed composite (0-100 scale).
    Returns None on any failure — never fallback to a default value."""
    try:
        r = SESSION.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            score = data.get("fear_and_greed", {}).get("score")
            if score is not None:
                return int(score)
    except Exception as e:
        log.warning(f"Equity F&G fetch failed: {e}")
    return None
```

#### Update `run_cycle()` to fetch and save equity F&G (around line 436):

**Before:**
```python
    with ThreadPoolExecutor(max_workers=6) as ex:
        f_vix    = ex.submit(get_vix)
        f_fg     = ex.submit(get_fear_greed_history)
        f_yield  = ex.submit(get_treasury_yield)
        f_pcr    = ex.submit(get_put_call_ratio)
        f_corr   = ex.submit(calculate_correlations)
        f_events = ex.submit(get_macro_events)

    vix         = f_vix.result()
    fg          = f_fg.result()
```

**After:**
```python
    with ThreadPoolExecutor(max_workers=7) as ex:
        f_vix    = ex.submit(get_vix)
        f_fg     = ex.submit(get_fear_greed_history)
        f_eq_fg  = ex.submit(get_equity_fear_greed)
        f_yield  = ex.submit(get_treasury_yield)
        f_pcr    = ex.submit(get_put_call_ratio)
        f_corr   = ex.submit(calculate_correlations)
        f_events = ex.submit(get_macro_events)

    vix         = f_vix.result()
    fg          = f_fg.result()
    eq_fg       = f_eq_fg.result()
```

#### Save equity F&G to brain table (after line 501, just before `compute_market_mode()`):

```python
    # Write equity F&G to brain table (separate from crypto F&G)
    if eq_fg is not None:
        try:
            import jarvis_memory_db as _memdb
            _memdb.brain_set("equity_fear_greed", eq_fg)
            log.info(f"Equity F&G updated: {eq_fg}")
        except Exception as _e:
            log.error(f"equity_fear_greed brain write: {_e}")
```

### 2. jarvis_options_brain.py

#### Update `get_all_context()` to fetch equity F&G from brain (around line 256):

**Before:**
```python
def get_all_context():
    """Load everything JARVIS knows"""
    ctx = {}
    for name, path in [
        ("macro", "/root/jarvis/jarvis_macro.json"),
        ("beast", "/root/jarvis/jarvis_beast_brain.json"),
        ("congress", "/root/jarvis/jarvis_congress.json"),
        ("earnings", "/root/jarvis/jarvis_earnings.json"),
        ("brain", "/root/jarvis/jarvis_central_brain.json"),
        ("intel", "/root/jarvis/jarvis_intel.json"),
    ]:
        try: ctx[name] = json.load(open(path))
        except: ctx[name] = {}
    # Regime comes from the canonical jarvis_memory.db brain (fresh intraday), not
    # the cached macro.json value — every downstream ctx.macro.regime read uses it.
    if DB_ENABLED:
        try:
            if not isinstance(ctx.get("macro"), dict):
                ctx["macro"] = {}
            ctx["macro"]["regime"] = memdb.get_regime(ctx["macro"].get("regime", "UNKNOWN"))
        except Exception:
            pass
    return ctx
```

**After:**
```python
def get_all_context():
    """Load everything JARVIS knows"""
    ctx = {}
    for name, path in [
        ("macro", "/root/jarvis/jarvis_macro.json"),
        ("beast", "/root/jarvis/jarvis_beast_brain.json"),
        ("congress", "/root/jarvis/jarvis_congress.json"),
        ("earnings", "/root/jarvis/jarvis_earnings.json"),
        ("brain", "/root/jarvis/jarvis_central_brain.json"),
        ("intel", "/root/jarvis/jarvis_intel.json"),
    ]:
        try: ctx[name] = json.load(open(path))
        except: ctx[name] = {}
    # Regime comes from the canonical jarvis_memory.db brain (fresh intraday), not
    # the cached macro.json value — every downstream ctx.macro.regime read uses it.
    if DB_ENABLED:
        try:
            if not isinstance(ctx.get("macro"), dict):
                ctx["macro"] = {}
            ctx["macro"]["regime"] = memdb.get_regime(ctx["macro"].get("regime", "UNKNOWN"))
            
            # Fetch equity F&G from brain table — if missing or stale (>2h), treat as unavailable (50).
            eq_fg_data = memdb.brain_get("equity_fear_greed")
            if eq_fg_data is not None:
                # Check staleness — brain_get returns just the value, need updated_at separately
                with memdb.get_conn() as conn:
                    row = conn.execute("SELECT updated_at FROM brain WHERE key=?", ("equity_fear_greed",)).fetchone()
                    if row:
                        updated = datetime.fromisoformat(row["updated_at"])
                        age_hours = (datetime.now() - updated).total_seconds() / 3600
                        if age_hours <= 2:
                            if not isinstance(ctx.get("brain"), dict):
                                ctx["brain"] = {}
                            ctx["brain"]["fear_greed"] = eq_fg_data
                        else:
                            log.warning(f"equity_fear_greed stale ({age_hours:.1f}h old) — using neutral 50")
                            if not isinstance(ctx.get("brain"), dict):
                                ctx["brain"] = {}
                            ctx["brain"]["fear_greed"] = 50
                    else:
                        log.warning("equity_fear_greed exists but no updated_at — using neutral 50")
                        if not isinstance(ctx.get("brain"), dict):
                            ctx["brain"] = {}
                        ctx["brain"]["fear_greed"] = 50
            else:
                log.warning("equity_fear_greed missing from brain — using neutral 50")
                if not isinstance(ctx.get("brain"), dict):
                    ctx["brain"] = {}
                ctx["brain"]["fear_greed"] = 50
        except Exception as e:
            log.warning(f"equity_fear_greed fetch failed: {e} — using neutral 50")
            if not isinstance(ctx.get("brain"), dict):
                ctx["brain"] = {}
            ctx["brain"]["fear_greed"] = 50
    return ctx
```

## Files NOT Modified

- `jarvis_brain.py` — crypto bots use this, crypto F&G is correct
- `jarvis_brain_v2.py` — crypto bots use this, crypto F&G is correct
- `jarvis_memory.db` — no direct data writes (backups exist)
- Any BTC/crypto bot files

## Testing Plan

1. Restart jarvis_macro.py — verify equity F&G appears in logs
2. Query brain table: `SELECT key, value, updated_at FROM brain WHERE key = 'equity_fear_greed';`
3. Check jarvis_options_brain.py logs for "equity_fear_greed" fetches
4. Verify current crypto F&G (~9) and CNN equity F&G (~29) are both being used correctly

## Rollback

If issues occur:
1. Stop both bots
2. Restore from .bak.2026-06-10 files
3. Previous behavior: both use crypto F&G (wrong but functional)
