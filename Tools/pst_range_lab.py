#!/usr/bin/env python3
"""
PST · Range LAB — juez fiel de PST-RangeBreaker
────────────────────────────────────────────────
Motor FaithfulRangeEngine: señal M15 con vela en formación, filtro macro, gate de
régimen H1, SL estructural/ATR del executor, TP técnico (BB media) salvo índices,
auto-fix min_rr, parcial 1R+BE, trailing, timeout live (~3h30 por offset de reloj),
cooldown 15 min, spread round-trip y comisión real por clase.

Uso:
    # Baseline fiel (config shippeada) en los símbolos activos
    python Tools/pst_range_lab.py --symbols GBPUSD,XAUUSD,BTCUSD,US100.cash,UK100.cash --days 20

    # A/B de palancas (una a la vez) contra el baseline en la misma corrida
    python Tools/pst_range_lab.py --symbols ... --days 20 --ab min_rr=1.0 --ab macro=off

    Palancas --ab: min_rr=<f> · macro=on|off · regime=on|off · tp=live|technical|atr
                   · timeout=<mins> (210 live / 30 nominal / 0 off) · threshold=<int>
                   · trailing=on|off · partial=on|off
"""
import sys
import os
import asyncio
import argparse
import pickle
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PST_Core.backtesting.faithful_range_engine import FaithfulRangeEngine  # noqa: E402
from PST_Core.strategies.pst_range_breaker import PSTRangeBreaker  # noqa: E402
from pst_bt_lab import metrics, aggregate, print_pair, verdict  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(REPO_ROOT, "Tools", ".bt_cache")

# Historial extra ANTES de la ventana para que los slices as-of tengan sus lookbacks
# completos desde la primera barra M1: h4 necesita 500 velas (~84d), h1 500 (~21d).
_TF_SPEC = {  # tf: (const_name, días_extra_de_historial)
    "m1": ("TIMEFRAME_M1", 0),
    "m5": ("TIMEFRAME_M5", 2),
    "m15": ("TIMEFRAME_M15", 4),
    "h1": ("TIMEFRAME_H1", 32),
    "h4": ("TIMEFRAME_H4", 125),
}


def get_data(symbol, days, refresh=False):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"range_{symbol}_{days}d.pkl")
    if os.path.exists(path) and not refresh:
        with open(path, "rb") as f:
            return pickle.load(f)

    import MetaTrader5 as mt5
    import pandas as pd
    if not mt5.initialize():
        print(f"    ⚠️  MT5 init falló: {mt5.last_error()}")
        return None
    # Símbolos fuera del Market Watch necesitan symbol_select antes de pedir histórico —
    # sin esto, copy_rates_range se cuelga/devuelve vacío indefinidamente (mismo bug que
    # pst_bt_lab.py, corregido en v2.6.4).
    mt5.symbol_select(symbol, True)
    info = mt5.symbol_info(symbol)
    if info is None:
        print(f"    ⚠️  {symbol}: symbol_info None")
        return None
    out = {
        "point": info.point,
        "spread_dist": info.spread * info.point,
        "tick_value": getattr(info, "trade_tick_value", None),
        "tick_size": getattr(info, "trade_tick_size", None),
    }
    end_dt = datetime.now()
    for tf, (const_name, extra) in _TF_SPEC.items():
        start_dt = end_dt - timedelta(days=days + extra)
        rates = mt5.copy_rates_range(symbol, getattr(mt5, const_name), start_dt, end_dt)
        if rates is None or len(rates) == 0:
            print(f"    {symbol} {tf}: sin datos")
            return None
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        out[tf] = df
        print(f"    {symbol} {tf:>3}: {len(df):>6} barras")
    with open(path, "wb") as f:
        pickle.dump(out, f)
    return out


def _parse_ab(specs):
    """['min_rr=1.0','macro=off'] → lista de (etiqueta, kwargs de FaithfulRangeEngine)."""
    variants = []
    for spec in specs or []:
        key, _, val = spec.partition("=")
        key = key.strip().lower()
        val = val.strip()
        if key == "min_rr":
            variants.append((f"min_rr={val}", {"min_rr": float(val)}))
        elif key == "macro":
            variants.append((f"macro={val}", {"macro_mode": val}))
        elif key == "regime":
            variants.append((f"regime={val}", {"regime_gate": val}))
        elif key == "tp":
            variants.append((f"tp={val}", {"tp_mode": val}))
        elif key == "timeout":
            variants.append((f"timeout={val}m", {"timeout_mins": int(val)}))
        elif key == "threshold":
            variants.append((f"threshold={val}", {"threshold": int(val)}))
        elif key == "trailing":
            variants.append((f"trailing={val}", {"trailing_enabled": val == "on"}))
        elif key == "partial":
            variants.append((f"partial={val}", {"partial_enabled": val == "on"}))
        else:
            raise SystemExit(f"Palanca --ab desconocida: {spec}")
    return variants


async def run_symbol(symbol, data, engine_kwargs):
    eng = FaithfulRangeEngine(**engine_kwargs)
    return await eng.run(PSTRangeBreaker(), symbol, data,
                         point=data.get("point"), spread_dist=data.get("spread_dist", 0.0))


def _trace(res, n):
    if n <= 0:
        return
    print(f"    · trazas ({min(n, len(res.trades))} de {len(res.trades)}):")
    for t in res.trades[:n]:
        dur = (t.exit_time - t.entry_time).total_seconds() / 60 if t.exit_time is not None else -1
        print(f"      {t.entry_time}  {('BUY ' if t.direction == 1 else 'SELL')} @ {t.entry_price:.5f} "
              f"SL {t.sl_price:.5f} TP {t.tp_price:.5f} -> {t.exit_reason or t.result} "
              f"({dur:.0f}m)  pnl {t.pnl_r:+.2f}R")


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default="GBPUSD,XAUUSD,BTCUSD,US100.cash,UK100.cash")
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--refresh", action="store_true", help="Fuerza re-descarga (ignora caché)")
    ap.add_argument("--trace", type=int, default=0, help="Imprime N trades por símbolo")
    ap.add_argument("--ab", action="append", default=None,
                    help="Variante A/B contra el baseline, p.ej. --ab min_rr=1.0 (repetible)")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    variants = _parse_ab(args.ab)

    print(f"\n{'#' * 66}\n#  PST RANGE LAB (motor fiel RangeBreaker)  |  días {args.days}\n"
          f"#  baseline: config shippeada (min_rr 1.8, macro on, tp live, timeout 210m)\n{'#' * 66}")

    base_all = []
    cand_all = {label: [] for label, _ in variants}
    for sym in symbols:
        print(f"\n  Datos {sym} ({args.days}d)...")
        data = get_data(sym, args.days, args.refresh)
        if not data:
            continue
        res = await run_symbol(sym, data, {})
        base_all.append(res)
        m = metrics(res)
        by_exit = {}
        for t in res.closed_trades:
            by_exit[t.exit_reason] = by_exit.get(t.exit_reason, 0) + 1
        print(f"  BASELINE {sym}: {m['trades']} trades | WR {m['win_rate']:.0f}% | "
              f"exp {m['expectancy']:+.3f}R | total {m['total_r']:+.1f}R | "
              f"PF {m['profit_factor']:.2f} | DD {m['max_dd']:.1f}R | salidas {by_exit}")
        _trace(res, args.trace)

        for label, kw in variants:
            vres = await run_symbol(sym, data, kw)
            cand_all[label].append(vres)
            vm = metrics(vres)
            d = vm["expectancy"] - m["expectancy"]
            print(f"    {label:<18} {vm['trades']:>4} trades | WR {vm['win_rate']:>3.0f}% | "
                  f"exp {vm['expectancy']:+.3f}R (Δ{d:+.3f}) | total {vm['total_r']:+.1f}R")

    ba = aggregate(base_all)
    if ba:
        print(f"\n{'=' * 66}\n  GLOBAL BASELINE: {ba['trades']} trades | WR {ba['win_rate']:.0f}% | "
              f"exp {ba['expectancy']:+.3f}R | total {ba['total_r']:+.1f}R | "
              f"PF {ba['profit_factor']:.2f} | sharpe {ba['sharpe']:.2f} | DD {ba['max_dd']:.1f}R")
        for label, _ in variants:
            ca = aggregate(cand_all[label])
            if ca:
                print_pair(f"GLOBAL {label}", ba, ca, "BASELINE", label[:13])
                ok, msg = verdict(ba, ca)
                print(f"  → {msg}")
    else:
        print("\n  Sin trades en el baseline.")

    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:
        pass
    print("\nRange Lab finalizado.\n")


if __name__ == "__main__":
    asyncio.run(main())
