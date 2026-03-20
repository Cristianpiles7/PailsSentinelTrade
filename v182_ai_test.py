import asyncio
import MetaTrader5 as mt5
from datetime import datetime, time
import os
import sys
from dotenv import load_dotenv
import google.generativeai as genai

PROJECT_ROOT = r'C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PailsSentinelTrade'
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PST_Core.models.database import PSTDatabase

async def test():
    mt5.initialize()
    acc = mt5.account_info()
    
    # --- RUTA DE PRODUCCIÓN (Raíz BOLSA -> PailsSentinelTrade) ---
    PROD_ROOT = r'C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade'
    db_path = os.path.join(PROD_ROOT, "PST_Core", "data", "pst_trading.db")
    db = PSTDatabase(db_path)
    
    # Cargar .env de la raíz de producción
    load_dotenv(os.path.join(PROD_ROOT, '.env'))
    genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
    model = genai.GenerativeModel('gemini-3.1-flash-lite-preview')
    
    hoy_pnl = await db.get_today_profit_by_symbol()
    total_pnl = await db.get_all_time_profit_by_symbol()
    
    prompt = (
        "Actúa como un experto en gestión de capital PST v1.8.2.\n"
        f"CUENTA: Equity {acc.equity}, Balance {acc.balance}, Floating {acc.profit}\n"
        f"PNL HOY (Sincronizado): {hoy_pnl}\n"
        f"PNL TOTAL: {total_pnl}\n\n"
        "MISION: Sé MUY DIRECTO. Di cuánto hemos perdido hoy en total (Realizado + Flotante). "
        "Dime cuáles son los 2 peores símbolos basados en las pérdidas de HOY."
    )
    
    print("--- SOLICITANDO ANÁLISIS SENTINEL v1.8.2 ---")
    try:
        response = model.generate_content(prompt)
        print(response.text)
    except Exception as e:
        print(f"Error AI: {e}")
    
    mt5.shutdown()

if __name__ == "__main__":
    asyncio.run(test())
