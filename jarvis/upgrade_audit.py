#!/usr/bin/env python3
import csv
from pathlib import Path
from collections import defaultdict

JARVIS_HOME = Path("/root/jarvis")
PREDICTIONS_LOG = JARVIS_HOME / "predictions_log.csv"
UPGRADE_REPORT = JARVIS_HOME / "upgrade_audit_report.txt"

class UpgradeAudit:
    def __init__(self):
        self.trades = []
        self.by_hour = defaultdict(lambda: {"wins": 0, "losses": 0})
        self.max_win_streak = 0
        self.max_loss_streak = 0

    def load_predictions(self):
        if not PREDICTIONS_LOG.exists():
            print(f"[ERROR] {PREDICTIONS_LOG} not found")
            return
        try:
            with open(PREDICTIONS_LOG) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("result") in ["WIN", "LOSS", "VOID"]:
                        try:
                            hour = int(row.get("edt_hour", "")) if row.get("edt_hour") else None
                        except:
                            hour = None
                        trade = {"id": row.get("prediction_id"), "result": row.get("result"), "hour": hour}
                        self.trades.append(trade)
            print(f"[OK] Loaded {len(self.trades)} trades")
        except Exception as e:
            print(f"[ERROR] {e}")

    def analyze_by_hour(self):
        for trade in self.trades:
            if trade["hour"] is not None:
                if trade["result"] == "WIN":
                    self.by_hour[trade["hour"]]["wins"] += 1
                elif trade["result"] == "LOSS":
                    self.by_hour[trade["hour"]]["losses"] += 1

    def analyze_streaks(self):
        current_type = None
        current_count = 0
        max_win = 0
        max_loss = 0
        for trade in self.trades:
            if trade["result"] == "VOID":
                continue
            result_type = "WIN" if trade["result"] == "WIN" else "LOSS"
            if result_type == current_type:
                current_count += 1
            else:
                if current_type == "WIN":
                    max_win = max(max_win, current_count)
                elif current_type == "LOSS":
                    max_loss = max(max_loss, current_count)
                current_type = result_type
                current_count = 1
        if current_type == "WIN":
            max_win = max(max_win, current_count)
        elif current_type == "LOSS":
            max_loss = max(max_loss, current_count)
        self.max_win_streak = max_win
        self.max_loss_streak = max_loss

    def calculate_kelly(self):
        wins = sum(1 for t in self.trades if t["result"] == "WIN")
        losses = sum(1 for t in self.trades if t["result"] == "LOSS")
        total = wins + losses
        if total == 0:
            return {}
        win_rate = wins / total
        kelly = (2 * win_rate - 1) * 100
        kelly_quarter = kelly * 0.25
        return {"win_rate": win_rate * 100, "kelly": kelly, "kelly_quarter": kelly_quarter}

    def save_report(self):
        kelly = self.calculate_kelly()
        wins = sum(1 for t in self.trades if t["result"] == "WIN")
        losses = sum(1 for t in self.trades if t["result"] == "LOSS")
        voids = sum(1 for t in self.trades if t["result"] == "VOID")
        total = wins + losses + voids
        with open(UPGRADE_REPORT, "w") as f:
            f.write("="*80 + "\nJARVIS UPGRADE AUDIT\n" + "="*80 + "\n\n")
            f.write("SUMMARY:\n")
            f.write(f"  Total: {total} | Graded: {wins+losses} ({wins}W {losses}L)\n")
            f.write(f"  Win Rate: {kelly.get('win_rate', 0):.1f}%\n\n")
            f.write("WIN RATE BY HOUR (EDT):\n")
            if self.by_hour:
                for hour in sorted(self.by_hour.keys()):
                    data = self.by_hour[hour]
                    total_h = data["wins"] + data["losses"]
                    if total_h > 0:
                        wr_h = (data["wins"] / total_h) * 100
                        f.write(f"  {hour:2d}:00 | {data['wins']:3d}W {data['losses']:3d}L | {wr_h:6.1f}%\n")
            f.write("\nSTREAK ANALYSIS:\n")
            f.write(f"  Longest Win: {self.max_win_streak} | Longest Loss: {self.max_loss_streak}\n")
            f.write("\nKELLY SIZING (25%):\n")
            f.write(f"  $500 → ${500 * kelly.get('kelly_quarter', 0) / 100:.2f} per bet\n")
            f.write(f"  $1000 → ${1000 * kelly.get('kelly_quarter', 0) / 100:.2f} per bet\n")
            f.write(f"  $5000 → ${5000 * kelly.get('kelly_quarter', 0) / 100:.2f} per bet\n")
        print(f"[SAVE] {UPGRADE_REPORT}")

    def run(self):
        print("[START] Upgrade Audit\n")
        self.load_predictions()
        self.analyze_by_hour()
        self.analyze_streaks()
        self.save_report()
        print("[DONE]\n")

if __name__ == "__main__":
    audit = UpgradeAudit()
    audit.run()
