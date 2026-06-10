import jarvis_brain as _jb_hb
#!/usr/bin/env python3
"""
JARVIS CONGRESS TRACKER
Scrapes Capitol Trades for recent congressional stock purchases.
Updates central brain with hot congress tickers.
Runs every 4 hours.
"""
import requests, json, os, time, logging
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

log = logging.getLogger("JARVIS_CONGRESS")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BRAIN_FILE    = "/root/jarvis/jarvis_central_brain.json"
CONGRESS_FILE = "/root/jarvis/jarvis_congress.json"
TG_TOKEN      = __import__("jarvis_secrets").TG_TOKEN_TRADER
CHAT_ID       = "7534553840"
INTERVAL      = 14400  # 4 hours

# High-value politicians to track (most active traders)
TRACK_POLITICIANS = [
    "Nancy Pelosi", "Michael McCaul", "Josh Gottheimer",
    "Ro Khanna", "Tommy Tuberville", "Marjorie Taylor Greene",
    "Rob Bresnahan", "Julie Johnson", "Jefferson Shreve",
    "Lisa McClain", "Byron Donalds", "David Taylor"
]

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/html",
    "Referer": "https://www.capitoltrades.com"
})

def tg(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg}, timeout=10)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def load(f, default=None):
    try: return json.load(open(f))
    except: return default or {}

def save(f, data):
    with open(f, 'w') as fp: json.dump(data, fp, indent=2)

def fetch_recent_trades(limit=50, tx_type="buy"):
    """Fetch recent congressional trades from Capitol Trades"""
    try:
        r = SESSION.get(
            "https://www.capitoltrades.com/trades",
            params={"txType": tx_type, "pageSize": limit},
            timeout=15
        )
        if r.status_code != 200:
            log.warning(f"Capitol Trades HTTP {r.status_code}")
            return []

        soup = BeautifulSoup(r.text, 'html.parser')
        trades = []

        # Parse trade rows
        rows = soup.find_all('tr', class_=lambda x: x and 'border-b' in x)
        for row in rows:
            try:
                # Politician name
                pol_el = row.find('h2', class_='politician-name')
                politician = pol_el.get_text(strip=True) if pol_el else "Unknown"

                # Party
                party_el = row.find('span', class_=lambda x: x and 'party' in str(x))
                party = party_el.get_text(strip=True) if party_el else ""

                # Ticker/Issuer
                ticker_el = row.find('span', class_='issuer-ticker')
                ticker = ticker_el.get_text(strip=True).replace(":US","") if ticker_el else ""

                issuer_el = row.find('h3', class_='q-fieldset')
                issuer = issuer_el.get_text(strip=True) if issuer_el else ""

                # Transaction type
                tx_el = row.find('span', class_=lambda x: x and 'tx-type' in str(x))
                tx_type_val = tx_el.get_text(strip=True) if tx_el else ""

                # Price
                price_els = row.find_all('span', class_=None)
                price = ""
                for el in price_els:
                    text = el.get_text(strip=True)
                    if text.startswith('$') and len(text) < 15:
                        price = text
                        break

                if ticker and tx_type_val == "buy":
                    trades.append({
                        "politician": politician,
                        "party": party,
                        "ticker": ticker,
                        "issuer": issuer,
                        "tx_type": tx_type_val,
                        "price": price,
                        "ts": datetime.now().isoformat()
                    })
            except: continue

        log.info(f"Parsed {len(trades)} trades from Capitol Trades")
        return trades

    except Exception as e:
        log.error(f"Fetch error: {e}")
        return []

def get_tracked_politician_trades():
    """Focus on high-value politicians"""
    all_trades = fetch_recent_trades(limit=100)
    tracked = [t for t in all_trades if any(p in t['politician'] for p in TRACK_POLITICIANS)]
    return all_trades, tracked

def update_congress_brain(trades):
    """Update congress data and hot tickers"""
    congress = load(CONGRESS_FILE, {"trades": [], "hot_tickers": {}, "politician_scores": {}})

    # Add new trades (avoid duplicates)
    existing = {f"{t['politician']}_{t['ticker']}_{t['ts'][:10]}" for t in congress["trades"]}
    new_trades = []
    for t in trades:
        key = f"{t['politician']}_{t['ticker']}_{t['ts'][:10]}"
        if key not in existing:
            new_trades.append(t)
            congress["trades"].append(t)

    # Keep last 500 trades
    congress["trades"] = congress["trades"][-500:]

    # Calculate hot tickers — how many politicians bought in last 30 days
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    recent = [t for t in congress["trades"] if t['ts'] > cutoff and t['tx_type'] == 'buy']

    ticker_counts = {}
    ticker_politicians = {}
    for t in recent:
        tk = t['ticker']
        if tk and tk != "":
            ticker_counts[tk] = ticker_counts.get(tk, 0) + 1
            if tk not in ticker_politicians:
                ticker_politicians[tk] = []
            if t['politician'] not in ticker_politicians[tk]:
                ticker_politicians[tk].append(t['politician'])

    congress["hot_tickers"] = {
        tk: {
            "count": cnt,
            "politicians": ticker_politicians.get(tk, []),
            "score": cnt * 10  # weight by number of buyers
        }
        for tk, cnt in sorted(ticker_counts.items(), key=lambda x: x[1], reverse=True)[:20]
    }
    congress["last_updated"] = datetime.now().isoformat()
    save(CONGRESS_FILE, congress)

    # Update central brain with congress hot tickers
    cb = load(BRAIN_FILE)
    congress_hot = sorted(congress["hot_tickers"].items(), key=lambda x: x[1]['count'], reverse=True)[:10]
    cb["congress_hot_tickers"] = [tk for tk, _ in congress_hot]
    cb["congress_last_update"] = datetime.now().isoformat()
    save(BRAIN_FILE, cb)

    return new_trades

def format_trade_alert(trades):
    """Format new trades for Telegram"""
    if not trades: return None
    lines = ["🏛 CONGRESS TRADES ALERT"]
    for t in trades[:8]:
        party_emoji = "🔴" if "Republican" in t['party'] else "🔵"
        lines.append(f"{party_emoji} {t['politician'].split()[-1]} BUY {t['ticker']} @ {t['price']}")
    if len(trades) > 8:
        lines.append(f"...and {len(trades)-8} more")
    return "\n".join(lines)

def run_cycle():
    log.info("Scanning Capitol Trades...")
    all_trades, tracked = get_tracked_politician_trades()

    if not all_trades:
        log.warning("No trades fetched")
        return

    new_trades = update_congress_brain(all_trades)

    # Alert on new tracked politician trades
    new_tracked = [t for t in new_trades if any(p in t['politician'] for p in TRACK_POLITICIANS)]
    if new_tracked:
        msg = format_trade_alert(new_tracked)
        if msg: tg(msg)
        for t in new_tracked:
            if not t.get("ticker"):
                continue
            sig = "CONGRESS_SELL" if t.get("tx_type") == "sell" else "CONGRESS_BUY"
            try:
                _jb_hb.log_intel_signal(
                    sig, t["ticker"], "congress",
                    f"{t.get('politician','?')} ({t.get('party','')}) {t.get('tx_type','buy')} @ {t.get('price','')}")
            except Exception as e:
                log.warning(f"log_intel_signal error: {e}")
        log.info(f"Alerted on {len(new_tracked)} tracked politician trades")

    # Log top congress hot tickers
    congress = load(CONGRESS_FILE, {})
    hot = congress.get("hot_tickers", {})
    if hot:
        top5 = list(hot.keys())[:5]
        log.info(f"Congress hot tickers: {top5}")

def main():
    log.info("JARVIS CONGRESS TRACKER ONLINE")
    tg("🏛 Congress Tracker online — monitoring 200 politicians")
    while True:
        try:
            run_cycle()
        except Exception as e:
            log.error(f"Cycle error: {e}")
        _jb_hb.update_bot_heartbeat("jarvis_congress")
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
