import logging
from ..core.bus import bus
from .manager import socket_manager
from ..models.events import TickEvent, SignalEvent, OrderEvent, RadarEvent

logger = logging.getLogger("PST_V2.Bridge")

class BusBridge:
    """
    Puente entre el EventBus interno y el SocketManager externo (Web).
    Escucha eventos y los retransmite vía WebSocket.
    """
    
    def __init__(self):
        self.enabled = False

    async def on_tick(self, event: TickEvent):
        await socket_manager.broadcast({
            "type": "TICK",
            "symbol": event.symbol,
            "bid": event.bid,
            "ask": event.ask,
            "time": event.time
        })

    async def on_signal(self, event: SignalEvent):
        await socket_manager.broadcast({
            "type": "SIGNAL",
            "strategy": event.strategy,
            "symbol": event.symbol,
            "direction": event.signal_type,
            "score": event.score,
            "price": event.entry_price,
            "sl": event.sl,
            "tp": event.tp,
            "metadata": event.metadata
        })

    async def on_order(self, event: OrderEvent):
        await socket_manager.broadcast({
            "type": "ORDER",
            "symbol": event.symbol,
            "ticket": event.ticket,
            "status": event.type,
            "price": event.price,
            "comment": event.comment
        })

    async def on_radar(self, event: RadarEvent):
        await socket_manager.broadcast({
            "type": "RADAR",
            "symbol": event.symbol,
            "strategy": event.strategy,
            "score": event.score,
            "direction": event.direction
        })

    async def broadcast_telemetry(self, data: dict):
        """Envía telemetría de toda la flota al frontend."""
        await socket_manager.broadcast({
            "type": "TELEMETRY_UPDATE",
            "data": data # { "BTCUSD": { "M5": { "rsi": 45 }, ... } }
        })

    def start(self):
        """Suscribir el puente a los eventos del bus."""
        self.enabled = True
        bus.subscribe("tick", self.on_tick)
        bus.subscribe("signal", self.on_signal)
        bus.subscribe("order", self.on_order)
        bus.subscribe("radar", self.on_radar)
        logger.info("🌉 Puente de WebSockets activado y suscrito al EventBus.")

bridge = BusBridge()
