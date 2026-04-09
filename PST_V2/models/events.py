from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any

@dataclass
class Event:
    """Clase base para todos los eventos del sistema."""
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class TickEvent(Event):
    symbol: str = ""
    bid: float = 0.0
    ask: float = 0.0
    volume: int = 0
    time: int = 0 # Timestamp de MT5

@dataclass
class SignalEvent(Event):
    symbol: str = ""
    strategy: str = ""
    score: int = 0
    signal_type: str = "NEUTRAL" # BUY, SELL, NEUTRAL
    entry_price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0

@dataclass
class OrderEvent(Event):
    ticket: int = 0
    symbol: str = ""
    type: str = "" # OPENED, CLOSED, MODIFIED, REJECTED
    price: float = 0.0
    profit: float = 0.0
    comment: str = ""

@dataclass
class RadarEvent(Event):
    """Evento para telemetría de radar (puntuaciones en tiempo real)."""
    symbol: str = ""
    strategy: str = ""
    score: float = 0.0
    direction: str = "NEUTRAL" # BUY, SELL, NEUTRAL
