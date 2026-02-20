import sqlite3
import pandas as pd
from datetime import datetime

def analyze():
    db_path = "PST_Core/data/pst_trading.db"
    conn = sqlite3.connect(db_path)
    
    today = "2026-02-18"
    print(f"--- Detailed Analysis for {today} ---")
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 2000)

    # 1. Trades of today (including open ones)
    query_trades = f"SELECT * FROM trades WHERE time_in LIKE '{today}%' OR time_out LIKE '{today}%'"
    df_trades = pd.read_sql_query(query_trades, conn)
    
    if df_trades.empty:
        print("No trades found for today.")
    else:
        print("\n[ACTIVE/CLOSED TRADES TODAY]")
        df_trades['status'] = df_trades['price_out'].apply(lambda x: 'OPEN' if x == 0 else 'CLOSED')
        cols = ['symbol', 'type', 'status', 'profit', 'time_in', 'time_out', 'strategy_name', 'price_in', 'price_out']
        print(df_trades[cols].to_string())

    # 2. Focus on EU50 specifically (Signals)
    print(f"\n[EU50.cash SIGNAL LOGS TODAY]")
    query_eu50 = f"SELECT * FROM signal_logs WHERE symbol = 'EU50.cash' AND timestamp LIKE '{today}%'"
    df_eu50 = pd.read_sql_query(query_eu50, conn)
    if df_eu50.empty:
        print("No signals registered for EU50 today.")
    else:
        print(df_eu50.groupby('signal_type').size().reset_index(name='count'))
        print("\n[EU50 DETAILED SIGNALS (last 10)]")
        print(df_eu50.tail(10).to_string())

    # 3. Blocked signals Summary
    query_signals = f"SELECT * FROM signal_logs WHERE timestamp LIKE '{today}%' AND signal_type LIKE 'BLOCKED%'"
    df_signals = pd.read_sql_query(query_signals, conn)
    if not df_signals.empty:
        print("\n[BLOCKED SIGNALS SUMMARY]")
        sig_summary = df_signals.groupby(['symbol', 'signal_type']).size().reset_index(name='count')
        print(sig_summary.sort_values('count', ascending=False))

    # 3. Overall stats
    total_profit = df_trades['profit'].sum() if not df_trades.empty else 0
    print(f"\nTOTAL PROFIT TODAY: {total_profit:.2f}")

    conn.close()

if __name__ == "__main__":
    analyze()
