with open('jarvis_trader_1.py', 'r') as f:
    c = f.read()

# Add minimum hold time - don't sell within 30 min of buying
old = '    has_pos = any("BTC" in str(p.get("symbol","")) for p in positions)\n    if side=="sell" and not has_pos:'

new = '''    has_pos = any("BTC" in str(p.get("symbol","")) for p in positions)
    # Minimum hold time — don't sell within 30 min of last buy
    last_buy_time = max((t.get("open_time","") for t in load_memory().get("trades",[]) if t.get("direction")=="BUY"), default="")
    if last_buy_time and side=="sell":
        import datetime
        try:
            bought_at = datetime.datetime.fromisoformat(last_buy_time)
            held_mins = (datetime.datetime.now() - bought_at).seconds / 60
            if held_mins < 30:
                log.info(f"Hold time {held_mins:.0f}min < 30min minimum — not selling yet")
                tg_send(f"⏳ Holding BTC — only {held_mins:.0f} min since buy (min 30)")
                return False
        except: pass
    if side=="sell" and not has_pos:'''

if old in c:
    c = c.replace(old, new)
    with open('jarvis_trader_1.py', 'w') as f:
        f.write(c)
    import py_compile
    try:
        py_compile.compile('jarvis_trader_1.py', doraise=True)
        print("SUCCESS — 30 min hold time added, no syntax errors")
    except py_compile.PyCompileError as e:
        print(f"Syntax error: {e}")
else:
    print("NOT FOUND")
    idx = c.find('has_pos = any("BTC"')
    print(repr(c[idx:idx+200]))
