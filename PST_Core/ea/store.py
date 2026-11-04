"""
Tablas ea_* en la BBDD principal. Se crean solas (CREATE IF NOT EXISTS) y se siembran desde
MT5/despliegues.json: solo se dan de ALTA los que faltan, nunca se pisa el estado guardado.

  ea_despliegues  un registro por EA (= estrategia x simbolo x TF x magic): estado, interruptor, riesgo
  ea_eventos      historial de todo lo que se hace (quien, que, cuando). Lo ve la app y el catalogo
  ea_backtests    resultados de cada backtest
  ea_config       configuracion global (clave -> JSON), editable desde la app
"""
import json
import sqlite3
import time
from contextlib import contextmanager

from . import rutas

ESTADOS = ("idea", "experimental", "propuesta", "demo-reducido", "activa", "pausada", "retirada")

# Configuracion global por defecto. La app la muestra y la edita; ver MT5/DESARROLLO_EAS.md §5.
CONFIG_DEFECTO = {
    "interruptor_global": False,          # False = ningun EA abre operaciones, digan lo que digan
    "terminal_vivo_dir": "",              # vacio = autodetectar (carpeta de datos del terminal FTMO)
    "terminal_vivo_instalacion": "",      # vacio = autodetectar (carpeta de terminal64.exe)
    "tester_dir": str(rutas.Path.home() / "PST_MT5_Tester"),
    "tester_login": "",                   # vacio = la cuenta guardada en el terminal portable
    "tester_server": "",
    "tester_modelo": 1,                   # 0 todos los ticks, 1 OHLC M1, 2 precios de apertura, 4 ticks reales
    "tester_deposito": 10000,
    "tester_divisa": "EUR",
    "tester_apalancamiento": 100,
    "tester_timeout_s": 1800,
    "tester_sin_freno_total": True,       # backtest sin el freno de perdida TOTAL de FTMO (1000 EUR): si no, la
                                          # primera mala racha para el EA para siempre y no se ve la estrategia
    "mult_riesgo_max": 1.5,
    "agentes_pueden": ["pausar", "compilar", "backtest"],   # acciones que un agente hace sin aprobacion
    "criterio_pf_min": 1.2,
    "criterio_sharpe_min": 1.0,
    "criterio_ops_min": 30,
    "criterio_dd_max_pct": 10.0,
    "criterio_is_oos_positivos": True,
}

ACCIONES_AGENTE = ("pausar", "activar", "cerrar", "riesgo", "instalar", "retirar", "compilar", "backtest")

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS ea_despliegues (
    id TEXT PRIMARY KEY,
    referencia TEXT, estrategia TEXT, categoria TEXT,   -- referencia: codigo del proyecto original, no se muestra
    simbolo TEXT NOT NULL, tf TEXT NOT NULL, magic INTEGER NOT NULL UNIQUE,
    version TEXT,
    estado TEXT NOT NULL DEFAULT 'pausada',
    activo INTEGER NOT NULL DEFAULT 0,       -- interruptor de la app: puede abrir operaciones
    instalar INTEGER NOT NULL DEFAULT 0,     -- el gestor lo pone en un grafico del terminal vivo
    mult_riesgo REAL NOT NULL DEFAULT 1.0,
    cerrar_token INTEGER NOT NULL DEFAULT 0, -- distinto de 0 y nuevo = el EA cierra sus posiciones
    notas TEXT DEFAULT '',
    creado REAL, actualizado REAL
);
CREATE TABLE IF NOT EXISTS ea_eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL, despliegue_id TEXT, accion TEXT NOT NULL, detalle TEXT, origen TEXT
);
CREATE INDEX IF NOT EXISTS ix_ea_eventos_desp ON ea_eventos(despliegue_id, ts);
CREATE TABLE IF NOT EXISTS ea_backtests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL, despliegue_id TEXT NOT NULL, version TEXT, simbolo TEXT, tf TEXT,
    desde TEXT, hasta TEXT, modelo INTEGER, tramo TEXT,
    neto REAL, pf REAL, sharpe REAL, ops INTEGER, dd_equity_pct REAL, calidad_pct REAL,
    informe TEXT, metricas TEXT, origen TEXT
);
CREATE TABLE IF NOT EXISTS ea_config (clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
"""


def _ruta_db():
    from PST_Core.config import DB_PATH
    return DB_PATH


@contextmanager
def conexion():
    con = sqlite3.connect(_ruta_db(), timeout=15)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


_inicializado = False


def inicializar():
    """Crea las tablas y da de alta los despliegues nuevos de despliegues.json. Idempotente."""
    global _inicializado
    with conexion() as con:
        con.executescript(_ESQUEMA)
        datos = json.loads(rutas.DESPLIEGUES_JSON.read_text(encoding="utf-8"))["despliegues"]
        ahora = time.time()
        for d in datos:
            existe = con.execute("SELECT 1 FROM ea_despliegues WHERE id=?", (d["id"],)).fetchone()
            if existe:
                continue
            con.execute(
                "INSERT INTO ea_despliegues (id, referencia, estrategia, categoria, simbolo, tf, magic, version,"
                " estado, activo, instalar, mult_riesgo, creado, actualizado)"
                " VALUES (?,?,?,?,?,?,?,?,'pausada',0,0,1.0,?,?)",
                (d["id"], d.get("referencia_historica", ""), d["estrategia"], d["categoria"], d["simbolo"], d["tf"], d["magic"],
                 d["version"], ahora, ahora))
            con.execute("INSERT INTO ea_eventos (ts, despliegue_id, accion, detalle, origen) VALUES (?,?,?,?,?)",
                        (ahora, d["id"], "alta", f"desde despliegues.json (origen {d.get('origen', '')})", "sistema"))
    _inicializado = True


def _asegurar():
    if not _inicializado:
        inicializar()


# ------------------------------------------------------------------ despliegues
def listar():
    _asegurar()
    with conexion() as con:
        return [dict(r) for r in con.execute("SELECT * FROM ea_despliegues ORDER BY categoria, estrategia, simbolo")]


def obtener(despliegue_id):
    _asegurar()
    with conexion() as con:
        r = con.execute("SELECT * FROM ea_despliegues WHERE id=?", (despliegue_id,)).fetchone()
        return dict(r) if r else None


def actualizar(despliegue_id, **campos):
    permitidos = {"estado", "activo", "instalar", "mult_riesgo", "cerrar_token", "notas", "version"}
    campos = {k: v for k, v in campos.items() if k in permitidos}
    if not campos:
        return
    sets = ", ".join(f"{k}=?" for k in campos)
    with conexion() as con:
        con.execute(f"UPDATE ea_despliegues SET {sets}, actualizado=? WHERE id=?",
                    (*campos.values(), time.time(), despliegue_id))


# ------------------------------------------------------------------ eventos
def evento(despliegue_id, accion, detalle="", origen="app"):
    _asegurar()
    with conexion() as con:
        con.execute("INSERT INTO ea_eventos (ts, despliegue_id, accion, detalle, origen) VALUES (?,?,?,?,?)",
                    (time.time(), despliegue_id, accion, detalle, origen))


def eventos(despliegue_id=None, limite=100):
    _asegurar()
    with conexion() as con:
        if despliegue_id:
            filas = con.execute("SELECT * FROM ea_eventos WHERE despliegue_id=? ORDER BY ts DESC LIMIT ?",
                                (despliegue_id, limite))
        else:
            filas = con.execute("SELECT * FROM ea_eventos ORDER BY ts DESC LIMIT ?", (limite,))
        return [dict(r) for r in filas]


# ------------------------------------------------------------------ backtests
def guardar_backtest(fila):
    _asegurar()
    cols = ("ts", "despliegue_id", "version", "simbolo", "tf", "desde", "hasta", "modelo", "tramo",
            "neto", "pf", "sharpe", "ops", "dd_equity_pct", "calidad_pct", "informe", "metricas", "origen")
    valores = [fila.get(c) for c in cols]
    with conexion() as con:
        cur = con.execute(f"INSERT INTO ea_backtests ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                          valores)
        return cur.lastrowid


def backtests(despliegue_id=None, limite=50):
    _asegurar()
    with conexion() as con:
        if despliegue_id:
            filas = con.execute("SELECT * FROM ea_backtests WHERE despliegue_id=? ORDER BY ts DESC LIMIT ?",
                                (despliegue_id, limite))
        else:
            filas = con.execute("SELECT * FROM ea_backtests ORDER BY ts DESC LIMIT ?", (limite,))
        return [dict(r) for r in filas]


# ------------------------------------------------------------------ configuracion
def config():
    """Configuracion efectiva: defectos + lo guardado."""
    _asegurar()
    valores = dict(CONFIG_DEFECTO)
    with conexion() as con:
        for r in con.execute("SELECT clave, valor FROM ea_config"):
            try:
                valores[r["clave"]] = json.loads(r["valor"])
            except ValueError:
                pass
    return valores


def guardar_config(cambios):
    desconocidas = [k for k in cambios if k not in CONFIG_DEFECTO]
    if desconocidas:
        raise ValueError(f"claves desconocidas: {desconocidas}")
    _asegurar()
    with conexion() as con:
        for k, v in cambios.items():
            con.execute("INSERT INTO ea_config (clave, valor) VALUES (?, ?) "
                        "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (k, json.dumps(v)))
