"""
PST Backtesting Engine
Simula señales de estrategias sobre datos históricos para medir rendimiento real.
"""
import asyncio
import logging
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("PST-Backtest")


@dataclass
class BacktestTrade:
    entry_time: Optional[datetime]
    exit_time: Optional[datetime]
    symbol: str
    strategy: str
    direction: int          # 1 = BUY, -1 = SELL
    entry_price: float
    sl_price: float
    tp_price: float
    exit_price: Optional[float] = None
    result: str = "OPEN"   # "WIN", "LOSS", "OPEN"
    pnl_r: float = 0.0     # PnL en múltiplos de R
    score: int = 0
    atr: float = 0.0
    exit_reason: str = ""  # "TP", "SL", "VWAP_EXIT", "TIMEOUT", "BE", "EOD" (motor fiel)


@dataclass
class BacktestResult:
    strategy_name: str
    symbol: str
    period_start: Optional[datetime]
    period_end: Optional[datetime]
    trades: List[BacktestTrade] = field(default_factory=list)

    @property
    def closed_trades(self) -> List[BacktestTrade]:
        return [t for t in self.trades if t.result in ("WIN", "LOSS")]

    @property
    def win_rate(self) -> float:
        ct = self.closed_trades
        if not ct:
            return 0.0
        return sum(1 for t in ct if t.result == "WIN") / len(ct) * 100

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t.pnl_r for t in self.closed_trades if t.pnl_r > 0)
        gross_loss = abs(sum(t.pnl_r for t in self.closed_trades if t.pnl_r < 0))
        return gross_profit / gross_loss if gross_loss > 0 else float("inf")

    @property
    def sharpe_ratio(self) -> float:
        if len(self.closed_trades) < 2:
            return 0.0
        pnls = [t.pnl_r for t in self.closed_trades]
        mean_r = np.mean(pnls)
        std_r = np.std(pnls, ddof=1)
        # Anualizado asumiendo ~252 operaciones/año como proxy
        return (mean_r / std_r) * np.sqrt(252) if std_r > 0 else 0.0

    @property
    def max_drawdown_r(self) -> float:
        """Máximo drawdown en múltiplos de R sobre la curva de equity."""
        if not self.closed_trades:
            return 0.0
        equity = [0.0]
        for t in self.closed_trades:
            equity.append(equity[-1] + t.pnl_r)
        peak = equity[0]
        max_dd = 0.0
        for e in equity:
            if e > peak:
                peak = e
            dd = peak - e
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @property
    def sortino_ratio(self) -> float:
        """Como Sharpe pero penalizando solo la volatilidad a la baja (downside)."""
        if len(self.closed_trades) < 2:
            return 0.0
        pnls = [t.pnl_r for t in self.closed_trades]
        mean_r = np.mean(pnls)
        downside = [p for p in pnls if p < 0]
        if not downside:
            return float("inf") if mean_r > 0 else 0.0
        # Desviación a la baja respecto a 0 (MAR = 0)
        down_std = np.sqrt(np.mean(np.square(downside)))
        return (mean_r / down_std) * np.sqrt(252) if down_std > 0 else 0.0

    @property
    def avg_rr_realized(self) -> float:
        """R:R medio REALIZADO (media de pnl_r ganadores / |media de pnl_r perdedores|)."""
        wins = [t.pnl_r for t in self.closed_trades if t.pnl_r > 0]
        losses = [abs(t.pnl_r) for t in self.closed_trades if t.pnl_r < 0]
        if not wins or not losses:
            return 0.0
        return float(np.mean(wins) / np.mean(losses))

    @property
    def total_r(self) -> float:
        return sum(t.pnl_r for t in self.closed_trades)

    @property
    def avg_win_r(self) -> float:
        wins = [t.pnl_r for t in self.closed_trades if t.result == "WIN"]
        return np.mean(wins) if wins else 0.0

    @property
    def avg_loss_r(self) -> float:
        losses = [t.pnl_r for t in self.closed_trades if t.result == "LOSS"]
        return np.mean(losses) if losses else 0.0

    def summary(self) -> dict:
        ct = self.closed_trades
        period = ""
        if self.period_start and self.period_end:
            period = f"{self.period_start.date()} → {self.period_end.date()}"
        return {
            "strategy":         self.strategy_name,
            "symbol":           self.symbol,
            "period":           period,
            "total_trades":     len(ct),
            "win_rate":         f"{self.win_rate:.1f}%",
            "profit_factor":    f"{self.profit_factor:.2f}",
            "sharpe":           f"{self.sharpe_ratio:.2f}",
            "sortino":          f"{self.sortino_ratio:.2f}",
            "avg_rr_realized":  f"{self.avg_rr_realized:.2f}",
            "max_drawdown_r":   f"{self.max_drawdown_r:.2f}R",
            "total_r":          f"{self.total_r:.2f}R",
            "avg_win_r":        f"{self.avg_win_r:.2f}R",
            "avg_loss_r":       f"{self.avg_loss_r:.2f}R",
        }

    def print_report(self):
        print(f"\n{'─'*55}")
        for k, v in self.summary().items():
            print(f"  {k:<25} {v}")
        print(f"{'─'*55}")


class PSTBacktestEngine:
    """
    Motor de backtesting para estrategias PST.
    Reproduce calculate_signal() barra a barra sobre datos históricos reales.
    No requiere conexión a MT5 durante la simulación (solo para obtener datos).
    """

    # Timeframes requeridos por cada estrategia
    STRATEGY_TIMEFRAMES: Dict[str, List[str]] = {
        "PST-AlphaTrend":        ["h1", "m15"],
        "PST-RangeBreaker":      ["m15", "h1"],
        "PST-PrecisionScalping": ["m1", "m5"],
    }

    # SL por defecto (igual que el executor)
    DEFAULT_SL_MULT: Dict[str, float] = {
        "PST-AlphaTrend":        2.5,
        "PST-RangeBreaker":      2.5,
        "PST-PrecisionScalping": 1.25,
    }

    # TP por defecto según clase de activo
    DEFAULT_TP_MULT: Dict[str, float] = {
        "CRYPTO":    4.0,
        "INDEX":     5.5,
        "METAL":     3.5,
        "COMMODITY": 3.5,
        "FOREX":     6.0,
    }

    def __init__(
        self,
        score_threshold: int = 80,
        sl_mult: Optional[float] = None,
        tp_mult: Optional[float] = None,
    ):
        self.score_threshold = score_threshold
        self._sl_mult_override = sl_mult
        self._tp_mult_override = tp_mult

    def _get_asset_class(self, symbol: str) -> str:
        try:
            from ..utils.tech_utils import get_asset_class
            return get_asset_class(symbol)
        except Exception:
            if any(k in symbol.upper() for k in ["BTC", "ETH", "ADA", "SOL"]):
                return "CRYPTO"
            if any(k in symbol.upper() for k in ["XAU", "XAG", "GOLD"]):
                return "METAL"
            if any(k in symbol.upper() for k in ["US500", "NAS", "EU50"]):
                return "INDEX"
            return "FOREX"

    def _time_to_idx(self, df: pd.DataFrame, cutoff_time: datetime) -> int:
        """Devuelve el número de filas del df anteriores o iguales al cutoff_time."""
        if "time" not in df.columns:
            return len(df)
        mask = df["time"] <= cutoff_time
        return int(mask.sum())

    async def _call_signal(self, strategy, mtf_slice: dict) -> dict:
        try:
            return await strategy.calculate_signal(mtf_slice)
        except Exception as e:
            logger.debug(f"[Backtest] Error en calculate_signal: {e}")
            return {"score": 0, "entry": 0, "atr": 0, "metadata": {}}

    async def run(
        self,
        strategy,
        symbol: str,
        all_data: Dict[str, pd.DataFrame],
        warmup_bars: int = 220,
        step_bars: int = 1,
    ) -> BacktestResult:
        """
        Ejecuta el backtest completo para una estrategia y símbolo.

        Args:
            strategy:     Instancia de estrategia PST (PSTAlphaTrend, etc.)
            symbol:       Símbolo (ej: "EURUSD")
            all_data:     Dict de DataFrames por timeframe {"h1": df, "m15": df, ...}
            warmup_bars:  Velas de calentamiento antes de generar señales
            step_bars:    Cada cuántas velas del timeframe primario evaluar señal
        """
        strategy_name = strategy.STRATEGY_NAME
        tfs = self.STRATEGY_TIMEFRAMES.get(strategy_name, ["h1"])
        primary_tf = tfs[0]

        primary_df = all_data.get(primary_tf)
        if primary_df is None or len(primary_df) < warmup_bars + 50:
            logger.warning(f"[Backtest] Datos insuficientes para {strategy_name}/{symbol} en {primary_tf}")
            return BacktestResult(strategy_name, symbol, None, None)

        asset_class = self._get_asset_class(symbol)
        sl_mult = self._sl_mult_override or self.DEFAULT_SL_MULT.get(strategy_name, 2.5)
        tp_mult = self._tp_mult_override or self.DEFAULT_TP_MULT.get(asset_class, 4.0)

        has_time = "time" in primary_df.columns
        period_start = primary_df.iloc[warmup_bars]["time"] if has_time else None
        period_end = primary_df.iloc[-1]["time"] if has_time else None

        result = BacktestResult(strategy_name, symbol, period_start, period_end)
        open_trade: Optional[BacktestTrade] = None  # Solo 1 trade abierto por vez

        total_bars = len(primary_df)
        logger.info(f"[Backtest] {strategy_name} | {symbol} | {total_bars - warmup_bars} barras a evaluar")

        for i in range(warmup_bars, total_bars - 1, step_bars):
            current_bar = primary_df.iloc[i]
            current_time = current_bar["time"] if has_time else None

            # --- Resolución SL/TP del trade abierto en esta barra ---
            if open_trade is not None:
                h = current_bar["high"]
                l = current_bar["low"]
                if open_trade.direction == 1:  # BUY
                    if l <= open_trade.sl_price:
                        open_trade.exit_price = open_trade.sl_price
                        open_trade.exit_time = current_time
                        open_trade.result = "LOSS"
                        open_trade.pnl_r = -1.0
                        open_trade = None
                    elif h >= open_trade.tp_price:
                        open_trade.exit_price = open_trade.tp_price
                        open_trade.exit_time = current_time
                        open_trade.result = "WIN"
                        r = (open_trade.tp_price - open_trade.entry_price) / max(
                            open_trade.entry_price - open_trade.sl_price, 1e-10
                        )
                        open_trade.pnl_r = r
                        open_trade = None
                else:  # SELL
                    if h >= open_trade.sl_price:
                        open_trade.exit_price = open_trade.sl_price
                        open_trade.exit_time = current_time
                        open_trade.result = "LOSS"
                        open_trade.pnl_r = -1.0
                        open_trade = None
                    elif l <= open_trade.tp_price:
                        open_trade.exit_price = open_trade.tp_price
                        open_trade.exit_time = current_time
                        open_trade.result = "WIN"
                        r = (open_trade.entry_price - open_trade.tp_price) / max(
                            open_trade.sl_price - open_trade.entry_price, 1e-10
                        )
                        open_trade.pnl_r = r
                        open_trade = None

            # Si ya hay un trade abierto, no abrir otro
            if open_trade is not None:
                continue

            # --- Construir snapshot MTF hasta la barra i ---
            mtf_slice: Dict[str, pd.DataFrame] = {}
            for tf, df in all_data.items():
                if has_time and "time" in df.columns and current_time is not None:
                    end = self._time_to_idx(df, current_time)
                else:
                    # Aproximar por proporción de barras
                    ratio = len(df) / total_bars
                    end = max(1, int(i * ratio))
                if end > 0:
                    mtf_slice[tf] = df.iloc[:end].copy()

            # --- Evaluar señal ---
            signal = await self._call_signal(strategy, mtf_slice)
            score = signal.get("score", 0)
            signal_entry = signal.get("entry", 0)  # Las estrategias PST devuelven 1/-1 (dirección), no precio
            atr = signal.get("atr", 0.0)
            meta = signal.get("metadata", {}) or {}

            if score < self.score_threshold or signal_entry == 0 or atr <= 0:
                continue

            # Las estrategias PST codifican la dirección en el campo 'entry' (1=BUY, -1=SELL)
            # El precio real de entrada es el close de la barra actual
            direction = int(signal_entry)  # 1 = BUY, -1 = SELL
            entry = float(current_bar["close"])  # precio real de entrada

            sl_dist = sl_mult * atr
            tp_dist = tp_mult * atr
            sl_price = entry - direction * sl_dist
            tp_price = entry + direction * tp_dist

            # Validar R:R mínimo
            rr = tp_dist / sl_dist if sl_dist > 0 else 0
            if rr < 1.2:
                continue

            trade = BacktestTrade(
                entry_time=current_time,
                exit_time=None,
                symbol=symbol,
                strategy=strategy_name,
                direction=direction,
                entry_price=entry,
                sl_price=sl_price,
                tp_price=tp_price,
                score=abs(score),
                atr=atr,
            )
            result.trades.append(trade)
            open_trade = trade

            if len(result.trades) % 10 == 0:
                logger.info(f"  [{strategy_name}] {len(result.trades)} señales generadas hasta barra {i}...")

        # Cerrar trade que quedó abierto al final del periodo
        if open_trade is not None:
            last = primary_df.iloc[-1]
            last_close = float(last["close"])
            open_trade.exit_price = last_close
            open_trade.exit_time = period_end
            open_trade.result = "OPEN"
            sl_d = abs(open_trade.entry_price - open_trade.sl_price)
            if sl_d > 0:
                if open_trade.direction == 1:
                    open_trade.pnl_r = (last_close - open_trade.entry_price) / sl_d
                else:
                    open_trade.pnl_r = (open_trade.entry_price - last_close) / sl_d

        logger.info(
            f"[Backtest] {strategy_name}/{symbol} completado: "
            f"{len(result.closed_trades)} trades cerrados | "
            f"WR {result.win_rate:.1f}% | PF {result.profit_factor:.2f} | {result.total_r:.2f}R"
        )
        return result
