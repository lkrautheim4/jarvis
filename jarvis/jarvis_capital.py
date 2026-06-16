import jarvis_brain as _jb_hb
#!/usr/bin/env python3
"""
JARVIS CAPITAL ORCHESTRATOR
Tracks P&L across all systems and auto-allocates capital
to highest performing strategy.
"""
import json, os, requests, time, logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("JARVIS_CAPITAL")

TG_TOKEN = __import__("jarvis_secrets").TG_TOKEN_TRADER
CHAT_ID  = "7534553840"
CAPITAL_FILE = "/root/jarvis/jarvis_capital.json"
INTERVAL = 3600  # hourly

def tg(msg):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg}, timeout=10)
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def load(f, d=None):
    try: return json.load(open(f))
    except: return d or {}

def save(f, data):
    with open(f, 'w') as fp: json.dump(data, fp, indent=2)

def get_all_pnl():
    """Get P&L from every system"""
    pnl = {}

    # Kalshi
    try:
        kb = load("/root/jarvis/kalshi_brain.json")
        s = kb.get("stats", {})
        total = s.get("total", 0); wins = s.get("wins", 0)
        pnl["kalshi"] = {
            "pnl": round(s.get("profit", 0), 2),
            "wr": round(wins/total*100, 1) if total > 0 else 0,
            "trades": total,
            "status": "PROVEN" if total >= 50 and wins/total >= 0.55 else "LEARNING"
        }
    except: pnl["kalshi"] = {"pnl": 0, "wr": 0, "trades": 0, "status": "ERROR"}

    # Beast stocks
    try:
        bb = load("/root/jarvis/jarvis_beast_brain.json")
        wins = bb.get("wins", 0); losses = bb.get("losses", 0)
        total = wins + losses
        pnl["beast"] = {
            "pnl": round(bb.get("total_pnl", 0), 2),
            "wr": round(wins/total*100, 1) if total > 0 else 0,
            "trades": total,
            "status": "PROVEN" if total >= 20 and wins/total >= 0.60 else "LEARNING"
        }
    except: pnl["beast"] = {"pnl": 0, "wr": 0, "trades": 0, "status": "LEARNING"}

    # Options
    try:
        om = load("/root/jarvis/options_memory.json")
        s = om.get("stats", {})
        wins = s.get("winners", 0); losses = s.get("losers", 0)
        total = wins + losses
        pnl["options"] = {
            "pnl": round(s.get("total_pnl", 0), 2),
            "wr": round(wins/total*100, 1) if total > 0 else 0,
            "trades": total,
            "status": "PROVEN" if total >= 10 and wins/total >= 0.55 else "LEARNING"
        }
    except: pnl["options"] = {"pnl": 0, "wr": 0, "trades": 0, "status": "LEARNING"}

    # Alpaca account
    try:
        hdrs = {"APCA-API-KEY-ID":"PKTHANGUNVFDSLLR3VXPETXRQF",
                "APCA-API-SECRET-KEY":"GRTDDfkCGWbZMoNSWms6uJSGvw72rHaAk1N1fvLi8EAP"}
        acct = requests.get("https://paper-api.alpaca.markets/v2/account",
            headers=hdrs, timeout=8).json()
        equity = float(acct.get("equity", 0))
        start = 100000  # paper starting equity
        pnl["alpaca_total"] = {
            "equity": equity,
            "pnl": round(equity - start, 2),
            "pct": round((equity - start)/start*100, 2)
        }
    except: pnl["alpaca_total"] = {"equity": 0, "pnl": 0, "pct": 0}

    return pnl

def recommend_allocation(pnl_data):
    """
    Recommend capital allocation based on win rates.
    Higher WR = more capital.
    """
    systems = {
        "kalshi": pnl_data.get("kalshi", {}),
        "beast": pnl_data.get("beast", {}),
        "options": pnl_data.get("options", {})
    }

    allocations = {}
    total_score = 0

    for sys_name, data in systems.items():
        wr = data.get("wr", 0)
        trades = data.get("trades", 0)
        status = data.get("status", "LEARNING")

        # Score based on WR and sample size
        if status == "PROVEN" and wr >= 60:
            score = wr * 1.5  # proven edge gets bonus
        elif status == "PROVEN":
            score = wr
        elif trades >= 10:
            score = wr * 0.7  # learning gets penalty
        else:
            score = 10  # minimum allocation while learning

        allocations[sys_name] = score
        total_score += score

    # Convert to percentages
    if total_score > 0:
        for sys_name in allocations:
            allocations[sys_name] = round(allocations[sys_name]/total_score*100, 1)

    return allocations

def format_capital_report(pnl_data, allocations):
    """Format capital report for Telegram"""
    kalshi = pnl_data.get("kalshi", {})
    beast = pnl_data.get("beast", {})
    options = pnl_data.get("options", {})
    alpaca = pnl_data.get("alpaca_total", {})

    total_pnl = kalshi.get("pnl", 0) + beast.get("pnl", 0) + options.get("pnl", 0)

    lines = [
        "💰 CAPITAL REPORT",
        "="*22,
        f"Kalshi: ${kalshi.get('pnl',0):+.0f} | {kalshi.get('wr',0)}% WR | {kalshi.get('trades',0)} trades | {kalshi.get('status','')}",
        f"Beast:  ${beast.get('pnl',0):+.0f} | {beast.get('wr',0)}% WR | {beast.get('trades',0)} trades | {beast.get('status','')}",
        f"Options:${options.get('pnl',0):+.0f} | {options.get('wr',0)}% WR | {options.get('trades',0)} trades | {options.get('status','')}",
        "="*22,
        f"Alpaca Equity: ${alpaca.get('equity',0):,.0f} ({alpaca.get('pct',0):+.1f}%)",
        f"Total P&L: ${total_pnl:+.0f}",
        "="*22,
        "📊 RECOMMENDED ALLOCATION",
        f"Kalshi: {allocations.get('kalshi',0)}%",
        f"Beast:  {allocations.get('beast',0)}%",
        f"Options:{allocations.get('options',0)}%",
        "="*22,
    ]

    # Best performing
    best = max(["kalshi","beast","options"], key=lambda x: pnl_data.get(x,{}).get("pnl",0))
    lines.append(f"🏆 Best performer: {best.upper()}")

    # Go-live checklist
    kalshi_trades = kalshi.get("trades", 0)
    kalshi_wr = kalshi.get("wr", 0)
    lines.append("="*22)
    lines.append("🎯 GO-LIVE CHECKLIST")
    lines.append(f"Kalshi: {kalshi_trades}/200 bets {'✅' if kalshi_trades>=200 else '⏳'}")
    lines.append(f"Kalshi WR: {kalshi_wr}%/60% {'✅' if kalshi_wr>=60 else '⏳'}")
    beast_trades = beast.get("trades", 0)
    beast_wr = beast.get("wr", 0)
    lines.append(f"Beast: {beast_trades}/30 trades {'✅' if beast_trades>=30 else '⏳'}")
    lines.append(f"Beast WR: {beast_wr}%/60% {'✅' if beast_wr>=60 else '⏳'}")

    return "\n".join(lines)

def run_cycle():
    pnl_data = get_all_pnl()
    allocations = recommend_allocation(pnl_data)

    # Save
    capital = {
        "ts": datetime.now().isoformat(),
        "pnl": pnl_data,
        "allocations": allocations,
        "total_pnl": sum(pnl_data.get(s,{}).get("pnl",0) for s in ["kalshi","beast","options"])
    }
    save(CAPITAL_FILE, capital)

    # Update central brain
    cb = load("/root/jarvis/jarvis_central_brain.json")
    cb["capital_allocations"] = allocations
    cb["total_system_pnl"] = capital["total_pnl"]
    save("/root/jarvis/jarvis_central_brain.json", cb)

    log.info(f"Capital report: Kalshi ${pnl_data['kalshi']['pnl']:+.0f} "
             f"Beast ${pnl_data['beast']['pnl']:+.0f} "
             f"Total ${capital['total_pnl']:+.0f}")

    msg = format_capital_report(pnl_data, allocations)
    tg(msg)
    return pnl_data, allocations

def main():
    log.info("JARVIS CAPITAL ORCHESTRATOR ONLINE")
    if True:  # run once
        try:
            run_cycle()
        except Exception as e:
            log.error(f"Capital cycle: {e}")

if __name__ == "__main__":
    # Run once and exit (scheduled by cron)
    try:
        main()
    except Exception as e:
        import logging
        logging.getLogger().error(f"Fatal: {e}")
    raise SystemExit(0)
