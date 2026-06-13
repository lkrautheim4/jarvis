#!/usr/bin/env python3
"""
jarvis_doctor.py — READ-ONLY diagnostic for the JARVIS trading bot system.
Never modifies any file or database.

Sections:
  1. Bot liveness / heartbeat / output freshness  (19 bots × 3 checks)
  2. DB ↔ JSON consistency
  3. Brain key staleness  (with equity F&G contamination spotlight)
  4. Grader status
  5. Safe-to-rearm verdict  (exit 1 if NOT SAFE)
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

JARVIS_DIR = "/root/jarvis"
DB_PATH    = f"{JARVIS_DIR}/jarvis_memory.db"
ET         = ZoneInfo("America/New_York")

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

# ── Bot registry ──────────────────────────────────────────────────────────────

BOTS = [
    {"name": "jarvis_trump_monitor", "file": "jarvis_trump_monitor.py", "critical": False},
    {"name": "jarvis_master",        "file": "jarvis_master.py",        "critical": True},
    {"name": "jarvis_api",           "file": "jarvis_api.py",           "critical": True},
    {"name": "jarvis_briefing",      "file": "jarvis_briefing.py",      "critical": False},
    {"name": "jarvis_intelligence",  "file": "jarvis_intelligence.py",  "critical": False},
    {"name": "jarvis_options_brain", "file": "jarvis_options_brain.py", "critical": False},
    {"name": "jarvis_stocks_v2",     "file": "jarvis_stocks_v2.py",     "critical": False},
    {"name": "jarvis_beast",         "file": "jarvis_beast.py",         "critical": False},
    {"name": "jarvis_congress",      "file": "jarvis_congress.py",      "critical": False},
    {"name": "jarvis_level5",        "file": "jarvis_level5.py",        "critical": False},
    {"name": "jarvis_cascade",       "file": "jarvis_cascade.py",       "critical": False},
    {"name": "lenny_predictions",    "file": "lenny_predictions.py",    "critical": False},
    {"name": "jarvis_futures",       "file": "jarvis_futures.py",       "critical": False},
    {"name": "lenny_trader_bot",     "file": "lenny_trader_bot.py",     "critical": False},
    {"name": "jarvis_trader",        "file": "jarvis_trader.py",        "critical": False},
    {"name": "jarvis_premium",       "file": "jarvis_premium.py",       "critical": False},
    {"name": "options_grader",       "file": "options_grader.py",       "critical": False},
    {"name": "btc_ticker",           "file": "btc_ticker.py",           "critical": False},
    {"name": "jarvis_health",        "file": "jarvis_health.py",        "critical": False},
]

# Per-bot heartbeat timeout (seconds).  Exceeding this → stale_hb.
HB_TIMEOUT = {
    "jarvis_master":        300,    # 90s scalp loop
    "jarvis_briefing":      3600,   # 30-min INTERVAL
    "jarvis_intelligence":  21600,  # 4-hour INTERVAL
    "jarvis_options_brain": 600,
    "jarvis_stocks_v2":     300,
    "jarvis_beast":         600,
    "jarvis_congress":      600,
    "jarvis_level5":        600,
    "jarvis_cascade":       600,
    "lenny_predictions":    300,
    "lenny_trader_bot":     300,
}
HB_DEFAULT = 600

# Bots that write to bot_heartbeats; others show "—" in that column.
HB_BOTS = {
    "jarvis_beast", "jarvis_briefing", "jarvis_cascade", "jarvis_congress",
    "jarvis_intelligence", "jarvis_level5", "jarvis_master", "jarvis_options_brain",
    "jarvis_stocks_v2", "lenny_predictions", "lenny_trader_bot",
}

# Output-freshness spec: (source_type, source_arg, ttl_seconds)
# source_type: brain_key | log_mtime | json_mtime | db_trump | db_intel | db_pred
#
# Correction (3): market-hours-active bots (cascade, lenny_trader_bot) use 7200s
# (2h) so silence during an active session surfaces as STALE, not hidden behind 24h.
OUTPUT = {
    "jarvis_trump_monitor": ("db_trump",   None,                                        86400),
    "jarvis_master":        ("brain_key",  "btc_signal",                                  300),
    "jarvis_api":           ("log_mtime",  f"{JARVIS_DIR}/jarvis_api.log",              86400),  # server: logs only on errors; liveness is the real check
    "jarvis_briefing":      ("log_mtime",  f"{JARVIS_DIR}/jarvis_briefing.log",          7200),
    "jarvis_intelligence":  ("db_intel",   None,                                         86400),
    "jarvis_options_brain": ("json_mtime", f"{JARVIS_DIR}/jarvis_options_brain.json",    3600),
    "jarvis_stocks_v2":     ("log_mtime",  f"{JARVIS_DIR}/jarvis_stocks_v2.log",          120),  # logs "Market closed" every 60s
    "jarvis_beast":         ("log_mtime",  f"{JARVIS_DIR}/jarvis_beast.log",             3600),
    "jarvis_congress":      ("brain_key",  "congress_hot_tickers",                      86400),
    "jarvis_level5":        ("log_mtime",  f"{JARVIS_DIR}/jarvis_level5.log",            3600),
    "jarvis_cascade":       ("brain_key",  "cascade_l0_fired",                          86400),  # event-driven (drawdown only)
    "lenny_predictions":    ("json_mtime", f"{JARVIS_DIR}/btc_memory.json",              7200),  # writes btc_memory.json, not DB
    "jarvis_futures":       ("brain_key",  "futures_best_signal",                       14400),
    "lenny_trader_bot":     ("log_mtime",  f"{JARVIS_DIR}/lenny_trader_bot.log",        86400),  # command-driven: only logs on startup/command
    "jarvis_trader":        ("log_mtime",  f"{JARVIS_DIR}/jarvis_trader.log",            3600),
    "jarvis_premium":       ("log_mtime",  f"{JARVIS_DIR}/jarvis_premium.log",           3600),
    "options_grader":       ("log_mtime",  f"{JARVIS_DIR}/options_grader.log",           3600),
    "btc_ticker":           ("log_mtime",  f"{JARVIS_DIR}/jarvis_btc.log",              4200),  # hourly bot; log up to 60min old
    "jarvis_health":        ("log_mtime",  f"{JARVIS_DIR}/jarvis_health.log",          25200),  # 6h cron; allow 7h TTL
}

# Brain key TTLs (seconds).  Keys not listed default to BRAIN_TTL_DEFAULT.
BRAIN_TTLS = {
    "btc_price":            300,
    "btc_signal":           300,
    "btc_rsi":              300,
    "btc_macd":             300,
    "market_mood":          3600,   # intelligence writes every 30min NEWS_POLL; 1h TTL
    "fear_greed":           3600,
    "equity_fear_greed":    3600,
    "macro_regime":         7200,
    "macro_beast_action":   7200,
    "macro_focus":          7200,
    "macro_size_mult":      7200,
    "macro_defensive":      7200,
    "regime_updated":       3600,
    "vix":                  3600,
    "yield_10yr":           3600,
    "hot_tickers":          3600,
    "market_context":       3600,
    "futures_best_signal":  14400,
    "btc_trend_4h":         14400,
    "btc_spy_corr":         14400,
    "congress_hot_tickers": 86400,
    "briefing_sent_date":   86400,
    "earnings_updated":     86400,
}
BRAIN_TTL_DEFAULT = 86400

# Reference value for the CNN equity F&G contamination divergence test.
# _fetch_cnn_fg() tries to pull this live; EXPECTED_CNN_FG is the fallback.
# Update EXPECTED_CNN_FG when the live endpoint has been unreachable for an
# extended period so the divergence test stays calibrated.
EXPECTED_CNN_FG = 34

# ── Formatting helpers ────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc)


def _parse_iso(s):
    if not s:
        return None
    s = s.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _age(dt):
    if dt is None:
        return None
    return (_now() - dt).total_seconds()


def _fmt_age(s):
    if s is None:
        return "N/A"
    s = max(0.0, s)
    h, rem = divmod(int(s), 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{sec:02d}s"
    return f"{sec}s"


def _fmt_et(dt):
    if dt is None:
        return "never"
    return dt.astimezone(ET).strftime("%Y-%m-%d %H:%M ET")


def _trunc(s, n=38):
    s = str(s)
    return s[:n] + "…" if len(s) > n else s


def _pad(text, width):
    """Pad `text` to `width` visible characters, ignoring ANSI escape codes."""
    import re
    visible = len(re.sub(r"\033\[[0-9;]*m", "", text))
    return text + " " * max(0, width - visible)


def _colored(text, color):
    return f"{color}{text}{RESET}"


def _ok(text):   return f"{GREEN}✓ {text}{RESET}"
def _bad(text):  return f"{RED}✗ {text}{RESET}"
def _warn(text): return f"{YELLOW}⚠ {text}{RESET}"


def _section(title):
    bar = "─" * 72
    print(f"\n{BOLD}{bar}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{bar}{RESET}")


# ── Data access ───────────────────────────────────────────────────────────────

def _fetch_cnn_fg():
    """Fetch live CNN Fear & Greed equity score from their public graphdata API.
    Returns an int, or None on any network/parse failure (caller uses EXPECTED_CNN_FG).
    """
    try:
        import urllib.request
        url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
        return int(round(data["fear_and_greed"]["score"]))
    except Exception:
        return None


def _get_running_scripts():
    """Return set of .py basenames running as python processes (exact argv match)."""
    running = set()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                argv = [a for a in f.read().split(b"\x00") if a]
        except OSError:
            continue
        if not argv:
            continue
        exe = os.path.basename(argv[0].decode("utf-8", "replace"))
        if not exe.startswith("python"):
            continue
        for arg in argv[1:]:
            name = os.path.basename(arg.decode("utf-8", "replace"))
            if name.endswith(".py"):
                running.add(name)
    return running


def _db():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _brain_key(conn, key):
    row = conn.execute(
        "SELECT value, updated_at FROM brain WHERE key=?", (key,)
    ).fetchone()
    if not row:
        return None, None
    return row["value"], _parse_iso(row["updated_at"])


def _output_age(conn, src_type, src_arg):
    """Return datetime of last productive output, or None."""
    if src_type in ("log_mtime", "json_mtime"):
        try:
            return datetime.fromtimestamp(os.path.getmtime(src_arg), tz=timezone.utc)
        except OSError:
            return None
    if src_type == "brain_key":
        _, dt = _brain_key(conn, src_arg)
        return dt
    if src_type == "db_trump":
        row = conn.execute("SELECT MAX(logged_at) AS t FROM trump_signals").fetchone()
        return _parse_iso(row["t"]) if row else None
    if src_type == "db_intel":
        row = conn.execute("SELECT MAX(ts) AS t FROM intel_signals").fetchone()
        return _parse_iso(row["t"]) if row else None
    if src_type == "db_pred":
        row = conn.execute("SELECT MAX(ts) AS t FROM predictions").fetchone()
        return _parse_iso(row["t"]) if row else None
    return None


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — BOT HEALTH
# ═════════════════════════════════════════════════════════════════════════════

def section1(conn):
    _section("SECTION 1 — Bot Health  (19 bots × 3 independent checks)")

    running = _get_running_scripts()
    hb_rows = conn.execute("SELECT bot_name, last_seen FROM bot_heartbeats").fetchall()
    hb_map  = {r["bot_name"]: _parse_iso(r["last_seen"]) for r in hb_rows}

    C_BOT = 26; C_LV = 5; C_HB = 10; C_OUT = 10

    print(f"\n{BOLD}{'BOT':<{C_BOT}} {'LIVE':<{C_LV}} {'HB_AGE':<{C_HB}} "
          f"{'OUT_AGE':<{C_OUT}} VERDICT{RESET}")
    print("─" * 80)

    failures = []   # list of (bot_name, [failing_check_names])

    for bot in BOTS:
        name  = bot["name"]
        fname = bot["file"]

        # — Check 1: liveness —
        live = fname in running

        # — Check 2: heartbeat freshness —
        hb_ok  = None   # None = not applicable (bot doesn't write heartbeats)
        hb_age = None
        if name in HB_BOTS:
            hb_dt  = hb_map.get(name)
            hb_age = _age(hb_dt)
            limit  = HB_TIMEOUT.get(name, HB_DEFAULT)
            hb_ok  = (hb_age is not None) and (hb_age <= limit)

        # — Check 3: output freshness —
        src_type, src_arg, ttl = OUTPUT[name]
        out_dt  = _output_age(conn, src_type, src_arg)
        out_age = _age(out_dt)
        out_ok  = (out_age is not None) and (out_age <= ttl)

        # — Verdict —
        checks_failed = []
        if not live:
            checks_failed.append("DEAD")
        if hb_ok is False:
            checks_failed.append(f"stale_hb({_fmt_age(hb_age)})")
        if not out_ok:
            label = src_arg if src_type == "brain_key" else src_type
            checks_failed.append(f"stale_out({label})")

        if checks_failed:
            failures.append((name, checks_failed))

        # — Render with ANSI-aware padding —
        lv_col = _colored("YES", GREEN) if live else _colored("NO", RED)

        if name not in HB_BOTS:
            hb_col = "—"
        elif hb_ok is None:
            hb_col = _colored("NEVER", YELLOW)
        elif hb_ok:
            hb_col = _colored(_fmt_age(hb_age), GREEN)
        else:
            hb_col = _colored(_fmt_age(hb_age), RED)

        if out_age is None:
            out_col = _colored("N/A", YELLOW)
        elif out_ok:
            out_col = _colored(_fmt_age(out_age), GREEN)
        else:
            out_col = _colored(_fmt_age(out_age), RED)

        if checks_failed:
            verdict = _colored("RED — " + ", ".join(checks_failed), RED)
        else:
            verdict = _colored("GREEN", GREEN)

        print(
            _pad(name,    C_BOT) + " " +
            _pad(lv_col,  C_LV)  + " " +
            _pad(hb_col,  C_HB)  + " " +
            _pad(out_col, C_OUT) + " " +
            verdict
        )

    print()
    if not failures:
        print(_ok("All 19 bots GREEN"))
    else:
        for name, checks in failures:
            print(_bad(f"{name}: {', '.join(checks)}"))

    return failures


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — DB ↔ JSON CONSISTENCY
# ═════════════════════════════════════════════════════════════════════════════

def section2(conn):
    _section("SECTION 2 — DB ↔ JSON Consistency")
    issues = []

    def count_check(label, json_count, db_count):
        delta = json_count - db_count
        if delta == 0:
            print(_ok(f"{label}: JSON={json_count}  DB={db_count}  (in sync)"))
        else:
            sign = "+" if delta > 0 else ""
            msg  = f"{label}: JSON={json_count}  DB={db_count}  (drift {sign}{delta})"
            print(_warn(msg))
            issues.append(msg)

    kb = _load_json(f"{JARVIS_DIR}/kalshi_brain.json") or {}
    count_check(
        "kalshi_brain.json[bets]  vs  kalshi_bets DB",
        len(kb.get("bets", [])),
        conn.execute("SELECT COUNT(*) FROM kalshi_bets").fetchone()[0],
    )

    om = _load_json(f"{JARVIS_DIR}/options_memory.json") or {}
    count_check(
        "options_memory.json[trades]  vs  options_trades DB",
        len(om.get("trades", [])),
        conn.execute("SELECT COUNT(*) FROM options_trades").fetchone()[0],
    )

    cb = _load_json(f"{JARVIS_DIR}/jarvis_central_brain.json") or {}
    count_check(
        "central_brain.json keys  vs  brain table rows",
        len(cb),
        conn.execute("SELECT COUNT(*) FROM brain").fetchone()[0],
    )

    # Spot-check values that should be identical in both stores
    for key in ("btc_price", "macro_regime"):
        json_val = cb.get(key)
        db_val, _ = _brain_key(conn, key)
        json_s = str(json_val) if json_val is not None else "None"
        db_s   = db_val        if db_val   is not None else "None"
        try:
            match = abs(float(json_s) - float(db_s)) < 2.0
        except (ValueError, TypeError):
            match = json_s.strip('"') == db_s.strip('"')
        if match:
            print(_ok(f"spot {key}: JSON={_trunc(json_s)}  ==  DB={_trunc(db_s)}"))
        else:
            msg = f"spot {key}: JSON={_trunc(json_s)}  ≠  DB={_trunc(db_s)}"
            print(_warn(msg))
            issues.append(msg)

    # kalshi win-rate: JSON claim vs recomputed from DB
    json_wr  = cb.get("kalshi_win_rate")
    rows     = conn.execute(
        "SELECT result FROM kalshi_bets WHERE result IN ('WIN','LOSS')"
    ).fetchall()
    n_graded = len(rows)
    n_wins   = sum(1 for r in rows if r["result"] == "WIN")
    db_wr    = round(n_wins / n_graded * 100, 1) if n_graded else 0.0
    try:
        ok_wr = abs(float(json_wr) - db_wr) < 2.0
    except (TypeError, ValueError):
        ok_wr = False
    if ok_wr:
        print(_ok(f"kalshi_win_rate: JSON={json_wr}%  DB-computed={db_wr}%"))
    else:
        msg = f"kalshi_win_rate: JSON={json_wr}%  DB-computed={db_wr}%"
        print(_warn(msg))
        issues.append(msg)

    return issues


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — BRAIN STALENESS SWEEP
# ═════════════════════════════════════════════════════════════════════════════

def section3(conn):
    _section("SECTION 3 — Brain Key Staleness")

    stale_keys = []

    # ── Spotlight: equity F&G contamination check ─────────────────────────────
    #
    # Correction (1) & (2):
    #   equity_fear_greed  = CNN Fear & Greed equity index (~34)  ← TRUSTED SOURCE
    #   fear_greed         = crypto F&G index (~13)               ← CONTAMINANT
    #
    #   RED if equity_fear_greed.value == fear_greed: that means the crypto
    #   index bled into the equity key — this is the contamination signature.
    # ─────────────────────────────────────────────────────────────────────────
    print(f"\n  {BOLD}[ Spotlight: equity F&G contamination check ]{RESET}")

    efg_val, efg_dt = _brain_key(conn, "equity_fear_greed")
    efg_age         = _age(efg_dt)
    try:
        efg_obj       = json.loads(efg_val) if efg_val else {}
        efg_num       = efg_obj.get("value")
        efg_ts        = _parse_iso(efg_obj.get("ts"))
        efg_inner_age = _age(efg_ts)
    except Exception:
        efg_num       = None
        efg_inner_age = None

    fg_val, fg_dt = _brain_key(conn, "fear_greed")
    fg_age        = _age(fg_dt)
    try:
        crypto_fg = int(float(fg_val)) if fg_val is not None else None
    except (ValueError, TypeError):
        crypto_fg = None

    # Contamination: equity key holds the same integer as the crypto index.
    # Fetch the live CNN equity F&G as the divergence reference.
    # Falls back to EXPECTED_CNN_FG if unreachable — update that constant when
    # the live endpoint has been down for an extended period.
    live_cnn     = _fetch_cnn_fg()
    expected_cnn = live_cnn if live_cnn is not None else EXPECTED_CNN_FG
    cnn_src      = "live CNN" if live_cnn is not None else f"fallback CNN"

    # Contamination signature: equity value orbiting the crypto index (within ±2)
    # while diverging from the CNN equity reference (by >5 points).
    # Exact equality misses off-by-one events (equity=13, crypto=12 this morning).
    if efg_num is not None and crypto_fg is not None:
        equity_int   = int(float(efg_num))
        near_crypto  = abs(equity_int - crypto_fg) <= 2
        far_from_cnn = abs(equity_int - expected_cnn) > 5
        contaminated = near_crypto and far_from_cnn
    else:
        contaminated = False

    if contaminated:
        print(_bad(
            f"equity_fear_greed (CNN equity — TRUSTED): value={efg_num}  "
            f"within ±2 of crypto({crypto_fg})  AND  >5 from "
            f"{cnn_src}({expected_cnn})  ← CONTAMINATION DETECTED "
            f"(equity tracking crypto, diverged from CNN)"
        ))
        stale_keys.append("equity_fear_greed_CONTAMINATED")
    else:
        efg_fresh = efg_inner_age is not None and efg_inner_age <= 7200
        cnn_label = f"{cnn_src}({expected_cnn})"
        if efg_fresh:
            print(_ok(
                f"equity_fear_greed (CNN equity — trusted): value={efg_num}  "
                f"inner_ts age={_fmt_age(efg_inner_age)}  ref={cnn_label}  "
                f"not within ±2 of crypto({crypto_fg})  — clean"
            ))
        else:
            print(_warn(
                f"equity_fear_greed (CNN equity — trusted): value={efg_num}  "
                f"inner_ts stale ({_fmt_age(efg_inner_age)})  ref={cnn_label}  "
                f"not within ±2 of crypto({crypto_fg})"
            ))

    # Crypto F&G: show for reference only — it is the contaminant to monitor.
    if crypto_fg is not None:
        fg_fresh = fg_age is not None and fg_age <= 3600
        fn = _ok if fg_fresh else _warn
        print(fn(
            f"fear_greed (CRYPTO — contaminant ref, must ≠ equity): "
            f"value={crypto_fg}  age={_fmt_age(fg_age)}"
        ))

    # macro_regime staleness spotlight
    mr_val, mr_dt = _brain_key(conn, "macro_regime")
    mr_age        = _age(mr_dt)
    if mr_age is None or mr_age > 14400:
        print(_warn(f"macro_regime: value={mr_val}  age={_fmt_age(mr_age)}  ← possibly FROZEN"))
    else:
        print(_ok(f"macro_regime: value={mr_val}  age={_fmt_age(mr_age)}"))

    # ── Full sweep ────────────────────────────────────────────────────────────
    print(f"\n  {BOLD}[ All brain keys ]{RESET}")
    print(f"\n  {'KEY':<28} {'VALUE':<22} {'AGE':<10} {'TTL':<8} STATUS")
    print("  " + "─" * 76)

    rows = conn.execute(
        "SELECT key, value, updated_at FROM brain ORDER BY key"
    ).fetchall()

    for row in rows:
        key     = row["key"]
        val     = row["value"] or ""
        updated = _parse_iso(row["updated_at"])
        ttl     = BRAIN_TTLS.get(key, BRAIN_TTL_DEFAULT)
        age     = _age(updated)

        if updated is None or age is None:
            status = _colored("FROZEN", RED)
            stale_keys.append(key)
        elif age > ttl * 2:
            status = _colored("STALE", RED)
            stale_keys.append(key)
        elif age > ttl:
            status = _colored("AGING", YELLOW)
        else:
            status = _colored("FRESH", GREEN)

        print(f"  {key:<28} {_trunc(val, 20):<22} {_fmt_age(age):<10} "
              f"{_fmt_age(ttl):<8} {status}")

    return stale_keys


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4 — GRADER STATUS
# ═════════════════════════════════════════════════════════════════════════════

def section4(conn):
    _section("SECTION 4 — Grader Status")

    # — kalshi_grader: last graded_at in kalshi_bets —
    row            = conn.execute("SELECT MAX(graded_at) AS g FROM kalshi_bets").fetchone()
    last_graded_dt = _parse_iso(row["g"]) if row else None
    graded_age     = _age(last_graded_dt)
    ungraded       = conn.execute(
        "SELECT COUNT(*) FROM kalshi_bets WHERE result IS NULL"
    ).fetchone()[0]
    total_bets = conn.execute("SELECT COUNT(*) FROM kalshi_bets").fetchone()[0]

    if last_graded_dt is None:
        print(_bad(
            f"kalshi_grader: NEVER GRADED — {total_bets} bets total, "
            f"{ungraded} ungraded"
        ))
    elif graded_age > 86400:
        print(_bad(
            f"kalshi_grader: FROZEN-as-of {_fmt_et(last_graded_dt)}  "
            f"({_fmt_age(graded_age)} ago)  {ungraded}/{total_bets} ungraded"
        ))
    elif graded_age > 3600:
        print(_warn(
            f"kalshi_grader: last graded {_fmt_age(graded_age)} ago  "
            f"({_fmt_et(last_graded_dt)})  {ungraded}/{total_bets} ungraded"
        ))
    else:
        print(_ok(
            f"kalshi_grader: FRESH — last graded {_fmt_age(graded_age)} ago  "
            f"{ungraded}/{total_bets} ungraded"
        ))

    # — options_grader: last closed paper trade —
    paper  = _load_json(f"{JARVIS_DIR}/paper_trades.json") or {}
    trades = paper.get("trades", [])
    closed = [t for t in trades if t.get("status") == "paper_closed"]
    open_t = [t for t in trades if t.get("status") == "paper_open"]

    if closed:
        last_t    = max(
            closed,
            key=lambda t: (t.get("exit_date") or "", t.get("exit_time") or ""),
        )
        exit_date = last_t.get("exit_date", "unknown")
        exit_time = last_t.get("exit_time", "")
        try:
            exit_dt    = datetime.strptime(exit_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            closed_age = _age(exit_dt)
        except ValueError:
            closed_age = None
        if closed_age is not None and closed_age > 86400:
            fn    = _warn
            stamp = f"FROZEN-as-of {exit_date} {exit_time}"
        else:
            fn    = _ok
            stamp = f"last closed {exit_date} {exit_time}"
        print(fn(
            f"options_grader: {stamp}  "
            f"{len(closed)} closed / {len(open_t)} open"
        ))
    else:
        print(_warn(f"options_grader: no closed trades  {len(open_t)} open"))

    # — kalshi_system2_predictions (separate grader table) —
    sys2 = conn.execute(
        "SELECT COUNT(*) FROM kalshi_system2_predictions"
    ).fetchone()[0]
    if sys2 == 0:
        print(_warn("kalshi_system2_predictions: 0 rows (table never populated)"))
    else:
        g2   = conn.execute(
            "SELECT COUNT(*) FROM kalshi_system2_predictions "
            "WHERE actual_outcome IS NOT NULL"
        ).fetchone()[0]
        last = conn.execute(
            "SELECT MAX(grade_timestamp) AS t FROM kalshi_system2_predictions"
        ).fetchone()["t"]
        print(_ok(
            f"kalshi_system2_predictions: {sys2} rows, {g2} graded, "
            f"last={_fmt_et(_parse_iso(last))}"
        ))

    return graded_age


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5 — SAFE-TO-REARM VERDICT
# ═════════════════════════════════════════════════════════════════════════════

def section5(bot_failures, brain_stale, grader_age):
    _section("SECTION 5 — Safe-to-Rearm Verdict")

    unsafe = []

    # Any bot not running
    dead = [name for name, checks in bot_failures if "DEAD" in checks]
    if dead:
        unsafe.append(f"{len(dead)} bot(s) not running: {', '.join(dead)}")

    # Critical bot heartbeat stale
    critical_names = {b["name"] for b in BOTS if b["critical"]}
    for name, checks in bot_failures:
        if name in critical_names and any("stale_hb" in c for c in checks):
            unsafe.append(f"critical bot stale heartbeat: {name}")

    # kalshi_grader not grading recently (>24h = win-rate / P&L are FROZEN)
    if grader_age is None or grader_age > 86400:
        age_str = _fmt_age(grader_age) if grader_age is not None else "never"
        unsafe.append(f"kalshi_grader last graded {age_str} ago (>24h — stats frozen)")

    # Short-TTL brain keys that are >2× stale gate active trading decisions
    short_stale = [k for k in brain_stale if BRAIN_TTLS.get(k, BRAIN_TTL_DEFAULT) <= 3600]
    if short_stale:
        unsafe.append(f"short-TTL brain keys stale: {', '.join(short_stale)}")

    print()
    if unsafe:
        print(f"{RED}{BOLD}NOT SAFE TO REARM{RESET}")
        for reason in unsafe:
            print(f"  {RED}✗{RESET} {reason}")
    else:
        print(f"{GREEN}{BOLD}SAFE TO REARM{RESET} — all checks passed")

    return len(unsafe) == 0


# ═════════════════════════════════════════════════════════════════════════════

def main():
    now_et = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    print(f"{BOLD}{'═' * 72}{RESET}")
    print(f"{BOLD}  JARVIS DOCTOR — {now_et}{RESET}")
    print(f"{BOLD}{'═' * 72}{RESET}")

    conn = _db()
    try:
        bot_failures = section1(conn)
        section2(conn)
        brain_stale  = section3(conn)
        grader_age   = section4(conn)
        safe         = section5(bot_failures, brain_stale, grader_age)
    finally:
        conn.close()

    sys.exit(0 if safe else 1)


if __name__ == "__main__":
    main()
