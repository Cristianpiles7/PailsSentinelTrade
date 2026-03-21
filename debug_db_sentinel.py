import sqlite3
import os

DB_PATH = r"C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PST_Core\data\pst_trading.db"

def debug_db():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: Archivo no existe en {DB_PATH}")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        
        print(f"--- DATABASE DEBUG ({DB_PATH}) ---")
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cur.fetchall()
        print(f"Tablas: {tables}")
        
        if ('trades',) in tables:
            cur.execute("SELECT id, symbol, profit, price_out, ticket, time_out FROM trades")
            rows = cur.fetchall()
            print(f"Num Trades: {len(rows)}")
            for r in rows:
                print(f"  ID:{r[0]} | Sym:{r[1]} | PnL:{r[2]} | Out:{r[3]} | Ticket:{r[4]} | Time:{r[5]}")
        else:
            print("ERROR: La tabla 'trades' no existe en esta DB.")
            
        conn.close()
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == "__main__":
    debug_db()
