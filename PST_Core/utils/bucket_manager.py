"""
PST Capital Bucket Manager
Gestiona la separación del capital en cubetas por tipo de estrategia.

Cada estrategia opera sobre una fracción del balance total, no sobre el total.
Esto permite:
  - Que una estrategia con racha mala no consuma el capital de las demás
  - Rebalanceo mensual automático basado en rendimiento real
  - Mayor control sobre la exposición por categoría

Ejemplo con balance 10.000€:
  TREND   (40%) → 4.000€ → el riesgo del 0.25% se aplica sobre 4.000, no 10.000
  RANGE   (30%) → 3.000€
  SCALPING(30%) → 3.000€

Rebalanceo:
  Si TREND generó +15% más que el promedio, aumenta su cubeta en 5pp
  Si RANGE perdió > 10%, reduce su cubeta en 5pp hasta un mínimo de 10%
"""
import logging
import aiosqlite
from datetime import datetime, timedelta
from typing import Dict, Optional

from ..config import CAPITAL_BUCKETS, STRATEGY_CATEGORIES, BUCKET_REBALANCE_THRESHOLD_PCT

logger = logging.getLogger("PST-Buckets")

BUCKET_MIN_PCT = 10.0   # Una cubeta nunca baja del 10%
BUCKET_MAX_PCT = 60.0   # Una cubeta nunca supera el 60%
REBALANCE_STEP  = 5.0   # Cuánto se mueve el peso en cada rebalanceo


class BucketManager:
    """
    Administra las cubetas de capital y calcula el balance efectivo
    que debe usar cada estrategia para su sizing de posición.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        # Pesos actuales (se cargan/actualizan desde DB)
        self._weights: Dict[str, float] = dict(CAPITAL_BUCKETS)
        self._last_rebalance: Optional[datetime] = None

    def get_bucket_balance(self, strategy_name: str, total_balance: float) -> float:
        """
        Retorna el balance efectivo de la cubeta correspondiente a esta estrategia.
        Si la estrategia no tiene cubeta definida, usa el balance total.
        """
        category = STRATEGY_CATEGORIES.get(strategy_name, "UNKNOWN")
        pct = self._weights.get(category, 100.0)
        bucket_balance = total_balance * (pct / 100.0)
        logger.debug(
            f"[Buckets] {strategy_name} ({category}): "
            f"{pct:.1f}% de {total_balance:.2f} = {bucket_balance:.2f}"
        )
        return bucket_balance

    async def load_weights(self):
        """Carga los pesos actuales desde la DB (persistencia entre reinicios)."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=10) as db:
                for category in CAPITAL_BUCKETS:
                    async with db.execute(
                        "SELECT value FROM bot_config WHERE key = ?",
                        (f"bucket_pct_{category}",),
                    ) as cursor:
                        row = await cursor.fetchone()
                        if row:
                            self._weights[category] = float(row[0])
            logger.info(f"[Buckets] Pesos cargados: {self._weights}")
        except Exception as e:
            logger.debug(f"[Buckets] Error cargando pesos (usando defaults): {e}")

    async def save_weights(self):
        """Persiste los pesos actuales en la DB."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=10) as db:
                for category, pct in self._weights.items():
                    await db.execute(
                        "INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)",
                        (f"bucket_pct_{category}", str(pct)),
                    )
                await db.commit()
        except Exception as e:
            logger.error(f"[Buckets] Error guardando pesos: {e}")

    async def get_monthly_pnl_by_category(self) -> Dict[str, float]:
        """Calcula el PnL del último mes por categoría de estrategia."""
        since = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
        result = {cat: 0.0 for cat in CAPITAL_BUCKETS}
        try:
            async with aiosqlite.connect(self.db_path, timeout=10) as db:
                async with db.execute(
                    "SELECT strategy_name, SUM(profit) FROM trades "
                    "WHERE time_out >= ? AND profit IS NOT NULL "
                    "GROUP BY strategy_name",
                    (since,),
                ) as cursor:
                    rows = await cursor.fetchall()

            for strategy_name, pnl in rows:
                cat = STRATEGY_CATEGORIES.get(strategy_name, "UNKNOWN")
                if cat in result:
                    result[cat] = result.get(cat, 0.0) + (pnl or 0.0)
        except Exception as e:
            logger.error(f"[Buckets] Error calculando PnL mensual: {e}")
        return result

    async def rebalance_if_needed(self, total_balance: float):
        """
        Rebalanceo mensual: redistribuye pesos basándose en el rendimiento relativo.
        Solo opera si han pasado al menos 30 días desde el último rebalanceo.
        """
        now = datetime.now()
        if self._last_rebalance and (now - self._last_rebalance).days < 30:
            return  # Muy pronto para rebalancear

        pnl_by_cat = await self.get_monthly_pnl_by_category()

        if not any(abs(v) > 0 for v in pnl_by_cat.values()):
            logger.info("[Buckets] Sin datos suficientes para rebalanceo.")
            return

        # Calcular rendimiento porcentual de cada cubeta respecto a su capital
        returns: Dict[str, float] = {}
        for cat, pnl in pnl_by_cat.items():
            bucket_capital = total_balance * (self._weights.get(cat, 100) / 100.0)
            returns[cat] = (pnl / bucket_capital * 100.0) if bucket_capital > 0 else 0.0

        avg_return = sum(returns.values()) / len(returns) if returns else 0.0

        new_weights = dict(self._weights)
        changed = False

        for cat, ret in returns.items():
            deviation = ret - avg_return
            if abs(deviation) >= BUCKET_REBALANCE_THRESHOLD_PCT:
                if deviation > 0:
                    # Estrategia rentable → aumentar su cubeta
                    new_weights[cat] = min(BUCKET_MAX_PCT, new_weights[cat] + REBALANCE_STEP)
                else:
                    # Estrategia con pérdidas → reducir su cubeta
                    new_weights[cat] = max(BUCKET_MIN_PCT, new_weights[cat] - REBALANCE_STEP)
                changed = True
                logger.info(
                    f"[Buckets] Rebalanceo {cat}: "
                    f"{self._weights[cat]:.1f}% → {new_weights[cat]:.1f}% "
                    f"(retorno: {ret:+.2f}% vs promedio {avg_return:+.2f}%)"
                )

        if changed:
            # Normalizar para que la suma no supere 100%
            total = sum(new_weights.values())
            if total > 100:
                factor = 100.0 / total
                new_weights = {k: v * factor for k, v in new_weights.items()}

            self._weights = new_weights
            await self.save_weights()
            self._last_rebalance = now

            summary = " | ".join(f"{k}: {v:.1f}%" for k, v in self._weights.items())
            logger.info(f"[Buckets] Nuevos pesos: {summary}")
        else:
            logger.info("[Buckets] Rebalanceo: sin cambios necesarios.")
            self._last_rebalance = now


# Singleton global — inicializado en start_v6
bucket_manager: Optional[BucketManager] = None
