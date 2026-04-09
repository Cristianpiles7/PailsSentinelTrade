import json

with open('.agents/data/recent_losses.json', 'r') as f:
    data = json.load(f)

print("----- DETALLE DE PERDIDAS SCALPER ACTIVE -----")
for l in data['losses']:
    if l['strategy_name'] == 'PST-Scalper-Active':
        print(f"{l['symbol']} {l['type']} | Regime: {l['regime_at_entry']} | Profit: {l['profit']:.2f} | In: {l['price_in']} SL: {l['sl']} (Diff: {abs(l['price_in']-l['sl']):.4f})")
