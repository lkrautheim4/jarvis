#!/usr/bin/env python3
"""
Shared data-freshness helpers.

jarvis_level5.py is the sole producer of sector rotation data. It already skips
RE-EMITTING an identical report when the market bars haven't advanced (the
last_sector_data_ts guard), but every DOWNSTREAM reader used to load
sector_scores from jarvis_level5.json with no age check — so a frozen Friday
close was served to trading bots and the morning brief all weekend / on holiday
mornings. These helpers gate sector data on the age of the freshest market bar
so decisions never act on stale numbers, and the brief can label them.

Freshness is measured against `last_sector_data_ts` (epoch of the freshest
regularMarketTime seen during the producing scan), NOT wall-clock of the last
poll — so a scan that re-ran against unchanged bars is still correctly "stale".
"""
import json
import os
import time

LEVEL5_FILE = "/root/jarvis/jarvis_level5.json"
MACRO_FILE  = "/root/jarvis/jarvis_macro.json"

# jarvis_macro is a cron one-shot every 2h (not a daemon). If its output is older
# than this, a cron cycle was missed (failure / reboot) and the regime is stale.
REGIME_FRESH_MAX_AGE = 3 * 3600

# Trust sector data only from the current session / a recent close. During
# market hours the freshest bar is <15min old (always fresh); after ~6h
# (overnight, weekend, holiday, Monday pre-market) it is treated as stale.
SECTOR_FRESH_MAX_AGE = 6 * 3600


def _read_level5():
    try:
        with open(LEVEL5_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def sector_scores_with_age(level5=None, max_age_s=SECTOR_FRESH_MAX_AGE):
    """Return (scores, age_seconds, is_fresh).

    `scores` is the RAW sector_scores dict (possibly stale) — callers that want
    to display data with a staleness label use this. `age_seconds` is None when
    no producer timestamp exists. `is_fresh` is True only when a timestamp
    exists and is within max_age_s.

    Pass an already-loaded level5 dict (e.g. the producer's in-process `data`)
    to avoid re-reading the file.
    """
    d = level5 if isinstance(level5, dict) else _read_level5()
    ts = d.get("last_sector_data_ts") or 0
    age = (time.time() - ts) if ts else None
    scores = d.get("sector_scores", {})
    if not isinstance(scores, dict):
        scores = {}
    is_fresh = age is not None and age <= max_age_s
    return scores, age, is_fresh


def fresh_sector_scores(level5=None, max_age_s=SECTOR_FRESH_MAX_AGE):
    """sector_scores for DECISIONS: the dict if fresh, else {} (no signal).

    Returning {} on stale/unknown data means a trading bot sees "no sector
    bias" rather than a frozen Friday number — fail safe, not fail stale.
    """
    scores, _age, is_fresh = sector_scores_with_age(level5, max_age_s)
    return scores if is_fresh else {}


def regime_with_age(max_age_s=REGIME_FRESH_MAX_AGE):
    """Return (regime, age_seconds, is_fresh) for the macro regime.

    Regime is sourced from the jarvis_memory.db brain table (the canonical store,
    kept fresh by jarvis_macro + jarvis_cascade), with age from the brain row's
    updated_at. Falls back to jarvis_macro.json mtime when the brain key isn't set.
    """
    try:
        import jarvis_memory_db as memdb
        regime, age = memdb.get_regime_with_age()
        if age is not None:
            return regime, age, age <= max_age_s
    except Exception:
        regime = None
    # Fallback: jarvis_macro.json mtime (pre-first-write).
    try:
        with open(MACRO_FILE) as f:
            d = json.load(f)
        if not regime:
            regime = d.get("regime", "UNKNOWN") if isinstance(d, dict) else "UNKNOWN"
        age = max(0.0, time.time() - os.path.getmtime(MACRO_FILE))
        return regime, age, age <= max_age_s
    except Exception:
        return (regime or "UNKNOWN"), None, False


def fresh_regime(max_age_s=REGIME_FRESH_MAX_AGE):
    """Regime for DECISIONS: the value if fresh, else 'UNKNOWN' (fail-safe)."""
    regime, _age, is_fresh = regime_with_age(max_age_s)
    return regime if is_fresh else "UNKNOWN"


def fmt_age(age_seconds):
    """Human label for a data age, e.g. '2.3h old' / 'age unknown'."""
    if age_seconds is None:
        return "age unknown"
    if age_seconds < 3600:
        return f"{int(age_seconds // 60)}m old"
    return f"{age_seconds / 3600:.1f}h old"
