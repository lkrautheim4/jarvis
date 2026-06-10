#!/usr/bin/env python3
"""
JARVIS STOCKS — Intelligent Stock Market Agent
Smart Memory | Always Learning | Adaptive Brain
Scans 30 tickers | Swing + Momentum + Breakout trades
Brain learns: time-of-day, sector, setup type, volume, regime
"""

import json, time, math, requests, os
from datetime import datetime, timedelta

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
ALPACA_KEY    = "PKTHANGUNVFDSLLR3VXPETXRQF"
ALPACA_SECRET = "GRTDDfkCGWbZMoNSWms6uJSGvw72rHaAk1N1fvLi8EAP"
ALPACA_BASE   = "https://paper-api.alpaca.markets"
TELEGRAM_TOKEN= __import__("jarvis_secrets").TG_TOKEN_INTEL
TELEGRAM_CHAT = "7534553840"

MEMORY_FILE   = "jarvis_stocks_brain.json"

# Trade sizing
MICRO_SIZE    = 200    # quick momentum trade
SWING_SIZE    = 500    # multi-hour swing
BREAKOUT_SIZE = 400    # breakout/squeeze play
MAX_TRADE     = 600    # hard cap per trade
MAX_POSITIONS = 5      # max open positions at once
MAX_PER_SECTOR= 2      # max positions per sector

# Strategy thresholds
BUY_RSI_STRONG  = 28   # extreme oversold — full swing
BUY_RSI_NORMAL  = 35   # oversold — normal swing
BUY_RSI_MICRO   = 45   # mild dip — micro only
SELL_RSI_HIGH   = 62   # start looking to sell
PROFIT_MICRO    = 0.5  # micro target %
PROFIT_SWING    = 2.0  # swing target %
PROFIT_BREAKOUT = 1.5  # breakout target %
STOP_LOSS       = 1.2  # stop loss %
TRAIL_STEP      = 0.3  # trailing stop step %
MIN_HOLD_MICRO  = 8    # min hold minutes
MIN_HOLD_SWING  = 30   # min hold minutes
MIN_VOLUME_MULT = 1.3  # min volume vs average to confirm signal

# Timing
SCAN_INTERVAL   = 60   # seconds between scans
REPORT_HOUR     = 7    # morning report hour

# ─────────────────────────────────────────
# WATCHLIST — 30 tickers across sectors
# ─────────────────────────────────────────
WATCHLIST = {
    # Tech
    "NVDA":  "tech",
    "AMD":   "tech",
    "MSFT":  "tech",
    "AAPL":  "tech",
    "META":  "tech",
    "GOOGL": "tech",
    # Finance
    "JPM":   "finance",
    "BAC":   "finance",
    "GS":    "finance",
    "COIN":  "finance",
    # Energy
    "XOM":   "energy",
    "CVX":   "energy",
    "OXY":   "energy",
    # ETFs / Index
    "SPY":   "etf",
    "QQQ":   "etf",
    "IWM":   "etf",
    "SOXS":  "etf",
    "TQQQ":  "etf",
    # EV / Auto
    "TSLA":  "auto",
    "RIVN":  "auto",
    "F":     "auto",
    # Healthcare
    "UNH":   "health",
    "JNJ":   "health",
    "PFE":   "health",
    # Consumer
    "AMZN":  "consumer",
    "WMT":   "consumer",
    "COST":  "consumer",
    # Misc momentum
    "PLTR":  "tech",
    "MSTR":  "tech",
    "HOOD":  "finance",
}

import logging
logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('JARVIS-STOCKS')

# ─────────────────────────────────────────
# SMART BRAIN SYSTEM
# ─────────────────────────────────────────
def load_brain():
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE,'r') as f:
                brain = json.load(f)
                log.info(f"Brain loaded: {brain.get('total_trades',0)} trades, WR: {_quick_wr(brain)}")
                return brain
    except Exception as e:
        log.error(f"Brain load error: {e}")
    return _fresh_brain()

def _fresh_brain():
    return {
        "trades": [],
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "total_pnl": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
        # Learning buckets
        "tickers": {},        # per-ticker win rates
        "sectors": {},        # per-sector win rates
        "setups": {},         # setup type win rates (swing/micro/breakout)
        "regimes": {},        # market regime win rates
        "rsi_zones": {},      # RSI zone win rates
        "hours": {},          # hour of day win rates (9,10,11...)
        "days": {},           # day of week win rates
        "hold_times": {},     # hold duration win rates
        "patterns": {},       # candlestick pattern win rates
        "volume_zones": {},   # volume multiplier zones
        "gap_types": {},      # gap up/down/flat
        "macd_states": {},    # macd above/below zero
        # Adaptive parameters
        "size_multiplier": 1.0,
        "best_tickers": [],
        "worst_tickers": [],
        "best_sectors": [],
        "worst_sectors": [],
        "best_hour": 10,
        "worst_hour": -1,
        "best_setup": "swing",
        "worst_regime": "CHOP",
        "avoid_hours": [],
        "hot_sectors": [],
        # Session tracking
        "session_trades": 0,
        "session_pnl": 0.0,
        "session_wins": 0,
        "last_session_date": "",
        "consecutive_losses": 0,
        "max_consecutive_losses": 0,
        # Market context memory
        "spy_trend": "UNKNOWN",
        "market_regime": "UNKNOWN",
        "vix_level": "UNKNOWN",
        "created": datetime.now().isoformat(),
        "last_updated": datetime.now().isoformat()
    }

def _quick_wr(brain):
    res = brain.get("wins",0) + brain.get("losses",0)
    if res == 0: return "--"
    return f"{brain['wins']/res*100:.0f}%"

def save_brain(b):
    try:
        b["last_updated"] = datetime.now().isoformat()
        with open(MEMORY_FILE,'w') as f: json.dump(b,f,indent=2)
    except Exception as e:
        log.error(f"Brain save: {e}")

def reset_session(brain):
    today = datetime.now().strftime("%Y-%m-%d")
    if brain.get("last_session_date","") != today:
        brain["session_trades"] = 0
        brain["session_pnl"] = 0.0
        brain["session_wins"] = 0
        brain["last_session_date"] = today
        log.info("Brain: New session started")

# ─────────────────────────────────────────
# BUCKET SYSTEM
# ─────────────────────────────────────────
def rsi_zone(v):
    if v>=75: return "EXTREME_OB"
    if v>=65: return "OVERBOUGHT"
    if v>=60: return "HIGH"
    if v>=50: return "BULLISH"
    if v>=40: return "NEUTRAL"
    if v>=30: return "BEARISH"
    if v>=20: return "OVERSOLD"
    return "EXTREME_OS"

def volume_zone(mult):
    if mult>=3.0:  return "EXTREME"
    if mult>=2.0:  return "HIGH"
    if mult>=1.5:  return "ELEVATED"
    if mult>=1.0:  return "NORMAL"
    return "LOW"

def hold_bucket(mins):
    if mins<10:   return "<10min"
    if mins<30:   return "10-30min"
    if mins<60:   return "30-60min"
    if mins<120:  return "1-2hr"
    if mins<240:  return "2-4hr"
    return "4hr+"

def gap_type(gap_pct):
    if gap_pct>1.5:   return "GAP_UP_LARGE"
    if gap_pct>0.3:   return "GAP_UP"
    if gap_pct<-1.5:  return "GAP_DOWN_LARGE"
    if gap_pct<-0.3:  return "GAP_DOWN"
    return "FLAT"

def update_bucket(b, k, won, pnl=0):
    if k not in b: b[k] = {"wins":0,"total":0,"avg_pnl":0.0,"streak":0}
    b[k]["total"] += 1
    if won:
        b[k]["wins"] += 1
        b[k]["streak"] = max(0, b[k].get("streak",0)) + 1
    else:
        b[k]["streak"] = min(0, b[k].get("streak",0)) - 1
    n = b[k]["total"]
    b[k]["avg_pnl"] = round((b[k]["avg_pnl"]*(n-1)+pnl)/n, 3)

def get_wr(b, k, min_t=3):
    if k not in b or b[k]["total"] < min_t: return 0.5, 0
    return b[k]["wins"]/b[k]["total"], b[k]["total"]

def get_streak(b, k):
    if k not in b: return 0
    return b[k].get("streak", 0)

# ─────────────────────────────────────────
# TRADE RECORDING
# ─────────────────────────────────────────
def record_open(brain, ticker, setup, order_id, price, analysis, size):
    trade = {
        "id": order_id,
        "ticker": ticker,
        "sector": WATCHLIST.get(ticker, "unknown"),
        "setup": setup,
        "open_price": price,
        "open_time": datetime.now().isoformat(),
        "size": size,
        "rsi": round(analysis["rsi"], 1),
        "rsi_zone": rsi_zone(analysis["rsi"]),
        "regime": analysis["regime"],
        "volume_mult": round(analysis.get("volume_mult", 1.0), 2),
        "volume_zone": volume_zone(analysis.get("volume_mult", 1.0)),
        "gap_pct": round(analysis.get("gap_pct", 0), 2),
        "gap_type": gap_type(analysis.get("gap_pct", 0)),
        "macd_state": "ABOVE" if analysis.get("macd", 0) > 0 else "BELOW",
        "patterns": [p[0] for p in analysis.get("patterns", [])],
        "hour": datetime.now().hour,
        "day": datetime.now().weekday(),
        "status": "open",
        "trail_stop": round(price*(1-STOP_LOSS/100), 4),
        "trail_high": price,
        "close_price": None,
        "pnl": None,
        "won": None,
        "hold_mins": None,
        "close_reason": None
    }
    brain["trades"].append(trade)
    brain["total_trades"] += 1
    brain["session_trades"] += 1
    save_brain(brain)
    return trade

def record_close(brain, order_id, close_price, reason):
    for t in brain["trades"]:
        if t["id"] == order_id and t["status"] == "open":
            t["close_price"] = close_price
            t["close_time"] = datetime.now().isoformat()
            t["status"] = "closed"
            t["close_reason"] = reason
            open_dt = datetime.fromisoformat(t["open_time"])
            held = int((datetime.now()-open_dt).total_seconds()//60)
            t["hold_mins"] = held
            pnl = (close_price-t["open_price"])/t["open_price"]*t["size"]
            t["pnl"] = round(pnl, 2)
            t["won"] = pnl > 0
            won = t["won"]

            # Update all learning buckets
            update_bucket(brain["tickers"],      t["ticker"],       won, pnl)
            update_bucket(brain["sectors"],      t["sector"],       won, pnl)
            update_bucket(brain["setups"],       t["setup"],        won, pnl)
            update_bucket(brain["regimes"],      t["regime"],       won, pnl)
            update_bucket(brain["rsi_zones"],    t["rsi_zone"],     won, pnl)
            update_bucket(brain["hours"],        str(t["hour"]),    won, pnl)
            update_bucket(brain["days"],         str(t["day"]),     won, pnl)
            update_bucket(brain["hold_times"],   hold_bucket(held), won, pnl)
            update_bucket(brain["volume_zones"], t["volume_zone"],  won, pnl)
            update_bucket(brain["gap_types"],    t["gap_type"],     won, pnl)
            update_bucket(brain["macd_states"],  t["macd_state"],   won, pnl)
            for pat in t["patterns"]:
                update_bucket(brain["patterns"], pat, won, pnl)

            # Update totals
            brain["total_pnl"] = round(brain.get("total_pnl",0)+pnl, 2)
            brain["session_pnl"] = round(brain.get("session_pnl",0)+pnl, 2)

            if won:
                brain["wins"] = brain.get("wins",0)+1
                brain["session_wins"] = brain.get("session_wins",0)+1
                brain["best_trade"] = max(brain.get("best_trade",0), pnl)
                brain["consecutive_losses"] = 0
            else:
                brain["losses"] = brain.get("losses",0)+1
                brain["consecutive_losses"] = brain.get("consecutive_losses",0)+1
                brain["max_consecutive_losses"] = max(
                    brain.get("max_consecutive_losses",0),
                    brain["consecutive_losses"])
                brain["worst_trade"] = min(brain.get("worst_trade",0), pnl)

            _adapt(brain)
            save_brain(brain)
            log.info(f"Brain: {t['ticker']} {t['setup']} P&L ${pnl:+.2f} {'WIN' if won else 'LOSS'} | Streak: {brain['consecutive_losses']} losses")
            return t
    return None

# ─────────────────────────────────────────
# BRAIN ADAPTATION ENGINE
# ─────────────────────────────────────────
def _adapt(brain):
    """The core learning engine — updates all adaptive parameters after every trade"""
    wins = brain.get("wins",0)
    losses = brain.get("losses",0)
    resolved = wins + losses
    if resolved < 3: return

    overall_wr = wins/resolved

    # ── Global size multiplier ──
    if overall_wr > 0.65:
        brain["size_multiplier"] = min(2.5, brain.get("size_multiplier",1.0)+0.1)
    elif overall_wr < 0.40:
        brain["size_multiplier"] = max(0.3, brain.get("size_multiplier",1.0)-0.15)

    # ── Cool down after consecutive losses ──
    consec = brain.get("consecutive_losses",0)
    if consec >= 3:
        brain["size_multiplier"] = max(0.3, brain["size_multiplier"]-0.2)
        log.warning(f"Brain: {consec} consecutive losses — reducing size to {brain['size_multiplier']:.1f}x")

    # ── Best and worst tickers ──
    tickers = brain.get("tickers",{})
    ranked = [(k, v["wins"]/v["total"], v["total"]) for k,v in tickers.items() if v["total"]>=3]
    ranked.sort(key=lambda x: x[1], reverse=True)
    brain["best_tickers"] = [r[0] for r in ranked if r[1]>=0.6][:5]
    brain["worst_tickers"] = [r[0] for r in ranked if r[1]<0.35]

    # ── Best and worst sectors ──
    sectors = brain.get("sectors",{})
    sec_ranked = [(k, v["wins"]/v["total"]) for k,v in sectors.items() if v["total"]>=3]
    sec_ranked.sort(key=lambda x: x[1], reverse=True)
    brain["best_sectors"] = [s[0] for s in sec_ranked if s[1]>=0.6]
    brain["worst_sectors"] = [s[0] for s in sec_ranked if s[1]<0.35]
    brain["hot_sectors"] = [s[0] for s in sec_ranked[:2]]

    # ── Best setup type ──
    setups = brain.get("setups",{})
    best_setup_wr = 0; best_setup = "swing"
    for s,d in setups.items():
        if d["total"]>=3:
            wr = d["wins"]/d["total"]
            if wr > best_setup_wr:
                best_setup_wr = wr; best_setup = s
    brain["best_setup"] = best_setup

    # ── Best and worst hours ──
    hours = brain.get("hours",{})
    hour_ranked = [(int(h), d["wins"]/d["total"], d["total"]) for h,d in hours.items() if d["total"]>=2]
    hour_ranked.sort(key=lambda x: x[1], reverse=True)
    if hour_ranked:
        brain["best_hour"] = hour_ranked[0][0]
        brain["worst_hour"] = hour_ranked[-1][0] if hour_ranked[-1][1]<0.35 else -1
        brain["avoid_hours"] = [h[0] for h in hour_ranked if h[1]<0.35 and h[2]>=2]

    # ── Worst market regime ──
    regimes = brain.get("regimes",{})
    worst_wr = 1.0; worst = None
    for r,d in regimes.items():
        if d["total"]>=3:
            wr = d["wins"]/d["total"]
            if wr < worst_wr: worst_wr=wr; worst=r
    if worst: brain["worst_regime"] = worst

    log.info(f"Brain adapted | WR:{overall_wr*100:.0f}% size:{brain['size_multiplier']:.1f}x | Best:{brain['best_tickers'][:3]} | Hot sectors:{brain['hot_sectors']} | Avoid hours:{brain['avoid_hours']}")

# ─────────────────────────────────────────
# SMART POSITION SIZING
# ─────────────────────────────────────────
def calc_size(brain, analysis, base_size, setup):
    mult = brain.get("size_multiplier", 1.0)
    ticker = analysis.get("ticker","")
    sector = WATCHLIST.get(ticker, "unknown")

    # ── Ticker reputation ──
    if ticker in brain.get("best_tickers",[]): mult *= 1.35
    if ticker in brain.get("worst_tickers",[]): mult *= 0.5

    # ── Sector reputation ──
    wr,n = get_wr(brain["sectors"], sector)
    if n>=3:
        if wr>0.65: mult *= 1.2
        elif wr<0.35: mult *= 0.6

    # ── Setup reputation ──
    wr,n = get_wr(brain["setups"], setup)
    if n>=3:
        if wr>0.65: mult *= 1.2
        elif wr<0.35: mult *= 0.7

    # ── Market regime ──
    wr,n = get_wr(brain["regimes"], analysis["regime"])
    if n>=3:
        if wr>0.65: mult *= 1.2
        elif wr<0.35: mult *= 0.6

    # ── Time of day ──
    hour = datetime.now().hour
    if hour in brain.get("avoid_hours",[]): mult *= 0.5
    elif hour == brain.get("best_hour",10): mult *= 1.2
    wr,n = get_wr(brain["hours"], str(hour))
    if n>=2:
        if wr>0.65: mult *= 1.15
        elif wr<0.35: mult *= 0.7

    # ── RSI zone ──
    zone = rsi_zone(analysis["rsi"])
    wr,n = get_wr(brain["rsi_zones"], zone)
    if n>=3:
        if wr>0.65: mult *= 1.15
        elif wr<0.35: mult *= 0.7

    # ── Volume confirmation ──
    vol_mult = analysis.get("volume_mult", 1.0)
    vzone = volume_zone(vol_mult)
    wr,n = get_wr(brain["volume_zones"], vzone)
    if n>=3:
        if wr>0.65: mult *= 1.15
        elif wr<0.35: mult *= 0.75
    # Raw volume boost
    if vol_mult>=2.5: mult *= 1.2
    elif vol_mult>=2.0: mult *= 1.1
    elif vol_mult<1.0: mult *= 0.7

    # ── RSI depth ──
    rsi_v = analysis["rsi"]
    if rsi_v<20: mult *= 1.5
    elif rsi_v<25: mult *= 1.3
    elif rsi_v<30: mult *= 1.15
    elif rsi_v<35: mult *= 1.0
    else: mult *= 0.85

    # ── Bollinger position ──
    bb = analysis.get("bb_pct", 50)
    if bb<5: mult *= 1.2
    elif bb<15: mult *= 1.1
    elif bb>90: mult *= 0.75

    # ── MACD state ──
    macd_state = "ABOVE" if analysis.get("macd",0)>0 else "BELOW"
    wr,n = get_wr(brain["macd_states"], macd_state)
    if n>=3:
        if wr>0.65: mult *= 1.1
        elif wr<0.35: mult *= 0.8

    # ── Consecutive loss protection ──
    consec = brain.get("consecutive_losses",0)
    if consec>=2: mult *= max(0.4, 1.0-(consec*0.2))

    size = round(base_size * mult)
    return max(50, min(MAX_TRADE, size)), mult

# ─────────────────────────────────────────
# ALPACA API
# ─────────────────────────────────────────
def alpaca(method, path, data=None):
    hdrs = {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "Content-Type": "application/json"
    }
    try:
        if method=="GET":      r=requests.get(f"{ALPACA_BASE}{path}",headers=hdrs,timeout=10)
        elif method=="POST":   r=requests.post(f"{ALPACA_BASE}{path}",headers=hdrs,json=data,timeout=10)
        elif method=="DELETE": r=requests.delete(f"{ALPACA_BASE}{path}",headers=hdrs,timeout=10)
        if r.status_code in(200,201,204): return r.json() if r.content else {}
        log.warning(f"Alpaca {method} {path}: {r.status_code} | {r.text[:200]}")
    except Exception as e:
        log.error(f"Alpaca: {e}")
    return None

def get_account():   return alpaca("GET","/v2/account")
def get_positions(): return alpaca("GET","/v2/positions") or []

def is_market_open():
    clock = alpaca("GET","/v2/clock")
    if clock: return clock.get("is_open",False)
    now = datetime.utcnow()-timedelta(hours=4)
    if now.weekday()>=5: return False
    return 9<=now.hour<16

def buy_stock(symbol, notional):
    return alpaca("POST","/v2/orders",{
        "symbol": symbol,
        "notional": str(round(notional,2)),
        "side": "buy",
        "type": "market",
        "time_in_force": "day"
    })

def sell_stock(symbol):
    result = alpaca("DELETE",f"/v2/positions/{symbol}")
    if result is not None: return result
    return alpaca("POST","/v2/orders",{
        "symbol": symbol,
        "qty": "1",
        "side": "sell",
        "type": "market",
        "time_in_force": "day"
    })

# ─────────────────────────────────────────
# MARKET DATA
# ─────────────────────────────────────────
def get_bars(symbol, limit=100):
    try:
        r = alpaca("GET",f"/v2/stocks/{symbol}/bars?timeframe=1Hour&limit={limit}&feed=iex")
        if not r: return []
        return [{"t":b["t"],"o":b["o"],"h":b["h"],"l":b["l"],"c":b["c"],"v":b["v"]}
                for b in r.get("bars",[])]
    except Exception as e:
        log.error(f"Bars {symbol}: {e}"); return []

def get_quote(symbol):
    try:
        r = alpaca("GET",f"/v2/stocks/{symbol}/quotes/latest?feed=iex")
        if r and "quote" in r:
            q = r["quote"]
            mid = (q.get("ap",0)+q.get("bp",0))/2
            return {"price": mid or q.get("ap",0)}
    except Exception as e:
        log.error(f"Quote {symbol}: {e}")
    return None

def get_spy_trend():
    """Get SPY direction as market context"""
    bars = get_bars("SPY", 20)
    if len(bars)<5: return "UNKNOWN"
    closes = [b["c"] for b in bars]
    recent = sum(closes[-3:])/3
    earlier = sum(closes[-8:-3])/5
    if recent > earlier*1.003: return "UPTREND"
    if recent < earlier*0.997: return "DOWNTREND"
    return "FLAT"

# ─────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────
def calc_rsi(closes, p=14):
    if len(closes)<p+1: return 50.0
    g=l=0.0
    for i in range(len(closes)-p,len(closes)):
        d=closes[i]-closes[i-1]
        if d>0: g+=d
        else: l-=d
    ag,al=g/p,l/p
    return 100.0 if al==0 else 100-(100/(1+ag/al))

def calc_ema(data, p):
    if len(data)<p: return None
    k=2/(p+1); e=sum(data[:p])/p
    for v in data[p:]: e=v*k+e*(1-k)
    return e

def calc_boll(closes, p=20):
    if len(closes)<p: return None,None,None
    sl=closes[-p:]; mean=sum(sl)/p
    std=math.sqrt(sum((x-mean)**2 for x in sl)/p)
    return mean+2*std, mean, mean-2*std

def calc_atr(candles, p=14):
    if len(candles)<2: return 0
    trs=[max(candles[i]["h"]-candles[i]["l"],
             abs(candles[i]["h"]-candles[i-1]["c"]),
             abs(candles[i]["l"]-candles[i-1]["c"]))
         for i in range(1,len(candles))]
    return sum(trs[-p:])/min(p,len(trs))

def calc_volume_mult(bars):
    """Current volume vs 20-bar average"""
    if len(bars)<5: return 1.0
    vols = [b["v"] for b in bars]
    avg = sum(vols[:-1])/max(1,len(vols)-1)
    if avg==0: return 1.0
    return round(vols[-1]/avg, 2)

def boll_squeeze(candles, p=20, lb=40):
    if len(candles)<lb: return False
    closes=[c["c"] for c in candles]
    widths=[]
    for i in range(lb,len(closes)):
        sl=closes[i-p:i]; mean=sum(sl)/p
        std=math.sqrt(sum((x-mean)**2 for x in sl)/p)
        if mean>0: widths.append(std*4/mean*100)
    if not widths: return False
    return widths[-1]<sum(widths)/len(widths)*0.65

def calc_patterns(candles):
    if len(candles)<3: return []
    p=[]; c=candles[-1]; p1=candles[-2]; p2=candles[-3]
    body=abs(c["c"]-c["o"]); rng=c["h"]-c["l"]
    if rng==0: return p
    uw=c["h"]-max(c["c"],c["o"]); lw=min(c["c"],c["o"])-c["l"]
    if p1["c"]<p1["o"] and c["c"]>c["o"] and c["o"]<p1["c"] and c["c"]>p1["o"]:
        p.append(("BULLISH_ENGULFING",True))
    if p1["c"]>p1["o"] and c["c"]<c["o"] and c["o"]>p1["c"] and c["c"]<p1["o"]:
        p.append(("BEARISH_ENGULFING",False))
    if body>0 and lw>body*2 and uw<body*0.5: p.append(("HAMMER",True))
    if body>0 and uw>body*2 and lw<body*0.5: p.append(("SHOOTING_STAR",False))
    if body<rng*0.1: p.append(("DOJI",None))
    if all(candles[-3+i]["c"]>candles[-3+i]["o"] for i in range(3)):
        if candles[-2]["c"]>candles[-3]["c"] and c["c"]>candles[-2]["c"]:
            p.append(("THREE_WHITE_SOLDIERS",True))
    if all(candles[-3+i]["c"]<candles[-3+i]["o"] for i in range(3)):
        if candles[-2]["c"]<candles[-3]["c"] and c["c"]<candles[-2]["c"]:
            p.append(("THREE_BLACK_CROWS",False))
    return p

def detect_regime(candles):
    if len(candles)<8: return "UNKNOWN","Insufficient data"
    closes=[c["c"] for c in candles[-24:]]
    highs=[c["h"] for c in candles[-24:]]
    lows=[c["l"] for c in candles[-24:]]
    rng=max(highs)-min(lows); avg=sum(closes)/len(closes) if closes else 1
    rng_pct=rng/avg*100
    mid=len(closes)//2
    f_avg=sum(closes[:mid])/mid if mid else avg
    s_avg=sum(closes[mid:])/len(closes[mid:]) if closes[mid:] else avg
    trend=(s_avg-f_avg)/f_avg*100 if f_avg else 0
    chop=sum(1 for i in range(2,len(closes))
             if (closes[i]-closes[i-1])*(closes[i-1]-closes[i-2])<0)/max(1,len(closes)-2)
    atr_pct=calc_atr(candles)/avg*100 if avg else 0
    if chop>0.65 and rng_pct<1.5: return "CHOP",f"Sideways {rng_pct:.2f}%"
    elif trend>1.5 and chop<0.55: return "UPTREND",f"+{trend:.2f}%"
    elif trend<-1.5 and chop<0.55: return "DOWNTREND",f"{trend:.2f}%"
    elif atr_pct>1.0: return "VOLATILE",f"ATR {atr_pct:.3f}%"
    else: return "RANGING",f"{trend:+.2f}%"

def analyze(ticker, bars, quote):
    if not quote or len(bars)<20: return None
    price = quote["price"]
    closes=[b["c"] for b in bars]
    opens=[b["o"] for b in bars]

    rsi_v=calc_rsi(closes)
    e12=calc_ema(closes,12); e26=calc_ema(closes,26)
    macd_v=(e12-e26) if e12 and e26 else 0
    bbu,bbm,bbl=calc_boll(closes)
    bb_pct=((price-bbl)/(bbu-bbl)*100) if bbu and bbl and bbu!=bbl else 50
    atr_v=calc_atr(bars)
    vol_mult=calc_volume_mult(bars)
    squeeze=boll_squeeze(bars)
    patterns=calc_patterns(bars)
    regime,rdesc=detect_regime(bars)

    # Gap from previous close
    prev_close=closes[-2] if len(closes)>=2 else price
    gap_pct=(opens[-1]-prev_close)/prev_close*100 if prev_close else 0

    # Momentum
    p1h=closes[-2] if len(closes)>=2 else price
    p4h=closes[-5] if len(closes)>=5 else price
    chg1h=(price-p1h)/p1h*100 if p1h else 0
    chg4h=(price-p4h)/p4h*100 if p4h else 0

    return {
        "ticker": ticker,
        "sector": WATCHLIST.get(ticker,"unknown"),
        "price": price,
        "rsi": rsi_v,
        "macd": macd_v,
        "bb_pct": bb_pct,
        "bbu": bbu,
        "bbl": bbl,
        "atr": atr_v,
        "volume_mult": vol_mult,
        "squeeze": squeeze,
        "gap_pct": gap_pct,
        "chg1h": chg1h,
        "chg4h": chg4h,
        "regime": regime,
        "regime_desc": rdesc,
        "patterns": patterns,
        "ts": datetime.now().strftime("%H:%M:%S")
    }

# ─────────────────────────────────────────
# TRADE DECISION ENGINE
# ─────────────────────────────────────────
def check_buy(a, brain, open_trades, spy_trend):
    """Smart buy decision with brain context"""
    if not a: return False,"",0,""

    ticker = a["ticker"]
    sector = a["sector"]
    rsi_v = a["rsi"]
    patterns = a["patterns"]
    bullish = [p[0] for p in patterns if p[1]==True]
    squeeze = a["squeeze"]
    regime = a["regime"]
    vol_mult = a["volume_mult"]
    hour = datetime.now().hour

    # ── Hard blocks ──
    if len([t for t in brain["trades"] if t["status"]=="open"]) >= MAX_POSITIONS:
        return False,"",0,"Max positions reached"

    sector_count = sum(1 for t in brain["trades"]
                      if t["status"]=="open" and t.get("sector")==sector)
    if sector_count >= MAX_PER_SECTOR:
        return False,"",0,f"Max {sector} sector positions"

    if ticker in [t["ticker"] for t in brain["trades"] if t["status"]=="open"]:
        return False,"",0,f"{ticker} already open"

    # ── Brain blocks ──
    if ticker in brain.get("worst_tickers",[]) and brain.get("total_trades",0)>15:
        return False,"",0,f"Brain: avoiding {ticker}"

    if sector in brain.get("worst_sectors",[]) and brain.get("total_trades",0)>20:
        return False,"",0,f"Brain: avoiding {sector} sector"

    if regime == brain.get("worst_regime","") and brain.get("total_trades",0)>10:
        return False,"",0,f"Brain: worst regime {regime}"

    if hour in brain.get("avoid_hours",[]) and brain.get("total_trades",0)>10:
        return False,"",0,f"Brain: avoiding hour {hour}:00"

    # ── Volume gate — require confirmation ──
    if vol_mult < MIN_VOLUME_MULT and rsi_v > BUY_RSI_NORMAL:
        return False,"",0,f"Low volume {vol_mult:.1f}x"

    # ── Market context ──
    if spy_trend == "DOWNTREND" and rsi_v > BUY_RSI_NORMAL:
        return False,"",0,"SPY downtrend — waiting for better entry"

    setup=""; reason=""; base_size=0

    # ── SWING: deep oversold ──
    if rsi_v < BUY_RSI_STRONG and vol_mult >= 1.2:
        setup="swing"; base_size=SWING_SIZE
        reason=f"STRONG BUY RSI {rsi_v:.1f} extreme oversold + vol {vol_mult:.1f}x"

    # ── SWING: oversold + below Bollinger ──
    elif rsi_v < BUY_RSI_NORMAL and a["bb_pct"]<10 and vol_mult>=1.3:
        setup="swing"; base_size=SWING_SIZE
        reason=f"BUY RSI {rsi_v:.1f} + below BB + vol {vol_mult:.1f}x"

    # ── BREAKOUT: Bollinger squeeze ──
    elif squeeze and rsi_v<50 and a["chg1h"]>0 and vol_mult>=1.5:
        setup="breakout"; base_size=BREAKOUT_SIZE
        reason=f"BREAKOUT squeeze + RSI {rsi_v:.1f} + vol {vol_mult:.1f}x"

    # ── MICRO: dip with bullish pattern + volume ──
    elif rsi_v < BUY_RSI_MICRO and bullish and vol_mult>=MIN_VOLUME_MULT:
        setup="micro"; base_size=MICRO_SIZE
        reason=f"MICRO RSI {rsi_v:.1f} + {', '.join(bullish)} + vol {vol_mult:.1f}x"

    # ── MICRO: oversold dip ──
    elif rsi_v < BUY_RSI_NORMAL and vol_mult>=1.5:
        setup="micro"; base_size=MICRO_SIZE
        reason=f"MICRO RSI {rsi_v:.1f} dip + elevated vol {vol_mult:.1f}x"

    # ── MICRO: hot sector momentum ──
    elif sector in brain.get("hot_sectors",[]) and rsi_v<48 and bullish and vol_mult>=1.3:
        setup="micro"; base_size=MICRO_SIZE
        reason=f"HOT SECTOR {sector} + RSI {rsi_v:.1f} + {', '.join(bullish)}"

    # ── SWING: best ticker on dip ──
    elif ticker in brain.get("best_tickers",[]) and rsi_v<40 and vol_mult>=1.2:
        setup="swing"; base_size=SWING_SIZE
        reason=f"BEST TICKER {ticker} dip RSI {rsi_v:.1f} + vol {vol_mult:.1f}x"

    if not setup: return False,"",0,""

    size, mult = calc_size(brain, a, base_size, setup)
    log.info(f"{ticker} size: base ${base_size} x {mult:.2f} = ${size}")
    return True, setup, size, reason

def check_sell(a, trade, brain):
    """Smart sell with trailing stop and brain signals"""
    if not a or not trade: return False,""

    price = a["price"]
    entry = trade["open_price"]
    open_dt = datetime.fromisoformat(trade["open_time"])
    held = int((datetime.now()-open_dt).total_seconds()//60)
    pct = (price-entry)/entry*100
    setup = trade.get("setup","swing")

    min_hold = MIN_HOLD_MICRO if setup=="micro" else MIN_HOLD_SWING
    if held < min_hold: return False,""

    # Update trailing stop
    if price > trade.get("trail_high",entry):
        trade["trail_high"] = price
        new_stop = round(price*(1-TRAIL_STEP/100), 4)
        if new_stop > trade.get("trail_stop",0):
            trade["trail_stop"] = new_stop

    trail_stop = trade.get("trail_stop", entry*(1-STOP_LOSS/100))
    bearish = [p[0] for p in a["patterns"] if p[1]==False]

    if setup=="micro":    profit_target=PROFIT_MICRO
    elif setup=="breakout": profit_target=PROFIT_BREAKOUT
    else:                 profit_target=PROFIT_SWING

    if price <= trail_stop:
        return True, f"Trail stop ${trail_stop:.2f} ({pct:+.2f}%)"
    if pct >= profit_target:
        return True, f"Profit target +{pct:.2f}% hit"
    if pct <= -STOP_LOSS:
        return True, f"Stop loss {pct:.2f}%"
    if a["rsi"] > SELL_RSI_HIGH and pct > 0.3:
        return True, f"RSI {a['rsi']:.1f} overbought + profitable"
    if a["rsi"] > 75:
        return True, f"RSI {a['rsi']:.1f} extreme overbought"
    if bearish and held>20 and pct>0.2:
        return True, f"Bearish signal {', '.join(bearish)} + profitable"
    if held>240 and pct<0:
        return True, f"Time stop: {held}min held, losing"

    return False,""

# ─────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────
def tg(msg):
    clean=str(msg).replace("<b>","").replace("</b>","").replace("<i>","").replace("</i>","")
    try:
        r=requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id":TELEGRAM_CHAT,"text":clean},timeout=10)
        if r.status_code==200: log.info(f"TG: {clean[:60].strip()}")
        else: log.warning(f"TG failed: {r.text[:80]}")
    except Exception as e: log.error(f"TG: {e}")

def tg_updates(offset=None):
    try:
        p={"timeout":5,"allowed_updates":["message"]}
        if offset: p["offset"]=offset
        r=requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",params=p,timeout=10)
        if r.status_code==200: return r.json().get("result",[])
    except: pass
    return []

# ─────────────────────────────────────────
# REPORTS
# ─────────────────────────────────────────
def brain_summary(brain):
    total=brain.get("total_trades",0)
    wins=brain.get("wins",0); losses=brain.get("losses",0)
    pnl=brain.get("total_pnl",0.0); res=wins+losses
    if total==0: return "Brain: No trades yet — watching and learning"
    wr=f"{wins/res*100:.0f}%" if res>0 else "--"
    mult=brain.get("size_multiplier",1.0)
    best=", ".join(brain.get("best_tickers",[])[:3]) or "learning..."
    avoid=", ".join(brain.get("worst_tickers",[])[:3]) or "none"
    hot=", ".join(brain.get("hot_sectors",[])) or "none"
    lines=[
        f"Brain: {total} trades | WR: {wr} | P&L: ${pnl:+.2f}",
        f"Size: {mult:.1f}x | Best: {best}",
        f"Avoid: {avoid} | Hot sectors: {hot}",
        f"Best hour: {brain.get('best_hour',10)}:00 | Session: {brain.get('session_trades',0)} trades ${brain.get('session_pnl',0):+.2f}"
    ]
    return "\n".join(lines)

def brain_full_report(brain):
    wins=brain.get("wins",0); losses=brain.get("losses",0)
    pnl=brain.get("total_pnl",0.0); res=wins+losses
    lines=["JARVIS STOCKS — BRAIN REPORT",""]
    lines.append(f"Total trades: {brain.get('total_trades',0)}")
    if res>0: lines.append(f"Win rate: {wins/res*100:.1f}% ({wins}W/{losses}L)")
    lines.append(f"Total P&L: ${pnl:+.2f}")
    lines.append(f"Best trade: ${brain.get('best_trade',0):+.2f}")
    lines.append(f"Worst trade: ${brain.get('worst_trade',0):+.2f}")
    lines.append(f"Size multiplier: {brain.get('size_multiplier',1.0):.1f}x")
    lines.append(f"Max consec losses: {brain.get('max_consecutive_losses',0)}")
    lines.append(f"Best hour: {brain.get('best_hour',10)}:00")
    lines.append(f"Avoid hours: {brain.get('avoid_hours',[])}")
    lines.append(f"Best setup: {brain.get('best_setup','swing')}")
    lines.append("")
    for label,bucket in [
        ("Ticker",    brain.get("tickers",{})),
        ("Sector",    brain.get("sectors",{})),
        ("Setup",     brain.get("setups",{})),
        ("Regime",    brain.get("regimes",{})),
        ("RSI Zone",  brain.get("rsi_zones",{})),
        ("Volume",    brain.get("volume_zones",{})),
        ("Hour",      brain.get("hours",{})),
        ("Hold Time", brain.get("hold_times",{})),
        ("Pattern",   brain.get("patterns",{})),
        ("Gap Type",  brain.get("gap_types",{})),
        ("MACD",      brain.get("macd_states",{})),
    ]:
        valid=[(k,v) for k,v in bucket.items() if v.get("total",0)>=2]
        if valid:
            lines.append(f"{label}:")
            for k,v in sorted(valid,key=lambda x:x[1]["wins"]/x[1]["total"],reverse=True):
                wr=v["wins"]/v["total"]*100
                apnl=v.get("avg_pnl",0)
                streak=v.get("streak",0)
                streak_str=f" streak:{streak:+d}" if streak!=0 else ""
                lines.append(f"  {k}: {wr:.0f}% WR ({v['total']} trades) avg ${apnl:+.2f}{streak_str}")
            lines.append("")
    return "\n".join(lines)

def send_status(brain, analyses=None):
    try:
        acct=get_account()
        if not acct: tg("Alpaca connection failed"); return
        eq=float(acct.get("equity",0))
        leq=float(acct.get("last_equity",eq))
        dpl=eq-leq; dpct=dpl/leq*100 if leq else 0
        positions=get_positions()
        open_t=[t for t in brain["trades"] if t["status"]=="open"]
        lines=[
            "JARVIS STOCKS STATUS",
            f"Paper Equity: ${eq:,.2f}",
            f"Daily PnL: ${dpl:+.2f} ({dpct:+.2f}%)",
            f"Open positions: {len(open_t)}/{MAX_POSITIONS}",
        ]
        if positions:
            lines.append("\nPositions:")
            for p in positions:
                upl=float(p.get("unrealized_pl",0))
                mv=float(p.get("market_value",0))
                lines.append(f"  {p.get('symbol')} ${mv:,.2f} PnL: ${upl:+.2f}")
        lines.append("")
        lines.append(brain_summary(brain))
        tg("\n".join(lines))
    except Exception as e:
        log.error(f"Status error: {e}")

def send_morning_report(brain):
    try:
        acct=get_account()
        if not acct: return
        eq=float(acct.get("equity",0))
        leq=float(acct.get("last_equity",eq))
        dpl=eq-leq; dpct=dpl/leq*100 if leq else 0
        yesterday=datetime.now()-timedelta(days=1)
        yest=[t for t in brain["trades"]
              if t.get("close_time","")>yesterday.isoformat() and t["status"]=="closed"]
        yest_pnl=sum(t.get("pnl",0) for t in yest)
        yest_wins=sum(1 for t in yest if t.get("won"))
        spy_trend=get_spy_trend()
        lines=[
            "JARVIS STOCKS — MORNING REPORT",
            f"Good morning Lenny!",
            "",
            f"Paper Equity: ${eq:,.2f}",
            f"24h PnL: ${dpl:+.2f} ({dpct:+.2f}%)",
            "",
            f"Yesterday: {len(yest)} trades | PnL: ${yest_pnl:+.2f} | Wins: {yest_wins}/{len(yest)}",
            f"SPY trend: {spy_trend}",
            "",
            brain_summary(brain),
            "",
            f"Watching {len(WATCHLIST)} tickers across {len(set(WATCHLIST.values()))} sectors",
            "Commands: STATUS / BRAIN / WATCHLIST / STOP"
        ]
        tg("\n".join(lines))
    except Exception as e:
        log.error(f"Morning report: {e}")

# ─────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────
def main():
    log.info("="*65)
    log.info("JARVIS STOCKS — Intelligent Stock Market Agent")
    log.info(f"Watching {len(WATCHLIST)} tickers | Smart memory | Always learning")
    log.info(f"Micro: ${MICRO_SIZE} | Swing: ${SWING_SIZE} | Breakout: ${BREAKOUT_SIZE}")
    log.info(f"Stop: -{STOP_LOSS}% | Trail: {TRAIL_STEP}% | Max positions: {MAX_POSITIONS}")
    log.info("="*65)

    brain=load_brain()
    reset_session(brain)

    acct=get_account()
    if not acct:
        log.error("Alpaca connection failed")
        tg("JARVIS STOCKS: Alpaca connection failed")
        return

    eq=float(acct.get("equity",0))
    log.info(f"Connected — Paper equity: ${eq:,.2f}")

    spy_trend=get_spy_trend()
    brain["spy_trend"]=spy_trend
    save_brain(brain)

    tg(f"JARVIS STOCKS ONLINE\n\nPaper: ${eq:,.2f}\nWatching: {len(WATCHLIST)} tickers\nSPY trend: {spy_trend}\nBrain: {brain.get('total_trades',0)} trades learned\nSize: {brain.get('size_multiplier',1.0):.1f}x\n\n{brain_summary(brain)}\n\nCommands: STATUS / BRAIN / WATCHLIST / STOP")

    tg_offset=None
    last_scan=0
    last_daily=0
    last_report_day=-1
    last_spy_check=0
    open_trades={}

    log.info("Running — waiting for market open...")

    while True:
        now=time.time()
        current_hour=datetime.now().hour
        current_day=datetime.now().day

        # TELEGRAM COMMANDS
        for u in tg_updates(tg_offset):
            tg_offset=u["update_id"]+1
            msg=u.get("message",{})
            chat=str(msg.get("chat",{}).get("id",""))
            text=msg.get("text","").strip().upper()
            if chat!=TELEGRAM_CHAT: continue
            if text=="STATUS":
                send_status(brain)
            elif text=="BRAIN":
                tg(brain_full_report(brain))
            elif text=="WATCHLIST":
                sectors={}
                for t,s in WATCHLIST.items():
                    sectors.setdefault(s,[]).append(t)
                lines=["WATCHLIST:"]
                for s,tickers in sectors.items():
                    lines.append(f"  {s}: {', '.join(tickers)}")
                tg("\n".join(lines))
            elif text=="STOP":
                tg("JARVIS STOCKS stopped."); return
            elif text=="HELP":
                tg("Commands:\nSTATUS - portfolio\nBRAIN - learning report\nWATCHLIST - all tickers\nSTOP - stop bot")

        # MORNING REPORT
        if current_hour==REPORT_HOUR and current_day!=last_report_day:
            last_report_day=current_day
            send_morning_report(brain)

        # CHECK IF MARKET IS OPEN
        if not is_market_open():
            time.sleep(30)
            continue

        # SPY TREND UPDATE every 15 min
        if now-last_spy_check>=900:
            last_spy_check=now
            spy_trend=get_spy_trend()
            brain["spy_trend"]=spy_trend
            log.info(f"SPY trend: {spy_trend}")

        # CHECK OPEN TRADES
        if open_trades:
            for order_id,trade in list(open_trades.items()):
                ticker=trade["ticker"]
                quote=get_quote(ticker)
                bars=get_bars(ticker,50)
                if not quote or not bars: continue
                a=analyze(ticker,bars,quote)
                if not a: continue
                sell,reason=check_sell(a,trade,brain)
                if sell:
                    result=sell_stock(ticker)
                    if result:
                        closed=record_close(brain,order_id,a["price"],reason)
                        pnl=closed["pnl"] if closed else 0
                        won=closed["won"] if closed else False
                        held=closed.get("hold_mins",0) if closed else 0
                        pct=(a["price"]-trade["open_price"])/trade["open_price"]*100
                        emoji="WIN" if won else "LOSS"
                        tg(f"{emoji} CLOSE {ticker} ({trade.get('setup','').upper()})\n\nEntry: ${trade['open_price']:.2f} -> Exit: ${a['price']:.2f}\nHeld: {held}min | P&L: ${pnl:+.2f} ({pct:+.2f}%)\nReason: {reason}\n\n{brain_summary(brain)}")
                        del open_trades[order_id]
                    else:
                        tg(f"SELL FAILED {ticker} — check Alpaca")

        # MAIN SCAN
        if now-last_scan>=SCAN_INTERVAL:
            last_scan=now
            log.info(f"--- SCAN ({len(WATCHLIST)} tickers | SPY:{spy_trend} | Open:{len(open_trades)}/{MAX_POSITIONS}) ---")

            top_signals=[]

            for ticker,sector in WATCHLIST.items():
                try:
                    quote=get_quote(ticker)
                    bars=get_bars(ticker,100)
                    if not quote or not bars: continue
                    a=analyze(ticker,bars,quote)
                    if not a: continue

                    log.info(f"{ticker} ${a['price']:.2f} RSI:{a['rsi']:.1f} Vol:{a['volume_mult']:.1f}x BB:{a['bb_pct']:.0f}% {a['regime']}")
                    if a["squeeze"]: log.info(f"  {ticker}: SQUEEZE")
                    if a["patterns"]: log.info(f"  {ticker} patterns: {[p[0] for p in a['patterns']]}")

                    buy,setup,size,reason=check_buy(a,brain,open_trades,spy_trend)
                    if buy:
                        # Score the signal for prioritization
                        score=0
                        if ticker in brain.get("best_tickers",[]): score+=3
                        if sector in brain.get("hot_sectors",[]): score+=2
                        if a["volume_mult"]>=2.0: score+=2
                        if a["squeeze"]: score+=2
                        if a["rsi"]<30: score+=3
                        top_signals.append((score,ticker,setup,size,reason,a))

                except Exception as e:
                    log.error(f"Scan {ticker}: {e}")

            # Execute highest scored signals first
            top_signals.sort(key=lambda x:x[0],reverse=True)
            for score,ticker,setup,size,reason,a in top_signals:
                if len(open_trades)>=MAX_POSITIONS: break
                log.info(f"{ticker} BUY ({setup}) score:{score} ${size} — {reason}")
                result=buy_stock(ticker,size)
                if result:
                    order_id=result.get("id","?")
                    trade=record_open(brain,ticker,setup,order_id,a["price"],a,size)
                    open_trades[order_id]=trade
                    pat_str=", ".join(p[0] for p in a["patterns"]) if a["patterns"] else "None"
                    squeeze_note=" SQUEEZE" if a["squeeze"] else ""
                    tg(f"BUY {ticker} {setup.upper()}{squeeze_note} ${size}\n\nPrice: ${a['price']:.2f} | Sector: {a['sector']}\nRSI: {a['rsi']:.1f} | BB: {a['bb_pct']:.0f}% | Vol: {a['volume_mult']:.1f}x\nReason: {reason}\nPatterns: {pat_str}\nRegime: {a['regime']}\nTrail stop: ${trade['trail_stop']:.2f}\nSPY: {spy_trend} | Score: {score}\nOrder: {order_id}")
                else:
                    tg(f"BUY FAILED {ticker} — check Alpaca")

        # DAILY STATUS
        if now-last_daily>=21600:
            last_daily=now
            send_status(brain)

        time.sleep(2)

if __name__=="__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped")
        tg("JARVIS STOCKS stopped (Ctrl+C)")
    except Exception as e:
        log.error(f"Fatal: {e}")
        tg(f"JARVIS STOCKS crashed: {e}")
