"""
jarvis_options_brain_v2.py — Conflict Detector + Unified Stance Engine
Drop this into your existing options brain. Call get_unified_stance() before
generating any brief output. It replaces contradictory multi-signal outputs
with a single, defensible trade stance.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Stance(Enum):
    BUY_CALLS       = "BUY_CALLS"
    SELL_PUTS       = "SELL_PUTS"
    BUY_DEBIT_SPREAD = "BUY_DEBIT_SPREAD"
    SELL_CREDIT_SPREAD = "SELL_CREDIT_SPREAD"
    WAIT            = "WAIT"
    NEUTRAL         = "NEUTRAL"


class ConflictFlag(Enum):
    VIX_VS_SELL_PREMIUM   = "VIX_LOW_BUT_SELL_SIGNAL"       # VIX < 18, signal says sell
    VIX_VS_BUY_PREMIUM    = "VIX_HIGH_BUT_BUY_SIGNAL"       # VIX > 25, signal says buy
    FEAR_VS_VIX_MISMATCH  = "FEAR_GREED_VIX_DISAGREE"       # Extreme fear but VIX calm
    REGIME_VS_STANCE      = "REGIME_CONTRADICTS_STANCE"     # Regime bearish but signal bullish
    WAIT_VS_AGGRESSIVE    = "WAIT_AND_AGGRESSIVE_BOTH_SET"  # Brief says wait AND act


@dataclass
class MarketInputs:
    vix: float
    fear_greed: int          # 0–100; <=25 = extreme fear, >=75 = extreme greed
    regime: str              # "TRENDING_UP" | "RANGING" | "TRENDING_DOWN" | "VOLATILE"
    raw_signal: Stance       # What the individual signal modules voted
    iv_percentile: Optional[float] = None   # 0–100 if available; None = use VIX proxy


@dataclass
class StanceResult:
    stance: Stance
    confidence: str          # "HIGH" | "MEDIUM" | "LOW"
    conflicts: list[ConflictFlag]
    rationale: str
    brief_line: str          # Single line for Telegram brief


# ── VIX / IV thresholds ──────────────────────────────────────────────────────
VIX_CHEAP   = 18.0   # Below = IV cheap = favor buying, not selling naked premium
VIX_RICH    = 24.0   # Above = IV elevated = selling premium has real edge
VIX_EXTREME = 30.0   # Above = fear spike, sell puts only with defined risk (spreads)

FEAR_EXTREME_LOW  = 25   # <= this = extreme fear
FEAR_EXTREME_HIGH = 75   # >= this = extreme greed


def _detect_conflicts(inputs: MarketInputs) -> list[ConflictFlag]:
    flags = []

    # VIX low but signal wants naked premium selling
    if inputs.vix < VIX_CHEAP and inputs.raw_signal in (Stance.SELL_PUTS,):
        flags.append(ConflictFlag.VIX_VS_SELL_PREMIUM)

    # VIX elevated but signal wants to buy premium (fine, but note it)
    if inputs.vix > VIX_RICH and inputs.raw_signal in (Stance.BUY_CALLS,):
        flags.append(ConflictFlag.VIX_VS_BUY_PREMIUM)

    # Fear/Greed extreme fear + VIX calm = data source mismatch
    if inputs.fear_greed <= FEAR_EXTREME_LOW and inputs.vix < VIX_CHEAP:
        flags.append(ConflictFlag.FEAR_VS_VIX_MISMATCH)

    # Regime bearish but stance is bullish calls/sell puts
    if inputs.regime == "TRENDING_DOWN" and inputs.raw_signal in (
        Stance.BUY_CALLS, Stance.SELL_PUTS
    ):
        flags.append(ConflictFlag.REGIME_VS_STANCE)

    return flags


def get_unified_stance(inputs: MarketInputs) -> StanceResult:
    """
    Core resolver. Takes all market inputs + raw signal vote,
    detects conflicts, and returns ONE defensible stance.
    """
    conflicts = _detect_conflicts(inputs)
    iv = inputs.iv_percentile  # May be None

    # ── RESOLVE ──────────────────────────────────────────────────────────────

    # Rule 1: VIX extreme spike — only defined-risk structures
    if inputs.vix >= VIX_EXTREME:
        stance = Stance.SELL_CREDIT_SPREAD if inputs.fear_greed <= FEAR_EXTREME_LOW else Stance.WAIT
        return StanceResult(
            stance=stance,
            confidence="MEDIUM",
            conflicts=conflicts,
            rationale=f"VIX {inputs.vix:.1f} — extreme spike. Naked premium dangerous. "
                      f"Defined-risk credit spread only or sit out.",
            brief_line=f"⚠️ VIX SPIKE {inputs.vix:.1f} — CREDIT SPREADS ONLY or WAIT"
        )

    # Rule 2: Low VIX (cheap IV) — don't sell naked, use spreads or buy
    if inputs.vix < VIX_CHEAP:
        if ConflictFlag.FEAR_VS_VIX_MISMATCH in conflicts:
            # Data conflict — safest to wait
            return StanceResult(
                stance=Stance.WAIT,
                confidence="LOW",
                conflicts=conflicts,
                rationale=f"Conflict: Fear/Greed shows extreme fear ({inputs.fear_greed}) "
                          f"but VIX {inputs.vix:.1f} is calm. Data sources disagree. "
                          f"No edge — wait for confirmation.",
                brief_line=f"🚫 DATA CONFLICT (Fear={inputs.fear_greed} vs VIX={inputs.vix:.1f}) — WAIT"
            )

        if inputs.regime == "TRENDING_UP":
            stance = Stance.BUY_CALLS
            return StanceResult(
                stance=stance,
                confidence="MEDIUM",
                conflicts=conflicts,
                rationale=f"VIX {inputs.vix:.1f} (cheap IV) + uptrend regime. "
                          f"Buying calls has better R/R than selling thin premium.",
                brief_line=f"📈 BUY CALLS — VIX {inputs.vix:.1f} cheap, regime trending up"
            )

        if inputs.regime == "RANGING":
            stance = Stance.BUY_DEBIT_SPREAD
            return StanceResult(
                stance=stance,
                confidence="MEDIUM",
                conflicts=conflicts,
                rationale=f"VIX {inputs.vix:.1f} (cheap IV) + ranging market. "
                          f"Debit spreads: defined risk, low cost, no naked exposure.",
                brief_line=f"↔️ DEBIT SPREADS — VIX cheap, no clear trend"
            )

        # Low VIX + downtrend or volatile = stay out
        return StanceResult(
            stance=Stance.WAIT,
            confidence="LOW",
            conflicts=conflicts,
            rationale=f"VIX {inputs.vix:.1f} cheap but regime is {inputs.regime}. "
                      f"No clean setup. Wait.",
            brief_line=f"⏸️ WAIT — VIX low + regime {inputs.regime} = no edge"
        )

    # Rule 3: VIX in rich zone — selling premium has real edge
    if inputs.vix >= VIX_RICH:
        if inputs.fear_greed <= FEAR_EXTREME_LOW and inputs.regime != "TRENDING_DOWN":
            stance = Stance.SELL_PUTS
            return StanceResult(
                stance=stance,
                confidence="HIGH",
                conflicts=conflicts,
                rationale=f"VIX {inputs.vix:.1f} elevated + extreme fear ({inputs.fear_greed}) "
                          f"+ regime not bearish. Classic put-selling setup.",
                brief_line=f"💰 SELL PUTS — VIX {inputs.vix:.1f} rich + fear={inputs.fear_greed} ✅"
            )

        if inputs.regime == "TRENDING_UP":
            stance = Stance.SELL_CREDIT_SPREAD
            return StanceResult(
                stance=stance,
                confidence="MEDIUM",
                conflicts=conflicts,
                rationale=f"VIX {inputs.vix:.1f} elevated + uptrend. Sell put credit spreads "
                          f"for defined risk premium collection.",
                brief_line=f"📉 SELL PUT SPREADS — VIX {inputs.vix:.1f} + uptrend"
            )

    # Rule 4: Middle zone (18–24), no clean signal
    return StanceResult(
        stance=Stance.WAIT,
        confidence="LOW",
        conflicts=conflicts,
        rationale=f"VIX {inputs.vix:.1f} in no-man's land. No dominant edge. "
                  f"Fear/Greed={inputs.fear_greed}, Regime={inputs.regime}. Wait.",
        brief_line=f"⏸️ WAIT — VIX {inputs.vix:.1f} neutral zone, no edge"
    )


# ── Brief formatter ───────────────────────────────────────────────────────────

def format_options_brief(result: StanceResult, vix: float, fear_greed: int, regime: str) -> str:
    """Replaces the contradictory brief with a single unified output."""
    conflict_lines = ""
    if result.conflicts:
        conflict_names = [c.value for c in result.conflicts]
        conflict_lines = f"\n⚡ CONFLICTS DETECTED: {', '.join(conflict_names)}"

    return f"""
JARVIS OPTIONS BRIEF
==========================
VIX: {vix:.1f} | Fear/Greed: {fear_greed} | Regime: {regime}
{conflict_lines}
==========================
UNIFIED STANCE: {result.stance.value}
Confidence: {result.confidence}
--------------------------
{result.rationale}
==========================
{result.brief_line}
""".strip()


# ── Example / smoke test ──────────────────────────────────────────────────────

if __name__ == "__main__":
    # Reproduces today's contradictory brief to show the fix
    inputs = MarketInputs(
        vix=15.3,
        fear_greed=18,          # Extreme fear
        regime="TRENDING_UP",
        raw_signal=Stance.SELL_PUTS,
    )

    result = get_unified_stance(inputs)
    print(format_options_brief(result, inputs.vix, inputs.fear_greed, inputs.regime))
    print()
    print("Conflicts found:", [c.value for c in result.conflicts])

    print("\n--- HIGH CONFIDENCE SELL PUTS scenario ---")
    inputs2 = MarketInputs(
        vix=26.0,
        fear_greed=20,
        regime="TRENDING_UP",
        raw_signal=Stance.SELL_PUTS,
    )
    result2 = get_unified_stance(inputs2)
    print(format_options_brief(result2, inputs2.vix, inputs2.fear_greed, inputs2.regime))
