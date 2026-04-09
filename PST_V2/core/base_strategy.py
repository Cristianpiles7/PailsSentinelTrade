import logging
from abc import ABC, abstractmethod
from typing import Any, Dict
from ..models.events import TickEvent, SignalEvent, RadarEvent
from ..core.bus import bus

logger = logging.getLogger("PST_V2.Strategy")

class BaseStrategyV2(ABC):
    """
    Clase base para estrategias Sentinel V2.
    Se suscribe a eventos de tick y emite eventos de señal.
    """
    
    def __init__(self, name: str, symbols: list):
        self.name = name
        self.symbols = symbols
        self.enabled = True
        self._last_signals = {} # Tracking de estado: {symbol: last_type}
        logger.info(f"🧠 Estrategia V2 Inicializada: {self.name} para {len(self.symbols)} símbolos")

    async def on_tick(self, tick: TickEvent):
        """Manejador de ticks. Filtra por símbolo y delega el cálculo."""
        if not self.enabled or tick.symbol not in self.symbols:
            return
        
        # Delegar el cálculo de la señal
        signal_data = await self.calculate_signal(tick)
        
        if signal_data:
            # 1. Siempre emitir evento de Radar para la UI (Pulso)
            radar_event = RadarEvent(
                symbol=tick.symbol,
                strategy=self.name,
                score=signal_data.get("score", 0.0),
                direction=signal_data.get("signal_type", "NEUTRAL")
            )
            await bus.emit("radar", radar_event)

            # 2. Emitir señal real de trading solo si CAMBIA el estado de la señal
            current_type = signal_data.get("signal_type", "NEUTRAL")
            last_type = self._last_signals.get(tick.symbol, "NEUTRAL")

            if current_type != last_type:
                self._last_signals[tick.symbol] = current_type
                
                if current_type != "NEUTRAL":
                    event = SignalEvent(
                        symbol=tick.symbol,
                        strategy=self.name,
                        score=signal_data.get("score", 0),
                        signal_type=current_type,
                        entry_price=tick.ask if current_type == "BUY" else tick.bid,
                        sl=signal_data.get("sl", 0.0),
                        tp=signal_data.get("tp", 0.0),
                        metadata=signal_data.get("metadata", {})
                    )
                    await bus.emit("signal", event)

    @abstractmethod
    async def calculate_signal(self, tick: TickEvent) -> Dict[str, Any]:
        """Lógica principal que debe implementar cada estrategia."""
        pass

    def start(self):
        """Suscribir la estrategia al bus."""
        bus.subscribe("tick", self.on_tick)
        logger.info(f"✅ {self.name} suscrita al Bus de Eventos")

    def stop(self):
        self.enabled = False
