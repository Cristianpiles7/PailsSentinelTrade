import sqlite3
import os

db_path = "PST_Core/data/trading_v6.db"

if not os.path.exists(db_path):
    print(f"❌ No se encontró la base de datos en {db_path}")
else:
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        print("🧹 Reseteando base de datos...")
        tables = ['trades', 'signal_logs', 'regime_history', 'trade_context']
        
        for table in tables:
            cursor.execute(f"DELETE FROM {table}")
            print(f"✅ Tabla {table} limpiada.")
            
        cursor.execute("DELETE FROM sqlite_sequence WHERE name IN ('trades', 'signal_logs', 'regime_history', 'trade_context')")
        conn.commit()
        conn.close()
        print("\n✨ ¡Base de datos reseteada con éxito!")
        print("Ahora puedes volver a arrancar PST_MASTER.py")
    except Exception as e:
        print(f"❌ Error al resetear: {e}")
