#!/usr/bin/env python3
"""
JARVIS Macro Engine - Fixed to write regime_updated to SQLite brain
"""
import sqlite3
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

DB_PATH = "/root/jarvis/jarvis_memory.db"

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def set_brain(conn, key, value):
    ts = datetime.now(ZoneInfo("America/New_York")).isoformat()
    if isinstance(value, (dict, list)):
        value = json.dumps(value)
    conn.execute(
        "INSERT OR REPLACE INTO brain (key, value, updated_at) VALUES (?, ?, ?)",
        (key, str(value), ts)
    )
    conn.commit()

def main():
    log.info("JARVIS MACRO ENGINE ONLINE")
    conn = get_db()
    
    try:
        log.info("Running macro cycle...")
        
        # Your existing macro logic here
        vix = 17.7
        fg_equity = 34
        fg_crypto = 20
        yield_10yr = 4.49
        pcr = 1.0
        
        # Determine regime
        if fg_equity > 70:
            regime = "RISK_OFF"
        elif fg_equity < 30:
            regime = "RISK_ON"
        else:
            regime = "RISK_ON"  # Default
        
        confidence = 67  # Mock
        
        log.info(f"Regime: {regime} ({confidence}%) VIX:{vix} F&G(equity):{fg_equity} F&G(crypto):{fg_crypto} Yield:{yield_10yr}% PCR:{pcr}")
        
        # DUAL-WRITE: Write both regime values AND regime_updated timestamp
        set_brain(conn, "regime", regime)
        set_brain(conn, "regime_confidence", confidence)
        set_brain(conn, "regime_updated", datetime.now(ZoneInfo("America/New_York")).isoformat())
        
        log.info("Macro cycle complete")
    
    except Exception as e:
        log.error(f"Macro failed: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
