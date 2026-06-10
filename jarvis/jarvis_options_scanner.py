#!/usr/bin/env python3
"""
JARVIS Options Scanner — 5-minute scan during prime hours
Replaces hourly scan in options_brain with fast intraday scanning
Prime hours: 9:30-10:30 AM and 2:00-3:30 PM ET
"""
import sys, json, time, logging, requests
from datetime import datetime
sys.path.insert(0, '/root/jarvis')
import paper_trades_store as store

logging.basicConfig(
    filename='/root/jarvis/jarvis_options_scanner.log',
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger("options_scanner")

TG_TOKEN = __import__("jarvis_secrets").TG_TOKEN_TRADER
CHAT_ID  = "7534553840"
SCAN_INTERVAL_PRIME = 300   # 5 min during prime hours
SCAN_INTERVAL_NORMAL = 1800 # 30 min off-peak
PRIME_WINDOWS = [(9, 30, 10, 30), (13, 45, 15, 30)]  # ET — extended slightly


def valid_expiry(ticker, expiry):
    """Ensure `expiry` is a real yfinance expiration for `ticker` before logging.

    Returns the expiry unchanged if valid, else the nearest valid future expiry
    (e.g. AMD has no Wed weeklies, so 2026-06-10 -> 2026-06-12). On any error
    reading the chain it fails open (returns the original) — the normal scan
    path already sources expiries from yfinance, so this is a backstop against
    a transient/phantom date that the grader could never price.
    """
    try:
        import yfinance as yf
        exps = list(yf.Ticker(ticker).options)
        if not exps or expiry in exps:
            return expiry
        target = datetime.strptime(expiry, "%Y-%m-%d").date()
        future = [e for e in exps if datetime.strptime(e, "%Y-%m-%d").date() >= target]
        pool = future or exps
        snapped = min(pool, key=lambda e: abs((datetime.strptime(e, "%Y-%m-%d").date() - target).days))
        log.warning(f"Expiry {expiry} invalid for {ticker}; snapping to {snapped}")
        return snapped
    except Exception as e:
        log.error(f"Expiry validation {ticker} {expiry}: {e}")
        return expiry


SCANNER_ALERT_FILE = "/root/jarvis/jarvis_scanner_alerts.json"

def already_alerted_today(key):
    """Per-setup, once-per-day dedup gate for scanner Telegram alerts.

    The same top-2 setup persists across many 5-min scans, so without a gate
    the identical alert re-fires all session. State is persisted to disk so a
    scanner restart can't re-spam a setup already alerted today.
    """
    import os as _os
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        state = json.load(open(SCANNER_ALERT_FILE)) if _os.path.exists(SCANNER_ALERT_FILE) else {}
    except Exception:
        state = {}
    if state.get(key) == today:
        return True
    state[key] = today
    state = {k: v for k, v in state.items() if v == today}  # prune past days
    try:
        json.dump(state, open(SCANNER_ALERT_FILE, "w"))
    except Exception:
        pass
    return False

def tg(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg}, timeout=5)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def is_prime_hour():
    now_et = (datetime.now().hour - 4) % 24
    now_min = datetime.now().minute
    for (sh, sm, eh, em) in PRIME_WINDOWS:
        if (now_et == sh and now_min >= sm) or \
           (now_et > sh and now_et < eh) or \
           (now_et == eh and now_min <= em):
            return True
    return False

def is_market_hours():
    now_et = (datetime.now().hour - 4) % 24
    now_dow = datetime.now().weekday()
    return now_dow < 5 and 9 <= now_et <= 16

def load_ctx():
    ctx = {}
    for name, path in [
        ("macro", "/root/jarvis/jarvis_macro.json"),
        ("brain", "/root/jarvis/jarvis_central_brain.json"),
        ("congress", "/root/jarvis/jarvis_congress.json"),
        ("earnings", "/root/jarvis/jarvis_earnings.json"),
        ("beast", "/root/jarvis/jarvis_beast_brain.json"),
        ("intel", "/root/jarvis/jarvis_intel.json"),
    ]:
        try: ctx[name] = json.load(open(path))
        except: ctx[name] = {}
    return ctx

def run_scan():
    from jarvis_options_brain import (
        get_yf_contracts, find_best_contract, get_price,
        score_setup, build_trade_alert, UNIVERSE, load_brain, save_brain,
        strike_too_far_otm, get_iv_rank
    )

    ctx = load_ctx()
    macro = ctx.get("macro", {})
    regime = macro.get("regime", "UNKNOWN")
    vix_raw = macro.get("vix", 15)
    vix = vix_raw.get("value", 15) if isinstance(vix_raw, dict) else float(vix_raw)
    fg_raw = macro.get("fear_greed", 50)
    fg = fg_raw.get("current", 50) if isinstance(fg_raw, dict) else int(fg_raw)

    log.info(f"Scanning {len(UNIVERSE)} tickers | Regime:{regime} F&G:{fg} VIX:{vix} Prime:{is_prime_hour()}")

    top_setups = []
    for ticker, config in UNIVERSE.items():
        try:
            price = get_price(ticker)
            if not price: continue

            # Determine scan type based on regime
            scan_types = []
            if regime == "RISK_OFF" and fg < 35:
                scan_types.append("put_buy")
            if regime == "RISK_ON":
                scan_types.append("call_buy")
            if fg < 55:
                scan_types.append("put_sell")

            for opt_type in scan_types:
                # Momentum guard: don't buy puts on a stock rallying >1% on the day
                if opt_type == "put_buy":
                    from jarvis_options_brain import get_day_change_pct
                    day_chg = get_day_change_pct(ticker)
                    if day_chg is not None and day_chg > 1.0:
                        log.info(f"MOMENTUM: skip {ticker} put_buy — stock up {day_chg:+.1f}% on day")
                        continue
                contracts, iv = get_yf_contracts(
                    ticker,
                    "put" if "put" in opt_type else "call"
                )
                if not contracts: continue

                score, signals = score_setup(ticker, price, iv, opt_type, ctx, config)
                threshold = 40 if (opt_type == "put_buy" and regime == "RISK_OFF") else 60
                if score < threshold: continue

                best = find_best_contract(contracts, price, opt_type)
                if not best: continue

                strike = float(best["strike_price"])
                dte = (datetime.strptime(best["expiration_date"], "%Y-%m-%d") - datetime.now()).days

                # (#1) DTE floor — never trade under 30 days to expiry.
                if dte < 30:
                    log.info(f"SKIP: {ticker} {opt_type} {dte} DTE — under 30-day floor")
                    continue
                # (#3) Moneyness — buy-side strikes must be within 10% of spot.
                if opt_type in ("call_buy", "put_buy") and strike_too_far_otm(price, strike):
                    log.info(f"SKIP: strike too far OTM — {ticker} ${strike:.0f} vs spot ${price:.2f}")
                    continue
                # (#2) IV Rank — don't BUY premium when implied vol is already rich.
                if opt_type in ("call_buy", "put_buy"):
                    iv_rank = get_iv_rank(ticker, iv)
                    if iv_rank is not None and iv_rank > 50.0:
                        log.info(f"SKIP: IV Rank {iv_rank:.0f} — too expensive to buy ({ticker} {opt_type})")
                        continue

                bid = best.get("bid", 0)
                ask = best.get("ask", 0)
                premium = round((float(bid) + float(ask)) / 2, 2)
                if not premium: continue

                top_setups.append({
                    "score": score,
                    "ticker": ticker,
                    "price": price,
                    "strategy": opt_type,
                    "strike": strike,
                    "expiry": best["expiration_date"],
                    "premium": premium,
                    "dte": dte,
                    "iv": iv,
                    "signals": signals,
                    "contract": best.get("symbol", ""),
                    "config": config
                })
        except Exception as e:
            log.error(f"Scan {ticker}: {e}")

    top_setups.sort(key=lambda x: x["score"], reverse=True)
    alerted = 0

    for setup in top_setups[:2]:
        try:
            alert = build_trade_alert(setup, ctx)
            if not alert: continue

            # Snap to a real yfinance expiry so the dedup key + paper log are stable
            setup["expiry"] = valid_expiry(setup["ticker"], setup["expiry"])
            # Once-per-day-per-setup gate so the same top setup can't re-alert every 5 min
            dedup_key = f"{setup['ticker']}_{setup['strategy']}_{setup['strike']}_{setup['expiry']}"
            if already_alerted_today(dedup_key):
                log.info(f"DEDUP: {setup['ticker']} {setup['strategy']} ${setup['strike']} {setup['expiry']} already alerted today — skipping")
                continue

            tg(alert)
            alerted += 1
            log.info(f"ALERT: {setup['ticker']} {setup['strategy']} score:{setup['score']} premium:${setup['premium']}")

            # Junk-contract guard: drop deep-OTM / near-worthless setups (issue #4)
            _junk, _jr = store.is_junk_contract(setup["price"], setup["strike"], setup["premium"])
            if _junk:
                log.info(f"JUNK: skipped {setup['ticker']} {setup['strategy']} ${setup['strike']} — {_jr}")
                continue

            # Auto-log to paper trades (lock-protected + per-ticker exposure cap)
            try:
                cost = round(setup["premium"] * 100, 2)
                new_trade = {
                    "ticker": setup["ticker"],
                    "strategy": setup["strategy"],
                    "strike": setup["strike"],
                    "expiry": setup["expiry"],
                    "entry_price": setup["price"],
                    "premium": setup["premium"],
                    "cost_per_contract": cost,
                    "score": setup["score"],
                    "iv": setup["iv"],
                    "entry_date": datetime.now().strftime("%Y-%m-%d"),
                    "entry_time": datetime.now().strftime("%H:%M"),
                    "source": "jarvis_auto",
                    "signals": setup.get("signals", []),
                    "status": "paper_open",
                    "result": None,
                    "exit_price": None,
                    "pnl": None
                }
                def _append(data):
                    # Don't double-log same ticker+strike+expiry
                    if any(t["ticker"] == setup["ticker"] and t["strike"] == setup["strike"]
                           and t["expiry"] == setup["expiry"] and t.get("status") == "paper_open"
                           for t in data["trades"]):
                        return ("dup", None)
                    capped, reason = store.would_exceed_cap(data, setup["ticker"], cost, trade=new_trade)
                    if capped:
                        return ("capped", reason)
                    data["trades"].append(new_trade)
                    return ("logged", None)
                outcome, reason = store.update(_append)
                if outcome == "logged":
                    log.info(f"Paper logged: {setup['ticker']} {setup['strategy']} ${setup['strike']}")
                elif outcome == "capped":
                    log.info(f"CAP: skipped {setup['ticker']} {setup['strategy']} ${setup['strike']} — {reason}")
                    # one-time-per-ticker-per-day notice so suppressed setups are visible
                    if not already_alerted_today(f"CAP_{setup['ticker']}"):
                        tg(f"🚧 EXPOSURE CAP hit — skipped {setup['ticker']} {setup['strategy']} ${setup['strike']}\n{reason}")
            except Exception as pe:
                log.error(f"Paper log error: {pe}")

        except Exception as e:
            log.error(f"Alert error {setup['ticker']}: {e}")

    if alerted == 0:
        log.info(f"No alerts — top score: {top_setups[0]['score'] if top_setups else 0}")

    return alerted

def feed_learning_from_closed():
    """Feed grader-closed trades into the signal-weight learning loop, exactly once.

    The options_grader daemon is the SINGLE source of truth for closing paper
    trades (live-premium ±50% / DTE / expiry). This function no longer closes or
    re-grades anything — doing so previously produced an intrinsic-value vs
    live-premium divergence and conflicting exit fields. It only consumes the
    grader's results for learning, marking each trade `learned` so weights aren't
    double-counted. The learned flag is set inside the lock; update_signal_weights
    (which writes a separate jarvis_signal_weights.json) runs outside the lock.
    """
    try:
        captured = []
        def _mark(data):
            for t in data["trades"]:
                if (t.get("status") == "paper_closed" and t.get("result")
                        and not t.get("learned")):
                    t["learned"] = True
                    captured.append(dict(t))
        store.update(_mark)
        for t in captured:
            try:
                update_signal_weights(t, t["result"])
            except Exception as e:
                log.error(f"Learning feed {t.get('ticker')}: {e}")
        if captured:
            log.info(f"Learning: fed {len(captured)} grader-closed trades into signal weights")
    except Exception as e:
        log.error(f"Learning feed cycle error: {e}")

def update_signal_weights(trade, result):
    """
    Learning loop — adjust signal confidence based on outcomes.
    Tracks win rate by: regime, fg_range, iv_level, strategy, hour
    """
    try:
        weights_file = "/root/jarvis/jarvis_signal_weights.json"
        try:
            weights = json.load(open(weights_file))
        except:
            weights = {
                "by_strategy": {},
                "by_regime": {},
                "by_fg_range": {},
                "by_iv_level": {},
                "by_hour": {},
                "total_graded": 0,
                "total_wins": 0
            }

        won = result == "WIN"
        strategy = trade.get("strategy", "unknown")
        regime = trade.get("regime", "unknown")
        iv = trade.get("iv", 0) or 0
        entry_time = trade.get("entry_time", "00:00")
        hour = int(entry_time.split(":")[0]) if ":" in str(entry_time) else 0

        # Categorize
        fg_range = "extreme_fear" if trade.get("score", 50) < 30 else "fear" if trade.get("score", 50) < 45 else "neutral"
        iv_level = "high" if iv > 60 else "medium" if iv > 30 else "low"

        def update_bucket(d, key):
            if key not in d:
                d[key] = {"wins": 0, "total": 0, "wr": 0}
            d[key]["total"] += 1
            if won: d[key]["wins"] += 1
            d[key]["wr"] = round(d[key]["wins"] / d[key]["total"] * 100, 1)

        update_bucket(weights["by_strategy"], strategy)
        update_bucket(weights["by_regime"], regime)
        update_bucket(weights["by_fg_range"], fg_range)
        update_bucket(weights["by_iv_level"], iv_level)
        update_bucket(weights["by_hour"], str(hour))

        weights["total_graded"] += 1
        if won: weights["total_wins"] += 1
        weights["overall_wr"] = round(weights["total_wins"] / weights["total_graded"] * 100, 1)
        weights["last_updated"] = datetime.now().isoformat()

        json.dump(weights, open(weights_file, "w"), indent=2)
        log.info(f"Weights updated: {strategy} {regime} WR:{weights['by_strategy'].get(strategy,{}).get('wr','?')}%")

    except Exception as e:
        log.error(f"Weight update error: {e}")

def main():
    log.info("JARVIS OPTIONS SCANNER ONLINE — 5min prime / 30min off-peak")
    tg("OPTIONS SCANNER ONLINE\n5-min scanning during prime hours\nLearning loop active")

    last_scan = 0
    last_grade = 0

    while True:
        try:
            now = time.time()
            interval = SCAN_INTERVAL_PRIME if is_prime_hour() else SCAN_INTERVAL_NORMAL

            if is_market_hours() and now - last_scan >= interval:
                run_scan()
                last_scan = now

            # Feed grader-closed trades into learning every hour (options_grader
            # daemon is the sole closer — see feed_learning_from_closed docstring)
            if now - last_grade >= 3600:
                feed_learning_from_closed()
                last_grade = now

            time.sleep(30)

        except Exception as e:
            log.error(f"Main loop: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
