import asyncio
import sys
import os

# Añadir el directorio raíz al path para que los imports relativos funcionen
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PST_Core.engine.orchestrator import start_v6

# --- CONFIGURACIÓN DE SÍMBOLOS PST ---
# Puedes editar esta lista o pasarla por argumentos en el futuro
SYMBOLS_TO_TRADE = [
    "US500.cash", 
    "EU50.cash", 
    "XAGUSD", 
    "XAUUSD", 
    "NVDA", 
    "TSLA", 
    "GOOG", 
    "AAPL",
    "BTCUSD",
    "ETHUSD"
]

if __name__ == "__main__":
    print("💎 INICIANDO PAILS SENTINEL TRADE ASYNC (FTMO EDITION) 💎")
    try:
        asyncio.run(start_v6(SYMBOLS_TO_TRADE))
    except KeyboardInterrupt:
        print("\n🛑 Bot detenido manualmente por el usuario.")
    except Exception as e:
        print(f"❌ Error crítico al arrancar PST: {e}")
