import sqlite3
import os
import sys
from datetime import datetime

# Añadir raíz para imports de config
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config import DB_PATH, setup_paths
setup_paths()

def check_blocks():
    print("="*50)
    print("🛑 PST ENGINE - BLOCK & GATE AUDIT")
    print(f"📂 DB: {DB_PATH}")
    print("="*50)

    if not os.path.exists(DB_PATH):
        print(f"❌ Error: No se encuentra la base de datos en {DB_PATH}")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # 1. Bloqueos de hoy por Cooldown u otros motivos técnicos
        today = datetime.now().strftime('%Y-%m-%d')
        print(f"🔍 EVENTOS DE BLOQUEO HOY ({today}):")
        
        # Filtros de búsqueda (Cooldown, Histeresis, Spread, Filtros)
        patterns = ['%BLOQUEO%', '%COOLDOWN%', '%GATED%', '%SPREAD%', '%RECHAZO%', '%ERROR%']
        query = "SELECT timestamp, message, level FROM system_logs WHERE (" + " OR ".join(["message LIKE ?"] * len(patterns)) + ") AND timestamp LIKE ? ORDER BY timestamp DESC LIMIT 20"
        
        cursor.execute(query, (*patterns, f"{today}%"))
        logs = cursor.fetchall()
        
        if not logs:
            print("   ✅ No se han detectado bloqueos o errores críticos hoy.")
        else:
            for l in logs:
                level_icon = "⚠️" if l['level'] == 'WARNING' else "❌" if l['level'] == 'ERROR' else "🧊"
                print(f"   [{l['timestamp']}] {level_icon} {l['message']}")
        
        print("-" * 50)
        
        # 2. Análisis por símbolo (Procesado en Python para robustez)
        print("📊 TOP SÍMBOLOS CON AVISOS/BLOQUEOS HOY:")
        cursor.execute("SELECT message FROM system_logs WHERE timestamp LIKE ?", (f"{today}%",))
        
        all_logs = cursor.fetchall()
        symbol_counts = {}
        import re
        for row in all_logs:
            msg = row['message']
            # Buscar patrones como [EURUSD] o [US500.cash]
            match = re.search(r'\[([A-Z0-9\.\-\#]+)\]', msg)
            if match:
                sym = match.group(1)
                symbol_counts[sym] = symbol_counts.get(sym, 0) + 1
        
        sorted_syms = sorted(symbol_counts.items(), key=lambda x: x[1], reverse=True)
        for sym, count in sorted_syms[:5]:
            print(f"   {sym}: {count} avisos/bloqueos")

        conn.close()
    except Exception as e:
        print(f"❌ Error ejecutando auditoría: {e}")

if __name__ == "__main__":
    check_blocks()
