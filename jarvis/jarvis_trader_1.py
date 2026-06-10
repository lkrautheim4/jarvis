#!/usr/bin/env python3
"""
JARVIS Trading Bot — BRAIN EDITION
Full learning system, scalping, trailing stops, multi-position,
active memory that adjusts future trades, ETH trading, volatility sizing
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

BASE_TRADE    = 200    # base trade size USD (scales with confidence)
MAX_TRADE     = 800    # max trade size USD
MAX_POSITIONS = 3      # max simultaneous positions per coin
POLL_INTERVAL = 90     # seconds between checks (faster for scalping)
MEMORY_FILE   = "jarvis_brain.json"
TRADES_FILE   = "jarvis_trades.json"

# Entry thresholds
BUY_RSI_STRONG  = 25   # RSI below this = strong buy, full size
BUY_RSI_NORMAL  = 33   # RSI below this = normal buy
BUY_RSI_SCALE   = 40   # RSI below this = small buy (scaling in)
SELL_RSI        = 55   # RSI above this = start looking to sell
PROFIT_TARGET   = 1.2  # take profit at +1.2%
STOP_LOSS       = 1.8  # stop loss at -1.8%
TRAIL_STEP      = 0.4  # trail stop every 0.4% gain
MIN_HOLD_MINS   = 10   # minimum hold time

# Active trading hours (ET) - BTC most volatile
ACTIVE_HOURS = list(range(8,13)) + list(range(20,24))  # 8am-1pm, 8pm-midnight

import logging
logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('JARVIS')

# ─────────────────────────────────────────
# JARVIS BRAIN — Full Learning System
# ─────────────────────────────────────────
def load_brain():
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE,'r') as f: return json.load(f)
    except: pass
    return {
        # Trade history
        "trades": [],
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "total_pnl": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,

        # Pattern learning
        "patterns": {},        # pattern -> {wins, total, avg_pnl}
        "regimes": {},         # regime -> {wins, total, avg_pnl}
        "rsi_zones": {},       # rsi_zone -> {wins, total, avg_pnl}
        "hours": {},           # hour -> {wins, total}
        "coins": {},           # coin -> {wins, total, pnl}
        "hold_times": {},      # hold_bucket -> {wins, total}
        "confluences": {},     # regime|rsi|pattern combo -> {wins, total, avg_pnl}

        # Market knowledge
        "avg_btc_atr": 500,    # average BTC hourly ATR in USD
        "avg_eth_atr": 20,     # average ETH hourly ATR in USD
        "best_hour": 9,        # historically best trading hour
        "worst_regime": "CHOP",# regime to avoid

        # Adaptive thresholds (Jarvis adjusts these over time)
        "buy_rsi_adj": 0,      # adjustment to buy RSI threshold
        "sell_rsi_adj": 0,     # adjustment to sell RSI threshold
        "size_multiplier": 1.0,# position size multiplier based on performance
        "confidence_boost": {},# signal type -> confidence adjustment

        "created": datetime.now().isoformat(),
        "last_updated": datetime.now().isoformat()
    }

def save_brain(b):
    try:
        b["last_updated"] = datetime.now().isoformat()
        with open(MEMORY_FILE,'w') as f: json.dump(b,f,indent=2)
    except Exception as e: log.error(f"Brain save error: {e}")

def rsi_zone(v):
    if v>=75: return "EXTREME_OB"
    if v>=65: return "OVERBOUGHT"
    if v>=55: return "BULLISH"
    if v>=45: return "NEUTRAL"
    if v>=35: return "BEARISH"
    if v>=25: return "OVERSOLD"
    return "EXTREME_OS"

def hold_bucket(mins):
    if mins<15: return "<15min"
    if mins<30: return "15-30min"
    if mins<60: return "30-60min"
    if mins<120: return "1-2hr"
    return "2hr+"

def update_bucket(b, k, won, pnl=0):
    if k not in b: b[k]={"wins":0,"total":0,"avg_pnl":0.0}
    b[k]["total"]+=1
    if won: b[k]["wins"]+=1
    # Running average PnL
    n=b[k]["total"]
    b[k]["avg_pnl"]=round((b[k]["avg_pnl"]*(n-1)+pnl)/n, 3)

def get_win_rate(b, k, min_trades=3):
    if k not in b or b[k]["total"]<min_trades: return 0.5, 0
    return b[k]["wins"]/b[k]["total"], b[k]["total"]

def brain_record_open(brain, coin, direction, order_id, price, analysis, size):
    trade = {
        "id": order_id,
        "coin": coin,
        "direction": direction,
        "open_price": price,
        "open_time": datetime.now().isoformat(),
        "size": size,
        "rsi": round(analysis["rsi"],1),
        "rsi_zone": rsi_zone(analysis["rsi"]),
        "macd": round(analysis["macd"],1),
        "bb_pct": round(analysis["bb_pct"],1),
        "regime": analysis["regime"],
        "patterns": [p[0] for p in analysis["patterns"]],
        "fg": analysis["fg"],
        "hour": datetime.now().hour,
        "status": "open",
        "trail_stop": round(price*(1-(STOP_LOSS/100)),2),
        "trail_high": price,
        "close_price": None,
        "pnl": None,
        "won": None,
        "hold_mins": None,
        "close_reason": None
    }
    brain["trades"].append(trade)
    brain["total_trades"]+=1
    save_brain(brain)
    return trade

def brain_record_close(brain, order_id, close_price, reason):
    for t in brain["trades"]:
        if t["id"]==order_id and t["status"]=="open":
            t["close_price"]=close_price
            t["close_time"]=datetime.now().isoformat()
            t["status"]="closed"
            t["close_reason"]=reason
            open_dt=datetime.fromisoformat(t["open_time"])
            held_mins=int((datetime.now()-open_dt).total_seconds()//60)
            t["hold_mins"]=held_mins
            if t["direction"]=="BUY":
                pnl=(close_price-t["open_price"])/t["open_price"]*t["size"]
            else:
                pnl=(t["open_price"]-close_price)/t["open_price"]*t["size"]
            t["pnl"]=round(pnl,2)
            t["won"]=pnl>0
            won=t["won"]
            # Update all learning buckets
            update_bucket(brain["regimes"],   t["regime"],   won, pnl)
            update_bucket(brain["rsi_zones"], t["rsi_zone"], won, pnl)
            update_bucket(brain["hours"],     str(t["hour"]),won, pnl)
            update_bucket(brain["coins"],     t["coin"],     won, pnl)
            update_bucket(brain["hold_times"],hold_bucket(held_mins),won,pnl)
            for pat in t["patterns"]:
                update_bucket(brain["patterns"], pat, won, pnl)
            # Confluence key
            conf_key=f"{t['regime']}|{t['rsi_zone']}"
            update_bucket(brain["confluences"],conf_key,won,pnl)
            # Totals
            brain["total_pnl"]=round(brain.get("total_pnl",0)+pnl,2)
            if won:
                brain["wins"]=brain.get("wins",0)+1
                brain["best_trade"]=max(brain.get("best_trade",0),pnl)
            else:
                brain["losses"]=brain.get("losses",0)+1
                brain["worst_trade"]=min(brain.get("worst_trade",0),pnl)
            # ADAPTIVE LEARNING — Jarvis adjusts thresholds
            _adapt_brain(brain)
            save_brain(brain)
            log.info(f"Brain updated: {t['coin']} {t['direction']} P&L ${pnl:+.2f} | {'WIN' if won else 'LOSS'}")
            return t
    return None

def _adapt_brain(brain):
    """
    Jarvis adjusts its own trading parameters based on performance.
    This is the actual learning — not just recording, but changing behavior.
    """
    wins=brain.get("wins",0); losses=brain.get("losses",0)
    resolved=wins+losses
    if resolved<5: return  # need minimum data

    overall_wr=wins/resolved

    # Adjust position size multiplier based on win rate
    if overall_wr>0.65:
        brain["size_multiplier"]=min(2.0, brain.get("size_multiplier",1.0)+0.1)
        log.info(f"Brain: Win rate {overall_wr*100:.0f}% > 65% -> size multiplier UP to {brain['size_multiplier']:.1f}x")
    elif overall_wr<0.40:
        brain["size_multiplier"]=max(0.5, brain.get("size_multiplier",1.0)-0.1)
        log.info(f"Brain: Win rate {overall_wr*100:.0f}% < 40% -> size multiplier DOWN to {brain['size_multiplier']:.1f}x")

    # Find worst regime and avoid it
    worst_wr=1.0; worst_regime=None
    for regime,data in brain["regimes"].items():
        if data["total"]>=3:
            wr=data["wins"]/data["total"]
            if wr<worst_wr:
                worst_wr=wr; worst_regime=regime
    if worst_regime: brain["worst_regime"]=worst_regime

    # Find best trading hour
    best_wr=0; best_hour=9
    for hour,data in brain["hours"].items():
        if data["total"]>=2:
            wr=data["wins"]/data["total"]
            if wr>best_wr:
                best_wr=wr; best_hour=int(hour)
    brain["best_hour"]=best_hour

def brain_size_recommendation(brain, analysis, base_size):
    """
    Jarvis calculates optimal trade size based on:
    - Overall win rate
    - Current regime historical performance
    - RSI zone historical performance
    - Current confidence level
    - Time of day
    """
    multiplier=brain.get("size_multiplier",1.0)

    # Regime adjustment
    wr,n=get_win_rate(brain["regimes"], analysis["regime"])
    if n>=3:
        if wr>0.65: multiplier*=1.3
        elif wr<0.40: multiplier*=0.6

    # RSI zone adjustment
    zone=rsi_zone(analysis["rsi"])
    wr,n=get_win_rate(brain["rsi_zones"], zone)
    if n>=3:
        if wr>0.65: multiplier*=1.2
        elif wr<0.40: multiplier*=0.7

    # Hour adjustment
    hour=str(datetime.now().hour)
    wr,n=get_win_rate(brain["hours"], hour)
    if n>=2:
        if wr>0.65: multiplier*=1.1
        elif wr<0.35: multiplier*=0.8

    # RSI strength adjustment (more oversold = bigger position)
    rsi_v=analysis["rsi"]
    if rsi_v<20:   multiplier*=1.5
    elif rsi_v<25: multiplier*=1.25
    elif rsi_v<30: multiplier*=1.0
    else:          multiplier*=0.75

    # Bollinger adjustment
    if analysis["bb_pct"]<0: multiplier*=1.2    # below lower band
    elif analysis["bb_pct"]<10: multiplier*=1.1

    size=round(base_size*multiplier)
    size=max(50, min(MAX_TRADE, size))  # clamp between $50 and max
    return size, multiplier

def brain_summary(brain):
    total=brain.get("total_trades",0)
    wins=brain.get("wins",0); losses=brain.get("losses",0)
    pnl=brain.get("total_pnl",0.0); res=wins+losses
    if total==0: return "Brain: No trades yet - learning starts now"
    wr=f"{wins/res*100:.0f}%" if res>0 else "--"
    mult=brain.get("size_multiplier",1.0)
    lines=[f"Brain: {total} trades | WR: {wr} | P&L: ${pnl:+.2f}"]
    lines.append(f"Size multiplier: {mult:.1f}x | Best hour: {brain.get('best_hour',9)}:00")
    if brain.get("worst_regime"): lines.append(f"Avoiding: {brain['worst_regime']} (low WR)")
    return "\n".join(lines)

def brain_full_report(brain):
    wins=brain.get("wins",0); losses=brain.get("losses",0)
    pnl=brain.get("total_pnl",0.0); res=wins+losses
    lines=["JARVIS BRAIN REPORT",""]
    lines.append(f"Total trades: {brain.get('total_trades',0)}")
    lines.append(f"Resolved: {res} | Win rate: {wins/res*100:.1f}%" if res>0 else "Resolved: 0")
    lines.append(f"Total P&L: ${pnl:+.2f}")
    lines.append(f"Best trade: ${brain.get('best_trade',0):+.2f}")
    lines.append(f"Worst trade: ${brain.get('worst_trade',0):+.2f}")
    lines.append(f"Size multiplier: {brain.get('size_multiplier',1.0):.1f}x")
    lines.append("")
    for label,bucket in [
        ("Regime",brain.get("regimes",{})),
        ("RSI Zone",brain.get("rsi_zones",{})),
        ("Hour",brain.get("hours",{})),
        ("Coin",brain.get("coins",{})),
        ("Hold Time",brain.get("hold_times",{})),
        ("Pattern",brain.get("patterns",{}))
    ]:
        valid=[(k,v) for k,v in bucket.items() if v.get("total",0)>=2]
        if valid:
            lines.append(f"{label}:")
            for k,v in sorted(valid,key=lambda x:x[1]["wins"]/x[1]["total"],reverse=True):
                wr=v["wins"]/v["total"]*100
                avg_pnl=v.get("avg_pnl",0)
                lines.append(f"  {k}: {wr:.0f}% WR ({v['total']} trades) avg ${avg_pnl:+.2f}")
            lines.append("")
    return "\n".join(lines)

# ─────────────────────────────────────────
# TELEGRAM — plain text, no parse errors
# ─────────────────────────────────────────
def tg(msg):
    # Clean any problematic characters
    clean=msg.replace("<b>","").replace("</b>","").replace("<i>","").replace("</i>","")
    try:
        r=requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id":TELEGRAM_CHAT,"text":clean},
            timeout=10)
        if r.status_code==200:
            log.info(f"TG: {clean[:50].strip()}")
        else:
            log.warning(f"TG failed: {r.text[:80]}")
    except Exception as e:
        log.error(f"TG error: {e}")

def tg_updates(offset=None):
    try:
        p={"timeout":5,"allowed_updates":["message"]}
        if offset: p["offset"]=offset
        r=requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params=p,timeout=10)
        if r.status_code==200: return r.json().get("result",[])
    except: pass
    return []

# ─────────────────────────────────────────
# ALPACA
# ─────────────────────────────────────────
def alpaca(method,path,data=None):
    hdrs={
        "APCA-API-KEY-ID":ALPACA_KEY,
        "APCA-API-SECRET-KEY":ALPACA_SECRET,
        "Content-Type":"application/json"
    }
    try:
        if method=="GET":      r=requests.get(f"{ALPACA_BASE}{path}",headers=hdrs,timeout=10)
        elif method=="POST":   r=requests.post(f"{ALPACA_BASE}{path}",headers=hdrs,json=data,timeout=10)
        elif method=="DELETE": r=requests.delete(f"{ALPACA_BASE}{path}",headers=hdrs,timeout=10)
        if r.status_code in(200,201,204):
            return r.json() if r.content else {}
        log.warning(f"Alpaca {method} {path}: {r.status_code} | {r.text[:200]}")
    except Exception as e:
        log.error(f"Alpaca error: {e}")
    return None

def get_account():   return alpaca("GET","/v2/account")
def get_positions(): return alpaca("GET","/v2/positions") or []

def count_btc_positions():
    return sum(1 for p in get_positions() if "BTC" in str(p.get("symbol","")))

def count_eth_positions():
    return sum(1 for p in get_positions() if "ETH" in str(p.get("symbol","")))

def get_coin_pnl(coin):
    total_mv=0; total_upl=0
    for p in get_positions():
        sym=str(p.get("symbol",""))
        if coin in sym:
            total_mv+=float(p.get("market_value",0))
            total_upl+=float(p.get("unrealized_pl",0))
    return total_mv, total_upl

def place_buy(symbol, notional):
    return alpaca("POST","/v2/orders",{
        "symbol":symbol,"notional":str(round(notional,2)),
        "side":"buy","type":"market","time_in_force":"gtc"})

def close_all_coin(coin_sym):
    """Close all positions for a coin"""
    result=alpaca("DELETE",f"/v2/positions/{coin_sym}")
    if result is not None: return result
    # Fallback
    return alpaca("POST","/v2/orders",{
        "symbol":coin_sym.replace("%2F","/"),
        "notional":"100","side":"sell",
        "type":"market","time_in_force":"gtc"})

# ─────────────────────────────────────────
# KRAKEN DATA
# ─────────────────────────────────────────
def kraken_ticker(pairs="XBTUSD,ETHUSD"):
    try:
        r=requests.get(f"https://api.kraken.com/0/public/Ticker?pair={pairs}",timeout=10)
        d=r.json()
        if d.get("error"): return None,None
        res=d["result"]
        btc_data=res.get("XXBTZUSD") or res.get("XBTUSD")
        eth_data=res.get("XETHZUSD") or res.get("ETHUSD")
        def parse(t):
            if not t: return None
            return {"price":float(t["c"][0]),"high":float(t["h"][1]),
                    "low":float(t["l"][1]),"open":float(t["o"]),"vol":float(t["v"][1])}
        return parse(btc_data),parse(eth_data)
    except Exception as e:
        log.error(f"Kraken ticker: {e}"); return None,None

def kraken_ohlc(pair="XBTUSD",interval=60,limit=100):
    try:
        r=requests.get(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}",timeout=10)
        d=r.json()
        if d.get("error"): return []
        key=next((k for k in d["result"] if k!="last"),None)
        if not key: return []
        return [{"t":int(k[0]),"o":float(k[1]),"h":float(k[2]),
                 "l":float(k[3]),"c":float(k[4]),"v":float(k[6])}
                for k in d["result"][key][-limit:]]
    except Exception as e:
        log.error(f"Kraken OHLC {pair}: {e}"); return []

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

def boll_squeeze(candles,p=20,lookback=50):
    """Detect Bollinger Band squeeze - predicts big move coming"""
    if len(candles)<lookback: return False,0
    closes=[c["c"] for c in candles]
    widths=[]
    for i in range(lookback,len(closes)):
        sl=closes[i-p:i]; mean=sum(sl)/p
        std=math.sqrt(sum((x-mean)**2 for x in sl)/p)
        widths.append(std*4/mean*100)  # band width as % of price
    if not widths: return False,0
    curr_width=widths[-1]
    avg_width=sum(widths)/len(widths)
    is_squeeze=curr_width<avg_width*0.7  # 30% tighter than average
    return is_squeeze, curr_width

def calc_patterns(candles):
    if len(candles)<3: return []
    p=[]; c=candles[-1]; p1=candles[-2]; p2=candles[-3]
    body=abs(c["c"]-c["o"]); rng=c["h"]-c["l"]
    uw=c["h"]-max(c["c"],c["o"]); lw=min(c["c"],c["o"])-c["l"]
    if rng==0: return p
    if p1["c"]<p1["o"] and c["c"]>c["o"] and c["o"]<p1["c"] and c["c"]>p1["o"]:
        p.append(("BULLISH_ENGULFING",True))
    if p1["c"]>p1["o"] and c["c"]<c["o"] and c["o"]>p1["c"] and c["c"]<p1["o"]:
        p.append(("BEARISH_ENGULFING",False))
    if lw>body*2 and uw<body*0.5 and body>0:
        p.append(("HAMMER",True))
    if uw>body*2 and lw<body*0.5 and body>0:
        p.append(("SHOOTING_STAR",False))
    if body<rng*0.1:
        p.append(("DOJI",None))
    if len(candles)>=3:
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
    rng=max(highs)-min(lows); avg=sum(closes)/len(closes)
    rng_pct=rng/avg*100
    mid=len(closes)//2
    f_avg=sum(closes[:mid])/mid
    s_avg=sum(closes[mid:])/len(closes[mid:])
    trend=(s_avg-f_avg)/f_avg*100
    chop=sum(1 for i in range(2,len(closes))
             if (closes[i]-closes[i-1])*(closes[i-1]-closes[i-2])<0)/max(1,len(closes)-2)
    atr_v=calc_atr(candles); atr_pct=atr_v/avg*100 if avg else 0
    if chop>0.65 and rng_pct<2:    return "CHOP",f"Sideways {rng_pct:.2f}%"
    elif trend>1.5 and chop<0.55:  return "UPTREND",f"+{trend:.2f}%"
    elif trend<-1.5 and chop<0.55: return "DOWNTREND",f"{trend:.2f}%"
    elif atr_pct>0.8:               return "VOLATILE",f"ATR {atr_pct:.3f}%"
    else:                           return "RANGING",f"{trend:+.2f}%"

def analyze(ticker,candles,coin="BTC"):
    if not ticker or len(candles)<20: return None
    price=ticker["price"]
    chg24=(price-ticker["open"])/ticker["open"]*100 if ticker["open"] else 0
    closes=[c["c"] for c in candles]
    rsi_v=calc_rsi(closes)
    e12=calc_ema(closes,12); e26=calc_ema(closes,26)
    macd_v=(e12-e26) if e12 and e26 else 0
    bbu,bbm,bbl=calc_boll(closes)
    bb_pct=((price-bbl)/(bbu-bbl)*100) if bbu and bbl and bbu!=bbl else 50
    atr_v=calc_atr(candles)
    squeeze,bw=boll_squeeze(candles)
    patterns=calc_patterns(candles)
    regime,rdesc=detect_regime(candles)
    fg_v,fg_l=get_fg()
    p1h=closes[-2] if len(closes)>=2 else price
    p4h=closes[-5] if len(closes)>=5 else price
    chg1h=(price-p1h)/p1h*100 if p1h else 0
    chg4h=(price-p4h)/p4h*100 if p4h else 0
    return {
        "coin":coin,"price":price,"rsi":rsi_v,"macd":macd_v,
        "bb_pct":bb_pct,"bbu":bbu,"bbl":bbl,"atr":atr_v,
        "squeeze":squeeze,"bw":bw,
        "chg1h":chg1h,"chg4h":chg4h,"chg24h":chg24,
        "fg":fg_v,"fg_label":fg_l,"regime":regime,"regime_desc":rdesc,
        "patterns":patterns,"ts":datetime.now().strftime("%H:%M:%S")
    }

# ─────────────────────────────────────────
# BRAIN-POWERED BUY/SELL DECISIONS
# ─────────────────────────────────────────
def should_buy(a, brain, current_positions):
    """Brain decides whether to buy and what size"""
    if not a: return False, 0, ""

    # Skip worst regime
    if a["regime"]==brain.get("worst_regime","") and brain.get("total_trades",0)>10:
        return False, 0, f"Skipping worst regime: {a['regime']}"

    # Check active hours boost
    hour=datetime.now().hour
    in_active=hour in ACTIVE_HOURS
    best_hour=brain.get("best_hour",9)

    rsi_v=a["rsi"]
    patterns=a["patterns"]
    bullish=[p[0] for p in patterns if p[1]==True]
    squeeze=a["squeeze"]

    # Buy signal logic
    buy_signal=False; reason=""

    if rsi_v < BUY_RSI_STRONG:
        buy_signal=True
        reason=f"STRONG BUY - RSI {rsi_v:.1f} extreme oversold"
    elif rsi_v < BUY_RSI_NORMAL and a["bb_pct"]<15:
        buy_signal=True
        reason=f"BUY - RSI {rsi_v:.1f} oversold + below Bollinger"
    elif rsi_v < BUY_RSI_SCALE and bullish:
        buy_signal=True
        reason=f"BUY - RSI {rsi_v:.1f} + {', '.join(bullish)}"
    elif squeeze and rsi_v<45 and a["chg1h"]>0:
        buy_signal=True
        reason=f"BUY - Bollinger squeeze + upward momentum"
    elif bullish and rsi_v<40:
        buy_signal=True
        reason=f"BUY - Bullish pattern: {', '.join(bullish)}"

    if not buy_signal: return False, 0, ""

    # Check max positions
    coin_positions=sum(1 for t in brain["trades"]
                      if t["coin"]==a["coin"] and t["status"]=="open")
    if coin_positions>=MAX_POSITIONS:
        return False, 0, f"Max positions ({MAX_POSITIONS}) reached for {a['coin']}"

    # Brain-powered position sizing
    size, mult = brain_size_recommendation(brain, a, BASE_TRADE)

    # Hour boost
    if hour==best_hour: size=min(MAX_TRADE,int(size*1.15))

    return True, size, reason

def should_sell(a, trade, brain):
    """Brain decides whether to sell based on conditions and learning"""
    if not a or not trade: return False, ""

    price=a["price"]
    entry=trade["open_price"]
    open_dt=datetime.fromisoformat(trade["open_time"])
    held_mins=int((datetime.now()-open_dt).total_seconds()//60)
    pct_chg=(price-entry)/entry*100

    if held_mins<MIN_HOLD_MINS: return False, ""

    # Update trailing stop
    if price>trade.get("trail_high",entry):
        trade["trail_high"]=price
        new_stop=round(price*(1-TRAIL_STEP/100),2)
        if new_stop>trade.get("trail_stop",0):
            trade["trail_stop"]=new_stop
            log.info(f"Trail stop updated: ${new_stop:,.2f} (+{TRAIL_STEP}% trail)")

    trail_stop=trade.get("trail_stop",entry*(1-STOP_LOSS/100))
    bearish=[p[0] for p in a["patterns"] if p[1]==False]

    # Sell conditions
    if price<=trail_stop:
        return True, f"Trailing stop hit ${trail_stop:,.2f} ({pct_chg:+.2f}%)"
    if pct_chg>=PROFIT_TARGET:
        return True, f"Profit target hit: +{pct_chg:.2f}%"
    if pct_chg<=-STOP_LOSS:
        return True, f"Hard stop loss: {pct_chg:.2f}%"
    if a["rsi"]>SELL_RSI and pct_chg>0:
        return True, f"RSI {a['rsi']:.1f} overbought + profitable"
    if a["rsi"]>65 and a["bb_pct"]>85:
        return True, f"RSI {a['rsi']:.1f} + above upper Bollinger"
    if bearish and held_mins>20 and pct_chg>0.3:
        return True, f"Bearish pattern: {', '.join(bearish)} + profitable"

    return False, ""

def is_active_hour():
    return datetime.now().hour in ACTIVE_HOURS

# ─────────────────────────────────────────
# STATUS
# ─────────────────────────────────────────
def send_status(brain, btc_a=None, eth_a=None):
    try:
        acct=get_account()
        if not acct: tg("Alpaca connection failed"); return
        eq=float(acct.get("equity",0))
        leq=float(acct.get("last_equity",eq))
        dpl=eq-leq; dpct=dpl/leq*100 if leq else 0
        positions=get_positions()
        pos_lines=[]
        for p in positions:
            upl=float(p.get("unrealized_pl",0))
            mv=float(p.get("market_value",0))
            pos_lines.append(f"{p.get('symbol')} ${mv:,.2f} PnL: ${upl:+.2f}")
        open_trades=[t for t in brain["trades"] if t["status"]=="open"]
        lines=[
            "JARVIS Portfolio",
            f"Paper Equity: ${eq:,.2f}",
            f"Daily PnL: ${dpl:+.2f} ({dpct:+.2f}%)",
            f"Open trades: {len(open_trades)}",
            ""
        ]
        if pos_lines: lines.extend(["Positions:"]+pos_lines+[""])
        lines.append(brain_summary(brain))
        if btc_a:
            lines.extend(["",
                f"BTC: ${btc_a['price']:,.2f} | RSI: {btc_a['rsi']:.1f}",
                f"Regime: {btc_a['regime']} | 1h: {btc_a['chg1h']:+.2f}%"])
        if eth_a:
            lines.extend([
                f"ETH: ${eth_a['price']:,.2f} | RSI: {eth_a['rsi']:.1f}",
                f"Regime: {eth_a['regime']} | 1h: {eth_a['chg1h']:+.2f}%"])
        tg("\n".join(lines))
    except Exception as e:
        log.error(f"Status error: {e}"); tg(f"Status error: {e}")

# ─────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────
def main():
    log.info("="*60)
    log.info("JARVIS BRAIN EDITION - Full Learning Trading Bot")
    log.info(f"BTC + ETH | Scalping | Trailing stops | Active memory")
    log.info(f"Base trade: ${BASE_TRADE} | Max: ${MAX_TRADE} | Max positions: {MAX_POSITIONS}")
    log.info(f"Buy RSI: <{BUY_RSI_STRONG}(strong) <{BUY_RSI_NORMAL}(normal) <{BUY_RSI_SCALE}(scale)")
    log.info(f"Sell: RSI>{SELL_RSI} OR +{PROFIT_TARGET}% OR -{STOP_LOSS}% | Trail: {TRAIL_STEP}%")
    log.info("="*60)

    brain=load_brain()
    acct=get_account()
    if not acct:
        log.error("Alpaca connection failed")
        tg("JARVIS: Alpaca connection failed"); return

    eq=float(acct.get("equity",0))
    log.info(f"Connected - Paper equity: ${eq:,.2f}")
    log.info(f"Brain loaded: {brain.get('total_trades',0)} historical trades")

    tg(f"JARVIS BRAIN ONLINE\n\nPaper Trading: ${eq:,.2f}\nTrading: BTC + ETH\nScalping + Trailing stops\nActive memory: {brain.get('total_trades',0)} trades learned\nSize multiplier: {brain.get('size_multiplier',1.0):.1f}x\n\n{brain_summary(brain)}\n\nCommands: STATUS / BRAIN / STOP")

    tg_offset=None; last_poll=0; last_daily=0
    # Track open trades in memory
    open_trades={}  # order_id -> trade_record

    log.info("Running. Ctrl+C to stop.")

    while True:
        now=time.time()

        # TELEGRAM COMMANDS
        for u in tg_updates(tg_offset):
            tg_offset=u["update_id"]+1
            msg=u.get("message",{})
            chat=str(msg.get("chat",{}).get("id",""))
            text=msg.get("text","").strip().upper()
            if chat!=TELEGRAM_CHAT: continue
            if text=="STATUS":
                btc_t,eth_t=kraken_ticker()
                btc_a=analyze(btc_t,kraken_ohlc("XBTUSD"),"BTC") if btc_t else None
                eth_a=analyze(eth_t,kraken_ohlc("ETHUSD"),"ETH") if eth_t else None
                send_status(brain,btc_a,eth_a)
            elif text=="BRAIN":
                tg(brain_full_report(brain))
            elif text=="STOP":
                tg("JARVIS stopped."); return
            elif text=="HELP":
                tg("Commands:\nSTATUS - portfolio + market\nBRAIN - full learning report\nSTOP - stop bot")

        # MARKET ANALYSIS
        if now-last_poll>=POLL_INTERVAL:
            last_poll=now
            active=is_active_hour()
            log.info(f"Fetching data... (active hours: {active})")

            # Fetch BTC and ETH simultaneously
            btc_ticker,eth_ticker=kraken_ticker()
            btc_candles=kraken_ohlc("XBTUSD",60,100)
            eth_candles=kraken_ohlc("ETHUSD",60,100)

            btc_a=analyze(btc_ticker,btc_candles,"BTC") if btc_ticker and btc_candles else None
            eth_a=analyze(eth_ticker,eth_candles,"ETH") if eth_ticker and eth_candles else None

            if btc_a:
                log.info(f"BTC ${btc_a['price']:,.2f} | RSI:{btc_a['rsi']:.1f} | MACD:{btc_a['macd']:+.0f} | BB%B:{btc_a['bb_pct']:.1f}% | {btc_a['regime']}")
                if btc_a["squeeze"]: log.info("BTC: BOLLINGER SQUEEZE detected - big move incoming")
                if btc_a["patterns"]: log.info(f"BTC patterns: {', '.join(p[0] for p in btc_a['patterns'])}")

            if eth_a:
                log.info(f"ETH ${eth_a['price']:,.2f} | RSI:{eth_a['rsi']:.1f} | MACD:{eth_a['macd']:+.0f} | BB%B:{eth_a['bb_pct']:.1f}% | {eth_a['regime']}")
                if eth_a["patterns"]: log.info(f"ETH patterns: {', '.join(p[0] for p in eth_a['patterns'])}")

            # CHECK OPEN TRADES - trailing stops + sell signals
            for order_id, trade in list(open_trades.items()):
                coin=trade["coin"]
                a=btc_a if coin=="BTC" else eth_a
                if not a: continue

                sell,reason=should_sell(a,trade,brain)
                if sell:
                    sym="BTC%2FUSD" if coin=="BTC" else "ETH%2FUSD"
                    result=close_all_coin(sym)
                    if result:
                        closed=brain_record_close(brain,order_id,a["price"],reason)
                        pnl=closed["pnl"] if closed else 0
                        won=closed["won"] if closed else False
                        held=closed["hold_mins"] if closed else 0
                        entry=trade["open_price"]
                        pct=(a["price"]-entry)/entry*100
                        emoji="WIN" if won else "LOSS"
                        tg(f"{emoji} CLOSE {coin}\n\nEntry: ${entry:,.2f} -> Exit: ${a['price']:,.2f}\nHeld: {held}min | P&L: ${pnl:+.2f} ({pct:+.2f}%)\nReason: {reason}\n\n{brain_summary(brain)}")
                        del open_trades[order_id]
                    else:
                        tg(f"SELL FAILED {coin} - check Alpaca")

            # BUY SIGNALS - BTC
            if btc_a:
                buy,size,reason=should_buy(btc_a,brain,open_trades)
                if buy:
                    log.info(f"BTC BUY: {reason} | Size: ${size}")
                    result=place_buy("BTC/USD",size)
                    if result:
                        order_id=result.get("id","?")
                        trade=brain_record_open(brain,"BTC","BUY",order_id,btc_a["price"],btc_a,size)
                        open_trades[order_id]=trade
                        pat_str=", ".join(p[0] for p in btc_a["patterns"]) if btc_a["patterns"] else "None"
                        squeeze_str=" SQUEEZE DETECTED" if btc_a["squeeze"] else ""
                        tg(f"BUY BTC ${size}{squeeze_str}\n\nPrice: ${btc_a['price']:,.2f}\nRSI: {btc_a['rsi']:.1f} | BB%B: {btc_a['bb_pct']:.1f}%\nReason: {reason}\nPatterns: {pat_str}\nRegime: {btc_a['regime']}\nTrail stop: ${trade['trail_stop']:,.2f}\nOrder: {order_id}")
                    else:
                        tg(f"BUY FAILED BTC - check Alpaca")
                else:
                    if reason: log.info(f"BTC no buy: {reason}")
                    else: log.info(f"BTC no buy signal - RSI {btc_a['rsi']:.1f}")

            # BUY SIGNALS - ETH
            if eth_a:
                buy,size,reason=should_buy(eth_a,brain,open_trades)
                if buy:
                    log.info(f"ETH BUY: {reason} | Size: ${size}")
                    result=place_buy("ETH/USD",size)
                    if result:
                        order_id=result.get("id","?")
                        trade=brain_record_open(brain,"ETH","BUY",order_id,eth_a["price"],eth_a,size)
                        open_trades[order_id]=trade
                        pat_str=", ".join(p[0] for p in eth_a["patterns"]) if eth_a["patterns"] else "None"
                        tg(f"BUY ETH ${size}\n\nPrice: ${eth_a['price']:,.2f}\nRSI: {eth_a['rsi']:.1f} | BB%B: {eth_a['bb_pct']:.1f}%\nReason: {reason}\nPatterns: {pat_str}\nRegime: {eth_a['regime']}\nTrail stop: ${trade['trail_stop']:,.2f}\nOrder: {order_id}")
                    else:
                        tg(f"BUY FAILED ETH - check Alpaca")
                else:
                    if reason: log.info(f"ETH no buy: {reason}")
                    else: log.info(f"ETH no buy - RSI {eth_a['rsi']:.1f}")

        # DAILY SUMMARY
        if now-last_daily>=21600:
            last_daily=now
            btc_t,eth_t=kraken_ticker()
            btc_a=analyze(btc_t,kraken_ohlc("XBTUSD"),"BTC") if btc_t else None
            eth_a=analyze(eth_t,kraken_ohlc("ETHUSD"),"ETH") if eth_t else None
            send_status(brain,btc_a,eth_a)

        time.sleep(2)

if __name__=="__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped")
        tg("JARVIS stopped (Ctrl+C)")
    except Exception as e:
        log.error(f"Fatal: {e}")
        tg(f"JARVIS crashed: {e}")
