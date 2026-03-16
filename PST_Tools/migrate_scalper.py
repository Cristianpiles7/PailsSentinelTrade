import sqlite3
import os

db_path = "PST_Core/data/pst_trading.db"

# Categoría CORE: 1 pos max
# Categoría SCALPING: 1 pos max
TARGET_SYMBOLS = ["BTCUSD", "ETHUSD", "US500.cash", "XAUUSD", "EURUSD", "NAS100"]

def migrate_scalper():
    if not os.path.exists(db_path):
        print(f"❌ DB no encontrada en {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("--- REGISTRANDO PST-SCALPER-PRO EN DB ---")
    for sym in TARGET_SYMBOLS:
        # 1. Verificar si el símbolo existe en symbols_config
        cursor.execute("SELECT symbol FROM symbols_config WHERE symbol = ?", (sym,))
        if not cursor.fetchone():
            print(f"⏩ Saltando {sym} (No configurado en el bot)")
            continue

        # 2. Insertar o actualizar en symbol_strategies
        # Usamos risk_value = 0.15 (conservador) para Scalper
        cursor.execute("""
            INSERT INTO symbol_strategies (symbol, strategy_name, is_active, risk_mode, risk_value, tp_mult, sl_mult, min_rr)
            VALUES (?, 'PST-Scalper-Pro', 1, 'PCT', 0.15, 2.0, 1.5, 1.0)
            ON CONFLICT(symbol, strategy_name) DO UPDATE SET 
            is_active = 1,
            risk_value = 0.15,
            tp_mult = 2.0,
            sl_mult = 1.5,
            min_rr = 1.0
        """, (sym,))
        print(f"✅ Scalper habilitado para {sym}")

    conn.commit()
    conn.close()
    print("\n✅ Migración de Scalper completada.")

if __name__ == "__main__":
    migrate_scalper()
