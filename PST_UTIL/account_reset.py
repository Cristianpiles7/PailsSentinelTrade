import sqlite3
import os
import sys

# Añadir raíz para imports de config
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config import DB_PATH, setup_paths
setup_paths()

def reset_account():
    print("="*50)
    print("🧹 PST ACCOUNT RESET - LIMPIEZA DE HISTORIAL")
    print(f"📂 DB: {DB_PATH}")
    print("="*50)

    if not os.path.exists(DB_PATH):
        print(f"❌ Error: No se encuentra la base de datos en {DB_PATH}")
        return

    print("⚠️  Esta acción ELIMINARÁ todos los trades y logs, pero MANTENDRÁ tus símbolos y configuraciones.")
    confirm = input("¿Estás seguro? (s/n): ")
    if confirm.lower() != 's':
        print("❌ Operación cancelada.")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Tablas a limpiar completamente
        tables_to_clear = [
            "trades",
            "system_logs",
            "signal_logs",
            "trade_context",
            "ai_oracle_state",
            "symbol_radar",
            "regime_history"
        ]

        for table in tables_to_clear:
            cursor.execute(f"DELETE FROM {table}")
            print(f"✅ Tabla '{table}' limpiada.")

        # Resetear bloqueos de seguridad
        cursor.execute("UPDATE bot_config SET value = '0' WHERE key = 'daily_drawdown_locked'")
        print("✅ Bloqueo de Drawdown Diario reseteado.")

        conn.commit()
        conn.close()
        print("\n✨ Reseteo completado con éxito. El bot está listo para una nueva cuenta MT5.")
        print("Tus símbolos, niveles y configuraciones de estrategia se han preservado.")

    except Exception as e:
        print(f"❌ Error durante el reseteo: {e}")

if __name__ == "__main__":
    reset_account()
