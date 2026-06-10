import jarvis_brain as _jb_hb
#!/usr/bin/env python3
"""
JARVIS EARNINGS ENGINE
Tracks upcoming earnings for all Beast/Options watchlist tickers.
Protects positions before earnings — prevents IV crush and gap losses.
Runs every 6 hours. Feeds into Beast, Options, and central brain.
"""
import requests, json, os, time, logging, csv, io
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("JARVIS_EARNINGS")

EARNINGS_FILE = "/root/jarvis/jarvis_earnings.json"
BRAIN_FILE    = "/root/jarvis/jarvis_central_brain.json"
TG_TOKEN      = __import__("jarvis_secrets").TG_TOKEN_TRADER
CHAT_ID       = "7534553840"
INTERVAL      = 21600  # 6 hours

# All tickers we care about across all bots
WATCHLIST = [
    "AAPL","MSFT","NVDA","META","GOOGL","AMZN","AMD","CRM",
    "JPM","GS","V","MA","UNH","JNJ","PFE","ABBV",
    "XOM","CVX","TSLA","PLTR","SOFI","COIN","MSTR",
    "SPY","QQQ","IWM","XLF","XLE","XLV","XLK",
    "PANW","DG","ULTA","HD","T","MEDP","PH"
]

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

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

def get_nasdaq_earnings(date_str=None):
    """Get earnings from Nasdaq API for a specific date"""
    earnings = []
    dates_to_check = []

    # Check next 14 days
    for i in range(14):
        d = datetime.now() + timedelta(days=i)
        # Skip weekends
        if d.weekday() < 5:
            dates_to_check.append(d.strftime("%Y-%m-%d"))

    for date in dates_to_check:
        try:
            r = SESSION.get("https://api.nasdaq.com/api/calendar/earnings",
                params={"date": date},
                headers={"Accept": "application/json,text/plain"},
                timeout=8)
            if r.status_code == 200:
                rows = r.json().get("data", {}).get("rows", [])
                for row in rows:
                    sym = row.get("symbol", "")
                    if sym in WATCHLIST:
                        earnings.append({
                            "symbol": sym,
                            "name": row.get("name", ""),
                            "date": date,
                            "time": row.get("time", ""),
                            "eps_estimate": row.get("epsForecast", ""),
                            "last_eps": row.get("lastYearEPS", ""),
                            "market_cap": row.get("marketCap", ""),
                            "days_away": i
                        })  # Rate limit
        except Exception as e:
            log.debug(f"Nasdaq earnings {date}: {e}")

    return earnings

def get_alpha_vantage_earnings():
    """Get 3-month earnings calendar from Alpha Vantage"""
    earnings = []
    try:
        r = SESSION.get("https://www.alphavantage.co/query",
            params={"function": "EARNINGS_CALENDAR", "horizon": "3month", "apikey": "demo"},
            timeout=10)
        if r.status_code == 200:
            reader = csv.DictReader(io.StringIO(r.text))
            for row in reader:
                sym = row.get("symbol", "")
                if sym in WATCHLIST:
                    report_date = row.get("reportDate", "")
                    try:
                        days = (datetime.strptime(report_date, "%Y-%m-%d") - datetime.now()).days
                        earnings.append({
                            "symbol": sym,
                            "name": row.get("name", ""),
                            "date": report_date,
                            "time": row.get("timeOfTheDay", ""),
                            "eps_estimate": row.get("estimate", ""),
                            "days_away": days
                        })
                    except: pass
    except Exception as e:
        log.warning(f"Alpha Vantage: {e}")
    return earnings

def merge_earnings(nasdaq, alpha):
    """Merge and deduplicate earnings from both sources"""
    merged = {}
    for e in nasdaq + alpha:
        sym = e["symbol"]
        days = e.get("days_away", 999)
        if sym not in merged or days < merged[sym].get("days_away", 999):
            merged[sym] = e
    return list(merged.values())

def classify_risk(earnings):
    """Classify earnings risk for each position"""
    risk_map = {}
    for e in earnings:
        sym = e["symbol"]
        days = e.get("days_away", 999)
        time_of_day = e.get("time", "")

        # Risk level
        if days <= 1:
            risk = "CRITICAL"  # Earnings today or tomorrow — close position
            action = "CLOSE POSITION NOW — earnings imminent"
        elif days <= 3:
            risk = "HIGH"
            action = "REDUCE or HEDGE — earnings in 3 days"
        elif days <= 7:
            risk = "MEDIUM"
            action = "MONITOR — earnings this week"
        else:
            risk = "LOW"
            action = "OK to hold"

        # After-hours is more dangerous (gaps next day)
        if "after" in time_of_day.lower() and days <= 2:
            risk = "CRITICAL"
            action = "CLOSE TODAY — after-hours earnings gap risk"

        risk_map[sym] = {
            "risk": risk,
            "days_away": days,
            "date": e.get("date", ""),
            "time": time_of_day,
            "eps_estimate": e.get("eps_estimate", ""),
            "action": action
        }
    return risk_map

def check_open_positions(risk_map):
    """Check if any open Alpaca positions have earnings risk"""
    alerts = []
    try:
        import requests as req
        hdrs = {"APCA-API-KEY-ID":"PKTHANGUNVFDSLLR3VXPETXRQF",
                "APCA-API-SECRET-KEY":"GRTDDfkCGWbZMoNSWms6uJSGvw72rHaAk1N1fvLi8EAP"}
        pos = req.get("https://paper-api.alpaca.markets/v2/positions",
            headers=hdrs, timeout=8).json()

        for p in pos:
            sym = p.get("symbol", "")
            # Skip crypto and options
            if "USD" in sym or len(sym) > 6: continue

            if sym in risk_map:
                risk_info = risk_map[sym]
                if risk_info["risk"] in ["CRITICAL", "HIGH"]:
                    pnl = float(p.get("unrealized_pl", 0))
                    alerts.append({
                        "symbol": sym,
                        "risk": risk_info["risk"],
                        "days_away": risk_info["days_away"],
                        "date": risk_info["date"],
                        "action": risk_info["action"],
                        "pnl": pnl
                    })
    except Exception as e:
        log.warning(f"Position check: {e}")
    return alerts

def format_earnings_alert(alerts):
    """Format Telegram alert for earnings risk"""
    if not alerts: return None
    lines = ["⚠️ EARNINGS RISK ALERT"]
    for a in alerts:
        emoji = "🚨" if a["risk"] == "CRITICAL" else "⚠️"
        lines.append(f"{emoji} {a['symbol']} — {a['risk']}")
        lines.append(f"  Earnings: {a['date']} ({a['days_away']}d away)")
        lines.append(f"  P&L: ${a['pnl']:+.0f}")
        lines.append(f"  Action: {a['action']}")
    return "\n".join(lines)

def get_upcoming_watchlist_earnings(risk_map, days=7):
    """Get all upcoming earnings for watchlist in next N days"""
    upcoming = [(sym, info) for sym, info in risk_map.items()
                if info["days_away"] <= days]
    upcoming.sort(key=lambda x: x[1]["days_away"])
    return upcoming

def run_cycle():
    log.info("Scanning earnings calendar...")

    # Fetch from both sources
    nasdaq = get_nasdaq_earnings()
    alpha  = get_alpha_vantage_earnings()
    log.info(f"Nasdaq: {len(nasdaq)} | Alpha Vantage: {len(alpha)}")

    # Merge
    all_earnings = merge_earnings(nasdaq, alpha)
    log.info(f"Total unique earnings in watchlist: {len(all_earnings)}")

    # Classify risk
    risk_map = classify_risk(all_earnings)

    # Check open positions
    alerts = check_open_positions(risk_map)
    if alerts:
        msg = format_earnings_alert(alerts)
        if msg:
            tg(msg)
            log.info(f"Sent {len(alerts)} earnings alerts")

    # Get upcoming this week
    upcoming = get_upcoming_watchlist_earnings(risk_map, days=7)

    # Save to file
    earnings_data = {
        "ts": datetime.now().isoformat(),
        "earnings": all_earnings,
        "risk_map": risk_map,
        "upcoming_week": [(s, i) for s,i in upcoming],
        "critical": [s for s,i in risk_map.items() if i["risk"] == "CRITICAL"],
        "high_risk": [s for s,i in risk_map.items() if i["risk"] == "HIGH"],
    }
    save(EARNINGS_FILE, earnings_data)

    # Update central brain
    cb = load(BRAIN_FILE)
    cb["earnings_blacklist"] = list(risk_map.keys())
    cb["earnings_critical"]  = earnings_data["critical"]
    cb["earnings_high_risk"] = earnings_data["high_risk"]
    cb["earnings_updated"]   = datetime.now().isoformat()
    save(BRAIN_FILE, cb)

    # Log upcoming
    if upcoming:
        log.info("Upcoming earnings this week:")
        for sym, info in upcoming[:10]:
            log.info(f"  {sym}: {info['date']} ({info['days_away']}d) [{info['risk']}]")

    return earnings_data

def main():
    log.info("JARVIS EARNINGS ENGINE ONLINE")
    tg("📅 EARNINGS ENGINE ONLINE\nTracking earnings for 37 tickers\nAlerts on earnings risk for open positions")

    if True:  # run once
        try:
            run_cycle()
        except Exception as e:
            log.error(f"Earnings cycle: {e}")
        time.sleep(INTERVAL)

if __name__ == "__main__":
    # Run once and exit (scheduled by cron)
    try:
        main()
    except Exception as e:
        import logging
        logging.getLogger().error(f"Fatal: {e}")
    raise SystemExit(0)
