#!/usr/bin/env python3
"""
JARVIS SESSION SYSTEM
Tracks a full Kalshi watch session from price selection to settlement
WATCH → PRED → BET → CLOSE
"""

import json, os, sqlite3
from datetime import datetime, timedelta

SESSION_FILE = "/root/jarvis/active_session.json"
DB_PATH = "/root/jarvis/jarvis_brain.db"

# ── SESSION MANAGEMENT ────────────────────────────────────────────────────────

def start_session(strike, kalshi_markets=None):
    """Start a new watch session on a strike price"""
    session = {
        "id": datetime.utcnow().strftime("%Y%m%d%H%M%S"),
        "started": datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        "strike": strike,
        "preds": [],          # all pred calls during session
        "bet": None,          # bet placed
        "close_price": None,  # actual BTC price at expiry
        "outcome": None,      # WIN or LOSS
        "graded": False,
        "kalshi_markets": kalshi_markets or []
    }
    with open(SESSION_FILE, "w") as f:
        json.dump(session, f, indent=2)
    return session

def get_session():
    """Get active session"""
    if not os.path.exists(SESSION_FILE):
        return None
    try:
        s = json.load(open(SESSION_FILE))
        # Expire sessions older than 2 hours
        started = datetime.strptime(s["started"], "%Y-%m-%d %H:%M")
        if datetime.utcnow() - started > timedelta(hours=2):
            return None
        return s
    except:
        return None

def save_session(session):
    with open(SESSION_FILE, "w") as f:
        json.dump(session, f, indent=2)

def clear_session():
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)

def log_pred_to_session(direction, confidence, mins, btc_now):
    """Add a pred call to active session"""
    session = get_session()
    if not session:
        return None
    pred = {
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        "direction": direction,
        "confidence": confidence,
        "mins_remaining": mins,
        "btc_at_pred": btc_now,
        "diff_from_strike": round(btc_now - session["strike"], 2)
    }
    session["preds"].append(pred)
    save_session(session)
    return session

def log_bet_to_session(side, dollars):
    """Link bet to active session"""
    session = get_session()
    if not session:
        return None
    session["bet"] = {
        "side": side,
        "dollars": dollars,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    }
    save_session(session)
    return session

def close_session(actual_price, won):
    """Grade session with actual close price"""
    session = get_session()
    if not session:
        return None, None

    strike = session["strike"]
    session["close_price"] = actual_price
    session["outcome"] = "WIN" if won else "LOSS"
    session["graded"] = True

    # Was BTC above or below at close?
    actual_above = actual_price > strike
    bet_side = session.get("bet", {}).get("side", "")
    predicted_above = bet_side == "YES"

    # Grade each pred call
    pred_results = []
    for pred in session["preds"]:
        pred_above = pred["direction"] in ["ABOVE", "UP"]
        correct = (pred_above == actual_above)
        pred["correct"] = correct
        pred["actual_close"] = actual_price
        pred["error"] = round(abs(actual_price - strike), 2)
        pred_results.append(correct)

    # Save to DB
    _save_session_to_db(session, actual_above, won, pred_results)
    save_session(session)
    clear_session()
    return session, pred_results

def _save_session_to_db(session, actual_above, won, pred_results):
    """Persist session to SQLite for learning"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row

        # Create sessions table if needed
        conn.execute('''CREATE TABLE IF NOT EXISTS watch_sessions (
            id TEXT PRIMARY KEY,
            ts TEXT, strike REAL, close_price REAL,
            actual_above INTEGER, bet_side TEXT,
            bet_dollars REAL, won INTEGER,
            pred_count INTEGER, pred_correct INTEGER,
            avg_confidence TEXT, first_diff REAL,
            session_duration_mins INTEGER
        )''')

        preds = session.get("preds", [])
        bet = session.get("bet") or {}
        pred_correct = sum(1 for r in pred_results if r)
        avg_conf = ""
        if preds:
            confs = []
            for p in preds:
                try: confs.append(float(p["confidence"].replace("%","")))
                except: pass
            avg_conf = f"{round(sum(confs)/len(confs))}%" if confs else ""

        first_diff = preds[0]["diff_from_strike"] if preds else 0
        started = datetime.strptime(session["started"], "%Y-%m-%d %H:%M")
        duration = int((datetime.utcnow() - started).total_seconds() / 60)

        conn.execute('''INSERT OR REPLACE INTO watch_sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
            session["id"], session["started"], session["strike"],
            session.get("close_price"), 1 if actual_above else 0,
            bet.get("side",""), bet.get("dollars",0), 1 if won else 0,
            len(preds), pred_correct, avg_conf, first_diff, duration
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"DB save error: {e}")

# ── PATTERN ANALYSIS ──────────────────────────────────────────────────────────

def get_session_patterns():
    """Analyze past sessions to find your edge"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row

        # Check table exists
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if not any(t["name"] == "watch_sessions" for t in tables):
            conn.close()
            return {}

        total = conn.execute("SELECT COUNT(*) as c FROM watch_sessions WHERE won IS NOT NULL").fetchone()["c"]
        if total < 3:
            conn.close()
            return {"total": total, "msg": "Need more sessions to find patterns"}

        wins = conn.execute("SELECT COUNT(*) as c FROM watch_sessions WHERE won=1").fetchone()["c"]
        
        # Win rate when BTC already above strike at first pred
        above_wins = conn.execute("SELECT COUNT(*) as c FROM watch_sessions WHERE first_diff > 0 AND won=1").fetchone()["c"]
        above_total = conn.execute("SELECT COUNT(*) as c FROM watch_sessions WHERE first_diff > 0").fetchone()["c"]
        
        below_wins = conn.execute("SELECT COUNT(*) as c FROM watch_sessions WHERE first_diff < 0 AND won=1").fetchone()["c"]
        below_total = conn.execute("SELECT COUNT(*) as c FROM watch_sessions WHERE first_diff < 0").fetchone()["c"]

        # YES vs NO win rate
        yes_wins = conn.execute("SELECT COUNT(*) as c FROM watch_sessions WHERE bet_side='YES' AND won=1").fetchone()["c"]
        yes_total = conn.execute("SELECT COUNT(*) as c FROM watch_sessions WHERE bet_side='YES'").fetchone()["c"]
        no_wins = conn.execute("SELECT COUNT(*) as c FROM watch_sessions WHERE bet_side='NO' AND won=1").fetchone()["c"]
        no_total = conn.execute("SELECT COUNT(*) as c FROM watch_sessions WHERE bet_side='NO'").fetchone()["c"]

        conn.close()
        return {
            "total": total,
            "overall_wr": round(wins/total*100) if total > 0 else 0,
            "when_btc_above_wr": round(above_wins/above_total*100) if above_total > 0 else 0,
            "when_btc_below_wr": round(below_wins/below_total*100) if below_total > 0 else 0,
            "yes_wr": round(yes_wins/yes_total*100) if yes_total > 0 else 0,
            "no_wr": round(no_wins/no_total*100) if no_total > 0 else 0,
            "above_sessions": above_total,
            "below_sessions": below_total,
        }
    except Exception as e:
        return {"error": str(e)}

def get_history_context(strike):
    """Get recent pred history on this strike for Claude prompt"""
    session = get_session()
    if not session or not session.get("preds"):
        return ""
    
    lines = [f"Session on ${strike:,.0f} — {len(session['preds'])} calls so far:"]
    for p in session["preds"]:
        diff = p["diff_from_strike"]
        pos = f"BTC was ${abs(diff):,.0f} {'above' if diff > 0 else 'below'}"
        lines.append(f"  {p['direction']} {p['confidence']} with {p['mins_remaining']}min left ({pos})")
    
    patterns = get_session_patterns()
    if patterns.get("total", 0) >= 3:
        lines.append(f"Historical: when BTC above strike I win {patterns['when_btc_above_wr']}% | below {patterns['when_btc_below_wr']}%")
    
    return "\n".join(lines)

def get_time_confidence_boost(mins, btc_now, strike):
    """
    Boost confidence as time runs out if BTC is on the right side.
    Less time = less room for reversal = higher confidence.
    ATR ~$400/hr = ~$6.67/min
    """
    expected_move = 6.67 * mins  # expected $ move in remaining time
    diff = abs(btc_now - strike)
    
    if diff == 0:
        return 0, "At the money — coin flip"
    
    # How many expected moves away is the strike?
    moves_away = diff / expected_move if expected_move > 0 else 999
    
    if moves_away > 2:
        boost = 20
        note = f"Strike is {moves_away:.1f}x expected move away — very likely to hold"
    elif moves_away > 1:
        boost = 10
        note = f"Strike is {moves_away:.1f}x expected move away — likely to hold"
    else:
        boost = 0
        note = f"Strike is only {moves_away:.1f}x expected move away — reversal possible"
    
    return boost, note

if __name__ == "__main__":
    # Test
    print("Session system loaded")
    patterns = get_session_patterns()
    print("Patterns:", patterns)
