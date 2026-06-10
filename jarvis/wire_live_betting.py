#!/usr/bin/env python3
"""
wire_live_betting.py — Patch jarvis_master.py and jarvis_commands.py
to use jarvis_live_betting.py engine.

Run once: python3 /root/jarvis/wire_live_betting.py
"""
import os

JARVIS = "/root/jarvis"

# ── Patch jarvis_master.py ───────────────────────────────────────────────────

def patch_master():
    path = f"{JARVIS}/jarvis_master.py"
    with open(path) as f:
        src = f.read()

    # 1. Add import
    if "jarvis_live_betting" not in src:
        src = src.replace(
            "from datetime import datetime, timedelta",
            "from datetime import datetime, timedelta\ntry:\n    import jarvis_live_betting as jlb\n    LIVE_BETTING = True\nexcept Exception as _lbe:\n    LIVE_BETTING = False\n    print(f'Live betting not loaded: {_lbe}')"
        )
        print("✅ Added live betting import")

    # 2. Replace the old inline bet block with live betting engine call
    old_block = '''    # ── LIVE BET PLACEMENT ──────────────────────────────────
    if bet in ["YES", "NO"]:
        try:
            prob_num = float(prob.replace("%","")) / 100
            # Confidence floors — hard rules
            if bet == "YES" and prob_num < 0.65:
                log.info(f"Live bet blocked: YES {prob_num:.0%} < 65% floor")
            elif bet == "NO" and prob_num < 0.80:
                log.info(f"Live bet blocked: NO {prob_num:.0%} < 80% floor")
            else:
                # EV check
                yes_price = best.get("yes", 0.5)
                if bet == "YES":
                    ev = (prob_num * (1 - yes_price)) - ((1 - prob_num) * yes_price)
                else:
                    ev = ((1 - prob_num) * yes_price) - (prob_num * (1 - yes_price))

                if ev < 0.05:
                    log.info(f"Live bet blocked: EV {ev:.3f} < 5% minimum")
                else:
                    # Loss streak guard
                    kb = {}
                    try:
                        import json as _j
                        kb = _j.load(open("/root/jarvis/kalshi_brain.json"))
                    except: pass
                    consec_losses = kb.get("consecutive_losses", 0)
                    if consec_losses >= 3:
                        log.info(f"Live bet blocked: {consec_losses} consecutive losses — halted")
                        tg(f"🛑 ORACLE HALTED\\n{consec_losses} consecutive losses\\nManual review required", TG_PRED)
                    else:
                        # Get live bankroll
                        try:
                            balance = kalshi_auth.get_balance()
                            bankroll = balance if balance > 50 else 500
                        except:
                            bankroll = 500
                        bet_size = kelly_bet_size(
                            float(prob.replace("%","")),
                            yes_price,
                            bankroll=bankroll
                        )
                        bet_size = min(bet_size, 25)  # hard cap $25 during live test
                        bet_size = max(bet_size, 5)   # min $5

                        # CONFIRMATION MODE — alert and wait for manual approval
                        LIVE_MODE = False  # flip to True when ready to go fully autonomous
                        if LIVE_MODE:
                            result = kalshi_auth.place_bet(best["ticker"], bet, bet_size)
                            if result:
                                tg(f"✅ BET PLACED\\n{bet} ${bet_size:.0f} on ${target:,.0f}\\nEV:{ev:+.1%} | Kelly:${bet_size:.0f}\\n{reason}", TG_PRED)
                                log.info(f"LIVE BET: {bet} ${bet_size:.0f} ticker={best['ticker']}")
                            else:
                                log.error("Bet placement failed")
                        else:
                            # MANUAL CONFIRMATION MODE
                            tg(f"🔔 CONFIRM BET?\\n{bet} ${bet_size:.0f} on BTC>${target:,.0f}\\nEV:{ev:+.1%} | Bankroll:${bankroll:.0f}\\nReply: BET YES or BET NO to confirm", TG_PRED)
                            log.info(f"Awaiting confirmation: {bet} ${bet_size:.0f} ticker={best['ticker']}")
        except Exception as _be:
            log.error(f"Bet placement error: {_be}")
    # ────────────────────────────────────────────────────────'''

    new_block = '''    # ── LIVE BET PLACEMENT (jarvis_live_betting engine) ────
    if LIVE_BETTING and bet in ["YES", "NO", "SKIP"]:
        try:
            jlb.evaluate_and_bet(
                bet=bet,
                prob_str=prob,
                target=target,
                yes_price=best.get("yes", 0.5),
                market_ticker=best.get("ticker", ""),
                reason=reason,
                regime=cb.get("regime", "UNKNOWN"),
                tg_func=lambda m: tg(m, TG_PRED),
            )
        except Exception as _be:
            log.error(f"Live betting error: {_be}")
    # ────────────────────────────────────────────────────────'''

    if old_block in src:
        src = src.replace(old_block, new_block)
        print("✅ Replaced inline bet block with engine call")
    elif "jarvis_live_betting" in src and "jlb.evaluate_and_bet" in src:
        print("✅ Engine already wired in master")
    else:
        # Fallback — inject after tg(msg, TG_PRED)
        src = src.replace(
            "    tg(msg, TG_PRED)\n    mem = load_memory()",
            '''    tg(msg, TG_PRED)
    # ── LIVE BET PLACEMENT ──────────────────────────────────
    if LIVE_BETTING and bet in ["YES", "NO", "SKIP"]:
        try:
            jlb.evaluate_and_bet(
                bet=bet,
                prob_str=prob,
                target=target,
                yes_price=best.get("yes", 0.5),
                market_ticker=best.get("ticker", ""),
                reason=reason,
                regime=cb.get("regime", "UNKNOWN"),
                tg_func=lambda m: tg(m, TG_PRED),
            )
        except Exception as _be:
            log.error(f"Live betting error: {_be}")
    # ────────────────────────────────────────────────────────
    mem = load_memory()'''
        )
        print("✅ Injected engine call via fallback")

    with open(path, "w") as f:
        f.write(src)
    print("✅ jarvis_master.py patched")

# ── Patch jarvis_commands.py ─────────────────────────────────────────────────

def patch_commands():
    path = f"{JARVIS}/jarvis_commands.py"
    with open(path) as f:
        src = f.read()

    # Add import
    if "jarvis_live_betting" not in src:
        src = "try:\n    import jarvis_live_betting as jlb\n    LIVE_BETTING = True\nexcept:\n    LIVE_BETTING = False\n\n" + src
        print("✅ Added live betting import to commands")

    # Wire GO, RESUME, BETSTAT commands into handle()
    old_handle = '''def handle(text, tg_func):
    parts = text.strip().upper().split()
    cmd = parts[0]
    if cmd == "LEARN":
        ticker = parts[1] if len(parts) > 1 else None
        cmd_learn(tg_func, ticker)
        return True
    if cmd in COMMANDS:
        globals()[COMMANDS[cmd]](tg_func)
        return True
    return False'''

    new_handle = '''def handle(text, tg_func):
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
    return False'''

    if old_handle in src:
        src = src.replace(old_handle, new_handle)
        print("✅ Wired GO/RESUME/BETSTAT/WIN/LOSS into handle()")
    elif "cmd == \"GO\"" in src:
        print("✅ Commands already wired")
    else:
        print("⚠️  Could not find handle() — check manually")

    with open(path, "w") as f:
        f.write(src)
    print("✅ jarvis_commands.py patched")

# ── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== WIRING LIVE BETTING ENGINE ===")
    patch_master()
    patch_commands()
    print("\n=== DONE ===")
    print("New Telegram commands:")
    print("  GO       — confirm pending bet")
    print("  RESUME   — resume after halt")
    print("  BETSTAT  — show bankroll + record")
    print("  WIN $X   — record a win")
    print("  LOSS $X  — record a loss")
    print("\nNext: restart master to activate")
    print("  pkill -f jarvis_master.py && sleep 2 && nohup python3 -B /root/jarvis/jarvis_master.py >> /root/jarvis/jarvis_master.log 2>&1 &")
