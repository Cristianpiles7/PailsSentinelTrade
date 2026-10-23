"""
Registro compartido de la flota LLM: lo que ve la app y lo que escriben los agentes.

Tablas en la BBDD central (pst_trading.db, PST_Core.config.DB_PATH):
  llm_agentes    un agente por simbolo activado desde la app (clase, modelo, modo, estado).
  llm_decisions  cada ciclo de cada agente: prompt (version), entrada, respuesta, validacion y coste.

Principio: escribir aqui NUNCA puede parar a un agente. Todo registro va en try/except.

Activar/desactivar desde la app lanza o para el proceso del agente con la misma logica que
llm_trader_flota.py. Un agente nuevo arranca en SIMULACION salvo que se pida `live`.
"""
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import rutas                                           # rutas de ejecucion: repo o exe (PST_LLM/rutas.py)
REPO_ROOT = rutas.DATOS
sys.path.insert(0, str(Path(__file__).resolve().parent))     # para importar universo y llm_trader_flota

import universo                                               # noqa: E402

DATA = REPO_ROOT / ".agents" / "data"
SCRIPT = rutas.PST_LLM_DIR / "llm_trader.py"


def _db_central_path():
    """Ruta de pst_trading.db. Sin fallback: si no se puede resolver, debe fallar a la vista."""
    if str(REPO_ROOT) not in sys.path:          # los scripts de PST_LLM arrancan con PST_LLM como raiz
        sys.path.insert(0, str(REPO_ROOT))
    from PST_Core.config import DB_PATH
    return Path(DB_PATH)


def central():
    con = sqlite3.connect(_db_central_path(), timeout=15)
    con.execute("""CREATE TABLE IF NOT EXISTS llm_agentes (
        symbol TEXT PRIMARY KEY, nombre TEXT, clase TEXT, magic INTEGER, modelo TEXT,
        activo INTEGER, live INTEGER, actualizado TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS llm_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, agente TEXT, clase TEXT,
        modo TEXT, modelo TEXT, prompt_version TEXT, llamado INTEGER, motivo_puerta TEXT,
        entrada_json TEXT, respuesta_json TEXT, accion TEXT, confianza REAL, validacion TEXT,
        ejecutado INTEGER, resultado TEXT, tokens_in INTEGER, tokens_out INTEGER,
        tokens_cache INTEGER, coste_usd REAL)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_llm_decisions_sym ON llm_decisions(symbol, ts)")
    for col in ("origen TEXT DEFAULT 'manual'", "bloqueado INTEGER DEFAULT 0"):
        try:
            con.execute(f"ALTER TABLE llm_agentes ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass                                   # ya existe
    con.execute("""CREATE TABLE IF NOT EXISTS llm_prompts (
        version TEXT PRIMARY KEY, texto TEXT, creado TEXT)""")
    return con


def guardar_prompt(version, texto):
    """Una vez por version del prompt. Idempotente: se puede llamar en cada arranque."""
    try:
        con = central()
        con.execute("INSERT OR IGNORE INTO llm_prompts (version, texto, creado) VALUES (?,?,?)",
                    (version, texto, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        con.commit()
        con.close()
    except Exception as e:
        print(f"[registro] no se pudo guardar el prompt {version}: {e}", flush=True)


def leer_prompt(version):
    con = central()
    con.row_factory = sqlite3.Row
    fila = con.execute("SELECT version, texto, creado FROM llm_prompts WHERE version=?", (version,)).fetchone()
    con.close()
    return dict(fila) if fila else None


def prompt_version(texto):
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()[:10]


def registrar_decision(**campos):
    """Nunca lanza: un fallo de la BBDD central no debe parar al agente."""
    try:
        con = central()
        cols = ", ".join(campos)
        con.execute(f"INSERT INTO llm_decisions ({cols}) VALUES ({', '.join('?' for _ in campos)})",
                    tuple(campos.values()))
        con.commit()
        con.close()
    except Exception as e:
        print(f"[registro] no se pudo guardar la decision: {e}", flush=True)


def nombre_agente(symbol):
    base = re.sub(r"\.cash$", "", symbol)
    return "Agente_" + base.replace(".", "_")


CLASE_POR_TIPO = {"INDEX": "indice", "STOCK": "accion_eeuu", "FOREX": "forex", "COMMODITY": "materia_prima",
                  "CRYPTO": "cripto"}


def _simbolo(symbol):
    """Universo LLM primero; si no, cualquier simbolo cargado en la app (symbols_config)."""
    for s in universo.UNIVERSO:
        if s.symbol == symbol:
            return s
    return _simbolo_cargado(symbol)


def _simbolo_cargado(symbol):
    """Simbolo fuera del universo: clase segun su tipo en la app, magic propio (el siguiente libre)."""
    con = central()
    try:
        tipo = con.execute("SELECT type FROM symbols_config WHERE symbol=?", (symbol,)).fetchone()
        if tipo is None:
            return None
        fila = con.execute("SELECT magic FROM llm_agentes WHERE symbol=?", (symbol,)).fetchone()
        usados = [r[0] for r in con.execute("SELECT magic FROM llm_agentes")]
        con_fila = {r[0] for r in con.execute("SELECT symbol FROM llm_agentes")}
        sin_fila = [r[0] for r in con.execute("SELECT symbol FROM symbols_config ORDER BY symbol")
                    if r[0] not in con_fila and r[0] not in {s.symbol for s in universo.UNIVERSO}]
    finally:
        con.close()
    if fila and fila[0]:
        magic = fila[0]
    else:
        # Base libre por encima de todo lo usado; cada simbolo sin fila toma el siguiente hueco.
        base = max([s.magic for s in universo.UNIVERSO] + usados + [109035]) + 1
        magic = base + sin_fila.index(symbol)
    clase = CLASE_POR_TIPO.get((tipo[0] or "").upper(), "otro")
    return universo.Simbolo(symbol, clase, magic, "haiku")


def simbolos_disponibles():
    """Universo LLM mas todo lo cargado en la app que no esta en el universo."""
    con = central()
    try:
        cargados = [r[0] for r in con.execute("SELECT symbol FROM symbols_config")]
    finally:
        con.close()
    vistos = {s.symbol for s in universo.UNIVERSO}
    extra = [_simbolo_cargado(sym) for sym in cargados if sym not in vistos]
    return list(universo.UNIVERSO) + [s for s in extra if s is not None]


def _ficheros(nombre):
    import llm_trader_flota as F     # import tardio: arrastra psutil
    return F


def activar(symbol, live=True, origen="manual"):
    """Crea (si hace falta) la configuracion del agente y lo arranca. Devuelve un resumen."""
    import bloqueo
    ajeno = bloqueo.tomar_o_latir()
    if ajeno:
        raise RuntimeError(f"opera otro equipo ({ajeno}): no se activa {symbol} aqui")
    s = _simbolo(symbol)
    if s is None:
        raise ValueError(f"{symbol} no esta en el universo (PST_LLM/universo.py)")
    F = _ficheros(None)
    nombre = nombre_agente(symbol)
    registro = F.cargar_registro()
    if not any(a["nombre"] == nombre for a in registro):
        registro.append({"nombre": nombre, "symbol": symbol, "timeframe": "M1", "magic": s.magic,
                         "sin_filtro": False, "clase": s.clase, "modelo": s.modelo})
        F.REGISTRO.write_text(json.dumps(registro, indent=2), encoding="utf-8")
    f = F.ficheros(nombre)
    cfg = F.config_por_defecto({"symbol": symbol, "timeframe": "M1", "sin_filtro": False})
    cfg.update({"model": s.modelo, "live": bool(live), "timeframe": "M1"})
    if f["config"].is_file():
        # Si ya existia (agente reactivado), se conserva lo que el usuario haya tocado a mano
        # salvo el modo, que lo pide la app explicitamente.
        previa = json.loads(f["config"].read_text(encoding="utf-8-sig"))
        previa["live"] = bool(live)
        previa.setdefault("model", s.modelo)     # el modelo elegido en la app/config manda sobre universo.py
        cfg = previa
    f["config"].write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    vivos = F.procesos_corriendo()
    if nombre in vivos:
        pid = vivos[nombre]
    else:
        f["stop"].unlink(missing_ok=True)
        args = rutas.comando_python(SCRIPT, "--nombre", nombre, "--magic", str(s.magic))
        salida, error = open(f["log"], "a", encoding="utf-8"), open(f["err"], "a", encoding="utf-8")
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        p = subprocess.Popen(args, cwd=str(REPO_ROOT), stdout=salida, stderr=error,
                             stdin=subprocess.DEVNULL, creationflags=flags)
        pid = p.pid
    _guardar_estado(symbol, nombre, s, activo=1, live=live, origen=origen, bloqueado=0)
    return {"symbol": symbol, "nombre": nombre, "pid": pid, "live": bool(live), "clase": s.clase,
            "modelo": s.modelo, "magic": s.magic}


def desactivar(symbol, esperar_s=90, origen="manual"):
    s = _simbolo(symbol)
    if s is None:
        raise ValueError(f"{symbol} no esta en el universo")
    F = _ficheros(None)
    nombre = nombre_agente(symbol)
    f = F.ficheros(nombre)
    f["stop"].write_text("stop", encoding="utf-8")
    t = time.time() + esperar_s
    while time.time() < t and nombre in F.procesos_corriendo():
        time.sleep(2)
    matado = False
    if nombre in F.procesos_corriendo():
        import psutil
        psutil.Process(F.procesos_corriendo()[nombre]).kill()
        matado = True
    f["stop"].unlink(missing_ok=True)
    _guardar_estado(symbol, nombre, s, activo=0, live=None, origen=origen,
                    bloqueado=1 if origen == "manual" else None)
    return {"symbol": symbol, "nombre": nombre, "parado": True, "matado": matado}


def restaurar_intencion():
    """Tras recrear la BBDD, llm_agentes esta vacia pero la intencion sigue en los ficheros
    llm_trader_<Nombre>.json (enabled). Los agentes del universo con enabled vuelven a quedar
    activos, con su modo (live) y su check en la home. Nunca pisa un registro que ya existe.
    """
    con = central()
    registrados = {r[0] for r in con.execute("SELECT symbol FROM llm_agentes")}
    con.close()
    F = _ficheros(None)
    for s in universo.UNIVERSO:
        if s.symbol in registrados:
            continue
        nombre = nombre_agente(s.symbol)
        f = F.ficheros(nombre)
        if not f["config"].is_file():
            continue
        try:
            cfg = json.loads(f["config"].read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if cfg.get("enabled"):
            _guardar_estado(s.symbol, nombre, s, activo=1, live=bool(cfg.get("live")),
                            origen="restaurado", bloqueado=0)


def sincronizar_home():
    """La home solo muestra como activo (check encendido) lo que opera con un agente LLM activo.
    Todo lo demas queda con is_active=0 en symbols_config."""
    con = central()
    try:
        con.execute("""UPDATE symbols_config SET is_active = CASE WHEN symbol IN
                       (SELECT symbol FROM llm_agentes WHERE activo=1) THEN 1 ELSE 0 END""")
        con.commit()
    finally:
        con.close()


def arrancar_activos():
    """Al abrir la app: relanza los agentes que estaban activos al cerrarla (modo sim/demo incluido).

    Usa `activo` de la BBDD como intencion del usuario. `detener_todos` NO lo toca, asi que un
    agente parado por cerrar la app sigue marcado y vuelve a arrancar aqui. Si la BBDD se recreo,
    primero restaura esa intencion desde los ficheros de config. Al final alinea los checks de la
    home con los agentes. Devuelve los simbolos.
    """
    import bloqueo
    ajeno = bloqueo.tomar_o_latir()
    if ajeno:
        print(f"[registro] no se relanzan agentes: opera otro equipo ({ajeno})", flush=True)
        return []
    restaurar_intencion()
    sincronizar_home()
    F = _ficheros(None)
    # Un STOP global que sobrevive a un cierre cortado (parar() no llego a borrarlo) hacia salir a todos
    # los agentes nada mas arrancar, y activar() solo borra el STOP propio de cada agente. Al abrir la app
    # la intencion es la de la BBDD (activo), no ese fichero.
    (F.DATA / "llm_trader.STOP").unlink(missing_ok=True)
    con = central()
    filas = con.execute("SELECT symbol, live, origen FROM llm_agentes WHERE activo=1 AND bloqueado=0").fetchall()
    con.close()
    arrancados = []
    for symbol, live, origen in filas:
        if _simbolo(symbol) is None:       # fuera del universo: se ignora
            continue
        activar(symbol, live=bool(live), origen=origen or "manual")
        arrancados.append(symbol)
    if arrancados:
        F.iniciar_revisor()                 # el revisor vigila las posiciones de estos agentes
        F.iniciar_bot()
    return arrancados


def detener_todos():
    """Al cerrar la app: para todos los procesos SIN cambiar `activo` (ver arrancar_activos)."""
    _ficheros(None).parar()


def _guardar_estado(symbol, nombre, s, activo, live, origen="manual", bloqueado=None):
    """bloqueado=None no cambia el valor guardado. Para un apagado manual, bloqueado=1."""
    try:
        con = central()
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if live is None:
            sets, args = ["activo=?", "actualizado=?"], [activo, ahora]
            if bloqueado is not None:
                sets.append("bloqueado=?"); args.append(bloqueado)
            con.execute(f"UPDATE llm_agentes SET {', '.join(sets)} WHERE symbol=?", (*args, symbol))
        else:
            con.execute("""INSERT INTO llm_agentes (symbol, nombre, clase, magic, modelo, activo, live, actualizado, origen, bloqueado)
                           VALUES (?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(symbol) DO UPDATE SET nombre=excluded.nombre, clase=excluded.clase,
                           magic=excluded.magic, modelo=excluded.modelo, activo=excluded.activo,
                           live=excluded.live, actualizado=excluded.actualizado, origen=excluded.origen,
                           bloqueado=excluded.bloqueado""",
                        (symbol, nombre, s.clase, s.magic, s.modelo, activo, int(bool(live)), ahora,
                         origen, bloqueado or 0))
        _sincronizar_home(con, symbol, activo)
        con.commit()
        con.close()
    except Exception as e:
        print(f"[registro] no se pudo guardar el estado de {symbol}: {e}", flush=True)


def _sincronizar_home(con, symbol, activo):
    """El check de la home (symbols_config.is_active) sigue al agente: agente activo = check encendido."""
    con.execute("UPDATE symbols_config SET is_active=? WHERE symbol=?", (int(bool(activo)), symbol))


def estado():
    """Universo con su estado real: proceso vivo, modo, ultima decision."""
    F = _ficheros(None)
    vivos = F.procesos_corriendo()
    try:
        con = central()
        filas = {r[0]: r for r in con.execute("SELECT symbol, activo, live, actualizado, origen, bloqueado FROM llm_agentes")}
        coste = dict(con.execute("SELECT symbol, ROUND(SUM(COALESCE(coste_usd,0)),4) FROM llm_decisions GROUP BY symbol"))
        con.close()
    except Exception:
        filas = {}
        coste = {}
    out = []
    for s in simbolos_disponibles():
        nombre = nombre_agente(s.symbol)
        fila = filas.get(s.symbol)
        vivo = nombre in vivos
        # Un agente que corre aunque no tenga fila (arrancado antes de existir este registro) cuenta
        # como activo; el modo sale de su fichero de configuracion.
        activo = bool(fila and fila[1]) or vivo
        live = bool(fila[2]) if fila else _live_de_config(nombre)
        out.append({
            "symbol": s.symbol, "clase": s.clase, "modelo": _modelo_de_config(nombre, s.modelo),
            "timeframe": _config_de(nombre, "timeframe", "M1"), "magic": s.magic, "nombre": nombre,
            "activo": activo, "live": live,
            "vivo": vivo, "pid": vivos.get(nombre), "actualizado": fila[3] if fila else None,
            "origen": (fila[4] if fila and fila[4] else "manual"), "bloqueado": bool(fila and fila[5]),
            "coste_usd": coste.get(s.symbol, 0.0),
        })
    return out


def coste_total():
    """Coste acumulado de toda la flota a precio de API (todas las decisiones guardadas)."""
    con = central()
    total = con.execute("SELECT ROUND(SUM(COALESCE(coste_usd,0)),4) FROM llm_decisions").fetchone()[0]
    con.close()
    return total or 0.0


def _live_de_config(nombre):
    try:
        f = _ficheros(None).ficheros(nombre)
        return bool(json.loads(f["config"].read_text(encoding="utf-8-sig")).get("live"))
    except Exception:
        return False


MODELOS_PERMITIDOS = ("haiku", "sonnet", "opus")
# Cada cuanto se llama al agente (su reloj). Mismos marcos que admite llm_trader (TF_SEGUNDOS).
TIMEFRAMES_PERMITIDOS = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")


def _config_de(nombre, campo, por_defecto):
    try:
        f = _ficheros(None).ficheros(nombre)
        return json.loads(f["config"].read_text(encoding="utf-8-sig")).get(campo) or por_defecto
    except Exception:
        return por_defecto


def cambiar_timeframe(symbol, timeframe):
    """Cambia cada cuanto se llama al agente. Se relee en su siguiente vuelta: no hace falta reiniciarlo."""
    if _simbolo(symbol) is None:
        raise ValueError(f"{symbol} no esta cargado en la app")
    if timeframe not in TIMEFRAMES_PERMITIDOS:
        raise ValueError(f"timeframe no valido: {timeframe} (usa {', '.join(TIMEFRAMES_PERMITIDOS)})")
    f = _ficheros(None).ficheros(nombre_agente(symbol))
    if not f["config"].is_file():
        raise ValueError(f"{symbol} no tiene configuracion: activalo primero")
    cfg = json.loads(f["config"].read_text(encoding="utf-8-sig"))
    cfg["timeframe"] = timeframe
    f["config"].write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return {"symbol": symbol, "timeframe": timeframe}


def _modelo_de_config(nombre, por_defecto):
    try:
        f = _ficheros(None).ficheros(nombre)
        return json.loads(f["config"].read_text(encoding="utf-8-sig")).get("model") or por_defecto
    except Exception:
        return por_defecto


def cambiar_modelo(symbol, modelo):
    """Cambia el modelo de un agente. El proceso relee su config en cada vuelta: no hace falta reiniciarlo."""
    if _simbolo(symbol) is None:
        raise ValueError(f"{symbol} no esta en el universo")
    if modelo not in MODELOS_PERMITIDOS:
        raise ValueError(f"modelo no valido: {modelo} (usa {', '.join(MODELOS_PERMITIDOS)})")
    f = _ficheros(None).ficheros(nombre_agente(symbol))
    if not f["config"].is_file():
        raise ValueError(f"{symbol} no tiene configuracion: activalo primero")
    cfg = json.loads(f["config"].read_text(encoding="utf-8-sig"))
    cfg["model"] = modelo
    f["config"].write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return {"symbol": symbol, "modelo": modelo}


def decisiones(symbol, limite=50):
    con = central()
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM llm_decisions WHERE symbol=? ORDER BY id DESC LIMIT ?",
                       (symbol, int(limite))).fetchall()
    con.close()
    return [dict(r) for r in rows]


def costes(dias=7):
    """Coste a precio de API (total_cost_usd del CLI) por dia, agente y modelo, de los ultimos `dias`.

    No es lo que se paga con la suscripcion: es lo que costaria esa misma actividad por API.
    """
    desde = (datetime.now() - timedelta(days=dias - 1)).strftime("%Y-%m-%d")
    con = central()
    filas = con.execute("""SELECT substr(ts,1,10) AS dia, agente, modelo, SUM(llamado), COUNT(*),
                                  ROUND(SUM(COALESCE(coste_usd,0)),4)
                           FROM llm_decisions WHERE substr(ts,1,10) >= ?
                           GROUP BY dia, agente, modelo ORDER BY dia DESC, 6 DESC""", (desde,)).fetchall()
    con.close()
    return filas


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Coste de la flota LLM a precio de API")
    ap.add_argument("--costes", action="store_true", help="resumen por dia, agente y modelo")
    ap.add_argument("--dias", type=int, default=7)
    args = ap.parse_args()
    if args.costes:
        filas = costes(args.dias)
        print(f"{'dia':<11} {'agente':<16} {'modelo':<8} {'llamadas':>8} {'decisiones':>10} {'USD API':>9}")
        for dia, agente, modelo, llam, dec, usd in filas:
            print(f"{dia:<11} {agente:<16} {modelo or '-':<8} {llam or 0:>8} {dec:>10} {usd:>9.4f}")
        total = sum(f[5] for f in filas)
        print(f"\nTotal {args.dias} dias: {total:.4f} USD · media diaria {total / args.dias:.4f} · "
              f"mes (30 dias) ~{total / args.dias * 30:.2f} USD por API")
