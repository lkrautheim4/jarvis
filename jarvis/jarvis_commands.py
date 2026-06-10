try:
    import jarvis_live_betting as jlb
    LIVE_BETTING = True
except:
    LIVE_BETTING = False

#!/usr/bin/env python3
"""
JARVIS COMMAND REGISTRY
All new commands go here. Never touch jarvis_master.py for commands.
"""
import json, requests, logging
log = logging.getLogger("JARVIS_COMMANDS")
HDRS = {"APCA-API-KEY-ID":"PKTHANGUNVFDSLLR3VXPETXRQF","APCA-API-SECRET-KEY":"GRTDDfkCGWbZMoNSWms6uJSGvw72rHaAk1N1fvLi8EAP"}

def load(f, d=None):
    try: return json.load(open(f))
    except: return d or {}

def cmd_premium(tg):
    try:
        data = load("/root/jarvis/jarvis_premium_brain.json")
        wins = data.get("wins",0); losses = data.get("losses",0)
        total_premium = data.get("total_premium_collected",0)
        total_pnl = data.get("total_pnl",0)
        positions = data.get("positions",[])
        out = ["💰 PREMIUM WHEEL STATUS","="*22,
               f"Premium collected: ${total_premium:+.0f}",
               f"Realized P&L: ${total_pnl:+.0f}",
               f"Wins: {wins} | Losses: {losses}","="*22,
               f"Open positions: {len(positions)}"]
        for p in positions[-3:]:
            out.append(f"  {p.get('strategy','')} {p.get('ticker','')} ${p.get('strike',0):.0f} = ${p.get('total_premium',0):.0f}")
        out.append("="*22)
        out.append("Wheel: Sell Put -> Assigned -> Sell Call -> Repeat")
        tg("\n".join(out))
    except Exception as e: tg(f"Premium error: {e}")

def cmd_macro(tg):
    try:
        macro = load("/root/jarvis/jarvis_macro.json")
        regime = macro.get("regime","?")
        conf = macro.get("regime_confidence",0)
        vix = macro.get("vix",{}).get("value",0)
        fg = macro.get("fear_greed",{}).get("current",0)
        y10 = macro.get("yield_10yr",{}).get("value",0)
        pcr = macro.get("put_call",{}).get("ratio",0)
        action = macro.get("beast_action","")
        focus = macro.get("focus_sectors","")
        size = macro.get("size_multiplier",1.0)
        corr = macro.get("correlations",{})
        emoji = {"RISK_ON":"🟢","RISK_OFF":"🔴","STAGFLATION":"🟡","RECOVERY":"🔵"}.get(regime,"⚪")
        out = [f"{emoji} MACRO REGIME: {regime} ({conf}%)","="*22,
               f"VIX: {vix:.1f} | Yield: {y10:.2f}%",
               f"Fear&Greed: {fg} | Put/Call: {pcr}",
               f"BTC: {corr.get('btc_1d',0):+.1f}% SPY: {corr.get('spy_1d',0):+.1f}%",
               "="*22, f"BEAST: {action}", f"Focus: {focus}", f"Size: {size}x"]
        for s in corr.get("signals",[])[:3]: out.append(f">> {s}")
        tg("\n".join(out))
    except Exception as e: tg(f"Macro error: {e}")

def cmd_earnings(tg):
    try:
        data = load("/root/jarvis/jarvis_earnings.json")
        upcoming = data.get("upcoming_week",[])
        critical = data.get("critical",[])
        high_risk = data.get("high_risk",[])
        out = ["📅 EARNINGS THIS WEEK"]
        if critical: out.append(f"🚨 CRITICAL: {', '.join(critical)}")
        if high_risk: out.append(f"⚠️ HIGH: {', '.join(high_risk)}")
        out.append("──────────────")
        for sym, info in upcoming[:8]:
            e = "🚨" if info["risk"]=="CRITICAL" else "⚠️" if info["risk"]=="HIGH" else "📅"
            out.append(f"{e} {sym}: {info['date']} ({info['days_away']}d)")
        out.append("Beast auto-avoids HIGH/CRITICAL")
        tg("\n".join(out))
    except Exception as e: tg(f"Earnings error: {e}")

def cmd_beast(tg):
    try:
        bb = load("/root/jarvis/jarvis_beast_brain.json")
        wins = bb.get("wins",0); losses = bb.get("losses",0)
        pnl = bb.get("total_pnl",0); scans = bb.get("total_scans",0)
        wr = round(wins/(wins+losses)*100,1) if wins+losses > 0 else 0
        macro = load("/root/jarvis/jarvis_macro.json")
        out = ["🦁 THE BEAST","="*20,
               f"Trades: {wins+losses} | WR: {wr}% | P&L: ${pnl:+.0f}",
               f"Scans: {scans}","="*20,
               f"Regime: {macro.get('regime','?')} {macro.get('size_multiplier',1)}x",
               f"Action: {macro.get('beast_action','')[:50]}"]
        if wins+losses == 0: out.append("No trades yet — opens Mon 9:30am")
        tg("\n".join(out))
    except Exception as e: tg(f"Beast error: {e}")

def cmd_brief(tg):
    try:
        import sys; sys.path.insert(0,'/root/jarvis')
        import jarvis_central_brain as jcb
        tg(jcb.format_morning_briefing())
    except Exception as e: tg(f"Brief error: {e}")

def cmd_capital(tg):
    try:
        kb = load("/root/jarvis/kalshi_brain.json")
        s = kb.get("stats",{}); total = s.get("total",0); wins = s.get("wins",0)
        wr = round(wins/total*100,1) if total > 0 else 0
        bb = load("/root/jarvis/jarvis_beast_brain.json")
        bw = bb.get("wins",0); bl = bb.get("losses",0)
        bwr = round(bw/(bw+bl)*100,1) if bw+bl > 0 else 0
        pm = load("/root/jarvis/jarvis_premium_brain.json")
        acct = requests.get("https://paper-api.alpaca.markets/v2/account",headers=HDRS,timeout=8).json()
        equity = float(acct.get("equity",0))
        out = ["💰 CAPITAL REPORT","="*22,
               f"Kalshi: ${s.get('profit',0):+.0f} | {wr}% WR | {total} bets",
               f"Beast:  ${bb.get('total_pnl',0):+.0f} | {bwr}% WR | {bw+bl} trades",
               f"Premium:${pm.get('total_premium_collected',0):+.0f} collected | {pm.get('wins',0)}W",
               "="*22, f"Alpaca Equity: ${equity:,.0f}",
               "="*22,"GO-LIVE CHECKLIST",
               f"Kalshi: {total}/200 {'✅' if total>=200 else '⏳'}",
               f"Kalshi WR: {wr}%/60% {'✅' if wr>=60 else '⏳'}",
               f"Beast: {bw+bl}/30 {'✅' if bw+bl>=30 else '⏳'}"]
        tg("\n".join(out))
    except Exception as e: tg(f"Capital error: {e}")

def cmd_intel(tg):
    try:
        intel = load("/root/jarvis/jarvis_intel.json")
        insider = intel.get("insider_alerts",[])
        hot = intel.get("hot_tickers",{})
        improve = intel.get("self_improve_log",[])
        hot_list = list(hot.keys())[:5] if isinstance(hot,dict) else []
        out = ["🔍 INTELLIGENCE","="*22,
               f"Insider alerts: {len(insider)}",
               f"Self-improve entries: {len(improve)}"]
        if hot_list: out.append(f"Hot tickers: {', '.join(hot_list)}")
        if improve: out.append(f"Last: {improve[-1].get('insight','')[:60]}")
        tg("\n".join(out))
    except Exception as e: tg(f"Intel error: {e}")

def cmd_insider(tg):
    try:
        intel = load("/root/jarvis/jarvis_intel.json")
        alerts = intel.get("insider_alerts",[])[-5:]
        if not alerts: tg("No insider alerts"); return
        out = [f"📋 INSIDER ({len(alerts)} recent)"]
        for a in alerts: out.append(f"  {a.get('summary','')[:70]}")
        tg("\n".join(out))
    except Exception as e: tg(f"Insider error: {e}")

def cmd_improve(tg):
    try:
        intel = load("/root/jarvis/jarvis_intel.json")
        entries = intel.get("self_improve_log",[])[-5:]
        if not entries: tg("No improvement logs yet"); return
        out = ["🧠 SELF-IMPROVEMENT"]
        for e in entries: out.append(f"  {e.get('ts','')[:10]}: {e.get('insight','')[:60]}")
        tg("\n".join(out))
    except Exception as e: tg(f"Improve error: {e}")

def cmd_report(tg):
    try:
        intel = load("/root/jarvis/jarvis_intel.json")
        reports = intel.get("weekly_reports",[])
        if not reports: tg("No weekly reports yet"); return
        r = reports[-1]
        out = ["📊 WEEKLY REPORT", r.get("date",""), "="*20, r.get("summary","")[:400]]
        tg("\n".join(out))
    except Exception as e: tg(f"Report error: {e}")



def cmd_options(tg):
    try:
        data = {}
        try: 
            import json
            data = json.load(open("/root/jarvis/jarvis_options_brain.json"))
        except: pass
        s = data.get("stats", {})
        total = s.get("total", 0)
        wins = s.get("wins", 0)
        losses = s.get("losses", 0)
        open_t = s.get("open", 0)
        pnl = s.get("total_pnl", 0)
        wr = round(wins/total*100) if total > 0 else 0
        trades = data.get("trades", [])
        recent = trades[-3:] if trades else []
        out = [
            "📊 OPTIONS BRAIN STATUS",
            "="*24,
            f"Total trades: {total} | Open: {open_t}",
            f"W:{wins} L:{losses} WR:{wr}%",
            f"Total P&L: ${pnl:+.0f}",
            "="*24,
            "Recent trades:"
        ]
        for t in reversed(recent):
            emoji = "✅" if t.get("result")=="WIN" else "❌" if t.get("result")=="LOSS" else "⏳"
            out.append(f"  {emoji} {t.get('ticker','')} {t.get('strategy','')} score:{t.get('score',0)}")
        by_strat = s.get("by_strategy", {})
        if by_strat:
            out.append("="*24)
            out.append("By strategy:")
            for strat, d in by_strat.items():
                swr = round(d["wins"]/d["total"]*100) if d["total"] > 0 else 0
                out.append(f"  {strat}: {swr}% WR ({d['total']} trades)")
        tg("\n".join(out))
    except Exception as e: tg(f"Options error: {e}")
def cmd_learn(tg, ticker=None):
    try:
        import sys; sys.path.insert(0,'/root/jarvis')
        from jarvis_options_coach import analyze_setup, load_all_context, get_stock_price
        if not ticker:
            tg("Usage: LEARN SOFI or LEARN NVDA or LEARN SPY\nAvailable: SOFI PLTR F BAC RIVN NVDA AMD COIN TSLA SPY")
            return
        ticker = ticker.upper()
        tg(f"Analyzing {ticker} options setup... 15 seconds")
        price = get_stock_price(ticker)
        if not price:
            tg(f"Could not get price for {ticker}")
            return
        ctx = load_all_context()
        analysis = analyze_setup(ticker, price, "both", ctx)
        if analysis:
            tg(f"🎓 OPTIONS ANALYSIS: {ticker} @ ${price:.2f}\n{'='*24}\n{analysis}")
        else:
            tg(f"Analysis failed for {ticker} — try again")
    except Exception as e: tg(f"Learn error: {e}")

def cmd_greeks(tg):
    tg("📚 THE GREEKS\n"
       "========================\n"
       "DELTA — direction\n"
       "  Buy call: +delta (profit when stock rises)\n"
       "  Buy put: -delta (profit when stock falls)\n"
       "  ATM = ~0.50 delta\n"
       "========================\n"
       "THETA — time decay\n"
       "  Negative when buying (enemy)\n"
       "  Positive when selling (friend)\n"
       "  Options lose value daily\n"
       "========================\n"
       "IV — Implied Volatility\n"
       "  High IV = expensive options = SELL\n"
       "  Low IV = cheap options = BUY\n"
       "  Check before every trade\n"
       "========================\n"
       "VEGA — IV sensitivity\n"
       "  Buy before IV rises (earnings)\n"
       "  Sell before IV falls (post earnings)\n"
       "========================\n"
       "Rule: Never buy high IV. Never sell low IV.")

def cmd_wheel(tg):
    tg("🎡 THE WHEEL STRATEGY\n"
       "========================\n"
       "STEP 1: Pick stock you want to own\n"
       "STEP 2: Sell PUT below current price\n"
       "  - Collect premium immediately\n"
       "  - Need cash to buy 100 shares\n"
       "========================\n"
       "OUTCOME A: Stock stays above strike\n"
       "  - Keep premium, repeat next month\n"
       "  - Target: 2-4% monthly\n"
       "========================\n"
       "OUTCOME B: Stock drops below strike\n"
       "  - You buy 100 shares at strike\n"
       "  - Your cost = strike - premium\n"
       "========================\n"
       "STEP 3: Now own stock\n"
       "STEP 4: Sell COVERED CALL above cost\n"
       "STEP 5: Collect more premium\n"
       "STEP 6: Repeat forever\n"
       "========================\n"
       "Best stocks: SOFI PLTR F BAC RIVN\n"
       "Best DTE: 21 days\n"
       "Best IV: above 30%")
def cmd_congress(tg):
    try:
        data = load("/root/jarvis/jarvis_congress.json")
        hot = data.get("hot_tickers",{})
        valid = [(tk,d) for tk,d in hot.items() if tk != "N/A"][:8]
        out = ["🏛 CONGRESS TRADES","="*22]
        for tk, d in valid:
            pols = ", ".join(d.get("politicians",[])[:2])
            out.append(f"  {tk} — {d.get('count',0)}x: {pols}")
        tg("\n".join(out))
    except Exception as e: tg(f"Congress error: {e}")

COMMANDS = {
    "MACRO":"cmd_macro", "REGIME":"cmd_macro",
    "EARNINGS":"cmd_earnings", "EARN":"cmd_earnings",
    "BEAST":"cmd_beast", "SCANNER":"cmd_beast",
    "BRIEF":"cmd_brief", "BRIEFING":"cmd_brief",
    "CAPITAL":"cmd_capital", "CAP":"cmd_capital",
    "INTEL":"cmd_intel", "INTELLIGENCE":"cmd_intel",
    "INSIDER":"cmd_insider",
    "IMPROVE":"cmd_improve",
    "REPORT":"cmd_report", "WEEKLY":"cmd_report",
    "CONGRESS":"cmd_congress",
    "PREMIUM":"cmd_premium", "WHEEL":"cmd_premium",
}

def handle(text, tg_func):
    parts = text.strip().upper().split()
    cmd = parts[0]

    if cmd == "LEARN":
        ticker = parts[1] if len(parts) > 1 else None
        cmd_learn(tg_func, ticker)
        return True

    # ── Live betting commands ────────────────────────────────
    if LIVE_BETTING:
        if cmd == "GO":
            jlb.confirm_bet(tg_func)
            return True
        if cmd == "RESUME":
            jlb.resume_betting(tg_func)
            return True
        if cmd in ("BETSTAT", "BANKROLL", "BETS"):
            jlb.betting_status(tg_func)
            return True
        if cmd == "WIN" and len(parts) >= 2:
            try:
                pnl = float(parts[1].replace("$","").replace(",",""))
                jlb.record_result(won=True, pnl=pnl, tg_func=tg_func)
            except:
                tg_func("Usage: WIN <profit_amount>")
            return True
        if cmd == "LOSS" and len(parts) >= 2:
            try:
                pnl = -abs(float(parts[1].replace("$","").replace(",","")))
                jlb.record_result(won=False, pnl=pnl, tg_func=tg_func)
            except:
                tg_func("Usage: LOSS <amount_lost>")
            return True
    # ────────────────────────────────────────────────────────

    if cmd in COMMANDS:
        globals()[COMMANDS[cmd]](tg_func)
        return True
    return False
