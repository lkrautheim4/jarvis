# Equity Fear & Greed Integration

**Date**: 2026-06-10  
**Status**: COMPLETE

## Summary

Added CNN equity Fear & Greed index for options trading while keeping crypto F&G for BTC bots.

## Changes

### 1. New Fetcher (`jarvis_macro.py`)

```python
def get_equity_fear_greed():
    """Equity Fear & Greed from CNN"""
    # Fetches from production.dataviz.cnn.io/index/fearandgreed/graphdata
    # Browser UA, 10s timeout
    # Returns None on failure (never writes a default)
```

- Added to concurrent executor pool (7 workers)
- Writes to central brain as `equity_fear_greed` key
- Format: `{"value": 27, "ts": "2026-06-10T23:45:44.800676"}`

### 2. Options Brain Reader (`jarvis_options_brain.py`)

```python
def get_equity_fear_greed(ctx):
    """Read equity F&G with 2-hour staleness guard"""
    # Returns (value, warning_msg)
    # Falls back to neutral 50 + warning if stale/missing
```

**Staleness Guard**:
- Age > 2 hours → fallback to 50 + warning
- Missing from brain → fallback to 50 + warning  
- Parse error → fallback to 50 + warning

**Integration Points** (6 places):
- `score_setup()` - scoring logic
- `morning_brief()` - daily brief (shows warning if stale)
- `scan_and_alert()` - scanner
- DB signal insert (2 places)
- Paper trade logging

### 3. Secrets Fix (`jarvis_trade_advisor.py`)

Changed from `os.environ.get("ANTHROPIC_API_KEY")` to `jarvis_secrets.CLAUDE_API_KEY`

### 4. Secrets Module Fix (`jarvis_secrets.py`)

Fixed line 43: `get("sk-ant-api...")` → `get("CLAUDE_API_KEY")`

## What Was NOT Changed

- **Crypto F&G untouched**: BTC bots (`jarvis_brain.py`, `brain_v2`) still read `fear_greed` (crypto)
- **Kalshi bots untouched**: No F&G usage
- **Central brain structure**: `fear_greed` (crypto) and `equity_fear_greed` (stocks) coexist

## Testing

```bash
# Fetch test
curl -H "User-Agent: Mozilla/5.0" https://production.dataviz.cnn.io/index/fearandgreed/graphdata
# Returns: {"fear_and_greed": {"score": 27.45...}, ...}

# Macro cycle
python3 jarvis_macro.py
# Writes: {"equity_fear_greed": {"value": 27, "ts": "2026-06-10T..."}}

# Options brain
# Reads equity_fear_greed with staleness guard
# Falls back to 50 + warning if >2h old
```

## Files Modified

1. `/root/jarvis/jarvis_macro.py` - fetcher + brain write
2. `/root/jarvis/jarvis_options_brain.py` - reader + staleness guard + 6 integration points
3. `/root/jarvis/jarvis_trade_advisor.py` - secrets import
4. `/root/jarvis/jarvis_secrets.py` - fixed key lookup

## Next Steps

- Monitor equity F&G freshness (should update every 30min with macro cycle)
- Check options brief shows warning if F&G stale
- Verify crypto F&G still flowing to BTC bots correctly
