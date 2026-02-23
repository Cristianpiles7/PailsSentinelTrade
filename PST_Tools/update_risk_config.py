import sqlite3
import os

db_path = "PST_Core/data/pst_trading.db"
if not os.path.exists(db_path):
    print(f"Error: DB not found at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Strategy ID for Mean Reversion is PST-Mean-Reversion (based on orchestrator mapping)
# We update it for ALL symbols in the symbol_strategies table
# 1. Actualizar todas las estrategias de todos los símbolos
cursor.execute("UPDATE symbol_strategies SET risk_mode = 'MONEY', risk_value = 10.0")
print(f"Updated {cursor.rowcount} entries in symbol_strategies")

# 2. Actualizar la configuración base de todos los símbolos
cursor.execute("UPDATE symbols_config SET risk_mode = 'MONEY', risk_value = 10.0")
print(f"Updated {cursor.rowcount} entries in symbols_config")

# 3. Verificar un par de ejemplos para estar seguro
cursor.execute("SELECT symbol, strategy_name, risk_value FROM symbol_strategies LIMIT 5")
print("Muestra symbol_strategies:", cursor.fetchall())

conn.commit()
conn.close()
