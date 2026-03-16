import asyncio
import os
import sys

# Añadir el directorio raíz al path para importar PST_Core
sys.path.append(os.getcwd())

from PST_Core.models.database import db

async def debug():
    print("--- Debugging Advanced Metrics ---")
    metrics = await db.get_advanced_metrics()
    print(f"Metrics: {metrics}")
    
    # Verificar si hay trades con nombres de estrategia
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT strategy_name, COUNT(*) as c FROM trades WHERE price_out > 0 GROUP BY strategy_name") as cursor:
            rows = await cursor.fetchall()
            print("\nTrades in DB (Grouping by Strategy):")
            for r in rows:
                print(f"  Strategy: {r['strategy_name']} | Count: {r['c']}")

if __name__ == "__main__":
    asyncio.run(debug())
