"""
kalshi_auth.py — Kalshi RSA Authentication Module
Reads keys from kalshi_keys.py — never hardcoded
"""

import time
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
import base64

# Load keys from secure file
try:
    from kalshi_keys import KALSHI_KEY_ID, KALSHI_PRIVATE_KEY
except ImportError:
    raise Exception("kalshi_keys.py not found — create it with KALSHI_KEY_ID and KALSHI_PRIVATE_KEY")

KALSHI_BASE = "https://trading-api.kalshi.com/trade-api/v2"

def _sign_request(method: str, path: str) -> dict:
    """Generate RSA signed headers for Kalshi API."""
    ts = str(int(time.time() * 1000))
    msg = ts + method.upper() + path
    
    # Load private key
    private_key = serialization.load_pem_private_key(
        KALSHI_PRIVATE_KEY.encode() if isinstance(KALSHI_PRIVATE_KEY, str) else KALSHI_PRIVATE_KEY,
        password=None,
        backend=default_backend()
    )
    
    # Sign
    signature = private_key.sign(
        msg.encode('utf-8'),
        padding.PKCS1v15(),
        hashes.SHA256()
    )
    sig_b64 = base64.b64encode(signature).decode('utf-8')
    
    return {
        "KALSHI-ACCESS-KEY":       KALSHI_KEY_ID,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "Content-Type":            "application/json"
    }

def kalshi_get(path: str, params: dict = None) -> dict:
    """Make authenticated GET request to Kalshi."""
    headers = _sign_request("GET", path)
    r = requests.get(
        KALSHI_BASE + path,
        headers=headers,
        params=params,
        timeout=15
    )
    if r.status_code == 200:
        return r.json()
    raise Exception(f"Kalshi GET {path} failed: {r.status_code} {r.text[:200]}")

def kalshi_post(path: str, data: dict) -> dict:
    """Make authenticated POST request to Kalshi."""
    import json
    headers = _sign_request("POST", path)
    r = requests.post(
        KALSHI_BASE + path,
        headers=headers,
        json=data,
        timeout=15
    )
    if r.status_code in [200, 201]:
        return r.json()
    raise Exception(f"Kalshi POST {path} failed: {r.status_code} {r.text[:200]}")

def get_btc_markets(current_price: float) -> list:
    """Get BTC hourly markets near current price."""
    try:
        data = kalshi_get("/markets", {
            "status": "open",
            "series_ticker": "KXBTC",
            "limit": 100
        })
        markets = data.get("markets", [])
        
        # Filter to markets with strikes within $500 of current price
        relevant = []
        for m in markets:
            strike = float(m.get("floor_strike", 0) or 0)
            if strike == 0:
                continue
            if abs(strike - current_price) <= 500:
                yes_bid = float(m.get("yes_bid_dollars", "0") or 0)
                no_bid  = float(m.get("no_bid_dollars",  "0") or 0)
                relevant.append({
                    "ticker":     m.get("ticker", ""),
                    "title":      m.get("title", ""),
                    "strike":     strike,
                    "yes_bid":    yes_bid,
                    "no_bid":     no_bid,
                    "yes_ask":    float(m.get("yes_ask_dollars", "0") or 0),
                    "close_time": m.get("close_time", ""),
                    "volume":     float(m.get("volume_24h_fp", "0") or 0),
                })
        
        # Sort by distance from current price
        relevant.sort(key=lambda x: abs(x["strike"] - current_price))
        return relevant[:5]
    except Exception as e:
        return []

def find_edge(jarvis_prob: float, current_price: float) -> dict:
    """
    Compare Jarvis probability to Kalshi market pricing.
    Returns the best edge opportunity.
    jarvis_prob: 0.0 to 1.0
    """
    markets = get_btc_markets(current_price)
    if not markets:
        return {"edge": None, "reason": "No Kalshi markets found"}
    
    best_edge = None
    best_edge_size = 0
    
    for m in markets:
        strike = m["strike"]
        yes_bid = m["yes_bid"]
        no_bid  = m["no_bid"]
        
        if yes_bid <= 0 and no_bid <= 0:
            continue
        
        # Kalshi implied probability
        kalshi_prob = yes_bid  # yes_bid in dollars = probability
        
        # Edge = difference between Jarvis and Kalshi
        edge = jarvis_prob - kalshi_prob
        edge_size = abs(edge)
        
        if edge_size > best_edge_size and edge_size > 0.10:  # min 10% edge
            best_edge_size = edge_size
            action = "YES" if edge > 0 else "NO"
            price_to_pay = yes_bid if action == "YES" else no_bid
            best_edge = {
                "ticker":        m["ticker"],
                "strike":        strike,
                "action":        action,
                "price_cents":   round(price_to_pay * 100),
                "jarvis_prob":   round(jarvis_prob * 100),
                "kalshi_prob":   round(kalshi_prob * 100),
                "edge_pct":      round(edge_size * 100),
                "close_time":    m["close_time"][:16],
                "volume":        m["volume"],
            }
    
    return best_edge or {"edge": None, "reason": "No significant edge found"}

def place_bet(ticker: str, action: str, amount_dollars: float) -> dict:
    """
    Place a Kalshi bet.
    action: YES or NO
    amount_dollars: how much to bet in dollars
    """
    side = "yes" if action == "YES" else "no"
    
    # Get current price for the order
    markets = kalshi_get(f"/markets/{ticker}")
    market = markets.get("market", {})
    
    if action == "YES":
        price = float(market.get("yes_ask_dollars", "0.5"))
    else:
        price = float(market.get("no_ask_dollars", "0.5"))
    
    # Calculate contracts (each contract = $1 notional)
    count = max(1, int(amount_dollars / 1.0))
    
    order = {
        "ticker":       ticker,
        "action":       "buy",
        "side":         side,
        "type":         "limit",
        "count":        count,
        "limit_price":  str(round(price, 2)),
        "time_in_force": "gtc",
    }
    
    return kalshi_post("/portfolio/orders", order)

def get_balance() -> float:
    """Get Kalshi account balance."""
    try:
        data = kalshi_get("/portfolio/balance")
        return float(data.get("balance", 0)) / 100  # cents to dollars
    except:
        return 0.0

def get_positions() -> list:
    """Get open Kalshi positions."""
    try:
        data = kalshi_get("/portfolio/positions")
        return data.get("market_positions", [])
    except:
        return []
