#!/usr/bin/env python3
"""
Prueba puntual de dos palancas del motor fiel que el sweep de pst_bt_lab.py no cubre:
  · SCALPER_PARTIAL_CLOSE_PCT (cuanto se cierra al llegar a 1R)
  · FaithfulScalpingEngine.TIMEOUT_SECS (minutos de estancamiento antes de cerrar)

Reutiliza SHIPPED_PROFILES/SYMBOL_PROFILE_OVERRIDES y la cache de Tools/.bt_cache/
de pst_bt_lab.py para comparar sobre las MISMAS barras. No toca disco/BBDD.
"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import PST_Core.backtesting.faithful_engine as fe
from pst_bt_lab import (
    SHIPPED_PROFILES, SYMBOL_PROFILE_OVERRIDES, SHIPPED_RISK,
    _get_group, get_data, load_working_tree, aggregate, run_variant, print_pair, verdict,
)

SYMBOLS = ["GBPUSD", "XAUUSD", "BTCUSD", "US500.cash", "GER40.cash", "EU50.cash",
           "UK100.cash", "AAPL", "MSFT", "US100.cash", "US30.cash"]
DAYS = 20
THRESHOLD = 72
LOOKBACK_M1 = 200


def _shipped_profile_for(symbol):
    group = _get_group(symbol)
    prof = dict(SHIPPED_PROFILES.get(group, {"noise_mode": "on", "entry_threshold": 72}))
    prof.update(SYMBOL_PROFILE_OVERRIDES.get(symbol, {}))
    return {"filter_profile": prof, **SHIPPED_RISK}


async def run_all(label):
    StratCls = load_working_tree()
    results = []
    for sym in SYMBOLS:
        data = get_data(sym, DAYS)
        if data is None:
            print(f"  {sym}: sin datos, se omite")
            continue
        res = await run_variant(StratCls, sym, data, THRESHOLD, LOOKBACK_M1, _shipped_profile_for(sym))
        results.append(res)
    agg = aggregate(results)
    print(f"\n[{label}] trades={agg['trades']} expectancy={agg['expectancy']:+.3f}R "
          f"sharpe={agg['sharpe']:.2f} avg_win={agg['avg_win']:+.3f} avg_loss={agg['avg_loss']:+.3f} "
          f"avg_rr={agg['avg_rr']:.2f} winrate={agg['win_rate']:.1f}%")
    return agg


async def main():
    print("=" * 70)
    print("BASELINE (shippeado: partial_pct=0.50, timeout=30min)")
    print("=" * 70)
    base = await run_all("BASELINE")

    variants = [
        ("partial_pct=0.30 (deja correr mas tamano)", "partial_pct", 0.30),
        ("partial_pct=0.70 (banca mas pronto)", "partial_pct", 0.70),
        ("partial_close OFF (sin banca parcial)", "partial_off", None),
        ("timeout=60min (deja correr mas tiempo)", "timeout_min", 60),
        ("timeout=15min (corta antes)", "timeout_min", 15),
    ]

    for label, kind, val in variants:
        # reset a defaults
        fe.SCALPER_PARTIAL_CLOSE_PCT = 0.50
        fe.SCALPER_PARTIAL_CLOSE_ENABLED = True
        fe.FaithfulScalpingEngine.TIMEOUT_SECS = 30 * 60

        if kind == "partial_pct":
            fe.SCALPER_PARTIAL_CLOSE_PCT = val
        elif kind == "partial_off":
            fe.SCALPER_PARTIAL_CLOSE_ENABLED = False
        elif kind == "timeout_min":
            fe.FaithfulScalpingEngine.TIMEOUT_SECS = val * 60

        print("\n" + "=" * 70)
        print(f"CANDIDATO: {label}")
        print("=" * 70)
        cand = await run_all(label)
        ok, msg = verdict(base, cand)
        print(f"  -> {msg}")

    # reset final
    fe.SCALPER_PARTIAL_CLOSE_PCT = 0.50
    fe.SCALPER_PARTIAL_CLOSE_ENABLED = True
    fe.FaithfulScalpingEngine.TIMEOUT_SECS = 30 * 60


if __name__ == "__main__":
    asyncio.run(main())
