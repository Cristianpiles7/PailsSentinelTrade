#!/usr/bin/env python3
"""
Script de humo — Corriente C del plan de expansión de estrategias (2026-08-12).
Verifica que PST-BreakoutScalping y PST-VolExpansion generan señales sin excepciones
sobre datos reales cacheados, con cadencia y distribución de scores razonables.
NO usa PSTBacktestEngine (evita depender de STRATEGY_TIMEFRAMES/DEFAULT_SL_MULT, que
no tienen entrada para estas 2 estrategias todavía) — llama a calculate_signal()
directamente, barra a barra sobre M5 (ambas estrategias resuelven su propio active_tf
internamente vía AdaptiveTFSelector a partir del dict mtf_data completo).

Igual que pst_ml_backtest_smoke.py (Fase 2): revisa cadencia de señales y
distribución de scores, NO expectancy real (eso requiere el motor de backtest fiel
de la Corriente A + costes, todavía no aplicable a estas 2 estrategias nuevas).

Uso:
    python Tools/pst_new_strategies_c_smoke.py --symbols US100.cash,US30.cash,XAUUSD,EURUSD --days 20
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
from PST_Core.strategies.pst_breakout_scalping import PSTBreakoutScalping  # noqa: E402
from PST_Core.strategies.pst_vol_expansion import PSTVolExpansion  # noqa: E402

_TF_KEYS = ("m2", "m3", "m5", "m15", "m30", "h1", "h4")


async def run_for_symbol(sym: str, days: int, refresh: bool, step: int, warmup: int):
    data = get_full_mtf_data(sym, days, refresh)
    if not data:
        print(f"  SIN DATOS para {sym}, saltando.")
        return

    full = {tf: data[tf] for tf in _TF_KEYS if tf in data}
    if "m5" not in full:
        print(f"  Sin M5 para {sym}, saltando.")
        return

    m5 = full["m5"]
    m5_times = m5["time"]
    n = len(m5)

    for strat_cls in (PSTBreakoutScalping, PSTVolExpansion):
        strat = strat_cls()
        scores, entries, errors, statuses = [], [], 0, {}

        for i in range(warmup, n, step):
            cutoff = m5_times.iloc[i]
            mtf_slice = {}
            for tf, df in full.items():
                end = int((df["time"] <= cutoff).sum())
                if end > 0:
                    mtf_slice[tf] = df.iloc[max(0, end - 400):end].reset_index(drop=True)
            if "m5" not in mtf_slice:
                continue
            try:
                sig = await strat.calculate_signal(mtf_slice, symbol=sym)
            except Exception as e:
                errors += 1
                if errors <= 3:
                    print(f"    [EXCEPCIÓN] barra {i}: {type(e).__name__}: {e}")
                continue
            score = sig.get("score", 0)
            entry = sig.get("entry", 0)
            scores.append(score)
            if entry != 0:
                entries.append((entry, score, sig.get("metadata", {}).get("active_tf")))
            status = (sig.get("metadata", {}) or {}).get("status", "")
            key = status.split("|")[0].strip() if "|" in status else status[:40]
            statuses[key] = statuses.get(key, 0) + 1

        print(f"\n  --- {strat_cls.__name__} / {sym} ---")
        print(f"    barras evaluadas: {len(scores)} | excepciones: {errors}")
        if scores:
            import numpy as np
            nz = [s for s in scores if s > 0]
            print(f"    score: min={min(scores)} max={max(scores)} media={np.mean(scores):.1f} (no-cero: {len(nz)})")
        print(f"    entradas (score >= ENTRY_THRESHOLD={strat.ENTRY_THRESHOLD}): {len(entries)}")
        if entries:
            buys = sum(1 for e in entries if e[0] == 1)
            sells = sum(1 for e in entries if e[0] == -1)
            tfs_used = {}
            for _, _, tf in entries:
                tfs_used[tf] = tfs_used.get(tf, 0) + 1
            print(f"    BUY={buys} SELL={sells} | active_tf usado: {tfs_used}")
        print(f"    razones de status (top 5): {sorted(statuses.items(), key=lambda kv: -kv[1])[:5]}")


async def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default="US100.cash,US30.cash,XAUUSD,EURUSD,BTCUSD")
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--step", type=int, default=3, help="Evaluar cada N barras M5 (por velocidad)")
    ap.add_argument("--warmup", type=int, default=150)
    args = ap.parse_args()

    for sym in [s.strip() for s in args.symbols.split(",")]:
        print(f"\n{'='*70}\n  {sym}\n{'='*70}")
        await run_for_symbol(sym, args.days, args.refresh, args.step, args.warmup)

    print("\nSmoke test de Corriente C finalizado.\n")
    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
