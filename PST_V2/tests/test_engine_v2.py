import asyncio
import logging
import sys
import os

# Ajustar path para importar PST_V2
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from PST_V2.core.bus import bus
from PST_V2.engine.mt5_streamer import MT5Streamer
from PST_V2.strategies.scalper_active_v2 import ScalperActiveV2

# Configuración de logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)

async def signal_logger(event):
    """Suscriptor que imprime las señales generadas por las estrategias."""
    print(f"\n🎯 [SENAL GENERADA] Símbolo: {event.symbol} | Estrategia: {event.strategy} | Tipo: {event.signal_type} | Score: {event.score}")
    print(f"   📊 Niveles: Entrada: {event.entry_price:.5f} | SL: {event.sl:.5f} | TP: {event.tp:.5f}\n")

async def main():
    print("🧪 Iniciando Test Pro de Motor V2 (Ticks + Estrategia)...")
    
    # 1. Suscribirse a señales
    bus.subscribe("signal", signal_logger)
    
    # 2. Iniciar el Bus
    bus.start()
    
    # 3. Inicializar Estrategia y suscribirla
    symbols = ["EURUSD", "BTCUSD", "ETHUSD", "XAUUSD"]
    scalper = ScalperActiveV2(symbols=symbols)
    scalper.start()
    
    # 4. Iniciar el Streamer
    streamer = MT5Streamer(symbols=symbols)
    streamer_task = streamer.start()
    
    if not streamer_task:
        print("❌ Error al iniciar el streamer.")
        return

    print("🚀 Motor y Estrategia en marcha. Esperando señales durante 30 segundos...")
    
    try:
        await asyncio.sleep(30)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n🛑 Deteniendo test...")
        streamer.stop()
        scalper.stop()
        bus.stop()
        print("✅ Test de Señales finalizado.")

if __name__ == "__main__":
    asyncio.run(main())
