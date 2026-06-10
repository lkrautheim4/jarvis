#!/usr/bin/env python3
"""
JARVIS Audit Bot v1.0
Unified prediction tracking + outcome grading + win rate verification
"""

import json
import sqlite3
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from zoneinfo import ZoneInfo

# Config
JARVIS_HOME = Path("/root/jarvis")
KALSHI_BRAIN = JARVIS_HOME / "kalshi_brain.json"
DB_PATH = JARVIS_HOME / "jarvis_memory.db"
PREDICTIONS_LOG = JARVIS_HOME / "predictions_log.csv"
AUDIT_REPORT = JARVIS_HOME / "audit_report.json"
AUDIT_SUMMARY = JARVIS_HOME / "audit_summary.txt"
MISMATCHES = JARVIS_HOME / "audit_mismatches.csv"

EDT = ZoneInfo("America/New_York")


class JARVISAudit:
    def __init__(self):
        self.kalshi_bets = {}
        self.predictions = {}
        self.outcomes = {}
        self.audit_trail = []
        self.mismatches = []

    def load_kalshi_brain(self):
        if not KALSHI_BRAIN.exists():
            print(f"[WARN] kalshi_brain.json not found at {KALSHI_BRAIN}")
            return
        try:
            with open(KALSHI_BRAIN) as f:
                data = json.load(f)
                bets = data.get("bets", data) if isinstance(data, dict) else data
                for bet in (bets if isinstance(bets, list) else []):
                    bet_id = bet.get("id") or str(len(self.kalshi_bets))
                    self.kalshi_bets[bet_id] = {
                        "timestamp": bet.get("timestamp") or bet.get("ts"),
                        "market": bet.get("market", ""),
                        "direction": bet.get("side") or bet.get("direction"),
                        "amount": bet.get("dollars") or bet.get("amount"),
                        "outcome": bet.get("outcome"),
                        "graded": bet.get("graded", False),
                        "result": bet.get("result"),
                        "win_loss": 1 if bet.get("result") == "WIN" else (0 if bet.get("result") == "LOSS" else None),
                    }
            print(f"[OK] Loaded {len(self.kalshi_bets)} Kalshi bets")
        except Exception as e:
            print(f"[ERROR] Failed to load kalshi_brain.json: {e}")

    def load_predictions_log(self):
        if not PREDICTIONS_LOG.exists():
            print(f"[WARN] predictions_log.csv not found at {PREDICTIONS_LOG}")
            return
        try:
            with open(PREDICTIONS_LOG) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    pred_id = row.get("prediction_id")
                    if pred_id:
                        try:
                            confidence = float(row.get("confidence", "") or 0) or None
                        except:
                            confidence = None
                        try:
                            bet_amount = float(row.get("bet_amount", "") or 0) or None
                        except:
                            bet_amount = None
                        try:
                            win_loss = int(row.get("win_loss", "")) if row.get("win_loss") else None
                        except:
                            win_loss = None
                        
                        self.predictions[pred_id] = {
                            "predictor": row.get("predictor"),
                            "prediction": row.get("prediction"),
                            "confidence": confidence,
                            "timestamp": row.get("timestamp"),
                            "kalshi_bet_id": row.get("kalshi_bet_id") or None,
                            "bet_amount": bet_amount,
                            "bet_direction": row.get("bet_direction"),
                            "result": row.get("result"),
                            "win_loss": win_loss,
                        }
            print(f"[OK] Loaded {len(self.predictions)} predictions")
        except Exception as e:
            print(f"[ERROR] Failed to load predictions_log.csv: {e}")

    def audit(self):
        print("\n[AUDIT] Starting reconciliation...")
        for pred_id, pred in self.predictions.items():
            bet_id = pred.get("kalshi_bet_id")
            audit_entry = {
                "prediction_id": pred_id,
                "predictor": pred.get("predictor"),
                "prediction": pred.get("prediction"),
                "confidence": pred.get("confidence"),
                "bet_id": bet_id,
                "bet_placed": "NO",
                "bet_direction": pred.get("bet_direction"),
                "bet_amount": pred.get("bet_amount"),
                "result": pred.get("result"),
                "win_loss": pred.get("win_loss"),
                "status": "OK",
            }
            if bet_id and bet_id in self.kalshi_bets:
                bet = self.kalshi_bets[bet_id]
                audit_entry["bet_placed"] = "YES"
                audit_entry["market"] = bet.get("market")
                audit_entry["result"] = bet.get("result")
                audit_entry["win_loss"] = bet.get("win_loss")
            elif bet_id:
                audit_entry["status"] = "BET_NOT_FOUND"
            self.audit_trail.append(audit_entry)
        print(f"[OK] {len(self.audit_trail)} entries reconciled")

    def calculate_stats(self):
        stats = {
            "total": len(self.audit_trail),
            "graded": sum(1 for e in self.audit_trail if e["win_loss"] is not None),
            "by_predictor": {},
        }
        for entry in self.audit_trail:
            if entry["win_loss"] is not None:
                predictor = entry["predictor"] or "UNKNOWN"
                if predictor not in stats["by_predictor"]:
                    stats["by_predictor"][predictor] = {"wins": 0, "losses": 0, "total": 0, "wr": 0}
                stats["by_predictor"][predictor]["total"] += 1
                if entry["win_loss"] == 1:
                    stats["by_predictor"][predictor]["wins"] += 1
                else:
                    stats["by_predictor"][predictor]["losses"] += 1
        for p, d in stats["by_predictor"].items():
            d["wr"] = round((d["wins"] / d["total"] * 100), 1) if d["total"] > 0 else 0
        return stats

    def save_reports(self):
        stats = self.calculate_stats()
        with open(AUDIT_SUMMARY, "w") as f:
            f.write("="*80 + "\nJARVIS AUDIT REPORT\n" + "="*80 + "\n\n")
            f.write(f"Total Predictions: {stats['total']}\n")
            f.write(f"Graded: {stats['graded']}\n\n")
            f.write("WIN RATE BY PREDICTOR:\n")
            for p, d in sorted(stats["by_predictor"].items(), key=lambda x: x[1]["wr"], reverse=True):
                f.write(f"  {p:20s} | {d['wins']:3d}W {d['losses']:3d}L | {d['wr']:6.1f}% ({d['total']} total)\n")
        print(f"[SAVE] {AUDIT_SUMMARY}")

    def run(self):
        print("[START] JARVIS Audit\n")
        self.load_kalshi_brain()
        self.load_predictions_log()
        self.audit()
        self.save_reports()
        print("\n[DONE]\n")

if __name__ == "__main__":
    audit = JARVISAudit()
    audit.run()
