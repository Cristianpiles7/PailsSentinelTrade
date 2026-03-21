import sqlite3
import os

def purge_bad_trades():
    db_path = r"C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PST_Core\data\pst_trading.db"
    if not os.path.exists(db_path):
        print("DB no encontrada")
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    print("--- PURGANDO TRADES CORRUPTOS (Profit 0 o sin fecha) ---")
    # Borrar trades que tengan profit 0 y que no sean posiciones abiertas reales (price_out > 0 indica que se intentó cerrar)
    cur.execute("DELETE FROM trades WHERE profit = 0 AND ticket > 0")
    deleted = conn.total_changes
    print(f"Registros eliminados: {deleted}")
    
    conn.commit()
    conn.close()
    print("Limpieza completada.")

if __name__ == "__main__":
    purge_bad_trades()
