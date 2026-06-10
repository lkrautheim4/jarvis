# ═══════════════════════════════════════════════════════════════════════════════
# JARVIS MASTER — INTELLIGENCE UPGRADE PATCH
# Drop these functions into jarvis_master.py replacing the originals.
# Preserves your exact memory schema (graded, target_hit, price_at_pred, etc.)
# ═══════════════════════════════════════════════════════════════════════════════

# ── TIER 1 ADDITIONS ──────────────────────────────────────────────────────────
# Add these new functions ABOVE run_hourly_prediction()

def get_macd(prices_list):
    """MACD from price list. Returns macd_line, signal, histogram, trend."""
    if len(prices_list) < 26:
        return {"macd": 0, "signal": 0, "hist": 0, "trend": "neutral"}
    def ema(data, span):
        k = 2 / (span + 1)
        e = data[0]
        for v in data[1:]:
            e = v * k + e * (1 - k)
        return e
    ema12 = ema(prices_list[-26:], 12)
    ema26 = ema(prices_list[-26:], 26)
    macd_line = ema12 - ema26
    macd_vals = []
    for i in range(9, 0, -1):
        e12 = ema(prices_list[-(26+i):-(i)] if i > 0 else prices_list[-26:], 12)
        e26 = ema(prices_list[-(26+i):-(i)] if i > 0 else prices_list[-26:], 26)
        macd_vals.append(e12 - e26)
    signal_line = ema(macd_vals, 9) if len(macd_vals) >= 9 else macd_line
    hist = macd_line - signal_line
    return {
        "macd": round(macd_line, 2),
        "signal": round(signal_line, 2),
        "hist": round(hist, 2),
        "trend": "bullish" if hist > 0 else "bearish"
    }

def get_bollinger(prices_list, period=20):
    """Bollinger Bands. Returns upper, middle, lower, %B, position."""
    if len(prices_list) < period:
        return {"upper": 0, "middle": 0, "lower": 0, "pct_b": 0.5, "position": "middle"}
    sma = sum(prices_list[-period:]) / period
    std = (sum((c - sma)**2 for c in prices_list[-period:]) / period) ** 0.5
    upper = sma + 2 * std
    lower = sma - 2 * std
    price = prices_list[-1]
    pct_b = (price - lower) / (upper - lower) if (upper - lower) != 0 else 0.5
    if pct_b > 0.85:
        position = "near_upper_band"
    elif pct_b < 0.15:
        position = "near_lower_band"
    else:
        position = "middle"
    return {
        "upper": round(upper, 2),
        "middle": round(sma, 2),
        "lower": round(lower, 2),
        "pct_b": round(pct_b, 3),
        "position": position
    }

def get_volume_spike_binance():
    """Compare current 1h volume vs 20h rolling average from Binance."""
    try:
        r = requests.get("https://api.binance.us/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "1h", "limit": 25}, timeout=8)
        klines = r.json()
        volumes = [float(k[5]) for k in klines]
        current_vol = volumes[-1]
        avg_vol = sum(volumes[-21:-1]) / 20
        ratio = round(current_vol / avg_vol, 2) if avg_vol > 0 else 1.0
        return {"spike": ratio >= 2.0, "ratio": ratio, "current": round(current_vol, 2), "avg": round(avg_vol, 2)}
    except:
        return {"spike": False, "ratio": 1.0}

def get_fear_greed():
    """Fear & Greed index from alternative.me — free, no key needed."""
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=8)
        d = r.json()["data"][0]
        val = int(d["value"])
        label = d["value_classification"]
        if val < 25:   zone = "extreme_fear"
        elif val < 45: zone = "fear"
        elif val < 55: zone = "neutral"
        elif val < 75: zone = "greed"
        else:          zone = "extreme_greed"
        return {"score": val, "label": label, "zone": zone}
    except:
        return {"score": 50, "label": "Unknown", "zone": "neutral"}

def get_binance_prices_for_indicators():
    """Fetch 60 hourly candles from Binance for RSI/MACD/BB computation."""
    try:
        r = requests.get("https://api.binance.us/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "1h", "limit": 60}, timeout=8)
        klines = r.json()
        closes = [float(k[4]) for k in klines]
        return closes
    except:
        return []

def get_4h_momentum():
    """4h candle momentum from Binance."""
    try:
        r = requests.get("https://api.binance.us/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "4h", "limit": 10}, timeout=8)
        klines = r.json()
        closes = [float(k[4]) for k in klines]
        if len(closes) >= 2:
            pct = round((closes[-1] - closes[-2]) / closes[-2] * 100, 3)
            return {"pct": pct, "direction": "up" if pct > 0 else "down"}
        return {"pct": 0, "direction": "flat"}
    except:
        return {"pct": 0, "direction": "unknown"}

# ── TIER 2 ADDITIONS ──────────────────────────────────────────────────────────

def build_signal_fingerprint(rsi, bb_position, macd_trend, funding, fg_zone, vol_spike):
    """Hash current signal state into a combo key for pattern tracking."""
    rsi_bucket = "high" if rsi > 70 else "low" if rsi < 30 else "mid"
    vol_str = "spike" if vol_spike else "normal"
    return f"rsi:{rsi_bucket}|bb:{bb_position}|macd:{macd_trend}|fund:{funding}|fg:{fg_zone}|vol:{vol_str}"

def get_pattern_modifier(fingerprint, mem):
    """Confidence modifier based on historical win rate for this signal combo."""
    patterns = mem.get("patterns", {})
    stats = patterns.get(fingerprint, {})
    wins   = stats.get("wins", 0)
    losses = stats.get("losses", 0)
    total  = wins + losses
    if total < 5:
        return 1.0, f"new combo ({total} samples)"
    win_rate = wins / total
    modifier = round(0.7 + win_rate * 0.8, 3)
    modifier = min(max(modifier, 0.6), 1.5)
    return modifier, f"{round(win_rate*100)}% win rate on this combo ({total} samples)"

def get_time_bias(mem):
    """Win rate modifier for current UTC hour."""
    hour = str(datetime.utcnow().hour)
    tod  = mem.get("patterns", {}).get("time_of_day", {}).get(hour, {})
    wins   = tod.get("wins", 0)
    losses = tod.get("losses", 0)
    if wins + losses < 5:
        return 1.0, ""
    wr = wins / (wins + losses)
    return round(0.7 + wr * 0.8, 3), f"hour {hour}UTC: {round(wr*100)}% hist win rate"

def get_dow_info():
    """Day-of-week info. Weekends raise the skip threshold."""
    dow = datetime.utcnow().weekday()  # 0=Mon, 6=Sun
    days = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    is_weekend = dow >= 5
    return {"dow": dow, "day": days[dow], "is_weekend": is_weekend}

def update_pattern_memory(mem, fingerprint, correct, hour, dow):
    """Update T2 pattern stats after a prediction is graded."""
    if "patterns" not in mem:
        mem["patterns"] = {}
    # Signal combo
    combo = mem["patterns"].setdefault(fingerprint, {"wins": 0, "losses": 0})
    if correct:
        combo["wins"] += 1
    else:
        combo["losses"] += 1
        # Track blind spots
        blind = mem["patterns"].setdefault("blind_spots", {})
        blind[fingerprint] = blind.get(fingerprint, 0) + 1

    # Time of day
    tod = mem["patterns"].setdefault("time_of_day", {}).setdefault(str(hour), {"wins": 0, "losses": 0})
    if correct: tod["wins"] += 1
    else:       tod["losses"] += 1

    # Day of week
    dow_stats = mem["patterns"].setdefault("day_of_week", {}).setdefault(str(dow), {"wins": 0, "losses": 0})
    if correct: dow_stats["wins"] += 1
    else:       dow_stats["losses"] += 1

    return mem

def is_blind_spot(fingerprint, mem, threshold=3):
    """Flag signal combos Jarvis keeps getting wrong."""
    return mem.get("patterns", {}).get("blind_spots", {}).get(fingerprint, 0) >= threshold

# ── TIER 4: KELLY SIZING (replaces your existing kelly_bet_size) ───────────────

def kelly_bet_size(confidence_pct, odds=0.50, bankroll=500):
    """
    Fractional Kelly (0.25x). Tiers: HIGH/MEDIUM/SKIP.
    Replaces original — same signature, smarter internals.
    """
    try:
        p = confidence_pct / 100
        q = 1 - p
        b = (1 - odds) / odds if odds < 1 else 1.0
        kelly_full = max(0, (p * b - q) / b)
        kelly_frac = kelly_full * 0.25
        bet = round(kelly_frac * bankroll, 0)

        if confidence_pct >= 75:
            tier = "HIGH"
        elif confidence_pct >= 65:
            bet = round(bet * 0.5, 0)
            tier = "MEDIUM"
        else:
            bet = 0
            tier = "SKIP"

        return int(bet)
    except:
        return 25

# ── TIER 5: ORACLE CONTEXT BUILDER ────────────────────────────────────────────

def build_oracle_context(mem):
    """
    Inject last 10 graded predictions into prompt context.
    T5 — Claude sees its own track record and adjusts.
    """
    preds = mem.get("predictions", [])
    graded = [p for p in preds if p.get("graded")][-10:]
    if not graded:
        return "No graded predictions yet — building track record."

    lines = ["YOUR LAST 10 GRADED PREDICTIONS:"]
    correct_count = 0
    for p in graded:
        hit = p.get("target_hit")
        bet = p.get("bet", "?")
        correct = (bet == "YES" and hit) or (bet == "NO" and not hit)
        if correct: correct_count += 1
        icon = "✓" if correct else "✗"
        lines.append(
            f"  {icon} {p['ts']} | target:${p.get('target',0):,.0f} | "
            f"bet:{bet} | hit:{hit} | {p.get('reason','')[:60]}"
        )

    wr = round(correct_count / len(graded) * 100) if graded else 0
    lines.append(f"Recent accuracy: {wr}% ({correct_count}/{len(graded)})")

    # Blind spot warning
    s = mem.get("stats", {})
    yes_wr = round(s.get("correct_bet_yes",0) / s.get("total_bet_yes",1) * 100)
    no_wr  = round(s.get("correct_bet_no",0) / s.get("total_bet_no",1) * 100)
    if yes_wr < 50 and s.get("total_bet_yes",0) >= 5:
        lines.append("⚠️ BLIND SPOT: Your YES bets are underperforming — scrutinize YES calls extra carefully.")
    if no_wr < 50 and s.get("total_bet_no",0) >= 5:
        lines.append("⚠️ BLIND SPOT: Your NO bets are underperforming — scrutinize NO calls extra carefully.")

    return "\n".join(lines)

# ── UPGRADED run_hourly_prediction() ──────────────────────────────────────────
# REPLACE your existing run_hourly_prediction() with this version.
# All your existing memory writes, grading, and Telegram format are preserved.

def run_hourly_prediction(price, rsi, momentum):
    """
    Upgraded hourly prediction — Tiers 1-5 fully integrated.
    Preserves exact memory schema (graded, target_hit, price_at_pred, bet, etc.)
    """
    log_price_tick(price, rsi, momentum)
    markets, event = get_kalshi_markets(price)
    if not markets:
        log.info("No Kalshi markets for prediction")
        return

    best   = min(markets, key=lambda m: abs(m["yes"] - 0.50))
    target = best["strike"]
    mkt_lines = "\n".join([
        f"${m['strike']:,.0f} YES:{m['yes']:.2f} NO:{m['no']:.2f}"
        for m in markets[:5]
    ])

    # ── Tier 1: Enhanced signals ──
    closes      = get_binance_prices_for_indicators()
    macd        = get_macd(closes) if closes else {"hist": 0, "trend": "neutral"}
    bb          = get_bollinger(closes) if closes else {"pct_b": 0.5, "position": "middle"}
    vol_spike   = get_volume_spike_binance()
    fear_greed  = get_fear_greed()
    h4_momentum = get_4h_momentum()
    funding     = get_funding_rate("BTC")
    vol         = get_volume_ratio("BTC")

    # ── Tier 2: Pattern memory ──
    mem          = load_memory()
    fingerprint  = build_signal_fingerprint(
        rsi, bb["position"], macd["trend"],
        "high" if funding > 0.001 else "low" if funding < -0.001 else "balanced",
        fear_greed["zone"],
        vol_spike["spike"]
    )
    pattern_mod, pattern_note = get_pattern_modifier(fingerprint, mem)
    tod_mod, tod_note         = get_time_bias(mem)
    dow_info                  = get_dow_info()
    blind_spot                = is_blind_spot(fingerprint, mem)

    # ── Tier 5: Oracle context ──
    oracle_ctx = build_oracle_context(mem)

    # ── Tier 3: Multi-horizon note (we run one Claude call but flag 4h alignment) ──
    h4_align = ""
    if h4_momentum["direction"] == "up" and momentum.get("1h", 0) > 0:
        h4_align = "✅ 1h + 4h momentum ALIGNED UP — higher conviction"
    elif h4_momentum["direction"] == "down" and momentum.get("1h", 0) < 0:
        h4_align = "✅ 1h + 4h momentum ALIGNED DOWN — higher conviction"
    else:
        h4_align = "⚠️ 1h and 4h momentum CONFLICT — lower conviction, prefer SKIP"

    # ── Regime ──
    regime, regime_stats = get_def_regime()
    strategy = get_multi_regime_strategy(regime, regime_stats, rsi, funding, vol)

    now_edt  = (datetime.utcnow().hour - 4) % 24
    next_edt = (now_edt + 1) % 24
    weekend_note = f"⚠️ WEEKEND — raise bar to 70%+ before betting" if dow_info["is_weekend"] else ""

    prompt = f"""You are Jarvis, expert BTC Kalshi trading AI with a proven track record to protect.

BTC @ ${price:,.2f} | Target: ${target:,.0f} by {next_edt}:00 EDT
{'='*50}
── TIER 1: FULL SIGNAL SUITE ──
RSI(14): {rsi} {'⚠️ OVERBOUGHT' if rsi > 70 else '⚠️ OVERSOLD' if rsi < 30 else ''}
MACD: hist={macd['hist']} trend={macd['trend']}
Bollinger: %B={bb['pct_b']} position={bb['position']}
Volume spike: {vol_spike['spike']} ({vol_spike['ratio']}x avg)
1h momentum: {momentum.get('1h',0):+.2f}% | 4h momentum: {h4_momentum['pct']:+.2f}%
24h momentum: {momentum.get('24h',0):+.2f}%
{h4_align}
Fear & Greed: {fear_greed['score']}/100 — {fear_greed['label']} ({fear_greed['zone']})
Funding rate: {funding:.4f} {'⚠️ LONGS OVEREXTENDED' if funding > 0.002 else ''}
Volume ratio: {vol}x
Regime: {regime} | Strategy: {strategy['description']}
{weekend_note}
── TIER 2: PATTERN MEMORY ──
Signal combo: {fingerprint}
Historical modifier: {pattern_mod}x ({pattern_note})
{tod_note}
Day: {dow_info['day']} (weekend: {dow_info['is_weekend']})
{'⚠️ BLIND SPOT WARNING — Jarvis has been repeatedly wrong on this combo' if blind_spot else ''}
── TIER 5: YOUR TRACK RECORD ──
{oracle_ctx}
── LIVE KALSHI MARKETS ──
{mkt_lines}
{'='*50}
DECISION RULES:
- BET YES if >65% confident price stays ABOVE ${target:,.0f}
- BET NO if <35% confident (i.e. >65% it goes BELOW)
- SKIP if uncertain, signals conflict, or weekend + <70%
- Near upper BB + high RSI + high funding = strong NO lean
- Aligned 1h+4h momentum = confidence boost
- Blind spot flag = SKIP unless overwhelming evidence

Reply ONLY: TARGET_PROB|PREDICTED_PRICE|BET|REASON
Example: 72%|73850|YES|RSI mid-range, 1h+4h aligned up, F&G neutral, funding low — clean YES setup"""

    reply = claude(prompt)
    if not reply:
        tg("Claude unavailable", TG_PRED)
        return

    parts = reply.split("|")
    if len(parts) < 3:
        log.error(f"Bad Claude format: {reply}")
        return

    prob    = parts[0].strip()
    pred_px = parts[1].strip()
    bet     = parts[2].strip().upper()
    reason  = parts[3].strip() if len(parts) > 3 else ""

    # ── Tier 4: Kelly sizing ──
    try:
        prob_num    = float(prob.replace("%",""))
        kalshi_yes  = best["yes"]
        edge        = abs(prob_num/100 - kalshi_yes)
        edge_str    = f"+{round(edge*100)}% EDGE" if edge > 0.10 else "thin edge"
        # Apply pattern + time modifiers to confidence for Kelly
        adjusted_conf = min(prob_num * pattern_mod * tod_mod, 95)
        kelly_size  = kelly_bet_size(adjusted_conf, odds=kalshi_yes)
        kelly_str   = f"Kelly: ${kelly_size} (adj conf: {round(adjusted_conf)}%)"
    except:
        edge_str  = ""
        kelly_str = "Kelly: n/a"

    # ── Format Telegram message ──
    bet_line = {"YES": "🟢 BET YES", "NO": "🔴 BET NO", "SKIP": "⚪ SKIP"}.get(bet, "⚪ SKIP")
    sr       = get_support_resistance()
    sr_line  = f"S:${sr['support']:,.0f} R:${sr['resistance']:,.0f}" if sr else ""
    blind_line = "\n⚠️ BLIND SPOT — verify carefully" if blind_spot else ""
    align_icon = "⬆️⬆️" if "ALIGNED UP" in h4_align else "⬇️⬇️" if "ALIGNED DOWN" in h4_align else "↔️"

    msg = f"""🤖 JARVIS PREDICTIONS
{'='*22}
BTC @ ${price:,.2f}
Target: ${target:,.0f} by {next_edt}:00 EDT
{'='*22}
{bet_line}
Prob: {prob} | Guess: ${pred_px}
{edge_str} | {kelly_str}
{'='*22}
RSI:{rsi} MACD:{macd['trend']} BB:{bb['position']}
Vol spike:{vol_spike['spike']}({vol_spike['ratio']}x) F&G:{fear_greed['score']}({fear_greed['zone']})
Fund:{funding:.4f} | Regime:{regime}
4h align: {align_icon} {h4_momentum['pct']:+.2f}%
{sr_line}{blind_line}
{'='*22}
{reason}"""

    tg(msg, TG_PRED)

    # ── Save to memory — EXACT ORIGINAL SCHEMA ──
    mem = load_memory()
    pred_record = {
        "id":            datetime.utcnow().strftime("%Y%m%d%H%M"),
        "ts":            datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        "symbol":        "BTC",
        "price_at_pred": round(price, 2),
        "target":        target,
        "target_prob":   prob,
        "predicted_price": pred_px,
        "bet":           bet,
        "reason":        reason,
        "actual_price":  None,
        "target_hit":    None,
        "graded":        False,
        # T1-T5 extras (additive — don't break anything)
        "fingerprint":   fingerprint,
        "rsi":           rsi,
        "macd_trend":    macd["trend"],
        "bb_position":   bb["position"],
        "vol_spike":     vol_spike["spike"],
        "fear_greed":    fear_greed["score"],
        "funding":       funding,
        "h4_pct":        h4_momentum["pct"],
        "regime":        regime,
        "pattern_mod":   pattern_mod,
        "kelly_size":    kelly_size if 'kelly_size' in dir() else 0,
        "blind_spot":    blind_spot,
        "dow":           dow_info["dow"],
        "hour_utc":      datetime.utcnow().hour,
    }
    mem["predictions"].append(pred_record)
    mem["stats"]["total_predictions"] = len(mem["predictions"])
    save_memory(mem)
    log.info(f"Prediction sent BTC target={target} bet={bet} prob={prob} kelly=${kelly_size if 'kelly_size' in dir() else 0}")

# ── UPGRADED grade_predictions() ──────────────────────────────────────────────
# REPLACE your existing grade_predictions() with this version.

def grade_predictions(current_price):
    """
    Auto-grade last prediction. Updates T2 pattern memory after grading.
    Preserves your exact schema — only adds pattern_memory update.
    """
    mem = load_memory()
    for pred in reversed(mem["predictions"]):
        if pred.get("graded"):
            break
        pred_ts = datetime.strptime(pred["ts"], "%Y-%m-%d %H:%M")
        if datetime.utcnow() < pred_ts + timedelta(hours=1):
            break

        pred["actual_price"] = round(current_price, 2)
        pred["target_hit"]   = current_price >= pred["target"]
        pred["graded"]       = True

        # Determine if bet was correct
        bet = pred.get("bet", "SKIP")
        hit = pred["target_hit"]
        correct = (bet == "YES" and hit) or (bet == "NO" and not hit)

        # Original stats update — unchanged
        s = mem["stats"]
        if pred["target_hit"]:
            s["correct_target"] = s.get("correct_target", 0) + 1
            s["current_streak"] = s.get("current_streak", 0) + 1
            s["best_streak"]    = max(s.get("best_streak", 0), s["current_streak"])
        else:
            s["current_streak"] = 0

        if bet == "YES":
            s["total_bet_yes"]   = s.get("total_bet_yes", 0) + 1
            if hit: s["correct_bet_yes"] = s.get("correct_bet_yes", 0) + 1
        elif bet == "NO":
            s["total_bet_no"]    = s.get("total_bet_no", 0) + 1
            if not hit: s["correct_bet_no"] = s.get("correct_bet_no", 0) + 1

        # T2: Update pattern memory
        fingerprint = pred.get("fingerprint")
        if fingerprint:
            hour = pred.get("hour_utc", datetime.utcnow().hour)
            dow  = pred.get("dow", datetime.utcnow().weekday())
            mem  = update_pattern_memory(mem, fingerprint, correct, hour, dow)

        save_memory(mem)
        log.info(f"Graded: target_hit={pred['target_hit']} bet={bet} correct={correct}")
        break

