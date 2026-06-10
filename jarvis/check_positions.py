import requests

ALPACA_KEY    = "PKTHANGUNVFDSLLR3VXPETXRQF"
ALPACA_SECRET = "GRTDDfkCGWbZMoNSWms6uJSGvw72rHaAk1N1fvLi8EAP"
ALPACA_BASE   = "https://paper-api.alpaca.markets"

hdrs = {
    "APCA-API-KEY-ID": ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET
}

# Get all positions
r = requests.get(f"{ALPACA_BASE}/v2/positions", headers=hdrs)
print("POSITIONS:")
print(r.json())

# Get all orders
r2 = requests.get(f"{ALPACA_BASE}/v2/orders?status=all&limit=5", headers=hdrs)
print("\nRECENT ORDERS:")
for o in r2.json():
    print(f"  {o.get('symbol')} | {o.get('side')} | {o.get('status')} | ${o.get('notional','?')}")
