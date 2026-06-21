from watch_function import watch
#!/usr/bin/env python3
"""
JARVIS TRADER — Unified Trading Bot
Crypto + Stocks + Futures | Claude AI | Signal Fusion | Shared Brain
"""
import json, time, requests, os
from jarvis_context import get_context
from datetime import datetime
import jarvis_brain
from jarvis_signal_fusion import get_fusion_score, get_position_size

ALPACA_KEY    = __import__("jarvis_secrets").ALPACA_PAPER_KEY
ALPACA_SECRET = __import__("jarvis_secrets").ALPACA_PAPER_SECRET
ALPACA_BASE   = "https://paper-api.alpaca.markets"
TELEGRAM_TOKEN = __import__("jarvis_secrets").TG_TOKEN_TRADER
TELEGRAM_CHAT  = "7534553840"
from jarvis_secrets import CLAUDE_API_KEY

CRYPTO_ASSETS = ["BTC", "ETH", "SOL", "AVAX"]
STOCK_ASSETS  = ["NVDA", "TSLA", "COIN", "SPY", "F", "GM", "RIVN"]
BRAIN_FILE    = "/root/jarvis/jarvis_trader_brain.json"

MAX_POSITIONS = 6
TRADE_SIZE    = 500
DAILY_LOSS_LIMIT = 300

import logging
import jarvis_brain as _jb_hb
log = logging.getLogger("jarvis_trader")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

def tg(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": msg}, timeout=5)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def tg_updates(offset=None):
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"timeout": 10, "offset": offset}, timeout=15)
        return r.json().get("result", [])
    except: return []

def headers():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

def get_equity():
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/account", headers=headers(), timeout=5)
        return float(r.json()["equity"])
    except: return 0

def get_crypto_price(symbol):
    try:
        r = requests.get(f"https://api.coinbase.com/v2/prices/{symbol}-USD/spot", timeout=5)
        return float(r.json()["data"]["amount"])
    except: return None

def get_rsi(symbol, is_crypto=True):
    try:
        if is_crypto:
            r = requests.get(f"https://api.coinbase.com/v2/prices/{symbol}-USD/historic?period=hour", timeout=5)
            closes = [float(p["price"]) for p in r.json().get("data", {}).get("prices", [])]
        else:
            r = requests.get(f"{ALPACA_BASE}/v2/stocks/{symbol}/bars?timeframe=15Min&limit=100", headers=headers(), timeout=5)
            closes = [float(b["c"]) for b in r.json().get("bars", [])]
        if len(closes) < 15: return 50
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i-1]
            gains.append(max(d,0)); losses.append(max(-d,0))
        ag = sum(gains[-14:])/14; al = sum(losses[-14:])/14
        return round(100-(100/(1+ag/al)),1) if al else 100
    except: return 50

def buy_asset(symbol, notional, is_crypto=True):
    try:
        sym = symbol+"USD" if is_crypto else symbol
        r = requests.post(f"{ALPACA_BASE}/v2/orders", headers=headers(), json={
            "symbol": sym, "notional": str(notional),
            "side": "buy", "type": "market", "time_in_force": "gtc"
        }, timeout=10)
        return r.json()
    except: return None

def sell_asset(symbol, is_crypto=True):
    try:
        sym = symbol+"USD" if is_crypto else symbol
        requests.delete(f"{ALPACA_BASE}/v2/positions/{sym}", headers=headers(), timeout=10)
    except: pass

def get_alpaca_crypto_positions():
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/positions", headers=headers(), timeout=8)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []

def _persist_own(open_trades, brain):
    """Persist only OWN (non-foreign) open positions so a restart recovers the
    book exactly — the missing piece that caused post-restart double-buys."""
    try:
        brain["open_trades"] = {k: v for k, v in open_trades.items() if not v.get("foreign")}
        save_brain(brain)
    except Exception as e:
        log.error(f"_persist_own: {e}")

def reconcile_open_trades(open_trades, brain):
    """Reconcile the in-memory crypto book against the LIVE shared Alpaca account.

    The account is shared by several crypto bots and Alpaca doesn't tag who owns
    a position, so we do two DISTINCT jobs:
      • OWN positions (recovered from our brain file at startup) are managed
        normally — this is what fixes the post-restart "lost my own book →
        double-buy / unmanaged exit" bug.
      • FOREIGN live positions (on the account but not ours) are recorded with
        foreign=True so we never DOUBLE-BUY a symbol already held — but
        check_exits leaves them alone (we don't sell another bot's trade).
    Own positions missing from the account (closed elsewhere) are dropped after a
    3-min grace so a just-placed, not-yet-settled buy isn't lost.
    """
    try:
        live = {}
        for p in get_alpaca_crypto_positions():
            sym = p.get("symbol", "")
            if not sym.endswith("USD"):
                continue
            base = sym[:-3]
            if base in CRYPTO_ASSETS:
                live[base] = p
        # Record foreign holdings — block-only, not managed.
        for base, p in live.items():
            if base not in open_trades:
                try:    mv = abs(float(p.get("market_value", 0) or 0))
                except: mv = 0
                open_trades[base] = {"asset": base, "type": "crypto", "foreign": True,
                                     "size": mv, "open_time": str(datetime.now())}
                log.info(f"Reconcile: {base} held on shared account by another bot "
                         f"(${mv:,.0f}) — blocking re-buy, not managing its exit")
        # Drop positions (own or foreign) no longer on the account.
        for base in list(open_trades.keys()):
            t = open_trades[base]
            if t.get("type") != "crypto" or base in live:
                continue
            try:
                age = (datetime.now() - datetime.fromisoformat(t["open_time"])).total_seconds()
            except Exception:
                age = 9999
            if age > 180:
                kind = "foreign" if t.get("foreign") else "own"
                log.info(f"Reconcile: {base} ({kind}) gone from Alpaca (age {age:.0f}s) — dropping")
                del open_trades[base]
        _persist_own(open_trades, brain)
    except Exception as e:
        log.error(f"reconcile_open_trades: {e}")

def load_brain():
    try:
        with open(BRAIN_FILE) as f: return json.load(f)
    except:
        return {"trades": [], "total_trades": 0, "wins": 0, "losses": 0,
                "total_pnl": 0, "assets": {}, "daily_pnl": 0, "session_date": ""}

def save_brain(brain):
    with open(BRAIN_FILE, "w") as f: json.dump(brain, f, indent=2)

def claude_approve(symbol, rsi, fusion_score, btc_signal, mood):
    try:
        prompt = (symbol + " trade request. RSI=" + str(rsi) + " Fusion=" + str(fusion_score) +
            "/100 BTC=" + btc_signal + " Mood=" + mood +
            " Should I BUY? Reply: BUY|SCORE|REASON or SKIP|SCORE|REASON")
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": 100,
                  "messages": [{"role": "user", "content": prompt}]}, timeout=10)
        text = r.json()["content"][0]["text"].strip()
        parts = text.split("|")
        return parts[0].strip() == "BUY", int(parts[1]) if len(parts) > 1 else 5
    except: return True, 5

def should_buy(symbol, rsi, regime, vol_mult, vwap_pct, patterns, open_trades):
    shared = jarvis_brain.read_brain()
    if shared.get("risk_level") == "stop": return False, 0, "kill_switch"
    if jarvis_brain.is_blacklisted(symbol): return False, 0, "blacklisted"
    if len(open_trades) >= MAX_POSITIONS: return False, 0, "max_positions"
    if symbol in open_trades: return False, 0, "already_open"
    fusion_score, reasons = get_fusion_score(symbol, rsi, regime, vol_mult, patterns, vwap_pct)
    log.info(symbol + " FUSION: " + str(fusion_score) + "/100")
    if fusion_score < 35: return False, fusion_score, "fusion_too_low"
    btc_sig = shared.get("btc_signal", "neutral")
    mood = shared.get("market_mood", "neutral")
    approved, score = claude_approve(symbol, rsi, fusion_score, btc_sig, mood)
    if not approved: return False, fusion_score, "claude_skip_" + str(score)
    return True, fusion_score, "|".join(reasons[:2])

def scan_crypto(brain, open_trades):
    for symbol in CRYPTO_ASSETS:
        try:
            price = get_crypto_price(symbol)
            if not price: continue
            rsi = get_rsi(symbol, is_crypto=True)
            log.info(symbol + " $" + str(round(price,2)) + " RSI:" + str(rsi))
            buy, score, reason = should_buy(symbol, rsi, "RANGING", 1.0, 0, [], open_trades)
            if buy:
                size, tier, mult = get_position_size(score, TRADE_SIZE)
                # HARD global ceiling — veto the entry if a portfolio limit
                # (drawdown / gross / symbol / sector) is breached.
                try:
                    import portfolio_state as _ps
                    _ok, _why = _ps.can_open(symbol, size)
                    if not _ok:
                        log.info(f"GLOBAL CEILING blocked {symbol}: {_why}")
                        tg(f"⛔ {symbol} buy blocked — {_why}")
                        continue
                except Exception:
                    pass
                order = buy_asset(symbol, size, is_crypto=True)
                if order and "id" in order:
                    open_trades[symbol] = {"asset": symbol, "entry": price, "size": size,
                        "open_time": str(datetime.now()), "type": "crypto", "rsi": rsi}
                    _persist_own(open_trades, brain)
                    jarvis_brain.log_alpha_trade({"asset": symbol, "type": "buy", "price": price, "time": str(datetime.now())})
                    tg("BUY " + symbol + " $" + str(round(price,2)) + " RSI=" + str(rsi) + " Fusion=" + str(score) + " Size=$" + str(size) + " [" + tier + "]")
        except Exception as e:
            log.error(symbol + " scan error: " + str(e))

def check_exits(brain, open_trades):
    for symbol, trade in list(open_trades.items()):
        try:
            if trade.get("foreign"):  # another bot's position — don't manage its exit
                continue
            is_crypto = trade.get("type") == "crypto"
            price = get_crypto_price(symbol) if is_crypto else None
            if not price: continue
            entry = trade["entry"]
            pnl_pct = (price - entry) / entry * 100
            held = (datetime.now() - datetime.fromisoformat(trade["open_time"])).seconds // 60
            exit_reason = None
            if pnl_pct >= 2.0: exit_reason = "profit +" + str(round(pnl_pct,1)) + "%"
            elif pnl_pct <= -1.5: exit_reason = "stop loss " + str(round(pnl_pct,1)) + "%"
            elif held >= 120: exit_reason = "time exit 2hr"
            if exit_reason:
                sell_asset(symbol, is_crypto)
                pnl = (pnl_pct/100) * trade["size"]
                won = pnl > 0
                brain["total_trades"] += 1
                brain["wins"] += 1 if won else 0
                brain["losses"] += 0 if won else 1
                brain["total_pnl"] = round(brain["total_pnl"] + pnl, 2)
                brain["daily_pnl"] = round(brain.get("daily_pnl",0) + pnl, 2)
                if not won:
                    stats = brain.get("assets", {}).get(symbol, {"wins":0,"total":0})
                    if stats["total"] >= 3 and stats["wins"] == 0:
                        jarvis_brain.blacklist_asset(symbol)
                        tg("BLACKLISTED " + symbol + " 3 losses — banned 24hr")
                save_brain(brain)
                del open_trades[symbol]
                _persist_own(open_trades, brain)
                emoji = "✅" if won else "❌"
                tg(emoji + " " + symbol + " CLOSED " + exit_reason + " PnL:$" + str(round(pnl,2)) + " Total:$" + str(brain["total_pnl"]))
        except Exception as e:
            log.error("Exit check error " + symbol + ": " + str(e))

def main():
    log.info("JARVIS TRADER ONLINE")
    brain = load_brain()
    open_trades = brain.get("open_trades", {}) or {}  # recover OWN book across restarts
    tg_offset = None
    paused = False
    last_crypto_scan = 0
    equity = get_equity()
    # Restart safety: recover our own book + flag foreign holdings so we don't
    # double-buy or leave our own positions unmanaged (see reconcile docs).
    reconcile_open_trades(open_trades, brain)
    tg("JARVIS TRADER ONLINE\nEquity: $" + str(round(equity,2)) + "\nOpen (reconciled): " + str(list(open_trades.keys())) + "\nAssets: " + str(CRYPTO_ASSETS + STOCK_ASSETS) + "\nCommands: STATUS PAUSE RESUME STOP BRAIN")

    while True:
        try:
            now = time.time()
            shared = jarvis_brain.read_brain()

            # Check daily loss
            if brain.get("daily_pnl",0) <= -DAILY_LOSS_LIMIT:
                jarvis_brain.set_risk_level("stop")
                tg("DAILY LOSS LIMIT HIT $" + str(DAILY_LOSS_LIMIT) + " — trading halted. Send RESUME to continue.")
                _jb_hb.update_bot_heartbeat("jarvis_trader")

                time.sleep(300)
                continue

            # Telegram commands
            for u in tg_updates(tg_offset):
                tg_offset = u["update_id"] + 1
                msg = u.get("message", {})
                if str(msg.get("chat",{}).get("id","")) != TELEGRAM_CHAT: continue
                text = msg.get("text","").strip().upper()
                if text == "STATUS":
                    wr = round(brain["wins"]/max(brain["total_trades"],1)*100)
                    tg("JARVIS TRADER STATUS\nTrades: " + str(brain["total_trades"]) + " WR: " + str(wr) + "%\nPnL: $" + str(brain["total_pnl"]) + "\nOpen: " + str(list(open_trades.keys())) + "\nRisk: " + shared.get("risk_level","normal"))
                elif text == "PAUSE": paused = True; tg("Paused")
                elif text == "RESUME": paused = False; jarvis_brain.set_risk_level("normal"); tg("Resumed")
                elif text == "STOP": tg("Stopping..."); return
                elif text == "BRAIN": tg(str(jarvis_brain.read_brain()))

            if not paused and shared.get("risk_level") != "stop":
                check_exits(brain, open_trades)
                if now - last_crypto_scan >= 120:
                    last_crypto_scan = now
                    reconcile_open_trades(open_trades, brain)  # resync with Alpaca before scanning
                    scan_crypto(brain, open_trades)

            time.sleep(15)

        except Exception as e:
            log.error("Main loop error: " + str(e))
            time.sleep(30)

if __name__ == "__main__":
    main()
