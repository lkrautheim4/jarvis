#!/usr/bin/env python3
"""
JARVIS REGIME CONFIDENCE SCORER
Adds confidence % to existing regime labels.
No HMM complexity — reads the same signals your
macro bot already tracks and scores agreement.

Drop-in: import and call get_regime_with_confidence(ctx)
instead of ctx["macro"]["regime"]
"""
import json, os, logging
from datetime import datetime

CONFIDENCE_FILE = "/root/jarvis/jarvis_regime_confidence.json"
MACRO_FILE      = "/root/jarvis/jarvis_macro.json"
CENTRAL_BRAIN   = "/root/jarvis/jarvis_central_brain.json"


def build_live_ctx():
    """Assemble a scorer context from the live macro + central-brain JSON files.
    Returns None if the macro file can't be read (caller falls back to test data)."""
    macro = json.load(open(MACRO_FILE))
    try:
        brain = json.load(open(CENTRAL_BRAIN))
    except Exception:
        brain = {}
    fg = macro.get("fear_greed")
    fg_val = fg.get("current") if isinstance(fg, dict) else fg
    return {
        "macro": {
            "regime":     macro.get("regime", "UNKNOWN"),
            "vix":        macro.get("vix", {}),
            "yield_10yr": macro.get("yield_10yr", {}),
            "yield_2yr":  macro.get("yield_2yr", {}),
        },
        "brain": {
            "fear_greed": fg_val if fg_val is not None else brain.get("fear_greed", 50),
            "btc_signal": brain.get("btc_signal", "neutral"),
        },
    }

log = logging.getLogger("REGIME_CONFIDENCE")

# ── SIGNAL WEIGHTS ─────────────────────────────────────────────────────────────
# How much each signal contributes to regime confidence (total = 100)
WEIGHTS = {
    "vix":       25,   # fear gauge — clearest single signal
    "fear_greed":20,   # retail sentiment
    "btc":       15,   # risk appetite proxy
    "yield":     15,   # bond market (smart money)
    "macro_label":25,  # your existing macro bot label
}

# ── SIGNAL → REGIME VOTE ───────────────────────────────────────────────────────
def vote_from_vix(vix):
    """VIX votes for regime"""
    if vix is None: return None, 0
    if vix < 13:   return "RISK_ON",     1.0
    if vix < 17:   return "RISK_ON",     0.7
    if vix < 20:   return "RISK_ON",     0.5   # low VIX = low fear = risk-on, not stagflation
    if vix < 25:   return "STAGFLATION", 0.5
    if vix < 30:   return "RISK_OFF",    0.7
    return               "RISK_OFF",     1.0

def vote_from_fg(fg):
    """Fear & Greed votes for regime"""
    if fg is None: return None, 0
    if fg > 70:   return "RISK_ON",     1.0
    if fg > 55:   return "RISK_ON",     0.7
    if fg > 40:   return "STAGFLATION", 0.5
    if fg > 25:   return "RISK_OFF",    0.7
    return               "RISK_OFF",    1.0

def vote_from_btc(btc_signal):
    """BTC momentum votes for regime (risk appetite)"""
    mapping = {
        "strongly_bullish": ("RISK_ON",     1.0),
        "bullish":          ("RISK_ON",     0.8),
        "neutral":          ("STAGFLATION", 0.5),
        "bearish":          ("RISK_OFF",    0.8),
        "strongly_bearish": ("RISK_OFF",    1.0),
    }
    return mapping.get(btc_signal, ("STAGFLATION", 0.3))

def vote_from_yield(yield_10yr, yield_2yr=None):
    """
    Yield curve votes:
    - Normal curve (10yr > 2yr) = RISK_ON
    - Flat / inverted = STAGFLATION / RISK_OFF
    """
    if yield_10yr is None: return None, 0
    if yield_2yr is not None:
        spread = yield_10yr - yield_2yr
        if spread > 0.5:  return "RISK_ON",     0.9
        if spread > 0.0:  return "RISK_ON",     0.6
        if spread > -0.3: return "STAGFLATION", 0.6
        return                   "RISK_OFF",    0.8
    else:
        # No 2yr — just use absolute 10yr level
        if yield_10yr < 3.5: return "RISK_ON",     0.6
        if yield_10yr < 4.5: return "STAGFLATION", 0.5
        return                      "RISK_OFF",    0.6

def vote_from_label(label):
    """Existing macro bot label gets full confidence in its own regime"""
    valid = {"RISK_ON", "RISK_OFF", "STAGFLATION", "RECOVERY"}
    if label in valid: return label, 1.0
    return "STAGFLATION", 0.3   # unknown = neutral

# ── CORE SCORER ────────────────────────────────────────────────────────────────
def get_regime_with_confidence(ctx):
    """
    Returns:
        regime (str)       — winning regime label
        confidence (int)   — 0-100, how aligned the signals are
        breakdown (dict)   — per-signal votes for transparency
        action (str)       — plain English sizing guidance
    """
    macro  = ctx.get("macro", {})
    brain  = ctx.get("brain", {})

    vix        = macro.get("vix", {}).get("value")
    _eq_fg_raw = brain.get("equity_fear_greed")
    if isinstance(_eq_fg_raw, dict) and _eq_fg_raw.get("value") is not None:
        try:
            from datetime import datetime, timezone
            _eq_age = (datetime.now(timezone.utc) - datetime.fromisoformat(_eq_fg_raw["ts"]).replace(tzinfo=timezone.utc)).total_seconds()
            fg = _eq_fg_raw["value"] if _eq_age < 7200 else 50
        except Exception:
            fg = 50
    else:
        fg = 50
    btc_signal = brain.get("btc_signal", "neutral")
    yield_10yr = macro.get("yield_10yr", {}).get("value")
    yield_2yr  = macro.get("yield_2yr",  {}).get("value")
    label      = macro.get("regime", "UNKNOWN")

    votes = {
        "vix":        (vote_from_vix(vix),               WEIGHTS["vix"]),
        "fear_greed": (vote_from_fg(fg),                  WEIGHTS["fear_greed"]),
        "btc":        (vote_from_btc(btc_signal),         WEIGHTS["btc"]),
        "yield":      (vote_from_yield(yield_10yr, yield_2yr), WEIGHTS["yield"]),
        "macro_label":(vote_from_label(label),            WEIGHTS["macro_label"]),
    }

    # Tally weighted votes per regime
    tally = {"RISK_ON": 0.0, "RISK_OFF": 0.0, "STAGFLATION": 0.0, "RECOVERY": 0.0}
    breakdown = {}
    total_weight = 0

    for signal_name, ((regime_vote, strength), weight) in votes.items():
        if regime_vote is None: continue
        contribution = weight * strength
        tally[regime_vote] = tally.get(regime_vote, 0) + contribution
        total_weight += weight
        breakdown[signal_name] = {
            "vote":         regime_vote,
            "strength":     round(strength, 2),
            "contribution": round(contribution, 1),
        }

    if total_weight == 0:
        return label or "UNKNOWN", 0, {}, "No signal data — sit out"

    # Winning regime
    winner = max(tally, key=tally.get)
    raw_confidence = tally[winner] / total_weight * 100

    # Agreement bonus: if ALL signals agree → boost confidence
    votes_for_winner = sum(1 for s in breakdown.values() if s["vote"] == winner)
    total_signals    = len(breakdown)
    agreement_ratio  = votes_for_winner / total_signals if total_signals > 0 else 0

    # Blend raw score with agreement ratio
    confidence = round(raw_confidence * 0.7 + agreement_ratio * 100 * 0.3)
    confidence = max(0, min(100, confidence))

    # ── SIZING GUIDANCE ────────────────────────────────────────────────────────
    if confidence >= 80:
        action = f"HIGH CONVICTION {winner} — full size, execute confidently"
    elif confidence >= 65:
        action = f"MODERATE {winner} — normal size, signals mostly agree"
    elif confidence >= 50:
        action = f"WEAK {winner} — half size, signals mixed"
    else:
        action = f"CONFLICTED — sit out or paper trade only ({votes_for_winner}/{total_signals} signals agree)"

    # Flag dangerous splits
    regimes_with_votes = [r for r, v in tally.items() if v > 0]
    if len(regimes_with_votes) >= 3:
        action += " ⚠️ 3-way split — reduce all risk"

    return winner, confidence, breakdown, action

def format_regime_block(ctx):
    """Ready-to-paste block for Telegram alerts and morning brief"""
    regime, conf, breakdown, action = get_regime_with_confidence(ctx)

    if conf >= 80:   bar = "████████░░"
    elif conf >= 65: bar = "██████░░░░"
    elif conf >= 50: bar = "████░░░░░░"
    else:            bar = "██░░░░░░░░"

    lines = [
        f"📡 REGIME: {regime} [{conf}%]",
        f"{bar}",
        f"{action}",
        f"Signal votes:",
    ]
    for sig, data in breakdown.items():
        arrow = "✓" if data["vote"] == regime else "✗"
        lines.append(f"  {arrow} {sig}: {data['vote']} (str {data['strength']:.1f})")

    return "\n".join(lines)

def save_confidence_to_brain(ctx, brain_file="/root/jarvis/jarvis_central_brain.json"):
    """Optionally persist confidence score into central brain for other bots to read"""
    regime, conf, breakdown, action = get_regime_with_confidence(ctx)
    try:
        brain = json.load(open(brain_file))
        brain["regime_confidence"]  = conf
        brain["regime_winner"]      = regime
        brain["regime_action"]      = action
        brain["regime_breakdown"]   = breakdown
        brain["regime_updated"]     = datetime.now().isoformat()
        with open(brain_file, 'w') as f: json.dump(brain, f, indent=2)
        log.info(f"Regime confidence saved: {regime} {conf}%")
    except Exception as e:
        log.error(f"Could not save regime confidence: {e}")

    # Always refresh the eponymous file so other bots (e.g. master's ADVISE) read
    # a current score, not a stale snapshot. Keys map onto that reader's schema
    # (label/score) while carrying the richer breakdown too. Atomic temp-rename.
    try:
        macro = ctx.get("macro", {})
        brain_ctx = ctx.get("brain", {})
        vix = macro.get("vix", {}).get("value")
        payload = {
            "ts":             datetime.now().isoformat(),
            "label":          regime,
            "score":          conf,
            # Consumer compat (master's ADVISE reads these): bet lean from regime,
            # reason = the scorer's plain-English action guidance.
            "recommendation": {"RISK_ON": "LEAN_YES", "RISK_OFF": "LEAN_NO"}.get(regime, "NEUTRAL"),
            "reason":         action,
            "regime_action":  action,
            "breakdown":      breakdown,
            "vix":            vix,
            "fear_greed":     brain_ctx.get("fear_greed"),
            "btc_signal":     brain_ctx.get("btc_signal"),
        }
        tmp = CONFIDENCE_FILE + ".tmp"
        with open(tmp, "w") as f: json.dump(payload, f, indent=2)
        os.replace(tmp, CONFIDENCE_FILE)
        log.info(f"Regime confidence file updated: {regime} {conf}%")
    except Exception as e:
        log.error(f"Could not write {CONFIDENCE_FILE}: {e}")

if __name__ == "__main__":
    # Run as the hourly cron entrypoint: score live regime and refresh
    # jarvis_regime_confidence.json. Falls back to dummy data only if the live
    # macro file is unavailable (e.g. first boot), so a cron run is never a no-op.
    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        ctx = build_live_ctx()
        log.info("Scoring live regime from macro + central brain")
    except Exception as e:
        log.warning(f"Live context unavailable ({e}) — using dummy test data")
        ctx = {
            "macro": {
                "regime":      "RISK_ON",
                "vix":         {"value": 14.2},
                "yield_10yr":  {"value": 4.31},
                "yield_2yr":   {"value": 3.95},
            },
            "brain": {
                "fear_greed":  68,
                "btc_signal":  "bullish",
            }
        }
    save_confidence_to_brain(ctx)
    regime, conf, breakdown, action = get_regime_with_confidence(ctx)
    print(format_regime_block(ctx))
