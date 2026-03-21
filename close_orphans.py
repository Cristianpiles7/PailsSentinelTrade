import sqlite3
import MetaTrader5 as mt5
import time
from datetime import datetime

DB_PATH = r"C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PST_Core\data\pst_trading.db"

def close_orphan_trades():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    cur = db.cursor()
    
    # Ver trades "abiertos" (price_out=0)
    cur.execute("SELECT id, symbol, ticket, profit, price_out FROM trades WHERE price_out IS NULL OR price_out = 0")
    open_trades = cur.fetchall()
    print(f"Trades abiertos en DB: {len(open_trades)}")
    for t in open_trades:
        print(f"  ID:{t['id']} | {t['symbol']} | ticket:{t['ticket']} | price_out:{t['price_out']}")
    
    # Conectar a MT5 y buscar si esos tickets CERRARON
    if not mt5.initialize():
        print("No MT5")
        db.close()
        return
    
    end_ts = int(time.time()) + 3600
    start_ts = end_ts - (3600 * 24 * 30)
    deals = mt5.history_deals_get(start_ts, end_ts)
    
    # Buscar los deals de cierre de cada trade abierto
    closed_count = 0
    for t in open_trades:
        pos_id = t['ticket']
        if not pos_id: continue
        
        # Buscar deal de cierre con este position_id
        closing_deals = [d for d in deals if d.position_id == pos_id and d.entry in [1,2]]
        
        if closing_deals:
            d = closing_deals[-1]  # Tomar el último cierre
            total_pnl = d.profit + d.swap + d.commission
            time_out = datetime.fromtimestamp(d.time).strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n  CERRANDO ID:{t['id']} {t['symbol']} ticket:{pos_id}")
            print(f"    Deal ticket:{d.ticket} | price:{d.price} | pnl:{total_pnl:.2f}")
            
            cur.execute("UPDATE trades SET price_out=?, profit=?, time_out=? WHERE id=?",
                       (d.price, float(total_pnl), time_out, t['id']))
            closed_count += 1
        else:
            # Comprobar si sigue abierto en MT5
            pos = mt5.positions_get(ticket=int(pos_id)) if pos_id else None
            if pos:
                print(f"  SIGUE ABIERTO: {t['symbol']} ticket:{pos_id}")
            else:
                print(f"  NO ENCONTRADO en MT5: {t['symbol']} ticket:{pos_id} - puede ser muy antiguo")
    
    db.commit()
    db.close()
    mt5.shutdown()
    print(f"\nCerrados: {closed_count} trades huerfanos.")

if __name__ == "__main__":
    close_orphan_trades()
