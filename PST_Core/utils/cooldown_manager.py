
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("PST-Cooldown")

class CooldownManager:
    """
    Gestiona los bloqueos temporales de activos tras un Stop Loss.
    Es un Singleton en memoria (se reinicia si se cierra el bot, lo cual es aceptable).
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
             cls._instance = super(CooldownManager, cls).__new__(cls)
             cls._instance.cooldowns = {} # {symbol: deadline_datetime}
        return cls._instance

    def register_loss(self, symbol: str, duration_minutes=60):
        """Registra una pérdida y bloquea el símbolo por X minutos."""
        deadline = datetime.now() + timedelta(minutes=duration_minutes)
        self.cooldowns[symbol] = deadline
        logger.warning(f"❄️ COOLDOWN: {symbol} bloqueado hasta {deadline.strftime('%H:%M')} tras Stop Loss.")

    def is_blocked(self, symbol: str) -> tuple[bool, str]:
        """Retorna (True, Mensaje) si está bloqueado."""
        if symbol in self.cooldowns:
            deadline = self.cooldowns[symbol]
            if datetime.now() < deadline:
                remaining = int((deadline - datetime.now()).total_seconds() / 60)
                return True, f"❄️ EN COOLDOWN ({remaining} min restan)"
            else:
                del self.cooldowns[symbol] # Expired
                
        return False, ""

# Global instance
cooldown_mgr = CooldownManager()
