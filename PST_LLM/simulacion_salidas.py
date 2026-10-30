"""
Fase 3 del plan v3 (docs/PLAN_OPERACIONES_SIMBOLOS.md): salidas por reglas. Repite las MISMAS entradas reales con
distintas reglas de salida, sobre velas M1 de MT5, sin gastar cuota ni arriesgar dinero.

Brazos:
  A   lo que paso de verdad: SL + TP + cierres del agente / revisor (r_real de llm_operaciones).
  A0  SL + TP fijos, sin cierres a mano. Es el brazo A sin intervencion: aisla lo que cuestan los cierres a mano.
  B   como A0, y al llegar a +1 R el stop pasa a la entrada (break-even).
  C   como B, mas cierre del 50 % a +1,5 R y el resto con stop en +0,5 R.

Lectura de las velas (pesimista): dentro de una misma vela manda el stop; los movimientos de stop (BE, +0,5 R)
valen desde la vela siguiente. Las velas son bid: el spread no se simula (A0, B y C son comparables entre si,
A trae el spread real). El coste de comision y swap de cada operacion real se resta igual en todos los brazos.
Criterio de paso a la fase 4: >= 150 operaciones y una regla con R medio mejor que A0 y A.

  python PST_LLM/simulacion_salidas.py [--desde 2026-10-23]     (necesita MT5 y llm_operaciones con fuente 'mt5')
"""
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # raiz del repo: PST_Core
from PST_Core.utils.reloj_mt5 import desfase_broker_s            # noqa: E402

HORIZONTE_S = 86400            # una operacion no se sigue mas de 24 h
BE_R, PARCIAL_R, RESTO_STOP_R = 1.0, 1.5, 0.5
MINIMO_PASO = 150


def simular(barras_r, tp_r, regla):
    """barras_r: [(minimo_R, maximo_R)] tras la entrada, con la entrada en 0 y el stop inicial en -1.
    Devuelve (R bruto, salida). 'abierta' si ninguna vela toco nada: se valora al ultimo extremo conservador."""
    r, salida, _ = simular_idx(barras_r, tp_r, regla)
    return r, salida


def simular_idx(barras_r, tp_r, regla):
    """Como simular, y ademas el indice de la vela en que se cerro la operacion (len-1 si sigue abierta)."""
    stop, abierto, acum, parcial = -1.0, 1.0, 0.0, False
    for k, (lo, hi) in enumerate(barras_r):
        if lo <= stop:
            return acum + abierto * stop, ("SL" if stop <= -1.0 else "BE" if stop == 0.0 else f"stop+{stop:g}R"), k
        if regla == "C" and not parcial and hi >= PARCIAL_R and tp_r > PARCIAL_R:
            acum, abierto, parcial = acum + 0.5 * PARCIAL_R, 0.5, True
        if hi >= tp_r:
            return acum + abierto * tp_r, "TP", k
        if regla in ("B", "C") and hi >= BE_R:
            stop = max(stop, 0.0)
        if parcial:
            stop = max(stop, RESTO_STOP_R)
    ultimo = barras_r[-1] if barras_r else (0.0, 0.0)
    return acum + abierto * ultimo[0], "abierta", max(len(barras_r) - 1, 0)


def a_barras_r(lado, entrada, dist, barras):
    """Velas de MT5 -> (minimo_R, maximo_R) respecto a la entrada, en el sentido de la operacion."""
    if lado == "buy":
        return [((float(b["low"]) - entrada) / dist, (float(b["high"]) - entrada) / dist) for b in barras]
    return [((entrada - float(b["high"])) / dist, (entrada - float(b["low"])) / dist) for b in barras]


def resumir(resultados):
    n = len(resultados)
    if not n:
        return None
    return {"n": n, "r_medio": sum(r for r, _ in resultados) / n,
            "acierto": sum(1 for r, _ in resultados if r > 0) / n,
            "r_total": sum(r for r, _ in resultados)}


def _operaciones(mt5, desde):
    """Operaciones simulables. El historial de deals de MT5 solo llega a donde el terminal lo tenga cargado
    (en esta instalacion, ~1 dia); las mas antiguas (fuente 'trades') se reconstruyen con el precio de entrada,
    SL y TP de la tabla `trades` y la hora de la decision. Las velas M1 si llegan a toda la semana."""
    con = sqlite3.connect(_db())
    con.row_factory = sqlite3.Row
    filas = con.execute("SELECT ticket, symbol, lado, riesgo_eur, comision, swap, r_real, motivo_salida, abierta, fuente "
                        "FROM llm_operaciones WHERE riesgo_eur > 0 AND r_real IS NOT NULL AND abierta >= ?",
                        (desde or "",)).fetchall()
    out = []
    for f in filas:
        f = dict(f)
        if f["fuente"] == "mt5":
            deals = mt5.history_deals_get(position=f["ticket"]) or []
            ent = [d for d in deals if d.entry == 0]
            sl = tp = None
            for o in (mt5.history_orders_get(position=f["ticket"]) or []):
                if o.sl or o.tp:
                    sl, tp = o.sl or None, o.tp or None
                    break
            precio, t_ent = (ent[0].price, int(ent[0].time)) if ent else (None, None)
        else:
            t = con.execute("SELECT price_in, sl, tp FROM trades WHERE ticket=?", (f["ticket"],)).fetchone()
            precio, sl, tp = (t if t else (None, None, None))
            # Hora de la decision (local) -> epoch del servidor. La orden se ejecuta segundos despues.
            t_ent = int(datetime.strptime(f["abierta"], "%Y-%m-%d %H:%M:%S").timestamp()) + desfase_broker_s()
        if not precio or not sl or not tp:
            continue
        dist = abs(precio - sl)
        if dist <= 0:
            continue
        hasta = min(t_ent + HORIZONTE_S, int(mt5.symbol_info_tick(f["symbol"]).time))
        barras = mt5.copy_rates_range(f["symbol"], mt5.TIMEFRAME_M1, t_ent + 60, hasta)
        if barras is None or not len(barras):
            continue
        out.append({"f": f, "tp_r": abs(tp - precio) / dist, "barras_r": a_barras_r(f["lado"], precio, dist, barras)})
    con.close()
    return out


def _db():
    import registro
    return registro._db_central_path()


def ejecutar(mt5, desde=None, out=print):
    ops = _operaciones(mt5, desde)
    if not ops:
        out("Sin operaciones simulables (llm_operaciones con fuente 'mt5': ejecuta operaciones.py --rellenar).")
        return
    # Sin deals de MT5 no se conoce la comision: se usa la mediana de las que si (en R, negativa).
    conocidos = sorted((o["f"]["comision"] + o["f"]["swap"]) / o["f"]["riesgo_eur"] for o in ops if o["f"]["fuente"] == "mt5")
    coste_tipico = conocidos[len(conocidos) // 2] if conocidos else 0.0
    brazos = {"A (real)": [], "A0 (SL+TP)": [], "B (BE a +1R)": [], "C (BE + 50% a +1,5R)": []}
    por_salida = {}
    for o in ops:
        f = o["f"]
        costes = ((f["comision"] + f["swap"]) / f["riesgo_eur"]) if f["fuente"] == "mt5" else coste_tipico
        brazos["A (real)"].append((f["r_real"], f["motivo_salida"]))
        for nombre, regla in (("A0 (SL+TP)", "A0"), ("B (BE a +1R)", "B"), ("C (BE + 50% a +1,5R)", "C")):
            r, salida = simular(o["barras_r"], o["tp_r"], regla)
            brazos[nombre].append((r + costes, salida))
            if regla == "A0" and f["motivo_salida"] in ("agente", "revisor"):
                por_salida.setdefault(f["motivo_salida"], []).append((f["r_real"], r + costes))
    n_mt5 = sum(1 for o in ops if o["f"]["fuente"] == "mt5")
    out(f"{len(ops)} operaciones reales repetidas sobre velas M1 (horizonte {HORIZONTE_S // 3600} h) · {n_mt5} con deals "
        f"de MT5, {len(ops) - n_mt5} desde `trades` (comision tipica {coste_tipico:+.3f} R)\n")
    out(f"  {'brazo':<24}{'ops':>5}{'acierto':>9}{'R medio':>9}{'R total':>9}")
    for nombre, res in brazos.items():
        s = resumir(res)
        out(f"  {nombre:<24}{s['n']:>5}{s['acierto'] * 100:>8.0f}%{s['r_medio']:>+9.2f}{s['r_total']:>+9.1f}")
    for quien, pares in por_salida.items():
        real = sum(a for a, _ in pares) / len(pares)
        sin = sum(b for _, b in pares) / len(pares)
        out(f"\nCierres de {quien}: {len(pares)} · R medio real {real:+.2f} · habrian dado {sin:+.2f} dejando SL+TP")
    mejor = max((n for n in brazos if n[0] in "BC"), key=lambda n: resumir(brazos[n])["r_medio"])
    base = max(resumir(brazos["A (real)"])["r_medio"], resumir(brazos["A0 (SL+TP)"])["r_medio"])
    gana = resumir(brazos[mejor])["r_medio"] > base
    out(f"\nMejor regla: {mejor} · {'supera' if gana else 'NO supera'} a A y A0")
    out(f"Criterio de paso (>= {MINIMO_PASO} operaciones): {'cumple' if gana and len(ops) >= MINIMO_PASO else 'no cumple'}"
        f" ({len(ops)} operaciones)")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Simula reglas de salida sobre las entradas reales")
    ap.add_argument("--desde")
    a = ap.parse_args()
    import MetaTrader5 as mt5
    if not mt5.initialize():
        print(f"MT5 no disponible: {mt5.last_error()}", file=sys.stderr)
        sys.exit(1)
    try:
        ejecutar(mt5, a.desde)
    finally:
        mt5.shutdown()
