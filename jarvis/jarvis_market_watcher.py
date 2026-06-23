#!/usr/bin/env python3
"""jarvis_market_watcher.py — monitors 6 tickers for VWAP/EMA9 cross,
volume spikes, key-level breaks. Alerts via Jarvis_Stocks_Bot (TG_TOKEN_INTEL).
Daemon: flock singleton, polls every 2 min during RTH only.
Run: python3 jarvis_market_watcher.py [--once]"""
import sys, os, time, json, fcntl
import urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
import jarvis_secrets as s

TICKERS = ["SPY", "QQQ", "TSLA", "NVDA", "AAPL", "SPCX"]
THIN = {"SPCX"}                       # lower-confidence liquidity tag
POLL_SEC = 120                        # 2 min
VOL_SPIKE_MULT = 3.0                  # current vol > 3x trailing 20-bar avg
COOLDOWN_MIN = 30                     # per ticker per condition
EMA_LEN = 9
CHAT = "7534553840"
TOK = s.TG_TOKEN_INTEL                # Jarvis_Stocks_Bot
DATA = "https://data.alpaca.markets/v2/stocks"
HDR = {"APCA-API-KEY-ID": s.ALPACA_PAPER_KEY,
       "APCA-API-SECRET-KEY": s.ALPACA_PAPER_SECRET}

# ---- state (in-memory; baseline on first poll, no alert) --------------------
last_side = {}        # (ticker,'vwap'|'ema'): 'above'|'below'
last_alert = {}       # (ticker,cond): epoch seconds
day_levels = {}       # ticker: {'pdh','pdl','pmh','pml'}  (prior-day + premarket)
levels_day = {None}   # marker for which date day_levels was built for

def tg(text):
    url = f"https://api.telegram.org/bot{TOK}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": CHAT, "text": text}).encode()
    try:
        urllib.request.urlopen(url, data=data, timeout=20)
    except Exception:
        pass

def _get(url):
    req = urllib.request.Request(url, headers=HDR)
    return json.load(urllib.request.urlopen(req, timeout=20))

def now_et():
    return datetime.now(timezone.utc) - timedelta(hours=4)  # ET (EDT) approx

def is_rth(dt):
    if dt.weekday() >= 5:   # Sat/Sun
        return False
    t = dt.hour * 60 + dt.minute
    return 570 <= t < 960   # 9:30 (570) .. 16:00 (960)

def cooled(ticker, cond):
    k = (ticker, cond)
    last = last_alert.get(k, 0)
    if time.time() - last >= COOLDOWN_MIN * 60:
        last_alert[k] = time.time()
        return True
    return False

def ema(values, length):
    k = 2 / (length + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e

def fetch_today_1min(ticker):
    et = now_et()
    start = et.replace(hour=4, minute=0, second=0, microsecond=0)
    start_utc = (start + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (f"{DATA}/{ticker}/bars?timeframe=1Min&start={start_utc}"
           f"&limit=1000&feed=iex")
    d = _get(url)
    return d.get("bars") or []

def build_levels(ticker):
    # prior day high/low via daily bars; premarket high/low from today's pre-9:30 bars
    try:
        et = now_et()
        start = (et - timedelta(days=7)).strftime("%Y-%m-%d")
        today = et.strftime("%Y-%m-%d")
        dd = _get(f"{DATA}/{ticker}/bars?timeframe=1Day&start={start}&limit=10&feed=iex")
        dbars = dd.get("bars") or []
        prior = [b for b in dbars if b["t"][:10] < today]   # exclude today's partial bar
        pdh = pdl = None
        if prior:
            pdh, pdl = prior[-1]["h"], prior[-1]["l"]
        bars = fetch_today_1min(ticker)
        pm = [b for b in bars if b["t"][11:16] < "13:30"]  # pre-9:30 ET = pre-13:30 UTC
        pmh = max((b["h"] for b in pm), default=None)
        pml = min((b["l"] for b in pm), default=None)
        return {"pdh": pdh, "pdl": pdl, "pmh": pmh, "pml": pml}
    except Exception:
        return {"pdh": None, "pdl": None, "pmh": None, "pml": None}

def vwap_of(bars):
    num = den = 0.0
    for b in bars:
        tp = (b["h"] + b["l"] + b["c"]) / 3
        num += tp * b["v"]; den += b["v"]
    return num / den if den else None

def check(ticker, first_pass):
    bars = fetch_today_1min(ticker)
    rth = [b for b in bars if b["t"][11:16] >= "13:30"]   # RTH bars only (>=9:30 ET)
    if len(rth) < EMA_LEN + 1:
        return
    closes = [b["c"] for b in rth]
    price = closes[-1]
    tag = "  (SPCX — thin liquidity, lower confidence)" if ticker in THIN else ""

    # VWAP (daily reset = RTH cumulative)
    vw = vwap_of(rth)
    if vw:
        side = "above" if price > vw else "below"
        prev = last_side.get((ticker, "vwap"))
        if prev and prev != side and not first_pass and cooled(ticker, "vwap"):
            tg(f"📊 {ticker} crossed {side.upper()} VWAP ({vw:.2f}) px {price:.2f}{tag}")
        last_side[(ticker, "vwap")] = side

    # EMA9 cross
    e = ema(closes, EMA_LEN)
    side = "above" if price > e else "below"
    prev = last_side.get((ticker, "ema"))
    if prev and prev != side and not first_pass and cooled(ticker, "ema"):
        tg(f"📈 {ticker} crossed {side.upper()} EMA9 ({e:.2f}) px {price:.2f}{tag}")
    last_side[(ticker, "ema")] = side

    # Volume spike: current bar vol > 3x trailing 20-bar avg
    vols = [b["v"] for b in rth]
    if len(vols) >= 21:
        trail = sum(vols[-21:-1]) / 20
        if trail > 0 and vols[-1] > VOL_SPIKE_MULT * trail and not first_pass and cooled(ticker, "vol"):
            tg(f"🔊 {ticker} volume spike: {vols[-1]:,} vs {trail:,.0f} avg ({vols[-1]/trail:.1f}x) px {price:.2f}{tag}")

    # Key-level breaks (prior day + premarket high/low)
    lv = day_levels.get(ticker, {})
    for name, key in [("PDH","pdh"),("PDL","pdl"),("PM-High","pmh"),("PM-Low","pml")]:
        level = lv.get(key)
        if level is None:
            continue
        broke_up = name.endswith("H") and price > level
        broke_dn = name.endswith("L") and price < level
        if (broke_up or broke_dn) and not first_pass and cooled(ticker, f"lvl_{key}"):
            d = "ABOVE" if broke_up else "BELOW"
            tg(f"🎯 {ticker} broke {d} {name} ({level:.2f}) px {price:.2f}{tag}")

def run_once(first_pass=False):
    et = now_et()
    today = et.strftime("%Y-%m-%d")
    if today not in levels_day:
        levels_day.clear(); levels_day.add(today)
        for t in TICKERS:
            day_levels[t] = build_levels(t)
    for t in TICKERS:
        try:
            check(t, first_pass)
        except Exception as e:
            print(f"{t} check error: {e}")

# ---- singleton + loop ------------------------------------------------------
_LOCK = None
def acquire_singleton():
    global _LOCK
    _LOCK = open("/root/jarvis/market_watcher.lock", "w")
    try:
        fcntl.flock(_LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another market_watcher holds the lock -- exiting"); sys.exit(0)
    open("/root/jarvis/market_watcher.pid", "w").write(str(os.getpid()))

def main():
    if "--once" in sys.argv:
        run_once(first_pass=True)
        print("--once complete: baseline established, no alerts sent")
        return
    acquire_singleton()
    tg("📡 market watcher online: SPY QQQ TSLA NVDA AAPL SPCX")
    first = True
    while True:
        try:
            if is_rth(now_et()):
                run_once(first_pass=first)
                first = False
            else:
                first = True   # reset baseline for next session
        except Exception as e:
            print(f"loop error: {e}")
        time.sleep(POLL_SEC)

if __name__ == "__main__":
    main()
