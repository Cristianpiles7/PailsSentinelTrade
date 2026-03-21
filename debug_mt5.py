import MetaTrader5 as mt5
import os
from dotenv import load_dotenv

def diagnose():
    if not mt5.initialize():
        print("Error: No se pudo conectar a MetaTrader 5")
        return

    print("--- POSICIONES EN TIEMPO REAL ---")
    positions = mt5.positions_get()
    if positions:
        for p in positions:
            print(f"Símbolo MT5: {p.symbol} | Profit: {p.profit}")
    else:
        print("No hay posiciones abiertas.")

    print("\n--- SÍMBOLOS DISPONIBLES EN MARKET WATCH ---")
    symbols = mt5.symbols_get()
    # Mostrar solo los primeros 10 para no inundar
    for s in symbols[:20]:
        print(f"MT5 Sym: {s.name}")

    mt5.shutdown()

if __name__ == "__main__":
    diagnose()
