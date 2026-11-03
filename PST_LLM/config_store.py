"""
Almacen unico de configuracion de la flota y de las estrategias (Fase 1 del plan Estrategias).

Tablas en la BBDD central (pst_trading.db):
  estrategias        una fila por estrategia: flota, scalping y las que vengan despues (EAs de MT5).
  config_parametros  valor JSON por (estrategia, simbolo, clave). Ambitos:
                       global      estrategia='global'  simbolo=''
                       estrategia  estrategia=<id>      simbolo=''
                       simbolo     estrategia=<id>      simbolo=<symbol>

Resolucion de un valor: simbolo > estrategia > global > defecto del esquema.

Esquema: ESQUEMA es la unica definicion de cada parametro editable (tipo, rango, defecto, etiqueta,
fichero JSON que se refleja). La app lo pinta tal cual y los rangos de scalping salen de aqui.

Espejo: cada escritura tambien vuelca el valor a su JSON de siempre. Hace falta porque
.agents/scheduler.py y los agentes ya en marcha leen esos ficheros hasta la Fase 5. Si el espejo
falla, la escritura en BBDD ya esta hecha y se avisa con error: el valor de la BBDD manda al leer.

Principio: leer config NUNCA puede parar a un agente. Las lecturas caen al defecto si algo falla.
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import rutas

DATA = rutas.AGENTS_DATA_DIR
GLOBAL = "global"
_RUTA_DB = None                 # se resuelve la primera vez; los tests pueden fijarla

TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
MODELOS = ("haiku", "sonnet", "opus")
ESFUERZOS = ("low", "medium", "high")

ESTRATEGIAS_INICIALES = [
    ("flota", "Agente de flota", "Un agente LLM por simbolo, con su modelo, cadencia y riesgo.", 1),
    ("scalping", "Scalping", "Agente aparte de la flota para indices. Simulacion, o real solo en cuenta DEMO.", 2),
]

# Cada entrada define un parametro editable. `simbolo=True` = se puede fijar por simbolo.
# `espejo` = fichero JSON (relativo a .agents/data) y clave donde se refleja. Con "{nombre}" se
# sustituye por el nombre del agente del simbolo; con "riesgo_por_simbolo" se escribe en ese mapa.
ESQUEMA = [
    # --- Global (lo que se edita en Ajustes) ---
    dict(clave="max_llamadas_dia", estrategia="flota", tipo="int", defecto=0, min=0, max=100000, ambitos=("estrategia",),
         grupo="Limites", etiqueta="Llamadas al dia (todos los agentes)",
         ayuda="0 = sin tope", espejo=("llm_global.json", "max_llamadas_dia")),
    dict(clave="max_activos", estrategia="flota", tipo="int", defecto=10, min=1, max=50, ambitos=("estrategia",),
         grupo="Rotacion", etiqueta="Agentes a la vez (maximo)", ayuda="Cuantos agentes de la flota pueden operar a la vez",
         espejo=("rotacion_config.json", "max_activos")),
    dict(clave="max_por_clase", estrategia="flota", tipo="int", defecto=5, min=1, max=20, ambitos=("estrategia",),
         grupo="Rotacion", etiqueta="Agentes por clase (maximo)", ayuda="Tope por clase de mercado (indices, acciones...)",
         espejo=("rotacion_config.json", "max_por_clase")),
    dict(clave="ciclo_min", estrategia="flota", tipo="int", defecto=5, min=1, max=60, ambitos=("estrategia",),
         grupo="Rotacion", etiqueta="Ciclo de rotacion (min)", ayuda="Cada cuanto revisa la rotacion quien opera",
         espejo=("rotacion_config.json", "ciclo_min")),
    dict(clave="cuota_tope_5h", estrategia=GLOBAL, tipo="int", defecto=60, min=10, max=100,
         grupo="Cuota de Claude", etiqueta="Tope de la ventana de 5 h (%)",
         ayuda="La flota no llama por encima de este % de la ventana de 5 h",
         espejo=("cuota_topes.json", "max_5h")),
    dict(clave="cuota_tope_semanal", estrategia=GLOBAL, tipo="int", defecto=70, min=10, max=100,
         grupo="Cuota de Claude", etiqueta="Tope semanal (%)",
         ayuda="La flota no llama por encima de este % de la cuota semanal",
         espejo=("cuota_topes.json", "max_semanal")),
    dict(clave="puntuador_cuota_max_5h", estrategia=GLOBAL, tipo="int", defecto=60, min=10, max=100,
         grupo="Puntuador (home)", etiqueta="Tope de la ventana de 5 h (%)",
         ayuda="Umbral propio del puntuador. Distinto del de la flota a proposito, hasta decidirlo",
         espejo=("puntuador_config.json", "cuota_max_5h")),
    dict(clave="puntuador_cuota_max_semanal", estrategia=GLOBAL, tipo="int", defecto=70, min=10, max=100,
         grupo="Puntuador (home)", etiqueta="Tope semanal (%)",
         ayuda="Umbral propio del puntuador",
         espejo=("puntuador_config.json", "cuota_max_semanal")),

    # --- Scalping (estrategia) ---
    dict(clave="enabled", estrategia="scalping", tipo="bool", defecto=True, grupo="Estado",
         etiqueta="Agente encendido", espejo=("llm_scalping.json", "enabled")),
    dict(clave="live", estrategia="scalping", tipo="bool", defecto=False, grupo="Estado",
         etiqueta="Ordenes reales (solo cuenta DEMO)", ayuda="Apagado = simulacion",
         espejo=("llm_scalping.json", "live")),
    dict(clave="model", estrategia="scalping", tipo="enum", opciones=MODELOS, defecto="haiku",
         grupo="Modelo", etiqueta="Modelo", espejo=("llm_scalping.json", "model")),
    dict(clave="effort", estrategia="scalping", tipo="enum", opciones=ESFUERZOS, defecto="low",
         grupo="Modelo", etiqueta="Esfuerzo", espejo=("llm_scalping.json", "effort")),
    dict(clave="cadencia_s", estrategia="scalping", tipo="int", defecto=60, min=0, max=600,
         grupo="Cadencia", etiqueta="Cadencia (s)", ayuda="0 = continuo: al acabar una vuelta empieza la siguiente",
         espejo=("llm_scalping.json", "cadencia_s")),
    dict(clave="pausa_minima_s", estrategia="scalping", tipo="int", defecto=1, min=1, max=60,
         grupo="Cadencia", etiqueta="Pausa minima (s)", ayuda="Pausa entre vueltas, aunque la cadencia sea 0",
         espejo=("llm_scalping.json", "pausa_minima_s")),
    dict(clave="riesgo_pct", estrategia="scalping", tipo="float", defecto=0.25, min=0.05, max=1.0, paso=0.05,
         grupo="Riesgo", etiqueta="Riesgo por operacion (% de equity)",
         ayuda="Por defecto para todos los simbolos de scalping. Cada simbolo puede tener el suyo",
         espejo=("llm_scalping.json", "riesgo_pct"), ambitos=("estrategia", "simbolo"),
         espejo_simbolo=("llm_scalping.json", "riesgo_por_simbolo")),
    dict(clave="max_hold_min", estrategia="scalping", tipo="int", defecto=20, min=1, max=240,
         grupo="Salida", etiqueta="Tiempo maximo (min)", ayuda="Pasado este tiempo la operacion se cierra sola",
         espejo=("llm_scalping.json", "max_hold_min")),
    dict(clave="cooldown_tras_stop_min", estrategia="scalping", tipo="int", defecto=10, min=0, max=120,
         grupo="Entrada", etiqueta="Enfriamiento tras un stop (min)",
         ayuda="Minutos sin entrar en ese simbolo tras un stop", espejo=("llm_scalping.json", "cooldown_tras_stop_min")),
    dict(clave="max_spread_sobre_stop", estrategia="scalping", tipo="float", defecto=0.10, min=0.01, max=0.5, paso=0.01,
         grupo="Entrada", etiqueta="Spread maximo sobre el stop",
         ayuda="Fraccion de la distancia al stop que puede ocupar el spread",
         espejo=("llm_scalping.json", "max_spread_sobre_stop")),
    dict(clave="max_coste_r", estrategia="scalping", tipo="float", defecto=0.10, min=0.01, max=0.5, paso=0.01,
         grupo="Entrada", etiqueta="Coste maximo (fraccion de R)", ayuda="Spread + comision ida y vuelta",
         espejo=("llm_scalping.json", "max_coste_r")),
    dict(clave="max_abiertas", estrategia="scalping", tipo="int", defecto=2, min=1, max=5,
         grupo="Limites", etiqueta="Operaciones abiertas", ayuda="A la vez, en todos los simbolos de scalping",
         espejo=("llm_scalping.json", "max_abiertas")),
    dict(clave="max_perdida_dia_r", estrategia="scalping", tipo="float", defecto=3.0, min=0.5, max=20, paso=0.5,
         grupo="Limites", etiqueta="Parada diaria (R)", ayuda="Si el dia suma esta perdida en R, no se abre nada mas",
         espejo=("llm_scalping.json", "max_perdida_dia_r")),

    # --- Flota a nivel de estrategia: valor para todos los agentes. Sin espejo: el agente lo lee de la BBDD.
    #     Al guardarlo, los simbolos con valor propio para esa clave pasan a usar este (la estrategia prevalece).
    dict(clave="model", estrategia="flota", tipo="enum", opciones=MODELOS, defecto="haiku", ambitos=("estrategia",),
         grupo="Modelo", etiqueta="Modelo de todos los agentes", ayuda="Al guardar, los simbolos con modelo propio usan este"),
    dict(clave="timeframe", estrategia="flota", tipo="enum", opciones=TIMEFRAMES, defecto="M5", ambitos=("estrategia",),
         grupo="Cadencia", etiqueta="Cada cuanto se llama (todos)", ayuda="Al guardar, los simbolos con reloj propio usan este"),
    dict(clave="sin_filtro", estrategia="flota", tipo="bool", defecto=True, ambitos=("estrategia",),
         grupo="Entrada", etiqueta="Sin filtro de setup (todos)", ayuda="Al guardar, los simbolos con filtro propio usan este"),
    dict(clave="riesgo_pct", estrategia="flota", tipo="float", defecto=0.5, min=0.05, max=1.0, paso=0.05, ambitos=("estrategia",),
         grupo="Riesgo", etiqueta="Riesgo por operacion (todos, % de equity)", ayuda="Al guardar, los simbolos con riesgo propio usan este"),
    dict(clave="live", estrategia="flota", tipo="bool", defecto=False, ambitos=("estrategia",),
         grupo="Estado", etiqueta="Ordenes reales (todos, solo cuenta DEMO)", ayuda="Apagado = simulacion"),

    # --- Flota (por simbolo: cada agente tiene su fichero llm_trader_<nombre>.json) ---
    dict(clave="model", estrategia="flota", tipo="enum", opciones=MODELOS, defecto="haiku", simbolo=True,
         grupo="Modelo", etiqueta="Modelo", espejo=("llm_trader_{nombre}.json", "model")),
    dict(clave="timeframe", estrategia="flota", tipo="enum", opciones=TIMEFRAMES, defecto="M5", simbolo=True,
         grupo="Cadencia", etiqueta="Cada cuanto se llama (reloj)", espejo=("llm_trader_{nombre}.json", "timeframe")),
    dict(clave="sin_filtro", estrategia="flota", tipo="bool", defecto=True, simbolo=True,
         grupo="Entrada", etiqueta="Sin filtro de setup (llama en cada vela)",
         espejo=("llm_trader_{nombre}.json", "sin_filtro")),
    dict(clave="riesgo_pct", estrategia="flota", tipo="float", defecto=0.5, min=0.05, max=1.0, paso=0.05, simbolo=True,
         grupo="Riesgo", etiqueta="Riesgo por operacion (% de equity)",
         espejo=("llm_trader_{nombre}.json", "riesgo_pct")),
    dict(clave="live", estrategia="flota", tipo="bool", defecto=False, simbolo=True,
         grupo="Estado", etiqueta="Ordenes reales (solo cuenta DEMO)", ayuda="Apagado = simulacion",
         espejo=("llm_trader_{nombre}.json", "live")),
]

# Indice (clave, ambito, estrategia) -> definicion. Ambito: global | estrategia | simbolo.
IDX = {}
for _d in ESQUEMA:
    if _d["estrategia"] == GLOBAL:
        _ambitos = (GLOBAL,)
    elif "ambitos" in _d:
        _ambitos = _d["ambitos"]
    else:
        _ambitos = ("simbolo",) if _d.get("simbolo") else ("estrategia",)
    for _amb in _ambitos:
        IDX[(_d["clave"], _amb, _d["estrategia"])] = _d


def ambito_de(estrategia, simbolo=""):
    if estrategia == GLOBAL:
        return GLOBAL
    return "simbolo" if simbolo else "estrategia"


def definicion(clave, estrategia, simbolo=""):
    """Definicion del esquema para esa clave en ese ambito, o None si no es editable ahi."""
    return IDX.get((clave, ambito_de(estrategia, simbolo), estrategia))


def rango(clave, estrategia):
    """(min, max) de una clave numerica del esquema a nivel estrategia. Lo usa scalping_config."""
    d = IDX.get((clave, "estrategia", estrategia)) or IDX.get((clave, "global", estrategia))
    if d is None or "min" not in d:
        raise KeyError(clave)
    return d["min"], d["max"]


# ---------------------------------------------------------------- BBDD

def _ruta_db():
    global _RUTA_DB
    if _RUTA_DB is None:
        try:
            from PST_Core.config import DB_PATH
        except ImportError:
            for p in (str(rutas.DATOS), str(rutas.RECURSOS)):
                if p not in sys.path:
                    sys.path.insert(0, p)
            from PST_Core.config import DB_PATH
        _RUTA_DB = Path(DB_PATH)
    return _RUTA_DB


def _conectar():
    return sqlite3.connect(_ruta_db(), timeout=15)


def asegurar_tablas(con):
    con.execute("""CREATE TABLE IF NOT EXISTS estrategias (
        id TEXT PRIMARY KEY, nombre TEXT NOT NULL, descripcion TEXT, orden INTEGER NOT NULL DEFAULT 99,
        activa INTEGER NOT NULL DEFAULT 1, creada TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS config_parametros (
        estrategia TEXT NOT NULL, simbolo TEXT NOT NULL DEFAULT '', clave TEXT NOT NULL, valor TEXT NOT NULL,
        origen TEXT, actualizado TEXT, PRIMARY KEY (estrategia, simbolo, clave))""")
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for id_, nombre, desc, orden in ESTRATEGIAS_INICIALES:
        con.execute("INSERT OR IGNORE INTO estrategias (id, nombre, descripcion, orden, activa, creada) "
                    "VALUES (?,?,?,?,1,?)", (id_, nombre, desc, orden, ahora))
    con.commit()


def estrategias_bd():
    """Filas de la tabla estrategias (las activas primero, en su orden)."""
    con = _conectar()
    try:
        asegurar_tablas(con)
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(
            "SELECT id, nombre, descripcion, orden, activa FROM estrategias ORDER BY activa DESC, orden, id")]
    finally:
        con.close()


def _cascada(estrategia, simbolo):
    if simbolo:
        return [(estrategia, simbolo), (estrategia, ""), (GLOBAL, "")]
    return [(estrategia, ""), (GLOBAL, "")]


def leer_valor(clave, estrategia, simbolo=""):
    """(valor, (estrategia, simbolo)) del primer nivel que lo tiene, o (None, None). Nunca lanza."""
    try:
        con = _conectar()
        try:
            for est, sym in _cascada(estrategia, simbolo):
                fila = con.execute("SELECT valor FROM config_parametros WHERE estrategia=? AND simbolo=? AND clave=?",
                                   (est, sym, clave)).fetchone()
                if fila is not None:
                    return json.loads(fila[0]), (est, sym)
        finally:
            con.close()
    except Exception as e:
        print(f"[config_store] no pude leer {clave} ({estrategia}/{simbolo}), uso el defecto: {e}", flush=True)
    return None, None


def valor_o(clave, por_defecto, estrategia, simbolo=""):
    """Valor efectivo para un lector: el de la BBDD si existe, si no `por_defecto` (el que ya usaba el codigo)."""
    v, _ = leer_valor(clave, estrategia, simbolo)
    return por_defecto if v is None else v


def overlay(estrategia, simbolo=""):
    """{clave: valor} de los parametros de la estrategia guardados en BBDD. Con simbolo, gana su valor propio.
    Un fallo de BBDD devuelve {} y el lector usa su JSON. Nunca lanza."""
    try:
        con = _conectar()
        try:
            filas = con.execute(
                "SELECT simbolo, clave, valor FROM config_parametros WHERE estrategia=? AND simbolo IN ('', ?) "
                "ORDER BY simbolo = '' DESC", (estrategia, simbolo or "")).fetchall()
        finally:
            con.close()
    except Exception as e:
        print(f"[config_store] overlay {estrategia}/{simbolo} sin BBDD, uso JSON: {e}", flush=True)
        return {}
    return {clave: json.loads(v) for _, clave, v in filas}


def overlay_simbolos(estrategia):
    """{simbolo: {clave: valor}} de los parametros fijados por simbolo en esa estrategia."""
    try:
        con = _conectar()
        try:
            filas = con.execute("SELECT simbolo, clave, valor FROM config_parametros "
                                "WHERE estrategia=? AND simbolo <> ''", (estrategia,)).fetchall()
        finally:
            con.close()
    except Exception as e:
        print(f"[config_store] overlay_simbolos {estrategia} sin BBDD: {e}", flush=True)
        return {}
    out = {}
    for sym, clave, v in filas:
        out.setdefault(sym, {})[clave] = json.loads(v)
    return out


def normalizar(d, valor):
    """Valida y convierte un valor segun su definicion. Lanza ValueError con el motivo."""
    clave = d["clave"]
    t = d["tipo"]
    if t == "bool":
        if not isinstance(valor, bool):
            raise ValueError(f"{clave} debe ser true o false")
        return valor
    if t == "enum":
        if valor not in d["opciones"]:
            raise ValueError(f"{clave} no valido: {valor} (usa {', '.join(d['opciones'])})")
        return valor
    try:
        v = float(valor)
    except (TypeError, ValueError):
        raise ValueError(f"{clave} debe ser un numero")
    if not d["min"] <= v <= d["max"]:
        raise ValueError(f"{clave} fuera de rango: usa entre {d['min']} y {d['max']}")
    return int(v) if t == "int" else round(v, 4)


def guardar(estrategia, simbolo, cambios, origen="app"):
    """
    Valida y guarda `cambios` ({clave: valor}) en el ambito indicado, en BBDD y en su espejo JSON.
    Lanza ValueError si algo no vale (no escribe nada). Devuelve los valores ya normalizados.
    """
    if not isinstance(cambios, dict) or not cambios:
        raise ValueError("no hay cambios que guardar")
    if estrategia != GLOBAL and estrategia not in {e["id"] for e in estrategias_bd()}:
        raise ValueError(f"estrategia desconocida: {estrategia}")
    defs = {}
    for clave, valor in cambios.items():
        d = definicion(clave, estrategia, simbolo)
        if d is None:
            raise ValueError(f"{clave} no es una clave editable en este ambito")
        defs[clave] = (d, normalizar(d, valor))

    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = _conectar()
    try:
        asegurar_tablas(con)
        with con:
            for clave, (d, v) in defs.items():
                con.execute("INSERT OR REPLACE INTO config_parametros (estrategia, simbolo, clave, valor, origen, actualizado) "
                            "VALUES (?,?,?,?,?,?)",
                            (estrategia if estrategia != GLOBAL else GLOBAL, simbolo or "", clave,
                             json.dumps(v), origen, ahora))
                if estrategia != GLOBAL and not simbolo:
                    # La estrategia prevalece: los simbolos pasan a heredar este valor
                    con.execute("DELETE FROM config_parametros WHERE estrategia=? AND simbolo<>'' AND clave=?",
                                (estrategia, clave))
    finally:
        con.close()

    errores = []
    for clave, (d, v) in defs.items():
        try:
            _reflejar(d, simbolo, v)
        except Exception as e:
            errores.append(f"{clave}: {e}")
    if errores:
        raise RuntimeError("guardado en la BBDD, pero no se pudo actualizar el JSON de espejo: " + "; ".join(errores))
    return {clave: v for clave, (_, v) in defs.items()}


# ---------------------------------------------------------------- espejo JSON

def _nombre_agente(simbolo):
    import registro   # import tardio: arrastra universo y el arbol de la flota
    return registro.nombre_agente(simbolo)


def _ruta_espejo(fichero, simbolo):
    if "{nombre}" in fichero:
        if not simbolo:
            raise ValueError("este parametro necesita un simbolo para saber su fichero")
        fichero = fichero.replace("{nombre}", _nombre_agente(simbolo))
    return DATA / fichero


def _leer_json(ruta):
    if not ruta.is_file():
        return {}
    return json.loads(ruta.read_text(encoding="utf-8-sig"))


def _escribir_json(ruta, datos):
    tmp = ruta.with_name(ruta.name + ".tmp")
    tmp.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ruta)


def _reflejar(d, simbolo, valor):
    if "espejo" not in d:
        return          # parametro solo de la BBDD (por ejemplo, flota a nivel de estrategia)
    fichero, clave_json = d["espejo"]
    if clave_json == "riesgo_por_simbolo" or (simbolo and d.get("espejo_simbolo")):
        # Por simbolo en un mapa del JSON de scalping: {simbolo: valor}
        fichero, clave_json = d["espejo_simbolo"]
        ruta = _ruta_espejo(fichero, simbolo)
        datos = _leer_json(ruta)
        mapa = dict(datos.get(clave_json) or {})
        mapa[simbolo] = valor
        datos[clave_json] = mapa
        _escribir_json(ruta, datos)
        return
    ruta = _ruta_espejo(fichero, simbolo)
    if "{nombre}" in fichero and not ruta.is_file():
        return   # el agente aun no tiene config (nunca activado): la BBDD ya manda al leer
    datos = _leer_json(ruta)
    datos[clave_json] = valor
    _escribir_json(ruta, datos)


# ---------------------------------------------------------------- migracion y divergencias

def _valores_espejo():
    """Lo que dicen hoy los JSON: [(estrategia, simbolo, clave, valor, d)] de todo lo que tiene espejo."""
    out = []
    for d in ESQUEMA:
        est, clave = d["estrategia"], d["clave"]
        if "espejo" not in d:
            continue
        if "{nombre}" in d["espejo"][0]:
            # Un fichero por agente: el simbolo sale del propio fichero
            for ruta in sorted(DATA.glob("llm_trader_Agente_*.json")):
                try:
                    cfg = _leer_json(ruta)
                except (OSError, ValueError):
                    continue
                if cfg.get("symbol") and clave in cfg:
                    out.append((est, cfg["symbol"], clave, cfg[clave], d))
            continue
        fichero, clave_json = d["espejo"]
        datos = _leer_json(DATA / fichero)
        if clave_json in datos:
            out.append((est, "", clave, datos[clave_json], d))
        if d.get("espejo_simbolo"):
            fichero_s, clave_s = d["espejo_simbolo"]
            for sym, v in (_leer_json(DATA / fichero_s).get(clave_s) or {}).items():
                out.append((est, sym, clave, v, d))
    return out


def divergencias():
    """Claves cuyo valor en BBDD no coincide con el JSON de espejo. Vacio = todo en orden."""
    filas = []
    con = _conectar()
    try:
        asegurar_tablas(con)
        bd = {(e, s, c): json.loads(v) for e, s, c, v in con.execute(
            "SELECT estrategia, simbolo, clave, valor FROM config_parametros")}
    finally:
        con.close()
    for est, sym, clave, json_val, d in _valores_espejo():
        clave_bd = (est, sym or "", clave)
        if clave_bd in bd and bd[clave_bd] != json_val:
            filas.append({"estrategia": est, "simbolo": sym or "", "clave": clave,
                          "bd": bd[clave_bd], "json": json_val})
    return filas


def migrar(aplicar=False, carpeta_copia=None, sobrescribir=False):
    """
    Importa a la BBDD lo que hay en los JSON, sin pisar lo que ya este en la BBDD.
    Sin `aplicar` solo informa. Con `aplicar` copia antes la BBDD (sqlite backup) y luego inserta.
    Con `sobrescribir` el JSON manda tambien donde la BBDD difiere: solo para la ultima sincronizacion antes de
    desplegar el codigo que lee la BBDD (el exe anterior seguia escribiendo los JSON).
    Devuelve un informe: nuevos, iguales, distintos (no se tocan) y claves sin esquema (pendientes).
    """
    con = _conectar()
    try:
        existentes = {}
        try:
            existentes = {(e, s, c): json.loads(v) for e, s, c, v in con.execute(
                "SELECT estrategia, simbolo, clave, valor FROM config_parametros")}
        except sqlite3.OperationalError:
            pass     # la tabla aun no existe: todo es nuevo
    finally:
        con.close()

    nuevos, iguales, distintos = [], [], []
    for est, sym, clave, valor, d in _valores_espejo():
        try:
            valor_norm = normalizar(d, valor)
        except ValueError as e:
            distintos.append({"estrategia": est, "simbolo": sym, "clave": clave, "motivo": f"fuera de esquema: {e}"})
            continue
        clave_bd = (est, sym or "", clave)
        fila = {"estrategia": est, "simbolo": sym or "", "clave": clave, "valor": valor_norm}
        if clave_bd not in existentes:
            nuevos.append(fila)
        elif existentes[clave_bd] == valor_norm:
            iguales.append(fila)
        else:
            distintos.append({**fila, "bd": existentes[clave_bd]})

    informe = {"nuevos": len(nuevos), "iguales": len(iguales), "distintos": distintos,
               "copia": None, "aplicado": False}
    if not aplicar:
        informe["detalle_nuevos"] = nuevos
        return informe

    ruta_db = _ruta_db()
    carpeta = Path(carpeta_copia or ruta_db.parent / "backups")
    carpeta.mkdir(parents=True, exist_ok=True)
    copia = carpeta / f"{ruta_db.stem}_antes_config_store_{datetime.now():%Y%m%d_%H%M%S}.db"
    origen_con = sqlite3.connect(ruta_db, timeout=60)
    try:
        destino = sqlite3.connect(copia)
        try:
            origen_con.backup(destino)
        finally:
            destino.close()
    finally:
        origen_con.close()
    informe["copia"] = str(copia)

    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = _conectar()
    try:
        asegurar_tablas(con)
        with con:
            for f in nuevos:
                con.execute("INSERT OR IGNORE INTO config_parametros (estrategia, simbolo, clave, valor, origen, actualizado) "
                            "VALUES (?,?,?,?,?,?)",
                            (f["estrategia"], f["simbolo"], f["clave"], json.dumps(f["valor"]), "migracion", ahora))
            if sobrescribir:
                for f in [d for d in distintos if "bd" in d]:
                    con.execute("UPDATE config_parametros SET valor=?, origen=?, actualizado=? "
                                "WHERE estrategia=? AND simbolo=? AND clave=?",
                                (json.dumps(f["valor"]), "sincronizacion-json", ahora, f["estrategia"], f["simbolo"], f["clave"]))
    finally:
        con.close()
    informe["aplicado"] = True
    return informe


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Migracion de la configuracion de los JSON a la BBDD")
    ap.add_argument("--aplicar", action="store_true", help="escribe en la BBDD (sin esto solo informa)")
    ap.add_argument("--divergencias", action="store_true", help="compara BBDD y JSON de espejo")
    ap.add_argument("--sobrescribir", action="store_true",
                    help="el JSON manda donde difiere (solo antes de desplegar el codigo que lee la BBDD)")
    args = ap.parse_args()
    if args.divergencias:
        print(json.dumps(divergencias(), indent=2, ensure_ascii=False))
    else:
        inf = migrar(aplicar=args.aplicar, sobrescribir=args.sobrescribir)
        print(json.dumps({k: v for k, v in inf.items() if k != "detalle_nuevos"}, indent=2, ensure_ascii=False))
        if not args.aplicar:
            for f in inf.get("detalle_nuevos", []):
                print(f"  + {f['estrategia']:9} {f['simbolo'] or '-':12} {f['clave']:26} {f['valor']}")
            print("(solo informe: nada escrito. Repite con --aplicar.)")
