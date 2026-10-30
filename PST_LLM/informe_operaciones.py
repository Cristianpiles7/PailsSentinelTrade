"""
Informe de las operaciones de la flota LLM por cortes (plan v3, Fase 0): marco del stop, objetivo, salida,
clase, hora de entrada, regimen H1/H4 y coste. Lee `llm_operaciones` (ver operaciones.py), no necesita MT5.

  python PST_LLM/informe_operaciones.py [--desde 2026-10-23] [--clase cripto]
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import registro                                               # noqa: E402

MINIMO_FIABLE = 20       # por debajo, el corte es ruido (plan §5)


def _tramo(valor, cortes, etiquetas):
    if valor is None:
        return "?"
    for corte, etiqueta in zip(cortes, etiquetas):
        if valor <= corte:
            return etiqueta
    return etiquetas[-1]


CORTES = {
    "marco del stop": lambda o: o["marco_stop"] or "?",
    "objetivo (tp_r)": lambda o: _tramo(o["tp_r"], (1.5, 2.5), ("<= 1.5", "1.5-2.5", "> 2.5")),
    "salida": lambda o: o["motivo_salida"] or "?",
    "clase": lambda o: o["clase"] or "?",
    "hora de entrada": lambda o: f"{(o['hora_entrada'] or 0) // 4 * 4:02d}-{(o['hora_entrada'] or 0) // 4 * 4 + 3:02d} h",
    "regimen H1/H4": lambda o: o["regimen_h1_h4"] or "?",
    "coste (% del R)": lambda o: _tramo(o["coste_r"], (0.05, 0.10), ("<= 5%", "5-10%", "> 10%")),
    "lado": lambda o: o["lado"] or "?",
}


def resumen(ops):
    n = len(ops)
    rs = [o["r_real"] for o in ops if o["r_real"] is not None]
    return {"n": n, "aciertos": sum(1 for o in ops if o["neto"] > 0) / n if n else 0,
            "neto": sum(o["neto"] for o in ops), "r_medio": sum(rs) / len(rs) if rs else None}


def _linea(nombre, ops):
    s = resumen(ops)
    r = f"{s['r_medio']:+.2f}" if s["r_medio"] is not None else "   ?"
    marca = "" if s["n"] >= MINIMO_FIABLE else "  (muestra corta)"
    return f"  {nombre:<22}{s['n']:>5}{s['aciertos'] * 100:>8.0f}%{s['neto']:>10.0f}{r:>9}{marca}"


def informe(ops, out=print):
    if not ops:
        out("Sin operaciones en llm_operaciones: ejecuta  python PST_LLM/operaciones.py --rellenar")
        return
    total = resumen(ops)
    brutas = [o for o in ops if o["bruto"] is not None]
    out(f"{total['n']} operaciones · aciertos {total['aciertos'] * 100:.0f}% · neto {total['neto']:+.0f} EUR · "
        f"R medio {total['r_medio']:+.2f}" if total["r_medio"] is not None else f"{total['n']} operaciones")
    if brutas:
        out(f"  con detalle de MT5: {len(brutas)} · comision {sum(o['comision'] for o in brutas):+.0f} · "
            f"swap {sum(o['swap'] for o in brutas):+.0f}")
    else:
        out("  AVISO: sin datos de MT5 (fuente 'trades'): no hay comision, motivo de salida ni MAE/MFE.")
    for titulo, clave in CORTES.items():
        grupos = {}
        for o in ops:
            grupos.setdefault(clave(o), []).append(o)
        out(f"\n{titulo}\n  {'':<22}{'ops':>5}{'acierto':>9}{'neto EUR':>10}{'R medio':>9}")
        for g in sorted(grupos):
            out(_linea(g, grupos[g]))
    mae = [o["mae_r"] for o in ops if o["mae_r"] is not None]
    mfe = [o["mfe_r"] for o in ops if o["mfe_r"] is not None]
    if mae:
        mediana = lambda v: sorted(v)[len(v) // 2]
        perdidas = [o for o in ops if o["mae_r"] is not None and o["neto"] <= 0]
        out(f"\nMAE/MFE (R): mediana MAE {mediana(mae):.2f} · mediana MFE {mediana(mfe):.2f}")
        if perdidas:
            llegaron = sum(1 for o in perdidas if o["mfe_r"] >= 1.0)
            out(f"  perdedoras que llegaron a +1 R antes de perder: {llegaron}/{len(perdidas)}")
        ganadoras = [o for o in ops if o["mfe_r"] is not None and o["neto"] > 0 and o["r_real"] is not None]
        if ganadoras:
            dejado = sum(o["mfe_r"] - o["r_real"] for o in ganadoras) / len(ganadoras)
            out(f"  ganadoras: R dejado sobre la mesa de media (MFE - R real): {dejado:.2f}")


def leer(desde=None, clase=None):
    con = registro.central()
    con.row_factory = sqlite3.Row
    from operaciones import tabla
    tabla(con)
    sql, args = "SELECT * FROM llm_operaciones WHERE neto IS NOT NULL", []
    if desde:
        sql += " AND abierta >= ?"
        args.append(desde)
    if clase:
        sql += " AND clase = ?"
        args.append(clase)
    ops = [dict(r) for r in con.execute(sql + " ORDER BY abierta", args)]
    con.close()
    return ops


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Informe de operaciones de la flota LLM por cortes")
    ap.add_argument("--desde")
    ap.add_argument("--clase")
    a = ap.parse_args()
    informe(leer(a.desde, a.clase))
