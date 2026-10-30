"""
Fase 4 del plan v3 (docs/PLAN_OPERACIONES_SIMBOLOS.md): el brazo base SIN LLM, el control que nunca se montó.

Regla (la que repiten casi todos los criterios aprobados): retroceso a la EMA20 de M15 a favor del regimen H1+H4.
  * Regimen alcista: en H1 y H4, cierre > EMA200 y EMA20 > EMA50. Bajista: al reves.
  * Compra en M15 con el cierre entre -0,5 y +0,7 ATR de la EMA20, RSI14 entre 40 y 60 y cierre > cierre anterior.
    Venta: simetrica (entre -0,7 y +0,5 ATR, cierre < anterior).
  * Solo si spread / ATR de M15 <= 0,15. Una posicion a la vez por simbolo.
  * Stop 1,5 ATR de M15, objetivo 2,0 R (los valores que mas usan los criterios), salidas B y C de simulacion_salidas.py.

Costes (lo que el sistema real paga): entrada al ask / salida al bid con el spread de cada vela, y comision de ida y vuelta
segun PST_Core.config.COMMISSION_SPEC (medida en el historial del broker). El swap no se modela.
Sin mirar el futuro: la senal usa velas cerradas y se entra en la apertura de la siguiente; el regimen H1/H4 usa
solo velas ya cerradas en ese instante.

Lectura de las tres salidas posibles del plan (§3 fase 4) con el R medio por clase de este brazo frente al agente
en la misma ventana. Antes de decidir, mirar el numero de operaciones (>= 100 por brazo) y no una sola semana.

  python PST_LLM/brazo_base.py [--dias 90] [--simbolo US100.cash] [--stop 1.5] [--tp 2.0]     (necesita MT5)
"""
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import simulacion_salidas as sim                               # noqa: E402

MAX_SPREAD_ATR = 0.15
HORIZONTE_BARRAS = 96          # 24 h de M15
CALENTAMIENTO = 250
# Espejo de PST_Core.config.COMMISSION_SPEC (no se importa: arrastra dependencias de la app)
COMISION = {"FOREX": {"per_lot": 4.3}, "METAL": {"per_lot": 5.5}, "COMMODITY": {"per_lot": 5.5},
            "CRYPTO": {"pct_notional": 0.00065}, "INDEX": {"per_lot": 0.0}, "EQUITIES": {"per_lot": 0.011}}
CLASE_COMISION = {"indice": "INDEX", "accion_eeuu": "EQUITIES", "forex": "FOREX", "cripto": "CRYPTO"}
TF_S = {"M15": 900, "H1": 3600, "H4": 14400}


# ── Indicadores (vectoriales por serie, recursivos como los de MT5) ──────────
def ema(x, n):
    out, a = np.empty(len(x)), 2 / (n + 1)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def wilder(x, n):
    out = np.full(len(x), np.nan)
    if len(x) <= n:
        return out
    out[n] = np.mean(x[1:n + 1])
    for i in range(n + 1, len(x)):
        out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out


def atr(h, l, c, n=14):
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    return wilder(np.concatenate([[h[0] - l[0]], tr]), n)


def rsi(c, n=14):
    d = np.diff(c, prepend=c[0])
    return 100 - 100 / (1 + wilder(np.maximum(d, 0), n) / np.maximum(wilder(np.maximum(-d, 0), n), 1e-12))


def regimen(rates):
    """+1 alcista, -1 bajista, 0 sin regimen, por vela; con el instante en que cada vela esta CERRADA."""
    c = rates["close"].astype(float)
    e20, e50, e200 = ema(c, 20), ema(c, 50), ema(c, 200)
    r = np.where((c > e200) & (e20 > e50), 1, np.where((c < e200) & (e20 < e50), -1, 0))
    r[:CALENTAMIENTO - 50] = 0
    return r, rates["time"].astype(np.int64)


def comision_r(clase, simbolo, precio, dist, vpp):
    spec = COMISION.get(simbolo_clase(clase, simbolo), {})
    if "pct_notional" in spec:
        return spec["pct_notional"] * precio / dist
    if spec.get("per_lot") and vpp:
        return spec["per_lot"] / (dist * vpp)
    return 0.0


def simbolo_clase(clase, simbolo):
    if clase == "materias":
        return "METAL" if simbolo.startswith("XA") else "COMMODITY"
    return CLASE_COMISION.get(clase, "")


# ── Backtest de un simbolo ───────────────────────────────────────────────────
def probar_simbolo(mt5, simbolo, clase, dias, sl_mult, tp_r, desde_epoch=None):
    """Lista de operaciones {t, lado, A0, B, C (R netos de costes), salidaB, salidaC} del brazo base."""
    info = mt5.symbol_info(simbolo)
    if info is None or not mt5.symbol_select(simbolo, True):
        return None
    m15 = mt5.copy_rates_from_pos(simbolo, mt5.TIMEFRAME_M15, 0, dias * 96 + CALENTAMIENTO)
    h1 = mt5.copy_rates_from_pos(simbolo, mt5.TIMEFRAME_H1, 0, dias * 24 + CALENTAMIENTO)
    h4 = mt5.copy_rates_from_pos(simbolo, mt5.TIMEFRAME_H4, 0, dias * 6 + CALENTAMIENTO)
    if any(x is None or len(x) < CALENTAMIENTO + 10 for x in (m15, h1, h4)):
        return None
    o, h, l, c = (m15[k].astype(float) for k in ("open", "high", "low", "close"))
    a, e20, rs = atr(h, l, c), ema(c, 20), rsi(c)
    punto = info.point
    spread = m15["spread"].astype(float) * punto
    vpp = info.trade_tick_value / info.trade_tick_size if info.trade_tick_size else None
    reg1, t1 = regimen(h1)
    reg4, t4 = regimen(h4)
    t15 = m15["time"].astype(np.int64)
    ops, libre_desde = [], 0
    for i in range(CALENTAMIENTO, len(m15) - 2):
        if i < libre_desde or not a[i] > 0 or np.isnan(rs[i]):
            continue
        if desde_epoch and t15[i] < desde_epoch:
            continue
        cierre = t15[i] + TF_S["M15"]                       # instante en que la vela M15 i queda cerrada
        j1 = np.searchsorted(t1 + TF_S["H1"], cierre, side="right") - 1
        j4 = np.searchsorted(t4 + TF_S["H4"], cierre, side="right") - 1
        if j1 < 0 or j4 < 0 or reg1[j1] != reg4[j4] or reg1[j1] == 0:
            continue
        lado, d = reg1[j1], (c[i] - e20[i]) / a[i]
        if spread[i] / a[i] > MAX_SPREAD_ATR or not 40 <= rs[i] <= 60:
            continue
        if lado == 1 and not (-0.5 <= d <= 0.7 and c[i] > c[i - 1]):
            continue
        if lado == -1 and not (-0.7 <= d <= 0.5 and c[i] < c[i - 1]):
            continue
        dist = sl_mult * a[i]
        sp = spread[i + 1]
        entrada = o[i + 1] + (sp if lado == 1 else 0.0)      # compra al ask, venta al bid
        fin = min(i + 1 + HORIZONTE_BARRAS, len(m15))
        if lado == 1:                                         # salida al bid
            barras = [((l[k] - entrada) / dist, (h[k] - entrada) / dist) for k in range(i + 1, fin)]
        else:                                                 # recompra al ask = bid + spread
            barras = [((entrada - (h[k] + spread[k])) / dist, (entrada - (l[k] + spread[k])) / dist) for k in range(i + 1, fin)]
        com = comision_r(clase, simbolo, entrada, dist, vpp)
        op = {"t": int(t15[i]), "lado": int(lado), "simbolo": simbolo, "clase": clase, "comision_r": com}
        k_cierre = 0
        for regla in ("A0", "B", "C"):
            r, salida, k = sim.simular_idx(barras, tp_r, regla)
            op[regla], op["salida_" + regla] = r - com, salida
            if regla == "C":
                k_cierre = k
        libre_desde = i + 2 + k_cierre                        # una posicion a la vez (la de la regla C, la elegida)
        ops.append(op)
    return ops


def _resumen(ops, clave):
    v = [o[clave] for o in ops]
    if not v:
        return "     -"
    return f"{np.mean(v):+.3f}"


def _tabla(ops, titulo, out):
    out(f"\n{titulo}")
    out(f"  {'':<14}{'ops':>5}{'acierto':>9}{'A0':>8}{'B':>8}{'C':>8}{'coste R':>9}")
    clases = sorted({o["clase"] for o in ops})
    for nombre, sub in [("TOTAL", ops)] + [(c_, [o for o in ops if o["clase"] == c_]) for c_ in clases]:
        if not sub:
            continue
        ac = np.mean([o["C"] > 0 for o in sub]) * 100
        out(f"  {nombre:<14}{len(sub):>5}{ac:>8.0f}%{_resumen(sub, 'A0'):>8}{_resumen(sub, 'B'):>8}"
            f"{_resumen(sub, 'C'):>8}{np.mean([o['comision_r'] for o in sub]):>9.3f}")


def _agente(desde_epoch_local):
    """R medio real del agente por clase en la misma ventana (llm_operaciones)."""
    import registro
    con = sqlite3.connect(registro._db_central_path())
    filas = con.execute("SELECT clase, COUNT(*), AVG(r_real) FROM llm_operaciones WHERE r_real IS NOT NULL "
                        "AND abierta >= ? GROUP BY clase", (time.strftime("%Y-%m-%d", time.localtime(desde_epoch_local)),)).fetchall()
    tot = con.execute("SELECT COUNT(*), AVG(r_real) FROM llm_operaciones WHERE r_real IS NOT NULL AND abierta >= ?",
                      (time.strftime("%Y-%m-%d", time.localtime(desde_epoch_local)),)).fetchone()
    con.close()
    return filas, tot


def ejecutar(mt5, dias=90, simbolo=None, sl_mult=1.5, tp_r=2.0, out=print):
    import registro
    con = sqlite3.connect(registro._db_central_path())
    agentes = [(s, c_) for s, c_ in con.execute("SELECT symbol, clase FROM llm_agentes ORDER BY symbol")]
    con.close()
    if simbolo:
        agentes = [x for x in agentes if x[0] == simbolo]
    ops, sin_datos = [], []
    for s, clase in agentes:
        r = probar_simbolo(mt5, s, clase, dias, sl_mult, tp_r)
        if r is None:
            sin_datos.append(s)
        else:
            ops += r
    if not ops:
        out("Sin operaciones del brazo base (¿sin historial de velas en el terminal?).")
        return ops
    out(f"Brazo base sin LLM · {len(agentes) - len(sin_datos)} simbolos · {dias} dias · stop {sl_mult} ATR M15, "
        f"objetivo {tp_r} R · R medio NETO de spread y comision")
    if sin_datos:
        out(f"  sin historial suficiente: {', '.join(sin_datos)}")
    _tabla(ops, f"Todo el periodo ({dias} dias)", out)
    semana = int(time.time()) - 7 * 86400 + sim.desfase_broker_s()
    ult = [o for o in ops if o["t"] >= semana]
    _tabla(ult, "Ultima semana (misma ventana que el agente)", out)
    filas, tot = _agente(time.time() - 7 * 86400)
    out("\nAgente LLM en esa misma ventana (R medio real): "
        + " · ".join(f"{c_} {n}: {r:+.2f}" for c_, n, r in filas) + (f" · TOTAL {tot[0]}: {tot[1]:+.2f}" if tot[0] else ""))
    return ops


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Brazo base sin LLM: retroceso a EMA20 de M15 a favor de H1+H4")
    ap.add_argument("--dias", type=int, default=90)
    ap.add_argument("--simbolo")
    ap.add_argument("--stop", type=float, default=1.5)
    ap.add_argument("--tp", type=float, default=2.0)
    a = ap.parse_args()
    import MetaTrader5 as mt5
    if not mt5.initialize():
        print(f"MT5 no disponible: {mt5.last_error()}", file=sys.stderr)
        sys.exit(1)
    try:
        ejecutar(mt5, a.dias, a.simbolo, a.stop, a.tp)
    finally:
        mt5.shutdown()
