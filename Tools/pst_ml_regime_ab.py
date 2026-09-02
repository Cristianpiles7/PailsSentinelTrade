#!/usr/bin/env python3
"""
A/B del bug de régimen VOLATILE (orchestrator.py, ver PST_Core/backtesting/faithful_ml_engine.py
HALLAZGO CRÍTICO #2 en su docstring): en producción, MomentumPulse (TREND) y
DivergenceReversion (RANGE) NUNCA operan cuando el régimen H1 es VOLATILE, pese a que
el código comenta que deberían ("tendencia Y volátil" / "rango Y volátil"). Este script
mide, con el motor fiel, cuánta señal se pierde por ese bug: compara
regime_volatile_bug_faithful=True (comportamiento REAL de hoy) vs False (diseño
pretendido, VOLATILE incluido) manteniendo todo lo demás constante (misma seed,
mismo macro filter, mismo motor de costes).

NO toca ningún archivo de producción — solo mide con datos ya cacheados.

Uso:
    python Tools/pst_ml_regime_ab.py --symbols US100.cash,US30.cash,XAUUSD,EURUSD --days 20
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
from PST_Core.backtesting.faithful_ml_engine import FaithfulMLEngine  # noqa: E402
from PST_Core.strategies.pst_momentum_pulse import PSTMomentumPulse  # noqa: E402
from PST_Core.strategies.pst_divergence_reversion import PSTDivergenceReversion  # noqa: E402

STRATS = {"PST-MomentumPulse": PSTMomentumPulse, "PST-DivergenceReversion": PSTDivergenceReversion}


async def run_variant(strat_cls, symbol, data, bug_faithful: bool):
    # NOTA (post-fix 2026-08-13): este script ya cumplió su propósito — el fix
    # asimétrico que sugirió (TREND incluye VOLATILE, RANGE no) está aplicado en
    # PST_Core/engine/orchestrator.py y es hoy el comportamiento "fixed" (default)
    # de FaithfulMLEngine. Se deja funcional, apuntando explícitamente a las
    # políticas históricas para poder reproducir esta medición si hace falta.
    policy = "pre_fix_bug" if bug_faithful else "symmetric"
    engine = FaithfulMLEngine(regime_volatile_policy=policy)
    strat = strat_cls()
    return await engine.run(strat, symbol, data)


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default="US100.cash,US30.cash,XAUUSD,EURUSD")
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]

    pool = {name: {"real": {"n": 0, "r": 0.0, "w": 0}, "design": {"n": 0, "r": 0.0, "w": 0}} for name in STRATS}

    for sym in symbols:
        data = get_full_mtf_data(sym, args.days, args.refresh)
        if not data:
            print(f"SIN DATOS cacheados para {sym} ({args.days}d) — saltando.")
            continue

        print(f"\n{'='*78}\n  {sym}\n{'='*78}")
        for strat_name, strat_cls in STRATS.items():
            res_real = await run_variant(strat_cls, sym, data, bug_faithful=True)
            res_design = await run_variant(strat_cls, sym, data, bug_faithful=False)

            ct_real, ct_design = res_real.closed_trades, res_design.closed_trades
            n_real, n_design = len(ct_real), len(ct_design)
            r_real, r_design = res_real.total_r, res_design.total_r
            wr_real = res_real.win_rate
            wr_design = res_design.win_rate

            extra_trades = n_design - n_real
            extra_r = r_design - r_real

            print(f"\n  --- {strat_name} / {sym} ---")
            print(f"    REAL (bug, sin VOLATILE):    {n_real:3d} trades | WR {wr_real:5.1f}% | total_r {r_real:+7.2f}R | PF {res_real.profit_factor:.2f}")
            print(f"    DISEÑO (con VOLATILE):       {n_design:3d} trades | WR {wr_design:5.1f}% | total_r {r_design:+7.2f}R | PF {res_design.profit_factor:.2f}")
            print(f"    DIFERENCIA (solo trades en VOLATILE): {extra_trades:+3d} trades | {extra_r:+7.2f}R")

            pool[strat_name]["real"]["n"] += n_real
            pool[strat_name]["real"]["r"] += r_real
            pool[strat_name]["real"]["w"] += sum(1 for t in ct_real if t.result == "WIN")
            pool[strat_name]["design"]["n"] += n_design
            pool[strat_name]["design"]["r"] += r_design
            pool[strat_name]["design"]["w"] += sum(1 for t in ct_design if t.result == "WIN")

    print(f"\n{'='*78}\n  POOL AGREGADO (todos los símbolos)\n{'='*78}")
    for strat_name, agg in pool.items():
        r_n, r_r, r_w = agg["real"]["n"], agg["real"]["r"], agg["real"]["w"]
        d_n, d_r, d_w = agg["design"]["n"], agg["design"]["r"], agg["design"]["w"]
        r_exp = r_r / r_n if r_n else 0.0
        d_exp = d_r / d_n if d_n else 0.0
        extra_n, extra_r = d_n - r_n, d_r - r_r
        extra_exp = (extra_r / extra_n) if extra_n else 0.0
        print(f"\n  {strat_name}:")
        print(f"    REAL (bug):    {r_n:3d} trades | WR {(100*r_w/r_n if r_n else 0):5.1f}% | total {r_r:+7.2f}R | exp/trade {r_exp:+.3f}R")
        print(f"    DISEÑO:        {d_n:3d} trades | WR {(100*d_w/d_n if d_n else 0):5.1f}% | total {d_r:+7.2f}R | exp/trade {d_exp:+.3f}R")
        print(f"    Trades SOLO-VOLATILE que el bug bloquea hoy: {extra_n:+3d} ({100*extra_n/max(r_n,1):.0f}% más frecuencia) | "
              f"{extra_r:+7.2f}R | expectancy de esos trades: {extra_exp:+.3f}R/trade")

    print()


if __name__ == "__main__":
    asyncio.run(main())
