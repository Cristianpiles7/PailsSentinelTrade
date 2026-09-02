#!/usr/bin/env python3
"""
Script de humo — Fase 2 del rediseño de estrategias.
Corre PST-DivergenceReversion y PST-MomentumPulse (solo-reglas, sin ML todavía) a
través del PSTBacktestEngine GENÉRICO (sin modificar) sobre los símbolos piloto.
Objetivo: revisar cadencia de señales, distribución de scores y factors_detailed —
NO expectancy real (eso es la Fase 3, con costes y SL/TP propios de la estrategia).

Uso:
    python Tools/pst_ml_backtest_smoke.py --symbols US100.cash,US30.cash,XAUUSD,EURUSD --days 5
"""
import sys
import os
import argparse
import asyncio

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Tools.ml_data import get_full_mtf_data  # noqa: E402
from PST_Core.backtesting.engine import PSTBacktestEngine  # noqa: E402
from PST_Core.strategies.pst_divergence_reversion import PSTDivergenceReversion  # noqa: E402
from PST_Core.strategies.pst_momentum_pulse import PSTMomentumPulse  # noqa: E402

_TF_KEYS = ("m2", "m3", "m5", "m15", "m30", "h1", "h4")


async def run_for_symbol(sym: str, days: int, refresh: bool):
    data = get_full_mtf_data(sym, days, refresh)
    if not data:
        print(f"  SIN DATOS para {sym}, saltando.")
        return

    all_data = {tf: data[tf] for tf in _TF_KEYS if tf in data}
    # Recorte del TF más rápido para que el smoke test corra en tiempo razonable
    # (el motor genérico copia el slice completo en cada barra evaluada — O(n^2)
    # sobre el df entero sin recorte). Solo afecta a este script de diagnóstico.
    for tf, cap in (("m2", 1500), ("m3", 1200)):
        if tf in all_data and len(all_data[tf]) > cap:
            all_data[tf] = all_data[tf].iloc[-cap:].reset_index(drop=True)

    engine = PSTBacktestEngine(score_threshold=1)  # sin filtrar por score: queremos ver TODO lo que genera la estrategia

    for strat_cls in (PSTDivergenceReversion, PSTMomentumPulse):
        strat = strat_cls()
        res = await engine.run(strat, sym, all_data, warmup_bars=150, step_bars=3)

        scores = [t.score for t in res.trades]
        print(f"\n  --- {strat_cls.__name__} / {sym} ---")
        print(f"    señales generadas: {len(res.trades)}")
        if scores:
            import numpy as np
            print(f"    score: min={min(scores)} max={max(scores)} media={np.mean(scores):.1f} mediana={np.median(scores):.1f}")
            above_thr = sum(1 for s in scores if s >= strat.ENTRY_THRESHOLD)
            print(f"    señales con score >= ENTRY_THRESHOLD ({strat.ENTRY_THRESHOLD}): {above_thr}")
            print(f"    trades cerrados: {len(res.closed_trades)} | WR {res.win_rate:.1f}% | total_r {res.total_r:.2f}R (SL/TP genéricos ATR-mult, no el propio de la estrategia)")
            sample = res.trades[min(3, len(res.trades) - 1)]
            print(f"    ejemplo factors_detailed (trade #{min(3, len(res.trades)-1)}):")
        else:
            print("    (ninguna señal — revisar gates/umbral)")


async def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default="US100.cash,US30.cash,XAUUSD,EURUSD")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    for sym in [s.strip() for s in args.symbols.split(",")]:
        print(f"\n{'='*70}\n  {sym}\n{'='*70}")
        await run_for_symbol(sym, args.days, args.refresh)

    print("\nBacktest smoke test finalizado.\n")
    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
