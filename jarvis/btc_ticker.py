import time, json, requests, logging
from datetime import datetime

LOG = "/root/jarvis/jarvis_btc.log"
MEMORY = "/root/jarvis/btc_memory.json"
logging.basicConfig(filename=LOG, level=logging.INFO, format="%(asctime)s %(message)s")

def fetch_btc():
    # CoinGecko free tier is heavily rate-limited; fall back to Coinbase on any failure
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
            timeout=10
        )
        data = r.json()
        if "bitcoin" in data:
            return data["bitcoin"]["usd"]
    except Exception:
        pass
    r = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=10)
    return float(r.json()["data"]["amount"])

def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i-1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)

def write_tick(price):
    try:
        with open(MEMORY, 'r') as f:
            data = json.load(f)
    except:
        data = {"prices": []}
    recent = [p["price"] for p in data["prices"][-20:]] + [price]
    rsi = calc_rsi(recent)
    data["prices"].append({"ts": datetime.now().isoformat(), "price": price, "rsi": rsi})
    data["prices"] = data["prices"][-100:]
    with open(MEMORY, 'w') as f:
        json.dump(data, f)
    logging.info(f"BTC tick: ${price:,.0f} RSI:{rsi}")

while True:
    try:
        price = fetch_btc()
        write_tick(price)
    except Exception as e:
        logging.error(f"Fetch error: {e}")
    time.sleep(3600)
