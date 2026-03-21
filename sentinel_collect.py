import MetaTrader5 as mt5
import sqlite3
import time
from datetime import datetime, time as dtime
import os

# Ruta absoluta verificada
DB_PATH = r"C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PST_Core\data\pst_trading.db"

def run_sentinel_data():
    if not mt5.initialize():
        print("ERROR: No se pudo inicializar MT5")
        return

    # 1. Información de la cuenta
    acc = mt5.account_info()
    if not acc:
        print("ERROR: No se pudo obtener información de la cuenta")
        mt5.shutdown()
        return

    print(f"--- ACCOUNT INFO ---")
    print(f"Balance: {acc.balance}")
    print(f"Equity: {acc.equity}")
    print(f"Profit: {acc.profit}")
    print(f"Margin: {acc.margin}")
    print(f"Margin Level: {acc.margin_level}")

    # 2. Sincronizar historial (deals de hoy)
    now = datetime.now()
    start_of_day = datetime.combine(now.date(), dtime.min)
    
    # 3. Consulta de beneficios en DB
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        
        # Hoy
        today_str = start_of_day.strftime('%Y-%m-%d %H:%M:%S')
        cur.execute("SELECT SUM(profit) FROM trades WHERE time_out >= ?", (today_str,))
        today_profit = cur.fetchone()[0] or 0.0
        
        # Total
        cur.execute("SELECT SUM(profit) FROM trades WHERE price_out > 0")
        total_profit = cur.fetchone()[0] or 0.0
        
        print(f"\n--- DATABASE PROFIT ---")
        print(f"Realized Today: {today_profit:.2f}")
        print(f"Realized Total: {total_profit:.2f}")
        conn.close()
    except Exception as e:
        print(f"ERROR DB en {DB_PATH}: {e}")

    # 4. Posiciones abiertas
    positions = mt5.positions_get()
    print(f"\n--- OPEN POSITIONS ({len(positions) if positions else 0}) ---")
    if positions:
        for p in positions:
            print(f"Symbol: {p.symbol} | Type: {'BUY' if p.type == 0 else 'SELL'} | Vol: {p.volume} | Profit: {p.profit}")

    mt5.shutdown()

if __name__ == "__main__":
    run_sentinel_data()
