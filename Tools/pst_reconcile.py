#!/usr/bin/env python3
"""
PST · Reconciliación LIVE vs JUEZ
─────────────────────────────────
Compara la expectancy REAL acumulada del bot (BBDD de trades) contra la que predijo el
motor fiel consciente de costes (juez), símbolo a símbolo. Sirve para CONFIRMAR con datos
reales si los símbolos marginales (BTCUSD, EU50, AAPL...) tracean lo esperado, en vez de
fiarse de una sola ventana de backtest.

Metodología:
  · Expectancy real por trade en R = profit / risk_value (risk_mode=MONEY → 1R = 7 EUR fijo).
    Así es directamente comparable con la expectancy en R del juez.
  · Error estándar = std(R)/sqrt(n). Veredicto por z = (live − juez)/SE.
  · Guardarraíl de muestra mínima: con pocos trades NO se concluye nada (el propósito es
    acumular). Un backtest son cientos de trades; el live tarda en igualar la muestra.

OJO: usa el profit REGISTRADO por el bot en la BBDD (columna trades.profit). Si ese profit
no incluyera comisión/swap, la comparación quedaría OPTIMISTA para el live; contrástalo con
el historial de MT5 si algo no cuadra (la comisión de cripto es grande, ~0.4R).

Uso:
    python Tools/pst_reconcile.py                       # todos los símbolos con trades
    python Tools/pst_reconcile.py --min-trades 30       # umbral de fiabilidad (default 30)
    python Tools/pst_reconcile.py --strategy PST-PrecisionScalping
    python Tools/pst_reconcile.py --baseline Tools/bt_baselines/judge_cost_aware.json
"""
import sqlite3
import json
import os
import sys
import argparse
import statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "bt_baselines", "judge_cost_aware.json")


def _db_path():
    try:
        from PST_Core.config import DB_PATH
        if DB_PATH and os.path.exists(DB_PATH):
            return DB_PATH
    except Exception:
        pass
    # Fallback: autodiscovery simple (un nivel por encima del repo, como en producción)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(here, "PST_Core", "data", "pst_trading.db"),
                 os.path.join(os.path.dirname(here), "PST_Core", "data", "pst_trading.db")):
        if os.path.exists(cand):
            return cand
    return None


def _risk_value(cur, symbol, default=7.0):
    """risk_value del símbolo en symbol_strategies (MONEY). Fallback 7.0."""
    try:
        cur.execute("SELECT risk_value FROM symbol_strategies WHERE symbol=? AND risk_value IS NOT NULL LIMIT 1", (symbol,))
        r = cur.fetchone()
        if r and r[0]:
            return float(r[0])
    except Exception:
        pass
    return default


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", default=DEFAULT_BASELINE, help="JSON con expectancy_R del juez por símbolo")
    ap.add_argument("--strategy", default="PST-PrecisionScalping", help="Filtra por estrategia (substring)")
    ap.add_argument("--min-trades", type=int, default=30, help="Muestra mínima para dar un veredicto fiable")
    ap.add_argument("--since", default=None, help="Solo trades con time_in >= esta fecha (YYYY-MM-DD)")
    args = ap.parse_args()

    db = _db_path()
    if not db:
        print("❌ No encuentro pst_trading.db"); return
    with open(args.baseline, encoding="utf-8") as f:
        base = json.load(f)
    judge = base.get("expectancy_R", {})
    risk_default = float(base.get("risk_value_eur", 7.0))

    c = sqlite3.connect(db, timeout=15)
    cur = c.cursor()

    where = "time_out IS NOT NULL AND profit IS NOT NULL"
    params = []
    if args.strategy:
        where += " AND strategy_name LIKE ?"; params.append(f"%{args.strategy}%")
    if args.since:
        where += " AND time_in >= ?"; params.append(args.since)

    cur.execute(f"SELECT symbol, profit FROM trades WHERE {where}", params)
    rows = cur.fetchall()
    by = {}
    for sym, profit in rows:
        by.setdefault(sym, []).append(float(profit))

    if not by:
        print("No hay trades cerrados que reconciliar (¿estrategia/fecha?)."); return

    print(f"\n{'='*84}")
    print(f"  RECONCILIACIÓN LIVE vs JUEZ   ·   {args.strategy or 'todas'}   ·   baseline: {os.path.basename(args.baseline)}")
    print(f"  juez: {base.get('engine','?')[:70]}")
    print(f"{'='*84}")
    print(f"  {'símbolo':<12}{'n':>4}{'WR':>6}{'net€':>9}{'liveExpR':>10}{'±SE':>7}{'juezExpR':>10}{'Δ':>8}   veredicto")
    print(f"  {'-'*82}")

    tot_net = 0.0
    tot_expected = 0.0
    lines = []
    for sym in sorted(by, key=lambda s: -len(by[s])):
        pls = by[sym]
        n = len(pls)
        rv = _risk_value(cur, sym, risk_default)
        Rs = [p / rv for p in pls]
        net = sum(pls)
        wr = 100.0 * sum(1 for p in pls if p > 0) / n
        live_exp = statistics.mean(Rs)
        se = (statistics.stdev(Rs) / (n ** 0.5)) if n > 1 else float("nan")
        j = judge.get(sym)
        tot_net += net
        active = sym in judge  # está en el universo evaluado
        if j is not None:
            tot_expected += j * rv * n
            delta = live_exp - j
            if n < args.min_trades:
                verd = f"⚪ muestra baja (faltan {args.min_trades - n})"
            elif se == se and se > 0 and abs(delta) <= se:
                verd = "✅ TRACEA (dentro de 1σ)"
            elif delta > 0:
                verd = "🟢 MEJOR que el juez"
            else:
                verd = "🔴 PEOR que el juez"
            jtxt = f"{j:>+10.3f}"; dtxt = f"{delta:>+8.3f}"
        else:
            verd = "— sin baseline (¿inactivo?)"
            jtxt = f"{'—':>10}"; dtxt = f"{'—':>8}"
        setxt = f"{se:>6.3f}" if se == se else f"{'—':>6}"
        lines.append((sym, n, wr, net, live_exp, setxt, jtxt, dtxt, verd))

    for sym, n, wr, net, live_exp, setxt, jtxt, dtxt, verd in lines:
        print(f"  {sym:<12}{n:>4}{wr:>5.0f}%{net:>9.2f}{live_exp:>+10.3f}{setxt:>7}{jtxt}{dtxt}   {verd}")

    print(f"  {'-'*82}")
    n_tot = sum(len(v) for v in by.values())
    print(f"  {'TOTAL':<12}{n_tot:>4}{'':>6}{tot_net:>9.2f}   (juez esperaba ≈ {tot_expected:>+8.2f}€ para estas muestras)")
    print(f"\n  Nota: con <{args.min_trades} trades por símbolo NO se concluye — el objetivo es ACUMULAR y")
    print(f"  volver a correr esto. El juez se calibra sobre cientos de trades; el live tarda en igualar.\n")
    c.close()


if __name__ == "__main__":
    main()
