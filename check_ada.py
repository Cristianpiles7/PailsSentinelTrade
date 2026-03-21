import sqlite3

db = sqlite3.connect(r"C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PST_Core\data\pst_trading.db")
db.row_factory = sqlite3.Row
cur = db.cursor()

print("=== Trades de ADA en DB ===")
cur.execute("SELECT id, symbol, type, profit, price_in, price_out, time_in, time_out, ticket FROM trades WHERE symbol LIKE '%ADA%' ORDER BY id DESC LIMIT 10")
rows = cur.fetchall()
for r in rows:
    print(f"ID:{r['id']} | {r['symbol']} | {r['type']} | profit:{r['profit']} | price_out:{r['price_out']} | time_out:{r['time_out']} | ticket:{r['ticket']}")

print()
print("=== Symbols en DB (is_active=1) ===")
cur.execute("SELECT symbol, is_active FROM symbols WHERE is_active=1")
for r in cur.fetchall():
    print(f"  {r['symbol']}")

print()
print("=== get_today_profit_by_symbol (simulado) ===")
from datetime import datetime, time
today_str = datetime.combine(datetime.now().date(), time.min).strftime('%Y-%m-%d %H:%M:%S')
print(f"today_start: {today_str}")
cur.execute("SELECT symbol, SUM(profit) as tot FROM trades WHERE price_out > 0 AND time_out >= ? GROUP BY symbol", (today_str,))
for r in cur.fetchall():
    print(f"  {r['symbol']}: {r['tot']:.2f}")

db.close()
