import sqlite3
import os

db_path = "PST_Core/data/pst_trading.db"

def debug():
    if not os.path.exists(db_path):
        print(f"DB not found at {db_path}")
        return
        
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 1. Schema
    print("--- Table schema for 'trades' ---")
    cursor.execute("PRAGMA table_info(trades)")
    for row in cursor.fetchall():
        print(dict(row))
        
    # 2. AUTO_SYNC data
    print("\n--- AUTO_SYNC trades ---")
    cursor.execute("SELECT id, symbol, type, time_in, profit, strategy_name FROM trades WHERE strategy_name = 'AUTO_SYNC'")
    rows = cursor.fetchall()
    if not rows:
        print("No AUTO_SYNC trades found")
    else:
        for r in rows:
            print(dict(r))
            
    # 3. All strategias
    print("\n--- Strategy counts (all trades) ---")
    cursor.execute("SELECT strategy_name, COUNT(*) as c FROM trades GROUP BY strategy_name")
    for r in cursor.fetchall():
        print(f"Strategy: {r['strategy_name']} | Count: {r['c']}")

    conn.close()

if __name__ == "__main__":
    debug()
