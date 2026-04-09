import asyncio
import logging
import MetaTrader5 as mt5
from typing import List, Set, Dict
from ..core.bus import bus
from ..models.events import TickEvent

logger = logging.getLogger("PST_V2.Streamer")

class MT5Streamer:
    """Consumidor de ticks de MT5 y productor de eventos TickEvent."""
    
    def __init__(self, symbols: List[str] = None):
        self.symbols: Set[str] = set(symbols) if symbols else set()
        self._last_ticks: Dict[str, float] = {} # symbol -> last_time_msc
        self._running = False
        self._polling_interval = 0.02 # 20ms (Grado Institucional)

    def add_symbol(self, symbol: str):
        if mt5.symbol_select(symbol, True):
            self.symbols.add(symbol)
            logger.info(f"👀 Streamer: Siguiendo {symbol}")
        else:
            logger.error(f"❌ Fallo al seleccionar {symbol} en MT5")

    async def _stream_loop(self):
        """Bucle de alta velocidad para detectar cambios en ticks."""
        self._running = True
        logger.info(f"📡 MT5 Streamer Activado ({len(self.symbols)} símbolos)")
        
        while self._running:
            try:
                for symbol in self.symbols:
                    tick = mt5.symbol_info_tick(symbol)
                    if not tick:
                        continue
                    
                    # Solo emitimos si el tiempo del tick ha cambiado (msc = milisegundos)
                    last_time = self._last_ticks.get(symbol, 0)
                    if tick.time_msc > last_time:
                        self._last_ticks[symbol] = tick.time_msc
                        
                        event = TickEvent(
                            symbol=symbol,
                            bid=tick.bid,
                            ask=tick.ask,
                            volume=tick.volume,
                            time=tick.time_msc
                        )
                        # Emitimos al bus de forma asíncrona
                        await bus.emit("tick", event)
                        
                await asyncio.sleep(self._polling_interval)
                
            except Exception as e:
                logger.error(f"❌ Error en MT5 Streamer: {e}")
                await asyncio.sleep(1)

    def start(self):
        if not mt5.initialize():
            logger.error("❌ No se pudo conectar a MT5 para el Streamer")
            return None
        return asyncio.create_task(self._stream_loop())

    def stop(self):
        self._running = False
        logger.info("🛑 MT5 Streamer Detenido")
