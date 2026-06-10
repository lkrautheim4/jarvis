import json, sys
sys.path.insert(0, '/root/jarvis')
from jarvis_options_brain import get_yf_contracts, get_price
from datetime import datetime

with open('/root/jarvis/paper_trades.json') as f:
    data = json.load(f)

trades = data['trades']
print(f"PAPER TRADE TRACKER — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print(f"{'='*60}")
total_cost = 0
total_pnl = 0

for t in trades:
    ticker = t['ticker']
    strike = t['strike']
    expiry = t['expiry']
    entry_premium = t['premium']
    cost = t['cost_per_contract']
    
    try:
        price = get_price(ticker)
        contracts, _ = get_yf_contracts(ticker, 'put', max_dte=30)
        current_premium = None
        for c in contracts:
            if float(c['strike_price']) == strike and c['expiration_date'] == expiry:
                bid = c.get('bid', 0)
                ask = c.get('ask', 0)
                current_premium = round((float(bid)+float(ask))/2, 2)
                break
        
        if current_premium:
            pnl = round((current_premium - entry_premium) * 100, 2)
            pnl_pct = round((current_premium - entry_premium) / entry_premium * 100, 1)
            status = "🟢" if pnl > 0 else "🔴"
            print(f"{status} {ticker} PUT ${strike} | Entry:${entry_premium} Now:${current_premium} | PnL:${pnl:+.0f} ({pnl_pct:+.1f}%) | Stock:${price:.0f}")
            total_cost += cost
            total_pnl += pnl
        else:
            print(f"⚪ {ticker} PUT ${strike} | no current price")
    except Exception as e:
        print(f"❌ {ticker}: {e}")

print(f"{'='*60}")
print(f"Total invested (paper): ${total_cost:,.0f}")
import requests; requests.post(f"https://api.telegram.org/bot{__import__('jarvis_secrets').TG_TOKEN_TRADER}/sendMessage", json={"chat_id":"7534553840","text":open("/tmp/pt.txt").read()}, timeout=5) if False else None
print(f"Total P&L: ${total_pnl:+.0f}")

# Send to Telegram
try:
    import requests as _r
    _r.post(f"https://api.telegram.org/bot{__import__('jarvis_secrets').TG_TOKEN_TRADER}/sendMessage",
        json={"chat_id": "7534553840", "text": f"PAPER TRADES {datetime.now().strftime('%m/%d %H:%M')}\nP&L: ${total_pnl:+.0f} | Invested: ${total_cost:,.0f}"},
        timeout=5)
except: pass
