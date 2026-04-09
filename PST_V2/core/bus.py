import asyncio
import logging
from typing import Dict, List, Callable, Any, Awaitable
from ..models.events import Event, TickEvent, SignalEvent, OrderEvent

logger = logging.getLogger("PST_V2.Bus")

class EventBus:
    """Bus de eventos centralizado y asíncrono para Sentinel V2."""
    
    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[Any], Awaitable[None]]]] = {}
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        logger.info("⚡ EventBus V2 Inicializado")

    def subscribe(self, event_type: str, callback: Callable[[Any], Awaitable[None]]):
        """Suscribir una función asíncrona a un tipo de evento."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)
        logger.debug(f"➕ Suscriptor añadido para: {event_type}")

    async def emit(self, event_type: str, data: Any):
        """Pone un evento en la cola para ser procesado."""
        await self._queue.put((event_type, data))

    async def _process_events(self):
        """Bucle principal de procesamiento de eventos."""
        self._running = True
        while self._running:
            try:
                event_type, data = await self._queue.get()
                
                # Obtener suscriptores
                targets = self._subscribers.get(event_type, [])
                wildcards = self._subscribers.get("*", [])
                
                tasks = []
                for callback in targets + wildcards:
                    tasks.append(asyncio.create_task(callback(data)))
                
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                
                self._queue.task_done()
                
            except Exception as e:
                logger.error(f"❌ Error en process_events: {e}")
                await asyncio.sleep(0.1)

    def start(self):
        return asyncio.create_task(self._process_events())

    def stop(self):
        self._running = False
        logger.info("🛑 EventBus V2 Detenido")

bus = EventBus()
