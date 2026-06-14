#!/usr/bin/env python3
"""
LENNY TRADER BOT
BTC/ETH prediction bot with Kalshi cross-reference
Telegram commands: BTC 77000 76500 77500 | ETH 2140 2100 2200 | STATUS | HELP
"""
import requests, json, time
from datetime import datetime
import jarvis_brain

# ─── CONFIG ───────────────────────────────────────────────
TELEGRAM_TOKEN = __import__("jarvis_secrets").TG_TOKEN_TRADER
TELEGRAM_CHAT  = "7534553840"
from jarvis_secrets import CLAUDE_API_KEY
KALSHI_API_KEY = "67dbc604-41b3-4cf6-bc11-df957dc2ce70"
KALSHI_BASE    = "https://trading-api.kalshi.com/trade-api/v2"
BRAIN_FILE     = "/root/jarvis/lenny_trader_brain.json"

import logging
log = logging.getLogger("lenny_trader")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

# ─── TELEGRAM ─────────────────────────────────────────────
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

# ─── PRICE & INDICATORS ───────────────────────────────────
def get_price(symbol):
    try:
        r = requests.get(f"https://api.binance.us/api/v3/ticker/price?symbol={symbol}USDT", timeout=5)
        return float(r.json()["price"])
    except: return None

def get_momentum(symbol):
    try:
        result = {}
        for label, interval in [("1h","1h"), ("4h","4h"), ("24h","1d")]:
            r = requests.get(f"https://api.binance.us/api/v3/klines?symbol={symbol}USDT&interval={interval}&limit=2", timeout=5)
            c = r.json()
            result[label] = round((float(c[-1][4]) - float(c[0][1])) / float(c[0][1]) * 100, 2)
        return result
    except: return {}

def get_rsi(symbol):
    try:
        r = requests.get(f"https://api.binance.us/api/v3/klines?symbol={symbol}USDT&interval=15m&limit=100", timeout=5)
        closes = [float(c[4]) for c in r.json()]
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i-1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        ag = sum(gains[-14:]) / 14
        al = sum(losses[-14:]) / 14
        return round(100 - (100 / (1 + ag / al)), 1) if al else 100
    except: return 50

# ─── KALSHI ───────────────────────────────────────────────
def get_kalshi_odds(symbol):
    try:
        r = requests.get(
            KALSHI_BASE + "/markets?search=" + symbol + "&limit=5",
            headers={"Authorization": "Bearer " + KALSHI_API_KEY},
            timeout=10
        )
        markets = r.json().get("markets", [])
        return [{"title": m.get("title", "?"), "yes_bid": m.get("yes_bid", 0)} for m in markets[:3]]
    except Exception as e:
        log.error("Kalshi error: " + str(e))
        return []

# ─── BRAIN ────────────────────────────────────────────────
def load_brain():
    try:
        with open(BRAIN_FILE) as f: return json.load(f)
    except: return {"predictions": [], "correct": 0, "total": 0}

def save_brain(brain):
    with open(BRAIN_FILE, "w") as f: json.dump(brain, f, indent=2)

# ─── CLAUDE PREDICTION ────────────────────────────────────
def ask_claude(symbol, price, target, low, high, rsi, momentum, brain):
    try:
        shared = jarvis_brain.read_brain()
        mood = shared.get("market_mood", "neutral")
        btc_sig = shared.get("btc_signal", "neutral")
        accuracy = round(brain["correct"] / brain["total"] * 100) if brain["total"] > 0 else 0
        kalshi = get_kalshi_odds(symbol)
        kalshi_text = "; ".join([m["title"][:40] + " YES=" + str(m["yes_bid"]) + "c" for m in kalshi]) or "No Kalshi data"

        prompt = (
            symbol + " is at $" + str(round(price, 2)) + "\n"
            "Target: at or above $" + str(target) + " by next hour\n"
            "Range: $" + str(low) + " - $" + str(high) + "\n"
            "RSI: " + str(rsi) + " | 1h: " + str(momentum.get("1h", 0)) + "% | 4h: " + str(momentum.get("4h", 0)) + "% | 24h: " + str(momentum.get("24h", 0)) + "%\n"
            "Market mood: " + mood + " | BTC signal: " + btc_sig + "\n"
            "Kalshi: " + kalshi_text + "\n"
            "My historical accuracy: " + str(accuracy) + "% (" + str(brain["total"]) + " predictions)\n\n"
            "Give me:\n"
            "1. Probability price hits target (0-100%)\n"
            "2. Best price prediction by next hour\n"
            "3. Probability price stays in range $" + str(low) + "-$" + str(high) + "\n"
            "4. One sentence reasoning\n"
            "Reply ONLY in format: TARGET_PROB|PREDICTED_PRICE|RANGE_PROB|REASON"
        )

        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": 150, "messages": [{"role": "user", "content": prompt}]},
            timeout=15)

        parts = r.json()["content"][0]["text"].strip().split("|")
        return {
            "target_prob": parts[0].strip() if len(parts) > 0 else "?",
            "predicted_price": parts[1].strip() if len(parts) > 1 else "?",
            "range_prob": parts[2].strip() if len(parts) > 2 else "?",
            "reason": parts[3].strip() if len(parts) > 3 else "?"
        }
    except Exception as e:
        log.error("Claude error: " + str(e))
        return {"target_prob": "?", "predicted_price": "?", "range_prob": "?", "reason": "Claude unavailable"}

# ─── SEND PREDICTION ──────────────────────────────────────
def send_prediction(symbol, target, low, high, brain):
    price = get_price(symbol)
    if not price:
        tg("Could not fetch " + symbol + " price")
        return
    rsi = get_rsi(symbol)
    momentum = get_momentum(symbol)
    pred = ask_claude(symbol, price, target, low, high, rsi, momentum, brain)
    next_hour = str((datetime.now().hour + 1) % 24) + ":00"
    accuracy = round(brain["correct"] / brain["total"] * 100) if brain["total"] > 0 else 0

    msg = (
        "LENNY TRADER PREDICTION\n"
        + "━" * 22 + "\n"
        + symbol + " @ $" + str(round(price, 2)) + "\n"
        + "Target: $" + str(target) + " by " + next_hour + "\n"
        + "Range: $" + str(low) + " - $" + str(high) + "\n"
        + "━" * 22 + "\n"
        + "Target prob:   " + str(pred["target_prob"]) + "\n"
        + "Predicted px:  $" + str(pred["predicted_price"]) + "\n"
        + "Range prob:    " + str(pred["range_prob"]) + "\n"
        + "RSI: " + str(rsi) + " | 1h: " + str(momentum.get("1h", 0)) + "% | 4h: " + str(momentum.get("4h", 0)) + "%\n"
        + "━" * 22 + "\n"
        + str(pred["reason"]) + "\n"
        + "Accuracy: " + str(accuracy) + "% (" + str(brain["total"]) + " predictions)"
    )
    tg(msg)
    log.info("Prediction sent for " + symbol + " target=" + str(target))

    brain["predictions"].append({
        "symbol": symbol, "price": price, "target": target,
        "low": low, "high": high, "timestamp": str(datetime.now()),
        "deadline": next_hour, "status": "pending", "pred": pred
    })
    brain["predictions"] = brain["predictions"][-200:]
    save_brain(brain)

# ─── CHECK OUTCOMES ───────────────────────────────────────
def check_outcomes(brain):
    current_hour = str(datetime.now().hour) + ":00"
    changed = False
    for pred in brain["predictions"]:
        if pred.get("status") == "pending" and pred.get("deadline", "") == current_hour:
            price = get_price(pred["symbol"])
            if not price: continue
            hit = price >= pred["target"]
            pred["status"] = "hit" if hit else "missed"
            pred["actual"] = price
            brain["total"] += 1
            if hit: brain["correct"] += 1
            accuracy = round(brain["correct"] / brain["total"] * 100)
            tg(
                ("✅ PREDICTION HIT!" if hit else "❌ PREDICTION MISSED") + "\n"
                + pred["symbol"] + " target $" + str(pred["target"]) + " — actual $" + str(round(price, 2)) + "\n"
                + "Accuracy: " + str(accuracy) + "% (" + str(brain["correct"]) + "/" + str(brain["total"]) + ")"
            )
            changed = True
    if changed: save_brain(brain)

# ─── MAIN ─────────────────────────────────────────────────
def main():
    log.info("LENNY TRADER BOT ONLINE")
    tg(
        "LENNY TRADER BOT ONLINE\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Commands:\n"
        "BTC <target> <low> <high>\n"
        "  e.g. BTC 77000 76500 77500\n"
        "ETH <target> <low> <high>\n"
        "  e.g. ETH 2140 2100 2200\n"
        "STATUS — show accuracy\n"
        "HELP"
    )
    brain = load_brain()
    tg_offset = None
    last_check = 0
    last_idle_log = 0

    while True:
        try:
            if time.time() - last_check >= 300:
                last_check = time.time()
                check_outcomes(brain)

            for u in tg_updates(tg_offset):
                tg_offset = u["update_id"] + 1
                msg = u.get("message", {})
                if str(msg.get("chat", {}).get("id", "")) != TELEGRAM_CHAT: continue
                text = msg.get("text", "").strip().upper()
                parts = text.split()

                if len(parts) == 4 and parts[0] in ["BTC", "ETH"]:
                    try:
                        sym = parts[0]
                        target = float(parts[1])
                        low = float(parts[2])
                        high = float(parts[3])
                        tg("Analyzing " + sym + " target=$" + str(target) + " range=$" + str(low) + "-$" + str(high) + "...")
                        send_prediction(sym, target, low, high, brain)
                    except:
                        tg("Format: BTC 77000 76500 77500")
                elif text == "STATUS":
                    acc = round(brain["correct"] / brain["total"] * 100) if brain["total"] > 0 else 0
                    tg("Total predictions: " + str(brain["total"]) + "\nCorrect: " + str(brain["correct"]) + "\nAccuracy: " + str(acc) + "%")
                elif text == "HELP":
                    tg("BTC <target> <low> <high>\nETH <target> <low> <high>\nSTATUS")

            if time.time() - last_idle_log >= 3600:
                log.info("Idle — waiting for Telegram commands")
                last_idle_log = time.time()

            jarvis_brain.update_bot_heartbeat("lenny_trader_bot")
            time.sleep(10)

        except Exception as e:
            log.error("Loop error: " + str(e))
            time.sleep(30)

if __name__ == "__main__":
    main()
