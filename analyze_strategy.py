import sqlite3
import pandas as pd
import os

DB_PATHS = [
    r"C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PST_Core\data\pst_trading.db",
    r"C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\old_PST_Core\data\pst_trading.db"
]

def analyze_scalper():
    all_trades = []
    
    for path in DB_PATHS:
        if not os.path.exists(path):
            print(f"Saltando {path} (no existe)")
            continue
            
        try:
            conn = sqlite3.connect(path)
            # Buscar trades que digan Scalper en comments o notes or similar
            # O simplemente todos los trades si la cuenta era solo para eso
            query = "SELECT symbol, type, profit, price_in, price_out, time_in, time_out, notes FROM trades WHERE price_out > 0"
            df = pd.read_sql_query(query, conn)
            all_trades.append(df)
            conn.close()
            print(f"Cargados {len(df)} trades de {path}")
        except Exception as e:
            print(f"Error en {path}: {e}")

    if not all_trades:
        print("No hay trades para analizar")
        return

    df = pd.concat(all_trades)
    
    # Filtrar por scalper si es posible (asumiendo que notes o symbols nos dan pistas)
    # Si no, analizamos todo lo que hay
    
    print("\n--- MÉTRICAS GENERALES ---")
    total_trades = len(df)
    wins = df[df['profit'] > 0]
    losses = df[df['profit'] <= 0]
    
    win_rate = (len(wins) / total_trades) * 100 if total_trades > 0 else 0
    avg_win = wins['profit'].mean() if not wins.empty else 0
    avg_loss = losses['profit'].mean() if not losses.empty else 0
    expectancy = (win_rate/100 * avg_win) + ((1 - win_rate/100) * avg_loss)
    
    print(f"Total Trades: {total_trades}")
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Avg Win: {avg_win:.2f}")
    print(f"Avg Loss: {avg_loss:.2f}")
    print(f"Esperanza Matemática: {expectancy:.2f}")
    
    print("\n--- RENDIMIENTO POR SÍMBOLO ---")
    sym_stats = df.groupby('symbol')['profit'].agg(['count', 'sum', 'mean']).sort_values(by='sum', ascending=False)
    print(sym_stats)
    
    # Analizar duracion si time_in/out estan disponibles
    try:
        df['time_in'] = pd.to_datetime(df['time_in'])
        df['time_out'] = pd.to_datetime(df['time_out'])
        df['duration_min'] = (df['time_out'] - df['time_in']).dt.total_seconds() / 60
        print("\n--- DURACIÓN PROMEDIO (MIN) ---")
        print(f"Global: {df['duration_min'].mean():.2f}")
        print(df.groupby('symbol')['duration_min'].mean())
    except:
        pass

if __name__ == "__main__":
    analyze_scalper()
