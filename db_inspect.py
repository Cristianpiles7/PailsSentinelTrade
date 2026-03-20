import MetaTrader5 as mt5
from datetime import datetime, timedelta
mt5.initialize()
# Bajamos un rango muy amplio (1 año atrás)
start = datetime.now() - timedelta(days=365)
deals = mt5.history_deals_get(start, datetime.now())
print(f"TOTAL DEALS ENCONTRADOS: {len(deals) if deals else 0}")
if deals:
    print(f"ÚLTIMOS 3 DEALS: {deals[-3:]}")
mt5.shutdown()
