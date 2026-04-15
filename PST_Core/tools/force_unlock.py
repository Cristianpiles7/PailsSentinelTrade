import sqlite3
import os

db_path = 'PST_Core/data/pst_trading.db'

if os.path.exists(db_path):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM config WHERE key LIKE 'daily_lock_%'")
        conn.commit()
        conn.close()
        print("OK: BLOQUEO DIARIO ELIMINADO")
    except Exception as e:
        print(f"ERROR: {e}")
else:
    print(f"NOT FOUND: {db_path}")
