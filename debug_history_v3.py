import MetaTrader5 as mt5
import time
from datetime import datetime

if not mt5.initialize():
    print(f"Error init: {mt5.last_error()}")
    quit()

# Rango muy amplio para no fallar
end_time = int(time.time()) + 3600*24 
start_time = end_time - (3600 * 24 * 30) 

deals = mt5.history_deals_get(start_time, end_time)

if deals:
    print(f"Encontrados {len(deals)} deals.")
    for d in deals:
        if d.symbol:
             print(f"Deal: {d.ticket}, Sym: {d.symbol}, Entry: {d.entry}, PosID: {d.position_id}, Time: {datetime.fromtimestamp(d.time)}")
else:
    print("No deals found")

mt5.shutdown()
