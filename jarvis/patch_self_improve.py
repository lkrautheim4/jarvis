
import re

with open('/root/jarvis/jarvis_master.py', 'r') as f:
    content = f.read()

# Find run_self_improvement and replace it entirely
start = content.find('def run_self_improvement(master):')
end = content.find('\ndef main():', start)

if start == -1 or end == -1:
    print(f"NOT FOUND start={start} end={end}")
else:
    new_func = '''def run_self_improvement(master):
    trades = master.get("trades",[]); wins = master["stats"]["wins"]
    losses = master["stats"]["losses"]; total = wins+losses
    if total < 5: return
    wr = wins/total; changes = []
    if wr > 0.65 and total >= 20:
        master["stats"]["size_multiplier"] = min(2.0, master["stats"]["size_multiplier"]+0.1)
        changes.append(f"Size UP to {master['stats']['size_multiplier']:.1f}x (WR:{wr*100:.0f}%)")
    elif wr < 0.40:
        master["stats"]["size_multiplier"] = max(0.3, master["stats"]["size_multiplier"]-0.15)
        changes.append(f"Size DOWN to {master['stats']['size_multiplier']:.1f}x (WR:{wr*100:.0f}%)")
    try:
        rsi_stats, hour_stats, funding_stats = analyze_condition_edges(trades)
        edge_alerts = get_edge_alerts(rsi_stats, hour_stats, funding_stats)
        master["stats"]["edge_alerts"] = edge_alerts
        master["stats"]["rsi_stats"] = rsi_stats
        master["stats"]["best_hours"] = {h:d for h,d in hour_stats.items() if d.get("total",0)>=3}
        high_edges = [a for a in edge_alerts if a["edge"] == "HIGH"]
        avoid_edges = [a for a in edge_alerts if a["edge"] == "AVOID"]
        for e in high_edges:
            changes.append(f"EDGE: {e['type'].upper()} {e['condition']} WR:{e['wr']}% ({e['total']} trades)")
        for e in avoid_edges:
            changes.append(f"AVOID: {e['type'].upper()} {e['condition']} WR:{e['wr']}% ({e['total']} trades)")
    except Exception as ex:
        pass
    try:
        patterns = load_patterns(); fps = patterns["fingerprints"]
        best_patterns = [(fp,d) for fp,d in fps.items() if d.get("total",0)>=5]
        if best_patterns:
            best_patterns.sort(key=lambda x: x[1]["wins"]/x[1]["total"], reverse=True)
            top = best_patterns[0]; wr_top = round(top[1]["wins"]/top[1]["total"]*100)
            changes.append(f"Best pattern WR:{wr_top}% {top[0]}")
    except: pass
    try:
        if len(trades) >= 10:
            rsi_s, _, _ = analyze_condition_edges(trades)
            rsi_summary = " | ".join([f"{z}:{round(d['wins']/d['total']*100)}%({d['total']})" for z,d in rsi_s.items() if d['total']>=3])
            prompt = f"Jarvis self-improvement. {total} trades {wr*100:.0f}% WR. RSI: {rsi_summary or 'building'}. Give 2 specific changes. Plain text only."
            reply = claude(prompt, max_tokens=100)
            if reply: changes.append(f"Claude: {reply.strip()}")
    except: pass
    save_master(master)
    if changes:
        msg = f"JARVIS SELF-IMPROVEMENT\nTrades:{total} WR:{wr*100:.0f}% Size:{master['stats']['size_multiplier']:.1f}x\n" + "\n".join(changes)
        tg(msg)
        log.info(f"Self-improvement complete")

'''
    content = content[:start] + new_func + content[end:]
    with open('/root/jarvis/jarvis_master.py', 'w') as f:
        f.write(content)
    print("run_self_improvement upgraded OK")
