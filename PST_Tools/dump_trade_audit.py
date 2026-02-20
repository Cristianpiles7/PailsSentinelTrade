import sqlite3
import pandas as pd
from datetime import datetime

def dump_analysis():
    db_path = "PST_Core/data/pst_trading.db"
    conn = sqlite3.connect(db_path)
    
    today = "2026-02-18"
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 10000)
    pd.set_option('display.max_rows', None)

    with open("full_trade_audit_today.txt", "w", encoding='utf-8') as f:
        f.write(f"--- Full Audit for {today} ---\n\n")
        
        # 1. Trades
        query_trades = f"SELECT * FROM trades WHERE time_in LIKE '{today}%' OR time_out LIKE '{today}%'"
        df_trades = pd.read_sql_query(query_trades, conn)
        f.write("[ALL TRADES TODAY]\n")
        if not df_trades.empty:
            df_trades['status'] = df_trades['price_out'].apply(lambda x: 'OPEN' if x == 0 else 'CLOSED')
            f.write(df_trades.to_string() + "\n")
        else:
            f.write("No trades found.\n")

        # 2. All signals for EU50 and GOOG (even those not blocked)
        f.write("\n[SIGNALS AUDIT (GOOG, EU50)]\n")
        query_audit = f"SELECT * FROM signal_logs WHERE (symbol='GOOG' OR symbol='EU50.cash') AND timestamp LIKE '{today}%' ORDER BY timestamp DESC"
        df_audit = pd.read_sql_query(query_audit, conn)
        if not df_audit.empty:
            f.write(df_audit.to_string() + "\n")
        else:
            f.write("No signals found for GOOG/EU50.\n")

        # 3. Global Regime History (last 20 entries)
        f.write("\n[RECENT REGIME HISTORY]\n")
        query_regime = f"SELECT timestamp, symbol, mode, adx FROM regime_history ORDER BY id DESC LIMIT 50"
        df_regime = pd.read_sql_query(query_regime, conn)
        f.write(df_regime.to_string() + "\n")

    conn.close()
    print("Audit written to full_trade_audit_today.txt")

if __name__ == "__main__":
    dump_analysis()
