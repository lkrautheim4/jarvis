"""
btc_memory.py — Jarvis BTC Memory & Learning Engine
Logs every price tick, prediction, outcome, and pattern.
Jarvis never forgets.
"""

import json
import os
from datetime import datetime, timedelta
from collections import defaultdict

MEMORY_FILE = "/root/jarvis/btc_memory.json"
MAX_PRICE_HISTORY = 2000   # ~83 days of hourly ticks
MAX_PREDICTIONS   = 500


# ─────────────────────────────────────────────
# I/O
# ─────────────────────────────────────────────

def _load() -> dict:
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE) as f:
                return json.load(f)
        except Exception as e:
            # NEVER silently return empty — that overwrites real history on next save.
            # Try the most recent backup before giving up.
            import glob
            bks = sorted(glob.glob(MEMORY_FILE + ".bak_*"))
            for bk in reversed(bks):
                try:
                    with open(bk) as f:
                        data = json.load(f)
                    print(f"[BTC_MEMORY] WARN: main file unreadable ({e}); recovered from {bk}")
                    return data
                except Exception:
                    continue
            raise RuntimeError(f"btc_memory.json unreadable and no backup recovered: {e}")
    return {
        "prices": [],
        "predictions": [],
        "daily_summaries": {},
        "patterns": {},
        "stats": {
            "total_predictions": 0,
            "correct_target": 0,
            "correct_range": 0,
            "total_bet_yes": 0,
            "correct_bet_yes": 0,
            "total_bet_no": 0,
            "correct_bet_no": 0,
            "best_streak": 0,
            "current_streak": 0,
            "avg_error_dollars": 0.0,
        }
    }


def _save(mem: dict):
    import glob, time, shutil
    # Shrink guard: refuse to overwrite a much larger prediction history with a tiny one
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE) as f:
                disk = json.load(f)
            disk_n = len(disk.get("predictions", []))
            new_n  = len(mem.get("predictions", []))
            if disk_n >= 10 and new_n < disk_n * 0.5:
                bad = MEMORY_FILE + f".BLOCKED_{int(time.time())}"
                with open(bad, "w") as f:
                    json.dump(mem, f, indent=2)
                raise RuntimeError(
                    f"_save BLOCKED: prediction count would drop {disk_n}->{new_n}. "
                    f"Attempt saved to {bad} for inspection; disk file untouched."
                )
        except (json.JSONDecodeError, FileNotFoundError):
            pass  # disk unreadable; let the write proceed to restore a valid file
    # Rolling backup before write (keep last 10)
    if os.path.exists(MEMORY_FILE):
        shutil.copy(MEMORY_FILE, MEMORY_FILE + f".bak_{int(time.time())}")
        bks = sorted(glob.glob(MEMORY_FILE + ".bak_*"))
        for old in bks[:-10]:
            try: os.remove(old)
            except: pass
    # Trim history to max sizes
    mem["prices"]      = mem["prices"][-MAX_PRICE_HISTORY:]
    mem["predictions"] = mem["predictions"][-MAX_PREDICTIONS:]
    tmp = MEMORY_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(mem, f, indent=2)
    os.replace(tmp, MEMORY_FILE)  # atomic — no half-written file on crash


# ─────────────────────────────────────────────
# PRICE LOGGING
# ─────────────────────────────────────────────

def log_price(price: float, rsi: float, momentum: dict):
    """Log a price snapshot every time the bot runs."""
    mem = _load()
    now = datetime.utcnow()
    entry = {
        "ts":    now.strftime("%Y-%m-%d %H:%M"),
        "date":  now.strftime("%Y-%m-%d"),
        "hour":  now.hour,
        "price": round(price, 2),
        "rsi":   rsi,
        "1h":    momentum.get("1h", 0),
        "4h":    momentum.get("4h", 0),
        "24h":   momentum.get("24h", 0),
    }
    mem["prices"].append(entry)
    _update_daily_summary(mem, entry)
    _save(mem)


def _update_daily_summary(mem: dict, entry: dict):
    date = entry["date"]
    ds = mem["daily_summaries"].setdefault(date, {
        "open": entry["price"], "high": entry["price"],
        "low":  entry["price"], "close": entry["price"],
        "ticks": 0, "avg_rsi": 0.0, "prices": []
    })
    ds["high"]   = max(ds["high"], entry["price"])
    ds["low"]    = min(ds["low"],  entry["price"])
    ds["close"]  = entry["price"]
    ds["ticks"] += 1
    ds.setdefault("prices", []).append(entry["price"])
    ds["avg_rsi"] = round(
        (ds["avg_rsi"] * (ds["ticks"] - 1) + entry["rsi"]) / ds["ticks"], 1
    )
    # Keep only last 60 days of daily summaries
    all_dates = sorted(mem["daily_summaries"].keys())
    if len(all_dates) > 60:
        for old in all_dates[:-60]:
            del mem["daily_summaries"][old]


# ─────────────────────────────────────────────
# PREDICTION LOGGING
# ─────────────────────────────────────────────

def log_prediction(symbol: str, price: float, target: float,
                   low: float, high: float,
                   target_prob: str, predicted_price: str,
                   range_prob: str, bet: str, reason: str,
                   entry_implied_prob: float = 0.0,
                   market_ticker: str = "",
                   contract_expiry: str = "") -> str:
    """Log a prediction. Returns a unique prediction ID."""
    mem = _load()
    now = datetime.utcnow()
    pred_id = now.strftime("%Y%m%d%H%M")

    pred = {
        "id":                   pred_id,
        "ts":                   now.strftime("%Y-%m-%d %H:%M"),
        "symbol":               symbol,
        "price_at_pred":        round(price, 2),
        "target":               target,
        "range_low":            low,
        "range_high":           high,
        "target_prob":          target_prob,
        "predicted_price":      predicted_price,
        "range_prob":           range_prob,
        "bet":                  bet,
        "reason":               reason,
        "entry_implied_prob":   round(entry_implied_prob, 4),  # Fix #1: Kalshi yes_price at prediction time
        "closing_implied_prob": None,                          # Fix #2: filled by check_closing_snapshots()
        "market_ticker":        market_ticker,
        "contract_expiry":      contract_expiry,               # Fix #3: real Kalshi close_time
        "actual_price":         None,   # filled in next cycle
        "target_hit":           None,
        "range_hit":            None,
        "price_error":          None,
        "graded":               False,
    }
    mem["predictions"].append(pred)
    mem["stats"]["total_predictions"] += 1
    _save(mem)
    return pred_id


# ─────────────────────────────────────────────
# OUTCOME GRADING  (call at start of each cycle)
# ─────────────────────────────────────────────

def grade_last_prediction(current_price: float):
    """Grade the most recent ungraded prediction using current price."""
    mem = _load()
    graded_any = False

    for pred in reversed(mem["predictions"]):
        if pred["graded"]:
            break
        # Fix #3: use stored contract_expiry as deadline; fall back to +1h for legacy records
        contract_expiry = pred.get("contract_expiry", "")
        if contract_expiry:
            try:
                deadline = datetime.fromisoformat(
                    contract_expiry.rstrip("Z") + "+00:00"
                ).replace(tzinfo=None)
            except Exception:
                deadline = datetime.strptime(pred["ts"], "%Y-%m-%d %H:%M") + timedelta(hours=1)
        else:
            deadline = datetime.strptime(pred["ts"], "%Y-%m-%d %H:%M") + timedelta(hours=1)
        if datetime.utcnow() < deadline:
            break

        pred["actual_price"] = round(current_price, 2)
        pred["target_hit"]   = current_price >= pred["target"]
        # Legacy records predate range_low/range_high — leave range_hit unknown (None)
        # rather than KeyError-ing the whole grading cycle.
        rl, rh = pred.get("range_low"), pred.get("range_high")
        pred["range_hit"]    = (rl <= current_price <= rh) if rl is not None and rh is not None else None
        # predicted_price may be a non-numeric placeholder ("?") from a Claude
        # fallback — guard the float() so grading can't crash the whole cycle on it.
        try:
            _pp = float(str(pred["predicted_price"]).replace(",", "").replace("$", ""))
            pred["price_error"] = round(abs(current_price - _pp), 2)
        except (ValueError, TypeError):
            pred["price_error"] = None
        pred["graded"]       = True

        s = mem["stats"]
        if pred["target_hit"]:
            s["correct_target"] += 1
            s["current_streak"] += 1
            s["best_streak"] = max(s["best_streak"], s["current_streak"])
        else:
            s["current_streak"] = 0

        if pred["range_hit"]:
            s["correct_range"] += 1

        if pred["bet"] == "YES":
            s["total_bet_yes"] += 1
            if pred["target_hit"]:
                s["correct_bet_yes"] += 1
        elif pred["bet"] == "NO":
            s["total_bet_no"] += 1
            if not pred["target_hit"]:
                s["correct_bet_no"] += 1

        # Rolling avg error — skip when this prediction had no numeric price (e.g. "?"),
        # so a non-numeric placeholder can't corrupt the average or crash the cycle.
        if pred["price_error"] is not None:
            n = s["total_predictions"]
            s["avg_error_dollars"] = round(
                (s["avg_error_dollars"] * (n - 1) + pred["price_error"]) / n, 2
            ) if n > 0 else pred["price_error"]

        graded_any = True
        break   # only grade one at a time

    if graded_any:
        _save(mem)

    return graded_any


# ─────────────────────────────────────────────
# CLOSING-PROB SNAPSHOT  (Fix #2: capture Kalshi market price near expiry)
# ─────────────────────────────────────────────

def check_closing_snapshots(markets_by_ticker: dict) -> int:
    """For each ungraded prediction within 15 min of its contract_expiry (and not yet
    snapshotted), store the current Kalshi yes_price as closing_implied_prob.
    Returns number of predictions snapshotted."""
    from datetime import timezone
    mem = _load()
    now = datetime.now(timezone.utc)
    count = 0
    for pred in mem["predictions"]:
        if pred.get("graded"):
            continue
        if not pred.get("market_ticker") or not pred.get("contract_expiry"):
            continue
        if pred.get("closing_implied_prob") is not None:
            continue
        try:
            exp = datetime.fromisoformat(pred["contract_expiry"].rstrip("Z") + "+00:00")
        except Exception:
            continue
        window_open = exp - timedelta(minutes=15)
        if window_open <= now <= exp + timedelta(minutes=5):
            m = markets_by_ticker.get(pred["market_ticker"])
            if m:
                pred["closing_implied_prob"] = round(m["yes_price"], 4)
                count += 1
    if count:
        _save(mem)
    return count


def force_closing_snapshot(pred_id: str, closing_prob: float) -> bool:
    """Directly store a closing_implied_prob on any ungraded prediction by ID.
    Used for testing or manual backfill."""
    mem = _load()
    for pred in mem["predictions"]:
        if pred["id"] == pred_id and not pred.get("graded"):
            pred["closing_implied_prob"] = round(closing_prob, 4)
            _save(mem)
            return True
    return False


# ─────────────────────────────────────────────
# CONTEXT BUILDER  (feed into Claude prompt)
# ─────────────────────────────────────────────

def build_context() -> str:
    """Return a rich context string for Claude to reason over."""
    mem = _load()
    s   = mem["stats"]
    prices = mem["prices"]
    preds  = mem["predictions"]

    lines = []

    # ── Accuracy block ──
    total = s["total_predictions"]
    if total > 0:
        t_acc = round(s["correct_target"] / total * 100)
        r_acc = round(s["correct_range"]  / total * 100)
        lines.append(f"MY TRACK RECORD ({total} predictions):")
        lines.append(f"  Target accuracy: {t_acc}% | Range accuracy: {r_acc}%")
        lines.append(f"  Avg price error: ${s['avg_error_dollars']}")
        lines.append(f"  Current streak: {s['current_streak']} | Best: {s['best_streak']}")
        by = s["total_bet_yes"]
        bn = s["total_bet_no"]
        if by > 0:
            lines.append(f"  BET YES wins: {s['correct_bet_yes']}/{by} ({round(s['correct_bet_yes']/by*100)}%)")
        if bn > 0:
            lines.append(f"  BET NO wins:  {s['correct_bet_no']}/{bn} ({round(s['correct_bet_no']/bn*100)}%)")
    else:
        lines.append("MY TRACK RECORD: No predictions graded yet.")

    # ── Recent price action (last 24 ticks) ──
    if prices:
        recent = prices[-24:]
        p_vals = [p["price"] for p in recent]
        p_high = max(p_vals)
        p_low  = min(p_vals)
        p_open = recent[0]["price"]
        p_now  = recent[-1]["price"]
        chg    = round((p_now - p_open) / p_open * 100, 2)
        lines.append(f"\nLAST 24H PRICE ACTION:")
        lines.append(f"  Open: ${p_open:,.2f} | High: ${p_high:,.2f} | Low: ${p_low:,.2f} | Now: ${p_now:,.2f} ({chg:+.2f}%)")
        lines.append(f"  Range: ${p_high - p_low:,.2f} spread")

        # Hourly price list (last 12)
        hourly = recent[-12:]
        ticks  = " → ".join([f"${p['price']:,.0f}" for p in hourly])
        lines.append(f"  Hourly: {ticks}")

    # ── RSI trend ──
    if len(prices) >= 6:
        rsi_vals = [p["rsi"] for p in prices[-6:]]
        rsi_trend = "rising" if rsi_vals[-1] > rsi_vals[0] else "falling"
        lines.append(f"\nRSI TREND (6h): {' → '.join([str(r) for r in rsi_vals])} ({rsi_trend})")

    # ── Today's summary ──
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if today in mem["daily_summaries"]:
        ds = mem["daily_summaries"][today]
        lines.append(f"\nTODAY'S SESSION:")
        lines.append(f"  Open: ${ds['open']:,.2f} | High: ${ds['high']:,.2f} | Low: ${ds['low']:,.2f} | Close: ${ds['close']:,.2f}")
        lines.append(f"  Avg RSI: {ds['avg_rsi']} | Ticks logged: {ds['ticks']}")

    # ── Last 5 prediction outcomes ──
    graded = [p for p in preds if p["graded"]]
    if graded:
        lines.append(f"\nRECENT PREDICTION OUTCOMES:")
        for p in graded[-5:]:
            # Legacy records may lack target_hit/range_hit — render "?" for unknown.
            hit  = "?" if p.get("target_hit") is None else ("✓" if p["target_hit"] else "✗")
            rhit = "?" if p.get("range_hit")  is None else ("✓" if p["range_hit"]  else "✗")
            err  = "?" if p.get("price_error") is None else f"${p['price_error']}"
            lines.append(
                f"  {p.get('ts','?')} | Pred ${p.get('predicted_price','?')} | "
                f"Actual ${p.get('actual_price','?')} | "
                f"Target{hit} Range{rhit} | Err {err} | Bet:{p.get('bet','?')}"
            )

    # ── Ungraded (pending) prediction ──
    pending = [p for p in preds if not p["graded"]]
    if pending:
        last = pending[-1]
        lines.append(f"\nLAST PREDICTION (pending grade):")
        lines.append(f"  Made at: {last['ts']} | Target: ${last['target']} | Bet: {last['bet']}")
        lines.append(f"  Reason: {last['reason']}")

    # ── Multi-day trend ──
    summaries = mem["daily_summaries"]
    if len(summaries) >= 3:
        days = sorted(summaries.keys())[-7:]
        lines.append(f"\nDAILY CLOSES (last {len(days)} days):")
        for d in days:
            ds = summaries[d]
            chg = round((ds["close"] - ds["open"]) / ds["open"] * 100, 2)
            lines.append(f"  {d}: ${ds['close']:,.2f} ({chg:+.2f}%) H:${ds['high']:,.0f} L:${ds['low']:,.0f}")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# QUICK STATS  (for Telegram message footer)
# ─────────────────────────────────────────────

def get_stats_line() -> str:
    mem = _load()
    s   = mem["stats"]
    total = s["total_predictions"]
    graded = len([p for p in mem["predictions"] if p["graded"]])
    if graded == 0:
        return f"Learning... ({total} predictions logged, 0 graded)"
    t_acc = round(s["correct_target"] / graded * 100)
    r_acc = round(s["correct_range"]  / graded * 100)
    streak = s["current_streak"]
    return (
        f"Target: {t_acc}% | Range: {r_acc}% | "
        f"Streak: {streak} | Err: ±${s['avg_error_dollars']} "
        f"({graded} graded)"
    )


def get_support_resistance() -> dict:
    """Find rough S/R levels from recent price history."""
    mem   = _load()
    prices = [p["price"] for p in mem["prices"][-168:]]  # last 7 days
    if len(prices) < 10:
        return {}
    p_high = max(prices)
    p_low  = min(prices)
    p_avg  = round(sum(prices) / len(prices), 2)
    return {"resistance": p_high, "support": p_low, "avg": p_avg}
