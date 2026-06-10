#!/usr/bin/env python3
"""
JARVIS ALPHA — Full Market Trading System
Crypto + Stocks | Swing + Micro trades | 24/7 Brain
BTC, ETH, SOL, AVAX + SPY, QQQ, NVDA, TSLA, COIN
"""

import json, time, math, requests, os
from datetime import datetime, timedelta

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
ALPACA_KEY    = "PKTHANGUNVFDSLLR3VXPETXRQF"
ALPACA_SECRET = "GRTDDfkCGWbZMoNSWms6uJSGvw72rHaAk1N1fvLi8EAP"
ALPACA_BASE   = "https://paper-api.alpaca.markets"
TELEGRAM_TOKEN= __import__("jarvis_secrets").TG_TOKEN_TRADER
TELEGRAM_CHAT = "7534553840"

# Trade sizing
MICRO_SIZE      = 150    # micro-trade size USD
SWING_SIZE      = 400    # swing trade base size USD
MAX_TRADE       = 500    # maximum single trade USD
MAX_CRYPTO_POS  = 3      # max simultaneous crypto positions
MAX_STOCK_POS   = 3      # max simultaneous stock positions

# Strategy thresholds
BUY_RSI_STRONG  = 25     # extreme oversold — full size
BUY_RSI_NORMAL  = 33     # oversold — normal size
BUY_RSI_MICRO   = 47     # mild dip — micro trade only
SELL_RSI        = 58     # start looking to sell
PROFIT_MICRO    = 0.6    # micro trade profit target %
PROFIT_SWING    = 1.8    # swing trade profit target %
STOP_LOSS       = 1.5    # stop loss %
TRAIL_STEP      = 0.35   # trailing stop step %
MIN_HOLD_MICRO  = 5      # min hold minutes for micro trades
MIN_HOLD_SWING  = 20     # min hold minutes for swing trades

# Timing
CRYPTO_POLL     = 90     # crypto check interval seconds
STOCK_POLL      = 120    # stock check interval seconds
REPORT_HOUR     = 7      # daily report hour (7am)

# Assets
CRYPTO_PAIRS = {
    "BTC":  ("XBTUSD",  "BTC/USD"),
    "ETH":  ("ETHUSD",  "ETH/USD"),
    "SOL":  ("SOLUSD",  "SOL/USD"),
    "AVAX": ("AVAXUSD", "AVAX/USD"),
}
STOCK_SYMBOLS = ["SPY","QQQ","NVDA","TSLA","COIN"]

MEMORY_FILE = "jarvis_alpha_brain.json"

import logging
logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('JARVIS')

# ─────────────────────────────────────────
# BRAIN SYSTEM
# ─────────────────────────────────────────
def load_brain():
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE,'r') as f: return json.load(f)
    except: pass
    return {
        "trades":[],
        "total_trades":0,"wins":0,"losses":0,"total_pnl":0.0,
        "best_trade":0.0,"worst_trade":0.0,
        "assets":{},
        "regimes":{},
        "rsi_zones":{},
        "hours":{},
        "patterns":{},
        "trade_types":{},
        "hold_times":{},
        "days":{},
        "size_multiplier":1.0,
        "best_assets":[],
        "worst_assets":[],
        "best_hour":9,
        "worst_regime":"CHOP",
        "micro_win_rate":0.5,
        "swing_win_rate":0.5,
        "created":datetime.now().isoformat(),
        "last_updated":datetime.now().isoformat()
    }

def save_brain(b):
    try:
        b["last_updated"]=datetime.now().isoformat()
        with open(MEMORY_FILE,'w') as f: json.dump(b,f,indent=2)
    except Exception as e: log.error(f"Brain save: {e}")

def rsi_zone(v):
    if v>=75: return "EXTREME_OB"
    if v>=65: return "OVERBOUGHT"
    if v>=55: return "BULLISH"
    if v>=45: return "NEUTRAL"
    if v>=35: return "BEARISH"
    if v>=25: return "OVERSOLD"
    return "EXTREME_OS"

def hold_bucket(mins):
    if mins<10:  return "<10min"
    if mins<30:  return "10-30min"
    if mins<60:  return "30-60min"
    if mins<240: return "1-4hr"
    return "4hr+"

def update_bucket(b,k,won,pnl=0):
    if k not in b: b[k]={"wins":0,"total":0,"avg_pnl":0.0}
    b[k]["total"]+=1
    if won: b[k]["wins"]+=1
    n=b[k]["total"]
    b[k]["avg_pnl"]=round((b[k]["avg_pnl"]*(n-1)+pnl)/n,3)

def get_wr(b,k,min_t=3):
    if k not in b or b[k]["total"]<min_t: return 0.5,0
    return b[k]["wins"]/b[k]["total"],b[k]["total"]

def record_open(brain,asset,trade_type,order_id,price,analysis,size):
    trade={
        "id":order_id,"asset":asset,"trade_type":trade_type,
        "open_price":price,"open_time":datetime.now().isoformat(),
        "size":size,"rsi":round(analysis["rsi"],1),
        "rsi_zone":rsi_zone(analysis["rsi"]),"regime":analysis["regime"],
        "patterns":[p[0] for p in analysis.get("patterns",[])],
        "hour":datetime.now().hour,"day":datetime.now().weekday(),
        "status":"open","trail_stop":round(price*(1-STOP_LOSS/100),4),
        "trail_high":price,"close_price":None,"pnl":None,
        "won":None,"hold_mins":None,"close_reason":None
    }
    brain["trades"].append(trade)
    brain["total_trades"]+=1
    save_brain(brain)
    return trade

def record_close(brain,order_id,close_price,reason):
    for t in brain["trades"]:
        if t["id"]==order_id and t["status"]=="open":
            t["close_price"]=close_price
            t["close_time"]=datetime.now().isoformat()
            t["status"]="closed"; t["close_reason"]=reason
            open_dt=datetime.fromisoformat(t["open_time"])
            held=int((datetime.now()-open_dt).total_seconds()//60)
            t["hold_mins"]=held
            pnl=(close_price-t["open_price"])/t["open_price"]*t["size"]
            t["pnl"]=round(pnl,2); t["won"]=pnl>0
            won=t["won"]
            update_bucket(brain["assets"],    t["asset"],       won,pnl)
            update_bucket(brain["regimes"],   t["regime"],      won,pnl)
            update_bucket(brain["rsi_zones"], t["rsi_zone"],    won,pnl)
            update_bucket(brain["hours"],     str(t["hour"]),   won,pnl)
            update_bucket(brain["trade_types"],t["trade_type"], won,pnl)
            update_bucket(brain["hold_times"],hold_bucket(held),won,pnl)
            update_bucket(brain["days"],      str(t["day"]),    won,pnl)
            for pat in t["patterns"]:
                update_bucket(brain["patterns"],pat,won,pnl)
            brain["total_pnl"]=round(brain.get("total_pnl",0)+pnl,2)
            if won:
                brain["wins"]=brain.get("wins",0)+1
                brain["best_trade"]=max(brain.get("best_trade",0),pnl)
            else:
                brain["losses"]=brain.get("losses",0)+1
                brain["worst_trade"]=min(brain.get("worst_trade",0),pnl)
            _adapt(brain)
            save_brain(brain)
            log.info(f"Brain: {t['asset']} {t['trade_type']} P&L ${pnl:+.2f} {'WIN' if won else 'LOSS'}")
            return t
    return None

def _adapt(brain):
    """Jarvis adjusts its own parameters based on what it learns"""
    wins=brain.get("wins",0); losses=brain.get("losses",0)
    resolved=wins+losses
    if resolved<5: return

    overall_wr=wins/resolved

    if overall_wr>0.65:
        brain["size_multiplier"]=min(2.5,brain.get("size_multiplier",1.0)+0.1)
    elif overall_wr<0.40:
        brain["size_multiplier"]=max(0.4,brain.get("size_multiplier",1.0)-0.1)

    assets=brain.get("assets",{})
    ranked=[(k,v["wins"]/v["total"]) for k,v in assets.items() if v["total"]>=3]
    ranked.sort(key=lambda x:x[1],reverse=True)
    brain["best_assets"]=[r[0] for r in ranked[:3]]
    brain["worst_assets"]=[r[0] for r in ranked[-2:] if r[1]<0.4]

    worst_wr=1.0; worst=None
    for r,d in brain.get("regimes",{}).items():
        if d["total"]>=3:
            wr=d["wins"]/d["total"]
            if wr<worst_wr: worst_wr=wr; worst=r
    if worst: brain["worst_regime"]=worst

    best_wr=0; best_h=9
    for h,d in brain.get("hours",{}).items():
        if d["total"]>=2:
            wr=d["wins"]/d["total"]
            if wr>best_wr: best_wr=wr; best_h=int(h)
    brain["best_hour"]=best_h

    tt=brain.get("trade_types",{})
    if "micro" in tt and tt["micro"]["total"]>=3:
        brain["micro_win_rate"]=tt["micro"]["wins"]/tt["micro"]["total"]
    if "swing" in tt and tt["swing"]["total"]>=3:
        brain["swing_win_rate"]=tt["swing"]["wins"]/tt["swing"]["total"]

    log.info(f"Brain adapted: WR={overall_wr*100:.0f}% size={brain['size_multiplier']:.1f}x best={brain['best_assets']} avoid={brain['worst_assets']}")

def calc_size(brain,analysis,base_size,trade_type):
    """Brain calculates optimal trade size"""
    mult=brain.get("size_multiplier",1.0)
    asset=analysis.get("asset","")

    if asset in brain.get("best_assets",[]):  mult*=1.3
    if asset in brain.get("worst_assets",[]): mult*=0.6

    wr,n=get_wr(brain["regimes"],analysis["regime"])
    if n>=3:
        if wr>0.65: mult*=1.25
        elif wr<0.40: mult*=0.65

    zone=rsi_zone(analysis["rsi"])
    wr,n=get_wr(brain["rsi_zones"],zone)
    if n>=3:
        if wr>0.65: mult*=1.2
        elif wr<0.40: mult*=0.7

    hour=str(datetime.now().hour)
    wr,n=get_wr(brain["hours"],hour)
    if n>=2:
        if wr>0.65: mult*=1.15
        elif wr<0.35: mult*=0.75

    rsi_v=analysis["rsi"]
    if rsi_v<20:   mult*=1.6
    elif rsi_v<25: mult*=1.35
    elif rsi_v<30: mult*=1.15
    elif rsi_v<35: mult*=1.0
    else:          mult*=0.8

    bb=analysis.get("bb_pct",50)
    if bb<0:    mult*=1.25
    elif bb<10: mult*=1.1
    elif bb>90: mult*=0.8

    mwr=brain.get("micro_win_rate",0.5)
    swr=brain.get("swing_win_rate",0.5)
    if trade_type=="micro" and mwr<0.4:  mult*=0.7
    if trade_type=="swing" and swr<0.4:  mult*=0.7
    if trade_type=="micro" and mwr>0.65: mult*=1.2
    if trade_type=="swing" and swr>0.65: mult*=1.2

    if datetime.now().hour==brain.get("best_hour",9): mult*=1.1

    size=round(base_size*mult)
    return max(50,min(MAX_TRADE,size)),mult

def brain_summary(brain):
    total=brain.get("total_trades",0)
    wins=brain.get("wins",0); losses=brain.get("losses",0)
    pnl=brain.get("total_pnl",0.0); res=wins+losses
    if total==0: return "Brain: No trades yet"
    wr=f"{wins/res*100:.0f}%" if res>0 else "--"
    mult=brain.get("size_multiplier",1.0)
    best=", ".join(brain.get("best_assets",[])) or "learning..."
    avoid=", ".join(brain.get("worst_assets",[])) or "none yet"
    lines=[
        f"Brain: {total} trades | WR: {wr} | P&L: ${pnl:+.2f}",
        f"Size: {mult:.1f}x | Best: {best}",
        f"Avoid: {avoid} | Best hour: {brain.get('best_hour',9)}:00"
    ]
    return "\n".join(lines)

def brain_full_report(brain):
    wins=brain.get("wins",0); losses=brain.get("losses",0)
    pnl=brain.get("total_pnl",0.0); res=wins+losses
    lines=["JARVIS ALPHA BRAIN REPORT",""]
    lines.append(f"Total trades: {brain.get('total_trades',0)}")
    if res>0: lines.append(f"Win rate: {wins/res*100:.1f}% ({wins}W/{losses}L)")
    lines.append(f"Total P&L: ${pnl:+.2f}")
    lines.append(f"Best trade: ${brain.get('best_trade',0):+.2f}")
    lines.append(f"Worst trade: ${brain.get('worst_trade',0):+.2f}")
    lines.append(f"Size multiplier: {brain.get('size_multiplier',1.0):.1f}x")
    lines.append(f"Best hour: {brain.get('best_hour',9)}:00")
    lines.append("")
    for label,bucket in [
        ("Asset",brain.get("assets",{})),
        ("Trade Type",brain.get("trade_types",{})),
        ("Regime",brain.get("regimes",{})),
        ("RSI Zone",brain.get("rsi_zones",{})),
        ("Hold Time",brain.get("hold_times",{})),
        ("Hour",brain.get("hours",{})),
        ("Day",brain.get("days",{})),
        ("Pattern",brain.get("patterns",{}))
    ]:
        valid=[(k,v) for k,v in bucket.items() if v.get("total",0)>=2]
        if valid:
            lines.append(f"{label}:")
            for k,v in sorted(valid,key=lambda x:x[1]["wins"]/x[1]["total"],reverse=True):
                wr=v["wins"]/v["total"]*100
                apnl=v.get("avg_pnl",0)
                lines.append(f"  {k}: {wr:.0f}% WR ({v['total']} trades) avg ${apnl:+.2f}")
            lines.append("")
    return "\n".join(lines)

# ─────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────
def tg(msg):
    clean=str(msg).replace("<b>","").replace("</b>","").replace("<i>","").replace("</i>","")
    try:
        r=requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id":TELEGRAM_CHAT,"text":clean},timeout=10)
        if r.status_code==200: log.info(f"TG: {clean[:50].strip()}")
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
# ALPACA
# ─────────────────────────────────────────
def alpaca(method,path,data=None):
    hdrs={"APCA-API-KEY-ID":ALPACA_KEY,"APCA-API-SECRET-KEY":ALPACA_SECRET,"Content-Type":"application/json"}
    try:
        if method=="GET":      r=requests.get(f"{ALPACA_BASE}{path}",headers=hdrs,timeout=10)
        elif method=="POST":   r=requests.post(f"{ALPACA_BASE}{path}",headers=hdrs,json=data,timeout=10)
        elif method=="DELETE": r=requests.delete(f"{ALPACA_BASE}{path}",headers=hdrs,timeout=10)
        if r.status_code in(200,201,204): return r.json() if r.content else {}
        log.warning(f"Alpaca {method} {path}: {r.status_code} | {r.text[:200]}")
    except Exception as e: log.error(f"Alpaca: {e}")
    return None

def get_account():   return alpaca("GET","/v2/account")
def get_positions(): return alpaca("GET","/v2/positions") or []
def get_orders():    return alpaca("GET","/v2/orders?status=open") or []

def is_market_open():
    clock=alpaca("GET","/v2/clock")
    if clock: return clock.get("is_open",False)
    now=datetime.utcnow()-timedelta(hours=4)
    if now.weekday()>=5: return False
    return 9<=now.hour<16

def buy_asset(symbol,notional,is_crypto=True):
    tif="gtc" if is_crypto else "day"
    return alpaca("POST","/v2/orders",{
        "symbol":symbol,"notional":str(round(notional,2)),
        "side":"buy","type":"market","time_in_force":tif})

def sell_asset(symbol,is_crypto=True):
    enc=symbol.replace("/","%2F")
    result=alpaca("DELETE",f"/v2/positions/{enc}")
    if result is not None: return result
    tif="gtc" if is_crypto else "day"
    return alpaca("POST","/v2/orders",{
        "symbol":symbol,"notional":"100",
        "side":"sell","type":"market","time_in_force":tif})

def get_open_count(asset):
    positions=get_positions()
    return sum(1 for p in positions if asset in str(p.get("symbol","")))

# ─────────────────────────────────────────
# MARKET DATA
# ─────────────────────────────────────────
def kraken_price(pair):
    try:
        r=requests.get(f"https://api.kraken.com/0/public/Ticker?pair={pair}",timeout=10)
        d=r.json()
        if d.get("error"): return None
        res=d["result"]
        key=list(res.keys())[0] if res else None
        if not key: return None
        t=res[key]
        return {"price":float(t["c"][0]),"high":float(t["h"][1]),
                "low":float(t["l"][1]),"open":float(t["o"]),"vol":float(t["v"][1])}
    except Exception as e: log.error(f"Kraken {pair}: {e}"); return None

def kraken_ohlc(pair,interval=60,limit=100):
    try:
        r=requests.get(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}",timeout=10)
        d=r.json()
        if d.get("error"): return []
        key=next((k for k in d["result"] if k!="last"),None)
        if not key: return []
        return [{"t":int(k[0]),"o":float(k[1]),"h":float(k[2]),
                 "l":float(k[3]),"c":float(k[4]),"v":float(k[6])}
                for k in d["result"][key][-limit:]]
    except Exception as e: log.error(f"Kraken OHLC {pair}: {e}"); return []

def alpaca_bars(symbol,limit=100):
    try:
        r=alpaca("GET",f"/v2/stocks/{symbol}/bars?timeframe=1Hour&limit={limit}&feed=iex")
        if not r: return []
        bars=r.get("bars",[])
        return [{"t":b["t"],"o":b["o"],"h":b["h"],"l":b["l"],"c":b["c"],"v":b["v"]} for b in bars]
    except Exception as e: log.error(f"Alpaca bars {symbol}: {e}"); return []

def alpaca_quote(symbol):
    try:
        r=alpaca("GET",f"/v2/stocks/{symbol}/quotes/latest?feed=iex")
        if r and "quote" in r:
            q=r["quote"]
            mid=(q.get("ap",0)+q.get("bp",0))/2
            return {"price":mid or q.get("ap",0)}
    except Exception as e: log.error(f"Quote {symbol}: {e}")
    return None

fg_cache=(50,"Neutral",0)
def get_fg():
    global fg_cache
    if time.time()-fg_cache[2]<3600: return fg_cache[0],fg_cache[1]
    try:
        r=requests.get("https://api.alternative.me/fng/?limit=1",timeout=10)
        d=r.json()
        if d.get("data"):
            v=int(d["data"][0]["value"]); l=d["data"][0]["value_classification"]
            fg_cache=(v,l,time.time()); return v,l
    except: pass
    return fg_cache[0],fg_cache[1]

# ─────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────
def calc_rsi(closes,p=14):
    if len(closes)<p+1: return 50.0
    g=l=0.0
    for i in range(len(closes)-p,len(closes)):
        d=closes[i]-closes[i-1]
        if d>0: g+=d
        else: l-=d
    ag,al=g/p,l/p
    return 100.0 if al==0 else 100-(100/(1+ag/al))

def calc_ema(data,p):
    if len(data)<p: return None
    k=2/(p+1); e=sum(data[:p])/p
    for v in data[p:]: e=v*k+e*(1-k)
    return e

def calc_boll(closes,p=20):
    if len(closes)<p: return None,None,None
    sl=closes[-p:]; mean=sum(sl)/p
    std=math.sqrt(sum((x-mean)**2 for x in sl)/p)
    return mean+2*std,mean,mean-2*std

def calc_atr(candles,p=14):
    if len(candles)<2: return 0
    trs=[max(candles[i]["h"]-candles[i]["l"],
             abs(candles[i]["h"]-candles[i-1]["c"]),
             abs(candles[i]["l"]-candles[i-1]["c"]))
         for i in range(1,len(candles))]
    return sum(trs[-p:])/min(p,len(trs))

def boll_squeeze(candles,p=20,lb=50):
    if len(candles)<lb: return False
    closes=[c["c"] for c in candles]
    widths=[]
    for i in range(lb,len(closes)):
        sl=closes[i-p:i]; mean=sum(sl)/p
        std=math.sqrt(sum((x-mean)**2 for x in sl)/p)
        if mean>0: widths.append(std*4/mean*100)
    if not widths: return False
    return widths[-1]<sum(widths)/len(widths)*0.7

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
    if chop>0.65 and rng_pct<2:    return "CHOP",f"Sideways {rng_pct:.2f}%"
    elif trend>1.5 and chop<0.55:  return "UPTREND",f"+{trend:.2f}%"
    elif trend<-1.5 and chop<0.55: return "DOWNTREND",f"{trend:.2f}%"
    elif atr_pct>0.8:               return "VOLATILE",f"ATR {atr_pct:.3f}%"
    else:                           return "RANGING",f"{trend:+.2f}%"

def analyze_asset(ticker,candles,asset):
    if not ticker or len(candles)<20: return None
    price=ticker["price"]
    open_p=ticker.get("open",price)
    chg24=(price-open_p)/open_p*100 if open_p else 0
    closes=[c["c"] for c in candles]
    rsi_v=calc_rsi(closes)
    e12=calc_ema(closes,12); e26=calc_ema(closes,26)
    macd_v=(e12-e26) if e12 and e26 else 0
    bbu,bbm,bbl=calc_boll(closes)
    bb_pct=((price-bbl)/(bbu-bbl)*100) if bbu and bbl and bbu!=bbl else 50
    atr_v=calc_atr(candles)
    squeeze=boll_squeeze(candles)
    patterns=calc_patterns(candles)
    regime,rdesc=detect_regime(candles)
    fg_v,fg_l=get_fg()
    p1h=closes[-2] if len(closes)>=2 else price
    p4h=closes[-5] if len(closes)>=5 else price
    chg1h=(price-p1h)/p1h*100 if p1h else 0
    chg4h=(price-p4h)/p4h*100 if p4h else 0
    return {
        "asset":asset,"price":price,"rsi":rsi_v,"macd":macd_v,
        "bb_pct":bb_pct,"bbu":bbu,"bbl":bbl,"atr":atr_v,
        "squeeze":squeeze,"chg1h":chg1h,"chg4h":chg4h,"chg24h":chg24,
        "fg":fg_v,"fg_label":fg_l,"regime":regime,"regime_desc":rdesc,
        "patterns":patterns,"ts":datetime.now().strftime("%H:%M:%S")
    }

# ─────────────────────────────────────────
# TRADE DECISION ENGINE
# ─────────────────────────────────────────
def check_buy(a,brain,open_trades,is_crypto=True):
    """Returns (should_buy, trade_type, size, reason)"""
    if not a: return False,"",0,""

    asset=a["asset"]
    rsi_v=a["rsi"]
    patterns=a["patterns"]
    bullish=[p[0] for p in patterns if p[1]==True]
    squeeze=a["squeeze"]
    regime=a["regime"]

    if regime==brain.get("worst_regime","") and brain.get("total_trades",0)>15:
        return False,"",0,f"Skipping worst regime {regime}"

    if asset in brain.get("worst_assets",[]) and brain.get("total_trades",0)>20:
        return False,"",0,f"Skipping underperforming asset {asset}"

    open_count=sum(1 for t in brain["trades"]
                  if t["asset"]==asset and t["status"]=="open")
    max_pos=MAX_CRYPTO_POS if is_crypto else MAX_STOCK_POS

    trade_type=""; reason=""; base_size=0

    if rsi_v<BUY_RSI_STRONG and open_count<max_pos:
        trade_type="swing"; base_size=SWING_SIZE
        reason=f"STRONG BUY RSI {rsi_v:.1f} extreme oversold"
    elif rsi_v<BUY_RSI_NORMAL and a["bb_pct"]<15 and open_count<max_pos:
        trade_type="swing"; base_size=SWING_SIZE
        reason=f"BUY RSI {rsi_v:.1f} + below Bollinger"
    elif squeeze and rsi_v<45 and a["chg1h"]>0 and open_count<max_pos:
        trade_type="swing"; base_size=SWING_SIZE
        reason=f"SWING Bollinger squeeze breakout"
    elif rsi_v<BUY_RSI_MICRO and bullish and open_count<max_pos:
        trade_type="micro"; base_size=MICRO_SIZE
        reason=f"MICRO RSI {rsi_v:.1f} + {', '.join(bullish)}"
    elif rsi_v<BUY_RSI_NORMAL and open_count<1:
        trade_type="micro"; base_size=MICRO_SIZE
        reason=f"MICRO RSI {rsi_v:.1f} dip entry"
    elif bullish and rsi_v<42 and open_count<max_pos:
        trade_type="micro"; base_size=MICRO_SIZE
        reason=f"MICRO pattern: {', '.join(bullish)}"

    if not trade_type: return False,"",0,""

    size,mult=calc_size(brain,a,base_size,trade_type)
    log.info(f"{asset} size calc: base ${base_size} x {mult:.2f} = ${size}")
    return True,trade_type,size,reason

def check_sell(a,trade,brain):
    """Returns (should_sell, reason)"""
    if not a or not trade: return False,""

    price=a["price"]; entry=trade["open_price"]
    open_dt=datetime.fromisoformat(trade["open_time"])
    held=int((datetime.now()-open_dt).total_seconds()//60)
    pct=(price-entry)/entry*100
    trade_type=trade.get("trade_type","swing")
    min_hold=MIN_HOLD_MICRO if trade_type=="micro" else MIN_HOLD_SWING

    if held<min_hold: return False,""

    if price>trade.get("trail_high",entry):
        trade["trail_high"]=price
        new_stop=round(price*(1-TRAIL_STEP/100),4)
        if new_stop>trade.get("trail_stop",0):
            trade["trail_stop"]=new_stop

    trail_stop=trade.get("trail_stop",entry*(1-STOP_LOSS/100))
    profit_target=PROFIT_MICRO if trade_type=="micro" else PROFIT_SWING
    bearish=[p[0] for p in a["patterns"] if p[1]==False]

    if price<=trail_stop:
        return True,f"Trail stop ${trail_stop:.2f} ({pct:+.2f}%)"
    if pct>=profit_target:
        return True,f"Profit target +{pct:.2f}% hit"
    if pct<=-STOP_LOSS:
        return True,f"Stop loss {pct:.2f}%"
    if a["rsi"]>SELL_RSI and pct>0.2:
        return True,f"RSI {a['rsi']:.1f} recovered + profitable"
    if a["rsi"]>70:
        return True,f"RSI {a['rsi']:.1f} overbought - take profit"
    if bearish and held>15 and pct>0.2:
        return True,f"Bearish pattern {', '.join(bearish)} + profitable"

    return False,""

# ─────────────────────────────────────────
# STATUS + DAILY REPORT
# ─────────────────────────────────────────
def send_status(brain,analyses=None):
    try:
        acct=get_account()
        if not acct: tg("Alpaca connection failed"); return
        eq=float(acct.get("equity",0))
        leq=float(acct.get("last_equity",eq))
        dpl=eq-leq; dpct=dpl/leq*100 if leq else 0
        positions=get_positions()
        open_trades=[t for t in brain["trades"] if t["status"]=="open"]
        lines=[
            "JARVIS ALPHA STATUS",
            f"Paper Equity: ${eq:,.2f}",
            f"Daily PnL: ${dpl:+.2f} ({dpct:+.2f}%)",
            f"Open trades: {len(open_trades)}",
        ]
        if positions:
            lines.append("\nPositions:")
            for p in positions:
                upl=float(p.get("unrealized_pl",0))
                mv=float(p.get("market_value",0))
                lines.append(f"  {p.get('symbol')} ${mv:,.2f} PnL: ${upl:+.2f}")
        lines.append("")
        lines.append(brain_summary(brain))
        market_open=is_market_open()
        lines.append(f"\nStock market: {'OPEN' if market_open else 'CLOSED'}")
        if analyses:
            lines.append("\nMarket snapshot:")
            for a in analyses[:4]:
                lines.append(f"  {a['asset']}: ${a['price']:,.2f} RSI:{a['rsi']:.0f} {a['regime']}")
        tg("\n".join(lines))
    except Exception as e:
        log.error(f"Status error: {e}"); tg(f"Status error: {e}")

def send_daily_report(brain):
    """Morning report"""
    try:
        acct=get_account()
        if not acct: return
        eq=float(acct.get("equity",0))
        leq=float(acct.get("last_equity",eq))
        dpl=eq-leq; dpct=dpl/leq*100 if leq else 0

        yesterday=datetime.now()-timedelta(days=1)
        yest_trades=[t for t in brain["trades"]
                    if t.get("close_time","")>yesterday.isoformat()
                    and t["status"]=="closed"]
        yest_pnl=sum(t.get("pnl",0) for t in yest_trades)
        yest_wins=sum(1 for t in yest_trades if t.get("won"))

        lines=[
            "JARVIS ALPHA - MORNING REPORT",
            "Good morning Lenny!",
            "",
            f"Paper Equity: ${eq:,.2f}",
            f"24h PnL: ${dpl:+.2f} ({dpct:+.2f}%)",
            "",
            f"Yesterday: {len(yest_trades)} trades",
            f"  PnL: ${yest_pnl:+.2f}",
            f"  Wins: {yest_wins}/{len(yest_trades)}",
            "",
            brain_summary(brain),
            "",
            "Today's plan: Bot is running. I'll alert you on every trade.",
            "Commands: STATUS / BRAIN / STOP"
        ]
        tg("\n".join(lines))
    except Exception as e:
        log.error(f"Daily report error: {e}")

# ─────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────
def main():
    log.info("="*65)
    log.info("JARVIS ALPHA - Full Market Trading System")
    log.info("Crypto: BTC, ETH, SOL, AVAX | Stocks: SPY, QQQ, NVDA, TSLA, COIN")
    log.info("Swing trades + Micro trades | Trailing stops | Brain learning")
    log.info(f"Micro: ${MICRO_SIZE} target +{PROFIT_MICRO}% | Swing: ${SWING_SIZE} target +{PROFIT_SWING}%")
    log.info(f"Stop loss: -{STOP_LOSS}% | Trail step: {TRAIL_STEP}%")
    log.info("="*65)

    brain=load_brain()
    acct=get_account()
    if not acct:
        log.error("Alpaca connection failed")
        tg("JARVIS ALPHA: Alpaca connection failed - check keys")
        return

    eq=float(acct.get("equity",0))
    log.info(f"Connected - Paper equity: ${eq:,.2f}")
    log.info(f"Brain: {brain.get('total_trades',0)} trades loaded")

    tg(f"JARVIS ALPHA ONLINE\n\nPaper Trading: ${eq:,.2f}\nCrypto: BTC, ETH, SOL, AVAX\nStocks: SPY, QQQ, NVDA, TSLA, COIN\nMicro + Swing trades\nBrain: {brain.get('total_trades',0)} trades learned\nSize: {brain.get('size_multiplier',1.0):.1f}x\n\n{brain_summary(brain)}\n\nCommands: STATUS / BRAIN / STOP")

    tg_offset=None
    last_crypto_poll=0
    last_stock_poll=0
    last_daily=0
    last_report_day=-1
    open_trades={}

    log.info("Running. Ctrl+C to stop.")

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
            elif text=="STOP":
                tg("JARVIS ALPHA stopped."); return
            elif text=="HELP":
                tg("Commands:\nSTATUS - portfolio + positions\nBRAIN - full learning report\nSTOP - stop bot\nHELP - this menu")

        # MORNING REPORT
        if current_hour==REPORT_HOUR and current_day!=last_report_day:
            last_report_day=current_day
            send_daily_report(brain)

        # CHECK OPEN TRADES
        if open_trades:
            for order_id,trade in list(open_trades.items()):
                asset=trade["asset"]
                is_crypto=asset in CRYPTO_PAIRS
                if is_crypto:
                    kraken_sym=CRYPTO_PAIRS[asset][0]
                    ticker=kraken_price(kraken_sym)
                    candles=kraken_ohlc(kraken_sym,60,50)
                else:
                    quote=alpaca_quote(asset)
                    ticker=quote
                    candles=alpaca_bars(asset,50)
                if not ticker or not candles: continue
                a=analyze_asset(ticker,candles,asset)
                if not a: continue
                sell,reason=check_sell(a,trade,brain)
                if sell:
                    alpaca_sym=CRYPTO_PAIRS[asset][1] if is_crypto else asset
                    result=sell_asset(alpaca_sym,is_crypto)
                    if result:
                        closed=record_close(brain,order_id,a["price"],reason)
                        pnl=closed["pnl"] if closed else 0
                        won=closed["won"] if closed else False
                        held=closed.get("hold_mins",0) if closed else 0
                        pct=(a["price"]-trade["open_price"])/trade["open_price"]*100
                        emoji="WIN" if won else "LOSS"
                        tg(f"{emoji} CLOSE {asset} ({trade.get('trade_type','').upper()})\n\nEntry: ${trade['open_price']:,.2f} -> Exit: ${a['price']:,.2f}\nHeld: {held}min | P&L: ${pnl:+.2f} ({pct:+.2f}%)\nReason: {reason}\n\n{brain_summary(brain)}")
                        del open_trades[order_id]
                    else:
                        tg(f"SELL FAILED {asset} - check Alpaca")

        # CRYPTO ANALYSIS
        if now-last_crypto_poll>=CRYPTO_POLL:
            last_crypto_poll=now
            log.info("--- CRYPTO SCAN ---")
            for asset,(kraken_sym,alpaca_sym) in CRYPTO_PAIRS.items():
                ticker=kraken_price(kraken_sym)
                candles=kraken_ohlc(kraken_sym,60,100)
                if not ticker or not candles:
                    log.warning(f"{asset}: no data"); continue
                a=analyze_asset(ticker,candles,asset)
                if not a: continue
                log.info(f"{asset} ${a['price']:,.2f} RSI:{a['rsi']:.1f} MACD:{a['macd']:+.0f} BB%B:{a['bb_pct']:.1f}% {a['regime']}")
                if a["squeeze"]: log.info(f"{asset}: SQUEEZE detected")
                if a["patterns"]: log.info(f"{asset} patterns: {[p[0] for p in a['patterns']]}")
                buy,trade_type,size,reason=check_buy(a,brain,open_trades,is_crypto=True)
                if buy:
                    log.info(f"{asset} BUY ({trade_type}): {reason} ${size}")
                    result=buy_asset(alpaca_sym,size,is_crypto=True)
                    if result:
                        order_id=result.get("id","?")
                        trade=record_open(brain,asset,trade_type,order_id,a["price"],a,size)
                        open_trades[order_id]=trade
                        pat_str=", ".join(p[0] for p in a["patterns"]) if a["patterns"] else "None"
                        squeeze_note=" SQUEEZE" if a["squeeze"] else ""
                        tg(f"BUY {asset} {trade_type.upper()}{squeeze_note} ${size}\n\nPrice: ${a['price']:,.2f}\nRSI: {a['rsi']:.1f} | BB%B: {a['bb_pct']:.1f}%\nReason: {reason}\nPatterns: {pat_str}\nRegime: {a['regime']}\nTrail stop: ${trade['trail_stop']:,.4f}\nOrder: {order_id}")
                    else:
                        tg(f"BUY FAILED {asset} - check Alpaca")
                else:
                    nb=reason if reason else "RSI "+str(round(a["rsi"],1))
                    log.info(f"{asset}: no buy ({nb})")

        # STOCK ANALYSIS
        if now-last_stock_poll>=STOCK_POLL:
            last_stock_poll=now
            market_open=is_market_open()
            if market_open:
                log.info("--- STOCK SCAN ---")
                for symbol in STOCK_SYMBOLS:
                    quote=alpaca_quote(symbol)
                    candles=alpaca_bars(symbol,100)
                    if not quote or not candles:
                        log.warning(f"{symbol}: no data"); continue
                    a=analyze_asset(quote,candles,symbol)
                    if not a: continue
                    log.info(f"{symbol} ${a['price']:.2f} RSI:{a['rsi']:.1f} BB%B:{a['bb_pct']:.1f}% {a['regime']}")
                    if a["patterns"]: log.info(f"{symbol} patterns: {[p[0] for p in a['patterns']]}")
                    buy,trade_type,size,reason=check_buy(a,brain,open_trades,is_crypto=False)
                    if buy:
                        log.info(f"{symbol} BUY ({trade_type}): {reason} ${size}")
                        result=buy_asset(symbol,size,is_crypto=False)
                        if result:
                            order_id=result.get("id","?")
                            trade=record_open(brain,symbol,trade_type,order_id,a["price"],a,size)
                            open_trades[order_id]=trade
                            pat_str=", ".join(p[0] for p in a["patterns"]) if a["patterns"] else "None"
                            tg(f"BUY {symbol} {trade_type.upper()} ${size}\n\nPrice: ${a['price']:.2f}\nRSI: {a['rsi']:.1f} | BB%B: {a['bb_pct']:.1f}%\nReason: {reason}\nPatterns: {pat_str}\nRegime: {a['regime']}\nTrail stop: ${trade['trail_stop']:.2f}\nOrder: {order_id}")
                        else:
                            tg(f"BUY FAILED {symbol} - check Alpaca")
                    else:
                        nb=reason if reason else "RSI "+str(round(a["rsi"],1))
                        log.info(f"{symbol}: no buy ({nb})")
            else:
                log.info("Stock market closed - crypto only")

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
        tg("JARVIS ALPHA stopped (Ctrl+C)")
    except Exception as e:
        log.error(f"Fatal: {e}")
        tg(f"JARVIS ALPHA crashed: {e}")
