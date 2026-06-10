#!/usr/bin/env python3
"""
JARVIS INTEL — Unified Intelligence Bot
News, SEC filings, options flow, dark pool, congressional trades
Economic calendar, crypto sentiment, morning brief
"""
import json, time, requests, os, re
from datetime import datetime, timedelta
import jarvis_brain

TELEGRAM_TOKEN = __import__("jarvis_secrets").TG_TOKEN_INTEL
TELEGRAM_CHAT  = "7534553840"
from jarvis_secrets import CLAUDE_API_KEY
INTEL_FILE     = "/root/jarvis/jarvis_intel.json"
WATCH_TICKERS  = ["NVDA","AMD","TSLA","COIN","SPY","AAPL","MSFT","F","GM","RIVN"]

import logging
import jarvis_brain as _jb_hb
log = logging.getLogger("jarvis_intel")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

def tg(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": msg[:4000]}, timeout=5)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def load_intel():
    try:
        with open(INTEL_FILE) as f: return json.load(f)
    except: return {"news_learnings":[],"insider_alerts":[],"options_alerts":[],"hot_tickers":{}}

def save_intel(intel):
    with open(INTEL_FILE,"w") as f: json.dump(intel,f,indent=2)

def fetch_news(intel):
    try:
        learnings = intel.get("news_learnings",[])
        positive_words = ["surge","jump","beat","soar","rally","buy","upgrade","bullish","record","gain"]
        negative_words = ["crash","drop","miss","fall","sell","downgrade","bearish","loss","cut","decline"]
        for ticker in WATCH_TICKERS + ["BTC","ETH"]:
            try:
                r = requests.get(f"https://finviz.com/rss.ashx?t={ticker}",
                    timeout=8, headers={"User-Agent":"Mozilla/5.0"})
                if r.status_code != 200: continue
                headlines = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", r.text)
                headlines = [h for h in headlines if "Finviz" not in h][:3]
                for headline in headlines:
                    if any(l.get("headline") == headline for l in learnings): continue
                    hl = headline.lower()
                    pos = sum(1 for w in positive_words if w in hl)
                    neg = sum(1 for w in negative_words if w in hl)
                    sentiment = "positive" if pos > neg else "negative" if neg > pos else "neutral"
                    learnings.append({"ticker":ticker,"headline":headline,"sentiment":sentiment,"timestamp":str(datetime.now())})
                    if sentiment == "positive": jarvis_brain.add_hot_ticker(ticker)
                    log.info("NEWS: "+ticker+" "+sentiment+" — "+headline[:60])
            except: continue
        intel["news_learnings"] = learnings[-500:]
        pos = sum(1 for l in learnings[-20:] if l.get("sentiment")=="positive")
        neg = sum(1 for l in learnings[-20:] if l.get("sentiment")=="negative")
        jarvis_brain.set_market_mood("bullish" if pos>neg else "bearish" if neg>pos else "neutral")
    except Exception as e:
        log.error("News error: "+str(e))

def fetch_insider_filings(intel):
    try:
        log.info("--- SEC INSIDER SCAN ---")
        r = requests.get("https://efts.sec.gov/LATEST/search-index?q=%22Form+4%22&dateRange=custom&startdt=" +
            (datetime.now()-timedelta(days=1)).strftime("%Y-%m-%d") +
            "&enddt="+datetime.now().strftime("%Y-%m-%d")+"&hits.hits.total.value=true",
            timeout=10, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code == 200:
            hits = r.json().get("hits",{}).get("hits",[])
            log.info("SEC returned "+str(len(hits))+" filings")
    except Exception as e:
        log.error("Insider error: "+str(e))

def fetch_crypto_sentiment(intel):
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        if r.status_code == 200:
            d = r.json().get("data",[{}])[0]
            score = d.get("value","?")
            label = d.get("value_classification","?")
            log.info("Fear & Greed: "+str(score)+" ("+label+")")
            jarvis_brain.set_btc_signal("bullish" if int(score) > 60 else "bearish" if int(score) < 30 else "neutral")
    except Exception as e:
        log.error("Sentiment error: "+str(e))

def check_crash(intel):
    try:
        r = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/historic?period=hour", timeout=5)
        prices = [float(p["price"]) for p in r.json().get("data",{}).get("prices",[])]
        if len(prices) < 5: return
        change = (prices[-1]-prices[-5])/prices[-5]*100
        if change <= -5:
            jarvis_brain.set_risk_level("stop")
            tg("CRASH DETECTED — BTC dropped "+str(round(change,1))+"% in 1hr\nALL TRADING HALTED\nSend RESUME to restart")
        elif change >= 5:
            jarvis_brain.set_btc_signal("bullish")
            tg("BTC SURGE "+str(round(change,1))+"% in 1hr — signal BULLISH")
    except: pass

def send_morning_brief(intel):
    try:
        brain = jarvis_brain.read_brain()
        r = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=5)
        btc = r.json()["data"]["amount"]
        r2 = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        fg = r2.json().get("data",[{}])[0]
        news = intel.get("news_learnings",[])
        pos_news = [l for l in news[-50:] if l.get("sentiment")=="positive"][:3]
        neg_news = [l for l in news[-50:] if l.get("sentiment")=="negative"][:3]
        hot = brain.get("hot_tickers",[])
        msg = (
            "JARVIS MORNING BRIEF\n"+"="*25+"\n"
            +datetime.now().strftime("%A, %B %d, %Y")+"\n"
            +"BTC: $"+str(btc)+"\n"
            +"Fear & Greed: "+str(fg.get("value","?"))+" ("+fg.get("value_classification","?")+")\n"
            +"BTC Signal: "+brain.get("btc_signal","neutral")+"\n"
            +"Market Mood: "+brain.get("market_mood","neutral")+"\n"
            +"Risk Level: "+brain.get("risk_level","normal")+"\n"
            +"Hot Tickers: "+str(hot[:5])+"\n"
            +"Last Alpha Trade: "+str(brain.get("alpha_last_trade",{}).get("asset","none"))+"\n"
            +"="*25+"\n"
            +"POSITIVE NEWS:\n"
            +("\n".join([l.get("ticker","")+" — "+l.get("headline","")[:50] for l in pos_news]) or "None")+"\n"
            +"\nNEGATIVE NEWS:\n"
            +("\n".join([l.get("ticker","")+" — "+l.get("headline","")[:50] for l in neg_news]) or "None")
        )
        tg(msg)
        jarvis_brain.set_market_mood("briefing_sent")
    except Exception as e:
        log.error("Morning brief error: "+str(e))

def main():
    log.info("JARVIS INTEL ONLINE")
    intel = load_intel()
    tg("JARVIS INTEL ONLINE\nMonitoring: News, SEC, Crypto Sentiment, Crash Detection")
    import time as _t
    # Persist last run times across restarts
    _ts_file = "/root/jarvis/jarvis_intel_timestamps.json"
    try:
        import json as _j
        _ts = _j.load(open(_ts_file))
    except:
        _ts = {}
    last_news = _ts.get("last_news", _t.time())
    last_insider = _ts.get("last_insider", _t.time())
    last_sentiment = _ts.get("last_sentiment", _t.time())
    last_crash = _ts.get("last_crash", _t.time())
    last_brief_day = ""

    while True:
        try:
            now = time.time()
            from zoneinfo import ZoneInfo
            current_hour = datetime.now(ZoneInfo('America/New_York')).hour
            if not (9 <= current_hour <= 16):
                time.sleep(300)
                continue
            today = datetime.now().strftime("%Y-%m-%d")

            # Morning brief at 7am
            if current_hour == 7 and last_brief_day != today:
                last_brief_day = today
                send_morning_brief(intel)

            def _persist_ts():
                import json as _j
                try:
                    _j.dump({"last_news":last_news,"last_insider":last_insider,
                             "last_sentiment":last_sentiment,"last_crash":last_crash},
                            open(_ts_file,"w"))
                except Exception as _te:
                    log.error("ts persist error: "+str(_te))

            # News every 30 min
            if now - last_news >= 1800:
                last_news = now
                fetch_news(intel)
                save_intel(intel)
                _persist_ts()

            # Insider filings every 2 hours
            if now - last_insider >= 7200:
                last_insider = now
                fetch_insider_filings(intel)
                _persist_ts()

            # Crypto sentiment every 30 min
            if now - last_sentiment >= 1800:
                last_sentiment = now
                fetch_crypto_sentiment(intel)
                _persist_ts()

            # Crash check every 10 min
            if now - last_crash >= 600:
                last_crash = now
                check_crash(intel)
                _persist_ts()

            _jb_hb.update_bot_heartbeat("jarvis_intel")


            time.sleep(60)

        except Exception as e:
            log.error("Intel loop error: "+str(e))
            time.sleep(30)

if __name__ == "__main__":
    main()
