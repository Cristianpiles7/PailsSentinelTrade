import sqlite3
import json
import os
import sys
from datetime import datetime

# Asegurar salida UTF-8
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

db_path = r"c:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PailsSentinelTrade\PST_Core\data\pst_trading.db"

def query_db():
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    print("--- ULTIMOS SNAPSHOTS DEL RADAR (EURUSD) ---")
    cursor.execute("SELECT * FROM symbol_radar WHERE symbol = 'EURUSD'")
    row = cursor.fetchone()
    if row:
        print(dict(row))
    else:
        print("No radar snapshot for EURUSD")

    print("\n--- ULTIMAS SEÑALES REGISTRADAS (EURUSD) ---")
    cursor.execute("SELECT * FROM signal_logs WHERE symbol = 'EURUSD' ORDER BY id DESC LIMIT 10")
    rows = cursor.fetchall()
    for r in rows:
        print(dict(r))

    print("\n--- ULTIMOS LOGS DEL SISTEMA (Relacionados con BLOQUEO o EURUSD) ---")
    # Buscar mensajes que contengan EURUSD, BLOCKED, LIMIT, news, o noticias
    cursor.execute("SELECT * FROM system_logs WHERE message LIKE '%EURUSD%' OR message LIKE '%BLOCKED%' OR message LIKE '%LIMIT%' OR message LIKE '%news%' OR message LIKE '%noticia%' ORDER BY id DESC LIMIT 30")
    rows = cursor.fetchall()
    for r in rows:
        print(dict(r))

    print("\n--- CONFIGURACION DE ESTRATEGIAS PARA EURUSD ---")
    cursor.execute("SELECT * FROM symbol_strategies WHERE symbol = 'EURUSD'")
    rows = cursor.fetchall()
    for r in rows:
        print(dict(r))

    conn.close()

if __name__ == "__main__":
    query_db()
