import sqlite3
import os

def inspect_xau():
    db_path = r"C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PST_Core\data\pst_trading.db"
    if not os.path.exists(db_path):
        print("DB no encontrada")
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    print("=== TODOS los trades de XAUUSD en la DB ===")
    cur.execute("SELECT id, symbol, type, profit, time_in, time_out, price_out, ticket, strategy_name FROM trades WHERE symbol LIKE '%XAU%' ORDER BY id")
    rows = cur.fetchall()
    total = 0
    for r in rows:
        print(f"ID:{r[0]} | {r[1]} | {r[2]} | Profit:{r[3]} | In:{r[4]} | Out:{r[5]} | POut:{r[6]} | Ticket:{r[7]} | Strat:{r[8]}")
        if r[3]:
            total += r[3]
    print(f"\nTotal P&L XAUUSD en DB: {total:.2f}")
    print(f"Num registros: {len(rows)}")
    
    print("\n=== Hoy (2026-03-20) ===")
    cur.execute("SELECT id, symbol, profit, time_out, ticket FROM trades WHERE symbol LIKE '%XAU%' AND time_out >= '2026-03-20' ORDER BY id")
    rows_today = cur.fetchall()
    today_total = 0
    for r in rows_today:
        print(f"ID:{r[0]} | {r[1]} | Profit:{r[2]} | Out:{r[3]} | Ticket:{r[4]}")
        if r[2]:
            today_total += r[2]
    print(f"Total P&L XAUUSD HOY: {today_total:.2f}")
    
    conn.close()

if __name__ == "__main__":
    inspect_xau()
