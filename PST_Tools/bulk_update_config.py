import sqlite3
import os
import sys

# Añadir el directorio raíz al path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "PST_Core/data/pst_trading.db")

def bulk_enable_protections():
    print(f"🔍 Conectando a la DB: {DB_PATH}")
    if not os.path.exists(DB_PATH):
        print("❌ Error: No se encuentra la base de datos.")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Actualizar todas las estrategias existentes
        print("⚙️ Activando BE y TS para todas las estrategias existentes...")
        cursor.execute("UPDATE symbol_strategies SET use_trailing = 1, use_breakeven = 1")
        
        # También asegurar que be_mult y ts_mult tengan valores por defecto si son NULL
        cursor.execute("UPDATE symbol_strategies SET be_mult = 2.0 WHERE be_mult IS NULL")
        cursor.execute("UPDATE symbol_strategies SET ts_mult = 2.5 WHERE ts_mult IS NULL")
        
        conn.commit()
        changes = conn.total_changes
        conn.close()
        
        print(f"✅ ¡Éxito! Se han actualizado {changes} filas en la tabla symbol_strategies.")
        print("🚀 Ahora todos los símbolos tienen Breakeven y Trailing Stop activos.")

    except Exception as e:
        print(f"❌ Error durante la actualización: {e}")

if __name__ == "__main__":
    bulk_enable_protections()
