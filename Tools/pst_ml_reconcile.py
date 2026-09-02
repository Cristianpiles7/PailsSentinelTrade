#!/usr/bin/env python3
"""
PST · Reconciliación LIVE vs JUEZ — sistema ML (MomentumPulse / DivergenceReversion)
─────────────────────────────────────────────────────────────────────────────────
Equivalente a Tools/pst_reconcile.py (PrecisionScalping) pero para las estrategias
del sistema ML nuevo, leyendo el baseline generado por Tools/pst_ml_lab.py
(Tools/bt_baselines/{momentum,divergence}_v1_fixed.json — formato {"symbols":
{SYM: {expectancy,...}}, "pooled": {...}}, no el {"expectancy_R": {...}} plano
que espera pst_reconcile.py).

Misma metodología (Tools/pst_reconcile.py):
  · Expectancy real por trade en R = profit / risk_value (risk_mode=MONEY, seed
    real de symbol_strategies: 7.0€ para ambas estrategias nuevas).
  · Error estándar = std(R)/sqrt(n). Veredicto por comparación con 1 SE.
  · Guardarraíl de muestra mínima: con pocos trades no se concluye nada.
  · Símbolos sin baseline (el universo real operado es mucho más amplio que los 4
    símbolos piloto con modelo ML — ver manual_docs/ESTRATEGIAS_NUEVO_SISTEMA.md)
    se listan aparte, sin veredicto — son candidatos naturales para la Corriente B
    (expandir símbolos con modelo entrenado).

Uso:
    python Tools/pst_ml_reconcile.py --strategy PST-MomentumPulse
    python Tools/pst_ml_reconcile.py --strategy PST-DivergenceReversion --since 2026-08-11
"""
import sqlite3
import json
import os
import sys
import argparse
import statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASELINE_MAP = {
    "PST-MomentumPulse": "momentum_v1_fixed.json",
    "PST-DivergenceReversion": "divergence_v1_fixed.json",
}
BASELINE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bt_baselines")


def _db_path():
    try:
        from PST_Core.config import DB_PATH
        if DB_PATH and os.path.exists(DB_PATH):
            return DB_PATH
    except Exception:
        pass
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(here, "PST_Core", "data", "pst_trading.db"),
                 os.path.join(os.path.dirname(here), "PST_Core", "data", "pst_trading.db")):
        if os.path.exists(cand):
            return cand
    return None


def _risk_value(cur, symbol, strategy, default=7.0):
    try:
        cur.execute("SELECT risk_value FROM symbol_strategies WHERE symbol=? AND strategy_name=? AND risk_value IS NOT NULL LIMIT 1",
                    (symbol, strategy))
        r = cur.fetchone()
        if r and r[0]:
            return float(r[0])
    except Exception:
        pass
    return default


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strategy", required=True, choices=list(BASELINE_MAP.keys()))
    ap.add_argument("--min-trades", type=int, default=15,
                    help="Muestra mínima para veredicto fiable (15, no 30: universo/frecuencia menor que PrecisionScalping)")
    ap.add_argument("--since", default="2026-08-11", help="Solo trades con time_in >= esta fecha (YYYY-MM-DD)")
    args = ap.parse_args()

    db = _db_path()
    if not db:
        print("No encuentro pst_trading.db"); return

    baseline_path = os.path.join(BASELINE_DIR, BASELINE_MAP[args.strategy])
    if not os.path.exists(baseline_path):
        print(f"No encuentro el baseline {baseline_path} — corre antes Tools/pst_ml_lab.py --save-baseline"); return
    with open(baseline_path, encoding="utf-8") as f:
        base = json.load(f)
    judge_symbols = base.get("symbols", {})
    judge_pooled = base.get("pooled")

    c = sqlite3.connect(db, timeout=15)
    cur = c.cursor()
    cur.execute(
        "SELECT symbol, profit FROM trades WHERE strategy_name=? AND time_in >= ? "
        "AND time_out IS NOT NULL AND time_out != '' AND profit IS NOT NULL",
        (args.strategy, args.since))
    rows = cur.fetchall()
    by = {}
    for sym, profit in rows:
        by.setdefault(sym, []).append(float(profit))

    print(f"\n{'='*88}")
    print(f"  RECONCILIACIÓN LIVE vs JUEZ (ML)   ·   {args.strategy}   ·   desde {args.since}")
    print(f"  baseline: {os.path.basename(baseline_path)}   (motor fiel: FaithfulMLEngine, post-fix régimen 2026-08-13)")
    print(f"{'='*88}")

    if not by:
        print(f"\n  Sin trades cerrados de {args.strategy} desde {args.since}.\n")
        return

    print(f"  {'símbolo':<14}{'n':>4}{'WR':>6}{'net€':>9}{'liveExpR':>10}{'±SE':>7}{'juezExpR':>10}{'Δ':>8}   veredicto")
    print(f"  {'-'*86}")

    tot_net = 0.0
    all_R = []
    lines_covered, lines_uncovered = [], []
    for sym in sorted(by, key=lambda s: -len(by[s])):
        pls = by[sym]
        n = len(pls)
        rv = _risk_value(cur, sym, args.strategy)
        Rs = [p / rv for p in pls]
        all_R.extend(Rs)
        net = sum(pls)
        wr = 100.0 * sum(1 for p in pls if p > 0) / n
        live_exp = statistics.mean(Rs)
        se = (statistics.stdev(Rs) / (n ** 0.5)) if n > 1 else float("nan")
        tot_net += net
        j = judge_symbols.get(sym, {}).get("expectancy") if sym in judge_symbols else None

        if j is not None:
            delta = live_exp - j
            if n < args.min_trades:
                verd = f"MUESTRA BAJA (faltan {args.min_trades - n})"
            elif se == se and se > 0 and abs(delta) <= se:
                verd = "TRACEA (dentro de 1σ)"
            elif delta > 0:
                verd = "MEJOR que el juez"
            else:
                verd = "PEOR que el juez"
            jtxt, dtxt = f"{j:>+10.3f}", f"{delta:>+8.3f}"
            lines_covered.append((sym, n, wr, net, live_exp, se, jtxt, dtxt, verd))
        else:
            lines_uncovered.append((sym, n, wr, net, live_exp, se))

    for sym, n, wr, net, live_exp, se, jtxt, dtxt, verd in lines_covered:
        setxt = f"{se:>6.3f}" if se == se else f"{'—':>6}"
        print(f"  {sym:<14}{n:>4}{wr:>5.0f}%{net:>9.2f}{live_exp:>+10.3f}{setxt}{jtxt}{dtxt}   {verd}")

    if lines_uncovered:
        print(f"\n  Símbolos SIN baseline ML (fuera de los 4 pilotos — candidatos a Corriente B):")
        for sym, n, wr, net, live_exp, se in lines_uncovered:
            setxt = f"{se:>6.3f}" if se == se else f"{'—':>6}"
            print(f"  {sym:<14}{n:>4}{wr:>5.0f}%{net:>9.2f}{live_exp:>+10.3f}{setxt}   — sin baseline")

    print(f"  {'-'*86}")
    n_tot = sum(len(v) for v in by.values())
    print(f"  {'TOTAL':<14}{n_tot:>4}{'':>6}{tot_net:>9.2f}€")

    if judge_pooled and len(all_R) >= 2:
        live_pool_exp = statistics.mean(all_R)
        se_pool = statistics.stdev(all_R) / (len(all_R) ** 0.5)
        j_pool = judge_pooled["expectancy"]
        delta = live_pool_exp - j_pool
        print(f"\n  POOL (símbolos con baseline únicamente): live {live_pool_exp:+.3f}R (±{se_pool:.3f}) "
              f"vs juez {j_pool:+.3f}R  →  Δ {delta:+.3f}R")
        if len(all_R) < args.min_trades:
            print(f"  ⚪ Muestra insuficiente para veredicto pool (mínimo {args.min_trades}).")
        elif abs(delta) <= se_pool:
            print(f"  ✅ TRACEA — dentro de 1 error estándar del juez.")
        else:
            print(f"  {'🟢' if delta > 0 else '🔴'} {'MEJOR' if delta > 0 else 'PEOR'} que el juez, fuera de 1 SE "
                  f"(revisar con más muestra antes de actuar).")

    print(f"\n  Nota: con <{args.min_trades} trades por símbolo NO se concluye — el objetivo es acumular")
    print(f"  y volver a correr esto periódicamente.\n")
    c.close()


if __name__ == "__main__":
    main()
