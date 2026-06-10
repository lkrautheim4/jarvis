#!/usr/bin/env python3
"""
jarvis_pred_audit.py — weekly re-audit of the hourly BTC predictor's edge.

READ-ONLY. Recomputes betting accuracy by EDT hour (and by 4h regime) from the
graded predictions in btc_memory.json, compares against the live alert gate
(EDGE_HOURS in jarvis_master.py), and Telegrams promote/demote recommendations.

It does NOT modify EDGE_HOURS or any file — it only flags hours that have crossed
or fallen below the 55% bar, so a human can widen/narrow the gate deliberately.

Cron: weekly (Monday 9am EDT). Never writes any .json/.db/.py.
"""
import re, json, sys, requests
from datetime import datetime
from collections import defaultdict

JARVIS_DIR = "/root/jarvis"
BTC_MEM    = f"{JARVIS_DIR}/btc_memory.json"
MASTER_SRC = f"{JARVIS_DIR}/jarvis_master.py"
TG_TOKEN   = __import__("jarvis_secrets").TG_TOKEN_TRADER
TG_CHAT    = "7534553840"
BAR        = 55.0   # win-rate bar (%)
MIN_N      = 5      # min graded bets in a bucket before recommending a gate change


def tg(msg) -> bool:
    """Send to Telegram, verifying delivery (HTTP 200 AND Telegram's `ok` flag).
    Logs failures to stderr (→ cron log) instead of swallowing them. Returns success."""
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg[:4000]}, timeout=10)
        if r.status_code != 200 or not r.json().get("ok"):
            print(f"[pred_audit] Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"[pred_audit] Telegram send error: {e}", file=sys.stderr)
        return False


def current_edge_hours() -> set:
    """Parse the live EDGE_HOURS gate from master's source (no import side-effects)."""
    try:
        m = re.search(r"EDGE_HOURS\s*=\s*\{([^}]*)\}", open(MASTER_SRC).read())
        return {int(x) for x in re.findall(r"\d+", m.group(1))} if m else set()
    except Exception:
        return set()


def _correct(p) -> bool:
    th = p.get("target_hit")
    return (p["bet"] == "YES" and th) or (p["bet"] == "NO" and not th)


def _edt_hour(ts):
    try:
        return (datetime.strptime(ts[:16], "%Y-%m-%d %H:%M").hour - 4) % 24
    except Exception:
        return None


def _regime(p):
    fp = p.get("fingerprint")
    parts = fp.split("|") if fp else []
    return parts[4] if len(parts) >= 5 else "unknown"


def audit():
    try:
        graded = [p for p in json.load(open(BTC_MEM)).get("predictions", []) if p.get("graded")]
    except Exception as e:
        return f"📊 BTC PREDICTOR RE-AUDIT\n⚠️ could not read btc_memory.json: {e}"
    bets = [p for p in graded if p.get("bet") in ("YES", "NO")]
    if not bets:
        return "📊 BTC PREDICTOR RE-AUDIT\n⚠️ no graded bets yet."

    edge = current_edge_hours()
    overall_c = sum(_correct(p) for p in bets)

    byh = defaultdict(list)
    for p in bets:
        h = _edt_hour(p["ts"])
        if h is not None:
            byh[h].append(p)

    promote, demote, hour_lines = [], [], []
    for h in sorted(byh):
        s = byh[h]; c = sum(_correct(p) for p in s); n = len(s); wr = c / n * 100
        in_gate = h in edge
        mark = "✅gate" if in_gate else "·"
        small = "" if n >= MIN_N else " (n<%d)" % MIN_N
        hour_lines.append(f"  {h:02d}EDT {c:>2}/{n:<2} {wr:4.0f}% {mark}{small}")
        if n >= MIN_N:
            if in_gate and wr < BAR:
                demote.append(f"{h:02d}EDT ({wr:.0f}%, n={n})")
            elif (not in_gate) and wr >= BAR:
                promote.append(f"{h:02d}EDT ({wr:.0f}%, n={n})")

    # regime cut (flat is currently skipped by the gate)
    reg = defaultdict(list)
    for p in bets:
        reg[_regime(p)].append(p)
    reg_lines = []
    for r in ("up", "down", "flat", "unknown"):
        s = reg.get(r)
        if s:
            c = sum(_correct(p) for p in s)
            reg_lines.append(f"  {r:7} {c:>2}/{len(s):<2} {c/len(s)*100:4.0f}%")

    rec = []
    if promote:
        rec.append("⬆️ ADD to EDGE_HOURS (now ≥55%): " + ", ".join(promote))
    if demote:
        rec.append("⬇️ REMOVE from EDGE_HOURS (now <55%): " + ", ".join(demote))
    if not rec:
        rec.append("✅ No gate changes recommended (no n≥%d hour crossed the 55%% bar)." % MIN_N)

    return "\n".join([
        "📊 BTC PREDICTOR — WEEKLY RE-AUDIT",
        datetime.now().strftime("%Y-%m-%d %H:%M UTC"),
        "=" * 26,
        f"Overall bet acc: {overall_c}/{len(bets)} = {overall_c/len(bets)*100:.1f}%  (bar {BAR:.0f}%)",
        f"Live EDGE_HOURS: {sorted(edge)}",
        "=" * 26,
        "BY HOUR (EDT):",
        *hour_lines,
        "=" * 26,
        "BY REGIME (flat is gate-skipped):",
        *reg_lines,
        "=" * 26,
        "RECOMMENDATIONS:",
        *rec,
        "",
        "(read-only — edit EDGE_HOURS in jarvis_master.py to apply)",
    ])


if __name__ == "__main__":
    report = audit()
    delivered = tg(report)
    print(report)
    print(f"[pred_audit] telegram delivered: {delivered}")
