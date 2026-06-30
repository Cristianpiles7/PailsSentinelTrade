#!/usr/bin/env python3
"""
PST Backtesting & Walk-Forward Validation Tool
───────────────────────────────────────────────
Uso básico:
    python Tools/pst_backtest.py

Con opciones:
    python Tools/pst_backtest.py --symbols EURUSD,XAUUSD --days 180
    python Tools/pst_backtest.py --strategy scalping --days 90
    python Tools/pst_backtest.py --walk-forward --wf-windows 5
    python Tools/pst_backtest.py --symbols BTCUSD --strategy alpha --days 365 --walk-forward
"""
import sys
import os
import asyncio
import argparse
import logging
from datetime import datetime, timedelta

# Añadir raíz del proyecto al path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("PST-BacktestCLI")


def fetch_mt5_history(symbol: str, days: int) -> dict:
    """Descarga datos históricos de MT5 para todos los timeframes necesarios."""
    import MetaTrader5 as mt5
    import pandas as pd

    if not mt5.initialize():
        logger.error(f"No se pudo inicializar MT5: {mt5.last_error()}")
        sys.exit(1)

    end_dt   = datetime.now()
    start_dt = end_dt - timedelta(days=days)

    tf_map = {
        "m1":  mt5.TIMEFRAME_M1,
        "m5":  mt5.TIMEFRAME_M5,
        "m15": mt5.TIMEFRAME_M15,
        "h1":  mt5.TIMEFRAME_H1,
        "h4":  mt5.TIMEFRAME_H4,
    }

    all_data = {}
    for tf_name, tf_const in tf_map.items():
        rates = mt5.copy_rates_range(symbol, tf_const, start_dt, end_dt)
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            all_data[tf_name] = df
            logger.info(f"  {symbol} {tf_name:>4}: {len(df):>6} barras")
        else:
            logger.warning(f"  {symbol} {tf_name:>4}: sin datos")

    return all_data


async def run_backtest(args):
    from PST_Core.strategies.pst_alpha_trend import PSTAlphaTrend
    from PST_Core.strategies.pst_range_breaker import PSTRangeBreaker
    from PST_Core.strategies.pst_precision_scalping import PSTPrecisionScalping
    from PST_Core.backtesting.engine import PSTBacktestEngine
    from PST_Core.backtesting.walk_forward import WalkForwardValidator

    # Selección de estrategias
    all_strategies = {
        "alpha":    PSTAlphaTrend(),
        "range":    PSTRangeBreaker(),
        "scalping": PSTPrecisionScalping(),
    }

    if args.strategy:
        key = args.strategy.lower()
        if key not in all_strategies:
            logger.error(f"Estrategia '{args.strategy}' no reconocida. Usa: alpha, range, scalping")
            sys.exit(1)
        strategies = [all_strategies[key]]
    else:
        strategies = list(all_strategies.values())

    symbols = [s.strip() for s in args.symbols.split(",")]

    print(f"\n{'='*62}")
    print(f"  PST BACKTESTING ENGINE")
    print(f"  Símbolos:  {', '.join(symbols)}")
    print(f"  Historial: {args.days} días")
    print(f"  Threshold: {args.threshold} pts")
    print(f"  Modo:      {'Walk-Forward' if args.walk_forward else 'Backtest simple'}")
    print(f"{'='*62}")

    for symbol in symbols:
        logger.info(f"\nDescargando historial de {symbol}...")
        all_data = fetch_mt5_history(symbol, args.days)

        if not all_data:
            logger.error(f"Sin datos para {symbol}. ¿Está el símbolo disponible en MT5?")
            continue

        for strategy in strategies:
            strategy_name = strategy.STRATEGY_NAME
            tfs_needed = PSTBacktestEngine.STRATEGY_TIMEFRAMES.get(strategy_name, ["h1"])

            # Verificar que hay suficientes datos para los timeframes requeridos
            missing = [
                tf for tf in tfs_needed
                if tf not in all_data or len(all_data[tf]) < 50
            ]
            if missing:
                logger.warning(f"  {strategy_name}: faltan datos para {missing} — saltando")
                continue

            if args.walk_forward:
                print(f"\n[WALK-FORWARD] {strategy_name} | {symbol}")
                validator = WalkForwardValidator(
                    n_windows=args.wf_windows,
                    train_ratio=0.7,
                )
                windows = await validator.validate(
                    strategy, symbol, all_data, score_threshold=args.threshold
                )
                validator.print_report(windows)

            else:
                print(f"\n[BACKTEST] {strategy_name} | {symbol}")
                engine = PSTBacktestEngine(score_threshold=args.threshold)
                result = await engine.run(strategy, symbol, all_data)
                result.print_report()

    import MetaTrader5 as mt5
    mt5.shutdown()
    print("\nBacktest finalizado.")


def main():
    parser = argparse.ArgumentParser(
        description="PST Backtesting & Walk-Forward Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--symbols", default="EURUSD,XAUUSD,BTCUSD",
        help="Símbolos separados por coma (default: EURUSD,XAUUSD,BTCUSD)"
    )
    parser.add_argument(
        "--days", type=int, default=180,
        help="Días de historial a descargar (default: 180)"
    )
    parser.add_argument(
        "--strategy", default=None,
        help="Filtrar estrategia: alpha | range | scalping (default: todas)"
    )
    parser.add_argument(
        "--threshold", type=int, default=80,
        help="Score mínimo para considerar señal como entrada (default: 80)"
    )
    parser.add_argument(
        "--walk-forward", action="store_true",
        help="Ejecutar validación walk-forward en lugar de backtest simple"
    )
    parser.add_argument(
        "--wf-windows", type=int, default=5,
        help="Número de ventanas para walk-forward (default: 5)"
    )
    args = parser.parse_args()
    asyncio.run(run_backtest(args))


if __name__ == "__main__":
    main()
