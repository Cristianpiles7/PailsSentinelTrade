import MetaTrader5 as mt5
import pandas as pd
import time
from datetime import datetime, timedelta

def analyze_mt5_history():
    if not mt5.initialize():
        print("Error al inicializar MT5")
        return

    # Historial de los últimos 7 días
    now = datetime.now()
    start = now - timedelta(days=7)
    
    deals = mt5.history_deals_get(start, now)
    if not deals:
        print("No se encontraron deals en los últimos 7 días.")
        mt5.shutdown()
        return

    df = pd.DataFrame(list(deals), columns=deals[0]._asdict().keys())
    # Quedarnos solo con ENTRY_OUT o ENTRY_INOUT (cierres)
    df = df[df['entry'].isin([1, 2])]
    
    if df.empty:
        print("No hay trades CERRADOS en el historial de MT5 de los últimos 7 días.")
        mt5.shutdown()
        return

    # Calcular beneficio neto (profit + swap + commission)
    df['net_profit'] = df['profit'] + df['swap'] + df['commission']
    
    print("\n--- MT5 HISTORY ANALYSIS (LAST 7 DAYS) ---")
    total_trades = len(df)
    wins = df[df['net_profit'] > 0]
    losses = df[df['net_profit'] <= 0]
    
    win_rate = (len(wins) / total_trades) * 100 if total_trades > 0 else 0
    avg_win = wins['net_profit'].mean() if not wins.empty else 0
    avg_loss = losses['net_profit'].mean() if not losses.empty else 0
    
    print(f"Total Trades: {total_trades}")
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Avg Win: {avg_win:.2f}")
    print(f"Avg Loss: {avg_loss:.2f}")
    
    print("\n--- PERFORMANCE BY SYMBOL (MT5) ---")
    sym_stats = df.groupby('symbol')['net_profit'].agg(['count', 'sum', 'mean']).sort_values(by='sum', ascending=False)
    print(sym_stats)

    mt5.shutdown()

if __name__ == "__main__":
    analyze_mt5_history()
