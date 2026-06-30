"""
PST Walk-Forward Validator
Detecta overfitting comparando rendimiento en periodos de entrenamiento vs. validación.
Divide los datos en N ventanas rolling y mide consistencia entre train/test.
"""
import logging
import numpy as np
import pandas as pd
from typing import Dict, List
from dataclasses import dataclass

from .engine import PSTBacktestEngine, BacktestResult

logger = logging.getLogger("PST-WalkForward")


@dataclass
class WalkForwardWindow:
    window_idx: int
    train_result: BacktestResult
    test_result: BacktestResult

    def is_train_valid(self) -> bool:
        """El entrenamiento se considera válido si PF > 1.2 y al menos 5 trades."""
        return (
            self.train_result.profit_factor > 1.2
            and len(self.train_result.closed_trades) >= 5
        )

    def test_degradation(self) -> float:
        """Cuánto cae el Profit Factor entre train y test (menor = más robusto)."""
        train_pf = self.train_result.profit_factor
        test_pf = self.test_result.profit_factor
        if train_pf == float("inf") or train_pf == 0:
            return 0.0
        return (train_pf - test_pf) / train_pf * 100  # % de caída


class WalkForwardValidator:
    """
    Valida estrategias con el método Walk-Forward para detectar overfitting.

    Mecánica:
        - Divide los datos históricos en N ventanas solapadas
        - Cada ventana tiene: TRAIN (70%) + TEST (30%)
        - La ventana se desplaza en el tiempo → simula trading real
        - Si el rendimiento en TEST es consistentemente bueno, la estrategia es robusta
    """

    def __init__(self, n_windows: int = 5, train_ratio: float = 0.7):
        self.n_windows = n_windows
        self.train_ratio = train_ratio

    async def validate(
        self,
        strategy,
        symbol: str,
        all_data: Dict[str, pd.DataFrame],
        score_threshold: int = 80,
    ) -> List[WalkForwardWindow]:
        """Ejecuta la validación walk-forward completa."""
        strategy_name = strategy.STRATEGY_NAME
        tfs = PSTBacktestEngine.STRATEGY_TIMEFRAMES.get(strategy_name, ["h1"])
        primary_tf = tfs[0]
        primary_df = all_data.get(primary_tf)

        if primary_df is None:
            logger.error(f"[WF] Sin datos de {primary_tf} para {strategy_name}/{symbol}")
            return []

        n = len(primary_df)
        # Cada ventana cubre 2/N del total, solapándose con la siguiente
        window_size = n // max(self.n_windows, 2)

        if window_size < 300:
            logger.warning(
                f"[WF] Ventanas de solo {window_size} barras — considera más historial (recomendado: 6+ meses)"
            )

        results: List[WalkForwardWindow] = []
        logger.info(
            f"[WF] Iniciando: {strategy_name} | {symbol} | "
            f"{self.n_windows} ventanas de {window_size} barras"
        )

        for w in range(self.n_windows - 1):
            # Inicio y fin de la ventana combinada (train+test)
            win_start = w * window_size
            win_end = min((w + 2) * window_size, n)

            if win_end - win_start < 100:
                continue

            split = win_start + int((win_end - win_start) * self.train_ratio)

            def slice_tf(df: pd.DataFrame, start: int, end: int) -> pd.DataFrame:
                return df.iloc[start:end].reset_index(drop=True)

            train_data = {tf: slice_tf(df, win_start, split) for tf, df in all_data.items()}
            test_data  = {tf: slice_tf(df, split, win_end)   for tf, df in all_data.items()}

            engine = PSTBacktestEngine(score_threshold=score_threshold)

            logger.info(
                f"  [WF] Ventana {w+1}/{self.n_windows-1}: "
                f"TRAIN [{win_start}→{split}] | TEST [{split}→{win_end}]"
            )

            train_result = await engine.run(strategy, symbol, train_data)
            test_result  = await engine.run(strategy, symbol, test_data)

            window = WalkForwardWindow(
                window_idx=w + 1,
                train_result=train_result,
                test_result=test_result,
            )
            results.append(window)

            t_trades = len(train_result.closed_trades)
            v_trades = len(test_result.closed_trades)
            logger.info(
                f"    TRAIN → WR {train_result.win_rate:.1f}% | "
                f"PF {train_result.profit_factor:.2f} | {t_trades} trades | {train_result.total_r:.2f}R"
            )
            logger.info(
                f"    TEST  → WR {test_result.win_rate:.1f}% | "
                f"PF {test_result.profit_factor:.2f} | {v_trades} trades | {test_result.total_r:.2f}R"
            )

        return results

    def print_report(self, windows: List[WalkForwardWindow]):
        """Imprime el informe de walk-forward con veredicto final."""
        if not windows:
            print("Sin resultados de walk-forward.")
            return

        strategy = windows[0].train_result.strategy_name
        symbol   = windows[0].train_result.symbol

        print(f"\n{'='*62}")
        print(f"  WALK-FORWARD REPORT: {strategy} | {symbol}")
        print(f"{'='*62}")

        valid_windows  = 0
        total_test_trades = 0
        test_pfs = []

        for w in windows:
            status = "✅ VÁLIDO" if w.is_train_valid() else "❌ INVÁLIDO"
            degradation = w.test_degradation()
            dg_str = f"Degradación PF: {degradation:+.1f}%"
            print(f"\n  Ventana {w.window_idx} [{status}]  {dg_str}")
            print(
                f"    TRAIN: WR {w.train_result.win_rate:.1f}% | "
                f"PF {w.train_result.profit_factor:.2f} | "
                f"{len(w.train_result.closed_trades)} trades | {w.train_result.total_r:.2f}R"
            )
            print(
                f"    TEST:  WR {w.test_result.win_rate:.1f}% | "
                f"PF {w.test_result.profit_factor:.2f} | "
                f"{len(w.test_result.closed_trades)} trades | {w.test_result.total_r:.2f}R"
            )

            total_test_trades += len(w.test_result.closed_trades)
            if w.test_result.closed_trades:
                test_pfs.append(w.test_result.profit_factor)
            if w.is_train_valid():
                valid_windows += 1

        consistency = valid_windows / len(windows) * 100 if windows else 0

        print(f"\n{'─'*62}")
        print(f"  Ventanas válidas en TRAIN: {valid_windows}/{len(windows)}")
        print(f"  Trades totales en TEST:    {total_test_trades}")

        if test_pfs:
            print(f"  PF promedio en TEST:       {np.mean(test_pfs):.2f}")
            print(f"  PF mínimo en TEST:         {min(test_pfs):.2f}")

        if consistency >= 70:
            verdict = "✅ ROBUSTA — Rendimiento consistente en datos no vistos. APTA para live."
        elif consistency >= 50:
            verdict = "⚠️  MARGINAL — Rendimiento inconsistente. Revisar parámetros antes de live."
        else:
            verdict = "❌ INESTABLE — Posible overfitting. NO recomendada en capital real."

        print(f"\n  VEREDICTO: {verdict}")
        print(f"{'='*62}\n")

        return {
            "strategy": strategy,
            "symbol": symbol,
            "valid_windows": valid_windows,
            "total_windows": len(windows),
            "consistency_pct": consistency,
            "avg_test_pf": float(np.mean(test_pfs)) if test_pfs else 0.0,
            "verdict": verdict,
        }
