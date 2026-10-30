"""
Metricas de la flota LLM: por agente (expectancy, perdida maxima, racha de perdidas, duracion) y por tramo de
confianza que el modelo declara en cada decision. Lee llm_operaciones y llm_decisions de la BBDD central: no
necesita MT5.

La confianza se cruza con la operacion por simbolo e instante de entrada (llm_decisions.ts = llm_operaciones.abierta),
asi que solo cuentan las operaciones cuya decision quedo registrada en la central.

  python PST_LLM/metricas.py                    # todo
  python PST_LLM/metricas.py --desde 2026-10-23
  python PST_LLM/metricas.py --clase cripto
"""
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import informe_operaciones as io                              # noqa: E402
import registro                                               # noqa: E402

TRAMOS_CONFIANZA = [(0.40, "< 0.40"), (0.45, "0.40-0.44"), (0.50, "0.45-0.49"), (0.60, "0.50-0.59")]
TRAMO_ALTO = ">= 0.60"


def tramo_confianza(c):
    if c is None:
        return "?"
    for corte, etiqueta in TRAMOS_CONFIANZA:
        if c < corte:
            return etiqueta
    return TRAMO_ALTO


def metricas(ops):
    """Resumen de una lista de operaciones cerradas. Perdida maxima en EUR: la mayor caida desde un maximo del neto acumulado."""
    if not ops:
        return None
    acumulado = pico = peor = 0.0
    racha = racha_max = 0
    for o in sorted(ops, key=lambda o: o["abierta"]):
        acumulado += o["neto"]
        pico = max(pico, acumulado)
        peor = min(peor, acumulado - pico)
        racha = racha + 1 if o["neto"] <= 0 else 0
        racha_max = max(racha_max, racha)
    n = len(ops)
    rs = [o["r_real"] for o in ops if o["r_real"] is not None]
    mins = [o["minutos"] for o in ops if o["minutos"] is not None]
    return {"n": n, "aciertos": sum(1 for o in ops if o["neto"] > 0) / n, "neto": acumulado,
            "expectancy_r": sum(rs) / len(rs) if rs else None, "perdida_max": peor, "racha_perdidas": racha_max,
            "minutos": sum(mins) / len(mins) if mins else None}


def por_agente(ops):
    grupos = defaultdict(list)
    for o in ops:
        grupos[o["symbol"]].append(o)
    return {sym: metricas(g) for sym, g in sorted(grupos.items())}


def por_confianza(con, desde=None):
    """Operaciones cerradas agrupadas por tramo de confianza de su decision de entrada."""
    sql = ("SELECT d.confianza, o.neto, o.abierta, o.r_real, o.minutos FROM llm_operaciones o JOIN llm_decisions d "
           "ON d.symbol = o.symbol AND d.ts = o.abierta AND d.accion IN ('buy', 'sell') AND d.ejecutado = 1 "
           "WHERE o.neto IS NOT NULL")
    args = []
    if desde:
        sql += " AND o.abierta >= ?"
        args.append(desde)
    grupos = defaultdict(list)
    con.row_factory = sqlite3.Row
    for r in con.execute(sql, args):
        grupos[tramo_confianza(r["confianza"])].append(dict(r))
    return {t: metricas(g) for t, g in grupos.items()}


def _linea(nombre, m):
    r = f"{m['expectancy_r']:+.2f}" if m["expectancy_r"] is not None else "   ?"
    marca = "" if m["n"] >= io.MINIMO_FIABLE else "  (muestra corta)"
    return (f"  {nombre:<14}{m['n']:>5}{m['aciertos'] * 100:>8.0f}%{m['neto']:>10.0f}{r:>9}"
            f"{m['perdida_max']:>10.0f}{m['racha_perdidas']:>8}{(m['minutos'] or 0):>9.0f}{marca}")


CABECERA = f"  {'':<14}{'ops':>5}{'acierto':>9}{'neto EUR':>10}{'R medio':>9}{'DD EUR':>10}{'racha':>8}{'min':>9}"


def imprimir(desde=None, clase=None, out=print):
    ops = io.leer(desde, clase)
    if not ops:
        out("Sin operaciones en llm_operaciones: ejecuta  python PST_LLM/operaciones.py --rellenar")
        return
    out("POR AGENTE (expectancy = R medio · DD = caida maxima desde un maximo · racha = perdidas seguidas)")
    out(CABECERA)
    for sym, m in por_agente(ops).items():
        out(_linea(sym, m))
    con = registro.central()
    try:
        tramos = por_confianza(con, desde)
    finally:
        con.close()
    out("\nPOR CONFIANZA DECLARADA (solo operaciones con su decision registrada en la central)")
    if not tramos:
        out("  Sin cruces: no hay decisiones de entrada registradas para estas operaciones.")
        return
    out(CABECERA)
    for etiqueta in ["< 0.40", "0.40-0.44", "0.45-0.49", "0.50-0.59", TRAMO_ALTO, "?"]:
        if etiqueta in tramos:
            out(_linea(etiqueta, tramos[etiqueta]))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Metricas por agente y por confianza de la flota LLM")
    ap.add_argument("--desde")
    ap.add_argument("--clase")
    a = ap.parse_args()
    imprimir(a.desde, a.clase)
