
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

    def register_loss(self, symbol: str, duration_minutes=60, base_time=None):
        """Registra una pérdida y bloquea el símbolo por X minutos desde base_time."""
        start = base_time if base_time else datetime.now()
        deadline = start + timedelta(minutes=duration_minutes)
        
        # Si el deadline ya pasó, no hacemos nada
        if deadline <= datetime.now():
            return

        self.cooldowns[symbol] = deadline
        logger.warning(f"❄️ COOLDOWN (PÉRDIDA): {symbol} bloqueado hasta {deadline.strftime('%H:%M')} (desde {start.strftime('%H:%M')}).")

    def register_trade_finish(self, symbol: str, duration_minutes=30, base_time=None):
        """Registra el fin de un trade para evitar re-entradas (desde base_time)."""
        start = base_time if base_time else datetime.now()
        deadline = start + timedelta(minutes=duration_minutes)
        
        if deadline <= datetime.now():
            return

        # Solo sobreescribimos si el nuevo bloqueo es mayor al existente
        if symbol in self.cooldowns:
            if deadline < self.cooldowns[symbol]:
                return
        
        self.cooldowns[symbol] = deadline
        logger.info(f"❄️ HISTÉRESIS: {symbol} bloqueado hasta {deadline.strftime('%H:%M')} (desde {start.strftime('%H:%M')}).")

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
