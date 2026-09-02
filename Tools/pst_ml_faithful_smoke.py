#!/usr/bin/env python3
"""
Script de humo — FaithfulMLEngine (motor fiel de PST-MomentumPulse / PST-DivergenceReversion).
Carga datos cacheados en Tools/.ml_cache/, corre el motor sobre ambas estrategias y
NO revienta. Esto NO es todavía el juicio fiel con baseline (tarea siguiente del
plan, extendiendo Tools/pst_new_strategy_lab.py) — solo confirma que el motor corre
de punta a punta sin excepciones e imprime result.summary().

Uso:
    python Tools/pst_ml_faithful_smoke.py --symbols US100.cash,US30.cash,XAUUSD,EURUSD --days 20
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


async def run_one(strat_name, strat_cls, symbol, data):
    engine = FaithfulMLEngine()
    strat = strat_cls()
    res = await engine.run(strat, symbol, data)
    print(f"\n  --- {strat_name} / {symbol} ---")
    for k, v in res.summary().items():
        print(f"    {k:<18} {v}")
    if res.trades:
        sample = res.trades[0]
        reasons = {}
        for t in res.trades:
            reasons[t.exit_reason or t.result] = reasons.get(t.exit_reason or t.result, 0) + 1
        print(f"    exit_reasons       {reasons}")
        print(f"    ejemplo trade      {'BUY' if sample.direction==1 else 'SELL'} "
              f"entry={sample.entry_price:.5f} sl={sample.sl_price:.5f} tp={sample.tp_price:.5f} "
              f"exit={sample.exit_price} ({sample.exit_reason or sample.result}) pnl={sample.pnl_r:+.3f}R")
    return res


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default="US100.cash,US30.cash,XAUUSD,EURUSD")
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    n_ok = 0
    n_fail = 0

    for sym in symbols:
        print(f"\n{'='*70}\n  {sym}\n{'='*70}")
        data = get_full_mtf_data(sym, args.days, args.refresh)
        if not data:
            print(f"  SIN DATOS cacheados para {sym} ({args.days}d) — saltando.")
            continue

        for strat_name, strat_cls in STRATS.items():
            try:
                await run_one(strat_name, strat_cls, sym, data)
                n_ok += 1
            except Exception as e:
                n_fail += 1
                import traceback
                print(f"    [FAIL] {strat_name}/{sym}: {e}")
                traceback.print_exc()

    print(f"\n{'='*70}\nSmoke test finalizado: {n_ok} corridas OK, {n_fail} con excepción.\n{'='*70}")

    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:
        pass

    if n_fail:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
