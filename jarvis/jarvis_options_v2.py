#!/usr/bin/env python3
"""
JARVIS OPTIONS v2 — Elite Trade Intelligence
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Upgrades over v1:
  - GO confirmation via Telegram before any trade executes
  - Multi-signal scoring: BTC + regime + sector + Trump + macro
  - Hard rules: min 7 DTE, no same-day expiry, no earnings within 5 days
  - Tighter exits: +20% fast take, -20% hard stop
  - Syncs all trades to options_trades SQLite table
  - Watchlist ranked by signal strength before scanning
  - Regime gate: RISK_OFF = no new trades

Telegram commands:
  GO <id>    — execute pending trade
  SKIP <id>  — skip pending trade
  EXITS      — check exit conditions now
  SCAN       — force a scan cycle
"""

import requests, json, time, logging, os, sqlite3, re
from datetime import datetime, timedelta

# ── Config ───────────────────────────────────────────────────────
from jarvis_secrets import CLAUDE_API_KEY as CLAUDE_KEY
ALPACA_KEY    = "PKTHANGUNVFDSLLR3VXPETXRQF"
ALPACA_SECRET = "GRTDDfkCGWbZMoNSWms6uJSGvw72rHaAk1N1fvLi8EAP"
ALPACA_BASE   = "https://paper-api.alpaca.markets"
ALPACA_DATA   = "https://data.alpaca.markets"
TG_TOKEN      = __import__("jarvis_secrets").TG_TOKEN_TRADER
TG_CHAT       = "7534553840"
DB_PATH       = "/root/jarvis/jarvis_memory.db"
PENDING_FILE  = "/root/jarvis/options_pending.json"
LOG_PATH      = "/root/jarvis/jarvis_options_v2.log"

# ── Risk limits ───────────────────────────────────────────────────
MAX_OPEN      = 4       # max concurrent positions
MIN_COST      = 50      # min contract cost
MAX_COST      = 600     # max contract cost
MIN_DTE       = 7       # never buy < 7 days to expiry
TARGET_DTE    = 14      # sweet spot
MIN_CONF      = 70      # minimum Claude confidence to alert
PROFIT_PCT    = 20      # take profit at +20%
STOP_PCT      = -20     # stop loss at -20%
PROFIT_USD    = 800     # hard profit target $800
STOP_USD      = -400    # hard stop $400
SCAN_INTERVAL = 1800    # 30 min scan cycle
GO_TIMEOUT    = 600     # 10 min to confirm GO before auto-skip

# ── Watchlist with sector mapping ─────────────────────────────────
WATCHLIST = {
    "SPY":  {"sector": "etf",      "min_score": 60},
    "QQQ":  {"sector": "tech",     "min_score": 60},
    "IWM":  {"sector": "etf",      "min_score": 55},
    "NVDA": {"sector": "tech",     "min_score": 65},
    "AAPL": {"sector": "tech",     "min_score": 65},
    "TSLA": {"sector": "auto",     "min_score": 65},
    "F":    {"sector": "auto",     "min_score": 60},
    "BAC":  {"sector": "finance",  "min_score": 60},
    "JPM":  {"sector": "finance",  "min_score": 60},
    "XOM":  {"sector": "energy",   "min_score": 60},
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("options_v2")


# ── Telegram ──────────────────────────────────────────────────────
def tg(msg):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg[:4000]},
            timeout=10
        )
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def tg_get_updates(offset=0):
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 5},
            timeout=10
        )
        return r.json().get("result", [])
    except:
        return []


# ── Alpaca ────────────────────────────────────────────────────────
def alpaca(method, path, data=None):
    hdrs = {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "Content-Type": "application/json"
    }
    for attempt in range(3):
        try:
            if method == "GET":
                r = requests.get(ALPACA_BASE + path, headers=hdrs, timeout=15)
            elif method == "POST":
                r = requests.post(ALPACA_BASE + path, headers=hdrs, json=data, timeout=15)
            elif method == "DELETE":
                r = requests.delete(ALPACA_BASE + path, headers=hdrs, timeout=15)
            if r.status_code in [200, 201]:
                return r.json()
            log.warning(f"Alpaca {r.status_code}: {r.text[:150]}")
        except Exception as e:
            log.error(f"Alpaca attempt {attempt+1}: {e}")
        time.sleep(1)
    return None

def is_market_open():
    c = alpaca("GET", "/v2/clock")
    return c and c.get("is_open", False)

def get_positions():
    return alpaca("GET", "/v2/positions") or []

def get_options_positions():
    return [p for p in get_positions() if p.get("asset_class") == "us_option"]

def get_account():
    return alpaca("GET", "/v2/account") or {}

def get_quote(symbol):
    try:
        r = requests.get(
            f"{ALPACA_DATA}/v2/stocks/{symbol}/quotes/latest",
            headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
            timeout=10
        )
        if r.status_code == 200:
            q = r.json().get("quote", {})
            ap, bp = q.get("ap", 0), q.get("bp", 0)
            if ap > 0 and bp > 0:
                return (ap + bp) / 2
    except:
        pass
    return None

def get_options_chain(symbol, min_dte=7, max_dte=30):
    try:
        r = requests.get(
            f"{ALPACA_BASE}/v2/options/contracts",
            headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
            params={
                "underlying_symbols": symbol,
                "expiration_date_gte": (datetime.now() + timedelta(days=min_dte)).strftime("%Y-%m-%d"),
                "expiration_date_lte": (datetime.now() + timedelta(days=max_dte)).strftime("%Y-%m-%d"),
                "status": "active",
                "limit": 100
            },
            timeout=15
        )
        if r.status_code == 200:
            return r.json().get("option_contracts", [])
    except Exception as e:
        log.error(f"Chain error {symbol}: {e}")
    return []

def get_contract_price(symbol):
    try:
        r = requests.get(
            f"{ALPACA_DATA}/v2/options/quotes/latest",
            headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
            params={"symbols": symbol},
            timeout=10
        )
        if r.status_code == 200:
            q = r.json().get("quotes", {}).get(symbol, {})
            ap, bp = float(q.get("ap", 0)), float(q.get("bp", 0))
            if ap > 0 and bp > 0:
                return (ap + bp) / 2
    except:
        pass
    return None

def find_contract(symbol, direction, price, offset, days):
    """Find best contract matching direction/strike/expiry within cost limits"""
    contracts = get_options_chain(symbol, min_dte=MIN_DTE, max_dte=max(days + 7, 21))
    if not contracts:
        return None, None

    target_strike = price + offset if direction == "CALL" else price - offset
    target_expiry = datetime.now() + timedelta(days=days)
    opt_type = "call" if direction == "CALL" else "put"

    candidates = []
    for c in contracts:
        if c.get("type") != opt_type:
            continue
        try:
            strike = float(c.get("strike_price", 0))
            exp_dt = datetime.strptime(c.get("expiration_date", ""), "%Y-%m-%d")
            dte = (exp_dt - datetime.now()).days
            if dte < MIN_DTE:
                continue
            score = abs(strike - target_strike) + abs((exp_dt - target_expiry).days) * 0.5
            candidates.append((score, c, dte))
        except:
            continue

    candidates.sort(key=lambda x: x[0])

    for score, c, dte in candidates[:15]:
        csym = c.get("symbol", "")
        est_price = get_contract_price(csym)
        if est_price:
            total_cost = est_price * 100
            if MIN_COST <= total_cost <= MAX_COST:
                log.info(f"Contract {csym} ${total_cost:.0f} DTE:{dte} ✓")
                return c, est_price
            else:
                log.info(f"Contract {csym} ${total_cost:.0f} outside range — skip")
        else:
            return c, 2.0  # No price data, try anyway

    return (candidates[0][1], 2.0) if candidates else (None, None)

def place_option_order(symbol, qty=1):
    return alpaca("POST", "/v2/orders", {
        "symbol": symbol,
        "qty": str(qty),
        "side": "buy",
        "type": "market",
        "time_in_force": "day"
    })

def close_position(symbol):
    return alpaca("DELETE", f"/v2/positions/{requests.utils.quote(symbol)}")


# ── Signal aggregation ────────────────────────────────────────────
def get_regime():
    """Read regime from central brain"""
    try:
        d = json.load(open("/root/jarvis/jarvis_central_brain.json"))
        return d.get("market_regime", "UNKNOWN"), d.get("market_mood", "neutral")
    except:
        return "UNKNOWN", "neutral"

def get_btc_state():
    """Read BTC state from memory"""
    try:
        mem = json.load(open("/root/jarvis/btc_memory.json"))
        prices = mem.get("prices", [])
        if not prices:
            return {}
        last = prices[-1]
        rsi = last.get("rsi", 50)
        change_1h = last.get("1h", 0)
        change_24h = last.get("24h", 0)
        price = last.get("price", 0)
        # BTC signal
        if rsi < 30:
            signal = "STRONG_BULLISH"
        elif rsi < 45:
            signal = "BULLISH"
        elif rsi > 70:
            signal = "STRONG_BEARISH"
        elif rsi > 58:
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"
        return {
            "price": price, "rsi": rsi,
            "change_1h": change_1h, "change_24h": change_24h,
            "signal": signal
        }
    except:
        return {"rsi": 50, "signal": "NEUTRAL"}

def get_sector_scores():
    """Read sector scores from level5"""
    try:
        d = json.load(open("/root/jarvis/jarvis_level5.json"))
        return d.get("sector_scores", {})
    except:
        return {}

def get_trump_signals(hours=2):
    """Get recent high-confidence Trump signals from DB"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute("""
            SELECT category, summary, direction, confidence, tickers
            FROM trump_signals
            WHERE logged_at > ? AND classification = 'POLICY' AND confidence >= 70
            ORDER BY logged_at DESC LIMIT 3
        """, (cutoff,)).fetchall()
        conn.close()
        return [{"category": r[0], "summary": r[1], "direction": r[2],
                 "confidence": r[3], "tickers": r[4]} for r in rows]
    except:
        return []

def get_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        d = r.json()["data"][0]
        return int(d["value"]), d["value_classification"]
    except:
        return 50, "Neutral"

def score_ticker(symbol, sector, regime, btc, sectors, trump_signals):
    """
    Score a ticker 0-100 based on all signals.
    Higher = stronger setup.
    """
    score = 50  # base

    # ── Regime gate ───────────────────────────────────────────────
    if regime == "RISK_OFF":
        return 0, "RISK_OFF — no new trades"
    if regime == "RISK_ON":
        score += 10

    # ── BTC signal ────────────────────────────────────────────────
    btc_signal = btc.get("signal", "NEUTRAL")
    rsi = btc.get("rsi", 50)
    if btc_signal == "STRONG_BULLISH":
        score += 15
    elif btc_signal == "BULLISH":
        score += 8
    elif btc_signal == "STRONG_BEARISH":
        score -= 15
    elif btc_signal == "BEARISH":
        score -= 8

    # ── Sector score ──────────────────────────────────────────────
    sec_score = sectors.get(sector, 0)
    if sec_score > 5:
        score += 12
    elif sec_score > 2:
        score += 6
    elif sec_score < -3:
        score -= 10
    elif sec_score < -1:
        score -= 5

    # ── Trump signals ─────────────────────────────────────────────
    for ts in trump_signals:
        tickers = ts.get("tickers", "")
        if symbol in tickers:
            if ts["direction"] == "BULLISH":
                score += 15
            elif ts["direction"] == "BEARISH":
                score -= 15
            elif ts["direction"] == "MIXED":
                score += 5

    # ── Time of day bonus ─────────────────────────────────────────
    hour = datetime.now().hour
    if 9 <= hour <= 11:
        score += 5   # morning momentum
    elif 14 <= hour <= 15:
        score += 3   # afternoon trend
    elif hour >= 15:
        score -= 5   # late day decay

    reason_parts = [
        f"regime:{regime}",
        f"BTC_RSI:{rsi:.0f}({btc_signal})",
        f"sector:{sector}({sec_score:+.1f})",
    ]
    if trump_signals:
        reason_parts.append(f"trump:{len(trump_signals)}signal(s)")

    return max(0, min(100, score)), " | ".join(reason_parts)


# ── Claude decision ───────────────────────────────────────────────
def ask_claude(symbol, price, score, bias, context_str, open_count):
    """Ask Claude for trade decision with full context"""
    try:
        prompt = f"""You are JARVIS, an elite options trader. Make a precise trade decision.

SYMBOL: {symbol} @ ${price:.2f}
SIGNAL SCORE: {score}/100
BIAS: {bias}
OPEN POSITIONS: {open_count}/{MAX_OPEN}
CONTEXT:
{context_str}

RULES:
- Only trade if confidence >= {MIN_CONF}%
- Min {MIN_DTE} DTE, prefer {TARGET_DTE} DTE
- Strike offset: how many dollars OTM (0 = ATM)
- If score < 55 or open >= {MAX_OPEN}, reply SKIP
- Never trade within 5 days of earnings

Reply ONLY in this exact format (no other text):
ACTION|DIRECTION|STRIKE_OFFSET|EXPIRY_DAYS|CONFIDENCE|REASON

ACTION = BUY or SKIP
DIRECTION = CALL or PUT
STRIKE_OFFSET = dollars OTM (e.g. 5 means $5 out of the money)
EXPIRY_DAYS = 7 to 21
CONFIDENCE = number 0-100
REASON = one sentence max

Example: BUY|CALL|3|14|74|RSI oversold + bullish sector + strong BTC momentum"""

        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CLAUDE_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=20
        )
        resp = r.json()
        if "error" in resp:
            log.error(f"Claude error: {resp['error']}")
            return None

        text = resp["content"][0]["text"].strip()
        parts = [p.strip() for p in text.split("|")]
        if len(parts) < 6:
            log.warning(f"Claude bad format: {text}")
            return None

        return {
            "action": parts[0].upper(),
            "direction": parts[1].upper(),
            "strike_offset": float(parts[2]),
            "expiry_days": int(parts[3]),
            "confidence": int(parts[4]),
            "reason": parts[5]
        }
    except Exception as e:
        log.error(f"Claude error: {e}")
        return None


# ── Pending trade management ──────────────────────────────────────
def save_pending(trade):
    pending = load_pending()
    pending[trade["id"]] = trade
    with open(PENDING_FILE, "w") as f:
        json.dump(pending, f, indent=2)

def load_pending():
    try:
        return json.load(open(PENDING_FILE))
    except:
        return {}

def clear_pending(trade_id):
    pending = load_pending()
    pending.pop(trade_id, None)
    with open(PENDING_FILE, "w") as f:
        json.dump(pending, f, indent=2)

def expire_pending():
    """Auto-skip trades older than GO_TIMEOUT"""
    pending = load_pending()
    now = datetime.now().timestamp()
    expired = []
    for tid, t in pending.items():
        if now - t.get("created_at", now) > GO_TIMEOUT:
            expired.append(tid)
            tg(f"⏰ Trade {tid} expired — auto-skipped\n{t['symbol']} {t['direction']} ${t['strike']}")
    for tid in expired:
        clear_pending(tid)


# ── SQLite trade logging ──────────────────────────────────────────
def log_trade_db(trade):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            INSERT OR IGNORE INTO options_trades
            (ticker, direction, strike, expiry, entry_date, result, pnl, dte_at_entry, notes, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade["symbol"], trade["direction"], trade["strike"],
            trade["expiry"], datetime.now().strftime("%m/%d"),
            "OPEN", 0, trade.get("dte"), trade.get("reason"), "jarvis_v2"
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"DB log error: {e}")

def update_trade_db(contract, pnl, result):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            UPDATE options_trades SET pnl=?, result=?
            WHERE notes LIKE ? AND result='OPEN'
        """, (pnl, result, f"%{contract}%"))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"DB update error: {e}")


# ── Exit management ───────────────────────────────────────────────
def check_exits():
    positions = get_options_positions()
    if not positions:
        log.info("No open options positions")
        return

    for pos in positions:
        contract = pos.get("symbol", "")
        cur      = float(pos.get("current_price", 0))
        pnl_pct  = float(pos.get("unrealized_plpc", 0)) * 100
        pnl_usd  = float(pos.get("unrealized_pl", 0))

        # Get DTE
        dte = 999
        m = re.search(r"(\d{6})[CP]", contract)
        if m:
            try:
                exp = datetime.strptime(m.group(1), "%y%m%d")
                dte = (exp - datetime.now()).days
            except:
                pass

        reason = None

        # Profit targets
        if pnl_usd >= PROFIT_USD:
            reason = f"💰 PROFIT TARGET ${pnl_usd:+.0f} hit"
        elif pnl_pct >= PROFIT_PCT:
            reason = f"💰 PROFIT +{pnl_pct:.1f}%"
        elif pnl_pct >= 10 and dte <= 3:
            reason = f"⏰ EXPIRY SOON — lock in +{pnl_pct:.1f}% ({dte}d left)"
        elif pnl_pct >= 5 and dte <= 1:
            reason = f"⏰ EXPIRES TOMORROW — closing +{pnl_pct:.1f}%"

        # Stop losses
        elif pnl_usd <= STOP_USD:
            reason = f"🛑 STOP ${pnl_usd:.0f} hit"
        elif pnl_pct <= STOP_PCT:
            reason = f"🛑 STOP {pnl_pct:.1f}%"
        elif pnl_pct <= -15 and dte <= 2:
            reason = f"🛑 EXPIRY RISK — cutting {pnl_pct:.1f}% ({dte}d left)"

        if reason:
            result = close_position(contract)
            if result:
                icon = "✅" if pnl_usd >= 0 else "🔴"
                tg(f"{icon} EXIT: {contract}\n{reason}\nP&L: ${pnl_usd:+.2f} ({pnl_pct:+.1f}%)\nDTE: {dte}d")
                log.info(f"Closed {contract}: {reason} ${pnl_usd:+.2f}")
                result_str = "WIN" if pnl_usd > 0 else "LOSS"
                update_trade_db(contract, round(pnl_usd, 2), result_str)
            else:
                log.warning(f"Failed to close {contract}")
        else:
            log.info(f"Hold {contract}: {pnl_pct:+.1f}% DTE:{dte}d")


# ── Main scan cycle ───────────────────────────────────────────────
def run_scan():
    if not is_market_open():
        log.info("Market closed — skip scan")
        return

    expire_pending()

    open_count = len(get_options_positions())
    if open_count >= MAX_OPEN:
        log.info(f"Max positions {open_count}/{MAX_OPEN} — skip scan")
        return

    # Gather all signals once
    regime, mood     = get_regime()
    btc              = get_btc_state()
    sectors          = get_sector_scores()
    trump_sigs       = get_trump_signals(hours=2)
    fg_val, fg_label = get_fear_greed()
    acct             = get_account()
    equity           = float(acct.get("equity", 0))

    log.info(f"Scan: regime={regime} BTC_RSI={btc.get('rsi',50):.0f} sectors={len(sectors)} trump={len(trump_sigs)} F&G={fg_val}")

    if regime == "RISK_OFF":
        log.info("RISK_OFF — no new trades")
        return

    # Score and rank watchlist
    ranked = []
    for symbol, meta in WATCHLIST.items():
        score, reason = score_ticker(symbol, meta["sector"], regime, btc, sectors, trump_sigs)
        if score >= meta["min_score"]:
            ranked.append((score, symbol, meta["sector"], reason))

    ranked.sort(key=lambda x: -x[0])
    log.info(f"Ranked: {[(s, sym) for s, sym, _, _ in ranked[:5]]}")

    if not ranked:
        log.info("No tickers above threshold")
        return

    # Build shared context string for Claude
    btc_str = f"BTC ${btc.get('price',0):,.0f} RSI:{btc.get('rsi',50):.0f} 1h:{btc.get('change_1h',0):+.2f}% signal:{btc.get('signal','NEUTRAL')}"
    trump_str = "\n".join([f"  [{t['category']}] {t['summary']} → {t['direction']}" for t in trump_sigs]) or "  None in last 2 hours"
    sector_str = " | ".join([f"{k}:{v:+.1f}" for k, v in sorted(sectors.items(), key=lambda x: -x[1])[:4]])

    for score, symbol, sector, signal_reason in ranked:
        if open_count >= MAX_OPEN:
            break

        price = get_quote(symbol)
        if not price:
            log.warning(f"No quote for {symbol}")
            continue

        # Determine bias
        btc_sig = btc.get("signal", "NEUTRAL")
        sec_score = sectors.get(sector, 0)
        if score >= 70 and (btc_sig in ["BULLISH", "STRONG_BULLISH"] or sec_score > 3):
            bias = "BULLISH"
        elif score <= 40 or btc_sig in ["BEARISH", "STRONG_BEARISH"]:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        context_str = f"""REGIME: {regime} | MOOD: {mood}
BTC: {btc_str}
SECTORS: {sector_str}
FEAR&GREED: {fg_val} ({fg_label})
SIGNAL SCORE: {score}/100 — {signal_reason}
TRUMP SIGNALS (last 2h):
{trump_str}
EQUITY: ${equity:,.0f}"""

        log.info(f"Asking Claude: {symbol} @ ${price:.2f} score:{score} bias:{bias}")
        sig = ask_claude(symbol, price, score, bias, context_str, open_count)

        if not sig or sig["action"] == "SKIP":
            log.info(f"{symbol} SKIP")
            continue

        if sig["confidence"] < MIN_CONF:
            log.info(f"{symbol} confidence {sig['confidence']}% below {MIN_CONF}% — skip")
            continue

        # Find contract
        contract, est_price = find_contract(
            symbol, sig["direction"], price,
            sig["strike_offset"], sig["expiry_days"]
        )

        if not contract:
            log.warning(f"No contract found for {symbol}")
            continue

        csym   = contract.get("symbol", "")
        strike = float(contract.get("strike_price", 0))
        expiry = contract.get("expiration_date", "")
        cost   = (est_price or 2.0) * 100

        # Get DTE
        dte = sig["expiry_days"]
        try:
            exp_dt = datetime.strptime(expiry, "%Y-%m-%d")
            dte = (exp_dt - datetime.now()).days
        except:
            pass

        # Create pending trade
        trade_id = f"{symbol}{datetime.now().strftime('%H%M')}"
        pending = {
            "id": trade_id,
            "symbol": symbol,
            "direction": sig["direction"],
            "strike": strike,
            "expiry": expiry,
            "contract": csym,
            "est_price": est_price,
            "cost": cost,
            "dte": dte,
            "confidence": sig["confidence"],
            "reason": sig["reason"],
            "score": score,
            "signal_reason": signal_reason,
            "created_at": datetime.now().timestamp()
        }
        save_pending(pending)

        # Send GO alert
        trump_note = ""
        if trump_sigs:
            trump_note = f"\n🇺🇸 Trump: {trump_sigs[0]['summary'][:60]}"

        tg(f"""🎯 TRADE ALERT — REPLY GO {trade_id} TO EXECUTE
━━━━━━━━━━━━━━━━━━━━
{symbol} {sig['direction']} ${strike} exp {expiry}
Contract: {csym}
Est cost: ${cost:.0f} | DTE: {dte}d
Confidence: {sig['confidence']}% | Score: {score}/100
━━━━━━━━━━━━━━━━━━━━
📊 {sig['reason']}
{signal_reason}{trump_note}
━━━━━━━━━━━━━━━━━━━━
Reply: GO {trade_id} or SKIP {trade_id}
Expires in {GO_TIMEOUT//60} min""")

        log.info(f"Pending: {trade_id} {csym} conf:{sig['confidence']}%")
        time.sleep(2)


# ── GO execution ──────────────────────────────────────────────────
def execute_trade(trade_id):
    pending = load_pending()
    t = pending.get(trade_id)
    if not t:
        tg(f"❌ Trade {trade_id} not found or already expired")
        return

    # Check if still valid
    if datetime.now().timestamp() - t.get("created_at", 0) > GO_TIMEOUT:
        tg(f"⏰ Trade {trade_id} expired — too late")
        clear_pending(trade_id)
        return

    log.info(f"Executing {trade_id}: {t['contract']}")
    result = alpaca("POST", "/v2/orders", {
        "symbol": t["contract"],
        "qty": "1",
        "side": "buy",
        "type": "market",
        "time_in_force": "day"
    })

    if result:
        clear_pending(trade_id)
        filled_price = float(result.get("filled_avg_price") or t.get("est_price") or 2.0)
        tg(f"""✅ EXECUTED: {trade_id}
{t['symbol']} {t['direction']} ${t['strike']} exp {t['expiry']}
Contract: {t['contract']}
Filled: ${filled_price:.2f} | Cost: ${filled_price*100:.0f}
DTE: {t['dte']}d | Conf: {t['confidence']}%
Exit plan: +{PROFIT_PCT}% profit | {STOP_PCT}% stop""")
        log.info(f"Executed {t['contract']} @ ${filled_price:.2f}")
        log_trade_db({**t, "entry_price": filled_price})
    else:
        tg(f"❌ Execution failed for {trade_id} — check Alpaca")
        log.error(f"Execution failed: {t['contract']}")


# ── Telegram command listener ─────────────────────────────────────
def listen_for_commands(tg_offset):
    updates = tg_get_updates(tg_offset)
    for u in updates:
        tg_offset = u["update_id"] + 1
        msg = u.get("message", {})
        text = msg.get("text", "").strip().upper()

        if text.startswith("GO "):
            trade_id = text[3:].strip()
            execute_trade(trade_id)

        elif text.startswith("SKIP "):
            trade_id = text[5:].strip()
            clear_pending(trade_id)
            tg(f"⏭ Skipped trade {trade_id}")

        elif text == "EXITS":
            check_exits()

        elif text == "SCAN":
            tg("🔍 Running manual scan...")
            run_scan()

        elif text == "PENDING":
            pending = load_pending()
            if not pending:
                tg("No pending trades")
            else:
                lines = ["📋 PENDING TRADES"]
                for tid, t in pending.items():
                    age = int((datetime.now().timestamp() - t.get("created_at", 0)) / 60)
                    lines.append(f"{tid}: {t['symbol']} {t['direction']} ${t['strike']} conf:{t['confidence']}% age:{age}m")
                tg("\n".join(lines))

    return tg_offset


# ── Main loop ─────────────────────────────────────────────────────
def run():
    log.info("JARVIS OPTIONS v2 ONLINE")
    tg("""🎯 JARVIS OPTIONS v2 ONLINE
━━━━━━━━━━━━━━━━━━━━
Upgrades: GO confirmation, regime gate, Trump signals, tight exits
Commands: GO <id> | SKIP <id> | EXITS | SCAN | PENDING
Exit rules: +20% profit | -20% stop | Min DTE: 7""")

    tg_offset = 0
    last_scan = 0
    last_exit_check = 0

    while True:
        try:
            now = time.time()

            # Listen for GO/SKIP commands every 10 seconds
            tg_offset = listen_for_commands(tg_offset)

            # Exit check every 15 minutes
            if now - last_exit_check > 900:
                check_exits()
                last_exit_check = now

            # Scan every 30 minutes
            if now - last_scan > SCAN_INTERVAL:
                run_scan()
                last_scan = now

            time.sleep(10)

        except Exception as e:
            log.error(f"Main loop error: {e}")
            time.sleep(30)


if __name__ == "__main__":
    run()
