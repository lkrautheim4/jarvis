#!/usr/bin/env python3
"""
JARVIS SHARED MEMORY
SQLite database all bots can read/write
Replaces scattered JSON files with one unified brain
"""
import sqlite3, json, os
from datetime import datetime

DB_PATH = "/root/jarvis/jarvis_brain.db"

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Create tables if they don't exist"""
    conn = get_db()
    c = conn.cursor()
    
    # Price ticks — BTC/ETH/SOL every hour
    c.execute('''CREATE TABLE IF NOT EXISTS price_ticks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, symbol TEXT, price REAL, rsi REAL,
        momentum_1h REAL, momentum_24h REAL,
        funding_rate REAL, volume_ratio REAL,
        regime TEXT
    )''')
    
    # Predictions — hourly Kalshi calls
    c.execute('''CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, symbol TEXT, price_at_pred REAL,
        target REAL, target_prob TEXT,
        predicted_price TEXT, bet TEXT, reason TEXT,
        actual_price REAL, target_hit INTEGER, graded INTEGER DEFAULT 0
    )''')
    
    # Kalshi bets — manual bets you place
    c.execute('''CREATE TABLE IF NOT EXISTS kalshi_bets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, side TEXT, type TEXT,
        dollars REAL, result TEXT, pnl REAL
    )''')
    
    # PRED calls — 15-min predictions
    c.execute('''CREATE TABLE IF NOT EXISTS pred_calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, price REAL, direction TEXT,
        confidence TEXT, mins INTEGER,
        actual_price REAL, correct INTEGER, graded INTEGER DEFAULT 0
    )''')
    
    # Scalp trades — Alpaca paper trades
    c.execute('''CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, symbol TEXT, side TEXT,
        entry_price REAL, exit_price REAL,
        qty REAL, pnl REAL, status TEXT,
        rsi REAL, funding REAL, volume REAL, regime TEXT
    )''')
    
    # Bot events — anything any bot wants to log
    c.execute('''CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, bot TEXT, event_type TEXT,
        data TEXT
    )''')
    
    # Key-value store — any bot can store anything
    c.execute('''CREATE TABLE IF NOT EXISTS kv_store (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT
    )''')
    
    conn.commit()
    conn.close()
    print(f"DB initialized: {DB_PATH}")

# ── PRICE TICKS ───────────────────────────────────────────────────────────────
def log_price(symbol, price, rsi=50, m1h=0, m24h=0, funding=0, volume=1, regime="UNKNOWN"):
    conn = get_db()
    conn.execute('''INSERT INTO price_ticks 
        (ts, symbol, price, rsi, momentum_1h, momentum_24h, funding_rate, volume_ratio, regime)
        VALUES (?,?,?,?,?,?,?,?,?)''',
        (datetime.utcnow().strftime("%Y-%m-%d %H:%M"), symbol, price, rsi, m1h, m24h, funding, volume, regime))
    conn.commit(); conn.close()

def get_recent_prices(symbol="BTC", hours=24):
    conn = get_db()
    rows = conn.execute('''SELECT * FROM price_ticks 
        WHERE symbol=? ORDER BY id DESC LIMIT ?''', (symbol, hours)).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]

def get_last_price(symbol="BTC"):
    conn = get_db()
    row = conn.execute('SELECT price FROM price_ticks WHERE symbol=? ORDER BY id DESC LIMIT 1', (symbol,)).fetchone()
    conn.close()
    return row["price"] if row else None

# ── PREDICTIONS ───────────────────────────────────────────────────────────────
def log_prediction(symbol, price, target, target_prob, predicted_price, bet, reason):
    conn = get_db()
    conn.execute('''INSERT INTO predictions 
        (ts, symbol, price_at_pred, target, target_prob, predicted_price, bet, reason)
        VALUES (?,?,?,?,?,?,?,?)''',
        (datetime.utcnow().strftime("%Y-%m-%d %H:%M"), symbol, price, target, target_prob, str(predicted_price), bet, reason))
    conn.commit(); conn.close()

def grade_last_prediction(actual_price):
    conn = get_db()
    row = conn.execute('SELECT * FROM predictions WHERE graded=0 ORDER BY id DESC LIMIT 1').fetchone()
    if row:
        hit = 1 if actual_price >= row["target"] else 0
        conn.execute('UPDATE predictions SET actual_price=?, target_hit=?, graded=1 WHERE id=?',
            (actual_price, hit, row["id"]))
        conn.commit()
    conn.close()
    return dict(row) if row else None

def get_prediction_accuracy():
    conn = get_db()
    total = conn.execute('SELECT COUNT(*) as c FROM predictions WHERE graded=1').fetchone()["c"]
    correct = conn.execute('SELECT COUNT(*) as c FROM predictions WHERE graded=1 AND target_hit=1').fetchone()["c"]
    yes_total = conn.execute('SELECT COUNT(*) as c FROM predictions WHERE graded=1 AND bet="YES"').fetchone()["c"]
    yes_correct = conn.execute('SELECT COUNT(*) as c FROM predictions WHERE graded=1 AND bet="YES" AND target_hit=1').fetchone()["c"]
    no_total = conn.execute('SELECT COUNT(*) as c FROM predictions WHERE graded=1 AND bet="NO"').fetchone()["c"]
    no_correct = conn.execute('SELECT COUNT(*) as c FROM predictions WHERE graded=1 AND bet="NO" AND target_hit=0').fetchone()["c"]
    conn.close()
    return {"total":total,"correct":correct,"yes_total":yes_total,"yes_correct":yes_correct,
            "no_total":no_total,"no_correct":no_correct,
            "wr":round(correct/total*100) if total>0 else 0}

# ── KALSHI BETS ───────────────────────────────────────────────────────────────
def log_bet(side, dollars, btype="hourly"):
    conn = get_db()
    conn.execute('INSERT INTO kalshi_bets (ts,side,type,dollars) VALUES (?,?,?,?)',
        (datetime.utcnow().strftime("%Y-%m-%d %H:%M"), side, btype, dollars))
    conn.commit(); conn.close()

def grade_bet(won):
    conn = get_db()
    row = conn.execute('SELECT * FROM kalshi_bets WHERE result IS NULL ORDER BY id DESC LIMIT 1').fetchone()
    if row:
        pnl = row["dollars"] if won else -row["dollars"]
        conn.execute('UPDATE kalshi_bets SET result=?, pnl=? WHERE id=?',
            ("WIN" if won else "LOSS", pnl, row["id"]))
        conn.commit()
    conn.close()
    return dict(row) if row else None

def get_bet_stats():
    conn = get_db()
    total = conn.execute('SELECT COUNT(*) as c FROM kalshi_bets WHERE result IS NOT NULL').fetchone()["c"]
    wins = conn.execute('SELECT COUNT(*) as c FROM kalshi_bets WHERE result="WIN"').fetchone()["c"]
    pnl = conn.execute('SELECT SUM(pnl) as s FROM kalshi_bets').fetchone()["s"] or 0
    conn.close()
    return {"total":total,"wins":wins,"losses":total-wins,"pnl":round(pnl,2),
            "wr":round(wins/total*100) if total>0 else 0}

# ── PRED CALLS ────────────────────────────────────────────────────────────────
def log_pred(price, direction, confidence, mins):
    conn = get_db()
    conn.execute('INSERT INTO pred_calls (ts,price,direction,confidence,mins) VALUES (?,?,?,?,?)',
        (datetime.utcnow().strftime("%Y-%m-%d %H:%M"), price, direction, confidence, mins))
    conn.commit(); conn.close()

def grade_pred(actual_price):
    conn = get_db()
    row = conn.execute('SELECT * FROM pred_calls WHERE graded=0 ORDER BY id DESC LIMIT 1').fetchone()
    if row:
        above = actual_price > row["price"]
        predicted_above = row["direction"] in ["ABOVE","UP"]
        correct = 1 if above==predicted_above else 0
        conn.execute('UPDATE pred_calls SET actual_price=?,correct=?,graded=1 WHERE id=?',
            (actual_price, correct, row["id"]))
        conn.commit()
    conn.close()
    return dict(row) if row else None

def get_pred_accuracy():
    conn = get_db()
    total = conn.execute('SELECT COUNT(*) as c FROM pred_calls WHERE graded=1').fetchone()["c"]
    correct = conn.execute('SELECT COUNT(*) as c FROM pred_calls WHERE graded=1 AND correct=1').fetchone()["c"]
    above_total = conn.execute('SELECT COUNT(*) as c FROM pred_calls WHERE graded=1 AND direction IN ("ABOVE","UP")').fetchone()["c"]
    above_correct = conn.execute('SELECT COUNT(*) as c FROM pred_calls WHERE graded=1 AND direction IN ("ABOVE","UP") AND correct=1').fetchone()["c"]
    below_total = conn.execute('SELECT COUNT(*) as c FROM pred_calls WHERE graded=1 AND direction IN ("BELOW","DOWN")').fetchone()["c"]
    below_correct = conn.execute('SELECT COUNT(*) as c FROM pred_calls WHERE graded=1 AND direction IN ("BELOW","DOWN") AND correct=1').fetchone()["c"]
    last5 = conn.execute('SELECT correct FROM pred_calls WHERE graded=1 ORDER BY id DESC LIMIT 5').fetchall()
    last5_str = "".join(["✓" if r["correct"] else "✗" for r in reversed(last5)])
    conn.close()
    return {"total":total,"correct":correct,"wr":round(correct/total*100) if total>0 else 0,
            "above_wr":round(above_correct/above_total*100) if above_total>0 else 0,
            "below_wr":round(below_correct/below_total*100) if below_total>0 else 0,
            "last5":last5_str}

# ── TRADES ────────────────────────────────────────────────────────────────────
def log_trade_open(symbol, side, entry, qty, rsi=50, funding=0, volume=1, regime="UNKNOWN"):
    conn = get_db()
    conn.execute('''INSERT INTO trades (ts,symbol,side,entry_price,qty,status,rsi,funding,volume,regime)
        VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (datetime.utcnow().strftime("%Y-%m-%d %H:%M"), symbol, side, entry, qty, "open", rsi, funding, volume, regime))
    conn.commit()
    row_id = conn.execute('SELECT last_insert_rowid() as id').fetchone()["id"]
    conn.close()
    return row_id

def log_trade_close(trade_id, exit_price, pnl):
    conn = get_db()
    conn.execute('UPDATE trades SET exit_price=?,pnl=?,status="closed" WHERE id=?',
        (exit_price, pnl, trade_id))
    conn.commit(); conn.close()

def get_trade_stats():
    conn = get_db()
    total = conn.execute('SELECT COUNT(*) as c FROM trades WHERE status="closed"').fetchone()["c"]
    wins = conn.execute('SELECT COUNT(*) as c FROM trades WHERE status="closed" AND pnl>0').fetchone()["c"]
    pnl = conn.execute('SELECT SUM(pnl) as s FROM trades WHERE status="closed"').fetchone()["s"] or 0
    conn.close()
    return {"total":total,"wins":wins,"losses":total-wins,"pnl":round(pnl,2),
            "wr":round(wins/total*100) if total>0 else 0}

# ── KEY-VALUE STORE ───────────────────────────────────────────────────────────
def kv_set(key, value):
    conn = get_db()
    conn.execute('INSERT OR REPLACE INTO kv_store (key,value,updated_at) VALUES (?,?,?)',
        (key, json.dumps(value), datetime.utcnow().strftime("%Y-%m-%d %H:%M")))
    conn.commit(); conn.close()

def kv_get(key, default=None):
    conn = get_db()
    row = conn.execute('SELECT value FROM kv_store WHERE key=?', (key,)).fetchone()
    conn.close()
    return json.loads(row["value"]) if row else default

# ── EVENTS ────────────────────────────────────────────────────────────────────
def log_event(bot, event_type, data=None):
    conn = get_db()
    conn.execute('INSERT INTO events (ts,bot,event_type,data) VALUES (?,?,?,?)',
        (datetime.utcnow().strftime("%Y-%m-%d %H:%M"), bot, event_type, json.dumps(data or {})))
    conn.commit(); conn.close()

def get_recent_events(limit=20):
    conn = get_db()
    rows = conn.execute('SELECT * FROM events ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── CONTEXT BUILDER ───────────────────────────────────────────────────────────
def build_context(symbol="BTC"):
    """Build full context string for Claude prompts"""
    prices = get_recent_prices(symbol, 12)
    pred_acc = get_prediction_accuracy()
    bet_stats = get_bet_stats()
    pred_stats = get_pred_accuracy()
    trade_stats = get_trade_stats()
    
    lines = []
    
    if prices:
        p_vals = [p["price"] for p in prices]
        lines.append(f"Price range 12h: ${min(p_vals):,.0f}-${max(p_vals):,.0f}")
        lines.append(f"Last: ${prices[-1]['price']:,.0f} RSI:{prices[-1]['rsi']}")
        recent = " ".join([f"${p['price']:,.0f}" for p in prices[-6:]])
        lines.append(f"Recent: {recent}")
        rsi_vals = [p["rsi"] for p in prices[-6:]]
        trend = "rising" if rsi_vals[-1] > rsi_vals[0] else "falling"
        lines.append(f"RSI trend: {' '.join(map(str,rsi_vals))} ({trend})")
    
    if pred_acc["total"] > 0:
        lines.append(f"Hourly pred: {pred_acc['wr']}% ({pred_acc['correct']}/{pred_acc['total']})")
    
    if pred_stats["total"] > 0:
        lines.append(f"15-min pred: {pred_stats['wr']}% ABOVE:{pred_stats['above_wr']}% BELOW:{pred_stats['below_wr']}% Last5:{pred_stats['last5']}")
    
    if bet_stats["total"] > 0:
        lines.append(f"Kalshi bets: {bet_stats['wr']}% WR P&L:${bet_stats['pnl']:+.0f}")
    
    if trade_stats["total"] > 0:
        lines.append(f"Scalp trades: {trade_stats['wr']}% WR P&L:${trade_stats['pnl']:+.0f}")
    
    return "\n".join(lines)

# ── MIGRATE FROM JSON ─────────────────────────────────────────────────────────
def migrate_from_json():
    """Import existing JSON data into SQLite"""
    imported = 0
    
    # Import btc_memory.json
    try:
        import json as _json
        m = _json.load(open("/root/jarvis/btc_memory.json"))
        for p in m.get("prices", []):
            try:
                log_price("BTC", p["price"], p.get("rsi",50), 
                         p.get("1h",0), p.get("24h",0))
                imported += 1
            except: pass
        for pred in m.get("predictions", []):
            try:
                conn = get_db()
                conn.execute('''INSERT INTO predictions 
                    (ts,symbol,price_at_pred,target,target_prob,predicted_price,bet,reason,actual_price,target_hit,graded)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (pred.get("ts",""), "BTC", pred.get("price_at_pred",0),
                     pred.get("target",0), pred.get("target_prob",""),
                     str(pred.get("predicted_price","")), pred.get("bet",""),
                     pred.get("reason",""), pred.get("actual_price"),
                     1 if pred.get("target_hit") else 0,
                     1 if pred.get("graded") else 0))
                conn.commit(); conn.close()
                imported += 1
            except: pass
    except Exception as e:
        print(f"btc_memory migration: {e}")
    
    # Import kalshi_brain.json
    try:
        kb = _json.load(open("/root/jarvis/kalshi_brain.json"))
        for bet in kb.get("bets", []):
            try:
                conn = get_db()
                conn.execute('INSERT INTO kalshi_bets (ts,side,type,dollars,result,pnl) VALUES (?,?,?,?,?,?)',
                    (bet.get("ts",""), bet.get("side",""), bet.get("type","hourly"),
                     bet.get("dollars",50), bet.get("result"), bet.get("pnl")))
                conn.commit(); conn.close()
                imported += 1
            except: pass
        for pred in kb.get("preds", []):
            try:
                conn = get_db()
                conn.execute('INSERT INTO pred_calls (ts,price,direction,confidence,mins,actual_price,correct,graded) VALUES (?,?,?,?,?,?,?,?)',
                    (pred.get("ts",""), pred.get("price",0), pred.get("direction",""),
                     pred.get("conf",""), pred.get("mins",15),
                     pred.get("actual_price"), 1 if pred.get("correct") else 0,
                     1 if pred.get("result") else 0))
                conn.commit(); conn.close()
                imported += 1
            except: pass
    except Exception as e:
        print(f"kalshi_brain migration: {e}")
    
    print(f"Migrated {imported} records to SQLite")

if __name__ == "__main__":
    init_db()
    migrate_from_json()
    print("\nShared memory stats:")
    print("Prices:", len(get_recent_prices("BTC", 1000)))
    print("Bet stats:", get_bet_stats())
    print("Pred stats:", get_pred_accuracy())
    print("\nContext sample:")
    print(build_context())
