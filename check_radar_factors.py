import sqlite3
import json
import os
import sys

# Asegurar salida UTF-8
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

db_path = r"c:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PailsSentinelTrade\PST_Core\data\pst_trading.db"

def query_radar():
    if not os.path.exists(db_path):
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM symbol_radar WHERE symbol = 'EURUSD'")
    row = cursor.fetchone()
    if row:
        data = dict(row)
        print(f"Symbol: {data['symbol']}")
        print(f"Score: {data['score']}")
        print(f"Signal: {data['signal_direction']}")
        print(f"Last Update: {data['last_update']}")
        
        if data['factors_json']:
            factors = json.loads(data['factors_json'])
            print("\n--- FACTORS ---")
            for strat, info in factors.items():
                print(f"\nStrategy: {strat} (Score: {info.get('score', 0)})")
                for f in info.get('factors', []):
                    # Solo mostrar factores con score != 0 o que parezcan importantes
                    if f.get('score', 0) != 0 or f.get('k') in ['BLOQUEO RSI', 'Aviso', 'NOTICIAS', 'Coste Spread']:
                        print(f"  [{f.get('k')}] {f.get('v')} | Score: {f.get('score')} | {f.get('desc', '')}")
    
    conn.close()

if __name__ == "__main__":
    query_radar()
