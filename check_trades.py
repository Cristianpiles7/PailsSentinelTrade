import sqlite3
import os
from datetime import datetime

def check_db():
    db_path = r"C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PST_Core\data\pst_trading.db"
    if not os.path.exists(db_path):
        print("DB no encontrada")
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"--- TRADES CERRADOS HOY ({today}) ---")
    cur.execute("SELECT symbol, profit, time_out FROM trades WHERE time_out LIKE ?", (f"{today}%",))
    rows = cur.fetchall()
    if rows:
        for r in rows:
            print(f"Sym: {r[0]} | Profit: {r[1]} | Time: {r[2]}")
    else:
        print("No se encontraron cierres hoy en la DB.")

    print("\n--- ÚLTIMOS 5 TRADES EN TOTAL ---")
    cur.execute("SELECT symbol, profit, time_out FROM trades ORDER BY time_out DESC LIMIT 5")
    rows = cur.fetchall()
    for r in rows:
        print(f"Sym: {r[0]} | Profit: {r[1]} | Time: {r[2]}")

    conn.close()

if __name__ == "__main__":
    check_db()
