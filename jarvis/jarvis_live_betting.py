#!/usr/bin/env python3
"""
jarvis_live_betting.py — Live Kalshi Bet Execution Engine
Tier sizing, confirmation mode, halt conditions, bankroll tracking.

LIVE_MODE = False → sends confirmation Telegram, waits for GO
LIVE_MODE = True  → fully autonomous, bets immediately

Tier system:
  Tier 1 (default/after loss): max $5
  Tier 2 (2+ consecutive wins): max $10
  Tier 3 (5+ consecutive wins): max $15
  Hard cap: 10% of current bankroll, never > $15 until bankroll > $300
"""

import json, os, logging, time
from datetime import datetime

log = logging.getLogger("jarvis_live_betting")

# ── Config ───────────────────────────────────────────────────────────────────

LIVE_MODE         = False   # flip True when ready for autonomous betting
STARTING_BANKROLL = 150.0
BRAIN_FILE        = "/root/jarvis/jarvis_live_betting.json"

# Confidence floors — live money, tighter than paper
YES_FLOOR         = 0.70   # 70% minimum for YES
NO_FLOOR          = 0.85   # 85% minimum for NO
RANGING_NO_FLOOR  = 0.90   # 90% in ranging markets
MIN_EV            = 0.08   # 8% minimum edge

# Halt conditions
MAX_CONSEC_LOSSES = 3      # halt after 3 consecutive losses
MAX_DAILY_LOSS_PCT = 0.30  # halt if down 30% in one day

# Tier sizing
TIERS = {
    1: {"min_streak": 0, "max_bet": 5.0,  "label": "Tier 1 — Conservative"},
    2: {"min_streak": 2, "max_bet": 10.0, "label": "Tier 2 — Moderate"},
    3: {"min_streak": 5, "max_bet": 15.0, "label": "Tier 3 — Confident"},
}

# Avoid hours EDT
AVOID_HOURS = [11, 18, 19, 20, 21, 22, 23]

# Pending confirmation store (in-memory)
_pending_bet = {}

# ── Brain I/O ────────────────────────────────────────────────────────────────

def load_betting_brain() -> dict:
    try:
        return json.load(open(BRAIN_FILE))
    except:
        return {
            "bankroll":          STARTING_BANKROLL,
            "starting_bankroll": STARTING_BANKROLL,
            "total_bets":        0,
            "total_wins":        0,
            "total_losses":      0,
            "consecutive_wins":  0,
            "consecutive_losses":0,
            "daily_start_balance": STARTING_BANKROLL,
            "daily_date":        "",
            "daily_losses":      0.0,
            "halted":            False,
            "halt_reason":       "",
            "total_pnl":         0.0,
            "bets":              [],
            "tier":              1,
        }

def save_betting_brain(brain: dict):
    tmp = BRAIN_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(brain, f, indent=2)
    os.replace(tmp, BRAIN_FILE)

# ── Tier calculation ─────────────────────────────────────────────────────────

def get_current_tier(brain: dict) -> int:
    streak = brain.get("consecutive_wins", 0)
    if streak >= 5: return 3
    if streak >= 2: return 2
    return 1

def get_max_bet(brain: dict) -> float:
    tier = get_current_tier(brain)
    tier_max = TIERS[tier]["max_bet"]
    bankroll = brain.get("bankroll", STARTING_BANKROLL)
    # Hard cap: 10% of bankroll, never > $15 until bankroll > $300
    bankroll_cap = bankroll * 0.10
    if bankroll <= 300:
        return min(tier_max, bankroll_cap, 15.0)
    else:
        return min(tier_max * 2, bankroll_cap, 25.0)

# ── Daily reset ──────────────────────────────────────────────────────────────

def check_daily_reset(brain: dict) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    if brain.get("daily_date") != today:
        brain["daily_date"]          = today
        brain["daily_start_balance"] = brain.get("bankroll", STARTING_BANKROLL)
        brain["daily_losses"]        = 0.0
        brain["consecutive_losses"]  = 0
        # Auto-clear streak halt at midnight — preserve manual halts
        if brain.get("halted") and "consecutive losses" in brain.get("halt_reason", ""):
            brain["halted"]      = False
            brain["halt_reason"] = ""
    return brain

# ── Halt checks ─────────────────────────────────────────────────────────────

def is_halted(brain: dict) -> tuple:
    if brain.get("halted"):
        return True, brain.get("halt_reason", "Manual halt")
    if brain.get("consecutive_losses", 0) >= MAX_CONSEC_LOSSES:
        return True, f"{MAX_CONSEC_LOSSES} consecutive losses"
    daily_start = brain.get("daily_start_balance", STARTING_BANKROLL)
    daily_loss = daily_start - brain.get("bankroll", STARTING_BANKROLL)
    if daily_start > 0 and daily_loss / daily_start >= MAX_DAILY_LOSS_PCT:
        return True, f"Daily loss limit hit ({daily_loss:.0f} = {daily_loss/daily_start:.0%})"
    return False, ""

def is_avoid_hour() -> bool:
    edt_hour = (datetime.utcnow().hour - 4) % 24
    return edt_hour in AVOID_HOURS

def mins_left_in_hour() -> int:
    return 60 - datetime.now().minute

# ── EV calculation ───────────────────────────────────────────────────────────

def compute_ev(prob: float, yes_price: float, bet: str) -> float:
    try:
        if bet == "YES":
            return (prob * (1 - yes_price)) - ((1 - prob) * yes_price)
        elif bet == "NO":
            no_price = 1 - yes_price
            return ((1 - prob) * yes_price) - (prob * no_price)
        return 0.0
    except:
        return 0.0

# ── Main entry point — called from run_hourly_prediction ────────────────────

def evaluate_and_bet(bet: str, prob_str: str, target: float, yes_price: float,
                     market_ticker: str, reason: str, regime: str,
                     tg_func, tg_pred_token=None) -> dict:
    """
    Full bet evaluation pipeline.
    Returns dict with action taken and reason.
    """
    brain = load_betting_brain()
    brain = check_daily_reset(brain)

    result = {"action": "SKIP", "reason": "", "amount": 0}

    # Parse prob
    try:
        prob = float(prob_str.replace("%","").replace("?","50")) / 100
    except:
        prob = 0.5

    # Time gate
    if is_avoid_hour():
        edt_hour = (datetime.utcnow().hour - 4) % 24
        result["reason"] = f"Avoid hour {edt_hour} EDT"
        log.info(f"Bet blocked: {result['reason']}")
        return result

    mins = mins_left_in_hour()
    if mins < 20:
        result["reason"] = f"Only {mins}min left — too late"
        log.info(f"Bet blocked: {result['reason']}")
        return result

    # Halt check
    halted, halt_reason = is_halted(brain)
    if halted:
        result["reason"] = f"HALTED: {halt_reason}"
        log.warning(f"Bet blocked: {result['reason']}")
        tg_func(f"🛑 ORACLE HALTED\n{halt_reason}\nSend RESUME to restart")
        return result

    # Confidence floors
    if bet == "YES":
        floor = YES_FLOOR
    elif bet == "NO":
        floor = RANGING_NO_FLOOR if regime == "RANGING" else NO_FLOOR
    else:
        result["reason"] = "SKIP signal"
        return result

    if prob < floor:
        result["reason"] = f"{bet} blocked: {prob:.0%} < {floor:.0%} floor"
        log.info(f"Bet blocked: {result['reason']}")
        return result

    # EV check
    ev = compute_ev(prob, yes_price, bet)
    if ev < MIN_EV:
        result["reason"] = f"EV {ev:.1%} < {MIN_EV:.0%} minimum"
        log.info(f"Bet blocked: {result['reason']}")
        return result

    # Size the bet
    max_bet = get_max_bet(brain)
    tier = get_current_tier(brain)
    tier_label = TIERS[tier]["label"]

    # Kelly sizing
    b = (1 - yes_price) / yes_price if bet == "YES" else yes_price / (1 - yes_price)
    p = prob if bet == "YES" else 1 - prob
    q = 1 - p
    kelly_f = max(0, min(0.25, (b * p - q) / b))
    bankroll = brain.get("bankroll", STARTING_BANKROLL)
    kelly_size = round(kelly_f * bankroll, 2)
    bet_size = min(kelly_size, max_bet)
    bet_size = max(bet_size, 3.0)  # minimum $3
    bet_size = round(bet_size, 0)

    streak = brain.get("consecutive_wins", 0)
    consec_losses = brain.get("consecutive_losses", 0)

    if LIVE_MODE:
        # Fully autonomous — place immediately
        try:
            import kalshi_auth
            place_result = kalshi_auth.place_bet(market_ticker, bet, bet_size)
            if place_result:
                brain["total_bets"] += 1
                brain["bets"].append({
                    "ts":       datetime.now().isoformat(),
                    "bet":      bet,
                    "target":   target,
                    "amount":   bet_size,
                    "ev":       round(ev, 4),
                    "prob":     round(prob, 3),
                    "ticker":   market_ticker,
                    "tier":     tier,
                    "result":   None,
                    "pnl":      None,
                })
                save_betting_brain(brain)
                tg_func(
                    f"✅ BET PLACED — {tier_label}\n"
                    f"{bet} ${bet_size:.0f} on BTC>${target:,.0f}\n"
                    f"Prob: {prob:.0%} | EV: {ev:+.1%}\n"
                    f"Bankroll: ${bankroll:.0f} | Streak: {streak}W/{consec_losses}L\n"
                    f"{reason}"
                )
                result["action"] = "BET"
                result["amount"] = bet_size
                log.info(f"LIVE BET PLACED: {bet} ${bet_size} EV={ev:+.1%}")
            else:
                log.error("place_bet returned None")
                result["reason"] = "Bet placement failed"
        except Exception as e:
            log.error(f"Live bet error: {e}")
            result["reason"] = str(e)
    else:
        # Confirmation mode — store pending and alert
        global _pending_bet
        _pending_bet = {
            "bet":      bet,
            "amount":   bet_size,
            "target":   target,
            "ticker":   market_ticker,
            "ev":       ev,
            "prob":     prob,
            "tier":     tier,
            "reason":   reason,
            "ts":       datetime.now().isoformat(),
        }
        tg_func(
            f"🔔 CONFIRM BET?\n"
            f"{'='*22}\n"
            f"{bet} ${bet_size:.0f} on BTC>${target:,.0f}\n"
            f"Prob: {prob:.0%} | EV: {ev:+.1%}\n"
            f"{tier_label} | Streak: {streak}W/{consec_losses}L\n"
            f"Bankroll: ${bankroll:.0f}\n"
            f"{'='*22}\n"
            f"{reason}\n"
            f"{'='*22}\n"
            f"Reply GO to confirm or SKIP to pass"
        )
        result["action"] = "PENDING"
        result["amount"] = bet_size
        log.info(f"Confirmation sent: {bet} ${bet_size} EV={ev:+.1%}")

    return result

# ── GO command handler — confirm pending bet ─────────────────────────────────

def confirm_bet(tg_func) -> bool:
    global _pending_bet
    if not _pending_bet:
        tg_func("No pending bet to confirm.")
        return False

    p = _pending_bet
    age = (datetime.now() - datetime.fromisoformat(p["ts"])).total_seconds()
    if age > 600:  # 10 min expiry
        tg_func("⏰ Bet expired — too much time passed. Wait for next Oracle signal.")
        _pending_bet = {}
        return False

    mins = mins_left_in_hour()
    if mins < 5:
        tg_func(f"⏰ Only {mins}min left — too late to place. Wait for next hour.")
        _pending_bet = {}
        return False

    try:
        import kalshi_auth
        result = kalshi_auth.place_bet(p["ticker"], p["bet"], p["amount"])
        brain = load_betting_brain()
        if result:
            brain["total_bets"] += 1
            brain["bets"].append({
                "ts":     datetime.now().isoformat(),
                "bet":    p["bet"],
                "target": p["target"],
                "amount": p["amount"],
                "ev":     round(p["ev"], 4),
                "prob":   round(p["prob"], 3),
                "ticker": p["ticker"],
                "tier":   p["tier"],
                "result": None,
                "pnl":    None,
            })
            save_betting_brain(brain)
            tg_func(
                f"✅ BET CONFIRMED & PLACED\n"
                f"{p['bet']} ${p['amount']:.0f} on BTC>${p['target']:,.0f}\n"
                f"EV: {p['ev']:+.1%} | Prob: {p['prob']:.0%}\n"
                f"Bankroll: ${brain['bankroll']:.0f}"
            )
            log.info(f"CONFIRMED BET: {p['bet']} ${p['amount']} ticker={p['ticker']}")
            _pending_bet = {}
            return True
        else:
            tg_func("❌ Bet placement failed — Kalshi API error")
            _pending_bet = {}
            return False
    except Exception as e:
        tg_func(f"❌ Bet error: {e}")
        log.error(f"Confirm bet error: {e}")
        _pending_bet = {}
        return False

# ── WIN/LOSS recording ───────────────────────────────────────────────────────

def record_result(won: bool, pnl: float, tg_func):
    """Call this when a bet resolves."""
    brain = load_betting_brain()
    brain["total_wins" if won else "total_losses"] += 1
    brain["total_pnl"] = round(brain.get("total_pnl", 0) + pnl, 2)
    brain["bankroll"]  = round(brain.get("bankroll", STARTING_BANKROLL) + pnl, 2)

    if won:
        brain["consecutive_wins"]   = brain.get("consecutive_wins", 0) + 1
        brain["consecutive_losses"] = 0
        # Auto-clear streak-based halt on WIN (manual halts require explicit RESUME)
        if brain.get("halted") and "consecutive losses" in brain.get("halt_reason", ""):
            brain["halted"]      = False
            brain["halt_reason"] = ""
            tg_func("✅ Streak halt auto-cleared — next graded win lifted the block")
    else:
        brain["consecutive_losses"] = brain.get("consecutive_losses", 0) + 1
        brain["consecutive_wins"]   = 0
        brain["daily_losses"]       = brain.get("daily_losses", 0) + abs(pnl)

    # Update most recent bet
    for b in reversed(brain["bets"]):
        if b.get("result") is None:
            b["result"] = "WIN" if won else "LOSS"
            b["pnl"]    = pnl
            break

    tier = get_current_tier(brain)
    halted, halt_reason = is_halted(brain)
    if halted:
        brain["halted"]      = True
        brain["halt_reason"] = halt_reason
        tg_func(
            f"🛑 BETTING HALTED\n"
            f"{halt_reason}\n"
            f"Bankroll: ${brain['bankroll']:.0f}\n"
            f"Send RESUME to restart"
        )

    save_betting_brain(brain)

    total = brain["total_wins"] + brain["total_losses"]
    wr = round(brain["total_wins"] / total * 100) if total else 0
    emoji = "✅" if won else "❌"

    tg_func(
        f"{emoji} BET {'WIN' if won else 'LOSS'}\n"
        f"P&L: ${pnl:+.2f} | Bankroll: ${brain['bankroll']:.0f}\n"
        f"Record: {brain['total_wins']}W/{brain['total_losses']}L ({wr}% WR)\n"
        f"Streak: {brain['consecutive_wins']}W/{brain['consecutive_losses']}L\n"
        f"Now on {TIERS[tier]['label']}"
    )

# ── RESUME command ───────────────────────────────────────────────────────────

def resume_betting(tg_func):
    brain = load_betting_brain()
    brain["halted"]             = False
    brain["halt_reason"]        = ""
    brain["consecutive_losses"] = 0
    save_betting_brain(brain)
    tg_func(f"✅ Betting resumed\nBankroll: ${brain['bankroll']:.0f}\nBack to Tier 1 — conservative sizing")

# ── STATUS command ───────────────────────────────────────────────────────────

def betting_status(tg_func):
    brain = load_betting_brain()
    total = brain["total_wins"] + brain["total_losses"]
    wr    = round(brain["total_wins"] / total * 100) if total else 0
    tier  = get_current_tier(brain)
    halted, halt_reason = is_halted(brain)
    roi   = round((brain["bankroll"] - brain["starting_bankroll"]) / brain["starting_bankroll"] * 100, 1)

    tg_func(
        f"💰 LIVE BETTING STATUS\n"
        f"{'='*22}\n"
        f"Bankroll: ${brain['bankroll']:.0f} (ROI: {roi:+.1f}%)\n"
        f"Record: {brain['total_wins']}W/{brain['total_losses']}L ({wr}% WR)\n"
        f"P&L: ${brain['total_pnl']:+.2f}\n"
        f"{'='*22}\n"
        f"Current tier: {TIERS[tier]['label']}\n"
        f"Max bet: ${get_max_bet(brain):.0f}\n"
        f"Streak: {brain['consecutive_wins']}W / {brain['consecutive_losses']}L\n"
        f"Mode: {'🤖 AUTONOMOUS' if LIVE_MODE else '🔔 CONFIRMATION'}\n"
        f"Status: {'🛑 HALTED — ' + halt_reason if halted else '✅ Active'}\n"
        f"{'='*22}\n"
        f"Floors: YES≥70% | NO≥85% | EV≥8%\n"
        f"Halt: {MAX_CONSEC_LOSSES} losses or {MAX_DAILY_LOSS_PCT:.0%} daily drawdown"
    )

if __name__ == "__main__":
    print("Live betting engine ready")
    brain = load_betting_brain()
    print(f"Bankroll: ${brain['bankroll']}")
    print(f"Mode: {'LIVE' if LIVE_MODE else 'CONFIRMATION'}")
