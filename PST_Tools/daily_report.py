import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import os
import sys

# Añadir el raíz al path para imports si fuera necesario
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def generate_report():
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "PST_Core/data/pst_trading.db")
    if not os.path.exists(db_path):
        print(f"❌ Base de datos no encontrada en {db_path}")
        return

    conn = sqlite3.connect(db_path)
    today = datetime.now().strftime("%Y-%m-%d")
    
    print("\n" + "="*60)
    print(f"💎 PAILS SENTINEL TRADE - REPORTE DIARIO ({today}) 💎")
    print("="*60)

    # 1. Resumen de Operaciones
    query_trades = f"SELECT * FROM trades WHERE time_in LIKE '{today}%' OR time_out LIKE '{today}%'"
    df_trades = pd.read_sql_query(query_trades, conn)
    
    if df_trades.empty:
        print("\n📭 No hay operaciones registradas hoy.")
    else:
        print("\n📈 [ESTADÍSTICAS DE TRADING]")
        df_trades['status'] = df_trades['price_out'].apply(lambda x: 'ABIERTA' if x == 0 else 'CERRADA')
        
        closed_trades = df_trades[df_trades['status'] == 'CERRADA']
        total_profit = closed_trades['profit'].sum()
        wins = len(closed_trades[closed_trades['profit'] > 0])
        losses = len(closed_trades[closed_trades['profit'] < 0])
        win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
        
        print(f"  - Beneficio Cerrado: {total_profit:+.2f}€")
        print(f"  - Operaciones: {len(closed_trades)} cerradas, {len(df_trades[df_trades['status']=='ABIERTA'])} abiertas")
        print(f"  - Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)")
        
        print("\n📝 [LISTADO DE TRADES]")
        cols = ['symbol', 'type', 'status', 'profit', 'strategy_name', 'time_in']
        print(df_trades[cols].to_string(index=False))

    # 2. Análisis de Señales Bloqueadas
    print("\n" + "-"*60)
    print("🧊 [SEÑALES BLOQUEADAS / COOLDOWNS]")
    query_signals = f"SELECT symbol, signal_type, COUNT(*) as count FROM signal_logs WHERE timestamp LIKE '{today}%' AND signal_type LIKE 'BLOCKED%' GROUP BY symbol, signal_type ORDER BY count DESC LIMIT 10"
    df_signals = pd.read_sql_query(query_signals, conn)
    
    if df_signals.empty:
        print("  - No se han bloqueado señales hoy.")
    else:
        print(df_signals.to_string(index=False))

    # 3. Estado de Protección del Portafolio
    print("\n" + "-"*60)
    print("🛡️ [SISTEMA DE PROTECCIÓN]")
    # Intentar obtener info de bot_config
    try:
        query_config = "SELECT key, value FROM bot_config WHERE key LIKE '%locked%'"
        df_config = pd.read_sql_query(query_config, conn)
        if not df_config.empty:
            for _, row in df_config.iterrows():
                print(f"  - {row['key']}: {row['value']}")
        else:
            print("  - No hay bloqueos activos por Drawdown.")
    except:
        print("  - Info de protección no disponible.")

    conn.close()
    print("\n" + "="*60 + "\n")

if __name__ == "__main__":
    generate_report()
