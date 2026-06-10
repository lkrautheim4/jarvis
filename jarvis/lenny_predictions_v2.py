"""
lenny_predictions.py — Jarvis BTC Prediction Bot v2
EV-based bet sizing, Kelly position sizing, confidence floor enforcement,
SQLite memory via jarvis_memory_db, dynamic Kalshi targets.
"""
import requests, json, time, logging, os, sys
from datetime import datetime
import jarvis_brain
import btc_memory
try:
    import kalshi_auth
    KALSHI_ENABLED = True
except:
    KALSHI_ENABLED = False

try:
    import jarvis_memory_db as memdb
    memdb.init_db()
    DB_ENABLED = True
except Exception as e:
    DB_ENABLED = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("lenny_predictions")

from jarvis_secrets import CLAUDE_API_KEY
TG_TOKEN       = "8713474292:AAEtNCL6xuqIbS3Adf5KsFhH5xZ3XQ7Rz0o"
TG_CHAT_ID     = "7534553840"
SYMBOL         = "BTC"
CHECK_INTERVAL = 3600

# Oracle rules — confidence floors
YES_FLOOR      = 0.65   # must be ≥65% to bet YES
NO_FLOOR       = 0.80   # must be ≥80% to bet NO (harder edge)
RANGING_SKIP   = True   # in ranging markets, lean YES or SKIP only
MAX_LOSS_STREAK = 3     # stop betting after 3 consecutive losses

# EV thresholds
MIN_EV         = 0.05   # minimum 5% edge to place bet

def tg(msg: str):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg},
            timeout=10
        )
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def get_price(symbol: str):
    try:
        coin = {"BTC": "bitcoin", "ETH": "ethereum"}.get(symbol)
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": coin, "vs_currencies": "usd"},
            timeout=10
        )
        return r.json()[coin]["usd"]
    except Exception as e:
        log.error(f"Price error: {e}")
        return None

def get_rsi(symbol: str, period: int = 14) -> float:
    try:
        coin = {"BTC": "bitcoin", "ETH": "ethereum"}.get(symbol)
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart",
            params={"vs_currency": "usd", "days": "2", "interval": "hourly"},
            timeout=15
        )
        data = r.json()
        prices = [p[1] for p in data.get("prices", [])][-period-1:]
        if not prices: return 50.0
        gains, losses = [], []
        for i in range(1, len(prices)):
            d = prices[i] - prices[i-1]
            (gains if d > 0 else losses).append(abs(d))
        if not gains: return 50.0
        ag = sum(gains) / len(gains)
        al = sum(losses) / len(losses) if losses else 0.001
        return round(100 - (100 / (1 + ag/al)), 1)
    except Exception as e:
        log.error(f"RSI error: {e}")
        return 50.0

def get_momentum(symbol: str) -> dict:
    try:
        coin = {"BTC": "bitcoin", "ETH": "ethereum"}.get(symbol)
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coin}",
            params={"localization": "false", "tickers": "false",
                    "community_data": "false", "developer_data": "false"},
            timeout=10
        )
        data = r.json().get("market_data")
        if not data: return {"1h":0.0,"24h":0.0,"7d":0.0}
        return {
            "1h":  round(data["price_change_percentage_1h_in_currency"]["usd"], 2),
            "24h": round(data["price_change_percentage_24h"], 2),
            "7d":  round(data["price_change_percentage_7d"], 2),
        }
    except Exception as e:
        log.error(f"Momentum error: {e}")
        return {"1h": 0.0, "24h": 0.0, "7d": 0.0}

def get_kalshi_odds(symbol: str, price: float = 0) -> list:
    if not KALSHI_ENABLED or symbol != "BTC":
        return []
    try:
        return kalshi_auth.get_btc_markets(price or 70000)
    except Exception as e:
        log.error(f"Kalshi error: {e}")
        return []

def compute_ev(prob: float, yes_price: float, bet: str) -> float:
    """
    EV = (prob * payout) - (1-prob) * cost
    YES bet: win (1-yes_price) per dollar risked, lose yes_price
    NO bet: win (1-no_price) = win yes_price per dollar risked
    """
    try:
        if bet == "YES":
            no_price = 1 - yes_price
            ev = (prob * no_price) - ((1 - prob) * yes_price)
        elif bet == "NO":
            no_price = 1 - yes_price
            ev = ((1 - prob) * yes_price) - (prob * no_price)
        else:
            ev = 0
        return round(ev, 4)
    except:
        return 0.0

def kelly_size(prob: float, yes_price: float, bet: str, bankroll: float = 100) -> float:
    """
    Kelly criterion: f = (bp - q) / b
    b = odds (payout per $ risked), p = win prob, q = 1-p
    Returns suggested $ bet size (capped at 25% bankroll)
    """
    try:
        if bet == "YES":
            b = (1 - yes_price) / yes_price  # odds
            p = prob
        elif bet == "NO":
            b = yes_price / (1 - yes_price)
            p = 1 - prob
        else:
            return 0
        q = 1 - p
        f = (b * p - q) / b
        f = max(0, min(0.25, f))  # cap at 25% Kelly
        return round(f * bankroll, 2)
    except:
        return 0.0

def check_loss_streak() -> int:
    """Check recent consecutive losses from DB"""
    if not DB_ENABLED:
        return 0
    try:
        import sqlite3
        conn = sqlite3.connect(memdb.DB_PATH)
        rows = conn.execute("""
            SELECT outcome FROM kalshi_bets
            WHERE result IS NOT NULL AND bet != 'SKIP'
            ORDER BY ts DESC LIMIT 10
        """).fetchall()
        conn.close()
        streak = 0
        for row in rows:
            if row[0] == "LOSS":
                streak += 1
            else:
                break
        return streak
    except:
        return 0

def get_kalshi_edge(jarvis_prob_str: str, price: float) -> dict:
    if not KALSHI_ENABLED:
        return {}
    try:
        prob_num = float(str(jarvis_prob_str).replace("%","").replace("?","50")) / 100
        return kalshi_auth.find_edge(prob_num, price)
    except Exception as e:
        log.error(f"Edge error: {e}")
        return {}

def ask_claude(symbol, price, target, low, high, rsi, momentum) -> dict:
    try:
        shared     = jarvis_brain.read_brain()
        mood       = shared.get("market_mood", "neutral")
        btc_sig    = shared.get("btc_signal", "neutral")
        regime     = shared.get("regime", "UNKNOWN")
        kalshi     = get_kalshi_odds(symbol, price)
        kalshi_txt = "; ".join([f"${m['strike']:,.0f} YES={m['yes_bid']:.2f} NO={m['no_bid']:.2f}" for m in kalshi]) or "No Kalshi data"
        sr         = btc_memory.get_support_resistance()
        sr_txt     = (f"Support:${sr.get('support',0):,.0f} Resistance:${sr.get('resistance',0):,.0f} 7dAvg:${sr.get('avg',0):,.0f}") if sr else "Building S/R..."
        memory_ctx = btc_memory.build_context()
        next_hour  = str(((datetime.utcnow().hour - 4) % 24) + 1) + ":00 EDT"

        # Confidence floor rules in prompt
        yes_rule = f"BET YES only if ≥{int(YES_FLOOR*100)}% confident"
        no_rule  = f"BET NO only if ≥{int(NO_FLOOR*100)}% confident (harder edge required)"
        ranging_rule = "In RANGING market: lean YES or SKIP — NO requires very strong evidence" if regime == "RANGING" else ""

        prompt = f"""You are Jarvis, a sharp crypto trading AI with perfect memory. Brutal, precise, never vague.
=== CURRENT MARKET ===
{symbol} @ ${price:,.2f}
Kalshi target: above ${target:,.0f} by {next_hour}
Prediction range: ${low:,.0f} - ${high:,.0f}
RSI:{rsi} | 1h:{momentum.get('1h',0):+.2f}% | 24h:{momentum.get('24h',0):+.2f}% | 7d:{momentum.get('7d',0):+.2f}%
Mood:{mood} | BTC signal:{btc_sig} | Regime:{regime}
=== SUPPORT / RESISTANCE ===
{sr_txt}
=== KALSHI ===
{kalshi_txt}
=== YOUR MEMORY ===
{memory_ctx}
=== ORACLE RULES ===
{yes_rule}
{no_rule}
{ranging_rule}
SKIP if uncertain or confidence between {int(NO_FLOOR*100)}% NO and {int(YES_FLOOR*100)}% YES.
=== TASK ===
Note Kalshi mispricing if you see edge. One sentence reason. Brutal and specific. No hedging.
Reply ONLY (pipe-separated):
TARGET_PROB|PREDICTED_PRICE|RANGE_PROB|BET|EDGE_REASON
Example: 71%|75842|68%|YES|RSI bouncing from oversold + holding 7d avg — Kalshi YES 58c underpriced
"""
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": 200,
                  "system": "Reply ONLY in pipe-separated format: TARGET_PROB|PREDICTED_PRICE|RANGE_PROB|BET|EDGE_REASON — no prose, no preamble, no explanation. Example: 71%|75842|68%|YES|RSI bouncing from oversold",
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=20
        )
        resp = r.json()
        if "error" in resp:
            log.error(f"Claude API error: {resp['error']}")
            return _fallback()
        raw   = resp["content"][0]["text"].strip()
        lines = [l for l in raw.split("\n") if "|" in l]
        text  = lines[-1].strip() if lines else raw
        parts = text.split("|")
        if len(parts) < 4:
            log.error(f"Claude bad format: {text}")
            return _fallback()
        return {
            "target_prob":     parts[0].strip(),
            "predicted_price": parts[1].strip().replace("?","0").replace("$","").replace(",",""),
            "range_prob":      parts[2].strip(),
            "bet":             parts[3].strip().upper(),
            "reason":          parts[4].strip() if len(parts) > 4 else "No reason",
        }
    except Exception as e:
        log.error(f"Claude error: {e}")
        return _fallback()

def _fallback():
    return {"target_prob":"?","predicted_price":"0","range_prob":"50%","bet":"SKIP","reason":"Claude unavailable"}

def apply_oracle_rules(pred: dict, kalshi_markets: list) -> dict:
    """
    Apply Oracle confidence floor rules.
    Upgrade bet decision with EV check and loss streak guard.
    """
    prob_str = pred["target_prob"].replace("%","").replace("?","50")
    try:
        prob = float(prob_str) / 100
    except:
        prob = 0.5

    bet = pred["bet"]
    ev  = 0.0
    kelly = 0.0

    # Find matching Kalshi market for EV calc
    yes_price = 0.5
    if kalshi_markets:
        yes_price = kalshi_markets[0].get("yes_bid", 0.5)

    # Enforce confidence floors
    if bet == "YES" and prob < YES_FLOOR:
        log.info(f"Oracle: YES blocked — prob {prob:.0%} < floor {YES_FLOOR:.0%}, downgrading to SKIP")
        bet = "SKIP"
    elif bet == "NO" and prob > (1 - NO_FLOOR):
        log.info(f"Oracle: NO blocked — implied NO prob {1-prob:.0%} < floor {NO_FLOOR:.0%}, downgrading to SKIP")
        bet = "SKIP"

    # EV check
    if bet in ["YES", "NO"]:
        ev = compute_ev(prob, yes_price, bet)
        if ev < MIN_EV:
            log.info(f"Oracle: {bet} blocked — EV {ev:.3f} < min {MIN_EV}")
            bet = "SKIP"
        else:
            kelly = kelly_size(prob, yes_price, bet)

    # Loss streak guard
    streak = check_loss_streak()
    if streak >= MAX_LOSS_STREAK and bet != "SKIP":
        log.info(f"Oracle: {bet} blocked — loss streak {streak} >= {MAX_LOSS_STREAK}")
        bet = "SKIP"
        pred["reason"] += f" [HALTED: {streak}-loss streak]"

    pred["bet"]    = bet
    pred["ev"]     = ev
    pred["kelly"]  = kelly
    pred["prob"]   = prob
    return pred

def run_prediction(symbol: str):
    price = get_price(symbol)
    if not price:
        tg(f"Could not fetch {symbol} price"); return

    rsi      = get_rsi(symbol)
    momentum = get_momentum(symbol)
    btc_memory.log_price(price, rsi, momentum)
    graded = btc_memory.grade_last_prediction(price)
    if graded:
        log.info("Graded last prediction")

    # Dynamic target from Kalshi
    target   = price
    low      = round(price * 0.998 / 100) * 100
    high     = round(price * 1.002 / 100) * 100
    kalshi_markets = []

    try:
        kalshi_markets = kalshi_auth.get_btc_markets(price)
        if kalshi_markets:
            best = next((m for m in kalshi_markets if 0.15 <= m["yes_bid"] <= 0.85), kalshi_markets[0])
            target = best["strike"]
            low  = round((price - 200) / 100) * 100
            high = round((price + 200) / 100) * 100
    except:
        pass

    pred = ask_claude(symbol, price, target, low, high, rsi, momentum)
    pred = apply_oracle_rules(pred, kalshi_markets)

    ev     = pred.get("ev", 0)
    kelly  = pred.get("kelly", 0)
    prob   = pred.get("prob", 0.5)

    # Log to SQLite
    if DB_ENABLED:
        try:
            memdb.log_prediction(
                symbol, price, target, low, high,
                pred["target_prob"], pred["predicted_price"],
                pred["range_prob"], pred["bet"], pred["reason"],
                ev=ev, kelly_size=kelly
            )
            if pred["bet"] in ["YES", "NO"] and kalshi_markets:
                memdb.log_kalshi_bet(
                    symbol, target, pred["bet"], pred["target_prob"],
                    kalshi_markets[0].get("yes_bid", 0),
                    kalshi_markets[0].get("no_bid", 0),
                    pred["reason"]
                )
        except Exception as e:
            log.error(f"DB log error: {e}")

    # Legacy btc_memory log
    btc_memory.log_prediction(
        symbol, price, target, low, high,
        pred["target_prob"], pred["predicted_price"],
        pred["range_prob"],  pred["bet"], pred["reason"]
    )

    next_hour = str(((datetime.utcnow().hour - 4) % 24) + 1) + ":00 EDT"
    diff      = target - price
    diff_str  = f"UP ${diff:,.2f}" if diff > 0 else f"DOWN ${abs(diff):,.2f}"
    bet       = pred["bet"]
    bet_line  = {"YES": "BET YES", "NO": "BET NO", "SKIP": "SKIP"}.get(bet, "SKIP")
    stats_line = btc_memory.get_stats_line()
    sr         = btc_memory.get_support_resistance()
    sr_line    = f"S:${sr.get('support',0):,.0f} R:${sr.get('resistance',0):,.0f}" if sr else "Building levels..."

    # EV / Kelly line — only show when betting
    ev_line = ""
    if bet in ["YES", "NO"]:
        ev_line = f"EV: {ev:+.1%} | Kelly: ${kelly:.0f}"

    msg = f"""JARVIS PREDICTIONS
{'='*24}
{symbol} @ ${price:,.2f}
Target: ${target:,.0f} ({diff_str})
Range:  ${low:,.0f} - ${high:,.0f}
Deadline: {next_hour}
{'='*24}
*** {bet_line} ***
Target prob:  {pred['target_prob']}
Best guess:   ${pred['predicted_price']}
Range prob:   {pred['range_prob']}
{'='*24}
RSI:{rsi} | 1h:{momentum.get('1h',0):+.1f}% | 24h:{momentum.get('24h',0):+.1f}%
{sr_line}
{'='*24}
{pred['reason']}
{ev_line}
{'='*24}
{stats_line}"""

    tg(msg)
    log.info(f"Prediction sent {symbol} target={target} bet={bet} prob={pred['target_prob']} ev={ev:+.3f}")

if __name__ == "__main__":
    log.info("JARVIS PREDICTIONS BOT ONLINE v2 — Oracle rules active")
    tg("JARVIS Predictions v2 online.\nOracle rules: YES≥65% | NO≥80% | EV≥5% | Kelly sizing active")
    while True:
        try:
            run_prediction(SYMBOL)
        except Exception as e:
            log.error(f"Cycle error: {e}")
            tg(f"Prediction cycle error: {e}")
        log.info(f"Sleeping {CHECK_INTERVAL}s")
        time.sleep(CHECK_INTERVAL)
