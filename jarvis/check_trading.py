import json, requests
hdrs = {"APCA-API-KEY-ID":"PKTHANGUNVFDSLLR3VXPETXRQF","APCA-API-SECRET-KEY":"GRTDDfkCGWbZMoNSWms6uJSGvw72rHaAk1N1fvLi8EAP"}
print("=== STOCKS ===")
try:
    sb = json.load(open('/root/jarvis/jarvis_stocks_brain.json'))
    print(f"Trades: {sb.get('total_trades',0)} | P&L: ${sb.get('session_pnl',0):+.2f} | WR: {sb.get('win_rate',0)}%")
    print(f"Hot sectors: {sb.get('hot_sectors',[])}")
    print(f"Best tickers: {sb.get('best_tickers',[])}")
except Exception as e: print(f"Error: {e}")
print("\n=== OPTIONS ===")
try:
    ob = json.load(open('/root/jarvis/options_memory.json'))
    st = ob.get('stats', {})  # stats are nested under 'stats', not top-level
    print(f"Trades: {st.get('total_trades',0)} | P&L: ${st.get('total_pnl',0):+.2f} | W:{st.get('winners',0)} L:{st.get('losers',0)}")
    open_pos = [t for t in ob.get('trades',[]) if str(t.get('status','')).upper() == 'OPEN']
    print(f"Open positions: {len(open_pos)}")
except Exception as e: print(f"Error: {e}")
print("\n=== ALPACA ===")
try:
    acct = requests.get("https://paper-api.alpaca.markets/v2/account", headers=hdrs, timeout=10).json()
    pos  = requests.get("https://paper-api.alpaca.markets/v2/positions", headers=hdrs, timeout=10).json()
    print(f"Equity: ${float(acct.get('equity',0)):,.2f} | Buying power: ${float(acct.get('buying_power',0)):,.2f}")
    print(f"Day trades: {acct.get('daytrade_count',0)}")
    if isinstance(pos, list) and pos:
        for p in pos: print(f"  {p.get('symbol')} | P&L: ${float(p.get('unrealized_pl',0)):+.2f}")
    else: print("  No open positions")
except Exception as e: print(f"Error: {e}")
