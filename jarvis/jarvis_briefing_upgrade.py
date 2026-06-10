"""
jarvis_briefing_upgrade.py
Drop-in patch for the morning briefing section of jarvis_briefing.py.
Adds: P&L summary, Kalshi win rates, theta decay warnings, options open positions,
BTC prediction accuracy, day agenda.

HOW TO APPLY:
Replace the send_morning_briefing() function in jarvis_briefing.py with this version,
and add the import at the top:
    try:
        import jarvis_memory_db as memdb
        memdb.init_db()
        DB_ENABLED = True
    except:
        DB_ENABLED = False
"""

MORNING_BRIEFING_PATCH = '''
def send_morning_briefing():
    log.info("Sending morning briefing")
    
    # ── Core briefing (existing) ──────────────────────────────
    msg = brain.format_morning_briefing()
    insight = run_ai_insight()
    if insight:
        msg += f"\\n==========================\\n🧠 AI INSIGHT\\n{insight}"

    # ── JARVIS P&L Dashboard ──────────────────────────────────
    try:
        import jarvis_memory_db as memdb
        summary = memdb.get_morning_summary()
        pred    = summary["pred_stats"]
        
        pnl_lines = [
            "",
            "==========================",
            "💰 JARVIS P&L DASHBOARD",
            "==========================",
        ]
        
        # BTC Predictions accuracy
        if pred["total"] > 0:
            pnl_lines.append(
                f"📈 BTC Predictions: {pred['total']} graded | {pred['wr']}% WR"
            )
            pnl_lines.append(
                f"   YES: {pred['yes_wr']}% WR | NO: {pred['no_wr']}% WR"
            )
        
        # Kalshi P&L
        kalshi_stats = memdb.get_kalshi_stats()
        if kalshi_stats:
            k_pnl = summary["kalshi_pnl"]
            pnl_lines.append(f"🎯 Kalshi P&L: ${k_pnl:+.2f}")
            for bet_type, data in kalshi_stats.items():
                total_k = data["wins"] + data["losses"]
                if total_k > 0:
                    wr_k = round(data["wins"] / total_k * 100)
                    pnl_lines.append(f"   {bet_type}: {total_k} bets | {wr_k}% WR | ${data['pnl']:+.2f}")
        
        # Options P&L
        if summary["open_trades"] > 0 or summary["options_pnl"] != 0:
            pnl_lines.append(f"⚙️ Options: {summary['open_trades']} open | P&L ${summary['options_pnl']:+.2f}")
        
        # ⚠️ Theta warnings
        theta_warn = summary["theta_warnings"]
        if theta_warn:
            pnl_lines.append("⚠️ THETA WARNINGS — expiring soon:")
            for t in theta_warn[:3]:
                pnl_lines.append(
                    f"   {t['ticker']} {t['strategy']} ${t['strike']:.0f} — {t['dte']}d left"
                )
        
        pnl_lines.append("==========================")
        msg += "\\n".join(pnl_lines)

    except Exception as e:
        log.warning(f"P&L dashboard error: {e}")

    # ── Day agenda ────────────────────────────────────────────
    try:
        from datetime import datetime
        now = datetime.now()
        day_name = now.strftime("%A")
        agenda_lines = [
            "",
            "📅 TODAY'S AGENDA",
            "==========================",
            f"6:00 EDT — Pre-market brief",
            f"9:35 EDT — ORB levels",
            f"Hourly — BTC predictions",
            f"8:00 EDT — Options scan",
            "==========================",
        ]
        msg += "\\n".join(agenda_lines)
    except:
        pass

    tg(msg)
    brain.write_brain({"briefing_sent_date": datetime.now().strftime("%Y-%m-%d")})
    log.info("Morning briefing sent")
'''


# ── Standalone test ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Briefing patch ready.")
    print("Apply by replacing send_morning_briefing() in jarvis_briefing.py")
    print()
    print(MORNING_BRIEFING_PATCH)
