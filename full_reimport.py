import sqlite3
import os
import MetaTrader5 as mt5
import time
from datetime import datetime

def full_reimport():
    db_path = r"C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PST_Core\data\pst_trading.db"
    if not os.path.exists(db_path):
        print("DB no encontrada")
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    print("Conectando a DB...")
    
    if not mt5.initialize():
        print("Fallo MT5")
        conn.close()
        return
    
    end_time = int(time.time()) + 86400
    start_time = end_time - (3600 * 24 * 30)
    deals = mt5.history_deals_get(start_time, end_time)
    
    if not deals:
        print("Sin deals en MT5")
        mt5.shutdown()
        conn.close()
        return
    
    print(f"Encontrados {len(deals)} deals en MT5")
    
    imported = 0
    for d in deals:
        if d.entry in [1, 2]:
            total_pnl = d.profit + d.swap + d.commission
            trade_type = "BUY" if d.type == 1 else "SELL"
            time_str = datetime.fromtimestamp(d.time).strftime("%Y-%m-%d %H:%M:%S")
            
            cur.execute("""
                INSERT INTO trades (symbol, type, volume, price_in, price_out, sl, tp, profit, time_in, time_out, regime_at_entry, strategy_name, ticket, is_partial_closed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d.symbol, trade_type, d.volume, 0.0, d.price, 0.0, 0.0,
                float(total_pnl), time_str, time_str, "EXTERNAL", "AUTO_SYNC",
                d.position_id, 0
            ))
            imported += 1
            print(f"  OK {d.symbol} | Ticket {d.position_id} | PnL: {total_pnl:+.2f}")
    
    conn.commit()
    conn.close()
    mt5.shutdown()
    
    print(f"\nImportados {imported} trades cerrados correctamente.")

if __name__ == "__main__":
    full_reimport()
