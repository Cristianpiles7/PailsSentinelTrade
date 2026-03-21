import sqlite3
import os
import MetaTrader5 as mt5
import time
from datetime import datetime

# Ruta REAL de la DB del bot
DB_PATH = r"C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PST_Core\data\pst_trading.db"

def check_and_fix():
    if not os.path.exists(DB_PATH):
        print(f"DB no encontrada en: {DB_PATH}")
        return
    
    size_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
    print(f"DB encontrada: {DB_PATH}")
    print(f"Tamanyo: {size_mb:.2f} MB")
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Ver estado actual de XAU
    print("\n=== ESTADO ACTUAL XAUUSD en DB ===")
    cur.execute("SELECT id, profit, ticket FROM trades WHERE symbol LIKE '%XAU%'")
    rows = cur.fetchall()
    total = sum(r[1] for r in rows if r[1])
    print(f"Registros: {len(rows)}, Total: {total:.2f}")
    for r in rows:
        print(f"  ID:{r[0]} | Profit:{r[1]:.2f} | Ticket:{r[2]}")
    
    # PURGAR y REIMPORTAR
    print("\nPurgando ALL trades...")
    cur.execute("DELETE FROM trades")
    conn.commit()
    
    if not mt5.initialize():
        print("No se puede conectar a MT5")
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
    
    print(f"Deals MT5: {len(deals)}")
    
    imported = 0
    seen_tickets = set()
    for d in deals:
        if d.entry in [1, 2] and d.ticket not in seen_tickets:
            seen_tickets.add(d.ticket)
            total_pnl = d.profit + d.swap + d.commission
            trade_type = "BUY" if d.type == 1 else "SELL"
            time_str = datetime.fromtimestamp(d.time).strftime("%Y-%m-%d %H:%M:%S")
            
            cur.execute("""
                INSERT INTO trades (symbol, type, volume, price_in, price_out, sl, tp, profit, time_in, time_out, regime_at_entry, strategy_name, ticket, is_partial_closed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d.symbol, trade_type, d.volume, 0.0, d.price, 0.0, 0.0,
                float(total_pnl), time_str, time_str, "AUTO_SYNC", "AUTO_SYNC",
                d.ticket, 0
            ))
            imported += 1
    
    conn.commit()
    
    # Verificar XAU final
    print("\n=== XAU TRAS REIMPORT ===")
    cur.execute("SELECT profit, ticket FROM trades WHERE symbol LIKE '%XAU%'")
    rows_xau = cur.fetchall()
    total_xau = sum(r[0] for r in rows_xau if r[0])
    for r in rows_xau:
        print(f"  Profit:{r[0]:.2f} | Ticket:{r[1]}")
    print(f"Total XAU: {total_xau:.2f}")
    
    conn.close()
    mt5.shutdown()
    print(f"\nTotal importados: {imported}. Listo.")

if __name__ == "__main__":
    check_and_fix()
