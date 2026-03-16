
import sqlite3
import os

DB_PATH = "PST_Core/data/pst_trading.db"

def fix_db_defaults():
    if not os.path.exists(DB_PATH):
        print(f"Error: No se encuentra la DB en {DB_PATH}")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 1. Forzar BE y TS en TODAS las estrategias de TODOS los símbolos
        print("Sincronizando: Activando use_breakeven y use_trailing en todos los registros...")
        cursor.execute("UPDATE symbol_strategies SET use_breakeven = 1, use_trailing = 1")
        
        # 2. Forzar riesgo de 5€ para cualquier estrategia que sea "Scalper"
        print("Sincronizando: Ajustando riesgo a 5 euros para estrategias Scalper...")
        cursor.execute("""
            UPDATE symbol_strategies 
            SET risk_value = 5.0, risk_mode = 'MONEY' 
            WHERE strategy_name LIKE '%Scalper%'
        """)

        # 3. Asegurar que las estrategias activas por defecto tengan 25 si no tienen nada
        cursor.execute("""
            UPDATE symbol_strategies 
            SET risk_value = 25.0 
            WHERE strategy_name = 'PST-EMA-Flow' AND (risk_value IS NULL OR risk_value = 0)
        """)

        conn.commit()
        print(f"Exito: Cambios aplicados: {conn.total_changes} filas afectadas.")
        conn.close()

    except Exception as e:
        print(f"Error actualizando DB: {e}")

if __name__ == "__main__":
    fix_db_defaults()
