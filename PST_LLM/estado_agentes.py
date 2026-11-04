"""
Estado del agente de cada simbolo para la home: que esta haciendo ahora (en posicion, buscando entrada,
frenado por un limite) y, en el scalping, el regimen de mercado que lee y lo que espera para entrar.

Fuentes, solo lectura:
  - Scalping: .agents/data/llm_scalping_estado.json (lo escribe scalping.py en cada decision o bloqueo)
    y las operaciones abiertas de llm_scalping.db (las simuladas no estan en MT5).
  - Flota: la ultima fila de llm_decisions de la BBDD central.

Se cachea unos segundos: la home lo pide cada 3 s para todos los simbolos. Nunca lanza.
"""
import json
import sqlite3
import time
from datetime import datetime

import registro
import rutas

ESTADO_SCALPING = rutas.AGENTS_DATA_DIR / "llm_scalping_estado.json"
ESTADO_FLOTA = rutas.AGENTS_DATA_DIR / "llm_flota_estado"      # un JSON por simbolo (lo escribe llm_trader.py)
CACHE_S = 5
VIEJO_S = 15 * 60          # una decision de hace mas de esto ya no describe el ahora
_cache = {"t": 0.0, "datos": {}}


def _edad_s(ts):
    try:
        return (datetime.now() - datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")).total_seconds()
    except (TypeError, ValueError):
        return None


def _primera_frase(texto, n=140):
    texto = (texto or "").strip().split("\n")[0]
    return texto if len(texto) <= n else texto[: n - 1].rstrip() + "…"


def _con_scalping():
    return sqlite3.connect(f"file:{registro.SCALPING_DB}?mode=ro", uri=True, timeout=5)


def _filas(con, sql, args=()):
    try:
        cur = con.execute(sql, args)
    except sqlite3.OperationalError:          # tabla o columna aun no creada por el agente
        return []
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, f)) for f in cur.fetchall()]


def _orden(o):
    if not o:
        return None
    return {"lado": o["lado"], "tipo": o["tipo"], "precio": o["precio"], "invalidacion": o["invalidacion"],
            "setup": o["setup"], "confianza": o["confianza"], "ts": o["ts"],
            "min_restantes": max(0, int((o["expira"] - time.time()) // 60)) if o.get("expira") else None}


def _abiertas_y_ordenes():
    """({simbolo: operacion abierta}, {simbolo: orden pendiente}) del scalping."""
    if not registro.SCALPING_DB.is_file():
        return {}, {}
    con = _con_scalping()
    try:
        abiertas = {f["symbol"]: f for f in _filas(con, "SELECT * FROM operaciones WHERE estado='abierta'")}
        ordenes = {f["symbol"]: _orden(f) for f in _filas(con, "SELECT * FROM ordenes WHERE estado='pendiente' ORDER BY id")}
    finally:
        con.close()
    return abiertas, ordenes


def _estado_scalping(symbol, crudo, abierta, orden=None):
    e = {"agente": "scalping", "regimen": crudo.get("regimen"), "ts": crudo.get("ts"),
         "setup": crudo.get("setup"), "texto": _primera_frase(crudo.get("razon")),
         "esperando": _primera_frase(crudo.get("esperando"), 60) or None, "orden": orden}
    if abierta:
        e.update(estado="posicion", lado=abierta["lado"], setup=abierta["setup"] or e["setup"], modo=abierta["modo"],
                 regimen=abierta.get("regimen") or e["regimen"])
    elif orden:
        e.update(estado="orden", lado=orden["lado"], setup=orden["setup"] or e["setup"])
    elif crudo.get("bloqueado"):
        e.update(estado="bloqueado", texto=_primera_frase(crudo.get("motivo")))
    elif crudo.get("accion") in ("buy", "sell") and crudo.get("motivo"):
        e.update(estado="frenada", lado=crudo["accion"], texto=_primera_frase(crudo.get("motivo")))
    elif not crudo:
        e.update(estado="sin_dato", texto="El agente aún no ha decidido sobre este símbolo")
    elif crudo.get("esperando"):
        e.update(estado="esperando", texto=_primera_frase(crudo["esperando"]))
    else:
        e.update(estado="buscando")
    return e


def _estado_flota(fila):
    ts, accion, llamado, motivo, respuesta = fila
    try:
        razon = (json.loads(respuesta or "{}") or {}).get("reason")
    except ValueError:
        razon = None
    e = {"agente": "flota", "regimen": None, "ts": ts}
    if not llamado:
        e.update(estado="bloqueado", texto=_primera_frase(motivo))
    elif accion in ("buy", "sell"):
        e.update(estado="senal", lado=accion, texto=_primera_frase(razon))
    else:
        e.update(estado="buscando", texto=_primera_frase(razon))
    return e


def _leer_flota(symbol):
    f = ESTADO_FLOTA / f"{symbol}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
    except (ValueError, OSError):
        return None


def _estado_flota_json(crudo):
    """Estado de un agente de la flota a partir de su JSON publicado (mismas claves que el scalping)."""
    e = {"agente": "flota", "regimen": crudo.get("regimen"), "ts": crudo.get("ts"), "setup": crudo.get("setup"),
         "texto": _primera_frase(crudo.get("razon")), "esperando": _primera_frase(crudo.get("esperando"), 60) or None,
         "orden": crudo.get("orden")}
    pos, orden = crudo.get("posicion"), crudo.get("orden")
    if pos:
        e.update(estado="posicion", lado=pos.get("lado"))
    elif orden:
        e.update(estado="orden", lado=orden.get("lado"), setup=orden.get("setup") or e["setup"])
    elif crudo.get("bloqueado"):
        e.update(estado="bloqueado", texto=_primera_frase(crudo.get("motivo")))
    elif crudo.get("accion") in ("buy", "sell") and crudo.get("motivo"):
        e.update(estado="frenada", lado=crudo["accion"], texto=_primera_frase(crudo.get("motivo")))
    elif crudo.get("esperando"):
        e.update(estado="esperando", texto=_primera_frase(crudo["esperando"]))
    else:
        e.update(estado="buscando")
    return e


def _calcular(simbolos):
    out = {}
    con_scalping = set(registro.simbolos_con("scalping"))
    try:
        crudos = json.loads(ESTADO_SCALPING.read_text(encoding="utf-8")) if ESTADO_SCALPING.is_file() else {}
    except (ValueError, OSError):
        crudos = {}
    abiertas, ordenes = _abiertas_y_ordenes()
    resto = []
    for s in simbolos:
        if s in con_scalping:
            out[s] = _estado_scalping(s, crudos.get(s) or {}, abiertas.get(s), ordenes.get(s))
        else:
            resto.append(s)
    sin_json = []
    for s in resto:
        crudo = _leer_flota(s)
        if crudo:
            out[s] = _estado_flota_json(crudo)
        else:
            sin_json.append(s)
    resto = sin_json
    if resto:
        con = sqlite3.connect(f"file:{registro._db_central_path()}?mode=ro", uri=True, timeout=5)
        try:
            for s in resto:
                f = con.execute("SELECT ts, accion, llamado, motivo_puerta, respuesta_json FROM llm_decisions "
                                "WHERE symbol=? ORDER BY ts DESC LIMIT 1", (s,)).fetchone()
                if f:
                    out[s] = _estado_flota(f)
        finally:
            con.close()
    for e in out.values():
        edad = _edad_s(e.get("ts"))
        e["edad_s"] = int(edad) if edad is not None else None
        if e["estado"] not in ("posicion", "orden", "sin_dato") and edad is not None and edad > VIEJO_S:
            e["estado"] = "parado"
    return out


def estados(simbolos):
    """{simbolo: {agente, estado, regimen, setup, lado, texto, ts, edad_s}}. Vacio si algo falla."""
    if time.time() - _cache["t"] < CACHE_S:
        return _cache["datos"]
    try:
        datos = _calcular(simbolos)
    except Exception as e:
        print(f"[estado_agentes] {type(e).__name__}: {e}", flush=True)
        datos = _cache["datos"]
    _cache.update(t=time.time(), datos=datos)
    return datos


def detalle_scalping(symbol):
    """Lo que el scalping ve de un simbolo, para la tarjeta del detalle: estado, posicion, orden pendiente,
    ultimas decisiones y ordenes, y el resultado de hoy. {'scalping': False} si el simbolo no es de scalping."""
    if symbol not in registro.simbolos_con("scalping"):
        return {"scalping": False}
    estado = _calcular([symbol]).get(symbol)
    out = {"scalping": True, "symbol": symbol, "estado": estado, "posicion": None, "decisiones": [], "ordenes": [], "hoy": None}
    if not registro.SCALPING_DB.is_file():
        return out
    hoy = datetime.now().strftime("%Y-%m-%d")
    con = _con_scalping()
    try:
        pos = _filas(con, "SELECT lado, modo, abierta, precio, sl, tp, dist, setup, regimen, motivo_entrada, invalidacion, "
                          "be_movido, origen, mfe_r, mae_r FROM operaciones WHERE estado='abierta' AND symbol=? "
                          "ORDER BY id DESC LIMIT 1", (symbol,))
        out["posicion"] = pos[0] if pos else None
        out["decisiones"] = _filas(con, "SELECT ts, accion, regimen, setup, esperando, razon, confianza, resultado, "
                                        "duracion_s FROM decisiones WHERE symbol=? AND llamada=1 ORDER BY id DESC LIMIT 8",
                                   (symbol,))
        out["ordenes"] = _filas(con, "SELECT ts, lado, tipo, precio, invalidacion, setup, estado, resultado, resuelta "
                                     "FROM ordenes WHERE symbol=? ORDER BY id DESC LIMIT 6", (symbol,))
        h = _filas(con, "SELECT COUNT(*) AS ops, COALESCE(SUM(r_real), 0) AS r, "
                        "SUM(CASE WHEN r_real > 0 THEN 1 ELSE 0 END) AS ganadoras FROM operaciones "
                        "WHERE estado='cerrada' AND symbol=? AND cerrada >= ?", (symbol, hoy))
        out["hoy"] = h[0] if h else None
    finally:
        con.close()
    return out


def _decisiones_flota(symbol, n=8):
    con = sqlite3.connect(f"file:{registro._db_central_path()}?mode=ro", uri=True, timeout=5)
    try:
        filas = _filas(con, "SELECT ts, accion, resultado, respuesta_json, motivo_puerta FROM llm_decisions "
                            "WHERE symbol=? AND llamado=1 ORDER BY ts DESC LIMIT ?", (symbol, n))
        hoy = datetime.now().strftime("%Y-%m-%d")
        h = _filas(con, "SELECT COUNT(*) AS ops, COALESCE(SUM(r_real), 0) AS r, "
                        "SUM(CASE WHEN r_real > 0 THEN 1 ELSE 0 END) AS ganadoras FROM llm_operaciones "
                        "WHERE symbol=? AND r_real IS NOT NULL AND abierta >= ?", (symbol, hoy))
    finally:
        con.close()
    out = []
    for f in filas:
        try:
            r = json.loads(f["respuesta_json"] or "{}") or {}
        except ValueError:
            r = {}
        out.append({"ts": f["ts"], "accion": f["accion"], "regimen": r.get("regimen"), "setup": r.get("setup"),
                    "esperando": r.get("esperando"), "razon": r.get("reason"), "confianza": r.get("confidence"),
                    "resultado": f["resultado"] if f["motivo_puerta"] != "orden pendiente disparada" else "orden disparada"})
    return out, (h[0] if h else None)


def detalle_agente(symbol):
    """Lo que ve el agente de un simbolo para la tarjeta del detalle, sea scalping o flota.
    {'tipo': 'scalping'|'flota'|None, ...}. Mismas claves en los dos casos."""
    if symbol in registro.simbolos_con("scalping"):
        d = detalle_scalping(symbol)
        d["tipo"] = "scalping"
        return d
    crudo = _leer_flota(symbol)
    if not crudo:
        return {"tipo": None, "scalping": False}
    estado = _estado_flota_json(crudo)
    edad = _edad_s(estado.get("ts"))
    estado["edad_s"] = int(edad) if edad is not None else None
    out = {"tipo": "flota", "scalping": False, "symbol": symbol, "estado": estado, "posicion": None,
           "decisiones": [], "ordenes": [], "hoy": None, "modo": crudo.get("modo")}
    pos = crudo.get("posicion")
    if pos:
        out["posicion"] = {"lado": pos.get("lado"), "modo": crudo.get("modo"), "precio": pos.get("precio"),
                           "sl": pos.get("sl"), "tp": pos.get("tp"), "flotante_eur": pos.get("flotante_eur"),
                           "minutos": pos.get("minutos"), "setup": crudo.get("setup"), "regimen": crudo.get("regimen")}
    try:
        out["decisiones"], out["hoy"] = _decisiones_flota(symbol)
    except sqlite3.Error as e:
        print(f"[estado_agentes] decisiones de {symbol}: {e}", flush=True)
    db = crudo.get("db")
    if db:
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            try:
                out["ordenes"] = _filas(con, "SELECT ts, lado, tipo, precio, invalidacion, setup, timeframe, estado, resultado, "
                                             "resuelta FROM ordenes ORDER BY id DESC LIMIT 6")
            finally:
                con.close()
        except sqlite3.Error:
            pass
    return out
