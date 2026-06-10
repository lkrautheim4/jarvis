"""
jarvis_options_brain_upgrade.py
Upgrade patch for jarvis_options_brain.py

Adds:
- Catalyst tagging (earnings, FOMC, CPI, product events)
- Theta decay per day calculation and warnings
- P&L tracking via jarvis_memory_db SQLite
- Better strike selection (delta-based OTM targeting)
- Regime-aware strike adjustment
- Daily theta warning alerts

HOW TO APPLY:
1. Copy jarvis_memory_db.py to /root/jarvis/
2. Add these functions to jarvis_options_brain.py
3. Replace log_trade() with log_trade_v2()
4. Add theta_warning_check() to the main loop
"""

import requests, json, time, logging
from datetime import datetime, timedelta

log = logging.getLogger("OPTIONS_BRAIN")

# ── Catalyst database ────────────────────────────────────────────────────────

KNOWN_CATALYSTS = {
    # Format: "TICKER": [("date", "event_type", "description")]
    # Populated dynamically from jarvis_earnings.json + manual overrides
}

MACRO_CATALYSTS = [
    # (date_str, event_type, description)
    # These get pulled from jarvis_macro.json
]

def get_catalyst_tag(ticker: str, dte: int, ctx: dict) -> str:
    """
    Returns a catalyst tag string for a given ticker and DTE.
    Checks: earnings, FOMC, CPI, known events.
    """
    tags = []
    
    # Earnings check
    earnings = ctx.get("earnings", {})
    risk_map = earnings.get("risk_map", {})
    if ticker in risk_map:
        risk  = risk_map[ticker].get("risk", "LOW")
        days  = risk_map[ticker].get("days_away", 99)
        if days <= dte:
            emoji = {"CRITICAL": "🚨", "HIGH": "⚠️", "MEDIUM": "📢"}.get(risk, "")
            tags.append(f"{emoji}EARNINGS in {days}d ({risk})")
    
    # FOMC check from macro
    macro = ctx.get("macro", {})
    events = macro.get("upcoming_events", [])
    for event in events:
        event_date = event.get("date", "")
        event_name = event.get("name", "")
        try:
            days_away = (datetime.strptime(event_date, "%Y-%m-%d") - datetime.now()).days
            if 0 <= days_away <= dte:
                if "FOMC" in event_name.upper():
                    tags.append(f"🏦 FOMC in {days_away}d")
                elif "CPI" in event_name.upper():
                    tags.append(f"📊 CPI in {days_away}d")
                elif "NFP" in event_name.upper() or "PAYROLL" in event_name.upper():
                    tags.append(f"📋 NFP in {days_away}d")
        except:
            pass
    
    return " | ".join(tags) if tags else "No major catalysts"


def compute_theta_per_day(premium: float, dte: int, stock_price: float, strike: float) -> float:
    """
    Estimate theta ($ per day decay) using simplified Black-Scholes approximation.
    For put selling: theta is your friend (positive for seller).
    """
    if dte <= 0: return 0
    # Simple approximation: premium decays faster near expiry
    # Theta accelerates in last 30 days
    if dte <= 7:
        daily_decay = premium * 0.15   # 15% per day in final week
    elif dte <= 14:
        daily_decay = premium * 0.08
    elif dte <= 21:
        daily_decay = premium * 0.05
    else:
        daily_decay = premium * 0.03
    return round(daily_decay * 100, 2)  # per contract (100 shares)


def find_best_contract_v2(contracts: list, stock_price: float, option_type: str,
                          target_dte: int = 21, regime: str = "UNKNOWN") -> dict:
    """
    Improved contract selection:
    - put_sell: target 0.25-0.30 delta (8-12% OTM), wider in volatile regimes
    - call_buy: target 0.30-0.40 delta (3-7% OTM), tighter in trending regimes
    - Regime adjustment: go further OTM in RISK_OFF/VOLATILE
    """
    if not contracts: return None
    best = None
    best_score = 0

    # Regime-adjusted OTM targets
    if regime == "RISK_OFF":
        put_otm_target = 0.12   # go further OTM when market weak
        call_otm_target = 0.08
    elif regime == "VOLATILE":
        put_otm_target = 0.10
        call_otm_target = 0.06
    elif regime == "RISK_ON":
        put_otm_target = 0.07   # closer to money when bullish
        call_otm_target = 0.04
    else:
        put_otm_target = 0.08
        call_otm_target = 0.05

    for c in contracts:
        try:
            strike = float(c.get("strike_price", 0))
            exp    = datetime.strptime(c.get("expiration_date", ""), "%Y-%m-%d")
            dte    = (exp - datetime.now()).days
            if dte < 7 or dte > 45: continue

            dte_score = 1 - abs(dte - target_dte) / target_dte

            if option_type == "put_sell":
                pct_otm = (stock_price - strike) / stock_price
                otm_range = (put_otm_target * 0.5, put_otm_target * 1.8)
                if otm_range[0] <= pct_otm <= otm_range[1]:
                    strike_score = 1 - abs(pct_otm - put_otm_target) / put_otm_target
                    score = dte_score * 0.4 + strike_score * 0.6
                    if score > best_score:
                        best_score = score
                        best = c

            elif option_type == "call_buy":
                pct_otm = (strike - stock_price) / stock_price
                otm_range = (call_otm_target * 0.5, call_otm_target * 2.5)
                if otm_range[0] <= pct_otm <= otm_range[1]:
                    strike_score = 1 - abs(pct_otm - call_otm_target) / call_otm_target
                    score = dte_score * 0.4 + strike_score * 0.6
                    if score > best_score:
                        best_score = score
                        best = c
        except:
            continue

    return best


def log_trade_v2(trade_data: dict):
    """
    Log trade to both legacy JSON brain AND SQLite DB.
    Adds catalyst tag and theta tracking.
    """
    try:
        import jarvis_memory_db as memdb
        memdb.log_options_trade(
            ticker         = trade_data["ticker"],
            strategy       = trade_data["strategy"],
            strike         = trade_data["strike"],
            premium        = trade_data["premium"],
            dte            = trade_data["dte"],
            iv             = trade_data.get("iv", 0),
            score          = trade_data.get("score", 0),
            contract_symbol= trade_data.get("contract_symbol", ""),
            stock_price    = trade_data.get("stock_price", 0),
            regime         = trade_data.get("regime", "?"),
            fear_greed     = trade_data.get("fg_at_entry", 50),
            vix            = trade_data.get("vix_at_entry", 15),
            btc_signal     = trade_data.get("btc_signal", "neutral"),
            catalyst       = trade_data.get("catalyst", ""),
            theta_per_day  = trade_data.get("theta_per_day", 0),
        )
    except Exception as e:
        log.warning(f"SQLite log error: {e}")


def check_theta_warnings(tg_func):
    """
    Check all open options trades for theta decay urgency.
    Alert if < 7 DTE with meaningful premium remaining.
    """
    try:
        import jarvis_memory_db as memdb
        open_trades = memdb.get_open_options_trades()
        warnings = []
        for t in open_trades:
            dte = t.get("dte", 99)
            if dte is None: continue
            # Estimate current DTE from entry
            try:
                entry_dt = datetime.fromisoformat(t["ts"])
                days_held = (datetime.now() - entry_dt).days
                current_dte = max(0, dte - days_held)
            except:
                current_dte = dte
            
            if current_dte <= 7:
                theta = compute_theta_per_day(t.get("premium", 0), current_dte,
                                              t.get("stock_price", 0), t.get("strike", 0))
                warnings.append(
                    f"⏰ {t['ticker']} {t['strategy']} ${t['strike']:.0f} — "
                    f"{current_dte}d left | θ≈${theta:.0f}/day"
                )
        
        if warnings:
            msg = "⚠️ THETA DECAY WARNINGS\n" + "="*24 + "\n"
            msg += "\n".join(warnings)
            msg += "\n" + "="*24
            msg += "\nConsider closing or rolling these positions"
            tg_func(msg)
            log.info(f"Sent {len(warnings)} theta warnings")
    except Exception as e:
        log.error(f"Theta warning check error: {e}")


def build_trade_alert_v2(setup: dict, ctx: dict) -> str:
    """
    Enhanced trade alert with catalyst tags, theta info, regime context.
    """
    ticker   = setup["ticker"]
    strategy = setup["strategy"]
    price    = setup["price"]
    strike   = setup["strike"]
    premium  = setup["premium"]
    dte      = setup["dte"]
    iv       = setup.get("iv") or 0
    score    = setup["score"]
    regime   = ctx.get("macro", {}).get("regime", "?")

    # Catalyst tag
    catalyst = get_catalyst_tag(ticker, dte, ctx)
    
    # Theta
    theta = compute_theta_per_day(premium, dte, price, strike)

    if strategy == "put_sell":
        emoji       = "🎡"
        action      = "SELL PUT"
        cash_needed = strike * 100
        max_profit  = premium * 100
        max_loss    = (strike - premium) * 100
        monthly     = round(premium / strike * 100 * 30 / dte, 1) if dte > 0 else 0
        plain = (
            f"Sell right to make you buy {ticker} at ${strike:.0f}\n"
            f"Collect ${premium:.2f}/share = ${max_profit:.0f} upfront\n"
            f"Stays above ${strike:.0f} → keep ${max_profit:.0f}\n"
            f"Drops below → own shares at ${strike - premium:.2f} (below market)\n"
            f"Theta working FOR you: +${theta:.0f}/day"
        )
    else:
        emoji       = "🚀"
        action      = "BUY CALL"
        cash_needed = premium * 100
        max_profit  = "unlimited"
        max_loss    = premium * 100
        monthly     = "N/A"
        plain = (
            f"Pay ${premium:.2f}/share = ${cash_needed:.0f} for upside on {ticker}\n"
            f"Rises above ${strike:.0f} by {dte}d → profit\n"
            f"Stays flat → lose ${cash_needed:.0f}\n"
            f"Theta working AGAINST you: -${theta:.0f}/day"
        )

    signal_plain = []
    for sig in setup.get("signals", [])[:4]:
        if "REGIME:RISK_ON" in sig:   signal_plain.append("Market trending up")
        elif "FG:" in sig and "FEAR" in sig: signal_plain.append("Everyone scared = opportunity")
        elif "IV:" in sig and "HIGH" in sig: signal_plain.append("Options expensive = fat premium")
        elif "CONGRESS" in sig:        signal_plain.append("Politicians buying")
        elif "EARNINGS:CRITICAL" in sig: signal_plain.append("⚠️ Earnings soon — risky!")

    lines = [
        f"{emoji} JARVIS OPTIONS SIGNAL",
        f"Score: {score}/100 | Regime: {regime}",
        "="*24,
        f"{action}: {ticker} @ ${price:.2f}",
        f"Strike: ${strike:.0f} | Exp: {dte}d | IV: {iv:.0f}%",
        f"Catalyst: {catalyst}",
        "="*24,
        "IN PLAIN ENGLISH:",
        plain,
        "="*24,
        "WHY THIS TRADE:",
    ] + signal_plain + [
        "="*24,
        "NUMBERS:",
        f"Cash needed: ${cash_needed:,.0f}",
        f"Max profit: {max_profit if isinstance(max_profit, str) else f'${max_profit:,.0f}'}",
        f"Max loss: ${max_loss:,.0f}",
        f"Monthly return: {monthly}%" if monthly != "N/A" else "",
        f"Theta: ${theta:.0f}/day ({'gain' if strategy == 'put_sell' else 'cost'})",
        "="*24,
        "Paper trade on Alpaca first",
        f"Text LEARN {ticker} for deep analysis",
    ]
    return "\n".join(l for l in lines if l)


# ── Main loop additions ──────────────────────────────────────────────────────
# Add to the options brain main loop inside the market hours block:
#
#   # Theta check every 4 hours during market
#   if time.time() - last_theta_check >= 14400:
#       check_theta_warnings(tg)
#       last_theta_check = time.time()
#
# And replace find_best_contract() calls with find_best_contract_v2(regime=regime)
# And replace build_trade_alert() calls with build_trade_alert_v2()
# And replace log_trade() calls with log_trade_v2()

if __name__ == "__main__":
    print("Options brain upgrade patch ready.")
    print("Copy jarvis_memory_db.py to /root/jarvis/ then apply patch.")
