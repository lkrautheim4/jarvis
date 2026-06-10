#!/usr/bin/env python3
import json
import csv
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

JARVIS_HOME = Path("/root/jarvis")
KALSHI_BRAIN = JARVIS_HOME / "kalshi_brain.json"
PREDICTIONS_LOG = JARVIS_HOME / "predictions_log.csv"
EDT = ZoneInfo("America/New_York")

def bootstrap():
    if not KALSHI_BRAIN.exists():
        print(f"ERROR: {KALSHI_BRAIN} not found")
        return
    print(f"[READ] Loading {KALSHI_BRAIN}...")
    with open(KALSHI_BRAIN) as f:
        data = json.load(f)
    bets = data.get("bets", data) if isinstance(data, dict) else data
    if not isinstance(bets, list):
        bets = [bets]
    rows = []
    for i, bet in enumerate(bets, start=1):
        bet_id = bet.get("id") or f"K-{i:04d}"
        ts_raw = bet.get("ts") or ""
        try:
            if "T" in ts_raw:
                timestamp = ts_raw
            else:
                dt = datetime.strptime(ts_raw, "%Y-%m-%d %H:%M")
                timestamp = dt.replace(tzinfo=EDT).isoformat()
        except:
            timestamp = datetime.now(EDT).isoformat()
        market = bet.get("market") or "Kalshi bet"
        direction = bet.get("side") or "?"
        amount = bet.get("dollars") or 0
        result = bet.get("result") or ""
        win_loss = 1 if result == "WIN" else (0 if result == "LOSS" else "")
        pnl = bet.get("pnl") or ""
        edt_hour = bet.get("edt_hour") or ""
        row = {
            "prediction_id": f"P{i:03d}",
            "predictor": "JARVIS",
            "pred_type": "kalshi",
            "prediction": market,
            "confidence": "",
            "timestamp": timestamp,
            "kalshi_bet_id": bet_id,
            "bet_amount": amount,
            "bet_direction": direction,
            "outcome_value": f"PnL: {pnl}" if pnl else "",
            "result": result,
            "win_loss": win_loss,
            "edt_hour": edt_hour,
            "notes": "Bootstrapped",
        }
        rows.append(row)
    print(f"[PARSE] Found {len(rows)} bets")
    if rows:
        with open(PREDICTIONS_LOG, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["prediction_id","predictor","pred_type","prediction","confidence","timestamp","kalshi_bet_id","bet_amount","bet_direction","outcome_value","result","win_loss","edt_hour","notes"])
            writer.writeheader()
            writer.writerows(rows)
        print(f"[WRITE] Wrote {len(rows)} rows")
    else:
        print("[WARN] No bets found")

if __name__ == "__main__":
    print("JARVIS Bootstrap\n")
    confirm = input(f"Overwrite {PREDICTIONS_LOG}? [y/N]: ").lower()
    if confirm == "y":
        bootstrap()
    else:
        print("Cancelled")
