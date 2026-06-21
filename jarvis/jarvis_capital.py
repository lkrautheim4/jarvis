import jarvis_brain as _jb_hb
#!/usr/bin/env python3
"""
JARVIS CAPITAL ORCHESTRATOR
Tracks P&L across all systems and auto-allocates capital
to highest performing strategy.
"""
import json, os, requests, time, logging
from datetime import datetime
from jarvis_allocation import (
    StrategyStats,
    recommend_allocation  as _ja_recommend,
    pick_best_performer   as _ja_best,
)

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
        import sqlite3
        _c = sqlite3.connect("/root/jarvis/jarvis_memory.db", timeout=10)
        _r = _c.execute("SELECT COUNT(*) n, SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) w, COALESCE(SUM(pnl),0) p FROM kalshi_bets WHERE source='auto' AND result IS NOT NULL").fetchone()
        _c.close()
        total = _r[0] or 0; wins = _r[1] or 0; profit = _r[2] or 0
        pnl["kalshi"] = {
            "pnl": round(profit, 2),
            "wr": round(wins/total*100, 1) if total > 0 else 0,
            "trades": total,
            "status": "PROVEN" if total >= 50 and (wins/total if total else 0) >= 0.55 else "LEARNING"
        }
    except Exception as _e: pnl["kalshi"] = {"pnl": 0, "wr": 0, "trades": 0, "status": "ERROR"}

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

    # Options — live from options_trades DB (PAPER TRADES ONLY — not real Webull fills)
    try:
        _oc = sqlite3.connect("/root/jarvis/jarvis_memory.db", timeout=10)
        _or = _oc.execute("""
            SELECT
                SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END)  AS wins,
                SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) AS losses,
                COALESCE(SUM(pnl), 0)                          AS realized_pnl,
                COUNT(*)                                       AS total
            FROM options_trades
            WHERE status = 'closed'
              AND result IN ('WIN', 'LOSS')
              AND pnl IS NOT NULL
        """).fetchone()
        _open_count = _oc.execute(
            "SELECT COUNT(*) FROM options_trades WHERE status IN ('paper','open')"
        ).fetchone()[0]
        _oc.close()
        wins = _or[0] or 0; losses = _or[1] or 0
        total = wins + losses
        pnl["options"] = {
            "pnl": round(_or[2], 2),
            "wr": round(wins/total*100, 1) if total > 0 else 0,
            "trades": total,
            "open": _open_count,
            "status": "PAPER/LEARNING" if total < 10 or (wins/total if total else 0) < 0.55 else "PAPER/PROVEN"
        }
    except: pnl["options"] = {"pnl": 0, "wr": 0, "trades": 0, "open": 0, "status": "PAPER/LEARNING"}

    # Alpaca account
    try:
        hdrs = {"APCA-API-KEY-ID": __import__("jarvis_secrets").ALPACA_PAPER_KEY,
                "APCA-API-SECRET-KEY": __import__("jarvis_secrets").ALPACA_PAPER_SECRET}
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

def _build_options_strategy_stats():
    """One StrategyStats per options strategy, real fills only (is_real=1)."""
    import sqlite3
    _c = sqlite3.connect("/root/jarvis/jarvis_memory.db", timeout=10)
    _c.row_factory = sqlite3.Row
    rows = _c.execute("""
        SELECT strategy,
               SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) AS wins,
               COUNT(*)                                       AS trades,
               COALESCE(SUM(pnl), 0)                         AS total_pnl
        FROM options_trades
        WHERE status  = 'closed'
          AND result  IN ('WIN', 'LOSS')
          AND pnl     IS NOT NULL
          AND is_real = 1
        GROUP BY strategy
    """).fetchall()
    _c.close()
    return [
        StrategyStats(
            name          = r["strategy"],
            trades        = r["trades"],
            wins          = r["wins"],
            total_pnl     = r["total_pnl"],
            target_trades = 30,
            target_wr     = 0.55,
        )
        for r in rows
    ]


def recommend_allocation(pnl_data):
    """
    Allocation gate via jarvis_allocation: 30-trade, per-strategy, real-only.
    Paper P&L in pnl_data is kept for display only — separate from the gate.
    """
    # Kalshi StrategyStats (source='auto' rows, already real-bet sourced)
    kd = pnl_data.get("kalshi", {})
    kalshi_stat = StrategyStats(
        name          = "kalshi",
        trades        = kd.get("trades", 0),
        wins          = round(kd.get("trades", 0) * kd.get("wr", 0) / 100),
        total_pnl     = kd.get("pnl", 0),
        target_trades = 200,
        target_wr     = 0.60,
    )

    # Beast StrategyStats
    bd = pnl_data.get("beast", {})
    beast_stat = StrategyStats(
        name          = "beast",
        trades        = bd.get("trades", 0),
        wins          = round(bd.get("trades", 0) * bd.get("wr", 0) / 100),
        total_pnl     = bd.get("pnl", 0),
        target_trades = 30,
        target_wr     = 0.60,
    )

    # Options: one StrategyStats per strategy, real-only
    options_stats = _build_options_strategy_stats()

    all_stats = [kalshi_stat, beast_stat] + options_stats
    result    = _ja_recommend(all_stats)

    # Collapse all options-strategy allocations into one "options" key
    # so format_capital_report and run_cycle callers stay backward-compatible.
    raw_alloc  = result["allocation"]
    options_pct = sum(
        v for k, v in raw_alloc.items()
        if k not in ("kalshi", "beast")
    )
    return {
        "kalshi":      raw_alloc.get("kalshi", 0.0),
        "beast":       raw_alloc.get("beast",  0.0),
        "options":     round(options_pct, 1),
        "deployable":  result["deployable"],
        "reason":      result["reason"],
        "status":      result["status"],
    }

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
        f"Options(PAPER):${options.get('pnl',0):+.0f} | {options.get('wr',0)}% WR | {options.get('trades',0)} closed+{options.get('open',0)} open | {options.get('status','')}",
        "="*22,
        f"Alpaca Equity: ${alpaca.get('equity',0):,.0f} ({alpaca.get('pct',0):+.1f}%)",
        f"Total P&L: ${total_pnl:+.0f}",
        "="*22,
        "📊 RECOMMENDED ALLOCATION",
        allocations.get("reason", ""),
        f"Kalshi: {allocations.get('kalshi',0)}%",
        f"Beast:  {allocations.get('beast',0)}%",
        f"Options(real):{allocations.get('options',0)}%",
        "="*22,
    ]

    # Best performing (paper P&L comparison — informational only)
    best = max(["kalshi","beast","options"], key=lambda x: pnl_data.get(x,{}).get("pnl",0))
    lines.append(f"🏆 Best P&L (paper): {best.upper()}")

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
    opt_status = allocations.get("status", {})
    for strat, st in sorted(opt_status.items()):
        if strat in ("kalshi", "beast"):
            continue
        lines.append(f"Opts/{strat}: {st} ⏳")

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
