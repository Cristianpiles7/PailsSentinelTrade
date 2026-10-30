"""
Niveles de la flota (plan v3, fase 6): que clases y simbolos operan de verdad y cuales solo se miden.

  operativo  opera en demo (el agente envia ordenes)
  prueba     simulacion: el agente sigue decidiendo y se simula lo que habria pasado, sin ordenes
  banquillo  sin agente

Se decide PRIMERO por clase (con 10-25 operaciones por simbolo manda el ruido) y despues por simbolo.
Un simbolo opera solo si su clase Y el estan en `operativo`. Las reglas (constantes de abajo, §3 fase 6 y §4 del plan):

  Clase operativo -> prueba      R medio < -0,15 tras >= 40 operaciones reales.
  Clase prueba -> operativo      >= 40 operaciones simuladas con R medio > 0 y coste <= 10 % del R.
  Simbolo operativo -> prueba    R medio < -0,15 tras >= 20 operaciones reales, o 3 semanas seguidas en negativo.
  Simbolo prueba -> operativo    >= 20 operaciones simuladas con R medio > 0 y coste <= 10 % del R (y clase operativa).
  Simbolo prueba -> banquillo    R medio < -0,15 tras >= 40 operaciones simuladas, o 3 semanas seguidas en negativo.
  Simbolo banquillo -> prueba    a los 28 dias (se vuelve a medir).
  Simbolo nuevo                  entra en prueba. Los que ya operaban el 2026-10-30 se sembraron como operativo.

La evidencia de cada nivel se cuenta desde que el nivel empezo (`desde`): lo medido con otra configuracion no vale.
Las operaciones en simulacion se simulan con velas M1 y la salida regla C (la misma que se aplica en vivo); su R va neto de
`coste` (spread + comision) y es un poco conservador: el spread de las ventas se descuenta entero.

  python PST_LLM/niveles.py --estado               niveles y evidencia por clase y simbolo
  python PST_LLM/niveles.py --evaluar [--aplicar]  reglas (necesita MT5); sin --aplicar solo muestra los cambios
  python PST_LLM/niveles.py --forzar SIMBOLO operativo|prueba|banquillo    decision manual
"""
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import registro                                               # noqa: E402
import universo                                               # noqa: E402

MIN_OPS_SIMBOLO = 20
MIN_OPS_CLASE = 40
MIN_OPS_BANQUILLO = 40
R_BAJAR = -0.15
COSTE_MAX = 0.10
SEMANAS_NEGATIVAS = 3
MIN_OPS_SEMANA = 3
BANQUILLO_DIAS = 28
COSTE_SIM_DEFECTO = 0.05          # operaciones simuladas anteriores a que el texto llevara el coste
HORIZONTE_S = 86400
NIVELES = ("operativo", "prueba", "banquillo")
FORMATO = "%Y-%m-%d %H:%M:%S"


def _ahora():
    return datetime.now().strftime(FORMATO)


def _con():
    con = registro.central()
    con.execute("""CREATE TABLE IF NOT EXISTS llm_niveles (
        clave TEXT PRIMARY KEY, nivel TEXT NOT NULL, desde TEXT NOT NULL, motivo TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS llm_niveles_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, clave TEXT, de TEXT, a TEXT, motivo TEXT)""")
    return con


# ── Lectura / escritura ──────────────────────────────────────────────────────
def _fila(clave):
    con = _con()
    try:
        return con.execute("SELECT nivel, desde, motivo FROM llm_niveles WHERE clave=?", (clave,)).fetchone()
    finally:
        con.close()


def _poner(clave, nivel, motivo, desde=None):
    assert nivel in NIVELES
    con = _con()
    try:
        previo = con.execute("SELECT nivel FROM llm_niveles WHERE clave=?", (clave,)).fetchone()
        con.execute("INSERT OR REPLACE INTO llm_niveles (clave, nivel, desde, motivo) VALUES (?,?,?,?)",
                    (clave, nivel, desde or _ahora(), motivo))
        con.execute("INSERT INTO llm_niveles_log (ts, clave, de, a, motivo) VALUES (?,?,?,?,?)",
                    (_ahora(), clave, previo[0] if previo else None, nivel, motivo))
        con.commit()
    finally:
        con.close()


def nivel_clase(clase):
    f = _fila(f"clase:{clase}")
    if f:
        return f[0]
    return "prueba" if clase in universo.CLASES_SOLO_SIMULACION else "operativo"


def nivel_simbolo(symbol):
    f = _fila(f"simbolo:{symbol}")
    return f[0] if f else "prueba"          # sin fila = simbolo nuevo (los que ya operaban se siembran)


def puede_operar(symbol, clase):
    """True si el simbolo puede enviar ordenes: su clase y el estan en operativo."""
    sembrar()          # idempotente: sin la siembra, los agentes que ya operaban pasarian a prueba al reiniciar
    return nivel_clase(clase) == "operativo" and nivel_simbolo(symbol) == "operativo"


def en_banquillo():
    con = _con()
    try:
        return {r[0].split(":", 1)[1] for r in con.execute(
            "SELECT clave FROM llm_niveles WHERE clave LIKE 'simbolo:%' AND nivel='banquillo'")}
    finally:
        con.close()


def alta(symbol, origen="manual"):
    """Un simbolo que se activa sin fila entra en prueba. Si estaba en banquillo y lo activas a mano, tambien pasa a prueba."""
    sembrar()
    f = _fila(f"simbolo:{symbol}")
    if f is None:
        _poner(f"simbolo:{symbol}", "prueba", f"simbolo nuevo ({origen})")
    elif f[0] == "banquillo" and origen == "manual":
        _poner(f"simbolo:{symbol}", "prueba", "activado a mano desde el banquillo")


def sembrar():
    """Una sola vez: los agentes que ya existen (operaban el 2026-10-30) quedan como operativo, salvo las clases que
    solo operan en simulacion. La evidencia de cada uno empieza ahora (configuracion nueva del plan v3)."""
    con = _con()
    try:
        if con.execute("SELECT 1 FROM llm_niveles WHERE clave='sembrado'").fetchone():
            return 0
        agentes = [tuple(r) for r in con.execute("SELECT symbol, clase FROM llm_agentes")]
        existentes = {r[0] for r in con.execute("SELECT clave FROM llm_niveles")}
    finally:
        con.close()
    n = 0
    for clase in sorted({c for _, c in agentes}):
        if f"clase:{clase}" not in existentes:
            _poner(f"clase:{clase}", "prueba" if clase in universo.CLASES_SOLO_SIMULACION else "operativo", "siembra inicial")
    for symbol, clase in agentes:
        if f"simbolo:{symbol}" not in existentes:
            solo_sim = clase in universo.CLASES_SOLO_SIMULACION
            _poner(f"simbolo:{symbol}", "prueba" if solo_sim else "operativo", "siembra inicial")
            n += 1
    _poner("sembrado", "operativo", "marca de siembra")
    return n


# ── Evidencia ────────────────────────────────────────────────────────────────
def _clases_de(symbols=None):
    con = _con()
    try:
        filas = [tuple(r) for r in con.execute("SELECT symbol, clase FROM llm_agentes")]
    finally:
        con.close()
    return {s: c for s, c in filas if symbols is None or s in symbols}


def ops_reales(symbol, desde):
    """[(abierta, R, coste_r)] de operaciones reales cerradas del simbolo desde `desde`."""
    con = _con()
    try:
        return [(a, r, c) for a, r, c in con.execute(
            "SELECT abierta, r_real, coste_r FROM llm_operaciones WHERE symbol=? AND r_real IS NOT NULL AND abierta>=? "
            "ORDER BY abierta", (symbol, desde))]
    finally:
        con.close()


_SIM = re.compile(r"SIM (BUY|SELL) .*?@ ([\d.]+) · SL ([\d.]+) · TP ([\d.]+) · riesgo ~(\d+) EUR(?: · marco \w+)?(?: · coste ([\d.]+)% R)?")


def ops_sim(mt5, symbol, desde):
    """Operaciones simuladas del simbolo: las entradas SIM de su BBDD, repetidas con velas M1 y la regla C.
    Una a la vez (las senales que caen con otra abierta se ignoran). Solo las ya resueltas o con 24 h cumplidas."""
    import simulacion_salidas as sim
    db_file = registro.DATA / f"llm_trader_{registro.nombre_agente(symbol)}.db"
    if not db_file.is_file():
        return []
    c = sqlite3.connect(db_file, timeout=15)
    try:
        filas = c.execute("SELECT ts, exec_result FROM decisions WHERE mode='sim' AND action IN ('buy','sell') "
                          "AND ts>=? AND exec_result LIKE 'SIM %' ORDER BY ts", (desde,)).fetchall()
    finally:
        c.close()
    desfase = sim.desfase_broker_s()
    ahora_b = int(time.time()) + desfase
    libre_hasta, out = 0, []
    for ts, texto in filas:
        m = _SIM.search(texto or "")
        if not m:
            continue                                            # rechazada por costes u otra regla
        lado, entrada, sl, tp, _, coste = m.groups()
        entrada, sl, tp = float(entrada), float(sl), float(tp)
        t = int(datetime.strptime(ts, FORMATO).timestamp()) + desfase
        dist = abs(entrada - sl)
        if t < libre_hasta or dist <= 0:
            continue
        barras = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, t + 60, min(t + HORIZONTE_S, ahora_b))
        if barras is None or not len(barras):
            continue
        r, salida, k = sim.simular_idx(sim.a_barras_r(lado.lower(), entrada, dist, barras), abs(tp - entrada) / dist, "C")
        if salida == "abierta" and ahora_b < t + HORIZONTE_S:
            continue                                            # aun sin resolver
        libre_hasta = int(barras[min(k, len(barras) - 1)]["time"]) + 60
        c_r = float(coste) / 100 if coste else COSTE_SIM_DEFECTO
        out.append((ts, r - c_r, c_r))
    return out


def resumir(ops):
    """{'n', 'r_medio', 'coste_medio', 'semanas_neg'} de [(abierta, R, coste_r)]."""
    n = len(ops)
    costes = [c for _, _, c in ops if c is not None]
    return {"n": n, "r_medio": sum(r for _, r, _ in ops) / n if n else None,
            "coste_medio": sum(costes) / len(costes) if costes else None, "semanas_neg": _semanas_negativas(ops)}


def _semanas_negativas(ops):
    """Semanas consecutivas (acabando en la ultima con datos) cuyo R total es negativo y que tienen >= MIN_OPS_SEMANA ops."""
    semanas = {}
    for a, r, _ in ops:
        y, w, _d = datetime.strptime(a, FORMATO).isocalendar()
        semanas.setdefault((y, w), []).append(r)
    cuenta, previa = 0, None
    for clave in sorted(semanas, reverse=True):
        v = semanas[clave]
        if len(v) < MIN_OPS_SEMANA or sum(v) >= 0 or (previa is not None and _contigua(clave, previa) is False):
            break
        cuenta, previa = cuenta + 1, clave
    return cuenta


def _contigua(menor, mayor):
    a = datetime.fromisocalendar(menor[0], menor[1], 1) + timedelta(days=7)
    return a == datetime.fromisocalendar(mayor[0], mayor[1], 1)


# ── Reglas (puras: se pueden probar sin MT5) ─────────────────────────────────
def decidir_clase(nivel, res):
    """(nuevo_nivel, motivo) o None."""
    if nivel == "operativo" and res["n"] >= MIN_OPS_CLASE and res["r_medio"] < R_BAJAR:
        return "prueba", f"R medio {res['r_medio']:+.2f} tras {res['n']} operaciones reales (< {R_BAJAR})"
    if nivel == "prueba" and res["n"] >= MIN_OPS_CLASE and res["r_medio"] > 0 and (res["coste_medio"] or 0) <= COSTE_MAX:
        return "operativo", (f"{res['n']} operaciones simuladas, R medio {res['r_medio']:+.2f}, "
                             f"coste {(res['coste_medio'] or 0) * 100:.1f}% del R")
    return None


def decidir_simbolo(nivel, res, clase_operativa=True, dias_en_nivel=0):
    if nivel == "operativo":
        if res["n"] >= MIN_OPS_SIMBOLO and res["r_medio"] < R_BAJAR:
            return "prueba", f"R medio {res['r_medio']:+.2f} tras {res['n']} operaciones reales (< {R_BAJAR})"
        if res["semanas_neg"] >= SEMANAS_NEGATIVAS:
            return "prueba", f"{res['semanas_neg']} semanas seguidas en negativo"
    elif nivel == "prueba":
        if (clase_operativa and res["n"] >= MIN_OPS_SIMBOLO and res["r_medio"] > 0
                and (res["coste_medio"] or 0) <= COSTE_MAX):
            return "operativo", (f"{res['n']} operaciones simuladas, R medio {res['r_medio']:+.2f}, "
                                 f"coste {(res['coste_medio'] or 0) * 100:.1f}% del R")
        if res["n"] >= MIN_OPS_BANQUILLO and res["r_medio"] < R_BAJAR:
            return "banquillo", f"R medio {res['r_medio']:+.2f} tras {res['n']} operaciones simuladas"
        if res["semanas_neg"] >= SEMANAS_NEGATIVAS:
            return "banquillo", f"{res['semanas_neg']} semanas seguidas en negativo en simulación"
    elif nivel == "banquillo" and dias_en_nivel >= BANQUILLO_DIAS:
        return "prueba", f"{BANQUILLO_DIAS} días en el banquillo: se vuelve a medir"
    return None


# ── Evaluacion y aplicacion ──────────────────────────────────────────────────
def _evidencia(mt5, symbol, nivel, desde):
    if nivel == "operativo":
        return ops_reales(symbol, desde)
    if nivel == "prueba":
        return ops_sim(mt5, symbol, desde)
    return []


def _cambiar_modo(symbol, live):
    """Pone al agente en demo (live) o simulacion si esta activo; el nivel ya esta guardado."""
    con = _con()
    try:
        f = con.execute("SELECT activo FROM llm_agentes WHERE symbol=?", (symbol,)).fetchone()
    finally:
        con.close()
    if f and f[0]:
        registro.activar(symbol, live=live, origen="niveles")


def evaluar(mt5, aplicar=False, avisar=print):
    """Aplica las reglas. Devuelve la lista de cambios [(clave, de, a, motivo)]. Con aplicar=False no toca nada."""
    sembrar()
    cambios, simbolos = [], _clases_de()
    por_clase = {}
    for s, c in simbolos.items():
        por_clase.setdefault(c, []).append(s)

    clase_nivel = {}
    for clase, lista in por_clase.items():
        f = _fila(f"clase:{clase}")
        nivel, desde = (f[0], f[1]) if f else (nivel_clase(clase), _ahora())
        ops = []
        for s in lista:
            ops += _evidencia(mt5, s, nivel, desde)
        r = decidir_clase(nivel, resumir(ops))
        clase_nivel[clase] = r[0] if r else nivel
        if r:
            cambios.append((f"clase:{clase}", nivel, r[0], r[1]))
            if aplicar:
                _poner(f"clase:{clase}", r[0], r[1])
                if r[0] == "prueba":                 # cada simbolo tendra que volver a ganarse el operativo en simulacion
                    for s in lista:
                        if nivel_simbolo(s) == "operativo":
                            _poner(f"simbolo:{s}", "prueba", f"su clase {clase} baja a prueba")
                            _cambiar_modo(s, False)
                avisar(f"🔻 Clase {clase}: {nivel} → {r[0]} · {r[1]}" if r[0] == "prueba" else f"🔺 Clase {clase}: {nivel} → {r[0]} · {r[1]}")

    for symbol, clase in sorted(simbolos.items()):
        f = _fila(f"simbolo:{symbol}")
        if f is None:
            continue
        nivel, desde, _ = f
        dias = (datetime.now() - datetime.strptime(desde, FORMATO)).days
        r = decidir_simbolo(nivel, resumir(_evidencia(mt5, symbol, nivel, desde)),
                            clase_operativa=clase_nivel.get(clase) == "operativo", dias_en_nivel=dias)
        if not r or (nivel == "operativo" and clase_nivel.get(clase) != "operativo"):
            continue
        cambios.append((f"simbolo:{symbol}", nivel, r[0], r[1]))
        if not aplicar:
            continue
        _poner(f"simbolo:{symbol}", r[0], r[1])
        if r[0] == "banquillo":
            try:
                registro.desactivar(symbol, origen="niveles")
            except Exception as e:
                avisar(f"[niveles] no se pudo apagar {symbol}: {e}")
        else:
            _cambiar_modo(symbol, r[0] == "operativo")
        icono = "🔺" if r[0] == "operativo" else "🔻"
        avisar(f"{icono} {symbol}: {nivel} → {r[0]} · {r[1]}")
    return cambios


# ── Linea de comandos ────────────────────────────────────────────────────────
def estado(mt5=None, out=print):
    sembrar()
    out(f"{'clave':<22}{'nivel':<11}{'desde':<20}{'ops':>5}{'R medio':>9}{'coste':>7}{'sem-':>5}  motivo")
    con = _con()
    try:
        filas = con.execute("SELECT clave, nivel, desde, motivo FROM llm_niveles WHERE clave!='sembrado' ORDER BY clave").fetchall()
    finally:
        con.close()
    for clave, nivel, desde, motivo in filas:
        res = None
        if clave.startswith("simbolo:") and mt5 is not None and nivel != "banquillo":
            res = resumir(_evidencia(mt5, clave.split(":", 1)[1], nivel, desde))
        elif clave.startswith("simbolo:") and nivel == "operativo":
            res = resumir(ops_reales(clave.split(":", 1)[1], desde))
        r = (f"{res['n']:>5}{(f'{res['r_medio']:+.2f}' if res['r_medio'] is not None else '-'):>9}"
             f"{(f'{res['coste_medio'] * 100:.0f}%' if res['coste_medio'] is not None else '-'):>7}{res['semanas_neg']:>5}") if res else " " * 26
        out(f"{clave:<22}{nivel:<11}{desde:<20}{r}  {motivo or ''}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Niveles de la flota: operativo / prueba / banquillo")
    ap.add_argument("--estado", action="store_true")
    ap.add_argument("--evaluar", action="store_true")
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--forzar", nargs=2, metavar=("SIMBOLO", "NIVEL"))
    a = ap.parse_args()
    if a.forzar:
        sym, niv = a.forzar
        if niv not in NIVELES:
            sys.exit(f"nivel no valido: {niv}")
        sembrar()
        _poner(f"simbolo:{sym}", niv, "decision manual")
        if niv == "banquillo":
            registro.desactivar(sym, origen="manual")
        else:
            _cambiar_modo(sym, niv == "operativo" and puede_operar(sym, _clases_de().get(sym, "")))
        print(f"{sym}: {niv}")
    elif a.evaluar:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            sys.exit(f"MT5 no disponible: {mt5.last_error()}")
        try:
            cambios = evaluar(mt5, aplicar=a.aplicar)
            for clave, de, a_, motivo in cambios:
                print(f"{clave}: {de} -> {a_} · {motivo}{'' if a.aplicar else '  (no aplicado)'}")
            print(f"{len(cambios)} cambios")
            estado(mt5)
        finally:
            mt5.shutdown()
    else:
        estado()
