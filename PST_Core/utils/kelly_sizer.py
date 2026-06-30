"""
PST Kelly Criterion Sizer
Calcula el tamaño de posición óptimo basado en el historial real de la estrategia.

Fórmula:
    Kelly%  = WinRate - (1 - WinRate) / RR_ratio
    HalfKelly = Kelly% * 0.5   ← siempre usamos Half-Kelly para reducir volatilidad

Límites de seguridad:
    - Mínimo: 0.10% del balance (nunca arriesgar menos de esto)
    - Máximo: 1.00% del balance (nunca más, independientemente de Kelly)
    - Muestra mínima: 20 trades cerrados antes de confiar en Kelly
      → Con menos datos, usa el riesgo base configurado en la DB

Uso:
    sizer = KellySizer(db)
    risk_pct = await sizer.get_risk_pct("PST-AlphaTrend", fallback_pct=0.25)
"""
import logging
import aiosqlite
from typing import Optional

logger = logging.getLogger("PST-Kelly")

KELLY_MIN_TRADES = 20    # Mínimo de trades para confiar en el cálculo
KELLY_MAX_LOOKBACK = 100 # Últimos N trades a considerar
KELLY_MIN_RISK_PCT = 0.10
KELLY_MAX_RISK_PCT = 1.00
KELLY_FRACTION    = 0.50  # Half-Kelly para conservar capital


class KellySizer:
    """
    Calcula el riesgo óptimo por operación usando Half-Kelly sobre el historial real.
    Thread-safe: cada llamada abre su propia conexión a la DB.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._cache: dict = {}  # {strategy_name: (risk_pct, trade_count)}

    async def get_stats(self, strategy_name: str) -> dict:
        """
        Obtiene estadísticas reales de una estrategia desde la DB.
        Usa los últimos KELLY_MAX_LOOKBACK trades cerrados.
        """
        try:
            async with aiosqlite.connect(self.db_path, timeout=10) as db:
                async with db.execute(
                    """
                    SELECT profit, price_in, price_out, type, sl
                    FROM trades
                    WHERE strategy_name = ?
                      AND price_out IS NOT NULL
                      AND profit IS NOT NULL
                    ORDER BY time_out DESC
                    LIMIT ?
                    """,
                    (strategy_name, KELLY_MAX_LOOKBACK),
                ) as cursor:
                    rows = await cursor.fetchall()
        except Exception as e:
            logger.debug(f"[Kelly] Error leyendo trades de {strategy_name}: {e}")
            return {}

        if not rows:
            return {}

        wins = [r for r in rows if r[0] > 0]
        losses = [r for r in rows if r[0] <= 0]
        n_total = len(rows)
        n_wins = len(wins)

        if n_total == 0:
            return {}

        win_rate = n_wins / n_total

        # Calcular R promedio de wins y losses usando precio_in y sl como referencia
        # Si no hay sl, estimamos el R directamente del profit ratio
        avg_win_r = 0.0
        avg_loss_r = 0.0

        win_rs = []
        for profit, price_in, price_out, trade_type, sl in wins:
            if sl and price_in and sl != price_in:
                sl_dist = abs(price_in - sl)
                tp_dist = abs(price_out - price_in) if price_out else 0
                if sl_dist > 0:
                    win_rs.append(tp_dist / sl_dist)
        avg_win_r = sum(win_rs) / len(win_rs) if win_rs else 1.5

        loss_rs = []
        for profit, price_in, price_out, trade_type, sl in losses:
            if sl and price_in and sl != price_in:
                sl_dist = abs(price_in - sl)
                actual_loss = abs(price_out - price_in) if price_out else sl_dist
                if sl_dist > 0:
                    loss_rs.append(actual_loss / sl_dist)
        avg_loss_r = sum(loss_rs) / len(loss_rs) if loss_rs else 1.0

        return {
            "n_trades":   n_total,
            "win_rate":   win_rate,
            "avg_win_r":  avg_win_r,
            "avg_loss_r": avg_loss_r,
        }

    def compute_kelly(self, win_rate: float, avg_win_r: float, avg_loss_r: float) -> float:
        """
        Calcula el Half-Kelly como % de capital a arriesgar.

        Kelly = W - (1-W)/RR
        donde RR = avg_win_r / avg_loss_r (ratio ganancia/pérdida en R)
        """
        if avg_loss_r <= 0 or avg_win_r <= 0:
            return KELLY_MIN_RISK_PCT

        rr = avg_win_r / avg_loss_r
        kelly_full = win_rate - (1 - win_rate) / rr

        if kelly_full <= 0:
            # Kelly negativo → la estrategia destruye capital en el largo plazo
            logger.warning(f"[Kelly] Kelly negativo ({kelly_full:.3f}) → estrategia no rentable")
            return KELLY_MIN_RISK_PCT

        half_kelly_pct = kelly_full * KELLY_FRACTION * 100  # convertir a %
        return max(KELLY_MIN_RISK_PCT, min(KELLY_MAX_RISK_PCT, half_kelly_pct))

    async def get_risk_pct(self, strategy_name: str, fallback_pct: float = 0.25) -> tuple[float, str]:
        """
        Devuelve el % de riesgo recomendado para esta estrategia.

        Returns:
            (risk_pct, source)
            source: "KELLY" si hay suficientes datos, "FALLBACK" si no
        """
        stats = await self.get_stats(strategy_name)

        if not stats or stats["n_trades"] < KELLY_MIN_TRADES:
            n = stats.get("n_trades", 0)
            logger.debug(
                f"[Kelly] {strategy_name}: solo {n} trades (min {KELLY_MIN_TRADES}). "
                f"Usando fallback {fallback_pct}%"
            )
            return fallback_pct, "FALLBACK"

        risk_pct = self.compute_kelly(
            stats["win_rate"], stats["avg_win_r"], stats["avg_loss_r"]
        )

        logger.info(
            f"[Kelly] {strategy_name}: WR={stats['win_rate']:.1%} | "
            f"AvgWin={stats['avg_win_r']:.2f}R | AvgLoss={stats['avg_loss_r']:.2f}R | "
            f"→ Half-Kelly={risk_pct:.3f}%"
        )

        # Cachear para no recalcular en cada tick
        self._cache[strategy_name] = (risk_pct, stats["n_trades"])

        return risk_pct, "KELLY"

    async def get_risk_summary(self) -> dict:
        """Devuelve resumen de riesgo Kelly para todas las estrategias conocidas."""
        from ..config import ENABLED_STRATEGIES
        result = {}
        for s in ENABLED_STRATEGIES:
            stats = await self.get_stats(s)
            if stats and stats["n_trades"] >= KELLY_MIN_TRADES:
                risk_pct = self.compute_kelly(
                    stats["win_rate"], stats["avg_win_r"], stats["avg_loss_r"]
                )
                result[s] = {**stats, "kelly_risk_pct": risk_pct}
            else:
                result[s] = {**stats, "kelly_risk_pct": None, "source": "FALLBACK"}
        return result
