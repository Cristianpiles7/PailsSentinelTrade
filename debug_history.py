import MetaTrader5 as mt5
import time
from datetime import datetime

if not mt5.initialize():
    print("Error init")
    quit()

end_time = int(time.time()) + 3600
start_time = end_time - (3600 * 24 * 30) # 30 dias
deals = mt5.history_deals_get(start_time, end_time)

if deals is None:
    print(f"Error history: {mt5.last_error()}")
else:
    print(f"Encontrados {len(deals)} deals")
    # Mostrar solo los 10 ultimos cierres
    count = 0
    for d in reversed(deals):
        if d.entry in [1, 2, 3]:
            print(f"DEAL: Sym: {d.symbol}, Entry: {d.entry}, PosID: {d.position_id}, Ticket: {d.ticket}, PnL: {d.profit}")
            count += 1
            if count >= 10: break

mt5.shutdown()
