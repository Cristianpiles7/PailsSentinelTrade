import json
with open('.agents/data/recent_losses.json', 'r') as f:
    d = json.load(f)
for l in d['losses'][:20]:
    print(f"{l['symbol']} {l['type']} IN: {l['price_in']} OUT: {l['price_out']} SL: {l['sl']} (dist: {abs(l['price_in'] - l['sl']):.4f}) Reg: {l.get('regime_at_entry')}")
