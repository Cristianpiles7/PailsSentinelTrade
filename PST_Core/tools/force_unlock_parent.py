import sqlite3
import os
from datetime import datetime

# La ruta real que usa el bot segun los logs
db_path = r'C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PST_Core\data\pst_trading.db'

# Intentar detectar si la ruta es relativa desde el workspace anidado
if not os.path.exists(db_path):
    db_path = os.path.abspath(os.path.join('..', 'PST_Core', 'data', 'pst_trading.db'))

print(f"Buscando DB en: {db_path}")

if os.path.exists(db_path):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # El nombre de la tabla es 'bot_config' segun models/database.py
        today_str = datetime.now().strftime('%Y-%m-%d')
        lock_key = f"daily_lock_{today_str}"
        
        # 1. Eliminar el bloqueo por fecha
        cursor.execute("DELETE FROM bot_config WHERE key = ?", (lock_key,))
        
        # 2. Resetear el flag de drawdown diario
        cursor.execute("UPDATE bot_config SET value = '0' WHERE key = 'daily_drawdown_locked'")
        
        conn.commit()
        conn.close()
        print("OK: Bloqueos eliminados en la tabla 'bot_config'.")
    except Exception as e:
        print(f"ERROR: {e}")
else:
    print(f"NOT FOUND: {db_path}")
