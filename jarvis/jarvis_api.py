#!/usr/bin/env python3
"""
JARVIS API SERVER v2 — Production ready
Serves live data from central brain to JARVIS.html dashboard
Port 5005 on DigitalOcean VPS
"""
import json, os, time, requests, subprocess
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 5005
from jarvis_secrets import CLAUDE_API_KEY as CLAUDE_KEY
TG_TOKEN       = __import__("jarvis_secrets").TG_TOKEN_TRADER
TG_CHAT        = "7534553840"

# All data files
FILES = {
    "central_brain":  "/root/jarvis/jarvis_central_brain.json",
    "kalshi_brain":   "/root/jarvis/kalshi_brain.json",
    "master_brain":   "/root/jarvis/jarvis_master_brain.json",
    "btc_memory":     "/root/jarvis/btc_memory.json",
    "patterns":       "/root/jarvis/jarvis_patterns.json",
    "level5":         "/root/jarvis/jarvis_level5.json",
    "stocks_brain":   "/root/jarvis/jarvis_stocks_brain.json",
    "options_memory": "/root/jarvis/options_memory.json",
}

LOGS = {
    "jarvis_master":       "/root/jarvis/jarvis_master.log",
    "jarvis_stocks_v2":    "/root/jarvis/jarvis_stocks_v2.log",
    "jarvis_options":      "/root/jarvis/jarvis_options.log",
    "jarvis_level5":       "/root/jarvis/jarvis_level5.log",
    "jarvis_briefing":     "/root/jarvis/jarvis_briefing.log",
    "jarvis_intelligence": "/root/jarvis/jarvis_intelligence.log",
}

def load(path):
    try:
        if os.path.exists(path):
            with open(path) as f: return json.load(f)
    except: pass
    return {}

def get_bot_status():
    bots = []
    for name, log in LOGS.items():
        try:
            alive = False
            last_log = ""
            age_mins = 999

            # Check process running
            result = subprocess.run(["pgrep", "-f", name + ".py"],
                capture_output=True, text=True)
            process_alive = result.returncode == 0

            # Check log freshness
            if os.path.exists(log):
                mtime = os.path.getmtime(log)
                age_mins = round((time.time() - mtime) / 60, 1)
                alive = age_mins < 10 and process_alive
                with open(log) as f:
                    lines = [l.strip() for l in f.readlines() if l.strip()]
                    last_log = lines[-1][:100] if lines else ""
            else:
                alive = process_alive

            bots.append({
                "name": name,
                "alive": alive,
                "process": process_alive,
                "age_mins": age_mins,
                "last_log": last_log
            })
        except:
            bots.append({"name": name, "alive": False, "process": False,
                         "age_mins": 999, "last_log": "error"})
    return bots

def get_live_btc():
    try:
        r = requests.get("https://api.binance.us/api/v3/ticker/24hr",
            params={"symbol": "BTCUSDT"}, timeout=5)
        d = r.json()
        return {
            "price":   round(float(d["lastPrice"]), 2),
            "chg24h":  round(float(d["priceChangePercent"]), 2),
            "high":    round(float(d["highPrice"]), 2),
            "low":     round(float(d["lowPrice"]), 2),
            "volume":  round(float(d["volume"]), 2),
        }
    except: pass
    # Fallback to central brain
    cb = load(FILES["central_brain"])
    return {"price": cb.get("btc_price", 0), "chg24h": 0}

def get_kalshi_stats():
    kb = load(FILES["kalshi_brain"])
    s = kb.get("stats", {})
    total = s.get("total", 0)
    wins  = s.get("wins", 0)
    wr    = round(wins/total*100, 1) if total > 0 else 0
    yes_wr = round(s.get("yes_wins",0)/s.get("yes_total",1)*100) if s.get("yes_total",0) > 0 else 0
    no_wr  = round(s.get("no_wins",0)/s.get("no_total",1)*100)  if s.get("no_total",0) > 0 else 0
    # Recent bets
    bets = kb.get("bets", [])
    open_bets = [b for b in bets if b.get("result") is None]
    last5 = [b for b in bets if b.get("result") and b.get("result") != "VOID"][-5:]
    # Pred accuracy
    preds = kb.get("preds", [])
    good_preds = [p for p in preds if p.get("result") and p.get("result") != "VOID"]
    pred_correct = sum(1 for p in good_preds if p.get("correct"))
    pred_total = len(good_preds)
    pred_wr = round(pred_correct/pred_total*100) if pred_total > 0 else 0
    return {
        "total_bets":   total,
        "wins":         wins,
        "losses":       s.get("losses", 0),
        "win_rate":     wr,
        "profit":       round(s.get("profit", 0), 2),
        "yes_wr":       yes_wr,
        "no_wr":        no_wr,
        "pred_wr":      pred_wr,
        "pred_total":   pred_total,
        "open_bets":    len(open_bets),
        "last5":        [{"side": b.get("side"), "result": b.get("result"),
                          "pnl": b.get("pnl", 0), "label": b.get("label","")} for b in last5],
    }

def get_pattern_stats():
    pat = load(FILES["patterns"])
    fps = pat.get("fingerprints", {})
    total_patterns = len(fps)
    # Top 5 by sample count
    top = sorted(fps.items(), key=lambda x: x[1].get("total",0), reverse=True)[:5]
    top_list = []
    for fp, d in top:
        t = d.get("total", 0)
        w = d.get("wins", 0)
        wr = round(w/t*100) if t > 0 else 0
        top_list.append({"fingerprint": fp, "total": t, "wins": w, "wr": wr})
    return {"total": total_patterns, "top": top_list}

def get_recent_predictions():
    kb = load(FILES["kalshi_brain"])
    preds = kb.get("preds", [])
    recent = [p for p in preds if p.get("price", 0) >= 1000][-10:]
    return [{"ts": p.get("ts"), "price": p.get("price"), "direction": p.get("direction"),
             "conf": p.get("conf"), "result": p.get("result"), "correct": p.get("correct"),
             "mins": p.get("mins")} for p in reversed(recent)]

def get_log_tail(bot_name, lines=20):
    log = LOGS.get(bot_name, "")
    try:
        if os.path.exists(log):
            with open(log) as f:
                all_lines = f.readlines()
                return [l.strip() for l in all_lines[-lines:] if l.strip()]
    except: pass
    return []

def get_full_status():
    cb = load(FILES["central_brain"])
    mb = load(FILES["master_brain"])
    l5 = load(FILES["level5"])
    btc = get_live_btc()
    bots = get_bot_status()
    kalshi = get_kalshi_stats()
    patterns = get_pattern_stats()
    preds = get_recent_predictions()

    # Master bot stats
    m_stats = mb.get("stats", {})
    alive_count = sum(1 for b in bots if b.get("alive"))

    return {
        "ts": datetime.now().isoformat(),
        "system": {
            "bots_alive": alive_count,
            "bots_total": len(bots),
            "uptime_ok": alive_count >= 5,
        },
        "btc": {
            **btc,
            "rsi":        cb.get("btc_rsi", 50),
            "signal":     cb.get("btc_signal", "neutral"),
            "trend_4h":   cb.get("btc_trend_4h", "NEUTRAL"),
            "macd":       cb.get("btc_macd", "neutral"),
            "fear_greed": cb.get("fear_greed", 50),
            "funding":    cb.get("funding_rate", 0),
            "volume":     cb.get("volume_ratio", 1),
            "risk":       cb.get("risk_level", "NORMAL"),
        },
        "kalshi": kalshi,
        "trading": {
            "total_trades":      m_stats.get("total_trades", 0),
            "wins":              m_stats.get("wins", 0),
            "losses":            m_stats.get("losses", 0),
            "total_pnl":         round(m_stats.get("total_pnl", 0), 2),
            "daily_loss":        round(m_stats.get("daily_loss", 0), 2),
            "size_multiplier":   m_stats.get("size_multiplier", 1.0),
            "consec_losses":     m_stats.get("consecutive_losses", 0),
        },
        "patterns": patterns,
        "predictions": preds,
        "bots": bots,
        "sector_scores": l5.get("sector_scores", {}),
        "hot_tickers":   cb.get("hot_tickers", [])[-5:],
        "sector_leader": cb.get("sector_leader", ""),
        "improvement_log": cb.get("improvement_log", [])[-5:],
    }

class JarvisHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass

    def handle(self):
        try:
            super().handle()
        except ConnectionResetError:
            pass

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        path = self.path.split('?')[0]

        if path == '/status':
            self.send_json(get_full_status())

        elif path == '/kalshi':
            self.send_json(get_kalshi_stats())

        elif path == '/patterns':
            self.send_json(get_pattern_stats())

        elif path == '/predictions':
            self.send_json(get_recent_predictions())

        elif path == '/btc':
            self.send_json(get_live_btc())

        elif path == '/bots':
            self.send_json(get_bot_status())

        elif path.startswith('/logs/'):
            bot = path.replace('/logs/', '')
            self.send_json({"bot": bot, "lines": get_log_tail(bot, 30)})

        elif path == '/brief':
            status = get_full_status()
            brief = generate_brief(status)
            self.send_json({"brief": brief, "ts": datetime.now().isoformat()})

        elif path == '/btc_memory':
            try:
                with open(FILES["btc_memory"]) as f:
                    self.send_json(json.load(f))
            except:
                self.send_json({"error": "not found"}, 404)

        elif path == '/ping':
            self.send_json({"alive": True, "ts": datetime.now().isoformat()})

        else:
            self.send_json({"error": "unknown endpoint"}, 404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        path = self.path.split('?')[0]

        if path == '/telegram':
            msg = body.get("msg", "")
            if msg:
                requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json={"chat_id": TG_CHAT, "text": msg[:4000]}, timeout=5)
            self.send_json({"ok": True})

        elif path == '/settings':
            # Update bot settings via API
            setting = body.get("key")
            value   = body.get("value")
            bot     = body.get("bot", "master")
            if setting and value is not None:
                # Write to a settings override file
                settings_file = f"/root/jarvis/jarvis_settings_{bot}.json"
                settings = load(settings_file) or {}
                settings[setting] = value
                settings["updated"] = datetime.now().isoformat()
                with open(settings_file, "w") as f:
                    json.dump(settings, f, indent=2)
                self.send_json({"ok": True, "key": setting, "value": value})
            else:
                self.send_json({"error": "missing key or value"}, 400)

        elif path == '/pause':
            cb = load(FILES["central_brain"])
            cb["risk_level"] = "EXTREME"
            cb["trading_paused"] = True
            with open(FILES["central_brain"], "w") as f:
                json.dump(cb, f, indent=2)
            self.send_json({"ok": True, "msg": "Trading paused"})

        elif path == '/resume':
            cb = load(FILES["central_brain"])
            cb["risk_level"] = "NORMAL"
            cb["trading_paused"] = False
            with open(FILES["central_brain"], "w") as f:
                json.dump(cb, f, indent=2)
            self.send_json({"ok": True, "msg": "Trading resumed"})

        else:
            self.send_json({"error": "unknown"}, 404)

def generate_brief(status):
    try:
        btc = status.get("btc", {})
        kalshi = status.get("kalshi", {})
        bots = status.get("bots", [])
        alive = sum(1 for b in bots if b.get("alive"))
        patterns = status.get("patterns", {})

        prompt = f"""You are Jarvis — sharp, direct AI trading assistant.
BTC: ${btc.get('price',0):,.0f} ({btc.get('chg24h',0):+.1f}%) RSI:{btc.get('rsi',50)} Signal:{btc.get('signal','neutral')}
Fear & Greed: {btc.get('fear_greed',50)} | Risk: {btc.get('risk','')} | 4H: {btc.get('trend_4h','')}
Kalshi: {kalshi.get('win_rate',0)}% WR | {kalshi.get('total_bets',0)} bets | ${kalshi.get('profit',0):+.0f} P&L
Bots: {alive}/{len(bots)} alive | Patterns: {patterns.get('total',0)} fingerprints
Give Lenny a 2-sentence sharp morning brief and one specific action. No markdown. Plain text only."""

        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": 150,
                  "messages": [{"role": "user", "content": prompt}]}, timeout=15)
        if r.status_code == 200:
            return r.json()["content"][0]["text"].strip()
    except Exception as e:
        print(f"Brief error: {e}")
    return "All systems online. Ready for your command."

if __name__ == "__main__":
    print(f"JARVIS API v2 starting on port {PORT}...")
    print(f"Live at http://68.183.107.46:{PORT}/status")
    server = HTTPServer(('0.0.0.0', PORT), JarvisHandler)
    server.serve_forever()
