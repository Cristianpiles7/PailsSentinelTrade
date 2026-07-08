#!/usr/bin/env python3
"""
Combina las palancas ganadoras del sweep (entry_threshold=76 y/o m1_eff_mode=on,
segun lo que cada simbolo mostro individualmente) y compara el bundle contra el
perfil shippeado actual, simbolo por simbolo y en agregado. Usa la misma cache
de Tools/.bt_cache/ que pst_bt_lab.py.
"""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pst_bt_lab import (
    SHIPPED_PROFILES, SYMBOL_PROFILE_OVERRIDES, SHIPPED_RISK,
    _get_group, get_data, load_working_tree, aggregate, run_variant, verdict, metrics,
)

DAYS = 20
THRESHOLD = 72
LOOKBACK_M1 = 200

# Combo candidato por simbolo, basado en los PASS individuales y fiables del sweep.
# None = mantener shippeado (sin lever ganador fiable).
CANDIDATE_OVERRIDES = {
    "GBPUSD":     {"entry_threshold": 76},
    "XAUUSD":     {"entry_threshold": 76},
    "BTCUSD":     {"entry_threshold": 76, "m1_eff_mode": "on"},
    "US500.cash": None,  # ya en su optimo (entry_threshold=68)
    "GER40.cash": {"m1_eff_mode": "on", "entry_threshold": 76},
    "EU50.cash":  {"entry_threshold": 76},
    "UK100.cash": {"m1_eff_mode": "on", "entry_threshold": 76},
    "AAPL":       None,
    "MSFT":       None,
    "US100.cash": {"entry_threshold": 76},
    "US30.cash":  {"entry_threshold": 76},  # m1_eff_mode ya shippeado on
}


def _shipped_profile_for(symbol):
    group = _get_group(symbol)
    prof = dict(SHIPPED_PROFILES.get(group, {"noise_mode": "on", "entry_threshold": 72}))
    prof.update(SYMBOL_PROFILE_OVERRIDES.get(symbol, {}))
    return prof


def _candidate_profile_for(symbol):
    prof = _shipped_profile_for(symbol)
    override = CANDIDATE_OVERRIDES.get(symbol)
    if override:
        prof.update(override)
    return prof


async def run_all(profile_fn):
    StratCls = load_working_tree()
    results = {}
    for sym in CANDIDATE_OVERRIDES:
        data = get_data(sym, DAYS)
        if data is None:
            continue
        prof = {"filter_profile": profile_fn(sym), **SHIPPED_RISK}
        res = await run_variant(StratCls, sym, data, THRESHOLD, LOOKBACK_M1, prof)
        results[sym] = res
    return results


async def main():
    base_res = await run_all(_shipped_profile_for)
    cand_res = await run_all(_candidate_profile_for)

    print("\n" + "=" * 78)
    print(f"{'simbolo':<14}{'base exp':>10}{'cand exp':>10}{'delta':>9}{'base n':>8}{'cand n':>8}")
    print("-" * 78)
    for sym in CANDIDATE_OVERRIDES:
        if sym not in base_res or sym not in cand_res:
            continue
        bm, cm = metrics(base_res[sym]), metrics(cand_res[sym])
        d = cm["expectancy"] - bm["expectancy"]
        flag = "->" if CANDIDATE_OVERRIDES[sym] else "  "
        print(f"{flag}{sym:<12}{bm['expectancy']:>10.3f}{cm['expectancy']:>10.3f}{d:>+9.3f}{bm['trades']:>8}{cm['trades']:>8}")

    base_agg = aggregate(list(base_res.values()))
    cand_agg = aggregate(list(cand_res.values()))
    print("\n" + "=" * 78)
    print(f"AGREGADO  base: trades={base_agg['trades']} exp={base_agg['expectancy']:+.3f}R sharpe={base_agg['sharpe']:.2f} winrate={base_agg['win_rate']:.1f}%")
    print(f"AGREGADO  cand: trades={cand_agg['trades']} exp={cand_agg['expectancy']:+.3f}R sharpe={cand_agg['sharpe']:.2f} winrate={cand_agg['win_rate']:.1f}%")
    ok, msg = verdict(base_agg, cand_agg)
    print(f"\n  -> {msg}")


if __name__ == "__main__":
    asyncio.run(main())
