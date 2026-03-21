import MetaTrader5 as mt5
import time
from datetime import datetime

def full_dump():
    if not mt5.initialize():
        print("Fallo MT5")
        return

    end_time = int(time.time()) + 86400
    start_time = end_time - (3600 * 24 * 365)
    
    # TODOS los deals
    deals = mt5.history_deals_get(start_time, end_time)
    
    if not deals:
        print(f"Sin deals. Error: {mt5.last_error()}")
        mt5.shutdown()
        return
    
    print(f"TOTAL DEALS: {len(deals)}")
    print("\n=== TODOS LOS DEALS (últimos 365 días) ===")
    for d in deals:
        t = datetime.fromtimestamp(d.time).strftime('%Y-%m-%d %H:%M:%S')
        print(f"Deal:{d.ticket:>12} | Pos:{d.position_id:>12} | {d.symbol:<12} | "
              f"Entry:{d.entry} | Type:{d.type} | Vol:{d.volume:.2f} | "
              f"Price:{d.price:.2f} | Profit:{d.profit:>8.2f} | "
              f"Swap:{d.swap:.2f} | Comm:{d.commission:.2f} | "
              f"Time:{t} | {d.comment}")
    
    # Resumen por símbolo de deals cerrados (entry=1)
    print("\n=== RESUMEN P&L POR SÍMBOLO (solo cierres, entry=1 o 2) ===")
    pnl_by_sym = {}
    for d in deals:
        if d.entry in [1, 2]:
            sym = d.symbol
            if sym not in pnl_by_sym:
                pnl_by_sym[sym] = {"profit": 0, "swap": 0, "comm": 0, "count": 0}
            pnl_by_sym[sym]["profit"] += d.profit
            pnl_by_sym[sym]["swap"] += d.swap
            pnl_by_sym[sym]["comm"] += d.commission
            pnl_by_sym[sym]["count"] += 1
    
    for sym, data in sorted(pnl_by_sym.items()):
        net = data["profit"] + data["swap"] + data["comm"]
        print(f"{sym:<12} | Cierres: {data['count']} | Profit: {data['profit']:>8.2f} | "
              f"Swap: {data['swap']:>6.2f} | Comm: {data['comm']:>6.2f} | NET: {net:>8.2f}")

    mt5.shutdown()

if __name__ == "__main__":
    full_dump()
