import pandas as pd
import pandas_ta as ta
import MetaTrader5 as mt5
import logging
from typing import Dict, Optional
import asyncio

logger = logging.getLogger("PST_V2.DataStore")

class DataStore:
    """
    Almacén de datos históricos que se mantiene actualizado.
    Permite a las estrategias consultar DFs de M1, M5, M15 sin coste de latencia alto.
    """
    def __init__(self):
        self._cache: Dict[str, Dict[int, pd.DataFrame]] = {} # symbol -> {timeframe: df}
        self._lock = asyncio.Lock()

    async def get_data(self, symbol: str, timeframe_min: int, count: int = 100) -> Optional[pd.DataFrame]:
        """Obtiene datos de MT5 de forma asíncrona y cacheada."""
        async with self._lock:
            # Por ahora, consulta simple a MT5 (Optimizaremos esto haciendo polling de velas cada X segundos)
            rates = mt5.copy_rates_from_pos(symbol, self._get_mt5_tf(timeframe_min), 0, count)
            if rates is None or len(rates) == 0:
                return None
            
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s')
            return df

    def _get_mt5_tf(self, minutes: int):
        mapping = {1: mt5.TIMEFRAME_M1, 3: mt5.TIMEFRAME_M3, 5: mt5.TIMEFRAME_M5, 15: mt5.TIMEFRAME_M15, 60: mt5.TIMEFRAME_H1}
        return mapping.get(minutes, mt5.TIMEFRAME_M5)

# Instancia global
store = DataStore()
