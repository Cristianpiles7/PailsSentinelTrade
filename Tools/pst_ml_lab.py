#!/usr/bin/env python3
"""
PST · ML LAB — juez fiel de PST-MomentumPulse / PST-DivergenceReversion
─────────────────────────────────────────────────────────────────────────
Motor: FaithfulMLEngine (PST_Core/backtesting/faithful_ml_engine.py) — réplica
de la doble capa orquestador→executor (SL/TP estructural + fallback ATR,
breakeven, trailing, cierre parcial a 1R, excepción de timeout TREND, filtro
macro + gate de régimen H1 YA CORREGIDO el 2026-08-13 — ver
PST_Core/engine/orchestrator.py y Tools/pst_ml_regime_ab.py, cooldown tras
pérdida, costes reales vía cost_model.py). NO el motor genérico
PSTBacktestEngine que usaba Tools/pst_new_strategy_lab.py (Fase 3, superficial
a propósito) — este es el equivalente para el sistema ML nuevo de
Tools/pst_bt_lab.py (PrecisionScalping) y Tools/pst_range_lab.py (RangeBreaker).

Datos: cacheados vía Tools/ml_data.py (Tools/.ml_cache/) — mismo caché que los
smoke tests de la Corriente ML, determinista, MT5 solo en la primera descarga.

Único tunable expuesto hoy por las estrategias: ENTRY_THRESHOLD (no hay
filter_profile por símbolo todavía, ver manual_docs/ESTRATEGIAS_NUEVO_SISTEMA.md
"Qué NO hacen (todavía)"). El --sweep barre esa única palanca; los flags de
FaithfulMLEngine (--index-technical-tp, --macro-mode, --regime-gate,
--timeout-mins) quedan disponibles para investigaciones puntuales tipo
Tools/pst_ml_regime_ab.py, con los defaults = producción fiel.

IMPORTANTE (disciplina de este proyecto, ver memoria strategy_exploration_lessons):
este script NO hace IS/OOS automático. Correrlo con >=2 ventanas de --days
independientes (o --days-oos) y exigir que un "ganador" del sweep mejore en
AMBAS antes de promoverlo a producción — igual que se hace con pst_bt_lab.py.

Uso:
    # Baseline rápido (sin comparar, solo reporta métricas actuales)
    python Tools/pst_ml_lab.py --strategy PST-MomentumPulse --symbols US100.cash,US30.cash,XAUUSD,EURUSD --days 20

    # Guardar el estado actual (working tree) como baseline persistente
    python Tools/pst_ml_lab.py --strategy PST-MomentumPulse --symbols US100.cash,US30.cash,XAUUSD,EURUSD --days 20 --save-baseline momentum_v1

    # Juzgar el working tree contra un baseline guardado (VERDE/ROJO) — guardarraíl anti-regresión
    python Tools/pst_ml_lab.py --strategy PST-MomentumPulse --symbols US100.cash,US30.cash,XAUUSD,EURUSD --days 20 --vs-baseline momentum_v1

    # Sweep de ENTRY_THRESHOLD (única palanca expuesta hoy), con veredicto por símbolo Y pooled
    python Tools/pst_ml_lab.py --strategy PST-DivergenceReversion --symbols US100.cash,US30.cash,XAUUSD,EURUSD --days 20 --sweep
"""
import sys
import os
import json
import asyncio
import argparse
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Tools.ml_data import get_full_mtf_data  # noqa: E402
from PST_Core.backtesting.faithful_ml_engine import FaithfulMLEngine  # noqa: E402
from PST_Core.strategies.pst_momentum_pulse import PSTMomentumPulse  # noqa: E402
from PST_Core.strategies.pst_divergence_reversion import PSTDivergenceReversion  # noqa: E402
from PST_Core.strategies.pst_breakout_scalping import PSTBreakoutScalping  # noqa: E402
from PST_Core.strategies.pst_vol_expansion import PSTVolExpansion  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_DIR = os.path.join(REPO_ROOT, "Tools", "bt_baselines")

# BreakoutScalping/VolExpansion (Corriente C) no tienen seed real en symbol_strategies
# todavía (no están activadas) — FaithfulMLEngine cae a _DEFAULT_SEED para ellas, con
# aviso explícito en el log. Resultados con estas dos son EXPLORATORIOS: los multiplicadores
# de riesgo no están calibrados/validados, a diferencia de MomentumPulse/DivergenceReversion.
STRATS = {
    "PST-MomentumPulse": PSTMomentumPulse, "PST-DivergenceReversion": PSTDivergenceReversion,
    "PST-BreakoutScalping": PSTBreakoutScalping, "PST-VolExpansion": PSTVolExpansion,
}

TOL_EXP = 0.02      # tolerancia de expectancy para el veredicto (igual que pst_bt_lab.py)
TOL_SHARPE = 0.10


# ─────────────────────────────────────────── datos (cache determinista)
def get_data(symbol, days, refresh=False):
    data = get_full_mtf_data(symbol, days, refresh)
    if not data:
        print(f"    ⚠️  sin datos cacheados/MT5 para {symbol} ({days}d)")
    return data


# ─────────────────────────────────────────── ejecución
async def run_variant(strat_cls, symbol, data, entry_threshold=None, engine_kwargs=None):
    strat = strat_cls()
    if entry_threshold is not None:
        strat.ENTRY_THRESHOLD = entry_threshold  # override de instancia, no toca la clase
    eng = FaithfulMLEngine(**(engine_kwargs or {}))
    return await eng.run(strat, symbol, data)


# ─────────────────────────────────────────── métricas (mismo shape que pst_bt_lab.py)
def metrics(res):
    ct = res.closed_trades
    exp = (sum(t.pnl_r for t in ct) / len(ct)) if ct else 0.0
    return {
        "trades": len(ct), "win_rate": res.win_rate, "profit_factor": res.profit_factor,
        "sharpe": res.sharpe_ratio, "sortino": res.sortino_ratio, "avg_rr": res.avg_rr_realized,
        "avg_win": res.avg_win_r, "avg_loss": res.avg_loss_r, "max_dd": res.max_drawdown_r,
        "total_r": res.total_r, "expectancy": exp,
    }


def aggregate(results):
    ct = [t for r in results for t in r.closed_trades]
    if not ct:
        return None
    import numpy as np
    pnls = [t.pnl_r for t in ct]
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p < 0]
    wr = len(wins) / len(ct) * 100
    gp = sum(wins); gl = abs(sum(losses))
    std = np.std(pnls, ddof=1) if len(ct) > 1 else 0.0
    sr = (np.mean(pnls) / std * np.sqrt(252)) if std > 0 else 0.0
    downside = [p for p in pnls if p < 0]
    dstd = np.sqrt(np.mean(np.square(downside))) if downside else 0.0
    sortino = (np.mean(pnls) / dstd * np.sqrt(252)) if dstd > 0 else 0.0
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    avg_rr = (avg_win / abs(avg_loss)) if (wins and losses) else 0.0
    eq, peak, mdd = 0.0, 0.0, 0.0
    for p in pnls:
        eq += p; peak = max(peak, eq); mdd = max(mdd, peak - eq)
    return {"trades": len(ct), "win_rate": wr, "profit_factor": (gp / gl if gl > 0 else float("inf")),
            "sharpe": sr, "sortino": sortino, "avg_rr": avg_rr, "avg_win": avg_win, "avg_loss": avg_loss,
            "max_dd": mdd, "total_r": sum(pnls), "expectancy": float(np.mean(pnls))}


def verdict(base, cand):
    de = cand["expectancy"] - base["expectancy"]
    ds = cand["sharpe"] - base["sharpe"]
    ok = (de >= -TOL_EXP) and (ds >= -TOL_SHARPE)
    tag = "✅ PASS" if ok else "❌ FAIL"
    return ok, f"{tag}  (Δexpectancy {de:+.3f}, Δsharpe {ds:+.2f})"


def _fmt(v, pct=False):
    if isinstance(v, float) and v == float("inf"):
        return "inf"
    return f"{v:.1f}%" if pct else f"{v:.2f}"


def print_pair(label, base, cand, base_label, cand_label):
    print(f"\n{'='*66}\n  {label}   |   {base_label}  vs  {cand_label}\n{'='*66}")
    print(f"  {'métrica':<18} {base_label:>13} {cand_label:>13}   Δ")
    print(f"  {'-'*60}")
    rows = [
        ("Trades", "trades", False, "high"), ("Win Rate", "win_rate", True, "high"),
        ("Profit Factor", "profit_factor", False, "high"), ("Sharpe", "sharpe", False, "high"),
        ("Sortino", "sortino", False, "high"), ("Avg R:R realiz.", "avg_rr", False, "high"),
        ("Avg Win (R)", "avg_win", False, "high"), ("Avg Loss (R)", "avg_loss", False, "high"),
        ("Max DD (R)", "max_dd", False, "low"), ("Total R", "total_r", False, "high"),
        ("Expectancy (R)", "expectancy", False, "high"),
    ]
    for lbl, key, pct, better in rows:
        o, n = base[key], cand[key]
        try:
            d = n - o
            good = (d > 0) if better == "high" else (d < 0)
            arrow = "▲" if abs(d) > 1e-9 and good else ("▼" if abs(d) > 1e-9 else "→")
            delta = f"{d:+.2f}"
        except Exception:
            arrow, delta = "→", ""
        print(f"  {lbl:<18} {_fmt(o, pct):>13} {_fmt(n, pct):>13}   {arrow} {delta}")


# ─────────────────────────────────────────── sweep de ENTRY_THRESHOLD
def _threshold_candidates(default_thr: int):
    steps = [-15, -10, -5, 5, 10, 15]
    return sorted({max(10, min(90, default_thr + s)) for s in steps} - {default_thr})


async def run_sweep(strat_cls, symbols, data_by_symbol, default_thr, engine_kwargs, sweep_min_trades):
    candidates = _threshold_candidates(default_thr)

    per_symbol_base, per_symbol_cands = {}, {}
    for sym in symbols:
        data = data_by_symbol.get(sym)
        if not data:
            continue
        per_symbol_base[sym] = await run_variant(strat_cls, sym, data, default_thr, engine_kwargs)
        per_symbol_cands[sym] = {}
        for thr in candidates:
            per_symbol_cands[sym][thr] = await run_variant(strat_cls, sym, data, thr, engine_kwargs)

    # --- tabla por símbolo ---
    for sym, base_res in per_symbol_base.items():
        base_m = metrics(base_res)
        print(f"\n{'='*70}\n  SWEEP {sym} (ENTRY_THRESHOLD, default={default_thr})\n{'='*70}")
        print(f"  BASELINE (thr={default_thr}): {base_m['trades']} trades | exp {base_m['expectancy']:+.3f}R | "
              f"sharpe {base_m['sharpe']:.2f} | DD {base_m['max_dd']:.1f}R")
        print(f"\n  {'thr':>6}{'trades':>8}{'expR':>9}{'Δexp':>9}{'Δsharpe':>10}   veredicto")
        for thr in candidates:
            m = metrics(per_symbol_cands[sym][thr])
            ok, _ = verdict(base_m, m)
            reliable = m["trades"] >= sweep_min_trades
            tag = "⚪ MUESTRA INSUFICIENTE" if not reliable else ("✅ PASS" if ok else "❌ FAIL")
            dexp = m["expectancy"] - base_m["expectancy"]
            dsharpe = m["sharpe"] - base_m["sharpe"]
            print(f"  {thr:>6}{m['trades']:>8}{m['expectancy']:>+9.3f}{dexp:>+9.3f}{dsharpe:>+10.2f}   {tag}")

    # --- pooled (agrega todos los símbolos por umbral — imprescindible para
    # DivergenceReversion, cuya muestra por símbolo suele ser demasiado pequeña) ---
    print(f"\n{'='*70}\n  POOL AGREGADO (todos los símbolos, por umbral)\n{'='*70}")
    pooled_base = aggregate(list(per_symbol_base.values()))
    if not pooled_base:
        print("  Sin trades en el pool baseline — sweep no concluyente.")
        return
    print(f"  BASELINE (thr={default_thr}): {pooled_base['trades']} trades | exp {pooled_base['expectancy']:+.3f}R | "
          f"sharpe {pooled_base['sharpe']:.2f} | PF {pooled_base['profit_factor']:.2f}")
    print(f"\n  {'thr':>6}{'trades':>8}{'expR':>9}{'Δexp':>9}{'Δsharpe':>10}   veredicto")
    rows = []
    for thr in candidates:
        pooled_cand = aggregate([per_symbol_cands[sym][thr] for sym in per_symbol_cands])
        if not pooled_cand:
            continue
        ok, _ = verdict(pooled_base, pooled_cand)
        reliable = pooled_cand["trades"] >= sweep_min_trades
        dexp = pooled_cand["expectancy"] - pooled_base["expectancy"]
        dsharpe = pooled_cand["sharpe"] - pooled_base["sharpe"]
        tag = "⚪ MUESTRA INSUFICIENTE" if not reliable else ("✅ PASS" if ok else "❌ FAIL")
        rows.append((thr, pooled_cand, dexp, dsharpe, tag, reliable, ok))
        print(f"  {thr:>6}{pooled_cand['trades']:>8}{pooled_cand['expectancy']:>+9.3f}{dexp:>+9.3f}{dsharpe:>+10.2f}   {tag}")

    # chequeo de consistencia direccional (mismo criterio que pst_bt_lab.py): si
    # subir Y bajar el umbral mejoran a la vez, es ruido de muestra, no señal real.
    lower = [r for r in rows if r[0] < default_thr]
    upper = [r for r in rows if r[0] > default_thr]
    lower_improves = any(r[6] and r[2] > 0 for r in lower)
    upper_improves = any(r[6] and r[2] > 0 for r in upper)
    if lower_improves and upper_improves:
        print("\n  🟡 RUIDO: tanto bajar como subir el umbral mejoran el pool — no hay dirección "
              "limpia, no promover ningún candidato de este sweep.")
        return

    winners = [r for r in rows if r[5] and r[6] and r[2] > 0]
    winners.sort(key=lambda r: r[2], reverse=True)
    if winners:
        thr, m, dexp, dsharpe, tag, reliable, ok = winners[0]
        print(f"\n  💡 Candidato pooled más prometedor: ENTRY_THRESHOLD={thr} (Δexp {dexp:+.3f}R, "
              f"Δsharpe {dsharpe:+.2f}, {m['trades']} trades) — validar en una 2ª ventana temporal "
              f"(--days distinto) antes de cambiar el default en el código.")
    else:
        print("\n  Sin candidatos fiables que batan al default en el pool. Mantener ENTRY_THRESHOLD como está.")


# ─────────────────────────────────────────── main
async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strategy", required=True, choices=list(STRATS.keys()))
    ap.add_argument("--symbols", default="US100.cash,US30.cash,XAUUSD,EURUSD")
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--save-baseline", default=None, help="Guarda métricas del working tree como baseline NOMBRE")
    ap.add_argument("--vs-baseline", default=None, help="Juzga el working tree contra el baseline NOMBRE guardado")
    ap.add_argument("--sweep", action="store_true", help="Barre ENTRY_THRESHOLD (única palanca expuesta hoy)")
    ap.add_argument("--sweep-min-trades", type=int, default=15,
                    help="Trades mínimos para que un PASS del sweep cuente como fiable "
                         "(15, no 30 como pst_bt_lab.py: DivergenceReversion es mucho menos frecuente por diseño)")
    ap.add_argument("--trace", type=int, default=0, help="Imprime N trades de ejemplo por símbolo")
    # A/B de FaithfulMLEngine — defaults = producción fiel (post-fix 2026-08-13).
    ap.add_argument("--index-technical-tp", action="store_true",
                    help="A/B: inyecta el TP técnico también en INDEX (NO es lo que corre en real, ver HALLAZGO #1)")
    ap.add_argument("--macro-mode", choices=["on", "off"], default="on")
    ap.add_argument("--regime-gate", choices=["on", "off"], default="on")
    ap.add_argument("--timeout-mins", type=int, default=30,
                    help="Timeout nominal para RANGE (TREND está exento siempre). Ver nota de "
                         "incertidumbre de offset de reloj en faithful_ml_engine.py antes de recalibrar.")
    args = ap.parse_args()

    strat_cls = STRATS[args.strategy]
    default_thr = strat_cls.ENTRY_THRESHOLD
    symbols = [s.strip() for s in args.symbols.split(",")]
    engine_kwargs = dict(
        index_technical_tp=args.index_technical_tp,
        macro_mode=args.macro_mode, regime_gate=args.regime_gate,
        timeout_mins=args.timeout_mins,
    )

    print(f"\n{'#'*70}\n#  PST ML LAB (motor fiel)  |  {args.strategy}  |  días {args.days}  |  "
          f"ENTRY_THRESHOLD default {default_thr}\n{'#'*70}")

    data_by_symbol = {}
    for sym in symbols:
        data_by_symbol[sym] = get_data(sym, args.days, args.refresh)

    if args.sweep:
        await run_sweep(strat_cls, symbols, data_by_symbol, default_thr, engine_kwargs, args.sweep_min_trades)
        _shutdown_mt5(); print("\nSweep finalizado.\n"); return

    if args.save_baseline:
        os.makedirs(BASELINE_DIR, exist_ok=True)
        store = {"strategy": args.strategy, "days": args.days, "entry_threshold": default_thr,
                 "engine_kwargs": engine_kwargs, "created": str(datetime.now()), "symbols": {}}
        print(f"\n# Guardando baseline '{args.save_baseline}' (working tree, {args.strategy})")
        results = []
        for sym in symbols:
            data = data_by_symbol.get(sym)
            if not data:
                continue
            res = await run_variant(strat_cls, sym, data, default_thr, engine_kwargs)
            results.append(res)
            store["symbols"][sym] = metrics(res)
            print(f"  {sym}: {store['symbols'][sym]['trades']} trades | exp {store['symbols'][sym]['expectancy']:+.3f}R")
        agg = aggregate(results)
        if agg:
            store["pooled"] = agg
            print(f"  POOL: {agg['trades']} trades | exp {agg['expectancy']:+.3f}R | sharpe {agg['sharpe']:.2f}")
        path = os.path.join(BASELINE_DIR, f"{args.save_baseline}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
        print(f"\n✅ Baseline guardado en {path}")
        _shutdown_mt5(); return

    if args.vs_baseline:
        path = os.path.join(BASELINE_DIR, f"{args.vs_baseline}.json")
        with open(path, encoding="utf-8") as f:
            store = json.load(f)
        if store.get("strategy") and store["strategy"] != args.strategy:
            print(f"  ⚠️  el baseline '{args.vs_baseline}' es de {store['strategy']}, no de {args.strategy}. Abortando.")
            return
        print(f"\n# Working tree ({args.strategy})  vs  baseline guardado '{args.vs_baseline}'")
        all_ok = True
        cand_all = []
        for sym in symbols:
            data = data_by_symbol.get(sym)
            if not data or sym not in store["symbols"]:
                continue
            res = await run_variant(strat_cls, sym, data, default_thr, engine_kwargs)
            cand_all.append(res)
            cand = metrics(res); base = store["symbols"][sym]
            print_pair(sym, base, cand, f"BASE:{args.vs_baseline}", "WORKING")
            ok, msg = verdict(base, cand); all_ok = all_ok and ok
            print(f"  → {msg}")
            _maybe_trace(res, args.trace)
        agg = aggregate(cand_all)
        if agg and "pooled" in store:
            print_pair("POOL", store["pooled"], agg, f"BASE:{args.vs_baseline}", "WORKING")
            ok, msg = verdict(store["pooled"], agg); all_ok = all_ok and ok
            print(f"  → {msg}")
        print(f"\n{'='*66}\n  VEREDICTO GLOBAL: {'✅ VERDE — no hay regresión' if all_ok else '❌ ROJO — hay regresión'}\n{'='*66}")
        _shutdown_mt5(); return

    # Modo default: solo reporta métricas actuales, sin comparación.
    results = []
    for sym in symbols:
        data = data_by_symbol.get(sym)
        if not data:
            continue
        res = await run_variant(strat_cls, sym, data, default_thr, engine_kwargs)
        results.append(res)
        m = metrics(res)
        print(f"\n  --- {sym} ---")
        for k, v in m.items():
            print(f"    {k:<15} {v}")
        _maybe_trace(res, args.trace)
    agg = aggregate(results)
    if agg:
        print(f"\n{'='*66}\n  POOL AGREGADO\n{'='*66}")
        for k, v in agg.items():
            print(f"    {k:<15} {v}")
    _shutdown_mt5()
    print("\nLab finalizado.\n")


def _maybe_trace(res, n):
    if n <= 0:
        return
    print(f"    · trazas ({min(n, len(res.trades))} de {len(res.trades)}):")
    for t in res.trades[:n]:
        print(f"      {('BUY ' if t.direction==1 else 'SELL')} @ {t.entry_price:.5f} "
              f"SL {t.sl_price:.5f} TP {t.tp_price:.5f} → {t.exit_reason or t.result} "
              f"pnl {t.pnl_r:+.2f}R")


def _shutdown_mt5():
    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
