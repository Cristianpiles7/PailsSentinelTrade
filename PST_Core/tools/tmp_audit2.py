import json
with open('.agents/data/recent_losses.json', 'r') as f: data = json.load(f)
for l in data['losses']:
    if 'Mean-Reversion' in l['strategy_name']:
        print(f"MR Loss: {l['symbol']} {l['type']} P/L: {l['profit']:.2f}")
