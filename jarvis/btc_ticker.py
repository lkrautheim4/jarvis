import time, json, requests, logging, sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

LOG = "/root/jarvis/btc_ticker.log"
MEMORY = "/root/jarvis/btc_memory.json"
DB_PATH = "/root/jarvis/jarvis_memory.db"

logging.basicConfig(filename=LOG, level=logging.INFO, format="%(asctime)s %(message)s")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def fetch_btc():
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
            timeout=10
        )
        data = r.json()
        if "bitcoin" in data:
            return data["bitcoin"]["usd"]
    except:
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
    rs = avg_gain / (avg_loss + 0.0001)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi

def update_btc_brain(conn, price, rsi):
    try:
        conn.execute("INSERT OR REPLACE INTO brain (key, value) VALUES (?, ?)", ("btc_price", price))
        conn.execute("INSERT OR REPLACE INTO brain (key, value) VALUES (?, ?)", ("btc_rsi", rsi))
        conn.commit()
        logging.info(f"Updated BTC in brain: ${price:,.0f}, RSI {rsi:.1f}")
    except Exception as e:
        logging.error(f"Error updating BTC brain: {e}")

def log_btc_tick(conn, price, rsi):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    date = ts[:10]
    hour = int(ts[11:13])
    try:
        conn.execute("""
            INSERT INTO btc_ticks (ts, price, rsi, momentum_1h, momentum_24h, momentum_7d)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (ts, price, rsi, 0.0, 0.0, 0.0))
        conn.commit()
        logging.info(f"Logged BTC tick to db: ${price:,.0f}, RSI {rsi:.1f}")
    except Exception as e:
        logging.error(f"Error logging BTC tick: {e}")

def update_btc_momentum(conn):
    conn.execute("""
        WITH recent AS (
            SELECT price FROM btc_ticks ORDER BY ts DESC LIMIT 1
        )
        UPDATE btc_ticks
        SET momentum_1h = 100.0 * (
            price - (SELECT avg(price) FROM btc_ticks WHERE ts > datetime('now','-1 hours'))
        ) / (SELECT price FROM recent)
    """)
    conn.execute("""
        WITH recent AS (
            SELECT price FROM btc_ticks ORDER BY ts DESC LIMIT 1
        )
        UPDATE btc_ticks
        SET momentum_24h = 100.0 * (
            price - (SELECT avg(price) FROM btc_ticks WHERE ts > datetime('now','-24 hours'))
        ) / (SELECT price FROM recent)
    """)
    conn.execute("""
        WITH recent AS (
            SELECT price FROM btc_ticks ORDER BY ts DESC LIMIT 1
        )
        UPDATE btc_ticks
        SET momentum_7d = 100.0 * (
            price - (SELECT avg(price) FROM btc_ticks WHERE ts > datetime('now','-7 days'))
        ) / (SELECT price FROM recent)
    """)

def main():
    while True:
        logging.info("BTC ticker awake")
        conn = get_db()
        try:
            price = fetch_btc()
            recent_prices = [
                row['price'] for row in conn.execute(
                    "SELECT price FROM btc_ticks ORDER BY ts DESC LIMIT 336"
                )
            ]
            recent_prices.insert(0, price)
            rsi = calc_rsi(recent_prices)

            update_btc_brain(conn, price, rsi)
            log_btc_tick(conn, price, rsi)
            update_btc_momentum(conn)

        except Exception as e:
            logging.error(f"BTC tick error: {e}")
        finally:
            conn.close()
            try:
                import jarvis_brain
                jarvis_brain.update_bot_heartbeat("btc_ticker")
            except Exception:
                pass
            time.sleep(300)

if __name__ == "__main__":
    main()
