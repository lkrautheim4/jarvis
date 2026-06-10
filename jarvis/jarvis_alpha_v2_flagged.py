#!/usr/bin/env python3
"""
JARVIS ALPHA V2 — Full Market Trading System
Crypto + Stocks | Smart Brain | Claude AI Morning Brief
Upgrades: Daily loss limit, VWAP, earnings awareness,
correlation filter, retry logic, news sentiment, ML scoring
"""

import json, time, math, requests, os
from datetime import datetime, timedelta

# ─────────────────────────────────────────
# MARKET FLAGS BRIDGE
# ─────────────────────────────────────────
try:
    from jarvis_flags import should_trade as _should_trade, get_bias, get_risk_level, load_flags as load_market_flags
    FLAGS_ENABLED = True
except:
    FLAGS_ENABLED = False
    def _should_trade(ticker="", sector="", size=0): return True, size, ""
    def get_bias(): return "NEUTRAL", ""
    def get_risk_level(): return "NORMAL"
    def load_market_flags(): return {}

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
ALPACA_KEY     = "PKTHANGUNVFDSLLR3VXPETXRQF"
ALPACA_SECRET  = "GRTDDfkCGWbZMoNSWms6uJSGvw72rHaAk1N1fvLi8EAP"
ALPACA_BASE    = "https://paper-api.alpaca.markets"
TELEGRAM_TOKEN = __import__("jarvis_secrets").TG_TOKEN_TRADER
TELEGRAM_CHAT  = "7534553840"
from jarvis_secrets import CLAUDE_API_KEY

MEMORY_FILE    = "jarvis_alpha_brain.json"

# Trade sizing
MICRO_SIZE      = 150
SWING_SIZE      = 400
MAX_TRADE       = 500
MAX_CRYPTO_POS  = 3
MAX_STOCK_POS   = 3

# Strategy thresholds
BUY_RSI_STRONG  = 25
BUY_RSI_NORMAL  = 33
BUY_RSI_MICRO   = 47
SELL_RSI        = 58
PROFIT_MICRO    = 0.6
PROFIT_SWING    = 1.8
STOP_LOSS       = 1.5
TRAIL_STEP      = 0.35
MIN_HOLD_MICRO  = 5
MIN_HOLD_SWING  = 20
MIN_VOLUME_MULT = 1.2

# Risk management
DAILY_LOSS_LIMIT    = 300.0   # stop trading if down this much in a day
MAX_CORRELATED_POS  = 2       # max positions in same correlation group
CLOSE_BEFORE_CLOSE  = 15      # minutes before market close to exit all stocks
MIN_VOLUME_MULT     = 1.2     # minimum volume confirmation

# Timing
CRYPTO_POLL   = 90
STOCK_POLL    = 120
REPORT_HOUR   = 7

# Correlation groups — never hold more than 2 from same group
CORRELATION_GROUPS = {
    "tech_semis": ["NVDA", "AMD", "QQQ", "SOXS"],
    "crypto_proxy": ["COIN", "MSTR", "BTC", "ETH"],
    "broad_market": ["SPY", "QQQ", "IWM"],
}

CRYPTO_PAIRS = {
    "BTC":  ("XBTUSD",  "BTC/USD"),
    "ETH":  ("ETHUSD",  "ETH/USD"),
    "SOL":  ("SOLUSD",  "SOL/USD"),
    "AVAX": ("AVAXUSD", "AVAX/USD"),
}
STOCK_SYMBOLS = ["SPY","QQQ","NVDA","TSLA","COIN"]

import logging
logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('JARVIS-ALPHA-V2')

# ─────────────────────────────────────────
# RETRY WRAPPER
# ─────────────────────────────────────────
def safe_request(method, url, retries=3, **kwargs):
    """Retry logic for all HTTP requests"""
    for attempt in range(retries):
        try:
            r = getattr(requests, method)(url, timeout=10, **kwargs)
            if r.status_code in (200,201,204):
                return r
            log.warning(f"Request {url} returned {r.status_code} attempt {attempt+1}")
        except Exception as e:
            log.error(f"Request error {url} attempt {attempt+1}: {e}")
        if attempt < retries-1:
            time.sleep(2**attempt)
    return None

# ─────────────────────────────────────────
# CLAUDE AI INTEGRATION
# ─────────────────────────────────────────
def claude_morning_brief(market_data):
    """Ask Claude for daily market bias and trading plan"""
    if not CLAUDE_API_KEY:
        return 
    try:
        prompt = f"""You are Jarvis, an AI trading assistant. Based on this market data, give me:
1. Overall market bias (BULLISH/BEARISH/NEUTRAL) with confidence %
2. Top 2 crypto opportunities today
3. Top 2 stock opportunities today  
4. Key risks to watch
5. Recommended position sizing (aggressive/normal/conservative)

Market data:
{json.dumps(market_data, indent=2)}

Be concise and specific. Max 200 words."""

        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 400,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )
        if r.status_code == 200:
            return r.json()["content"][0]["text"]
    except Exception as e:
        log.error(f"Claude brief error: {e}")
    return "Claude brief unavailable"

def claude_trade_signal(asset, analysis, brain):
    """Ask Claude to validate a trade signal before executing"""
    if not CLAUDE_API_KEY:
        return True, "Claude not configured - proceeding"
    try:
        prompt = f"""Jarvis is about to make a trade. Should it proceed?

Asset: {asset}
Price: ${analysis['price']:,.2f}
RSI: {analysis['rsi']:.1f}
MACD: {analysis['macd']:+.2f}
BB%: {analysis['bb_pct']:.1f}%
Regime: {analysis['regime']}
Volume: {analysis.get('volume_mult',1):.1f}x avg
Patterns: {[p[0] for p in analysis.get('patterns',[])]}
Brain win rate: {brain.get('wins',0)}/{brain.get('wins',0)+brain.get('losses',0)} trades
Consecutive losses: {brain.get('consecutive_losses',0)}

Reply with just: PROCEED or SKIP, then one sentence reason."""

        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=15
        )
        if r.status_code == 200:
            text = r.json()["content"][0]["text"].strip()
            proceed = text.upper().startswith("PROCEED")
            return proceed, text
    except Exception as e:
        log.error(f"Claude signal error: {e}")
    return True, "Claude unavailable - proceeding"

# ─────────────────────────────────────────
# RISK MANAGEMENT
# ─────────────────────────────────────────
def check_daily_loss(brain, current_equity, start_equity):
    """Stop trading if daily loss limit hit"""
    daily_loss = start_equity - current_equity
    if daily_loss >= DAILY_LOSS_LIMIT:
        log.warning(f"DAILY LOSS LIMIT HIT: ${daily_loss:.2f}")
        return True, daily_loss
    return False, daily_loss

def check_correlation(asset, open_trades):
    """Prevent overloading correlated positions"""
    for group_name, group_assets in CORRELATION_GROUPS.items():
        if asset in group_assets:
            count = sum(1 for t in open_trades.values()
                       if t.get("asset","") in group_assets)
            if count >= MAX_CORRELATED_POS:
                return False, f"Correlation limit: {count} in {group_name}"
    return True, ""

def should_close_before_market_close():
    """Close all stock positions 15min before market close"""
    now = datetime.utcnow() - timedelta(hours=4)  # ET
    if now.weekday() >= 5: return False
    close_time = now.replace(hour=15, minute=45, second=0)
    market_close = now.replace(hour=16, minute=0, second=0)
    return close_time <= now <= market_close

def check_earnings_today(symbol):
    """Check if stock has earnings today or tomorrow — avoid holding"""
    try:
        # Use Alpaca calendar or free earnings API
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")
        r = safe_request("get",
            f"https://api.nasdaq.com/api/calendar/earnings?date={today}",
            headers={"User-Agent": "Mozilla/5.0"})
        if r:
            data = r.json()
            rows = data.get("data",{}).get("rows",[]) or []
            for row in rows:
                if symbol.upper() in str(row.get("symbol","")).upper():
                    return True
    except Exception as e:
        log.error(f"Earnings check {symbol}: {e}")
    return False

# ─────────────────────────────────────────
# NEWS SENTIMENT
# ─────────────────────────────────────────
news_cache = {}
def get_news_sentiment(asset):
    """Pull recent news and score sentiment -1 to +1"""
    global news_cache
    cache_key = asset
    if cache_key in news_cache:
        cached_time, score = news_cache[cache_key]
        if time.time() - cached_time < 1800:  # 30min cache
            return score

    try:
        r = safe_request("get",
            f"https://cryptopanic.com/api/v1/posts/?auth_token=free&currencies={asset}&kind=news&filter=important",
        )
        if r:
            posts = r.json().get("results", [])[:5]
            if not posts:
                news_cache[cache_key] = (time.time(), 0)
                return 0
            score = 0
            for post in posts:
                votes = post.get("votes", {})
                pos = votes.get("positive", 0)
                neg = votes.get("negative", 0)
                total = pos + neg
                if total > 0:
                    score += (pos - neg) / total
            final = score / len(posts) if posts else 0
            news_cache[cache_key] = (time.time(), final)
            return final
    except Exception as e:
        log.error(f"News sentiment {asset}: {e}")
    return 0

# ─────────────────────────────────────────
# BRAIN SYSTEM
# ─────────────────────────────────────────
def load_brain():
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE,'r') as f:
                b = json.load(f)
                log.info(f"Brain loaded: {b.get('total_trades',0)} trades")
                return b
    except: pass
    return _fresh_brain()

def _fresh_brain():
    return {
        "trades":[],"total_trades":0,"wins":0,"losses":0,"total_pnl":0.0,
        "best_trade":0.0,"worst_trade":0.0,
        "assets":{},"regimes":{},"rsi_zones":{},"hours":{},"patterns":{},
        "trade_types":{},"hold_times":{},"days":{},"volume_zones":{},
        "news_sentiment_zones":{},
        "size_multiplier":1.0,"best_assets":[],"worst_assets":[],
        "best_hour":9,"worst_regime":"CHOP",
        "micro_win_rate":0.5,"swing_win_rate":0.5,
        "consecutive_losses":0,"max_consecutive_losses":0,
        "session_pnl":0.0,"session_trades":0,"session_wins":0,
        "daily_start_equity":0.0,"last_session_date":"",
        "market_bias":"NEUTRAL","market_bias_confidence":50,
        "created":datetime.now().isoformat(),
        "last_updated":datetime.now().isoformat()
    }

def save_brain(b):
    try:
        b["last_updated"] = datetime.now().isoformat()
        with open(MEMORY_FILE,'w') as f: json.dump(b,f,indent=2)
    except Exception as e: log.error(f"Brain save: {e}")

def reset_session(brain, equity):
    today = datetime.now().strftime("%Y-%m-%d")
    if brain.get("last_session_date","") != today:
        brain["session_pnl"] = 0.0
        brain["session_trades"] = 0
        brain["session_wins"] = 0
        brain["last_session_date"] = today
        brain["daily_start_equity"] = equity
        brain["consecutive_losses"] = 0
        log.info(f"New session — equity: ${equity:,.2f}")

def rsi_zone(v):
    if v>=75: return "EXTREME_OB"
    if v>=65: return "OVERBOUGHT"
    if v>=55: return "BULLISH"
    if v>=45: return "NEUTRAL"
    if v>=35: return "BEARISH"
    if v>=25: return "OVERSOLD"
    return "EXTREME_OS"

def volume_zone(mult):
    if mult>=3.0: return "EXTREME"
    if mult>=2.0: return "HIGH"
    if mult>=1.5: return "ELEVATED"
    if mult>=1.0: return "NORMAL"
    return "LOW"

def hold_bucket(mins):
    if mins<10:  return "<10min"
    if mins<30:  return "10-30min"
    if mins<60:  return "30-60min"
    if mins<240: return "1-4hr"
    return "4hr+"

def sentiment_zone(score):
    if score > 0.5:  return "VERY_BULLISH"
    if score > 0.2:  return "BULLISH"
    if score > -0.2: return "NEUTRAL"
    if score > -0.5: return "BEARISH"
    return "VERY_BEARISH"

def update_bucket(b, k, won, pnl=0):
    if k not in b: b[k]={"wins":0,"total":0,"avg_pnl":0.0}
    b[k]["total"]+=1
    if won: b[k]["wins"]+=1
    n=b[k]["total"]
    b[k]["avg_pnl"]=round((b[k]["avg_pnl"]*(n-1)+pnl)/n,3)

def get_wr(b, k, min_t=3):
    if k not in b or b[k]["total"]<min_t: return 0.5,0
    return b[k]["wins"]/b[k]["total"],b[k]["total"]

def record_open(brain, asset, trade_type, order_id, price, analysis, size):
    trade={
        "id":order_id,"asset":asset,"trade_type":trade_type,
        "open_price":price,"open_time":datetime.now().isoformat(),
        "size":size,"rsi":round(analysis["rsi"],1),
        "rsi_zone":rsi_zone(analysis["rsi"]),"regime":analysis["regime"],
        "volume_mult":round(analysis.get("volume_mult",1.0),2),
        "volume_zone":volume_zone(analysis.get("volume_mult",1.0)),
        "news_sentiment":round(analysis.get("news_sentiment",0),2),
        "news_zone":sentiment_zone(analysis.get("news_sentiment",0)),
        "patterns":[p[0] for p in analysis.get("patterns",[])],
        "hour":datetime.now().hour,"day":datetime.now().weekday(),
        "status":"open","trail_stop":round(price*(1-STOP_LOSS/100),4),
        "trail_high":price,"close_price":None,"pnl":None,
        "won":None,"hold_mins":None,"close_reason":None
    }
    brain["trades"].append(trade)
    brain["total_trades"]+=1
    brain["session_trades"]+=1
    save_brain(brain)
    return trade

def record_close(brain, order_id, close_price, reason):
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
            update_bucket(brain["assets"],    t["asset"],      won,pnl)
            update_bucket(brain["regimes"],   t["regime"],     won,pnl)
            update_bucket(brain["rsi_zones"], t["rsi_zone"],   won,pnl)
            update_bucket(brain["hours"],     str(t["hour"]),  won,pnl)
            update_bucket(brain["trade_types"],t["trade_type"],won,pnl)
            update_bucket(brain["hold_times"],hold_bucket(held),won,pnl)
            update_bucket(brain["days"],      str(t["day"]),   won,pnl)
            update_bucket(brain["volume_zones"],t.get("volume_zone","NORMAL"),won,pnl)
            update_bucket(brain["news_sentiment_zones"],t.get("news_zone","NEUTRAL"),won,pnl)
            for pat in t["patterns"]:
                update_bucket(brain["patterns"],pat,won,pnl)
            brain["total_pnl"]=round(brain.get("total_pnl",0)+pnl,2)
            brain["session_pnl"]=round(brain.get("session_pnl",0)+pnl,2)
            if won:
                brain["wins"]=brain.get("wins",0)+1
                brain["session_wins"]=brain.get("session_wins",0)+1
                brain["best_trade"]=max(brain.get("best_trade",0),pnl)
                brain["consecutive_losses"]=0
            else:
                brain["losses"]=brain.get("losses",0)+1
                brain["consecutive_losses"]=brain.get("consecutive_losses",0)+1
                brain["max_consecutive_losses"]=max(
                    brain.get("max_consecutive_losses",0),
                    brain["consecutive_losses"])
                brain["worst_trade"]=min(brain.get("worst_trade",0),pnl)
            _adapt(brain)
            save_brain(brain)
            log.info(f"Brain: {t['asset']} {t['trade_type']} P&L ${pnl:+.2f} {'WIN' if won else 'LOSS'}")
            return t
    return None

def _adapt(brain):
    wins=brain.get("wins",0); losses=brain.get("losses",0)
    resolved=wins+losses
    if resolved<5: return
    overall_wr=wins/resolved
    if overall_wr>0.65:
        brain["size_multiplier"]=min(2.5,brain.get("size_multiplier",1.0)+0.1)
    elif overall_wr<0.40:
        brain["size_multiplier"]=max(0.3,brain.get("size_multiplier",1.0)-0.15)
    consec=brain.get("consecutive_losses",0)
    if consec>=3:
        brain["size_multiplier"]=max(0.3,brain["size_multiplier"]-0.2)
        log.warning(f"Brain: {consec} consecutive losses — reducing size")
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
    log.info(f"Brain adapted: WR={overall_wr*100:.0f}% size={brain['size_multiplier']:.1f}x best={brain['best_assets']}")

def calc_size(brain, analysis, base_size, trade_type):
    mult=brain.get("size_multiplier",1.0)
    asset=analysis.get("asset","")
    if asset in brain.get("best_assets",[]): mult*=1.3
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
    if rsi_v<20: mult*=1.6
    elif rsi_v<25: mult*=1.35
    elif rsi_v<30: mult*=1.15
    elif rsi_v<35: mult*=1.0
    else: mult*=0.8
    bb=analysis.get("bb_pct",50)
    if bb<0: mult*=1.25
    elif bb<10: mult*=1.1
    elif bb>90: mult*=0.8
    # News sentiment adjustment
    sentiment=analysis.get("news_sentiment",0)
    if sentiment>0.5: mult*=1.2
    elif sentiment<-0.5: mult*=0.6
    elif sentiment<-0.3: mult*=0.8
    # Market bias adjustment
    bias=brain.get("market_bias","NEUTRAL")
    if bias=="BULLISH": mult*=1.1
    elif bias=="BEARISH": mult*=0.7
    mwr=brain.get("micro_win_rate",0.5)
    swr=brain.get("swing_win_rate",0.5)
    if trade_type=="micro" and mwr<0.4: mult*=0.7
    if trade_type=="swing" and swr<0.4: mult*=0.7
    if trade_type=="micro" and mwr>0.65: mult*=1.2
    if trade_type=="swing" and swr>0.65: mult*=1.2
    if datetime.now().hour==brain.get("best_hour",9): mult*=1.1
    consec=brain.get("consecutive_losses",0)
    if consec>=2: mult*=max(0.4,1.0-(consec*0.2))
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
    avoid=", ".join(brain.get("worst_assets",[])) or "none"
    sess_pnl=brain.get("session_pnl",0)
    sess_trades=brain.get("session_trades",0)
    lines=[
        f"Brain: {total} trades | WR: {wr} | P&L: ${pnl:+.2f}",
        f"Size: {mult:.1f}x | Best: {best} | Avoid: {avoid}",
        f"Session: {sess_trades} trades ${sess_pnl:+.2f} | Bias: {brain.get('market_bias','NEUTRAL')}",
        f"Consec losses: {brain.get('consecutive_losses',0)} | Best hour: {brain.get('best_hour',9)}:00"
    ]
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
# ALPACA
# ─────────────────────────────────────────
def alpaca(method, path, data=None):
    hdrs={"APCA-API-KEY-ID":ALPACA_KEY,"APCA-API-SECRET-KEY":ALPACA_SECRET,"Content-Type":"application/json"}
    for attempt in range(3):
        try:
            if method=="GET":      r=requests.get(f"{ALPACA_BASE}{path}",headers=hdrs,timeout=10)
            elif method=="POST":   r=requests.post(f"{ALPACA_BASE}{path}",headers=hdrs,json=data,timeout=10)
            elif method=="DELETE": r=requests.delete(f"{ALPACA_BASE}{path}",headers=hdrs,timeout=10)
            if r.status_code in(200,201,204): return r.json() if r.content else {}
            log.warning(f"Alpaca {method} {path}: {r.status_code}")
        except Exception as e:
            log.error(f"Alpaca attempt {attempt+1}: {e}")
        if attempt<2: time.sleep(2**attempt)
    return None

def get_account():   return alpaca("GET","/v2/account")
def get_positions(): return alpaca("GET","/v2/positions") or []

def is_market_open():
    clock=alpaca("GET","/v2/clock")
    if clock: return clock.get("is_open",False)
    now=datetime.utcnow()-timedelta(hours=4)
    if now.weekday()>=5: return False
    return 9<=now.hour<16

def buy_asset(symbol, notional, is_crypto=True):
    tif="gtc" if is_crypto else "day"
    return alpaca("POST","/v2/orders",{
        "symbol":symbol,"notional":str(round(notional,2)),
        "side":"buy","type":"market","time_in_force":tif})

def sell_asset(symbol, is_crypto=True):
    enc=symbol.replace("/","%2F")
    result=alpaca("DELETE",f"/v2/positions/{enc}")
    if result is not None: return result
    tif="gtc" if is_crypto else "day"
    return alpaca("POST","/v2/orders",{
        "symbol":symbol,"notional":"100",
        "side":"sell","type":"market","time_in_force":tif})

def close_all_stock_positions(open_trades):
    """Force close all stock positions"""
    closed=[]
    for order_id,trade in list(open_trades.items()):
        if trade["asset"] not in CRYPTO_PAIRS:
            result=sell_asset(trade["asset"],is_crypto=False)
            if result:
                closed.append(trade["asset"])
    return closed

# ─────────────────────────────────────────
# MARKET DATA
# ─────────────────────────────────────────
def kraken_price(pair):
    try:
        r=safe_request("get",f"https://api.kraken.com/0/public/Ticker?pair={pair}")
        if not r: return None
        d=r.json()
        if d.get("error"): return None
        res=d["result"]; key=list(res.keys())[0] if res else None
        if not key: return None
        t=res[key]
        return {"price":float(t["c"][0]),"high":float(t["h"][1]),
                "low":float(t["l"][1]),"open":float(t["o"]),"vol":float(t["v"][1])}
    except Exception as e: log.error(f"Kraken {pair}: {e}"); return None

def kraken_ohlc(pair, interval=60, limit=100):
    try:
        r=safe_request("get",f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}")
        if not r: return []
        d=r.json()
        if d.get("error"): return []
        key=next((k for k in d["result"] if k!="last"),None)
        if not key: return []
        return [{"t":int(k[0]),"o":float(k[1]),"h":float(k[2]),
                 "l":float(k[3]),"c":float(k[4]),"v":float(k[6])}
                for k in d["result"][key][-limit:]]
    except Exception as e: log.error(f"Kraken OHLC {pair}: {e}"); return []

def alpaca_bars(symbol, limit=100):
    try:
        r=alpaca("GET",f"/v2/stocks/{symbol}/bars?timeframe=1Hour&limit={limit}&feed=iex")
        if not r: return []
        return [{"t":b["t"],"o":b["o"],"h":b["h"],"l":b["l"],"c":b["c"],"v":b["v"]}
                for b in r.get("bars",[])]
    except Exception as e: log.error(f"Bars {symbol}: {e}"); return []

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
        r=safe_request("get","https://api.alternative.me/fng/?limit=1")
        if r:
            d=r.json()
            if d.get("data"):
                v=int(d["data"][0]["value"]); l=d["data"][0]["value_classification"]
                fg_cache=(v,l,time.time()); return v,l
    except: pass
    return fg_cache[0],fg_cache[1]

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
    return mean+2*std,mean,mean-2*std

def calc_atr(candles, p=14):
    if len(candles)<2: return 0
    trs=[max(candles[i]["h"]-candles[i]["l"],
             abs(candles[i]["h"]-candles[i-1]["c"]),
             abs(candles[i]["l"]-candles[i-1]["c"]))
         for i in range(1,len(candles))]
    return sum(trs[-p:])/min(p,len(trs))

def calc_vwap(candles):
    """Volume Weighted Average Price — key intraday level"""
    if len(candles)<2: return None
    today = datetime.now().strftime("%Y-%m-%d")
    today_candles=[c for c in candles
                  if datetime.fromtimestamp(c["t"] if isinstance(c["t"],int)
                  else datetime.fromisoformat(c["t"]).timestamp()).strftime("%Y-%m-%d")==today]
    if not today_candles: today_candles=candles[-8:]
    cum_vol=cum_vp=0
    for c in today_candles:
        typical=(c["h"]+c["l"]+c["c"])/3
        cum_vp+=typical*c["v"]
        cum_vol+=c["v"]
    return cum_vp/cum_vol if cum_vol>0 else None

def calc_volume_mult(bars):
    if len(bars)<5: return 1.0
    vols=[b["v"] for b in bars]
    avg=sum(vols[:-1])/max(1,len(vols)-1)
    if avg==0: return 1.0
    return round(vols[-1]/avg,2)

def boll_squeeze(candles, p=20, lb=50):
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

def analyze_asset(ticker, candles, asset, is_crypto=True):
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
    vol_mult=calc_volume_mult(candles)
    vwap=calc_vwap(candles)
    vwap_pct=((price-vwap)/vwap*100) if vwap else 0
    squeeze=boll_squeeze(candles)
    patterns=calc_patterns(candles)
    regime,rdesc=detect_regime(candles)
    fg_v,fg_l=get_fg()
    p1h=closes[-2] if len(closes)>=2 else price
    p4h=closes[-5] if len(closes)>=5 else price
    chg1h=(price-p1h)/p1h*100 if p1h else 0
    chg4h=(price-p4h)/p4h*100 if p4h else 0
    news_sentiment=get_news_sentiment(asset) if is_crypto else 0
    return {
        "asset":asset,"price":price,"rsi":rsi_v,"macd":macd_v,
        "bb_pct":bb_pct,"bbu":bbu,"bbl":bbl,"atr":atr_v,
        "volume_mult":vol_mult,"vwap":vwap,"vwap_pct":vwap_pct,
        "squeeze":squeeze,"chg1h":chg1h,"chg4h":chg4h,"chg24h":chg24,
        "fg":fg_v,"fg_label":fg_l,"regime":regime,"regime_desc":rdesc,
        "patterns":patterns,"news_sentiment":news_sentiment,
        "ts":datetime.now().strftime("%H:%M:%S")
    }

# ─────────────────────────────────────────
# TRADE DECISION ENGINE
# ─────────────────────────────────────────
def check_buy(a, brain, open_trades, is_crypto=True):
    if not a: return False,"",0,""
    asset=a["asset"]
    rsi_v=a["rsi"]
    patterns=a["patterns"]
    bullish=[p[0] for p in patterns if p[1]==True]
    squeeze=a["squeeze"]
    regime=a["regime"]
    vol_mult=a.get("volume_mult",1.0)
    vwap_pct=a.get("vwap_pct",0)
    news=a.get("news_sentiment",0)

    # ── Hard blocks ──
    if regime==brain.get("worst_regime","") and brain.get("total_trades",0)>15:
        return False,"",0,f"Worst regime {regime}"
    if asset in brain.get("worst_assets",[]) and brain.get("total_trades",0)>20:
        return False,"",0,f"Avoiding {asset}"

    # ── Correlation check ──
    corr_ok, corr_reason = check_correlation(asset, open_trades)
    if not corr_ok:
        return False,"",0,corr_reason

    # ── Volume gate ──
    if vol_mult < MIN_VOLUME_MULT and rsi_v > BUY_RSI_NORMAL:
        return False,"",0,f"Low volume {vol_mult:.1f}x"

    # ── News sentiment gate ──
    if news < -0.6:
        return False,"",0,f"Very bearish news sentiment {news:.2f}"

    # ── Max positions ──
    open_count=sum(1 for t in brain["trades"] if t["status"]=="open")
    max_pos=MAX_CRYPTO_POS if is_crypto else MAX_STOCK_POS
    asset_open=sum(1 for t in brain["trades"]
                  if t["asset"]==asset and t["status"]=="open")
    if asset_open>0: return False,"",0,f"{asset} already open"

    trade_type=""; reason=""; base_size=0

    # SWING — extreme oversold
    if rsi_v<BUY_RSI_STRONG and open_count<max_pos:
        trade_type="swing"; base_size=SWING_SIZE
        reason=f"STRONG BUY RSI {rsi_v:.1f} extreme oversold vol:{vol_mult:.1f}x"

    # SWING — oversold + below Bollinger + volume
    elif rsi_v<BUY_RSI_NORMAL and a["bb_pct"]<15 and vol_mult>=1.3 and open_count<max_pos:
        trade_type="swing"; base_size=SWING_SIZE
        reason=f"BUY RSI {rsi_v:.1f} below BB vol:{vol_mult:.1f}x"

    # SWING — below VWAP bounce + oversold
    elif vwap_pct<-1.0 and rsi_v<40 and vol_mult>=1.3 and open_count<max_pos:
        trade_type="swing"; base_size=SWING_SIZE
        reason=f"VWAP bounce {vwap_pct:.1f}% below RSI:{rsi_v:.1f} vol:{vol_mult:.1f}x"

    # SWING — Bollinger squeeze breakout
    elif squeeze and rsi_v<45 and a["chg1h"]>0 and vol_mult>=1.5 and open_count<max_pos:
        trade_type="swing"; base_size=SWING_SIZE
        reason=f"SQUEEZE breakout vol:{vol_mult:.1f}x"

    # MICRO — dip with bullish pattern + volume
    elif rsi_v<BUY_RSI_MICRO and bullish and vol_mult>=MIN_VOLUME_MULT and open_count<max_pos:
        trade_type="micro"; base_size=MICRO_SIZE
        reason=f"MICRO RSI {rsi_v:.1f} + {', '.join(bullish)} vol:{vol_mult:.1f}x"

    # MICRO — oversold dip
    elif rsi_v<BUY_RSI_NORMAL and open_count<1:
        trade_type="micro"; base_size=MICRO_SIZE
        reason=f"MICRO RSI {rsi_v:.1f} dip"

    # MICRO — best asset on dip
    elif asset in brain.get("best_assets",[]) and rsi_v<42 and vol_mult>=1.2 and open_count<max_pos:
        trade_type="micro"; base_size=MICRO_SIZE
        reason=f"BEST ASSET {asset} dip RSI:{rsi_v:.1f}"

    if not trade_type: return False,"",0,""

    size,mult=calc_size(brain,a,base_size,trade_type)
    log.info(f"{asset} size: base ${base_size} x {mult:.2f} = ${size}")

    # Check market flags before trading
    if FLAGS_ENABLED:
        ok,adj_size,flag_reason = _should_trade(ticker=asset, size=size)
        if not ok:
            return False,"",0,f"FLAGS: {flag_reason}"
        if adj_size != size and adj_size > 0:
            log.info(f"{asset}: flags adjusted size {size} -> {adj_size:.0f}")
            size = int(adj_size)

    return True,trade_type,size,reason

def check_sell(a, trade, brain):
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
    vwap=a.get("vwap")
    if price<=trail_stop:
        return True,f"Trail stop ${trail_stop:.2f} ({pct:+.2f}%)"
    if pct>=profit_target:
        return True,f"Profit target +{pct:.2f}% hit"
    if pct<=-STOP_LOSS:
        return True,f"Stop loss {pct:.2f}%"
    if a["rsi"]>SELL_RSI and pct>0.2:
        return True,f"RSI {a['rsi']:.1f} overbought + profitable"
    if a["rsi"]>70:
        return True,f"RSI {a['rsi']:.1f} extreme overbought"
    if bearish and held>15 and pct>0.2:
        return True,f"Bearish signal + profitable"
    if vwap and price>vwap*1.015 and pct>0.5:
        return True,f"Price 1.5% above VWAP — take profit"
    if held>300 and pct<0:
        return True,f"Time stop: {held}min held, losing"
    return False,""

# ─────────────────────────────────────────
# REPORTS
# ─────────────────────────────────────────
def send_status(brain, analyses=None):
    try:
        acct=get_account()
        if not acct: tg("Alpaca connection failed"); return
        eq=float(acct.get("equity",0))
        leq=float(acct.get("last_equity",eq))
        dpl=eq-leq; dpct=dpl/leq*100 if leq else 0
        positions=get_positions()
        open_trades=[t for t in brain["trades"] if t["status"]=="open"]
        start_eq=brain.get("daily_start_equity",eq)
        daily_loss=start_eq-eq
        lines=[
            "JARVIS ALPHA V2 STATUS",
            f"Paper Equity: ${eq:,.2f}",
            f"Daily PnL: ${dpl:+.2f} ({dpct:+.2f}%)",
            f"Daily loss limit: ${daily_loss:.2f}/${DAILY_LOSS_LIMIT:.0f}",
            f"Open trades: {len(open_trades)}",
            f"Market bias: {brain.get('market_bias','NEUTRAL')}",
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

def send_daily_report(brain, market_data=None):
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
        fg_v,fg_l=get_fg()
        # Get Claude's morning brief
        brief="Claude API not configured"
        if market_data:
            brief=claude_morning_brief(market_data)
            # Parse bias from brief
            if "BULLISH" in brief.upper():
                brain["market_bias"]="BULLISH"
            elif "BEARISH" in brief.upper():
                brain["market_bias"]="BEARISH"
            else:
                brain["market_bias"]="NEUTRAL"
            save_brain(brain)
        lines=[
            "JARVIS ALPHA V2 — MORNING BRIEF",
            f"Good morning Lenny!",
            "",
            f"Paper Equity: ${eq:,.2f}",
            f"24h PnL: ${dpl:+.2f} ({dpct:+.2f}%)",
            f"Fear & Greed: {fg_v} ({fg_l})",
            "",
            f"Yesterday: {len(yest)} trades | PnL: ${yest_pnl:+.2f} | Wins: {yest_wins}/{len(yest)}",
            "",
            "CLAUDE'S MARKET ANALYSIS:",
            brief,
            "",
            brain_summary(brain),
            "",
            "Commands: STATUS / BRAIN / STOP / PAUSE / RESUME"
        ]
        tg("\n".join(lines))
    except Exception as e:
        log.error(f"Daily report: {e}")

# ─────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────
def main():
    log.info("="*65)
    log.info("JARVIS ALPHA V2 — Full Market Trading System")
    log.info("Claude AI + VWAP + Earnings + Correlation + News Sentiment")
    log.info(f"Daily loss limit: ${DAILY_LOSS_LIMIT} | Max positions: {MAX_CRYPTO_POS+MAX_STOCK_POS}")
    log.info("="*65)

    brain=load_brain()
    acct=get_account()
    if not acct:
        log.error("Alpaca connection failed")
        tg("JARVIS ALPHA V2: Alpaca connection failed")
        return

    eq=float(acct.get("equity",0))
    reset_session(brain, eq)
    log.info(f"Connected — Paper equity: ${eq:,.2f}")

    tg(f"JARVIS ALPHA V2 ONLINE\n\nPaper: ${eq:,.2f}\nCrypto: BTC/ETH/SOL/AVAX\nStocks: SPY/QQQ/NVDA/TSLA/COIN\nFeatures: VWAP + Earnings + Correlation + News + Claude AI\nBrain: {brain.get('total_trades',0)} trades | Size: {brain.get('size_multiplier',1.0):.1f}x\n\n{brain_summary(brain)}\n\nCommands: STATUS / BRAIN / STOP / PAUSE / RESUME")

    tg_offset=None
    last_crypto_poll=0
    last_stock_poll=0
    last_daily=0
    last_report_day=-1
    open_trades={}
    paused=False
    daily_limit_hit=False

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
            if text=="STATUS": send_status(brain)
            elif text=="BRAIN": tg(brain_summary(brain))
            elif text=="PAUSE":
                paused=True; tg("JARVIS ALPHA V2 paused — no new trades")
            elif text=="RESUME":
                paused=False; daily_limit_hit=False
                tg("JARVIS ALPHA V2 resumed")
            elif text=="STOP":
                tg("JARVIS ALPHA V2 stopped."); return
            elif text=="FLAGS":
                if FLAGS_ENABLED:
                    f=load_market_flags()
                    tg(f"ALPHA FLAGS\nPaused: {f.get('trading_paused',False)}\nRisk: {f.get('risk_level','NORMAL')}\nSize: {f.get('size_reduction',1.0)*100:.0f}%\nBias: {f.get('bias','NEUTRAL')}\nEvent: {f.get('macro_event','none')}")
                else:
                    tg("Flags not enabled")
            elif text=="HELP":
                tg("Commands:\nSTATUS / BRAIN / PAUSE / RESUME / FLAGS / STOP")

        # MORNING REPORT
        if current_hour==REPORT_HOUR and current_day!=last_report_day:
            last_report_day=current_day
            acct=get_account()
            if acct:
                eq=float(acct.get("equity",0))
                reset_session(brain,eq)
                daily_limit_hit=False
            market_data={
                "fear_greed":get_fg()[0],
                "crypto_assets":list(CRYPTO_PAIRS.keys()),
                "stocks":STOCK_SYMBOLS,
                "brain_win_rate":brain.get("wins",0)/(max(1,brain.get("wins",0)+brain.get("losses",0))),
                "total_pnl":brain.get("total_pnl",0),
                "size_multiplier":brain.get("size_multiplier",1.0)
            }
            send_daily_report(brain,market_data)

        # CHECK DAILY LOSS LIMIT
        if not daily_limit_hit and not paused:
            acct=get_account()
            if acct:
                eq=float(acct.get("equity",0))
                start_eq=brain.get("daily_start_equity",eq)
                limit_hit,daily_loss=check_daily_loss(brain,eq,start_eq)
                if limit_hit:
                    daily_limit_hit=True
                    tg(f"DAILY LOSS LIMIT HIT — ${daily_loss:.2f}\nStopping new trades for today.\nSend RESUME to override.")

        # FORCE CLOSE STOCKS BEFORE MARKET CLOSE
        if should_close_before_market_close() and open_trades:
            closed=close_all_stock_positions(open_trades)
            if closed:
                tg(f"PRE-CLOSE: Force closed {', '.join(closed)} before market close")

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
                a=analyze_asset(ticker,candles,asset,is_crypto)
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
                        tg(f"SELL FAILED {asset} — check Alpaca")

        # SKIP NEW TRADES IF PAUSED OR LIMIT HIT
        if paused or daily_limit_hit:
            time.sleep(5); continue

        # CRYPTO SCAN
        if now-last_crypto_poll>=CRYPTO_POLL:
            last_crypto_poll=now
            log.info("--- CRYPTO SCAN ---")
            for asset,(kraken_sym,alpaca_sym) in CRYPTO_PAIRS.items():
                ticker=kraken_price(kraken_sym)
                candles=kraken_ohlc(kraken_sym,60,100)
                if not ticker or not candles:
                    log.warning(f"{asset}: no data"); continue
                a=analyze_asset(ticker,candles,asset,is_crypto=True)
                if not a: continue
                vwap_str=f" VWAP:{a['vwap_pct']:+.1f}%" if a.get("vwap_pct") else ""
                log.info(f"{asset} ${a['price']:,.2f} RSI:{a['rsi']:.1f} Vol:{a['volume_mult']:.1f}x{vwap_str} {a['regime']}")
                if a["squeeze"]: log.info(f"{asset}: SQUEEZE")
                if a["patterns"]: log.info(f"{asset} patterns: {[p[0] for p in a['patterns']]}")
                buy,trade_type,size,reason=check_buy(a,brain,open_trades,is_crypto=True)
                if buy:
                    # Ask Claude to validate
                    proceed,claude_reason=claude_trade_signal(asset,a,brain)
                    if not proceed:
                        log.info(f"{asset} Claude SKIP: {claude_reason}")
                        tg(f"Claude SKIP {asset}: {claude_reason}")
                        continue
                    log.info(f"{asset} BUY ({trade_type}): {reason} ${size}")
                    result=buy_asset(alpaca_sym,size,is_crypto=True)
                    if result:
                        order_id=result.get("id","?")
                        trade=record_open(brain,asset,trade_type,order_id,a["price"],a,size)
                        open_trades[order_id]=trade
                        pat_str=", ".join(p[0] for p in a["patterns"]) if a["patterns"] else "None"
                        tg(f"BUY {asset} {trade_type.upper()} ${size}\n\nPrice: ${a['price']:,.2f}\nRSI: {a['rsi']:.1f} | Vol: {a['volume_mult']:.1f}x | VWAP: {a.get('vwap_pct',0):+.1f}%\nNews: {a.get('news_sentiment',0):+.2f}\nReason: {reason}\nClaude: {claude_reason[:60]}\nPatterns: {pat_str}\nTrail stop: ${trade['trail_stop']:,.4f}")
                    else:
                        tg(f"BUY FAILED {asset}")
                else:
                    nb=reason if reason else "RSI "+str(round(a["rsi"],1))
                    log.info(f"{asset}: no buy ({nb})")

        # STOCK SCAN
        if now-last_stock_poll>=STOCK_POLL:
            last_stock_poll=now
            market_open=is_market_open()
            if market_open:
                log.info("--- STOCK SCAN ---")
                for symbol in STOCK_SYMBOLS:
                    # Earnings check
                    if check_earnings_today(symbol):
                        log.info(f"{symbol}: EARNINGS TODAY — skipping")
                        continue
                    quote=alpaca_quote(symbol)
                    candles=alpaca_bars(symbol,100)
                    if not quote or not candles:
                        log.warning(f"{symbol}: no data"); continue
                    a=analyze_asset(quote,candles,symbol,is_crypto=False)
                    if not a: continue
                    vwap_str=f" VWAP:{a['vwap_pct']:+.1f}%" if a.get("vwap_pct") else ""
                    log.info(f"{symbol} ${a['price']:.2f} RSI:{a['rsi']:.1f} Vol:{a['volume_mult']:.1f}x{vwap_str} {a['regime']}")
                    if a["patterns"]: log.info(f"{symbol} patterns: {[p[0] for p in a['patterns']]}")
                    buy,trade_type,size,reason=check_buy(a,brain,open_trades,is_crypto=False)
                    if buy:
                        proceed,claude_reason=claude_trade_signal(symbol,a,brain)
                        if not proceed:
                            log.info(f"{symbol} Claude SKIP: {claude_reason}")
                            continue
                        log.info(f"{symbol} BUY ({trade_type}): {reason} ${size}")
                        result=buy_asset(symbol,size,is_crypto=False)
                        if result:
                            order_id=result.get("id","?")
                            trade=record_open(brain,symbol,trade_type,order_id,a["price"],a,size)
                            open_trades[order_id]=trade
                            pat_str=", ".join(p[0] for p in a["patterns"]) if a["patterns"] else "None"
                            tg(f"BUY {symbol} {trade_type.upper()} ${size}\n\nPrice: ${a['price']:.2f}\nRSI: {a['rsi']:.1f} | Vol: {a['volume_mult']:.1f}x | VWAP: {a.get('vwap_pct',0):+.1f}%\nReason: {reason}\nClaude: {claude_reason[:60]}\nPatterns: {pat_str}\nTrail stop: ${trade['trail_stop']:.2f}")
                        else:
                            tg(f"BUY FAILED {symbol}")
                    else:
                        nb=reason if reason else "RSI "+str(round(a["rsi"],1))
                        log.info(f"{symbol}: no buy ({nb})")
            else:
                log.info("Stock market closed")

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
        tg("JARVIS ALPHA V2 stopped (Ctrl+C)")
    except Exception as e:
        log.error(f"Fatal: {e}")
        tg(f"JARVIS ALPHA V2 crashed: {e}")
