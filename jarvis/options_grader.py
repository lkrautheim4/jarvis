"""
JARVIS Options Paper Trade Grader
Checks open paper trades every 5 minutes during market hours.
Exit rules:
  - Take profit: current premium >= 1.5x entry (50% gain)
  - Stop loss: current premium <= 0.5x entry (50% loss)
  - DTE exit: 2 days or less remaining — close regardless
  - Expiry: auto-close as LOSS if expired
"""
import sys, time, json, logging
sys.path.insert(0, '/root/jarvis')
import requests
from datetime import datetime, date
import yfinance as yf
import paper_trades_store as store

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

PAPER_TRADES_FILE = '/root/jarvis/paper_trades.json'
TELEGRAM_TOKEN = __import__("jarvis_secrets").TG_TOKEN_INTEL
TELEGRAM_CHAT = __import__("jarvis_secrets").TG_CHAT_ID

def tg(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        log.info(f"TG (no token): {msg[:100]}")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT, "text": msg},
            timeout=10
        )
        if r.status_code != 200 or not r.json().get("ok"):
            log.error(f"Telegram send FAILED: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")

def load_trades():
    try:
        with open(PAPER_TRADES_FILE, 'r') as f:
            return json.load(f)
    except:
        return {"trades": []}

def save_trades(data):
    with open(PAPER_TRADES_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def get_current_premium(ticker, strategy, strike, expiry):
    """Fetch current mid price for option contract"""
    try:
        tk = yf.Ticker(ticker)
        opt = tk.option_chain(expiry)
        chain = opt.puts if 'put' in strategy else opt.calls
        row = chain[chain['strike'] == strike]
        if row.empty:
            # Find closest strike
            chain['diff'] = abs(chain['strike'] - strike)
            row = chain.nsmallest(1, 'diff')
        if row.empty:
            return None
        bid = float(row['bid'].values[0])
        ask = float(row['ask'].values[0])
        if bid <= 0 and ask <= 0:
            return None
        return round((bid + ask) / 2, 2)
    except Exception as e:
        log.error(f"Premium fetch error {ticker}: {e}")
        return None

def check_trade(trade):
    """
    Returns (should_close, reason, exit_price, pnl, result)
    """
    entry_premium = trade.get('premium', 0)
    strike = trade.get('strike', 0)
    expiry = trade.get('expiry', '')
    ticker = trade.get('ticker', '')
    strategy = trade.get('strategy', 'put_buy')

    if not entry_premium or not expiry:
        return False, None, None, None, None

    # Short positions (put_sell, call_sell) profit when the premium decays.
    # P&L sign and gain_pct direction are flipped vs. long positions.
    is_short = strategy in ('put_sell', 'call_sell')

    def _calc_pnl(current):
        if is_short:
            return round((entry_premium - current) * 100, 2)
        return round((current - entry_premium) * 100, 2)

    def _gain_pct(current):
        if entry_premium == 0:
            return 0.0
        if is_short:
            return (entry_premium - current) / entry_premium
        return (current - entry_premium) / entry_premium

    # Check DTE
    try:
        exp_date = datetime.strptime(expiry, '%Y-%m-%d').date()
        dte = (exp_date - date.today()).days
    except:
        dte = 999

    # Auto-close expired trades
    if dte < 0:
        current = get_current_premium(ticker, strategy, strike, expiry) or 0
        pnl = _calc_pnl(current)
        result = 'WIN' if pnl > 0 else 'LOSS'
        return True, 'EXPIRED', current, pnl, result

    # DTE exit — close at 2 days remaining
    if dte <= 2:
        current = get_current_premium(ticker, strategy, strike, expiry)
        if current is None:
            return False, None, None, None, None
        pnl = _calc_pnl(current)
        result = 'WIN' if pnl > 0 else 'LOSS'
        return True, f'DTE_EXIT ({dte}d remaining)', current, pnl, result

    # Get current premium
    current = get_current_premium(ticker, strategy, strike, expiry)
    if current is None:
        log.info(f"Could not fetch premium for {ticker} {strategy} ${strike} {expiry}")
        return False, None, None, None, None

    pnl = _calc_pnl(current)
    gain_pct = _gain_pct(current)

    # Take profit: +50% (from seller's perspective: premium decayed ≥50%)
    if gain_pct >= 0.50:
        return True, f'TAKE_PROFIT (+{round(gain_pct*100)}%)', current, pnl, 'WIN'

    # Stop loss: -50% (from seller's perspective: premium grew ≥50%)
    if gain_pct <= -0.50:
        return True, f'STOP_LOSS ({round(gain_pct*100)}%)', current, pnl, 'LOSS'

    direction = "short" if is_short else "long"
    log.info(f"{ticker} ${strike} {strategy} ({direction}): premium ${current} vs entry ${entry_premium} ({round(gain_pct*100):+}%) — holding")
    return False, None, None, None, None

def _trade_key(t):
    """Stable identity for a trade (no unique id field exists). entry_time
    disambiguates otherwise-identical AMD puts logged at different minutes."""
    return (t.get('ticker'), t.get('strategy'), t.get('strike'), t.get('expiry'),
            t.get('premium'), t.get('entry_date'), t.get('entry_time'))

def run_grader():
    # Phase 1: read a snapshot and do all the slow yfinance work WITHOUT the
    # lock, so we never hold paper_trades.json locked during network calls.
    snapshot = store.read()
    open_trades = [t for t in snapshot['trades'] if t.get('status') == 'paper_open']
    log.info(f"Checking {len(open_trades)} open paper trades")

    closes = {}  # _trade_key -> (reason, exit_price, pnl, result, trade)
    for trade in open_trades:
        should_close, reason, exit_price, pnl, result = check_trade(trade)
        if should_close:
            closes[_trade_key(trade)] = (reason, exit_price, pnl, result, trade)

    # Phase 2: commit the closes inside the lock, re-reading fresh and matching
    # by identity — so any trades appended by other bots since the snapshot are
    # preserved (no lost updates) and we only touch still-open matches.
    applied = []
    if closes:
        def _apply(data):
            for t in data['trades']:
                if t.get('status') != 'paper_open':
                    continue
                k = _trade_key(t)
                c = closes.get(k)
                if not c:
                    continue
                reason, exit_price, pnl, result, _ = c
                t['status'] = 'paper_closed'
                t['result'] = result
                t['exit_premium'] = exit_price  # option premium at exit
                t['exit_price'] = None           # stock price at exit (not fetched from chain)
                t['pnl'] = pnl
                t['exit_date'] = datetime.now().strftime('%Y-%m-%d')
                t['exit_time'] = datetime.now().strftime('%H:%M')
                t['exit_reason'] = reason
                applied.append(k)
        store.update(_apply)

    # Phase 3: notify + log for what we actually closed (outside the lock).
    for k in applied:
        reason, exit_price, pnl, result, trade = closes[k]
        emoji = '✅' if result == 'WIN' else '❌'
        msg = (
            f"{emoji} PAPER TRADE CLOSED\n"
            f"{'='*24}\n"
            f"{trade['strategy'].upper()}: {trade['ticker']}\n"
            f"Strike: ${trade['strike']} | Entry: ${trade['premium']:.2f}\n"
            f"Exit: ${exit_price:.2f} | Reason: {reason}\n"
            f"P&L: ${pnl:+.2f} per contract\n"
            f"{'='*24}\n"
            f"Score was: {trade.get('score', '?')}/100"
        )
        tg(msg)
        log.info(f"Closed {trade['ticker']} ${trade['strike']}: {result} {reason} pnl=${pnl}")
        # Feedback loop: credit the signals that triggered this trade (+1 WIN / -1 LOSS)
        try:
            from jarvis_options_brain import credit_signal_weights
            credit_signal_weights(trade.get('signals', []), result)
        except Exception as e:
            log.error(f"signal credit {trade.get('ticker')}: {e}")

    # Summary stats (fresh read)
    data = store.read()
    closed = [t for t in data['trades'] if t.get('status') == 'paper_closed' and t.get('result')]
    if closed:
        wins = sum(1 for t in closed if t['result'] == 'WIN')
        total_pnl = sum(t.get('pnl', 0) or 0 for t in closed)
        log.info(f"Paper record: {wins}W/{len(closed)-wins}L | Total P&L: ${total_pnl:.2f}")

REPORT_STATE_FILE = '/root/jarvis/morning_report_sent.json'

def _report_already_sent_today():
    today = date.today().isoformat()
    try:
        st = json.load(open(REPORT_STATE_FILE))
    except Exception:
        st = {}
    if st.get('date') == today:
        return True
    try:
        json.dump({'date': today}, open(REPORT_STATE_FILE, 'w'))
    except Exception:
        pass
    return False

def morning_report():
    """8am ET summary: every open position w/ current P&L, total book P&L, and
    any position within 10% of its stop-loss (-50%) or take-profit (+50%)."""
    data = store.read()
    opens = [t for t in data['trades'] if t.get('status') == 'paper_open']
    if not opens:
        tg("☀️ MORNING OPTIONS REPORT\nNo open paper positions.")
        return
    rows = []
    total_pnl = 0.0
    total_cost = 0.0
    near = []
    for t in opens:
        entry = t.get('premium', 0) or 0
        total_cost += t.get('cost_per_contract', 0) or 0
        strat = t.get('strategy', 'put_buy')
        is_short = strat in ('put_sell', 'call_sell')
        cur = get_current_premium(t['ticker'], strat, t['strike'], t['expiry'])
        if cur is None:
            rows.append((t, None, None))
            continue
        if entry:
            pct = ((entry - cur) / entry * 100) if is_short else ((cur - entry) / entry * 100)
        else:
            pct = 0.0
        pos_pnl = ((entry - cur) if is_short else (cur - entry)) * 100
        total_pnl += pos_pnl
        rows.append((t, cur, pct))
        if pct >= 40:
            near.append(f"🟢 {t['ticker']} ${t['strike']} {pct:+.0f}% — near TAKE-PROFIT")
        elif pct <= -40:
            near.append(f"🔴 {t['ticker']} ${t['strike']} {pct:+.0f}% — near STOP-LOSS")
    rows.sort(key=lambda r: (r[2] is None, -(r[2] if r[2] is not None else -999)))
    lines = ["☀️ MORNING OPTIONS REPORT", "=" * 26]
    for t, cur, pct in rows:
        if cur is None:
            lines.append(f"{t['ticker']} ${t['strike']} {t['expiry']} — no quote")
        else:
            entry = t.get('premium', 0) or 0
            is_short = t.get('strategy', 'put_buy') in ('put_sell', 'call_sell')
            pnl = ((entry - cur) if is_short else (cur - entry)) * 100
            lines.append(f"{t['ticker']} ${t['strike']} {t['expiry']}  "
                         f"${entry:.2f}→${cur:.2f}  {pct:+.0f}%  ${pnl:+.0f}")
    lines.append("=" * 26)
    lines.append(f"Open: {len(opens)} | Book cost: ${total_cost:,.0f}")
    lines.append(f"Open book P&L: ${total_pnl:+,.0f}")
    if near:
        lines.append("")
        lines.append("⚠️ Within 10% of a threshold:")
        lines += near
    tg("\n".join(lines))
    log.info(f"Morning report sent: {len(opens)} open, P&L ${total_pnl:+.0f}")

def log_event(bot, event, sym=None, dec=None, reason=None):
    import sqlite3
    from datetime import datetime
    conn = sqlite3.connect('jarvis_memory.db')
    cur = conn.cursor()
    cur.execute("INSERT INTO bot_events (ts, bot_name, event_type, symbol, decision, reason) VALUES (?,?,?,?,?,?)", (datetime.now().isoformat(), bot, event, sym, dec, reason))
    conn.commit()
    conn.close()

def main():
    log.info("OPTIONS GRADER ONLINE — checking every 5 minutes during market hours")
    tg("📊 OPTIONS PAPER GRADER ONLINE\nMonitoring open trades\nExit rules: +50% profit, -50% stop, 2 DTE close")
    while True:
        try:
            now = datetime.now()
            edt_hour = (datetime.utcnow().hour - 4) % 24
            # Morning report at 8am ET (before market open), once per day
            if edt_hour == 8 and not _report_already_sent_today():
                morning_report()
            # Only run during market hours + 30 min after close
            if 9 <= edt_hour <= 16:
                run_grader()
            else:
                log.info(f"Market closed (EDT hour {edt_hour}) — sleeping")
        except Exception as e:
            log.error(f"Grader error: {e}")
        try:
            import jarvis_brain
            jarvis_brain.update_bot_heartbeat("options_grader")
        except Exception:
            pass
        time.sleep(300)  # every 5 minutes

if __name__ == '__main__':
    main()
