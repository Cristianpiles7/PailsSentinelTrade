import MetaTrader5 as mt5
import time
from datetime import datetime

def check_deal():
    if not mt5.initialize():
        print("Fallo MT5")
        return

    # Buscar el deal específico
    end_time = int(time.time()) + 3600
    start_time = end_time - (3600 * 24 * 30)
    
    deals = mt5.history_deals_get(start_time, end_time)
    
    if not deals:
        print("Sin deals")
        mt5.shutdown()
        return
    
    print(f"Total deals: {len(deals)}")
    print("\n=== DEALS DE XAU ===")
    for d in deals:
        if 'XAU' in d.symbol:
            print(f"Ticket: {d.ticket} | PosID: {d.position_id} | Sym: {d.symbol} | "
                  f"Entry: {d.entry} | Type: {d.type} | Profit: {d.profit} | "
                  f"Swap: {d.swap} | Comm: {d.commission} | "
                  f"Volume: {d.volume} | Price: {d.price} | "
                  f"Time: {datetime.fromtimestamp(d.time)} | Comment: {d.comment}")
    
    # Verificar posiciones abiertas de XAU
    print("\n=== POSICIONES ABIERTAS XAU ===")
    pos = mt5.positions_get()
    if pos:
        for p in pos:
            if 'XAU' in p.symbol:
                print(f"Ticket: {p.ticket} | Sym: {p.symbol} | Profit: {p.profit} | Volume: {p.volume} | Type: {p.type}")
    
    mt5.shutdown()

if __name__ == "__main__":
    check_deal()
