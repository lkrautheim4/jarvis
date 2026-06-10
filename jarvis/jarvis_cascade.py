#!/usr/bin/env python3
"""
JARVIS CASCADE — intraday SPY drawdown circuit-breaker.

Polls SPY every 5 min, measures % change from today's open, and fires staged
CASCADE alerts (L0/L1/L2) — each once per session, reset at 4am ET. On a trigger
it tightens Kalshi floors, forces an options put scan, flips market mode, and
logs the event. After any cascade it watches for a dead-cat bounce.

Guards: only evaluates during regular market hours on a weekday AND only when
Yahoo's data is from today (no firing on stale weekend/after-hours data).
"""
import json, os, time, logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
import jarvis_brain as _jb_hb
import jarvis_memory_db as memdb
import jarvis_macro  # reuse compute_market_mode() + get_vix()

# ── config ───────────────────────────────────────────────────────────────────
TG_TOKEN  = __import__("jarvis_secrets").TG_TOKEN_TRADER  # shared bot token
CHAT_ID   = "7534553840"
STATE_FILE = "/root/jarvis/jarvis_cascade.json"   # absolute — bots may run with CWD=/root
POLL = 300                       # 5 minutes
ET = ZoneInfo("America/New_York")

# Threshold (% from open) -> message. Ordered most-shallow first.
LEVELS = [
    ("L0", -0.75, "⚡ CAUTION: SPY -0.75%. Watch put setups. Mode check running."),
    ("L1", -1.5,  "🔴 CASCADE L1: SPY -1.5%. Tightening rules. Forcing put scan."),
    ("L2", -2.5,  "🚨 CASCADE L2: SPY -2.5%. FULL PROTECTION. No longs. YES bets disabled."),
]
BOUNCE_PCT   = 0.5    # SPY must bounce >=0.5% off intraday low for a dead-cat
VIX_ELEVATED = 20.0   # "VIX still elevated" threshold (matches protection-mode VIX)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("jarvis_cascade")


def tg(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      json={"chat_id": CHAT_ID, "text": msg}, timeout=10)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
        else:
            log.info(f"TG: {msg[:60]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")


# ── SPY data ─────────────────────────────────────────────────────────────────
def get_spy():
    """Return SPY snapshot or None. {price, open, pct, low, green_15m, fresh}."""
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/SPY?interval=5m&range=1d",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        res = r.json()["chart"]["result"][0]
        meta = res["meta"]
        ts = res.get("timestamp", [])
        q = res.get("indicators", {}).get("quote", [{}])[0]
        opens  = q.get("open", [])
        closes = q.get("close", [])
        # Session open = first real candle open (meta.regularMarketOpen is unreliable).
        first_open = next((o for o in opens if o is not None), None)
        price = meta.get("regularMarketPrice") or next(
            (c for c in reversed(closes) if c is not None), None)
        low = meta.get("regularMarketDayLow")
        if not (first_open and price):
            return None
        # Freshness: first candle must be today (ET), else it's a prior session.
        fresh = bool(ts) and datetime.fromtimestamp(ts[0], ET).date() == datetime.now(ET).date()
        # 15-min green = net higher than 15 min (3 x 5m candles) ago.
        green_15m = False
        valid_closes = [c for c in closes if c is not None]
        if len(opens) >= 3 and len(valid_closes) >= 1 and opens[-3] is not None:
            green_15m = valid_closes[-1] > opens[-3]
        return {
            "price": float(price),
            "open": float(first_open),
            "pct": round((price - first_open) / first_open * 100, 2),
            "low": float(low) if low else float(price),
            "green_15m": green_15m,
            "fresh": fresh,
        }
    except Exception as e:
        log.error(f"get_spy: {e}")
        return None


def get_vix_value():
    try:
        return float(jarvis_macro.get_vix().get("value", 0) or 0)
    except Exception:
        return 0.0


def get_regime_and_fg():
    """(regime, f_and_g) — regime from the canonical jarvis_memory.db brain,
    F&G from macro's latest output."""
    try:
        regime = memdb.get_regime("UNKNOWN")
    except Exception:
        regime = "UNKNOWN"
    try:
        with open("/root/jarvis/jarvis_macro.json") as f:
            fg = (json.load(f).get("fear_greed") or {}).get("current", 50)
    except Exception:
        fg = 50
    return regime, fg


def get_fg_value():
    """Fresh F&G pull via macro (alternative.me). NOTE: that index updates DAILY,
    not intraday, so the F&G-drop trigger rarely fires during a session."""
    try:
        return float(jarvis_macro.get_fear_greed_history().get("current", 50) or 50)
    except Exception:
        return 50.0


SNAPSHOT_TICKERS = ["SPY", "QQQ", "NVDA", "TSLA"]
def get_ticker_snapshot():
    """{ticker: price} at event time → market_events.ticker_snapshot. The cascade
    trigger event's snapshot is the per-ticker resistance reference for the reload."""
    snap = {}
    for t in SNAPSHOT_TICKERS:
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?interval=5m&range=1d",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            p = r.json()["chart"]["result"][0]["meta"].get("regularMarketPrice")
            if p:
                snap[t] = round(float(p), 2)
        except Exception:
            pass
    return snap


# ── session state ────────────────────────────────────────────────────────────
def session_key(now_et):
    """Trading session id — rolls over at 4am ET (so 12am-4am belongs to prev day)."""
    return (now_et - timedelta(hours=4)).strftime("%Y-%m-%d")


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(s):
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(s, f)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        log.error(f"save_state: {e}")


def fresh_state(key):
    return {"session": key, "fired": [], "cascade_active": False, "rip_fired": False}


# ── actions ──────────────────────────────────────────────────────────────────
def fire_level(level, spy, vix, regime, fg):
    """Run a cascade level's alert + actions + event log."""
    msg = next(m for (lv, _t, m) in LEVELS if lv == level)
    tg(msg)
    try:
        memdb.brain_set(f"cascade_{level.lower()}_fired", 1)  # persistent fired flag
        if level == "L1":
            memdb.brain_set("kalshi_no_floor", 0.82)
            memdb.brain_set("kalshi_yes_floor", 0.68)
            memdb.brain_set("force_put_scan", True)   # options brain picks this up
            log.info("L1: floors tightened (NO 0.82 / YES 0.68), force_put_scan set")
        # Mode check on every level.
        jarvis_macro.compute_market_mode(fg, vix, regime)
        if level == "L2":
            memdb.brain_set("kalshi_yes_disabled", True)
            memdb.brain_set("market_mode", "PROTECTION")  # force, after compute
            log.info("L2: YES bets disabled, market_mode forced PROTECTION")
    except Exception as e:
        log.error(f"fire_level {level} actions: {e}")
    # Structured market_events log + ticker snapshot (reload reads this snapshot).
    try:
        memdb.log_market_event(
            event_type=f"CASCADE_{level}",
            spy_price=spy["price"], spy_pct_change=spy["pct"],
            vix=vix, f_and_g=fg, market_mode=memdb.brain_get("market_mode"),
            notes=msg, ticker_snapshot=get_ticker_snapshot())
    except Exception as e:
        log.error(f"log_market_event {level}: {e}")


def check_dead_cat(spy, vix, state):
    """After any cascade: flag a dead-cat bounce once per session."""
    if not state.get("cascade_active") or state.get("rip_fired"):
        return
    bounce = (spy["price"] - spy["low"]) / spy["low"] * 100 if spy["low"] else 0
    if spy["green_15m"] and bounce >= BOUNCE_PCT and vix >= VIX_ELEVATED:
        tg("🎯 RIP ALERT: Dead cat bounce forming on SPY. Put reload window open. Wait for stall.")
        log.info(f"RIP: bounce {bounce:.2f}% off low, VIX {vix:.1f}")
        state["rip_fired"] = True
        try:
            memdb.brain_set("reload_scan", True)   # options brain runs the put reload
            memdb.log_market_event(
                event_type="DEAD_CAT", spy_price=spy["price"], spy_pct_change=spy["pct"],
                vix=vix, market_mode=memdb.brain_get("market_mode"),
                notes=f"bounce {bounce:.2f}% off low", ticker_snapshot=get_ticker_snapshot())
        except Exception as e:
            log.error(f"log_market_event DEAD_CAT: {e}")


def market_open(now_et):
    mins = now_et.hour * 60 + now_et.minute
    return now_et.weekday() < 5 and (9 * 60 + 30) <= mins <= (16 * 60)


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    log.info("JARVIS CASCADE ONLINE — SPY drawdown circuit-breaker")
    try:
        memdb.init_db()
    except Exception as e:
        log.error(f"init_db: {e}")
    tg("⚡ JARVIS CASCADE ONLINE — watching SPY for L0/L1/L2 drawdown cascades")

    while True:
        try:
            now_et = datetime.now(ET)
            state = load_state()
            sk = session_key(now_et)
            if state.get("session") != sk:
                state = fresh_state(sk)       # new session → reset all triggers
                save_state(state)
                # Clear cascade-driven brain overrides so they don't persist past
                # the 4am rollover (fired flags, YES-disable, tightened floors).
                try:
                    for _k in ("cascade_l0_fired", "cascade_l1_fired", "cascade_l2_fired", "kalshi_yes_disabled"):
                        memdb.brain_set(_k, 0)
                    memdb.brain_set("kalshi_yes_floor", None)
                    memdb.brain_set("kalshi_no_floor", None)
                except Exception as _e:
                    log.error(f"session brain reset: {_e}")
                log.info(f"New session {sk} — cascade triggers reset")

            if not market_open(now_et):
                _jb_hb.update_bot_heartbeat("jarvis_cascade")
                time.sleep(POLL); continue

            spy = get_spy()
            if not spy or not spy["fresh"]:
                if spy and not spy["fresh"]:
                    log.info("SPY data stale (not today) — skipping")
                _jb_hb.update_bot_heartbeat("jarvis_cascade")
                time.sleep(POLL); continue

            log.info(f"SPY ${spy['price']:.2f} {spy['pct']:+.2f}% from open")
            vix = get_vix_value()
            regime, fg = get_regime_and_fg()

            # Fire any breached, not-yet-fired level (a gap-down can fire several).
            for lv, thresh, _msg in LEVELS:
                if spy["pct"] <= thresh and lv not in state["fired"]:
                    fire_level(lv, spy, vix, regime, fg)
                    state["fired"].append(lv)
                    state["cascade_active"] = True
                    save_state(state)

            # Dead-cat detector runs after any cascade is active.
            check_dead_cat(spy, vix, state)
            save_state(state)

            # ── Intraday F&G/VIX drift vs morning baseline (checked every 30 min) ──
            if "fg_baseline" not in state:
                # First fresh RTH read of the session = the morning baseline.
                state["fg_baseline"] = get_fg_value()
                state["vix_baseline"] = vix
                state["last_drift"] = time.time()
                memdb.brain_set("fg_morning_baseline", state["fg_baseline"])
                memdb.brain_set("vix_morning_baseline", state["vix_baseline"])
                save_state(state)
                log.info(f"Morning baseline: F&G {state['fg_baseline']:.0f} VIX {state['vix_baseline']:.1f}")
            elif time.time() - state.get("last_drift", 0) >= 1800:
                state["last_drift"] = time.time()
                fg_now = get_fg_value()
                # F&G drop >= 3 points from morning read (Telegram once/session).
                if (not state.get("fg_shift_fired")) and (state["fg_baseline"] - fg_now) >= 3:
                    tg(f"⚠️ REGIME SHIFT: F&G dropped {state['fg_baseline']:.0f}→{fg_now:.0f}. Re-evaluating mode.")
                    jarvis_macro.compute_market_mode(fg_now, vix, regime)
                    memdb.brain_set("regime", regime)   # update regime in jarvis_memory.db
                    state["fg_shift_fired"] = True
                    log.info(f"Drift: F&G shift {state['fg_baseline']:.0f}→{fg_now:.0f}")
                    try:
                        memdb.log_market_event(
                            event_type="REGIME_SHIFT", spy_price=spy["price"], spy_pct_change=spy["pct"],
                            vix=vix, f_and_g=fg_now, market_mode=memdb.brain_get("market_mode"),
                            notes=f"F&G {state['fg_baseline']:.0f}->{fg_now:.0f}", ticker_snapshot=get_ticker_snapshot())
                    except Exception as _e:
                        log.error(f"log_market_event REGIME_SHIFT: {_e}")
                # VIX spike >= 10% from morning read (Telegram once/session).
                _vb = state.get("vix_baseline", 0)
                if (not state.get("vix_spike_fired")) and _vb > 0 and (vix - _vb) / _vb >= 0.10:
                    _pct = (vix - _vb) / _vb * 100
                    tg(f"⚠️ VIX SPIKE: {_vb:.1f}→{vix:.1f} (+{_pct:.0f}%). Tightening posture.")
                    jarvis_macro.compute_market_mode(fg_now, vix, regime)
                    state["vix_spike_fired"] = True
                    log.info(f"Drift: VIX spike {_vb:.1f}→{vix:.1f} (+{_pct:.0f}%)")
                    try:
                        memdb.log_market_event(
                            event_type="VIX_SPIKE", spy_price=spy["price"], spy_pct_change=spy["pct"],
                            vix=vix, f_and_g=fg_now, market_mode=memdb.brain_get("market_mode"),
                            notes=f"VIX {_vb:.1f}->{vix:.1f} (+{_pct:.0f}%)", ticker_snapshot=get_ticker_snapshot())
                    except Exception as _e:
                        log.error(f"log_market_event VIX_SPIKE: {_e}")
                save_state(state)

            _jb_hb.update_bot_heartbeat("jarvis_cascade")
        except Exception as e:
            log.error(f"Cascade loop: {e}")
        time.sleep(POLL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped")
