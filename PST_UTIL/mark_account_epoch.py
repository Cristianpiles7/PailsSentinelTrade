import sqlite3
import os
import sys
import argparse

# Añadir raíz para imports de config
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config import DB_PATH, setup_paths
setup_paths()

# Corte por id y no por fecha: los time_in/time_out de la BBDD van con desfase de reloj
# respecto al local, y el id siempre crece en orden de inserción.
EPOCH_KEY = "account_epoch_first_trade_id"
LOGIN_KEY = "account_epoch_login"


def mark_epoch(db_path, from_id=None, login=None, clear=False):
    if not os.path.exists(db_path):
        print(f"❌ Error: No se encuentra la base de datos en {db_path}")
        return

    print(f"📂 DB: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        if clear:
            cursor.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, '0')", (EPOCH_KEY,))
            cursor.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, '')", (LOGIN_KEY,))
            conn.commit()
            print("✅ Época eliminada: las métricas vuelven a contar todo el histórico.")
            return

        if from_id is None:
            cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM trades")
            from_id = cursor.fetchone()[0]

        cursor.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)", (EPOCH_KEY, str(from_id)))
        if login is not None:
            cursor.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)", (LOGIN_KEY, str(login)))
        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM trades WHERE id < ?", (from_id,))
        previas = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM trades WHERE id >= ?", (from_id,))
        actuales = cursor.fetchone()[0]
        print(f"✅ Época fijada en trade id >= {from_id} (login {login if login is not None else 'sin indicar'}).")
        print(f"   Operaciones de cuentas anteriores (excluidas): {previas}")
        print(f"   Operaciones de la cuenta actual: {actuales}")
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Marca el inicio de la cuenta MT5 actual en la BBDD.")
    parser.add_argument("--from-id", type=int, help="Primer trade id de la cuenta actual (por defecto, MAX(id)+1)")
    parser.add_argument("--login", type=int, help="Login MT5 de la cuenta actual (solo informativo)")
    parser.add_argument("--clear", action="store_true", help="Quitar la época y contar todo el histórico")
    parser.add_argument("--db", default=DB_PATH, help="Ruta de la BBDD (por defecto, la que resuelve PST_UTIL/config.py)")
    args = parser.parse_args()
    mark_epoch(args.db, from_id=args.from_id, login=args.login, clear=args.clear)
