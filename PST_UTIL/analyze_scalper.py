import sqlite3
import os
import sys
from datetime import datetime, timedelta

# Añadir raíz para imports de config
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config import DB_PATH, setup_paths
setup_paths()

def analyze_scalper():
    print("="*50)
    print("📊 PST SCALPER PRO - PERFORMANCE AUDIT")
    print(f"📂 DB: {DB_PATH}")
    print("="*50)

    if not os.path.exists(DB_PATH):
        print(f"❌ Error: No se encuentra la base de datos en {DB_PATH}")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # 1. Resumen General
        query = """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN profit <= 0 THEN 1 ELSE 0 END) as losses,
                SUM(profit) as net_profit,
                AVG(profit) as avg_profit
            FROM trades 
            WHERE (strategy_name LIKE 'PST-Scalper-Pro%' OR strategy_name LIKE 'Scalping Pro%') 
            AND price_out > 0
        """
        cursor.execute(query)
        res = cursor.fetchone()
        
        if not res or res['total'] == 0:
            print("⚠️ No hay trades cerrados de Scalper Pro en el historial.")
            return

        total = res['total']
        win_rate = (res['wins'] / total) * 100
        print(f"📈 Trades Totales: {total}")
        print(f"✅ Victorias:      {res['wins']} ({win_rate:.1f}%)")
        print(f"❌ Derrotas:       {res['losses']}")
        print(f"💰 PnL Neto:       {res['net_profit']:.2f}€")
        print(f"🎯 PnL Medio:      {res['avg_profit']:.2f}€")
        print("-" * 50)

        # 2. Últimos 5 Trades Detallados
        print("🔍 ÚLTIMOS 5 TRADES:")
        cursor.execute("""
            SELECT symbol, type, volume, price_in, price_out, profit, time_in, notes
            FROM trades 
            WHERE (strategy_name LIKE 'PST-Scalper-Pro%' OR strategy_name LIKE 'Scalping Pro%') 
            AND price_out > 0
            ORDER BY time_out DESC LIMIT 5
        """)
        for row in cursor.fetchall():
            res_icon = "🟢" if row['profit'] > 0 else "🔴"
            print(f"{res_icon} {row['symbol']} | {row['type']} | Vol: {row['volume']} | PnL: {row['profit']:.2f}€")
            print(f"   Entrada: {row['time_in']} | Precio: {row['price_in']} -> {row['price_out']}")
            if row['notes']:
                print(f"   📝 Notas: {row['notes']}")
            print("-" * 30)

        # 3. Eficiencia de filtros (Bloqueos de hoy)
        today = datetime.now().strftime('%Y-%m-%d')
        print(f"🛑 BLOQUEOS/FILTROS DETECTADOS HOY ({today}):")
        cursor.execute("""
            SELECT message, timestamp 
            FROM system_logs 
            WHERE (message LIKE '%BLOQUEO%' OR message LIKE '%GATED%') 
            AND timestamp LIKE ?
            ORDER BY timestamp DESC LIMIT 5
        """, (f"{today}%",))
        logs = cursor.fetchall()
        if logs:
            for l in logs:
                print(f"   [{l['timestamp']}] {l['message']}")
        else:
            print("   ✅ No hay bloqueos críticos registrados hoy.")

        conn.close()
    except Exception as e:
        print(f"❌ Error ejecutando análisis: {e}")

if __name__ == "__main__":
    analyze_scalper()
