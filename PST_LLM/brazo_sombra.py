"""
Brazo base en SOMBRA (plan v3, fase 4): la regla de brazo_base.py aplicada a los mismos simbolos que el agente, en
paralelo y sin enviar ordenes. Cada operacion hipotetica queda en `llm_brazo_base` (BBDD central) y se compara con las
reales del agente en `llm_operaciones`.

No hace falta un proceso propio: la regla es determinista sobre velas cerradas, asi que se recalcula sobre los ultimos
dias y se guarda con upsert por (simbolo, instante). El revisor lo llama por lotes (actualizar). Las operaciones aun
abiertas se marcan cerrada=0 y se reevaluan en la siguiente pasada.

  python PST_LLM/brazo_sombra.py --actualizar          (necesita MT5)
  python PST_LLM/brazo_sombra.py --comparar [--dias 7]
"""
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import registro                                               # noqa: E402

DIAS_CALCULO = 10          # los primeros 3 sirven de calentamiento (posicion en curso al empezar); se guardan 7
DIAS_GUARDADO = 7
_turno = {"i": 0}


def tabla(con):
    con.execute("""CREATE TABLE IF NOT EXISTS llm_brazo_base (
        simbolo TEXT, t INTEGER, clase TEXT, lado INTEGER, a0 REAL, b REAL, c REAL, comision_r REAL,
        salida_c TEXT, cerrada INTEGER, actualizado TEXT, PRIMARY KEY (simbolo, t))""")


def actualizar(mt5, lote=6, log=print):
    """Reevalua `lote` simbolos (rotando) y guarda sus operaciones. Nunca lanza."""
    try:
        import brazo_base, simulacion_salidas as sim
        con = registro.central()
        tabla(con)
        agentes = [tuple(r) for r in con.execute("SELECT symbol, clase FROM llm_agentes ORDER BY symbol")]
        if not agentes:
            return 0
        desde = int(time.time()) - DIAS_GUARDADO * 86400 + sim.desfase_broker_s()
        n = 0
        for _ in range(min(lote, len(agentes))):
            simbolo, clase = agentes[_turno["i"] % len(agentes)]
            _turno["i"] += 1
            ops = brazo_base.probar_simbolo(mt5, simbolo, clase, DIAS_CALCULO, 1.5, 2.0)
            for o in ops or []:
                if o["t"] < desde:
                    continue
                con.execute("INSERT OR REPLACE INTO llm_brazo_base VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (simbolo, o["t"], clase, o["lado"], o["A0"], o["B"], o["C"], o["comision_r"], o["salida_C"],
                             0 if o["salida_C"] == "abierta" else 1, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                n += 1
        con.commit()
        con.close()
        return n
    except Exception as e:
        log(f"[brazo_sombra] no se pudo actualizar: {type(e).__name__}: {e}")
        return 0


def comparar(dias=7, out=print):
    """R medio por clase del brazo base (salida C, solo cerradas) frente al agente en la misma ventana."""
    import sqlite3
    sim_dias = time.time() - dias * 86400
    con = registro.central()
    tabla(con)
    import simulacion_salidas as sim
    try:
        desfase = sim.desfase_broker_s()
    except Exception:
        desfase = 0                                      # sin MT5 (informe en frio): ventana en hora local
    base = con.execute("SELECT clase, COUNT(*), AVG(a0), AVG(b), AVG(c) FROM llm_brazo_base WHERE cerrada=1 AND t >= ? "
                       "GROUP BY clase", (int(sim_dias) + desfase,)).fetchall()
    agente = dict((c, (n, r)) for c, n, r in con.execute(
        "SELECT clase, COUNT(*), AVG(r_real) FROM llm_operaciones WHERE r_real IS NOT NULL AND abierta >= ? GROUP BY clase",
        (time.strftime("%Y-%m-%d", time.localtime(sim_dias)),)))
    con.close()
    if not base:
        out("Brazo en sombra sin datos todavia (lo rellena el revisor, o: brazo_sombra.py --actualizar).")
        return
    out(f"Ultimos {dias} dias · R medio neto\n  {'':<14}{'base n':>7}{'A0':>8}{'B':>8}{'C':>8}   {'agente n':>9}{'R real':>8}")
    for clase, n, a0, b, c in sorted(base):
        an, ar = agente.get(clase, (0, None))
        out(f"  {clase:<14}{n:>7}{a0:>+8.2f}{b:>+8.2f}{c:>+8.2f}   {an:>9}{(f'{ar:+.2f}' if ar is not None else '-'):>8}")
    tn = sum(r[1] for r in base)
    out(f"  {'TOTAL':<14}{tn:>7}{sum(r[1] * r[2] for r in base) / tn:>+8.2f}{sum(r[1] * r[3] for r in base) / tn:>+8.2f}"
        f"{sum(r[1] * r[4] for r in base) / tn:>+8.2f}   {sum(v[0] for v in agente.values()):>9}"
        f"{(sum(v[0] * v[1] for v in agente.values()) / max(sum(v[0] for v in agente.values()), 1)):>+8.2f}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Brazo base en sombra")
    ap.add_argument("--actualizar", action="store_true")
    ap.add_argument("--comparar", action="store_true")
    ap.add_argument("--dias", type=int, default=7)
    a = ap.parse_args()
    if a.actualizar:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            print(f"MT5 no disponible: {mt5.last_error()}", file=sys.stderr)
            sys.exit(1)
        try:
            print("operaciones guardadas:", actualizar(mt5, lote=10 ** 6))
        finally:
            mt5.shutdown()
    if a.comparar or not a.actualizar:
        comparar(a.dias)
