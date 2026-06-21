import sqlite3, json
from datetime import datetime
import os

DB = '/root/jarvis/jarvis_memory.db'

class JarvisContext:
    def ensure_tables(self):
        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS bot_decisions (
            id INTEGER PRIMARY KEY, ts TEXT, bot_name TEXT, decision_type TEXT, symbol TEXT,
            signal TEXT, confidence REAL, action TEXT, reason TEXT, data JSON,
            UNIQUE(ts, bot_name, decision_type, symbol))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS shared_context (
            key TEXT PRIMARY KEY, value TEXT, ts TEXT, source_bot TEXT)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS bot_messages (
            id INTEGER PRIMARY KEY, ts TEXT, from_bot TEXT, to_bot TEXT,
            message_type TEXT, payload JSON, read_at TEXT)""")
        conn.commit()
        conn.close()


    def log_to_audit(self, from_bot, decision_type, symbol=None, signal=None, 
                     action=None, confidence=None, reason=None, approved_by=None, blocked_reason=None):
        """Log decision to audit trail"""
        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO decision_audit 
            (ts, from_bot, decision_type, symbol, signal, action, confidence, reason, approved_by, blocked_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(), from_bot, decision_type, symbol, signal,
            action, confidence, reason, approved_by, blocked_reason
        ))
        conn.commit()
        conn.close()


    def write_decision(self, bot_name, decision_type, symbol=None, signal=None, 
                      confidence=None, action=None, reason=None, data=None):
        """Bot writes a decision to shared state (non-blocking)"""
        import time
        for attempt in range(3):
            try:
                conn = sqlite3.connect(DB, timeout=1)
                cur = conn.cursor()
                cur.execute("""
                    INSERT OR REPLACE INTO bot_decisions 
                    (ts, bot_name, decision_type, symbol, signal, confidence, action, reason, data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    datetime.now().isoformat(),
                    bot_name,
                    decision_type,
                    symbol,
                    signal,
                    confidence,
                    action,
                    reason,
                    json.dumps(data) if data else None
                ))
                conn.commit()
                conn.close()
                return  # Success
            except Exception as e:
                if attempt < 2:
                    time.sleep(0.1)  # Brief backoff
                else:
                    pass  # Silently drop write on final failure

    def read_decision(self, bot_name, decision_type, symbol=None):
        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        query = "SELECT signal, confidence, action, reason FROM bot_decisions WHERE bot_name=? AND decision_type=?"
        params = [bot_name, decision_type]
        if symbol:
            query += " AND symbol=?"
            params.append(symbol)
        query += " ORDER BY ts DESC LIMIT 1"
        cur.execute(query, params)
        result = cur.fetchone()
        conn.close()
        return result

    def get_context(self, key):
        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        cur.execute("SELECT value FROM shared_context WHERE key=?", (key,))
        result = cur.fetchone()
        conn.close()
        return result[0] if result else None

    def set_context(self, key, value, source_bot):
        conn = sqlite3.connect(DB)
        cur = conn.cursor()
        cur.execute("""INSERT OR REPLACE INTO shared_context (key, value, ts, source_bot)
            VALUES (?, ?, ?, ?)""",
            (key, str(value), datetime.now().isoformat(), source_bot))
        conn.commit()
        conn.close()

_ctx = None
def get_context():
    global _ctx
    if _ctx is None:
        _ctx = JarvisContext()
        _ctx.ensure_tables()
    return _ctx
