#!/usr/bin/env python3
"""Traza trade a trade de PST-MomentumPulse / PST-DivergenceReversion (diagnostico
puntual de resultados sospechosos del juez pst_new_strategy_lab.py)."""
import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pst_new_strategy_lab import run_variant, STRATS  # noqa: E402
from ml_data import get_full_mtf_data  # noqa: E402


async def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True, choices=list(STRATS.keys()))
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--days", type=int, default=20)
    args = ap.parse_args()

    data = get_full_mtf_data(args.symbol, args.days)
    if not data:
        print("sin datos"); return
    res = await run_variant(STRATS[args.strategy], args.symbol, data)
    print(f"{args.strategy} / {args.symbol}: {len(res.closed_trades)} trades cerrados\n")
    for t in res.closed_trades:
        dur = (t.exit_time - t.entry_time) if (t.exit_time and t.entry_time) else None
        print(f"  {('BUY ' if t.direction==1 else 'SELL')} entry={t.entry_price:.5f} sl={t.sl_price:.5f} "
              f"tp={t.tp_price:.5f}  exit={t.exit_price:.5f} ({t.exit_reason or t.result})  "
              f"pnl={t.pnl_r:+.3f}R  dur={dur}  entry_t={t.entry_time}")

    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
