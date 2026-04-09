import asyncio
import logging
import MetaTrader5 as mt5
import sys
import os
import uvicorn

# Asegurar que el terminal use UTF-8 para evitar errores de Emojis en Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Asegurar que el root del proyecto está en el path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PST_V2.core.bus import bus
from PST_V2.engine.mt5_streamer import MT5Streamer
from PST_V2.engine.executor_async import AsyncExecutor
from PST_V2.strategies.scalper_active_v2 import ScalperActiveV2
from PST_V2.strategies.ema_flow_v2 import EMAFlowV2
from PST_V2.strategies.mean_reversion_v2 import MeanReversionV2
from PST_V2.api.manager import app
from PST_V2.api.bridge import bridge

# Configuración de Logs profesional
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler("v2_sentinel_prime.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("SentinelPrime_V2")

async def run_api():
    """Inicia el servidor FastAPI/WebSockets de forma asíncrona."""
    config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    logger.info("📡 API V2 & WebSockets escuchando en http://127.0.0.1:8000")
    await server.serve()

async def main():
    logger.info("🌌 [SENTINEL PRIME V2] Iniciando Motor de Eventos...")
    
    # 1. Inicializar MT5
    if not mt5.initialize():
        logger.error("❌ Fallo crítico al inicializar MT5")
        return

    # 2. Configurar Componentes
    active_symbols = ["EURUSD", "BTCUSD", "ETHUSD", "XAUUSD", "DE40.CASH", "US30.CASH"]
    
    # Iniciar Bus y APIs
    bus_task = bus.start()
    api_task = asyncio.create_task(run_api())
    
    # Activar Puente de Datos (Bus -> WebSockets)
    bridge.start()
    
    # Ejecutor (Riesgo nominal de 5€)
    executor = AsyncExecutor(risk_per_trade_euro=5.0)
    executor.start()
    
    # Estrategias
    strategies = [
        ScalperActiveV2(symbols=active_symbols),
        EMAFlowV2(symbols=active_symbols),
        MeanReversionV2(symbols=active_symbols)
    ]
    for strat in strategies:
        strat.start()
        
    # 3. Iniciar Streamer
    streamer = MT5Streamer(symbols=active_symbols)
    streamer_task = streamer.start()
    
    logger.info("✅ Sentinel V2 FULL STACK Online (Motor + Streaming + API + WebSockets)")
    
    # 4. Tarea de Telemetría Global (Latido de Fondo)
    async def telemetry_loop():
        import pandas as pd
        import pandas_ta as ta
        tfs = {"M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1}
        while True:
            try:
                fleet_data = {}
                for sym in active_symbols:
                    sym_tel = {}
                    for name, tf_val in tfs.items():
                        rates = mt5.copy_rates_from_pos(sym, tf_val, 0, 50)
                        if rates is not None and len(rates) > 20:
                            df = pd.DataFrame(rates)
                            rsi = ta.rsi(df['close'], length=14)
                            last_rsi = rsi.iloc[-1] if not rsi.empty else None
                            # Momentum simple para el campo A:
                            momentum = abs(df['close'].iloc[-1] - df['close'].iloc[-5])
                            sym_tel[name] = {"rsi": round(float(last_rsi), 1) if last_rsi is not None else None, "adx": round(float(momentum), 2)}
                    fleet_data[sym] = sym_tel
                
                await bridge.broadcast_telemetry(fleet_data)
                await asyncio.sleep(10) # Latido cada 10 segundos
            except Exception as e:
                logger.error(f"❌ Error en Telemetría Loop: {e}")
                await asyncio.sleep(15)

    tel_task = asyncio.create_task(telemetry_loop())

    try:
        # Mantener el loop vivo
        await asyncio.gather(bus_task, streamer_task, api_task, tel_task)
    except KeyboardInterrupt:
        logger.info("🛑 Deteniendo Sentinel V2...")
    finally:
        streamer.stop()
        executor.stop()
        bus.stop()
        mt5.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
