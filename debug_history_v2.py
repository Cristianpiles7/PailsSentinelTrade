import MetaTrader5 as mt5
import time
from datetime import datetime

if not mt5.initialize():
    print(f"Error init: {mt5.last_error()}")
    quit()

acc = mt5.account_info()
if acc:
    print(f"Login: {acc.login}, Server: {acc.server}, Balance: {acc.balance}")
else:
    print("No account info")

end_time = int(time.time()) + 3600*24 # +1 dia margen
start_time = end_time - (3600 * 24 * 60) # 60 dias

deals = mt5.history_deals_get(start_time, end_time)

if deals is None:
    print(f"Error history: {mt5.last_error()}")
else:
    print(f"Encontrados {len(deals)} deals en los ultimos 60 dias.")
    if len(deals) > 0:
        for d in deals[:5]:
            print(f"D: {d.ticket}, Sym: {d.symbol}, Time: {datetime.fromtimestamp(d.time)}")

mt5.shutdown()
