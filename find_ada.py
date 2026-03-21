import sqlite3, MetaTrader5 as mt5, time
from datetime import datetime

DB_PATH = r"C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PST_Core\data\pst_trading.db"

mt5.initialize()
end_ts = int(time.time()) + 3600
start_ts = end_ts - (3600 * 24 * 30)  # 30 dias
deals = mt5.history_deals_get(start_ts, end_ts)
print(f"Total deals en 30d: {len(deals) if deals else 0}")

# Mostrar todos los deals de ADA en 30d
print("\n=== ADA deals (30d) ===")
for d in deals:
    if 'ADA' in d.symbol.upper():
        print(f"Deal:{d.ticket} | Pos:{d.position_id} | Entry:{d.entry} | Price:{d.price:.4f} | Profit:{d.profit:.4f} | Comm:{d.commission:.4f} | Time:{datetime.fromtimestamp(d.time)}")

# Buscar los position_id huerfanos
orphan_pos_ids = [406834989, 406838116]
print("\n=== Buscando cierres de orphans ===")
for pos_id in orphan_pos_ids:
    closing = [d for d in deals if d.position_id == pos_id and d.entry in [1,2]]
    print(f"position_id {pos_id}: {len(closing)} deals de cierre encontrados")
    for d in closing:
        print(f"  Deal:{d.ticket} | Price:{d.price:.4f} | Profit:{d.profit:.4f}+{d.commission:.4f} | Time:{datetime.fromtimestamp(d.time)}")

mt5.shutdown()
