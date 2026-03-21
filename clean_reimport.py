import sqlite3
import os
import MetaTrader5 as mt5
import time
from datetime import datetime

def clean_reimport():
    db_path = r"C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PST_Core\data\pst_trading.db"
    if not os.path.exists(db_path):
        print("DB no encontrada")
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # PASO 1: Purgar TODO
    cur.execute("DELETE FROM trades")
    conn.commit()
    print("DB purgada.")
    
    # PASO 2: Obtener deals
    if not mt5.initialize():
        print("Fallo MT5")
        conn.close()
        return
    
    end_time = int(time.time()) + 86400
    start_time = end_time - (3600 * 24 * 30)
    deals = mt5.history_deals_get(start_time, end_time)
    
    if not deals:
        print("Sin deals")
        mt5.shutdown()
        conn.close()
        return
    
    print(f"Deals MT5: {len(deals)}")
    
    # PASO 3: Importar cierres SIN DUPLICADOS (usar deal ticket como unique key)
    imported = 0
    seen_deal_tickets = set()
    for d in deals:
        if d.entry in [1, 2] and d.ticket not in seen_deal_tickets:
            seen_deal_tickets.add(d.ticket)
            total_pnl = d.profit + d.swap + d.commission
            trade_type = "BUY" if d.type == 1 else "SELL"
            time_str = datetime.fromtimestamp(d.time).strftime("%Y-%m-%d %H:%M:%S")
            
            cur.execute("""
                INSERT INTO trades (symbol, type, volume, price_in, price_out, sl, tp, profit, time_in, time_out, regime_at_entry, strategy_name, ticket, is_partial_closed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d.symbol, trade_type, d.volume, 0.0, d.price, 0.0, 0.0,
                float(total_pnl), time_str, time_str, "EXTERNAL", "AUTO_SYNC",
                d.ticket, 0  # Usar deal ticket, NO position_id para evitar colisiones
            ))
            imported += 1
    
    conn.commit()
    
    # Verificar XAU
    cur.execute("SELECT id, profit, ticket FROM trades WHERE symbol LIKE '%XAU%'")
    xau_rows = cur.fetchall()
    xau_total = sum(r[1] for r in xau_rows if r[1])
    print(f"\nXAUUSD: {len(xau_rows)} registros, Total: {xau_total:.2f}")
    for r in xau_rows:
        print(f"  ID:{r[0]} | Profit:{r[1]:.2f} | DealTicket:{r[2]}")
    
    conn.close()
    mt5.shutdown()
    print(f"\nImportados {imported} trades. LISTO.")

if __name__ == "__main__":
    clean_reimport()
