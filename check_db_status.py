
import sqlite3
import os

db_path = "PST_Core/data/pst_trading.db"
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Ver todos los nombres de estrategias registrados
    cursor.execute("SELECT DISTINCT strategy_name FROM symbol_strategies")
    names = [row[0] for row in cursor.fetchall()]
    print(f"Estrategias encontradas: {names}")
    
    # Ver el estado de la estrategia Mean Reversion para un par de símbolos
    cursor.execute("SELECT symbol, strategy_name, is_active FROM symbol_strategies WHERE strategy_name LIKE '%Mean%' LIMIT 5")
    rows = cursor.fetchall()
    print(f"Muestra de estados Mean Reversion: {rows}")
    
    conn.close()
else:
    print(f"ERROR: DB no encontrada en {db_path}")
