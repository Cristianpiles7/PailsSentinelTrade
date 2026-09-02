#!/usr/bin/env python3
"""
PST · Nueva Estrategia LAB — validación con costes reales de PST-MomentumPulse /
PST-DivergenceReversion
──────────────────────────────────────────────────────────────────────────────
Motor: PSTBacktestEngine GENÉRICO (no un motor fiel a medida) + cost_model.py
(comisión + spread de salida + swap/overnight) + SL/TP ESTRUCTURAL de la propia
estrategia (use_structural_sltp=True) en vez del proxy de ATR-múltiplo fijo.

Datos deterministas cacheados en Tools/.ml_cache/ (mismo caché que pst_ml_smoke.py
y pst_ml_backtest_smoke.py). Convenciones de CLI espejo de Tools/pst_bt_lab.py.

Uso:
    # Correr y ver métricas (modo default)
    python Tools/pst_new_strategy_lab.py --strategy PST-MomentumPulse \
        --symbols US100.cash,US30.cash,XAUUSD,EURUSD --days 20

    # Guardar baseline persistente
    python Tools/pst_new_strategy_lab.py --strategy PST-DivergenceReversion \
        --symbols XAUUSD,US30.cash --days 20 --save-baseline reversion_v1

    # Juzgar contra el baseline guardado (VERDE/ROJO)
    python Tools/pst_new_strategy_lab.py --strategy PST-DivergenceReversion \
        --symbols XAUUSD,US30.cash --days 20 --vs-baseline reversion_v1

    # Barrer ENTRY_THRESHOLD (único tunable expuesto hoy — no hay filter_profile
    # todavía para estas 2 estrategias) alrededor del valor actual de la clase
    python Tools/pst_new_strategy_lab.py --strategy PST-DivergenceReversion \
        --symbols XAUUSD --days 20 --sweep
"""
import sys
import os
import json
import argparse
import asyncio
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Tools.ml_data import get_full_mtf_data  # noqa: E402
from PST_Core.backtesting.engine import PSTBacktestEngine  # noqa: E402
from PST_Core.strategies.pst_momentum_pulse import PSTMomentumPulse  # noqa: E402
from PST_Core.strategies.pst_divergence_reversion import PSTDivergenceReversion  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_DIR = os.path.join(REPO_ROOT, "Tools", "bt_baselines")
TOL_EXP = 0.02
TOL_SHARPE = 0.10

STRATS = {"PST-MomentumPulse": PSTMomentumPulse, "PST-DivergenceReversion": PSTDivergenceReversion}
_TF_KEYS = ("m2", "m3", "m5", "m15", "m30", "h1", "h4")


def _capped(data):
    """Ya NO recorta nada: PSTBacktestEngine.run() usa una ventana deslizante
    ACOTADA (MTF_SLICE_LOOKBACK) por barra en vez de copiar df.iloc[:end] entero,
    así que el coste por barra es constante sin importar cuántas velas traiga cada
    timeframe. Se mantiene la función (nombre + firma) para no tocar las llamadas
    existentes; solo filtra a las claves de TF relevantes."""
    return {tf: data[tf] for tf in _TF_KEYS if tf in data}


async def run_variant(strat_cls, symbol, data, entry_threshold=None, step_bars=3):
    all_data = _capped(data)
    strat = strat_cls()
    if entry_threshold is not None:
        strat.ENTRY_THRESHOLD = entry_threshold
    engine = PSTBacktestEngine(score_threshold=1, model_costs=True,
                                use_structural_sltp=True, use_dynamic_exit=True)
    return await engine.run(
        strat, symbol, all_data, warmup_bars=150, step_bars=step_bars,
        spread_dist=data.get("spread_dist") or 0.0, point=data.get("point"),
        tick_value=data.get("tick_value"), tick_size=data.get("tick_size"),
        swap_long=data.get("swap_long") or 0.0, swap_short=data.get("swap_short") or 0.0,
    )


def metrics(res):
    ct = res.closed_trades
    exp = (sum(t.pnl_r for t in ct) / len(ct)) if ct else 0.0
    return {
        "trades": len(ct), "win_rate": res.win_rate, "profit_factor": res.profit_factor,
        "sharpe": res.sharpe_ratio, "sortino": res.sortino_ratio, "avg_rr": res.avg_rr_realized,
        "avg_win": res.avg_win_r, "avg_loss": res.avg_loss_r, "max_dd": res.max_drawdown_r,
        "total_r": res.total_r, "expectancy": exp,
    }


def verdict(base, cand):
    de = cand["expectancy"] - base["expectancy"]
    ds = cand["sharpe"] - base["sharpe"]
    ok = (de >= -TOL_EXP) and (ds >= -TOL_SHARPE)
    tag = "PASS" if ok else "FAIL"
    return ok, f"{tag}  (Δexpectancy {de:+.3f}, Δsharpe {ds:+.2f})"


def print_pair(symbol, base, cand, base_label, cand_label):
    print(f"\n{'='*66}\n  {symbol}   |   {base_label}  vs  {cand_label}\n{'='*66}")
    rows = [
        ("Trades", "trades"), ("Win Rate", "win_rate"), ("Profit Factor", "profit_factor"),
        ("Sharpe", "sharpe"), ("Sortino", "sortino"), ("Avg R:R realiz.", "avg_rr"),
        ("Max DD (R)", "max_dd"), ("Total R", "total_r"), ("Expectancy (R)", "expectancy"),
    ]
    for label, key in rows:
        o, n = base[key], cand[key]
        print(f"  {label:<18} {o:>10.2f} {n:>10.2f}")


def aggregate(results):
    ct = [t for r in results for t in r.closed_trades]
    if not ct:
        return None
    import numpy as np
    pnls = [t.pnl_r for t in ct]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    wr = len(wins) / len(ct) * 100
    gp, gl = sum(wins), abs(sum(losses))
    std = np.std(pnls, ddof=1) if len(ct) > 1 else 0.0
    sr = (np.mean(pnls) / std * np.sqrt(252)) if std > 0 else 0.0
    downside = [p for p in pnls if p < 0]
    dstd = np.sqrt(np.mean(np.square(downside))) if downside else 0.0
    sortino = (np.mean(pnls) / dstd * np.sqrt(252)) if dstd > 0 else 0.0
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    avg_rr = (avg_win / abs(avg_loss)) if (wins and losses) else 0.0
    eq = peak = mdd = 0.0
    for p in pnls:
        eq += p; peak = max(peak, eq); mdd = max(mdd, peak - eq)
    return {"trades": len(ct), "win_rate": wr, "profit_factor": (gp / gl if gl > 0 else float("inf")),
            "sharpe": sr, "sortino": sortino, "avg_rr": avg_rr, "avg_win": avg_win, "avg_loss": avg_loss,
            "max_dd": mdd, "total_r": sum(pnls), "expectancy": float(np.mean(pnls))}


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strategy", required=True, choices=list(STRATS.keys()))
    ap.add_argument("--symbols", default="US100.cash,US30.cash,XAUUSD,EURUSD")
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--save-baseline", default=None)
    ap.add_argument("--vs-baseline", default=None)
    ap.add_argument("--sweep", action="store_true",
                     help="Barre ENTRY_THRESHOLD ±5/±10 desde el valor actual de la clase (único "
                          "tunable expuesto hoy — no hay filter_profile todavía)")
    ap.add_argument("--sweep-min-trades", type=int, default=10)
    args = ap.parse_args()

    strat_cls = STRATS[args.strategy]
    symbols = [s.strip() for s in args.symbols.split(",")]

    if args.sweep:
        base_thr = strat_cls.ENTRY_THRESHOLD
        for sym in symbols:
            data = get_full_mtf_data(sym, args.days, args.refresh)
            if not data:
                print(f"  sin datos para {sym}, saltando."); continue
            print(f"\n{'='*70}\n  SWEEP {args.strategy} / {sym}  (ENTRY_THRESHOLD base {base_thr})\n{'='*70}")
            base_res = await run_variant(strat_cls, sym, data, base_thr)
            base_m = metrics(base_res)
            print(f"  BASE thr={base_thr}: {base_m['trades']} trades | exp {base_m['expectancy']:+.3f}R | sharpe {base_m['sharpe']:.2f}")
            for alt in sorted({base_thr - 10, base_thr - 5, base_thr + 5, base_thr + 10}):
                if not (10 <= alt <= 95) or alt == base_thr:
                    continue
                res = await run_variant(strat_cls, sym, data, alt)
                m = metrics(res)
                ok, _ = verdict(base_m, m)
                reliable = m["trades"] >= args.sweep_min_trades
                tag = "PASS" if (ok and reliable) else ("MUESTRA INSUFICIENTE" if not reliable else "FAIL")
                print(f"    thr={alt:>3}  trades={m['trades']:>4}  exp={m['expectancy']:+.3f}R  "
                      f"Δexp={m['expectancy']-base_m['expectancy']:+.3f}  {tag}")
        return

    if args.save_baseline:
        os.makedirs(BASELINE_DIR, exist_ok=True)
        store = {"strategy": args.strategy, "days": args.days, "created": str(datetime.now()), "symbols": {}}
        for sym in symbols:
            data = get_full_mtf_data(sym, args.days, args.refresh)
            if not data:
                continue
            res = await run_variant(strat_cls, sym, data)
            store["symbols"][sym] = metrics(res)
            print(f"  {sym}: {store['symbols'][sym]['trades']} trades | exp {store['symbols'][sym]['expectancy']:+.3f}R")
        path = os.path.join(BASELINE_DIR, f"{args.save_baseline}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
        print(f"\nBaseline guardado en {path}")
        return

    if args.vs_baseline:
        path = os.path.join(BASELINE_DIR, f"{args.vs_baseline}.json")
        with open(path, encoding="utf-8") as f:
            store = json.load(f)
        all_ok = True
        for sym in symbols:
            if sym not in store["symbols"]:
                continue
            data = get_full_mtf_data(sym, args.days, args.refresh)
            if not data:
                continue
            res = await run_variant(strat_cls, sym, data)
            cand = metrics(res)
            base = store["symbols"][sym]
            print_pair(sym, base, cand, f"BASE:{args.vs_baseline}", "WORKING")
            ok, msg = verdict(base, cand)
            all_ok = all_ok and ok
            print(f"  -> {msg}")
        print(f"\n{'='*66}\n  VEREDICTO GLOBAL: {'VERDE — sin regresion' if all_ok else 'ROJO — hay regresion'}\n{'='*66}")
        return

    # Modo default: correr y mostrar métricas por símbolo + agregado
    all_res = []
    for sym in symbols:
        data = get_full_mtf_data(sym, args.days, args.refresh)
        if not data:
            print(f"  sin datos para {sym}"); continue
        res = await run_variant(strat_cls, sym, data)
        all_res.append(res)
        m = metrics(res)
        print(f"\n{sym}: {m['trades']} trades | WR {m['win_rate']:.1f}% | PF {m['profit_factor']:.2f} | "
              f"sharpe {m['sharpe']:.2f} | exp {m['expectancy']:+.3f}R | total {m['total_r']:+.2f}R")
    agg = aggregate(all_res)
    if agg:
        print(f"\n{'='*66}\nGLOBAL: {agg['trades']} trades | WR {agg['win_rate']:.1f}% | PF {agg['profit_factor']:.2f} | "
              f"sharpe {agg['sharpe']:.2f} | exp {agg['expectancy']:+.3f}R | total {agg['total_r']:+.2f}R\n{'='*66}")

    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
