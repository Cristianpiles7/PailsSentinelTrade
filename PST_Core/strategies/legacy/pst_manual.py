import logging
from ..models.classifier import RegimeMode

logger = logging.getLogger("PST-Manual")

class PSTManual:
    """
    Estrategia de Marcador de Posición para Trading Manual.
    No genera señales automáticas (Score siempre 0).
    Permite al usuario tener un activo en el radar sin ruidos de bot.
    """
    STRATEGY_NAME = "PST-Manual"
    STRATEGY_TYPE = "ALL" # Compatible con cualquier régimen
    WEIGHT = 1.0

    async def calculate_signal(self, data_input, current_regime=None, user_levels=None, **kwargs):
        """
        Retorna siempre 0. Esta estrategia no opera sola.
        """
        return {
            "entry": 0, 
            "atr": 0, 
            "score": 0,
            "metadata": {
                "strategy": self.STRATEGY_NAME,
                "score": 0,
                "total_score": 0,
                "score_breakdown": {"Modo": "Operativa Discrecional"},
                "factors_detailed": [{"k": "Modo", "v": "Trading Manual", "score": 0}]
            }
        }
