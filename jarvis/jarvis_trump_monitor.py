#!/usr/bin/env python3
"""
JARVIS Trump Signal Monitor
Scrapes Truth Social every 5 min, classifies market-moving policy signals,
maps to affected tickers, sends Telegram alerts.

Only alerts on: tariffs, chips/semis, defense, trade deals, Fed/rates, energy policy
Ignores: endorsements, rallies, personal posts, vague rhetoric
"""

import time
import requests
import json
import sqlite3
import hashlib
from datetime import datetime

# ── Config ───────────────────────────────────────────────────────
TELEGRAM_TOKEN = open("/root/jarvis/telegram_token.txt").read().strip() if __import__("os").path.exists("/root/jarvis/telegram_token.txt") else ""
TELEGRAM_CHAT  = "7534553840"
ANTHROPIC_KEY  = open("/root/jarvis/anthropic_key.txt").read().strip() if __import__("os").path.exists("/root/jarvis/anthropic_key.txt") else ""
DB_PATH        = "/root/jarvis/jarvis_memory.db"
LOG_PATH       = "/root/jarvis/jarvis_trump_monitor.log"
POLL_INTERVAL  = 300  # 5 minutes
SEEN_PATH      = "/root/jarvis/trump_seen.json"

# ── Ticker impact map ────────────────────────────────────────────
POLICY_TICKER_MAP = {
    "tariff":           ["SPY", "QQQ", "XLI", "CAT", "DE", "BA"],
    "china":            ["AAPL", "NVDA", "AMD", "QCOM", "TSM", "AMAT", "LRCX"],
    "semiconductor":    ["NVDA", "AMD", "INTC", "AMAT", "LRCX", "KLAC", "QCOM", "TSM"],
    "chips":            ["NVDA", "AMD", "INTC", "AMAT", "LRCX", "KLAC", "QCOM"],
    "export_control":   ["NVDA", "AMD", "AMAT", "LRCX", "KLAC"],
    "defense":          ["LMT", "RTX", "NOC", "GD", "BA", "L3H"],
    "trade_deal":       ["SPY", "QQQ", "XLI", "XLB"],
    "fed_rates":        ["TLT", "GLD", "SPY", "XLF", "JPM", "BAC"],
    "energy":           ["XOM", "CVX", "COP", "XLE", "OXY", "LNG"],
    "oil":              ["XOM", "CVX", "COP", "XLE", "OXY", "USO"],
    "steel_aluminum":   ["NUE", "STLD", "X", "AA", "CLF"],
    "pharma":           ["JNJ", "PFE", "MRK", "LLY", "ABBV"],
    "crypto":           ["BTC", "COIN", "MSTR", "MARA"],
    "sanctions":        ["SPY", "GLD", "USO"],
    "mexico_canada":    ["SPY", "XLI", "F", "GM", "STLD"],
}

SYSTEM_PROMPT = """You are a financial intelligence classifier for a trading system.

Analyze this Trump Truth Social post and determine if it contains a MARKET-MOVING policy signal.

CLASSIFY AS "POLICY" only if it contains:
- Tariff announcements, changes, carve-outs, or exemptions (specific countries/products)
- Semiconductor/chip export controls, restrictions, or CHIPS Act mentions
- Defense spending, contracts, or procurement (specific companies or sectors)
- Trade deal announcements or breakdowns (specific countries)
- Federal Reserve pressure, rate commentary, or Powell criticism with policy implications
- Energy policy: drilling, LNG exports, oil sanctions, strategic reserve
- Steel/aluminum tariffs or industrial policy
- Pharma pricing or import policy
- Crypto regulation or government Bitcoin policy
- Sanctions on specific countries or sectors

CLASSIFY AS "HYPE" if it contains:
- Political endorsements of candidates or officials
- Rally or event announcements
- Personal attacks or social media feuds
- Vague "America First" rhetoric without specific policy action
- Sports, golf, or personal lifestyle content
- General praise/criticism without policy substance
- Reposting other people's content without new policy info

Respond ONLY with valid JSON, no markdown:
{
  "classification": "POLICY" or "HYPE",
  "confidence": 0-100,
  "category": one of [tariff, semiconductor, chips, defense, trade_deal, fed_rates, energy, oil, steel_aluminum, pharma, crypto, sanctions, china, mexico_canada, export_control, other],
  "summary": "one sentence max — what is the actual policy action",
  "direction": "BULLISH" or "BEARISH" or "MIXED",
  "affected_sectors": ["list", "of", "sectors"],
  "urgency": "HIGH" or "MEDIUM" or "LOW",
  "reasoning": "why this is or isn't market moving"
}"""


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{ts}] {msg}"
    with open(LOG_PATH, "a") as f:
        f.write(entry + "\n")
    print(entry)


def tg(msg):
    if not TELEGRAM_TOKEN:
        log(f"[TG] {msg}")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": msg[:4000]},
            timeout=10
        )
        if r.status_code != 200 or not r.json().get("ok"):
            log(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log(f"Telegram send error: {e}")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trump_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_hash TEXT UNIQUE,
            post_text TEXT,
            classification TEXT,
            confidence INTEGER,
            category TEXT,
            summary TEXT,
            direction TEXT,
            urgency TEXT,
            tickers TEXT,
            logged_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def load_seen():
    try:
        return set(json.load(open(SEEN_PATH)))
    except:
        return set()


def save_seen(seen):
    with open(SEEN_PATH, "w") as f:
        json.dump(list(seen)[-500:], f)  # keep last 500


def hash_post(text):
    return hashlib.md5(text.encode()).hexdigest()


def fetch_truth_social():
    """
    Fetch latest Trump Truth Social posts.
    Uses rss2json as a lightweight bridge — no auth required.
    Falls back to nitter RSS if primary fails.
    """
    sources = [
        "https://rss.app/feeds/truthsocial_realDonaldTrump.xml",
        "https://truthsocial.com/@realDonaldTrump.rss",
    ]

    for url in sources:
        try:
            resp = requests.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (compatible; JARVIS/1.0)"
            })
            if resp.status_code == 200:
                # Parse RSS/XML
                import xml.etree.ElementTree as ET
                root = ET.fromstring(resp.text)
                items = root.findall(".//item")
                posts = []
                for item in items[:10]:  # last 10 posts
                    title = item.findtext("title", "")
                    desc = item.findtext("description", "")
                    pub_date = item.findtext("pubDate", "")
                    text = desc or title
                    # Strip HTML tags
                    import re
                    text = re.sub(r"<[^>]+>", "", text).strip()
                    if text:
                        posts.append({"text": text, "date": pub_date})
                if posts:
                    log(f"Fetched {len(posts)} posts from {url}")
                    return posts
        except Exception as e:
            log(f"Source {url} failed: {e}")
            continue

    log("All sources failed — no posts fetched")
    return []


def classify_post(text):
    """Send post to Claude for classification"""
    if not ANTHROPIC_KEY:
        log("No Anthropic key found")
        return None
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 500,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": f"Classify this post:\n\n{text}"}]
            },
            timeout=30
        )
        raw = resp.json()["content"][0]["text"].strip()
        # Strip markdown fences if present
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        log(f"Claude classify error: {e}")
        return None


def get_tickers_for_category(category, text):
    """Get relevant tickers based on category and post content"""
    tickers = set()
    text_lower = text.lower()

    # Primary category match
    if category in POLICY_TICKER_MAP:
        tickers.update(POLICY_TICKER_MAP[category])

    # Secondary keyword scan
    for keyword, ticker_list in POLICY_TICKER_MAP.items():
        if keyword in text_lower:
            tickers.update(ticker_list)

    # Specific company mentions
    company_map = {
        "nvidia": ["NVDA"], "nvda": ["NVDA"],
        "apple": ["AAPL"], "microsoft": ["MSFT"],
        "boeing": ["BA"], "lockheed": ["LMT"],
        "exxon": ["XOM"], "chevron": ["CVX"],
        "jpmorgan": ["JPM"], "goldman": ["GS"],
        "ford": ["F"], "gm": ["GM"], "general motors": ["GM"],
        "bitcoin": ["BTC", "COIN", "MSTR"],
        "taiwan": ["TSM", "NVDA", "AMD"],
    }
    for name, syms in company_map.items():
        if name in text_lower:
            tickers.update(syms)

    return sorted(list(tickers))[:8]  # cap at 8 tickers


def save_signal(post_hash, post_text, result, tickers):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.execute("""
            INSERT OR IGNORE INTO trump_signals
            (post_hash, post_text, classification, confidence, category,
             summary, direction, urgency, tickers)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            post_hash, post_text[:500],
            result.get("classification"),
            result.get("confidence"),
            result.get("category"),
            result.get("summary"),
            result.get("direction"),
            result.get("urgency"),
            ",".join(tickers)
        ))
        conn.commit()
    except Exception as e:
        log(f"DB save error: {e}")
    conn.close()


def format_alert(result, tickers, post_text):
    direction_icon = {"BULLISH": "🟢", "BEARISH": "🔴", "MIXED": "🟡"}.get(result.get("direction"), "⚪")
    urgency_icon = {"HIGH": "🚨", "MEDIUM": "⚠️", "LOW": "ℹ️"}.get(result.get("urgency"), "")

    ticker_str = " ".join(tickers) if tickers else "SPY"

    return (
        f"{urgency_icon} TRUMP POLICY SIGNAL {direction_icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 {result.get('summary', 'No summary')}\n"
        f"Category: {result.get('category', '').upper()}\n"
        f"Direction: {result.get('direction')} | Confidence: {result.get('confidence')}%\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Watch: {ticker_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 \"{post_text[:200]}{'...' if len(post_text) > 200 else ''}\""
    )


def run():
    init_db()
    seen = load_seen()
    log("Trump Monitor ONLINE — polling every 5 min")
    log("Watching: tariffs | chips | defense | trade | Fed | energy")
    tg("🇺🇸 Trump Signal Monitor ONLINE\nWatching for: tariffs, chips, defense, trade deals, Fed, energy\nPolling every 5 min")

    while True:
        try:
            posts = fetch_truth_social()

            for post in posts:
                text = post["text"]
                if len(text) < 20:
                    continue

                h = hash_post(text)
                if h in seen:
                    continue

                seen.add(h)
                save_seen(seen)

                log(f"New post: {text[:80]}...")

                result = classify_post(text)
                if not result:
                    continue

                log(f"Classification: {result.get('classification')} | {result.get('category')} | confidence {result.get('confidence')}%")

                if result.get("classification") == "POLICY" and result.get("confidence", 0) >= 70:
                    tickers = get_tickers_for_category(result.get("category", ""), text)
                    save_signal(h, text, result, tickers)
                    alert = format_alert(result, tickers, text)
                    tg(alert)
                    log(f"ALERT SENT: {result.get('summary')}")
                else:
                    log(f"Skipped (HYPE or low confidence): {text[:60]}")

        except Exception as e:
            log(f"Loop error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
