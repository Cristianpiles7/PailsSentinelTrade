import sqlite3
import os
import sys

# Añadir raíz al path para importar utilidades
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PST_Core.utils.tech_utils import get_asset_class
from PST_Core.config import TP_ATR_BY_CLASS

db_path = "PST_Core/data/pst_trading.db"

def bulk_update_tp():
    if not os.path.exists(db_path):
        print(f"❌ DB no encontrada en {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. Actualizar symbols_config (Parámetros globales por símbolo)
    cursor.execute("SELECT symbol FROM symbols_config")
    symbols = cursor.fetchall()

    print("--- ACTUALIZANDO SYMBOLS_CONFIG ---")
    for (sym,) in symbols:
        a_class = get_asset_class(sym)
        new_tp = TP_ATR_BY_CLASS.get(a_class, 6.0)
        print(f"Updating {sym} ({a_class}) -> TP: {new_tp}")
        cursor.execute("UPDATE symbols_config SET tp_mult = ? WHERE symbol = ?", (new_tp, sym))

    # 2. Actualizar symbol_strategies (Parámetros por estrategia activa)
    cursor.execute("SELECT symbol, strategy_name FROM symbol_strategies")
    strats = cursor.fetchall()

    print("\n--- ACTUALIZANDO SYMBOL_STRATEGIES ---")
    for sym, strat in strats:
        a_class = get_asset_class(sym)
        new_tp = TP_ATR_BY_CLASS.get(a_class, 6.0)
        print(f"Updating {sym} [{strat}] -> TP: {new_tp}")
        cursor.execute("UPDATE symbol_strategies SET tp_mult = ? WHERE symbol = ? AND strategy_name = ?", (new_tp, sym, strat))

    conn.commit()
    conn.close()
    print("\n✅ Sincronización de TP completada.")

if __name__ == "__main__":
    bulk_update_tp()
