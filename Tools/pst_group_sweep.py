#!/usr/bin/env python3
"""
PST · GROUP SWEEP — barrido de palancas con muestra AGREGADA por grupo de activo
────────────────────────────────────────────────────────────────────────────────
El sweep por símbolo (pst_bt_lab --sweep) tiene 30-90 trades de muestra → solo detecta
efectos grandes. Este runner agrega los trades de VARIOS símbolos del mismo grupo por
variante (~200-300 trades) para poder juzgar palancas finas:

  · Filtros granulares de FILTER_DEFAULTS nunca barridos (adx/chop clean-ok-extreme,
    rsi_strong) — los campos "def" del Matrix Editor.
  · Valores LEJANOS de la geometría de riesgo (sl_mult 1.0/2.2, be_mult 2.0/5.5,
    min_rr 1.4/2.4) — el sweep normal solo prueba ±1 paso.

Protocolo: pool de tuning (p.ej. US500/US100/US30/GER40) + HOLDOUT que nunca ve el
tuning (EU50/UK100). Un ganador del pool solo se promueve si valida en el holdout
y en una segunda ventana temporal. Mismos guardarraíles que el sweep clásico
(tolerancias, chequeo direccional de ruido).

Uso (por fases, paralelizable por símbolo):
    # 1. correr cada símbolo del pool en un proceso (background)
    python Tools/pst_group_sweep.py --symbol US500.cash --days 15 --out us500.json
    # 2. consolidar y juzgar
    python Tools/pst_group_sweep.py --consolidate us500.json,us100.json,...
"""
import sys
import os
import json
import asyncio
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pst_bt_lab import (  # noqa: E402
    load_working_tree, get_data, metrics, _shipped_full_profile_for, TOL_EXP, TOL_SHARPE,
)
from PST_Core.backtesting.faithful_engine import FaithfulScalpingEngine  # noqa: E402

# Palancas: (scope, clave, valor). Una a la vez sobre el perfil shippeado del símbolo.
GRID = [
    # filtros granulares (dentro de filter_profile) — defaults: adx_clean 25, adx_ok 18,
    # chop_clean 38.2, chop_ok 61.8, adx_extreme 15, chop_extreme 70, rsi_strong 55
    ("filter", "adx_clean", 20.0), ("filter", "adx_clean", 30.0),
    ("filter", "adx_ok", 15.0), ("filter", "adx_ok", 21.0),
    ("filter", "chop_clean", 33.0), ("filter", "chop_clean", 43.0),
    ("filter", "chop_ok", 56.0), ("filter", "chop_ok", 66.0),
    ("filter", "adx_extreme", 12.0), ("filter", "adx_extreme", 18.0),
    ("filter", "chop_extreme", 65.0), ("filter", "chop_extreme", 75.0),
    ("filter", "rsi_strong", 52.0), ("filter", "rsi_strong", 58.0),
    # geometría de riesgo, valores LEJANOS (los cercanos ya se barrieron sin señal)
    ("top", "sl_mult", 1.0), ("top", "sl_mult", 2.2),
    ("top", "be_mult", 2.0), ("top", "be_mult", 5.5),
    ("top", "min_rr", 1.4), ("top", "min_rr", 2.4),
    # tp_mult: solo gobierna el fallback ATR-based. En FOREX/METAL/CRYPTO el TP técnico
    # (VWAP/Donchian) tiene prioridad si es válido, así que aquí se espera poca señal
    # (a diferencia de INDEX, donde el ATR-based es el único TP).
    ("top", "tp_mult", 2.0), ("top", "tp_mult", 3.0),
]


def _key(scope, k, v):
    return f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}"


async def run_symbol(symbol, days, out_path, threshold=70, lookback=200):
    WT = load_working_tree()
    data = get_data(symbol, days)
    if not data:
        print(f"{symbol}: sin datos"); return
    shipped = _shipped_full_profile_for(symbol)

    async def one(f, t):
        prof = {"filter_profile": f, **t}
        eng = FaithfulScalpingEngine(threshold=threshold, lookback_m1=lookback)
        res = await eng.run(WT(), symbol, data, profile=prof,
                            point=data.get("point"), spread_dist=data.get("spread_dist", 0.0))
        m = metrics(res)
        m["pnls"] = [t_.pnl_r for t_ in res.closed_trades]   # para agregar en el consolidador
        return m

    out = {"symbol": symbol, "days": days, "shipped": shipped, "variants": {}}
    out["variants"]["BASELINE"] = await one(shipped["filter"], shipped["top"])
    print(f"{symbol} BASELINE: {out['variants']['BASELINE']['trades']} trades "
          f"exp {out['variants']['BASELINE']['expectancy']:+.3f}R")
    for scope, k, v in GRID:
        f, t = dict(shipped["filter"]), dict(shipped["top"])
        (f if scope == "filter" else t)[k] = v
        m = await one(f, t)
        out["variants"][_key(scope, k, v)] = m
        print(f"  {_key(scope, k, v):<20} {m['trades']:>4}t exp {m['expectancy']:+.3f}R")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    print(f"OK → {out_path}")


def consolidate(paths):
    import numpy as np
    per_sym = [json.load(open(p, encoding="utf-8")) for p in paths]
    syms = [d["symbol"] for d in per_sym]
    print(f"\n{'='*78}\n  GROUP SWEEP consolidado — pool: {', '.join(syms)}\n{'='*78}")

    def pooled(vkey):
        pnls = []
        for d in per_sym:
            v = d["variants"].get(vkey)
            if v:
                pnls += v["pnls"]
        if not pnls:
            return None
        arr = np.array(pnls)
        std = arr.std(ddof=1) if len(arr) > 1 else 0.0
        wins = arr[arr > 0]; losses = arr[arr < 0]
        gl = abs(losses.sum())
        return {"trades": len(arr), "expectancy": float(arr.mean()),
                "sharpe": float(arr.mean() / std * np.sqrt(252)) if std > 0 else 0.0,
                "win_rate": len(wins) / len(arr) * 100,
                "profit_factor": float(wins.sum() / gl) if gl > 0 else float("inf"),
                "total_r": float(arr.sum())}

    base = pooled("BASELINE")
    print(f"  BASELINE pool: {base['trades']} trades | exp {base['expectancy']:+.3f}R | "
          f"WR {base['win_rate']:.1f}% | PF {base['profit_factor']:.2f} | sharpe {base['sharpe']:.2f}")

    rows = []
    for scope, k, v in GRID:
        vkey = _key(scope, k, v)
        m = pooled(vkey)
        if not m:
            continue
        de = m["expectancy"] - base["expectancy"]
        ds = m["sharpe"] - base["sharpe"]
        ok = (de >= -TOL_EXP) and (ds >= -TOL_SHARPE)
        rows.append([k, v, m, de, ds, ok])

    # chequeo direccional: si ambos valores de la misma palanca "mejoran", es ruido
    by_key = {}
    for r in rows:
        by_key.setdefault(r[0], []).append(r)
    for k, group in by_key.items():
        if len(group) == 2 and all(r[5] and r[3] > 0 for r in group):
            for r in group:
                r[5] = None   # marcar ruido

    rows.sort(key=lambda r: r[3], reverse=True)
    print(f"\n  {'palanca':<22}{'trades':>7}{'WR':>7}{'PF':>6}{'expR':>9}{'Δexp':>9}{'Δsharpe':>9}   veredicto")
    print("  " + "-" * 76)
    for k, v, m, de, ds, ok in rows:
        tag = "RUIDO (2 direcciones)" if ok is None else ("PASS" if ok else "FAIL")
        star = " **" if (ok and de > 0.02) else ""
        print(f"  {_key('', k, v):<22}{m['trades']:>7}{m['win_rate']:>6.1f}%{m['profit_factor']:>6.2f}"
              f"{m['expectancy']:>+9.3f}{de:>+9.3f}{ds:>+9.2f}   {tag}{star}")
    winners = [r for r in rows if r[5] and r[3] > 0.02]
    if winners:
        print("\n  ** Candidatos a promover (Δexp>+0.02 y sin ruido direccional):")
        for k, v, m, de, ds, ok in winners:
            print(f"     {k}={v:g}  (Δexp {de:+.3f}, Δsharpe {ds:+.2f})")
        print("     → confirmar en HOLDOUT (EU50/UK100) y 2ª ventana antes de aplicar.")
    else:
        print("\n  Sin candidatos que batan al perfil shippeado con muestra agregada.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--days", type=int, default=15)
    ap.add_argument("--out", default=None)
    ap.add_argument("--consolidate", default=None, help="lista de JSONs por símbolo, separados por coma")
    args = ap.parse_args()
    if args.consolidate:
        consolidate([p.strip() for p in args.consolidate.split(",")])
        return
    if not args.symbol or not args.out:
        ap.error("--symbol y --out son obligatorios (o usa --consolidate)")
    asyncio.run(run_symbol(args.symbol, args.days, args.out))


if __name__ == "__main__":
    main()
