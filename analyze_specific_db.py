import sqlite3
import json
import os

DB_PATH = r"C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PST_Core\data\pst_trading.db"

def analyze_signals():
    if not os.path.exists(DB_PATH):
        print(f"Error: {DB_PATH} no existe")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Ver columnas
    cur.execute('PRAGMA table_info(signal_logs)')
    cols = [c[1] for c in cur.fetchall()]
    
    print(f"=== ÚLTIMAS 20 SEÑALES DE SCALPER EN {DB_PATH} ===")
    
    # Intentar buscar señales de Scalper
    query = "SELECT symbol, signal, score, metadata, time FROM signal_logs WHERE strategy LIKE '%Scalper%' ORDER BY id DESC LIMIT 20"
    cur.execute(query)
    
    for r in cur.fetchall():
        symbol, sig, score, metadata, timestamp = r
        print(f"[{timestamp}] {symbol} | {sig} | Score: {score}")
        try:
            meta = json.loads(metadata)
            factors = meta.get('factors_detailed', [])
            for f in factors:
                if f.get('score', 0) != 0:
                    print(f"  - {f.get('k')}: {f.get('v')} ({f.get('score')} pts)")
        except:
            pass
    conn.close()

if __name__ == "__main__":
    analyze_signals()
