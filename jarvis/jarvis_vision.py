#!/usr/bin/env python3
import requests, base64, json, logging
log = logging.getLogger("JARVIS_VISION")
from jarvis_secrets import CLAUDE_API_KEY as CLAUDE_KEY
TG_TOKEN = __import__("jarvis_secrets").TG_TOKEN_TRADER

def download_telegram_photo(file_id):
    try:
        r = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getFile",
            params={"file_id": file_id}, timeout=10)
        file_path = r.json()["result"]["file_path"]
        r2 = requests.get(f"https://api.telegram.org/file/bot{TG_TOKEN}/{file_path}", timeout=15)
        return r2.content
    except Exception as e:
        log.error(f"Download: {e}")
        return None

def analyze_chart(image_bytes, ticker=None):
    try:
        image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        cb = {}; macro = {}
        try:
            cb = json.load(open("/root/jarvis/jarvis_central_brain.json"))
            macro = json.load(open("/root/jarvis/jarvis_macro.json"))
        except: pass
        regime = macro.get("regime", "UNKNOWN")
        fg = cb.get("fear_greed", 50)
        vix = macro.get("vix", {}).get("value", 0)
        ticker_str = f"Ticker: {ticker}" if ticker else "Identify ticker from chart"
        prompt = f"""You are JARVIS, elite trading AI. Analyze this chart.
{ticker_str}
CONTEXT: Regime={regime} Fear&Greed={fg} VIX={vix:.1f}

Reply EXACTLY in this format:
TICKER: [symbol]
PATTERN: [pattern name]
TREND: [UP/DOWN/SIDEWAYS] [STRONG/WEAK]
SUPPORT: $[level]
RESISTANCE: $[level]
SIGNAL: [BUY/SELL/HOLD] [confidence%]
ENTRY: $[price]
STOP: $[stop loss]
TARGET: $[target]
REASON: [one sentence]
BEAST_SIGNALS: [0-6 how many of 6 signals this confirms]"""
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": 300,
                  "messages": [{"role": "user", "content": [
                      {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                      {"type": "text", "text": prompt}
                  ]}]}, timeout=30)
        d = r.json()
        if "error" in d: return f"Claude error: {d['error']}"
        return d["content"][0]["text"].strip()
    except Exception as e:
        log.error(f"Analyze: {e}")
        return None

def handle_photo(file_id, caption=None):
    try:
        image_bytes = download_telegram_photo(file_id)
        if not image_bytes: return "Failed to download image"
        ticker = caption.strip().upper() if caption and len(caption.strip()) < 10 else None
        try:
            macro = json.load(open("/root/jarvis/jarvis_macro.json"))
            regime = macro.get("regime", "UNKNOWN")
        except: regime = "UNKNOWN"
        emoji = {"RISK_ON":"🟢","RISK_OFF":"🔴","STAGFLATION":"🟡","RECOVERY":"🔵"}.get(regime,"⚪")
        analysis = analyze_chart(image_bytes, ticker)
        if not analysis: return "Chart analysis failed"
        return f"📊 JARVIS CHART ANALYSIS\n{emoji} Regime: {regime}\n{'='*22}\n{analysis}\n{'='*22}\nSend another chart or text BTC for Kalshi"
    except Exception as e:
        return f"Error: {e}"
