#!/usr/bin/env python3
"""
PST · FADE LAB — segunda familia de hipótesis intradía (reversión), con el mismo rigor
──────────────────────────────────────────────────────────────────────────────────────
El ORB LAB demostró que las rupturas de apertura PIERDEN de forma sistemática en este
bróker/universo (24/24 combos negativos in-sample). Este lab prueba las dos hipótesis
inversas, con el MISMO protocolo IS/OOS y los mismos costes reales:

  A) GAPFADE — reversión del movimiento overnight en índices US: si el precio a la
     apertura de NY (16:30 bróker) se ha alejado del cierre de caja anterior (23:00
     bróker) más de un umbral en ATR diarios, operar DE VUELTA hacia ese cierre.
     TP = cierre anterior (gap fill) · SL = fracción del movimiento, más allá.
     Cierre forzado 22:45. 0-1 trade/día.

  B) ORBFADE — fade del breakout: cuando una vela M5 cierra fuera del Opening Range
     de 30 min (el trigger EXACTO que pierde dinero en el ORB LAB), entrar en contra.
     TP = medio del rango u opuesto · SL = k × rango, más allá de la entrada.

Reutiliza datos cacheados, costes y métricas de pst_orb_lab (mismas barras idénticas).

Uso:
    python Tools/pst_fade_lab.py            # ambas hipótesis, protocolo completo
"""
import sys
import os
from datetime import time as dtime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Tools.pst_orb_lab import (  # noqa: E402
    get_data, metrics, commission_r, session_for, asset_class,
    HDR, fmt_row, COMMISSION_SPEC, BUF_FRAC, IS_FRAC,
)

# GAPFADE: solo índices US/EU (el "gap" es el drift overnight vs el cierre de caja).
GAP_IS = ["US500.cash"]
GAP_OOS = ["GER40.cash", "EU50.cash"]
# ORBFADE: mismo universo que el ORB LAB.
ORB_IS = ["US500.cash", "XAUUSD", "EURUSD", "BTCUSD"]
ORB_OOS = ["EU50.cash", "GER40.cash", "GBPUSD", "USDJPY", "ETHUSD"]

MIN_TRADES_GRID = 50


def _daily_atr(df):
    """ATR diario aproximado (media de high-low de los 14 días PREVIOS, sin lookahead).
    Devuelve dict fecha -> atr."""
    daily = df.groupby(df["time"].dt.date).agg(h=("high", "max"), l=("low", "min"))
    rng = (daily["h"] - daily["l"])
    atr = rng.rolling(14).mean().shift(1)          # solo días previos
    return atr.to_dict()


# ─────────────────────────────────────────── A) GAPFADE
def run_gapfade(symbol, data, g_min, sl_frac, frac_from=0.0, frac_to=1.0):
    df = data["m5"]
    n = len(df)
    df = df.iloc[int(n * frac_from):int(n * frac_to)]
    if df.empty:
        return []
    spec = COMMISSION_SPEC.get(asset_class(symbol), {})
    tv, ts = data.get("tick_value"), data.get("tick_size")
    vpp = (tv / ts) if (tv and ts) else None
    spread = data.get("spread_dist", 0.0) or 0.0
    hs = spread / 2.0
    atr_by_day = _daily_atr(df)

    t_open, t_eod = dtime(16, 30), dtime(22, 45)
    t_cash_close = dtime(23, 0)

    trades = []
    prev_close = None
    prev_day = None
    for day, g in df.groupby(df["time"].dt.date):
        g = g.reset_index(drop=True)
        tt = g["time"].dt.time
        atrd = atr_by_day.get(day)
        # cierre de caja del día (última barra <= 23:00) para usar mañana
        cash = g[tt <= t_cash_close]
        this_close = float(cash["close"].iloc[-1]) if len(cash) else None

        sess = g[(tt >= t_open) & (tt <= t_eod)].reset_index(drop=True)
        if prev_close is not None and atrd and atrd > 0 and len(sess) > 3 \
                and prev_day is not None and (day - prev_day).days <= 4:
            open_bar = sess.iloc[0]
            open_px = float(open_bar["close"])       # entrada al cierre de la 1ª vela
            move = open_px - prev_close
            if abs(move) >= g_min * atrd:
                d = -1 if move > 0 else 1            # fade: contra el movimiento overnight
                fill = open_px + d * hs
                tp = prev_close                       # gap fill
                sl = fill - d * sl_frac * abs(move)
                R = abs(fill - sl)
                if R > 0 and abs(tp - fill) > 0:
                    comm = commission_r(spec, fill, R, vpp)
                    pos = {"sl": sl, "tp": tp, "be": False}
                    closed = False
                    for k in range(1, len(sess)):
                        bar = sess.iloc[k]
                        h, l, c = float(bar["high"]), float(bar["low"]), float(bar["close"])
                        if d == 1:
                            if l <= pos["sl"]:
                                px, reason, closed = pos["sl"], "SL", True
                            elif h >= pos["tp"]:
                                px, reason, closed = pos["tp"], "TP", True
                        else:
                            if h >= pos["sl"]:
                                px, reason, closed = pos["sl"], "SL", True
                            elif l <= pos["tp"]:
                                px, reason, closed = pos["tp"], "TP", True
                        if not closed and k == len(sess) - 1:
                            px, reason, closed = c, "EOD", True
                        if closed:
                            eff = px - d * hs
                            pnl = d * (eff - fill) / R - comm
                            trades.append({"pnl_r": pnl, "entry_time": open_bar["time"],
                                           "exit_time": bar["time"], "exit_reason": reason,
                                           "direction": d, "symbol": symbol})
                            break
        if this_close is not None:
            prev_close = this_close
            prev_day = day
    return trades


# ─────────────────────────────────────────── B) ORBFADE
def run_orbfade(symbol, data, tp_mode, sl_k, frac_from=0.0, frac_to=1.0):
    df = data["m5"]
    n = len(df)
    df = df.iloc[int(n * frac_from):int(n * frac_to)]
    if df.empty:
        return []
    sess = session_for(symbol)
    spec = COMMISSION_SPEC.get(asset_class(symbol), {})
    tv, ts = data.get("tick_value"), data.get("tick_size")
    vpp = (tv / ts) if (tv and ts) else None
    spread = data.get("spread_dist", 0.0) or 0.0
    hs = spread / 2.0

    t_open, t_eod = sess["open"], sess["eod"]
    or_end = dtime(t_open.hour, t_open.minute + 30) if t_open.minute + 30 < 60 \
        else dtime(t_open.hour + 1, t_open.minute + 30 - 60)
    cut = dtime(min(23, t_open.hour + 4), t_open.minute)

    trades = []
    for day, g in df.groupby(df["time"].dt.date):
        g = g.reset_index(drop=True)
        tt = g["time"].dt.time
        or_bars = g[(tt >= t_open) & (tt < or_end)]
        if len(or_bars) < 3:
            continue
        orh, orl = float(or_bars["high"].max()), float(or_bars["low"].min())
        rng = orh - orl
        if rng <= 0:
            continue
        buf = BUF_FRAC * rng
        mid = (orh + orl) / 2.0
        after = g[(tt >= or_end) & (tt <= t_eod)].reset_index(drop=True)

        pos = None
        for k in range(len(after)):
            bar = after.iloc[k]
            btime = bar["time"].time()
            h, l, c = float(bar["high"]), float(bar["low"]), float(bar["close"])
            if pos is None:
                if btime >= cut:
                    break
                d = 0
                if c > orh + buf:
                    d = -1                      # fade del breakout alcista
                elif c < orl - buf:
                    d = 1                       # fade del breakout bajista
                if d == 0:
                    continue
                fill = c + d * hs
                tp = mid if tp_mode == "mid" else (orl if d == -1 else orh)
                if (d == 1 and tp <= fill) or (d == -1 and tp >= fill):
                    continue                    # tp del lado equivocado (cierre dentro del rango)
                sl = fill - d * sl_k * rng
                R = abs(fill - sl)
                if R <= 0 or abs(tp - fill) <= 0:
                    continue
                comm = commission_r(spec, fill, R, vpp)
                pos = {"d": d, "fill": fill, "sl": sl, "tp": tp, "R": R, "comm": comm,
                       "entry_time": bar["time"]}
                continue

            d, fill, R = pos["d"], pos["fill"], pos["R"]
            closed = False
            if d == 1:
                if l <= pos["sl"]:
                    px, reason, closed = pos["sl"], "SL", True
                elif h >= pos["tp"]:
                    px, reason, closed = pos["tp"], "TP", True
            else:
                if h >= pos["sl"]:
                    px, reason, closed = pos["sl"], "SL", True
                elif l <= pos["tp"]:
                    px, reason, closed = pos["tp"], "TP", True
            if not closed and (btime >= t_eod or k == len(after) - 1):
                px, reason, closed = c, "EOD", True
            if closed:
                eff = px - d * hs
                pnl = d * (eff - fill) / R - pos["comm"]
                trades.append({"pnl_r": pnl, "entry_time": pos["entry_time"],
                               "exit_time": bar["time"], "exit_reason": reason,
                               "direction": d, "symbol": symbol})
                pos = None
                break                            # 1 trade/día
    return trades


# ─────────────────────────────────────────── protocolo
def protocol(name, runner, grid, is_syms, oos_syms, data):
    print("\n" + "#" * 74)
    print(f"#  {name}")
    print("#" * 74)
    rows = []
    for params in grid:
        pool = []
        for s in is_syms:
            if s in data:
                pool += runner(s, data[s], *params, 0.0, IS_FRAC)
        rows.append((params, metrics(pool)))
    rows.sort(key=lambda r: r[1]["expectancy"], reverse=True)
    print(f"\n  {'combo':<22} {'trades':>6} {'WR':>7} {'PF':>6} {'expR':>7} {'totR':>8} {'maxDD':>7} {'eqR2':>6}")
    print("  " + "-" * 72)
    for params, m in rows:
        pf = "inf" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
        tag = "" if m["trades"] >= MIN_TRADES_GRID else "  (muestra escasa)"
        print(f"  {str(params):<22} {m['trades']:>6} {m['win_rate']:>6.1f}% {pf:>6} "
              f"{m['expectancy']:>+7.3f} {m['total_r']:>+8.1f} {m['max_dd']:>7.1f} {m['eq_r2']:>6.2f}{tag}")
    eligible = [r for r in rows if r[1]["trades"] >= MIN_TRADES_GRID and r[1]["expectancy"] > 0]
    if not eligible:
        print(f"\n  [X] {name}: sin combo elegible con expectancy > 0 in-sample. NO APTA.")
        return None
    best = eligible[0][0]
    print(f"\n  -> Mejor combo IS: {best} — validando OOS…")

    def eval_set(title, syms, f0, f1):
        pool = []
        print(f"\n· {title}")
        print(HDR)
        print("  " + "-" * 72)
        for s in syms:
            if s not in data:
                continue
            trs = runner(s, data[s], *best, f0, f1)
            pool += trs
            print(fmt_row(s, metrics(trs)))
        agg = metrics(pool)
        print("  " + "-" * 72)
        print(fmt_row("AGREGADO", agg))
        return agg

    oos_t = eval_set("OOS TEMPORAL (simbolos IS, ultimo 30%)", is_syms, IS_FRAC, 1.0)
    oos_s = eval_set("OOS SIMBOLOS (nunca tuneados, historico completo)", oos_syms, 0.0, 1.0)
    ok = oos_t["expectancy"] > 0 and oos_t["profit_factor"] > 1.0 \
        and oos_s["expectancy"] > 0 and oos_s["profit_factor"] > 1.0
    print(f"\n  VEREDICTO {name}: {'[OK] APTA (pasa OOS)' if ok else '[X] NO APTA (falla OOS)'}")
    return best if ok else None


def main():
    syms = sorted(set(GAP_IS + GAP_OOS + ORB_IS + ORB_OOS))
    data = {}
    for s in syms:
        d = get_data(s, 140)
        if d:
            data[s] = d
    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:
        pass

    # A) GAPFADE: grid (g_min en ATR diarios, sl_frac del movimiento)
    protocol("A) GAPFADE — reversión del drift overnight (índices US/EU)",
             run_gapfade,
             [(g, s) for g in (0.25, 0.4, 0.6) for s in (0.5, 1.0)],
             GAP_IS, GAP_OOS, data)

    # B) ORBFADE: grid (tp_mode, sl_k × rango)
    protocol("B) ORBFADE — fade del breakout de apertura",
             run_orbfade,
             [(tp, k) for tp in ("mid", "opposite") for k in (0.75, 1.25)],
             ORB_IS, ORB_OOS, data)


if __name__ == "__main__":
    main()
