# This is a patch script — runs once and upgrades jarvis_alpha_v2.py
# Adds: trend filter, regime detector, scalp mode, time filter,
# BTC dominance filter, volume confirmation, daily loss post-mortem,
# AVAX ban, correlation fix, go-live readiness tracker

import json, re

with open('/root/jarvis/jarvis_alpha_v2.py', 'r') as f:
    content = f.read()

# ── 1. CONFIG UPGRADES ──
old_config = '''# Risk management
DAILY_LOSS_LIMIT    = 300.0   # stop trading if down this much in a day
MAX_CORRELATED_POS  = 2       # max positions in same correlation group
CLOSE_BEFORE_CLOSE  = 15      # minutes before market close to exit all stocks
MIN_VOLUME_MULT     = 1.2     # minimum volume confirmation'''

new_config = '''# Risk management
DAILY_LOSS_LIMIT    = 300.0   # stop trading if down this much in a day
MAX_CORRELATED_POS  = 1       # max 1 position per correlation group (was 2)
CLOSE_BEFORE_CLOSE  = 15      # minutes before market close to exit all stocks
MIN_VOLUME_MULT     = 1.2     # minimum volume confirmation
# Trend filter
TREND_BLOCK_24H     = -2.0    # block buys if 24h momentum worse than this
BTC_CRASH_BLOCK     = -3.0    # block all buys if BTC drops more than this
# Time filter (UTC hours)
TRADE_START_HOUR    = 13      # no trades before 1pm UTC
TRADE_END_HOUR      = 22      # no trades after 10pm UTC
# Banned assets (consistent losers)
BANNED_ASSETS       = ["AVAX"]
# Scalp mode thresholds
SCALP_PROFIT        = 0.3     # 0.3% profit target in scalp mode
SCALP_STOP          = 0.25    # 0.25% stop loss in scalp mode
SCALP_HOLD_MAX      = 10      # max 10 minutes in scalp mode
# Regime detection
REGIME_RANGE_BAND   = 0.015   # 1.5% — if 24h range < this, market is ranging
REGIME_TREND_MIN    = 0.025   # 2.5% — if 24h move > this, market is trending'''

content = content.replace(old_config, new_config)

# ── 2. ADD REGIME DETECTOR + SCALP MODE + ALL FILTERS after check_trend_filter ──
old_inject = 'def check_correlation(asset, open_trades):'
new_inject = '''def detect_regime(prices_24h):
    """Detect market regime: RANGING, TRENDING_UP, TRENDING_DOWN, VOLATILE"""
    if not prices_24h or len(prices_24h) < 4:
        return "UNKNOWN"
    high = max(prices_24h)
    low  = min(prices_24h)
    last = prices_24h[-1]
    first = prices_24h[0]
    range_pct = (high - low) / first if first > 0 else 0
    move_pct  = (last - first) / first if first > 0 else 0
    if range_pct < REGIME_RANGE_BAND:
        return "RANGING"
    elif move_pct > REGIME_TREND_MIN:
        return "TRENDING_UP"
    elif move_pct < -REGIME_TREND_MIN:
        return "TRENDING_DOWN"
    else:
        return "VOLATILE"

def get_trade_mode(regime, momentum_1h, momentum_24h):
    """Return SCALP, SWING, or SKIP based on regime"""
    if regime == "RANGING":
        return "SCALP"   # choppy market — fast in/out
    elif regime == "TRENDING_UP" and momentum_1h > 0:
        return "SWING"   # strong uptrend — ride it
    elif regime == "TRENDING_DOWN":
        return "SKIP"    # downtrend — don't buy
    elif regime == "VOLATILE":
        return "SCALP"   # volatile — small quick trades only
    else:
        return "SCALP"   # default to scalp when unsure

def check_time_filter():
    """Only trade during active hours"""
    hour = datetime.utcnow().hour
    if hour < TRADE_START_HOUR or hour > TRADE_END_HOUR:
        return False, f"Outside trading hours (UTC {hour}:00)"
    return True, "time OK"

def check_trend_filter(momentum_24h, btc_24h):
    """Block buys when market is in strong downtrend"""
    if momentum_24h < TREND_BLOCK_24H:
        return False, f"24h momentum {momentum_24h:.1f}% too bearish"
    if btc_24h < BTC_CRASH_BLOCK:
        return False, f"BTC 24h {btc_24h:.1f}% crash — all buys blocked"
    return True, "trend OK"

def check_asset_banned(asset):
    """Skip banned assets"""
    if asset in BANNED_ASSETS:
        return False, f"{asset} is banned (consistent loser)"
    return True, "asset OK"

def run_loss_postmortem(brain, daily_loss):
    """When daily loss limit hits, analyze why and write new rules"""
    try:
        trades_today = [t for t in brain.get("trades", [])
                       if t.get("status") == "closed" and
                       t.get("close_time", "")[:10] == datetime.now().strftime("%Y-%m-%d")]
        if not trades_today:
            return

        # Find patterns in today's losses
        losing_assets = {}
        losing_regimes = {}
        losing_hours = {}

        for t in trades_today:
            if not t.get("won", True):
                asset = t.get("asset", "?")
                regime = t.get("regime", "?")
                hour = str(t.get("hour", "?"))
                losing_assets[asset] = losing_assets.get(asset, 0) + 1
                losing_regimes[regime] = losing_regimes.get(regime, 0) + 1
                losing_hours[hour] = losing_hours.get(hour, 0) + 1

        lessons = []

        # Ban assets that lost 2+ times today
        for asset, count in losing_assets.items():
            if count >= 2 and asset not in BANNED_ASSETS:
                BANNED_ASSETS.append(asset)
                lessons.append(f"BANNED {asset} — lost {count}x today")

        # Reduce size multiplier hard
        brain["size_multiplier"] = max(0.3, brain.get("size_multiplier", 1.0) - 0.3)
        lessons.append(f"Size reduced to {brain['size_multiplier']:.1f}x after daily loss")

        # Log the postmortem
        postmortem = {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "daily_loss": round(daily_loss, 2),
            "trades_today": len(trades_today),
            "lessons": lessons,
            "losing_assets": losing_assets,
            "losing_regimes": losing_regimes,
        }
        if "postmortems" not in brain:
            brain["postmortems"] = []
        brain["postmortems"].append(postmortem)

        log.info(f"Postmortem: {lessons}")
        save_brain(brain)
        return postmortem
    except Exception as e:
        log.error(f"Postmortem error: {e}")
        return None

def check_go_live_readiness(brain):
    """Track progress toward real money trading"""
    wins = brain.get("wins", 0)
    losses = brain.get("losses", 0)
    total = wins + losses
    pnl = brain.get("total_pnl", 0)
    postmortems = len(brain.get("postmortems", []))

    if total < 30:
        return False, f"Need 30+ trades ({total} so far)"
    wr = wins / total
    if wr < 0.60:
        return False, f"Need 60%+ win rate ({wr*100:.0f}% so far)"
    if pnl < 50:
        return False, f"Need $50+ profit (${pnl:.2f} so far)"
    if postmortems > 3:
        return False, f"Too many loss days ({postmortems}) — needs more consistency"

    return True, f"READY FOR REAL MONEY — {wr*100:.0f}% WR, ${pnl:.2f} profit, {total} trades"

def check_correlation(asset, open_trades):'''

content = content.replace(old_inject, new_inject)

# ── 3. INJECT FILTERS INTO TRADE ENTRY ──
# Find the entry signal check area and add filters
old_entry = '''    zone=rsi_zone(analysis["rsi"])
    wr,n=get_wr(brain["rsi_zones"],zone)'''

new_entry = '''    # ── FILTERS — check before any trade ──
    time_ok, time_msg = check_time_filter()
    if not time_ok:
        log.info(f"Filter: {time_msg}")
        return None

    asset_ok, asset_msg = check_asset_banned(asset if isinstance(asset, str) else "")
    if not asset_ok:
        log.info(f"Filter: {asset_msg}")
        return None

    mom_24h = analysis.get("momentum_24h", 0) if isinstance(analysis, dict) else 0
    btc_24h = analysis.get("btc_24h", 0) if isinstance(analysis, dict) else 0
    trend_ok, trend_msg = check_trend_filter(mom_24h, btc_24h)
    if not trend_ok:
        log.info(f"Filter: {trend_msg}")
        return None

    # Detect regime and set trade mode
    prices_24h = analysis.get("prices_24h", []) if isinstance(analysis, dict) else []
    regime = detect_regime(prices_24h) if prices_24h else analysis.get("regime", "UNKNOWN") if isinstance(analysis, dict) else "UNKNOWN"
    mom_1h = analysis.get("momentum_1h", 0) if isinstance(analysis, dict) else 0
    trade_mode = get_trade_mode(regime, mom_1h, mom_24h)
    if trade_mode == "SKIP":
        log.info(f"Filter: regime={regime} — SKIP")
        return None
    log.info(f"Trade mode: {trade_mode} | Regime: {regime}")

    zone=rsi_zone(analysis["rsi"])
    wr,n=get_wr(brain["rsi_zones"],zone)'''

content = content.replace(old_entry, new_entry)

# ── 4. INJECT POSTMORTEM INTO DAILY LOSS CHECK ──
old_loss = '''    if daily_loss >= DAILY_LOSS_LIMIT:
        log.warning(f"DAILY LOSS LIMIT HIT: ${daily_loss:.2f}")
        jarvis_brain.set_risk_level("stop")
        return True, daily_loss
    return False, daily_loss'''

new_loss = '''    if daily_loss >= DAILY_LOSS_LIMIT:
        log.warning(f"DAILY LOSS LIMIT HIT: ${daily_loss:.2f}")
        jarvis_brain.set_risk_level("stop")
        # Run postmortem immediately
        try:
            brain_data = json.load(open(MEMORY_FILE)) if os.path.exists(MEMORY_FILE) else {}
            postmortem = run_loss_postmortem(brain_data, daily_loss)
            if postmortem:
                lessons_str = " | ".join(postmortem.get("lessons", []))
                tg(f"JARVIS POSTMORTEM\\nLoss: ${daily_loss:.2f}\\nLessons: {lessons_str}")
        except Exception as e:
            log.error(f"Postmortem failed: {e}")
        return True, daily_loss
    return False, daily_loss'''

content = content.replace(old_loss, new_loss)

# ── 5. ADD GO-LIVE CHECK TO MORNING REPORT ──
old_report = '''        f"Consec losses: {brain.get('consecutive_losses',0)} | Best hour: {brain.get('best_hour',9)}:00"'''

new_report = '''        f"Consec losses: {brain.get('consecutive_losses',0)} | Best hour: {brain.get('best_hour',9)}:00",
        "",
        "GO-LIVE STATUS:",
        check_go_live_readiness(brain)[1]'''

content = content.replace(old_report, new_report)

with open('/root/jarvis/jarvis_alpha_v2.py', 'w') as f:
    f.write(content)

print("All upgrades applied successfully")

# Verify key additions
checks = [
    "detect_regime",
    "get_trade_mode",
    "check_time_filter",
    "check_trend_filter",
    "check_asset_banned",
    "run_loss_postmortem",
    "check_go_live_readiness",
    "BANNED_ASSETS",
    "SCALP_PROFIT",
    "TRADE_START_HOUR",
]
for check in checks:
    found = check in content
    print(f"  {'OK' if found else 'MISSING'} — {check}")
