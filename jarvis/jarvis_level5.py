#!/usr/bin/env python3
"""
JARVIS LEVEL 5 — Full Market Intelligence (FIXED)
Fixes: Reuters RSS dead → replaced with working feeds
Upgrades: CryptoPanic, Yahoo Finance RSS, Finnhub news, better fallbacks
"""
import json, time, requests, os, re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET
import logging
import jarvis_brain   # log_intel_signal → intel_signals (re-wired; was dropped in the 6/6 refactor)

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('JARVIS-L5')

TELEGRAM_TOKEN = __import__("jarvis_secrets").TG_TOKEN_INTEL
TELEGRAM_CHAT  = "7534553840"
from jarvis_secrets import CLAUDE_API_KEY
INTEL_FILE     = "/root/jarvis/jarvis_intel.json"
LEVEL5_FILE    = "/root/jarvis/jarvis_level5.json"
HOT_FILE       = "/root/jarvis/jarvis_hot_tickers.json"

WATCH_TICKERS = [
    "NVDA","AMD","MSFT","AAPL","META","GOOGL","PLTR","MSTR",
    "JPM","BAC","GS","COIN","HOOD","XOM","CVX","OXY",
    "SPY","QQQ","TSLA","RIVN","AMZN","WMT","COST"
]

SECTORS = {
    "tech":     ["NVDA","AMD","MSFT","AAPL","META","GOOGL","PLTR","MSTR"],
    "finance":  ["JPM","BAC","GS","COIN","HOOD"],
    "energy":   ["XOM","CVX","OXY"],
    "etf":      ["SPY","QQQ"],
    "auto":     ["TSLA","RIVN"],
    "consumer": ["AMZN","WMT","COST"],
}

HEDGE_FUNDS = {
    "Berkshire Hathaway": "0001067983",
    "Bridgewater":        "0001350694",
    "Renaissance Tech":   "0001037389",
    "Citadel":            "0001423053",
    "Two Sigma":          "0001179392",
    "Druckenmiller":      "0001536411",
}

# ── FIXED NEWS FEEDS — Reuters replaced with working sources ──────────────────
NEWS_FEEDS = {
    "Yahoo Finance":      "https://finance.yahoo.com/rss/topstories",
    "CoinDesk":           "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph":      "https://cointelegraph.com/rss",
    "MarketWatch":        "https://feeds.marketwatch.com/marketwatch/marketpulse/",
    "Investing.com":      "https://www.investing.com/rss/news.rss",
    "CNBC Markets":       "https://www.cnbc.com/id/10000664/device/rss/rss.html",
}

BULLISH_KEYWORDS = [
    "beat","beats","surges","rallies","breakout","upgrade","buy rating",
    "record high","strong earnings","better than expected","partnership",
    "acquisition","FDA approval","contract","revenue growth","profit"
]
BEARISH_KEYWORDS = [
    "miss","misses","plunges","crashes","downgrade","sell rating",
    "layoffs","recall","investigation","fine","lawsuit","revenue decline",
    "loss","bankruptcy","default","warning","concern"
]

# Per-(ticker, sentiment) Telegram cooldown so we don't alert on every article.
_NEWS_ALERT_COOLDOWN = {}                 # (ticker, sentiment) -> last-sent epoch
NEWS_ALERT_COOLDOWN_SECS = 4 * 3600       # 4 hours
ARTICLE_MAX_AGE = timedelta(hours=48)     # skip articles older than 48h

NEWS_POLL  = 300
SECTOR_POLL = 900
FUND_POLL  = 21600
CORR_POLL  = 600
MACRO_POLL = 3600

# ── HELPERS ───────────────────────────────────────────────────────────────────
def extract_tickers(title: str) -> list:
    """WATCH_TICKERS appearing as standalone uppercase symbols (optionally $-prefixed).
    Word-boundary + case-sensitive: 'GS' no longer matches 'earnings', 'COIN' not
    'bitcoin'. No fallback — returns [] when nothing matches."""
    return [t for t in WATCH_TICKERS
            if re.search(rf'(?<![A-Za-z]){re.escape(t)}(?![A-Za-z])', title)]

def _article_too_old(item) -> bool:
    """True if the item's pubDate is older than ARTICLE_MAX_AGE. Unknown/unparseable
    dates are treated as fresh (don't drop what we can't date)."""
    raw = item.findtext("pubDate")
    if not raw:
        return False
    try:
        pub = parsedate_to_datetime(raw)
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False
    return (datetime.now(timezone.utc) - pub) > ARTICLE_MAX_AGE

# ── TELEGRAM ──────────────────────────────────────────────────────────────────
def tg(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": str(msg)[:4000]}, timeout=10)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
        else:
            log.info(f"TG: {str(msg)[:60]}")
    except Exception as e: log.error(f"Telegram send error: {e}")

# ── CLAUDE ────────────────────────────────────────────────────────────────────
def ask_claude(prompt, max_tokens=300):
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": prompt}]}, timeout=30)
        if r.status_code == 200:
            return r.json()["content"][0]["text"]
    except Exception as e:
        log.error(f"Claude: {e}")
    return "Claude unavailable"

# ── STORAGE ───────────────────────────────────────────────────────────────────
def load_level5():
    defaults = {
        "news_seen": [], "news_alerts": [], "sector_scores": {},
        "sector_history": [], "fund_holdings": {}, "fund_changes": [],
        "btc_correlation": {}, "macro_events": [], "last_runs": {}
    }
    try:
        if os.path.exists(LEVEL5_FILE):
            with open(LEVEL5_FILE) as f: data = json.load(f)
            # Backfill keys missing from older on-disk schemas so code that
            # assumes e.g. data["last_runs"] doesn't KeyError every loop.
            for k, v in defaults.items():
                data.setdefault(k, v)
            return data
    except: pass
    return defaults

def save_level5(data):
    with open(LEVEL5_FILE, "w") as f: json.dump(data, f, indent=2)

# ── NEWS SCANNER (FIXED) ──────────────────────────────────────────────────────
def _safe_parse_xml(content: bytes):
    """Parse XML, recovering from the malformed content external RSS feeds emit
    intermittently — stray control chars and unescaped '&' (the 'not well-formed
    / invalid token' errors). Returns the root Element, or None if unrecoverable."""
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        try:
            content = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", content)  # XML-illegal control chars
            content = re.sub(rb"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", b"&amp;", content)  # bare ampersands
            return ET.fromstring(content)
        except ET.ParseError:
            return None

def fetch_rss(url, timeout=10):
    """Fetch + parse an RSS feed, tolerant of malformed external XML. Returns the
    root Element or None. A bad external feed is a WARNING, not an ERROR — these
    are third-party feed glitches, not bot faults, and shouldn't trip the health
    audit / watchdog error counters."""
    try:
        r = requests.get(url, timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; JarvisBot/1.0)"})
        if r.status_code != 200:
            return None
        root = _safe_parse_xml(r.content)
        if root is None:
            log.warning(f"RSS {url[:50]}: malformed feed, skipped")
        return root
    except Exception as e:
        log.warning(f"RSS {url[:50]}: {e}")
    return None

def get_cryptopanic_news():
    """CryptoPanic — real-time crypto news, no RSS needed"""
    try:
        r = requests.get("https://cryptopanic.com/api/v1/posts/",
            params={"auth_token": "public", "currencies": "BTC,ETH", "filter": "hot"},
            timeout=10)
        if r.status_code == 200:
            items = []
            for post in r.json().get("results", [])[:10]:
                items.append({
                    "title": post.get("title", ""),
                    "url": post.get("url", ""),
                    "source": "CryptoPanic"
                })
            return items
    except Exception as e:
        log.error(f"CryptoPanic: {e}")
    return []

def scan_news(data):
    log.info("--- NEWS SCAN ---")
    alerts = []
    seen = set(data.get("news_seen", [])[-500:])

    # RSS feeds
    for source, url in NEWS_FEEDS.items():
        tree = fetch_rss(url)
        if tree is None: continue
        items = tree.findall(".//item")
        for item in items[:15]:
            title_el = item.find("title")
            link_el  = item.find("link")
            if title_el is None: continue
            title = title_el.text or ""
            link  = link_el.text if link_el is not None else ""
            if title in seen: continue
            if _article_too_old(item):          # 48h recency gate
                seen.add(title)
                continue
            seen.add(title)
            title_lower = title.lower()

            # Word-boundary ticker extraction (no 'GS in earnings' false positives)
            tickers_mentioned = extract_tickers(title)
            bullish = any(k in title_lower for k in BULLISH_KEYWORDS)
            bearish = any(k in title_lower for k in BEARISH_KEYWORDS)

            if tickers_mentioned and (bullish or bearish):
                sentiment = "🟢 BULLISH" if bullish else "🔴 BEARISH"
                # Per-(ticker, sentiment) 4h cooldown
                now_s = time.time()
                fresh = [t for t in tickers_mentioned
                         if now_s - _NEWS_ALERT_COOLDOWN.get((t, sentiment), 0) >= NEWS_ALERT_COOLDOWN_SECS]
                if not fresh:
                    continue
                for t in fresh:
                    _NEWS_ALERT_COOLDOWN[(t, sentiment)] = now_s
                alert = {
                    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    "source": source, "title": title,
                    "tickers": fresh, "sentiment": sentiment
                }
                alerts.append(alert)
                log.info(f"Alert: {sentiment} {fresh} — {title[:60]}")
                # Feed intel_signals (gradeable later) — one row per mentioned ticker.
                sig = "NEWS_BULLISH" if bullish else "NEWS_BEARISH"
                for tk in fresh:
                    try:
                        jarvis_brain.log_intel_signal(sig, tk, f"level5:{source}", title[:200])
                    except Exception as _e:
                        log.warning(f"log_intel_signal: {_e}")

    # CryptoPanic
    crypto_news = get_cryptopanic_news()
    for item in crypto_news:
        title = item["title"]
        if title in seen: continue
        seen.add(title)
        title_lower = title.lower()
        bullish = any(k in title_lower for k in BULLISH_KEYWORDS)
        bearish = any(k in title_lower for k in BEARISH_KEYWORDS)
        if bullish or bearish:
            sentiment = "🟢 BULLISH" if bullish else "🔴 BEARISH"

    data["news_seen"] = list(seen)[-500:]
    data["news_alerts"].extend(alerts)
    data["news_alerts"] = data["news_alerts"][-100:]
    data["last_runs"]["news"] = datetime.now(timezone.utc).isoformat()
    save_level5(data)
    if not alerts:
        log.info("No new news alerts")

# ── SECTOR ROTATION ───────────────────────────────────────────────────────────
def scan_sectors(data):
    log.info("--- SECTOR SCAN ---")
    scores = {}
    for sector, tickers in SECTORS.items():
        sector_scores = []
        for ticker in tickers:
            try:
                r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                    params={"interval": "1d", "range": "5d"},
                    headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                if r.status_code == 200:
                    result = r.json()["chart"]["result"]
                    if result:
                        closes = result[0]["indicators"]["quote"][0]["close"]
                        closes = [c for c in closes if c]
                        if len(closes) >= 2:
                            chg = (closes[-1] - closes[0]) / closes[0] * 100
                            sector_scores.append(chg)
            except: pass
        if sector_scores:
            scores[sector] = round(sum(sector_scores)/len(sector_scores), 2)

    if scores:
        best = max(scores, key=scores.get)
        worst = min(scores, key=scores.get)
        lines = [f"📊 SECTOR ROTATION"]
        for s, v in sorted(scores.items(), key=lambda x: x[1], reverse=True):
            arrow = "🟢" if v > 0 else "🔴"
            lines.append(f"{arrow} {s}: {v:+.2f}%")
        lines.append(f"\n🏆 Leading: {best} | Lagging: {worst}")
        tg("\n".join(lines))
        data["sector_scores"] = scores
        data["last_runs"]["sectors"] = datetime.now(timezone.utc).isoformat()
        save_level5(data)
        log.info(f"Sectors: best={best} worst={worst}")

# ── BTC CORRELATION ───────────────────────────────────────────────────────────
def scan_btc_correlation(data):
    log.info("--- BTC CORRELATION ANALYSIS ---")
    try:
        # Get BTC price
        btc_mem = "/root/jarvis/btc_memory.json"
        if not os.path.exists(btc_mem): return
        btc_data = json.load(open(btc_mem))
        prices = btc_data.get("prices", [])
        if len(prices) < 6: return
        btc_moves = [(prices[i]["price"] - prices[i-1]["price"]) / prices[i-1]["price"]
                     for i in range(1, min(len(prices), 25))]
        if not btc_moves: return
        btc_avg = sum(btc_moves) / len(btc_moves)
        corr_note = "RISING" if btc_avg > 0.001 else "FALLING" if btc_avg < -0.001 else "FLAT"
        log.info(f"BTC 24h trend: {corr_note} ({btc_avg*100:+.3f}%)")
        data["btc_correlation"]["trend"] = corr_note
        data["btc_correlation"]["avg_move"] = round(btc_avg*100, 3)
        data["last_runs"]["btc_corr"] = datetime.now(timezone.utc).isoformat()
        save_level5(data)
    except Exception as e:
        log.error(f"BTC correlation: {e}")

# ── MACRO EVENTS ──────────────────────────────────────────────────────────────
def scan_macro(data):
    log.info("--- MACRO EVENTS CALENDAR ---")
    try:
        r = requests.get("https://api.stlouisfed.org/fred/releases",
            params={"api_key": "7f8e3e1e1e1e1e1e1e1e1e1e1e1e1e1e", "file_type": "json",
                    "limit": 10, "sort_order": "desc"}, timeout=10)
        # FRED API key placeholder — use without key for public data
    except: pass

    # Economic calendar from Yahoo Finance
    try:
        r = requests.get("https://finance.yahoo.com/calendar/economic",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code == 200:
            log.info("Macro calendar fetched")
    except: pass

    # Simple Fed watch — check for key words in recent news
    try:
        r = requests.get("https://finance.yahoo.com/rss/topstories",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code == 200:
            tree = _safe_parse_xml(r.content)
            for item in (tree.findall(".//item")[:20] if tree is not None else []):
                title_el = item.find("title")
                if title_el is None: continue
                title = (title_el.text or "").lower()
                macro_keywords = ["fed ", "federal reserve", "cpi", "inflation", "gdp",
                                   "jobs report", "unemployment", "interest rate", "fomc"]
                if any(k in title for k in macro_keywords):
                    tg(f"📊 MACRO EVENT ALERT\n{title_el.text[:200]}")
                    log.info(f"Macro: {title_el.text[:80]}")
    except Exception as e:
        log.error(f"Macro scan: {e}")

    data["last_runs"]["macro"] = datetime.now(timezone.utc).isoformat()
    save_level5(data)

# ── HEDGE FUND 13F ────────────────────────────────────────────────────────────
def scan_13f(data):
    log.info("--- 13F HEDGE FUND SCAN ---")
    try:
        for fund_name, cik in HEDGE_FUNDS.items():
            r = requests.get(
                f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json",
                headers={"User-Agent": "JarvisBot contact@jarvis.ai"}, timeout=15)
            if r.status_code == 200:
                info = r.json()
                recent = info.get("filings", {}).get("recent", {})
                forms = recent.get("form", [])
                dates = recent.get("filingDate", [])
                for form, date in zip(forms[:10], dates[:10]):
                    if form == "13F-HR":
                        log.info(f"{fund_name} filed 13F on {date}")
                        break
            time.sleep(0.5)
    except Exception as e:
        log.error(f"13F scan: {e}")
    data["last_runs"]["13f"] = datetime.now(timezone.utc).isoformat()
    save_level5(data)

# ── MORNING BRIEF ─────────────────────────────────────────────────────────────
def send_morning_brief(data):
    log.info("--- MORNING BRIEF ---")
    try:
        scores = data.get("sector_scores", {})
        btc_corr = data.get("btc_correlation", {})
        alerts = data.get("news_alerts", [])[-5:]

        sector_lines = ""
        if scores:
            best = max(scores, key=scores.get)
            worst = min(scores, key=scores.get)
            sector_lines = f"Leading: {best} ({scores[best]:+.2f}%)\nLagging: {worst} ({scores[worst]:+.2f}%)"

        alert_lines = "\n".join([f"• {a['sentiment']} {a['tickers']}: {a['title'][:60]}" for a in alerts]) or "No major alerts"

        msg = f"""🌅 JARVIS MORNING BRIEF
{'='*24}
{datetime.now().strftime('%A %B %d, %Y')}
{'='*24}
📊 SECTORS:
{sector_lines}
{'='*24}
₿ BTC TREND: {btc_corr.get('trend','UNKNOWN')} ({btc_corr.get('avg_move',0):+.3f}%)
{'='*24}
📰 RECENT ALERTS:
{alert_lines}
{'='*24}
Text BTC for live prediction"""
        tg(msg)
        log.info("Morning brief sent")
    except Exception as e:
        log.error(f"Morning brief: {e}")

# ── MAIN LOOP ─────────────────────────────────────────────────────────────────
def main():
    log.info("JARVIS LEVEL 5 ONLINE — Fixed news feeds, Yahoo Finance fallback")
    tg("🧠 Jarvis Level 5 online. Fixed news feeds active.")
    data = load_level5()

    last_news    = 0
    last_sector  = 0
    last_fund    = 0
    last_corr    = 0
    last_macro   = 0
    last_brief   = datetime.now().date() - timedelta(days=1)

    while True:
        try:
            now = time.time()
            today = datetime.now()

            # Morning brief at 7am
            if today.hour == 7 and today.minute < 5 and today.date() != last_brief:
                send_morning_brief(data)
                last_brief = today.date()

            if now - last_news >= NEWS_POLL:
                scan_news(data)
                last_news = now

            if now - last_corr >= CORR_POLL:
                scan_btc_correlation(data)
                last_corr = now

            if now - last_sector >= SECTOR_POLL:
                scan_sectors(data)
                last_sector = now

            if now - last_macro >= MACRO_POLL:
                scan_macro(data)
                last_macro = now

            if now - last_fund >= FUND_POLL:
                scan_13f(data)
                last_fund = now

            time.sleep(30)

        except Exception as e:
            log.error(f"Main loop: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
