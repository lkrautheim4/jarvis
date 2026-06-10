#!/usr/bin/env python3
"""
JARVIS FUTURES BOT
Crypto futures trading via Alpaca
Reads jarvis_brain for signals, learns from every trade
"""
import json, time, requests, os
from datetime import datetime
import jarvis_brain

ALPACA_KEY    = "PKTHANGUNVFDSLLR3VXPETXRQF"
ALPACA_SECRET = "GRTDDfkCGWbZMoNSWms6uJSGvw72rHaAk1N1fvLi8EAP"
ALPACA_BASE   = "https://paper-api.alpaca.markets"
TELEGRAM_TOKEN = __import__("jarvis_secrets").TG_TOKEN_TRADER
TELEGRAM_CHAT  = "7534553840"

from jarvis_secrets import CLAUDE_API_KEY
BRAIN_FILE = "jarvis_futures_brain.json"
ASSETS     = ["BTC/USD", "ETH/USD"]
TRADE_SIZE = 1000  # aggressive $1000 per trade
LEVERAGE   = 10    # 10x

import logging
log = logging.getLogger("jarvis_futures")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

def tg(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": f"FUTURES: {msg}"}, timeout=5)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def headers():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

def get_price(symbol):
    try:
        sym = symbol.replace("/", "")
        r = requests.get(f"https://api.binance.us/api/v3/ticker/price?symbol={sym}T", timeout=5)
        return float(r.json()["price"])
    except: return None

def get_equity():
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/account", headers=headers(), timeout=5)
        return float(r.json()["equity"])
    except: return 0

def buy(symbol, notional):
    try:
        sym = symbol.replace("/", "")
        r = requests.post(f"{ALPACA_BASE}/v2/orders", headers=headers(), json={
            "symbol": sym, "notional": str(notional),
            "side": "buy", "type": "market", "time_in_force": "gtc"
        }, timeout=10)
        return r.json()
    except Exception as e:
        log.error(f"Buy error: {e}")
        return None

def sell(symbol):
    try:
        sym = symbol.replace("/", "")
        r = requests.delete(f"{ALPACA_BASE}/v2/positions/{sym}", headers=headers(), timeout=10)
        return r.json()
    except Exception as e:
        log.error(f"Sell error: {e}")
        return None

def load_brain():
    try:
        if os.path.exists(BRAIN_FILE):
            with open(BRAIN_FILE) as f: return json.load(f)
    except: pass
    return {"trades": [], "total_trades": 0, "wins": 0, "losses": 0, "total_pnl": 0, "learnings": []}

def save_brain(brain):
    with open(BRAIN_FILE, "w") as f: json.dump(brain, f, indent=2)

def get_rsi(symbol, period=14):
    try:
        sym = symbol.replace("/", "") + "T"
        r = requests.get(f"https://api.binance.us/api/v3/klines?symbol={sym}&interval=15m&limit=100", timeout=5)
        closes = [float(c[4]) for c in r.json()]
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0: return 100
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 1)
    except: return 50

def learn_from_trade(brain, trade):
    """Extract lessons from completed trade"""
    learning = {
        "asset": trade["asset"],
        "signal": trade.get("signal", "unknown"),
        "btc_signal": trade.get("btc_signal", "unknown"),
        "market_mood": trade.get("market_mood", "unknown"),
        "rsi_at_entry": trade.get("rsi", 0),
        "pnl": trade.get("pnl", 0),
        "won": trade.get("won", False),
        "hold_mins": trade.get("hold_mins", 0),
        "timestamp": str(datetime.now())
    }
    brain["learnings"].append(learning)
    
    # Summarize what works
    wins = [l for l in brain["learnings"] if l["won"]]
    if wins:
        best_mood = max(set(l["market_mood"] for l in wins), key=lambda m: sum(1 for l in wins if l["market_mood"] == m))
        best_signal = max(set(l["btc_signal"] for l in wins), key=lambda s: sum(1 for l in wins if l["btc_signal"] == s))
        jarvis_brain.write_brain({"futures_best_mood": best_mood, "futures_best_signal": best_signal})
        log.info(f"LEARNED: Best mood={best_mood} Best signal={best_signal}")



def claude_confidence_futures(symbol, rsi, btc_signal, market_mood):
    try:
        prompt = 'Trading futures on ' + symbol + '. RSI=' + str(rsi) + ', BTC signal=' + btc_signal + ', market mood=' + market_mood + ', leverage=10x. Should I enter a LONG? Reply: BUY|SCORE|REASON or SKIP|SCORE|REASON'
        r = requests.post('https://api.anthropic.com/v1/messages',
            headers={'x-api-key': CLAUDE_API_KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
            json={'model': 'claude-sonnet-4-6', 'max_tokens': 100, 'messages': [{'role': 'user', 'content': prompt}]},
            timeout=10)
        text = r.json()['content'][0]['text'].strip()
        parts = text.split('|')
        decision = parts[0].strip()
        score = int(parts[1].strip()) if len(parts) > 1 else 5
        reason = parts[2].strip() if len(parts) > 2 else ''
        log.info('Claude futures: ' + decision + ' score=' + str(score) + ' ' + reason)
        return decision == 'BUY' and score >= 7, score, reason
    except Exception as e:
        log.error('Claude futures error: ' + str(e))
        return True, 5, 'claude_unavailable'

def should_buy(symbol, brain, shared):
    """Decide whether to buy based on shared brain signals"""
    rsi = get_rsi(symbol)
    btc_signal = shared.get("btc_signal", "neutral")
    market_mood = shared.get("market_mood", "neutral")
    risk_level = shared.get("risk_level", "normal")
    
    if risk_level == "stop":
        return False, "risk_stop", rsi
    
    # Check learnings — what conditions have won before
    learnings = brain.get("learnings", [])
    if len(learnings) > 10:
        winning_moods = [l["market_mood"] for l in learnings if l["won"]]
        if market_mood not in winning_moods and market_mood != "neutral":
            return False, "bad_mood_history", rsi
    
    if rsi < 35 and btc_signal in ["bullish", "active"]:
        approved, score, reason = claude_confidence_futures(symbol, rsi, btc_signal, market_mood)
        if not approved:
            return False, f"claude_rejected_score={score}", rsi
        return True, f"oversold+{btc_signal}+claude{score}", rsi
    if rsi < 40 and market_mood == "bullish":
        approved, score, reason = claude_confidence_futures(symbol, rsi, btc_signal, market_mood)
        if not approved:
            return False, f"claude_rejected_score={score}", rsi
        return True, f"bullish_mood+rsi{rsi}+claude{score}", rsi

    return False, "no_signal", rsi

def main():
    log.info("JARVIS FUTURES BOT ONLINE")
    tg("JARVIS FUTURES BOT ONLINE\nAssets: BTC/USD, ETH/USD\nSize: $1000 | Learning mode ON")
    
    brain = load_brain()
    open_trades = {}
    try:
        r = requests.get(ALPACA_BASE + "/v2/positions", headers=headers())
        for p in r.json():
            sym = p.get("symbol","")
            if sym in ["BTCUSD","ETHUSD"]:
                open_trades[sym.replace("USD","/USD")] = {"asset": sym, "entry": float(p.get("avg_entry_price",0)), "size": TRADE_SIZE, "open_time": str(datetime.now())}
        log.info("Loaded " + str(len(open_trades)) + " existing positions")
    except Exception as e:
        log.error("Position load error: " + str(e))
    
    while True:
        try:
            shared = jarvis_brain.read_brain()
            
            for symbol in ASSETS:
                price = get_price(symbol)
                if not price: continue
                
                # Check open trade
                if symbol in open_trades:
                    trade = open_trades[symbol]
                    pnl_pct = ((price - trade["entry"]) / trade["entry"]) * 100
                    hold_mins = (datetime.now() - datetime.fromisoformat(trade["open_time"])).seconds // 60
                    
                    # Exit conditions
                    should_exit = False
                    reason = ""
                    if pnl_pct >= 2.0: should_exit, reason = True, f"profit +{pnl_pct:.1f}%"
                    elif pnl_pct <= -1.5: should_exit, reason = True, f"stop loss {pnl_pct:.1f}%"
                    elif hold_mins >= 120: should_exit, reason = True, "time exit 2hr"
                    
                    if should_exit:
                        sell(symbol)
                        pnl_dollar = (pnl_pct / 100) * trade["size"] * LEVERAGE
                        won = pnl_pct > 0
                        trade.update({"pnl": round(pnl_dollar, 2), "won": won, "hold_mins": hold_mins, "close_reason": reason, "close_price": price})
                        brain["trades"].append(trade)
                        brain["total_trades"] += 1
                        brain["wins"] += 1 if won else 0
                        brain["losses"] += 0 if won else 1
                        brain["total_pnl"] = round(brain["total_pnl"] + pnl_dollar, 2)
                        learn_from_trade(brain, trade)
                        save_brain(brain)
                        del open_trades[symbol]
                        emoji = "✅" if won else "❌"
                        tg(f"{emoji} {symbol} CLOSED\nReason: {reason}\nPnL: ${pnl_dollar:+.2f}\nTotal: ${brain['total_pnl']:+.2f}")
                        log.info(f"CLOSED {symbol} {reason} PnL=${pnl_dollar:+.2f}")
                else:
                    # Check if should buy
                    if len(open_trades) >= 2: continue
                    buy_signal, signal_reason, rsi = should_buy(symbol, brain, shared)
                    log.info(symbol + " RSI=" + str(rsi) + " signal=" + str(buy_signal) + " reason=" + str(signal_reason))
                    if buy_signal:
                        import crypto_risk
                        ok, cx, why = crypto_risk.can_add_crypto(TRADE_SIZE)
                        if not ok:
                            log.info(f"CRYPTO CAP: skip {symbol} buy — {why}")
                            continue
                        order = buy(symbol, TRADE_SIZE)
                        if order and "id" in order:
                            open_trades[symbol] = {
                                "asset": symbol, "entry": price, "size": TRADE_SIZE,
                                "open_time": str(datetime.now()), "signal": signal_reason,
                                "rsi": rsi, "btc_signal": shared.get("btc_signal"),
                                "market_mood": shared.get("market_mood")
                            }
                            jarvis_brain.log_alpha_trade({"asset": symbol, "type": "futures_buy", "price": price, "time": str(datetime.now())})
                            tg(f"🚀 FUTURES BUY {symbol}\nPrice: ${price:,.2f}\nSignal: {signal_reason}\nRSI: {rsi}\nSize: ${TRADE_SIZE} x {LEVERAGE}x")
                            log.info(f"BOUGHT {symbol} @ ${price} signal={signal_reason}")
            
            log.info("Futures loop tick — scanning " + str(ASSETS))
            time.sleep(60)
            
        except Exception as e:
            log.error("Loop error: " + str(e))
            import traceback; log.error(traceback.format_exc())
            time.sleep(30)

if __name__ == "__main__":
    main()
