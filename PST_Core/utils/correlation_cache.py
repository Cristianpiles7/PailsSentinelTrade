"""
PST Dynamic Correlation Cache
Calcula correlaciones reales entre símbolos usando datos H1 de los últimos 20 días.
Se actualiza cada hora para evitar computar en cada tick.

Uso:
    from PST_Core.utils.correlation_cache import corr_cache
    await corr_cache.update(symbols)
    is_correlated = corr_cache.is_correlated("EURUSD", "GBPUSD", threshold=0.80)
"""
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

logger = logging.getLogger("PST-CorrCache")

# Singleton compartido entre todos los símbolos del orchestrator
corr_cache: "DynamicCorrelationCache" = None  # inicializado al final del módulo


class DynamicCorrelationCache:
    """
    Mantiene una matriz de correlaciones de Pearson actualizada periódicamente.
    Usa retornos logarítmicos H1 de los últimos 20 días de trading.
    """

    UPDATE_INTERVAL_SECONDS = 3600  # 1 hora

    def __init__(self):
        self._matrix: pd.DataFrame = pd.DataFrame()
        self._last_update: Optional[datetime] = None
        self._lock = asyncio.Lock()

    @property
    def is_stale(self) -> bool:
        if self._last_update is None:
            return True
        return (datetime.now() - self._last_update).total_seconds() > self.UPDATE_INTERVAL_SECONDS

    async def update(self, symbols: List[str]) -> bool:
        """
        Descarga H1 de los últimos 20 días para cada símbolo y calcula la matriz de correlación.
        Thread-safe: usa asyncio.Lock para evitar dobles actualizaciones.
        Returns True si la actualización fue exitosa.
        """
        if not self.is_stale:
            return True

        async with self._lock:
            if not self.is_stale:  # Re-check tras obtener el lock
                return True

            try:
                import MetaTrader5 as mt5
                from datetime import datetime, timedelta

                end_dt   = datetime.now()
                start_dt = end_dt - timedelta(days=20)

                closes: Dict[str, pd.Series] = {}
                for sym in symbols:
                    rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H1, start_dt, end_dt)
                    if rates is not None and len(rates) > 20:
                        df = pd.DataFrame(rates)
                        df["time"] = pd.to_datetime(df["time"], unit="s")
                        df = df.set_index("time")
                        closes[sym] = df["close"]
                    else:
                        logger.debug(f"[CorrCache] Sin datos H1 para {sym}")

                if len(closes) < 2:
                    logger.warning("[CorrCache] Menos de 2 símbolos con datos H1 — no se puede calcular correlación.")
                    return False

                # Alinear en un DataFrame común y calcular retornos logarítmicos
                price_df = pd.DataFrame(closes).dropna(how="all")
                # Rellenar huecos menores (fines de semana) y descartar el resto
                price_df = price_df.ffill(limit=5).dropna()
                returns  = np.log(price_df / price_df.shift(1)).dropna()

                self._matrix = returns.corr(method="pearson")
                self._last_update = datetime.now()

                logger.info(
                    f"[CorrCache] Matriz de correlación actualizada: "
                    f"{len(self._matrix)} símbolos, {len(returns)} barras H1"
                )
                return True

            except Exception as e:
                logger.error(f"[CorrCache] Error actualizando correlaciones: {e}")
                return False

    def get_correlation(self, sym_a: str, sym_b: str) -> Optional[float]:
        """Devuelve la correlación entre dos símbolos, o None si no está disponible."""
        if self._matrix.empty:
            return None
        if sym_a not in self._matrix.index or sym_b not in self._matrix.columns:
            return None
        return float(self._matrix.loc[sym_a, sym_b])

    def is_correlated(self, sym_a: str, sym_b: str, threshold: float = 0.80) -> bool:
        """
        Devuelve True si la correlación absoluta entre sym_a y sym_b supera el umbral.
        Correlación negativa fuerte (< -threshold) también cuenta como riesgo en mercados
        adversos, pero para gestión de posiciones solo bloqueamos correlación POSITIVA alta.
        """
        corr = self.get_correlation(sym_a, sym_b)
        if corr is None:
            return False
        return abs(corr) >= threshold

    def get_correlated_symbols(self, symbol: str, threshold: float = 0.80) -> List[Tuple[str, float]]:
        """
        Devuelve lista de (símbolo, correlación) altamente correlacionados con `symbol`.
        Ordenados de mayor a menor correlación absoluta.
        """
        if self._matrix.empty or symbol not in self._matrix.index:
            return []
        row = self._matrix.loc[symbol].drop(symbol, errors="ignore")
        correlated = [(s, float(c)) for s, c in row.items() if abs(c) >= threshold]
        return sorted(correlated, key=lambda x: abs(x[1]), reverse=True)


# Singleton global
corr_cache = DynamicCorrelationCache()
