import sqlite3
import os
import sys

def full_reset():
    db_path = 'PST_Core/data/pst_trading.db'
    if not os.path.exists(db_path):
        print(f"❌ No se encontró la base de datos en {db_path}")
        return

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        print("🧹 Iniciando limpieza profunda de la base de datos...")

        # 1. Eliminar Bloqueos de Seguridad (Drawdown/Daily Lock)
        cursor.execute("DELETE FROM bot_config WHERE key LIKE 'daily_lock_%'")
        cursor.execute("DELETE FROM bot_config WHERE key LIKE 'LOCK_DRAWDOWN_%'")
        print("✅ Bloqueos de Drawdown eliminados.")

        # 2. Eliminar Historial de Trades (Para resetear el cálculo de pérdida diaria)
        cursor.execute("DELETE FROM trades")
        cursor.execute("DELETE FROM signal_logs")
        cursor.execute("DELETE FROM regime_history")
        cursor.execute("DELETE FROM trade_context")
        print("✅ Historial de operaciones y señales eliminado.")

        # 3. Limpiar secuencias
        cursor.execute("DELETE FROM sqlite_sequence WHERE name IN ('trades', 'signal_logs', 'regime_history', 'trade_context')")
        
        # 4. Optimizar espacio
        conn.commit()
        cursor.execute("VACUUM")
        conn.close()

        print("\n✨ RESET COMPLETADO. El sistema ya no tiene bloqueos activos.")
        print("🚀 Puedes reiniciar PST_MASTER.py ahora.")

    except Exception as e:
        print(f"❌ Error durante el reset: {e}")

if __name__ == "__main__":
    full_reset()
