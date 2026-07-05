#!/usr/bin/env python3
"""
PST · ORB LAB — backtest riguroso de Opening Range Breakout intradía (candidata NUEVA)
──────────────────────────────────────────────────────────────────────────────────────
Familia NO probada aún en este proyecto (las 3 exploraciones de 2026-07-03 eran
micro-reversión M1, micro-momentum M1 y trend-pullback M5/M15 — todas fallaron OOS).
ORB es distinto: breakout ANCLADO A LA SESIÓN (apertura NY/EU), 0-1 trade/día/símbolo,
horizonte de horas y cierre forzado antes del fin de sesión (compatible FTMO: nunca
duerme una posición abierta).

Reglas simuladas (por día de calendario del bróker):
  · Opening Range = high/low de los primeros `or_mins` minutos tras la apertura de la
    sesión del activo (NY 16:30 hora bróker para índices US/cripto; EU/Londres 10:00
    para índices EU, forex y metales).
  · Entrada: primer CIERRE M5 fuera del rango (± buffer 5% del rango) dentro de la
    ventana de entrada (4h tras la apertura). Máximo 1 trade/día/símbolo.
  · SL: lado opuesto del rango (sl_mode=range) o punto medio (sl_mode=mid).
  · TP: tp_r × R (R = distancia de SL). Breakeven al alcanzar +1R.
  · Cierre forzado fin de sesión (22:45 bróker; cripto 23:45) — nada queda abierto.
  · Filtro de calidad del rango: 0.35×mediana ≤ OR ≤ 2.5×mediana (mediana rodante de
    los 20 días PREVIOS — sin lookahead).
  · Gate de coste: se descarta el trade si (spread + comisión) estimados > 25% de R.

Costes reales (calibrados con la cuenta, como el motor fiel):
  · Medio spread en la entrada + medio en la salida (round-trip completo).
  · Comisión round-turn de config.COMMISSION_SPEC convertida a R (per_lot vía
    tick_value/tick_size; pct_notional para cripto).

Protocolo anti-sobreajuste (la lección de las 3 estrategias fallidas):
  · Grid PEQUEÑO (12 combos) solo sobre símbolos IS y el primer 70% del histórico.
  · La config ganadora se juzga en: (a) mismos símbolos, último 30% (OOS temporal) y
    (b) símbolos NUNCA usados para tunear (OOS de símbolo), histórico completo.
  · Veredicto APTA solo si el edge sobrevive en AMBOS ejes.

Uso:
    python Tools/pst_orb_lab.py                       # protocolo completo (grid IS + OOS)
    python Tools/pst_orb_lab.py --days 140 --refresh  # re-descargar datos
    python Tools/pst_orb_lab.py --combo "60,range,2.0"  # forzar un combo y saltar el grid
"""
import sys
import os
import argparse
import pickle
from datetime import datetime, timedelta, time as dtime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PST_Core.config import COMMISSION_SPEC  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(REPO_ROOT, "Tools", ".bt_cache")

# Universo. IS = símbolos donde se PERMITE tunear; OOS = jueces intocables.
IS_SYMBOLS = ["US500.cash", "XAUUSD", "EURUSD", "BTCUSD"]
OOS_SYMBOLS = ["EU50.cash", "GER40.cash", "GBPUSD", "USDJPY", "ETHUSD"]

IS_FRAC = 0.70          # primer 70% del histórico = in-sample temporal
COST_GATE_R = 0.25      # descarta trades donde el coste estimado > 25% de R
BUF_FRAC = 0.05         # buffer de ruptura: 5% del rango
ENTRY_WINDOW_H = 4      # solo entradas en las 4h posteriores a la apertura
MIN_TRADES_GRID = 80    # muestra mínima agregada para que un combo sea elegible


def asset_class(symbol: str) -> str:
    s = symbol.upper()
    if "XAU" in s or "XAG" in s:
        return "METAL"
    if any(k in s for k in ("US500", "US30", "EU50", "GER40", "GER30", "NAS100", "DAX")):
        return "INDEX"
    if any(k in s for k in ("BTC", "ETH", "SOL", "XRP", "LTC")):
        return "CRYPTO"
    return "FOREX"


# Sesiones en HORA BRÓKER (EET, UTC+2/+3). NY open = 16:30 bróker (el bróker sigue el
# horario "New York close", por lo que el ancla es estable todo el año).
SESSIONS = {
    "INDEX_US": {"open": dtime(16, 30), "eod": dtime(22, 45)},
    "INDEX_EU": {"open": dtime(10, 0),  "eod": dtime(22, 45)},
    "FOREX":    {"open": dtime(10, 0),  "eod": dtime(22, 45)},
    "METAL":    {"open": dtime(10, 0),  "eod": dtime(22, 45)},
    "CRYPTO":   {"open": dtime(16, 30), "eod": dtime(23, 45)},
}


def session_for(symbol: str) -> dict:
    cls = asset_class(symbol)
    if cls == "INDEX":
        return SESSIONS["INDEX_EU"] if any(k in symbol.upper() for k in ("EU50", "GER40", "DAX")) else SESSIONS["INDEX_US"]
    return SESSIONS[cls]


# ─────────────────────────────────────────── datos
def get_data(symbol: str, days: int, refresh: bool = False):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{symbol}_ORB_M5_{days}d.pkl")
    if os.path.exists(path) and not refresh:
        with open(path, "rb") as f:
            return pickle.load(f)

    import MetaTrader5 as mt5
    if not mt5.initialize():
        print(f"    MT5 init falló: {mt5.last_error()}")
        return None
    info = mt5.symbol_info(symbol)
    if info is None:
        print(f"    {symbol}: símbolo no existe en el bróker")
        return None
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5,
                                 datetime.now() - timedelta(days=days), datetime.now())
    if rates is None or len(rates) < 5000:
        print(f"    {symbol}: histórico insuficiente ({0 if rates is None else len(rates)} barras)")
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    out = {
        "m5": df,
        "point": info.point,
        "spread_dist": info.spread * info.point,
        "tick_value": getattr(info, "trade_tick_value", None),
        "tick_size": getattr(info, "trade_tick_size", None),
    }
    with open(path, "wb") as f:
        pickle.dump(out, f)
    print(f"    {symbol}: {len(df)} barras M5 descargadas y cacheadas")
    return out


def commission_r(spec: dict, entry_price: float, sl_dist: float, value_per_price) -> float:
    """Comisión round-turn en R — misma matemática que faithful_engine._commission_r."""
    if not spec or sl_dist <= 0:
        return 0.0
    if "pct_notional" in spec:
        pct = float(spec["pct_notional"])
        return pct * entry_price / sl_dist if pct > 0 else 0.0
    per_lot = float(spec.get("per_lot", 0.0))
    if per_lot > 0 and value_per_price:
        return per_lot / (sl_dist * value_per_price)
    return 0.0


# ─────────────────────────────────────────── simulador ORB
def run_orb(symbol: str, data: dict, or_mins: int, sl_mode: str, tp_r: float,
            frac_from: float = 0.0, frac_to: float = 1.0, use_be: bool = True):
    """Simula ORB sobre el tramo [frac_from, frac_to) del histórico. Devuelve lista de
    trades: dicts con pnl_r, entry_time, exit_reason, direction."""
    df = data["m5"]
    n = len(df)
    lo, hi = int(n * frac_from), int(n * frac_to)
    df = df.iloc[lo:hi]
    if df.empty:
        return []

    sess = session_for(symbol)
    cls = asset_class(symbol)
    spec = COMMISSION_SPEC.get(cls, {})
    tick_value, tick_size = data.get("tick_value"), data.get("tick_size")
    vpp = (tick_value / tick_size) if (tick_value and tick_size) else None
    spread = data.get("spread_dist", 0.0) or 0.0
    half_spread = spread / 2.0

    t_open, t_eod = sess["open"], sess["eod"]
    or_end_min = t_open.hour * 60 + t_open.minute + or_mins
    or_end = dtime(or_end_min // 60, or_end_min % 60)
    cut_min = t_open.hour * 60 + t_open.minute + ENTRY_WINDOW_H * 60
    t_cutoff = dtime(min(23, cut_min // 60), cut_min % 60)

    trades = []
    range_hist = []          # anchos de OR de días previos (para la mediana sin lookahead)

    for day, g in df.groupby(df["time"].dt.date):
        g = g.reset_index(drop=True)
        tt = g["time"].dt.time
        or_bars = g[(tt >= t_open) & (tt < or_end)]
        min_bars = max(3, int(or_mins / 5 * 0.8))
        if len(or_bars) < min_bars:
            continue
        orh, orl = float(or_bars["high"].max()), float(or_bars["low"].min())
        or_range = orh - orl
        if or_range <= 0:
            continue

        # filtro de calidad del rango vs mediana de los 20 días previos (sin lookahead)
        med = float(np.median(range_hist[-20:])) if len(range_hist) >= 10 else None
        range_hist.append(or_range)
        if med is not None and not (0.35 * med <= or_range <= 2.5 * med):
            continue

        buf = BUF_FRAC * or_range
        after = g[(tt >= or_end) & (tt <= t_eod)].reset_index(drop=True)

        pos = None
        for k in range(len(after)):
            bar = after.iloc[k]
            btime = bar["time"].time()
            h, l, c = float(bar["high"]), float(bar["low"]), float(bar["close"])

            if pos is None:
                if btime >= t_cutoff:
                    break
                d = 0
                if c > orh + buf:
                    d = 1
                elif c < orl - buf:
                    d = -1
                if d == 0:
                    continue
                fill = c + d * half_spread
                if sl_mode == "range":
                    sl = (orl - buf) if d == 1 else (orh + buf)
                else:  # mid
                    mid = (orh + orl) / 2.0
                    sl = mid
                R = abs(fill - sl)
                if R <= 0:
                    continue
                comm = commission_r(spec, fill, R, vpp)
                cost = spread / R + comm
                if cost > COST_GATE_R:
                    break               # coste estructural: no habrá trade válido hoy
                tp = fill + d * tp_r * R
                pos = {"d": d, "fill": fill, "sl": sl, "tp": tp, "R": R, "comm": comm,
                       "be": False, "entry_time": bar["time"]}
                continue

            # gestión de la posición
            d, fill, R = pos["d"], pos["fill"], pos["R"]

            def close(px, reason):
                eff = px - d * half_spread
                pnl = d * (eff - fill) / R - pos["comm"]
                trades.append({"pnl_r": pnl, "entry_time": pos["entry_time"],
                               "exit_time": bar["time"], "exit_reason": reason,
                               "direction": d, "symbol": symbol})

            if d == 1:
                if l <= pos["sl"]:
                    close(pos["sl"], "SL"); pos = "DONE"; break
                if h >= pos["tp"]:
                    close(pos["tp"], "TP"); pos = "DONE"; break
            else:
                if h >= pos["sl"]:
                    close(pos["sl"], "SL"); pos = "DONE"; break
                if l <= pos["tp"]:
                    close(pos["tp"], "TP"); pos = "DONE"; break

            if use_be and not pos["be"]:
                if (d == 1 and h >= fill + R) or (d == -1 and l <= fill - R):
                    pos["sl"] = fill            # breakeven a +1R
                    pos["be"] = True

            if btime >= t_eod or k == len(after) - 1:
                close(c, "EOD"); pos = "DONE"; break

        if pos not in (None, "DONE") and isinstance(pos, dict):
            # el día acabó sin barra de cierre explícita (histórico truncado)
            last = after.iloc[-1]
            d, fill, R = pos["d"], pos["fill"], pos["R"]
            eff = float(last["close"]) - d * half_spread
            pnl = d * (eff - fill) / R - pos["comm"]
            trades.append({"pnl_r": pnl, "entry_time": pos["entry_time"],
                           "exit_time": last["time"], "exit_reason": "EOD",
                           "direction": d, "symbol": symbol})
    return trades


# ─────────────────────────────────────────── métricas
def metrics(trades):
    if not trades:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "expectancy": 0.0,
                "total_r": 0.0, "max_dd": 0.0, "sharpe": 0.0, "eq_r2": 0.0}
    pnls = [t["pnl_r"] for t in sorted(trades, key=lambda t: t["entry_time"])]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gp, gl = sum(wins), abs(sum(losses))
    eq = np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    mdd = float(np.max(peak - eq)) if len(eq) else 0.0
    std = np.std(pnls, ddof=1) if len(pnls) > 1 else 0.0
    # estabilidad de la curva de equity: R² contra una recta (1.0 = perfectamente lineal)
    if len(eq) > 2 and np.std(eq) > 0:
        x = np.arange(len(eq))
        r = np.corrcoef(x, eq)[0, 1]
        eq_r2 = float(r * r)
    else:
        eq_r2 = 0.0
    return {
        "trades": len(pnls),
        "win_rate": len(wins) / len(pnls) * 100,
        "profit_factor": (gp / gl) if gl > 0 else float("inf"),
        "expectancy": float(np.mean(pnls)),
        "total_r": float(sum(pnls)),
        "max_dd": mdd,
        "sharpe": float(np.mean(pnls) / std * np.sqrt(252)) if std > 0 else 0.0,
        "eq_r2": eq_r2,
    }


def fmt_row(label, m):
    pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
    return (f"  {label:<14} {m['trades']:>6} {m['win_rate']:>6.1f}% {pf:>6} "
            f"{m['expectancy']:>+7.3f} {m['total_r']:>+8.1f} {m['max_dd']:>7.1f} {m['eq_r2']:>6.2f}")


HDR = f"  {'símbolo':<14} {'trades':>6} {'WR':>7} {'PF':>6} {'expR':>7} {'totR':>8} {'maxDD':>7} {'eqR²':>6}"


# ─────────────────────────────────────────── main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=140)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--combo", default=None, help='forzar "or_mins,sl_mode,tp_r" y saltar el grid')
    args = ap.parse_args()

    print("\n" + "#" * 74)
    print("#  PST ORB LAB — Opening Range Breakout intradía (costes reales)")
    print(f"#  IS: {', '.join(IS_SYMBOLS)}  (primer {IS_FRAC:.0%} del histórico)")
    print(f"#  OOS temporal: mismo universo, último {1-IS_FRAC:.0%} · OOS símbolos: {', '.join(OOS_SYMBOLS)}")
    print("#" * 74)

    print("\n· Cargando datos…")
    data = {}
    for s in IS_SYMBOLS + OOS_SYMBOLS:
        d = get_data(s, args.days, args.refresh)
        if d:
            data[s] = d
            print(f"    {s:<12} {len(d['m5']):>6} barras M5 | spread={d['spread_dist']:.5f}")
    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:
        pass

    is_syms = [s for s in IS_SYMBOLS if s in data]
    oos_syms = [s for s in OOS_SYMBOLS if s in data]

    # ── FASE 1: grid pequeño, solo IS (símbolos IS × primer 70%)
    if args.combo:
        parts = args.combo.split(",")
        om, sm, tr = parts[0], parts[1], parts[2]
        be = (parts[3].strip().lower() != "nobe") if len(parts) > 3 else True
        best_combo = (int(om), sm.strip(), float(tr), be)
        print(f"\n· Combo forzado: or={best_combo[0]}min sl={best_combo[1]} tp={best_combo[2]}R be={be} (grid omitido)")
    else:
        print(f"\n· FASE 1 — grid in-sample ({len(is_syms)} símbolos, primer {IS_FRAC:.0%})")
        grid = [(om, sm, tr, be)
                for om in (30, 60)
                for sm in ("range", "mid")
                for tr in (2.0, 3.0, 8.0)
                for be in (True, False)]
        rows = []
        for om, sm, tr, be in grid:
            all_tr = []
            for s in is_syms:
                all_tr += run_orb(s, data[s], om, sm, tr, 0.0, IS_FRAC, use_be=be)
            m = metrics(all_tr)
            rows.append(((om, sm, tr, be), m))
        rows.sort(key=lambda r: r[1]["expectancy"], reverse=True)
        print(f"\n  {'combo (or,sl,tp,be)':<24} {'trades':>6} {'WR':>7} {'PF':>6} {'expR':>7} {'totR':>8} {'maxDD':>7} {'eqR2':>6}")
        print("  " + "-" * 74)
        for (om, sm, tr, be), m in rows:
            pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
            tag = "" if m["trades"] >= MIN_TRADES_GRID else "  (muestra escasa)"
            print(f"  {f'{om}min/{sm}/tp{tr:g}/{'be' if be else 'nobe'}':<24} {m['trades']:>6} {m['win_rate']:>6.1f}% {pf:>6} "
                  f"{m['expectancy']:>+7.3f} {m['total_r']:>+8.1f} {m['max_dd']:>7.1f} {m['eq_r2']:>6.2f}{tag}")
        eligible = [r for r in rows if r[1]["trades"] >= MIN_TRADES_GRID]
        if not eligible or eligible[0][1]["expectancy"] <= 0:
            print("\n  [X] NINGUN combo con muestra suficiente tiene expectancy > 0 in-sample.")
            print("      Veredicto: ORB NO tiene edge ni siquiera con tuning. NO APTA para integrar.")
            return
        best_combo = eligible[0][0]
        print(f"\n  -> Mejor combo IS elegible: or={best_combo[0]}min sl={best_combo[1]} tp={best_combo[2]:g}R be={best_combo[3]}")

    om, sm, tr, be = best_combo

    # ── FASE 2: la config ganadora, desglosada y juzgada OOS
    def eval_set(title, syms, f0, f1):
        print(f"\n· {title}")
        print(HDR)
        print("  " + "-" * 72)
        pool = []
        for s in syms:
            trs = run_orb(s, data[s], om, sm, tr, f0, f1, use_be=be)
            pool += trs
            print(fmt_row(s, metrics(trs)))
        agg = metrics(pool)
        print("  " + "-" * 72)
        print(fmt_row("AGREGADO", agg))
        return agg

    is_m = eval_set(f"IN-SAMPLE (símbolos IS, primer {IS_FRAC:.0%})", is_syms, 0.0, IS_FRAC)
    oos_t = eval_set(f"OOS TEMPORAL (símbolos IS, último {1-IS_FRAC:.0%})", is_syms, IS_FRAC, 1.0)
    oos_s = eval_set("OOS SÍMBOLOS (nunca tuneados, histórico completo)", oos_syms, 0.0, 1.0)

    # ── Veredicto
    print("\n" + "=" * 74)
    ok_is = is_m["expectancy"] > 0.03 and is_m["profit_factor"] > 1.15
    ok_t = oos_t["expectancy"] > 0.0 and oos_t["profit_factor"] > 1.0
    ok_s = oos_s["expectancy"] > 0.0 and oos_s["profit_factor"] > 1.0
    print(f"  IS:            exp {is_m['expectancy']:+.3f}R  PF {is_m['profit_factor']:.2f}  -> {'[OK]' if ok_is else '[X]'} (exige exp>0.03, PF>1.15)")
    print(f"  OOS temporal:  exp {oos_t['expectancy']:+.3f}R  PF {oos_t['profit_factor']:.2f}  -> {'[OK]' if ok_t else '[X]'} (exige exp>0, PF>1)")
    print(f"  OOS simbolos:  exp {oos_s['expectancy']:+.3f}R  PF {oos_s['profit_factor']:.2f}  -> {'[OK]' if ok_s else '[X]'} (exige exp>0, PF>1)")
    if ok_is and ok_t and ok_s:
        print("\n  [OK] VEREDICTO: APTA — el edge sobrevive fuera de muestra en tiempo y simbolo.")
        print("       Puede integrarse en produccion (config: "
              f"or_mins={om}, sl_mode={sm}, tp_r={tr:g}, be={be}).")
    else:
        print("\n  [X] VEREDICTO: NO APTA — el edge no sobrevive fuera de muestra.")
        print("      NO integrar en produccion (requisito critico del protocolo).")
    print("=" * 74 + "\n")


if __name__ == "__main__":
    main()
