"""
Rotacion de la flota LLM: que simbolos operan a la vez (maximo max_activos, por defecto 10), elegidos del pool
de universo.py segun lo que este abierto ahora mismo y su puntuacion.

REGLAS (decididas con el usuario, 2026-10-22):
  - Lo activado a mano cuenta igual que lo de la rotacion: si da perdidas, sale y entra otro.
    Lo que paraste a mano queda bloqueado: la rotacion no lo vuelve a poner.
  - Solo cuentan los mercados ABIERTOS. Un simbolo con mercado cerrado y sin posicion libera su hueco.
  - Max max_activos agentes a la vez y max max_por_clase por clase (indice, accion_eeuu).
    Ambos en .agents/data/rotacion_config.json (ver _limites). Lo activado a mano no se frena con ellos.
  - Sustitucion por perdidas: 3 perdidas seguidas en el dia, o -1,5 R en el dia (1 R = 0,5 % equity),
    en ese simbolo. Max MAX_SUSTITUCIONES_DIA al dia. Un simbolo sustituido no vuelve ese dia.
    Esta sustitucion se aplica sola (no pasa por aprobacion).
  - Nunca se cierra una posicion al rotar: el simbolo sale del grupo y su posicion la gestiona el revisor.

PROPUESTAS (2026-10-23): huecos libres y reseleccion no se aplican solos. Cada cadencia_ranking_min minutos se
  calcula una lista de cambios (una entrada o una salida cada vez) y se guarda en la bandeja de propuestas
  (tabla proposals, kind rotacion_entrada / rotacion_salida). Se avisa por Telegram.
  Se decide en la bandeja de la app (Aprobar / Rechazar). Lo aprobado lo aplica la siguiente pasada de riesgo
  (cada ciclo_min) y pasa a 'viva'; si ya no es valido, pasa a 'rechazada'.
  Sin respuesta en ventana_aprobacion_min minutos, cada tipo hace lo que diga su ajuste (desde la app):
    accion_entrada / accion_salida = aprobar (se aplica sola) | rechazar | pendiente (no caduca: decides tu).
    Mientras no venza el plazo decides tu, a mano desde la bandeja o Telegram; el plazo solo actua si no contestas.
    Lo aprobado, sea a mano o por plazo, lo revalida la pasada de riesgo: una propuesta ya obsoleta no entra.
  Cada pasada de propuestas sustituye las pendientes por las nuevas; el plazo cuenta desde el primer aviso.

PUNTUACION (0-1, pesos fijos, sin LLM):
  0,35 coste   : spread frente a la distancia de stop (1,5 ATR). 1 si <=5 %, 0 si >=15 %.
  0,25 tendencia: ADX(14)/30, limitado a 1.
  0,15 volatilidad: ATR/precio entre 0,3 % y 2,5 % = 1; fuera = 0,5.
  0,25 historial : (ganadoras 7 d + 1)/(operaciones 7 d + 2). Con pocas operaciones tiende a 0,5.
PENALIZACION POR COMISION (2026-10-27): resta puntos, no entra en los pesos. Comision real del broker por operacion
  cerrada (ultimos COMISION_DIAS dias), en R. Resta penalizacion_comision * min(1, comision_R / COMISION_TOPE_R).
  Un simbolo sin comision no pierde nada; uno caro baja, pero sigue entrando si su puntuacion lo compensa.

Uso:
    python PST_LLM/rotacion.py --ranking        # puntua lo abierto ahora, sin cambiar nada
    python PST_LLM/rotacion.py --ciclo          # una pasada (riesgo, aprobadas y propuestas)
    python PST_LLM/rotacion.py --loop           # pasada de riesgo cada ciclo_min; propuestas cada cadencia_ranking_min
"""
import argparse
import json
import sqlite3
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import registro                       # noqa: E402
import universo                       # noqa: E402

import rutas                                           # rutas de ejecucion: repo o exe (PST_LLM/rutas.py)
REPO_ROOT = rutas.DATOS
ESTADO = REPO_ROOT / ".agents" / "data" / "rotacion_estado.json"
STOP = REPO_ROOT / ".agents" / "data" / "rotacion.STOP"     # crear este fichero para parar el bucle
REF_BROKER = "BTCUSD"                 # cotiza 24/7: su ultimo tick marca la hora actual del broker
CONFIG = REPO_ROOT / ".agents" / "data" / "rotacion_config.json"   # se relee en cada pasada: no hace falta reiniciar
TICK_S = 5                            # el bucle despierta cada 5 s para parar a tiempo al pedirlo
KIND = {"entrada": "rotacion_entrada", "salida": "rotacion_salida"}
PENALIZACIONES = REPO_ROOT / ".agents" / "data" / "rotacion_penalizaciones.json"   # {symbol: momento en que lo quitaste}
# Ajustes editables desde la app (rotacion_config.json). Se relee en cada pasada.
LIMITES_POR_DEFECTO = {"max_activos": 10, "max_por_clase": 5,
                       "ciclo_min": 5, "cadencia_ranking_min": 15, "ventana_aprobacion_min": 25,
                       "margen_cambio": 0.10, "max_sustituciones_dia": 2,
                       "penalizacion_cripto": 0.15, "penalizacion_comision": 0.20,
                       "penalizacion_quitar": 0.20, "penalizacion_dias": 3, "inactividad_min": 180,
                       "accion_entrada": "aprobar", "accion_salida": "aprobar"}
TIPOS = {"max_activos": int, "max_por_clase": int, "ciclo_min": int,
         "cadencia_ranking_min": int, "ventana_aprobacion_min": int,
         "margen_cambio": float, "max_sustituciones_dia": int,
         "penalizacion_cripto": float, "penalizacion_comision": float,
         "penalizacion_quitar": float, "penalizacion_dias": int,
         "inactividad_min": int, "accion_entrada": str, "accion_salida": str}
ACCIONES = ("aprobar", "rechazar", "pendiente")     # que pasa con una propuesta sin respuesta en el plazo
PERDIDAS_SEGUIDAS = 3
PERDIDA_DIA_R = -1.5
RIESGO_R_PCT = 0.5
PESOS = {"coste": 0.35, "tendencia": 0.25, "volatilidad": 0.15, "historial": 0.25}
COMISION_DIAS = 30                    # ventana para medir la comision real de cada simbolo
COMISION_TOPE_R = 0.5                 # comision por operacion (en R) a partir de la cual se aplica la penalizacion entera


def _mt5():
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise RuntimeError(f"MT5 no disponible: {mt5.last_error()}")
    return mt5


def _clamp(x):
    return max(0.0, min(1.0, x))


def _tabla():
    con = registro.central()
    con.execute("""CREATE TABLE IF NOT EXISTS llm_rotaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, accion TEXT, symbol TEXT, motivo TEXT, datos_json TEXT)""")
    return con


def _anotar(accion, symbol, motivo, datos=None):
    print(f"[rotacion] {accion} {symbol}: {motivo}", flush=True)
    try:
        con = _tabla()
        con.execute("INSERT INTO llm_rotaciones (ts, accion, symbol, motivo, datos_json) VALUES (?,?,?,?,?)",
                    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), accion, symbol, motivo,
                     json.dumps(datos or {}, ensure_ascii=False)))
        con.commit()
        con.close()
    except Exception as e:
        print(f"[rotacion] no se pudo registrar: {e}", flush=True)


def _notificar(texto, botones=None):
    """Aviso por Telegram (botones opcionales). Nunca tumba la rotacion: si falla, se anota y sigue."""
    try:
        import llm_trader as lt
        token, chat = lt._env_telegram()
        if not token or not chat:
            return
        msg = {"chat_id": chat, "text": texto[:4000], "parse_mode": "HTML", "disable_web_page_preview": True}
        if botones:
            msg["reply_markup"] = {"inline_keyboard": botones}
        body = json.dumps(msg).encode("utf-8")
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                     headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=10).read())["result"]["message_id"]
    except Exception as e:                      # sin el token en el mensaje: solo el tipo de error
        print(f"[rotacion] aviso Telegram no enviado: {type(e).__name__}", flush=True)
    return None


def _estado_dia():
    hoy = datetime.now().strftime("%Y-%m-%d")
    e = json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.is_file() else {}
    if e.get("dia") != hoy:
        e = {"dia": hoy, "sustituciones": 0, "fuera_hoy": []}
    return e


def _guardar_estado(e):
    ESTADO.parent.mkdir(parents=True, exist_ok=True)
    ESTADO.write_text(json.dumps(e, indent=2), encoding="utf-8")


def abierto(mt5, symbol, ref_time):
    t = mt5.symbol_info_tick(symbol)
    return t is not None and t.time >= ref_time - 900      # tick de los ultimos 15 min del broker


def puntuar(mt5, symbol):
    import llm_trader as lt
    tick = mt5.symbol_info_tick(symbol)
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 300)
    if rates is None or len(rates) < 210:
        return None
    ind = lt.indicadores(rates["open"].astype(float), rates["high"].astype(float),
                         rates["low"].astype(float), rates["close"].astype(float))
    atr, adx, cierre = ind["atr"], ind["adx"], float(rates["close"][-1])
    spread = tick.ask - tick.bid
    stop = 1.5 * atr
    s_coste = _clamp((0.15 - spread / stop) / 0.10) if stop > 0 else 0.0
    s_tend = _clamp(adx / 30.0)
    pct = atr / cierre if cierre else 0
    s_vol = 1.0 if 0.003 <= pct <= 0.025 else 0.5
    ganadoras, total = _resultados_7d(mt5, symbol)
    s_hist = (ganadoras + 1) / (total + 2)
    puntos = {"coste": s_coste, "tendencia": s_tend, "volatilidad": s_vol, "historial": s_hist}
    score = sum(PESOS[k] * v for k, v in puntos.items())
    return {"symbol": symbol, "score": round(score, 3), **{k: round(v, 2) for k, v in puntos.items()}}


def _resultados_7d(mt5, symbol):
    desde = datetime.now() - timedelta(days=7)
    deals = mt5.history_deals_get(desde, datetime.now() + timedelta(days=1), group=symbol) or []
    salidas = [d for d in deals if d.entry == 1]
    ganadoras = sum(1 for d in salidas if (d.profit + d.commission + d.swap) > 0)
    return ganadoras, len(salidas)


def _comision_r(mt5, symbol, r_eur):
    """Comision real del broker por operacion cerrada del simbolo, en R (0 si no hay operaciones en la ventana)."""
    desde = datetime.now() - timedelta(days=COMISION_DIAS)
    deals = mt5.history_deals_get(desde, datetime.now() + timedelta(days=1), group=symbol) or []
    salidas = [d for d in deals if d.entry == 1]
    if not salidas or r_eur <= 0:
        return 0.0
    # Se suman las comisiones de entrada y salida (algunos brokers cobran en ambas patas) y se divide entre las operaciones.
    com = sum(abs(d.commission + getattr(d, "fee", 0.0)) for d in deals)
    return (com / len(salidas)) / r_eur


def _leer_penalizaciones():
    try:
        return json.loads(PENALIZACIONES.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _guardar_penalizaciones(d):
    PENALIZACIONES.parent.mkdir(parents=True, exist_ok=True)
    PENALIZACIONES.write_text(json.dumps(d, indent=2), encoding="utf-8")


def _penalizacion_quitar(symbol, lim):
    """Puntos que resta un simbolo que quitaste. Baja linealmente a cero en penalizacion_dias."""
    desde = _leer_penalizaciones().get(symbol)
    if not desde:
        return 0.0
    dias = (datetime.now() - datetime.fromisoformat(desde)).total_seconds() / 86400
    return lim["penalizacion_quitar"] * max(0.0, 1 - dias / lim["penalizacion_dias"])


def ranking(mt5, ref_time):
    """Lista de (puntuacion, Simbolo) de los simbolos abiertos, de mejor a peor."""
    out = []
    lim = _limites()
    r_eur = _r_eur(mt5)
    for s in universo.UNIVERSO:
        if not abierto(mt5, s.symbol, ref_time):
            continue
        p = puntuar(mt5, s.symbol)
        if p:
            com_r = _comision_r(mt5, s.symbol, r_eur)
            resta = _penalizacion_quitar(s.symbol, lim)
            if s.clase == "cripto":          # menos prioridad para quedarse (comisiones)
                resta += lim["penalizacion_cripto"]
            resta += lim["penalizacion_comision"] * min(1.0, com_r / COMISION_TOPE_R)   # comision real medida
            p = {**p, "comision_r": round(com_r, 3), "score": p["score"] - resta}
            out.append((p, s))
    return sorted(out, key=lambda x: -x[0]["score"])


def _limites():
    """Ajustes de la rotacion. Lee rotacion_config.json; lo que falte o este roto usa el valor por defecto."""
    lim = dict(LIMITES_POR_DEFECTO)
    try:
        guardado = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return lim
    for k, v in guardado.items():
        if k in TIPOS:
            try:
                lim[k] = TIPOS[k](v)
            except (TypeError, ValueError):
                pass
    return lim


def guardar_ajustes(cambios):
    """Cambia los ajustes indicados (lo que no se pase se conserva). Lo llama la app; valida antes de escribir."""
    lim = _limites()
    for k, v in cambios.items():
        if v is None:
            continue
        if k not in TIPOS:
            raise ValueError(f"ajuste desconocido: {k}")
        lim[k] = TIPOS[k](v)
    if not 1 <= lim["max_activos"] <= len(universo.UNIVERSO):
        raise ValueError(f"max_activos debe estar entre 1 y {len(universo.UNIVERSO)} (tamano del universo)")
    if not 1 <= lim["max_por_clase"] <= lim["max_activos"]:
        raise ValueError(f"max_por_clase debe estar entre 1 y max_activos ({lim['max_activos']})")
    if not 1 <= lim["ciclo_min"] <= 60:
        raise ValueError("ciclo_min debe estar entre 1 y 60 minutos")
    if not 5 <= lim["cadencia_ranking_min"] <= 240:
        raise ValueError("cadencia_ranking_min debe estar entre 5 y 240 minutos")
    if not 5 <= lim["ventana_aprobacion_min"] <= 720:
        raise ValueError("ventana_aprobacion_min debe estar entre 5 y 720 minutos")
    if not 0 <= lim["margen_cambio"] <= 1:
        raise ValueError("margen_cambio debe estar entre 0 y 1")
    if not 0 <= lim["max_sustituciones_dia"] <= 10:
        raise ValueError("max_sustituciones_dia debe estar entre 0 y 10")
    if not 0 <= lim["penalizacion_cripto"] <= 0.5:
        raise ValueError("penalizacion_cripto debe estar entre 0 y 0,5")
    if not 0 <= lim["penalizacion_comision"] <= 0.5:
        raise ValueError("penalizacion_comision debe estar entre 0 y 0,5")
    if not 0 <= lim["penalizacion_quitar"] <= 0.5:
        raise ValueError("penalizacion_quitar debe estar entre 0 y 0,5")
    if not 1 <= lim["penalizacion_dias"] <= 30:
        raise ValueError("penalizacion_dias debe estar entre 1 y 30")
    if not 30 <= lim["inactividad_min"] <= 1440:
        raise ValueError("inactividad_min debe estar entre 30 y 1440 minutos")
    for k in ("accion_entrada", "accion_salida"):
        if lim[k] not in ACCIONES:
            raise ValueError(f"{k} debe ser uno de: {', '.join(ACCIONES)}")
    CONFIG.write_text(json.dumps(lim, indent=2), encoding="utf-8")
    return lim


def _clase_ok(s, activos_pool):
    return sum(1 for x in activos_pool if x.clase == s.clase) < _limites()["max_por_clase"]


def _posiciones_abiertas(mt5, symbol):
    return len(mt5.positions_get(symbol=symbol) or [])


def _perdidas_dia(mt5, magic):
    """(perdidas seguidas al final del dia, resultado neto del dia en EUR) de un agente."""
    ahora = datetime.now()
    inicio = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    deals = mt5.history_deals_get(inicio, ahora + timedelta(days=1)) or []
    salidas = sorted([d for d in deals if d.magic == magic and d.entry == 1], key=lambda d: d.time)
    neto = [d.profit + d.commission + d.swap for d in salidas]
    seguidas = 0
    for x in reversed(neto):
        if x < 0:
            seguidas += 1
        else:
            break
    return seguidas, sum(neto)


def _r_eur(mt5):
    cuenta = mt5.account_info()
    return cuenta.equity * RIESGO_R_PCT / 100.0


def _activar(symbol, motivo, datos):
    try:
        r = registro.activar(symbol, live=True, origen="rotacion")
        _penalizaciones_actualizar(symbol, quitar=True)     # al entrar, la penalizacion ya no aplica
        _anotar("activa", symbol, motivo, {**datos, "pid": r.get("pid")})
    except Exception as e:
        _anotar("error_activar", symbol, str(e), datos)


def _desactivar(symbol, motivo, datos):
    try:
        registro.desactivar(symbol, origen="rotacion")
        _anotar("desactiva", symbol, motivo, datos)
    except Exception as e:
        _anotar("error_desactivar", symbol, str(e), datos)


def _preparar(mt5):
    """Comprobaciones previas a cualquier cambio. Devuelve la hora de referencia del broker."""
    # Sin conexion, positions_get devuelve None y contaria 0 posiciones: desactivaria tambien los agentes
    # con posicion abierta. Con el terminal caido no se rota nada.
    ti = mt5.terminal_info()
    if ti is None or not ti.connected:
        raise RuntimeError("MT5 desconectado: no se rota")
    if mt5.positions_get() is None:
        raise RuntimeError(f"no se pueden leer las posiciones ({mt5.last_error()}): no se rota")
    import bloqueo
    ajeno = bloqueo.tomar_o_latir()
    if ajeno:
        raise RuntimeError(f"opera otro equipo ({ajeno}): no se rota aqui")
    ref = mt5.symbol_info_tick(REF_BROKER)
    if ref is None:
        raise RuntimeError(f"{REF_BROKER} no disponible: no hay referencia de hora del broker")
    return ref.time


def _activos_ahora():
    """Agentes activos ahora. Refresca BLOQUEADOS (lo que paraste a mano)."""
    flota = registro.estado()
    BLOQUEADOS.clear()
    BLOQUEADOS.update(x["symbol"] for x in flota if x["bloqueado"])
    return {x["symbol"]: x for x in flota if x["activo"]}


def ciclo(mt5, ranking_toca=False):
    ref_time = _preparar(mt5)
    lim = _limites()
    e = _estado_dia()
    activos = _activos_ahora()
    simbolos = {s.symbol: s for s in universo.UNIVERSO}
    rank = ranking(mt5, ref_time)
    abiertos = {p["symbol"] for p, _ in rank}

    # 1) Mercado cerrado sin posicion: libera el hueco.
    for sym in list(activos):
        if sym not in abiertos and _posiciones_abiertas(mt5, sym) == 0:
            _desactivar(sym, "mercado cerrado y sin posicion", {})
            del activos[sym]

    # 2) Perdidas: 3 seguidas o -1,5 R en el dia. Se sustituye por el mejor abierto no activo (automatico).
    r_eur = _r_eur(mt5)
    for sym in list(activos):
        magic = simbolos[sym].magic
        seguidas, neto = _perdidas_dia(mt5, magic)
        if seguidas < PERDIDAS_SEGUIDAS and neto > PERDIDA_DIA_R * r_eur:
            continue
        motivo = f"{seguidas} perdidas seguidas" if seguidas >= PERDIDAS_SEGUIDAS else f"{neto:.0f} EUR hoy ({neto / r_eur:.1f} R)"
        datos = {"perdidas_seguidas": seguidas, "neto_dia_eur": round(neto, 2), "r_eur": round(r_eur, 2)}
        if e["sustituciones"] >= lim["max_sustituciones_dia"]:
            _anotar("aviso", sym, f"{motivo}: sin sustitucion (limite {lim['max_sustituciones_dia']} al dia)", datos)
            continue
        _desactivar(sym, motivo, datos)
        e["fuera_hoy"].append(sym)
        del activos[sym]
        e["sustituciones"] += 1
        entra = _mejor_candidato(rank, activos, e)
        if entra:
            _activar(entra, f"sustituye a {sym} ({motivo})", {"sale": sym})
            activos[entra] = True
        _guardar_estado(e)

    # 3) Propuestas: las vencidas sin respuesta se resuelven por plazo; las aprobadas se aplican si siguen validas.
    _resolver_vencidas()
    _ejecutar_aprobadas(mt5, rank, lim)

    # 4) Cada cadencia_ranking_min: recalcular las propuestas pendientes (huecos y reseleccion).
    if ranking_toca:
        _proponer_ranking(mt5, rank, _activos_ahora(), e, lim)
    _guardar_estado(e)
    return len(_activos_ahora())


BLOQUEADOS = set()                    # simbolos que tu paraste a mano: la rotacion no los vuelve a poner


def _mejor_candidato(rank, activos, e):
    """Mejor simbolo abierto, no activo, no sustituido hoy y dentro del cupo por clase."""
    activos_pool = [simbolos_por_nombre(s) for s in activos]
    for p, s in rank:
        if s.symbol in activos or s.symbol in e["fuera_hoy"] or s.symbol in BLOQUEADOS:
            continue
        if not _clase_ok(s, [x for x in activos_pool if x]):
            continue
        return s.symbol
    return None


def simbolos_por_nombre(nombre):
    for s in universo.UNIVERSO:
        if s.symbol == nombre:
            return s
    return None


# ── Propuestas: bandeja de propuestas (tabla proposals) ──────────────────────

def _db():
    from PST_Core.config import DB_PATH
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _filas(con, estado):
    """Propuestas de rotacion en un estado ('propuesta', 'aprobada'...), mas antiguas primero."""
    marcas = ",".join("?" * len(KIND))
    cur = con.execute(f"SELECT * FROM proposals WHERE kind IN ({marcas}) AND state = ? ORDER BY id",
                      (*KIND.values(), estado))
    return cur.fetchall()


def _item(fila):
    """Propuesta de la tabla -> dict con lo que necesita la rotacion."""
    p = json.loads(fila["proposed_json"] or "{}")
    return {"id": fila["id"], "tipo": fila["kind"].removeprefix("rotacion_"), "symbol": fila["target"],
            "motivo": p.get("motivo", ""), "datos": p.get("datos", {}), "expira": p.get("expira")}


def _mover(con, pid, desde, hacia, por):
    """Cambia de estado con la misma comprobacion que la bandeja: solo si sigue en 'desde'."""
    sello = ", applied_at = CURRENT_TIMESTAMP" if hacia == "viva" else ""
    cur = con.execute(
        f"UPDATE proposals SET state = ?, decided_by = ?, decided_at = CURRENT_TIMESTAMP{sello}"
        f" WHERE id = ? AND state = ?", (hacia, por, pid, desde))
    con.commit()
    return cur.rowcount == 1


def propuestas_pendientes():
    """Propuestas de rotacion sin decidir (para la API y la app)."""
    con = _db()
    try:
        return [_item(f) for f in _filas(con, "propuesta")]
    finally:
        con.close()


def _penalizaciones_actualizar(symbol, quitar=False):
    """Marca (ahora) la penalizacion de un simbolo que quitaste, o la borra si vuelve a entrar."""
    d = _leer_penalizaciones()
    if quitar:
        d.pop(symbol, None)
    else:
        d[symbol] = datetime.now().isoformat(timespec="seconds")
    _guardar_penalizaciones(d)


def _insertar(con, tipo, sym, motivo, datos, score, lim):
    """Inserta una propuesta nueva en la bandeja con su plazo. Devuelve el dato para el aviso."""
    expira = (datetime.now() + timedelta(minutes=lim["ventana_aprobacion_min"])).isoformat(timespec="seconds")
    proposed = {"motivo": motivo, "datos": datos, "expira": expira}
    cur = con.execute(
        "INSERT INTO proposals (kind, target, proposed_json, evidence_json, rationale, state)"
        " VALUES (?, ?, ?, ?, ?, 'propuesta')",
        (KIND[tipo], sym, json.dumps(proposed, ensure_ascii=False), json.dumps({"score": score}), motivo))
    con.commit()
    return {"id": cur.lastrowid, "tipo": tipo, "symbol": sym, "motivo": motivo, "expira": expira}


def quitar_simbolo(symbol, por="telegram"):
    """
    Saca un simbolo del grupo a peticion tuya (no lo bloquea: la rotacion puede volver a ponerlo mas adelante)
    y propone el relleno del hueco como una entrada que apruebas en la bandeja o en Telegram.
    Devuelve un texto corto para responder.
    """
    if simbolos_por_nombre(symbol) is None:
        return f"{symbol} no esta en el universo"
    mt5 = _mt5()
    ref_time = _preparar(mt5)
    if symbol not in _activos_ahora():
        return f"{symbol} no esta activo"
    _desactivar(symbol, f"quitado desde {por}", {})
    if symbol in _activos_ahora():
        return f"no se pudo quitar {symbol} (mira el log de rotacion)"
    lim = _limites()
    _penalizaciones_actualizar(symbol)
    activos = _activos_ahora()
    e = _estado_dia()
    # El simbolo que acabo de quitar no es candidato a su propio relleno (solo para esta llamada).
    cand = _mejor_candidato(ranking(mt5, ref_time), activos, dict(e, fuera_hoy=e["fuera_hoy"] + [symbol]))
    if not cand:
        return f"{symbol} quitado (penalizado {lim['penalizacion_dias']} dias). No hay candidato abierto para rellenar el hueco ahora."
    con = _db()
    try:
        it = _insertar(con, "entrada", cand, f"relleno del hueco de {symbol} (quitado desde {por})", {}, None, lim)
    finally:
        con.close()
    _avisar_propuesta(it, lim)
    return f"{symbol} quitado (penalizado {lim['penalizacion_dias']} dias). Propuesta para rellenar: ENTRAR {cand}."


def poner_simbolo(symbol, por="telegram"):
    """
    Propone meter un simbolo elegido por ti (lo apruebas en la bandeja o en Telegram). Salta la penalizacion,
    pero no un simbolo excluido: para ese, primero hay que reactivarlo a mano.
    """
    if simbolos_por_nombre(symbol) is None:
        return f"{symbol} no esta en el universo"
    mt5 = _mt5()
    ref_time = _preparar(mt5)
    if symbol in _activos_ahora():
        return f"{symbol} ya esta activo"
    if symbol in BLOQUEADOS:
        return f"{symbol} esta excluido: reactivalo a mano para poder ponerlo"
    if symbol not in {p["symbol"] for p, _ in ranking(mt5, ref_time)}:
        return f"{symbol} no esta abierto ahora: no se puede proponer"
    lim = _limites()
    con = _db()
    try:
        it = _insertar(con, "entrada", symbol, f"puesto a mano desde {por}", {"manual": True}, None, lim)
    finally:
        con.close()
    _avisar_propuesta(it, lim)
    return f"Propuesta para meter {symbol}: decide en Telegram o en la app."


def _ultimas_llamadas():
    """symbol -> hora de la ultima llamada real al modelo (llamado=1) en la BBDD central."""
    con = registro.central()
    try:
        return {s: t for s, t in con.execute(
            "SELECT symbol, MAX(ts) FROM llm_decisions WHERE llamado = 1 GROUP BY symbol") if t}
    finally:
        con.close()


def _ociosos(activos, lim):
    """
    Agentes activos que no llaman al modelo desde hace inactividad_min (contado desde la ultima llamada o
    desde que se activaron, lo que sea mas reciente). Solo cuenta lo que no tiene posicion abierta.
    Devuelve {symbol: minutos sin llamar}.
    """
    ultimas = _ultimas_llamadas()
    ahora = datetime.now()
    out = {}
    for sym, fila in activos.items():
        marcas = [t for t in (ultimas.get(sym), fila.get("actualizado")) if t]
        if not marcas:
            continue
        desde = max(datetime.fromisoformat(t) for t in marcas)
        minutos = int((ahora - desde).total_seconds() // 60)
        if minutos >= lim["inactividad_min"]:
            out[sym] = minutos
    return out


def _proponer_ranking(mt5, rank, activos, e, lim):
    """Calcula los cambios que propone el ranking (entradas para huecos, salida+entrada para reseleccion
    o para sacar a un agente ocioso)."""
    deseadas = []                                  # (tipo, symbol, motivo, datos)
    puntos = {p["symbol"]: p["score"] for p, _ in rank}
    simulado = dict(activos)
    while len(simulado) < lim["max_activos"]:
        entra = _mejor_candidato(rank, simulado, e)
        if not entra:
            break
        deseadas.append(("entrada", entra, "hueco libre: mejor simbolo abierto", {}))
        simulado[entra] = True
    ociosos = _ociosos(activos, lim)
    # Los ociosos van primero (sin margen); el resto, de peor a mejor puntuacion, como antes.
    peores = sorted([s for s in activos if s in puntos], key=lambda s: (s not in ociosos, puntos[s]))
    for sym in peores:
        if _posiciones_abiertas(mt5, sym):
            continue
        entra = _mejor_candidato(rank, simulado, e)
        if not entra:
            break
        if sym in ociosos:
            motivo = (f"ocioso: {sym} sin llamadas al agente desde hace {ociosos[sym]} min; "
                      f"{entra} puntua {puntos[entra]:.2f}")
        elif puntos[entra] < puntos[sym] + lim["margen_cambio"]:
            break
        else:
            motivo = f"reseleccion: {entra} puntua {puntos[entra]:.2f} vs {puntos[sym]:.2f}"
        deseadas.append(("salida", sym, motivo, {"entra": entra}))
        deseadas.append(("entrada", entra, f"reseleccion: sustituye a {sym}", {"sale": sym}))
        del simulado[sym]
        simulado[entra] = True

    avisar = []
    con = _db()
    try:
        pendientes = {(f["kind"], f["target"]): f for f in _filas(con, "propuesta")}
        vistas = set()
        for tipo, sym, motivo, datos in deseadas:
            clave = (KIND[tipo], sym)
            vistas.add(clave)
            fila = pendientes.get(clave)
            if fila:                                   # misma propuesta: conserva el plazo del primer aviso
                p = json.loads(fila["proposed_json"] or "{}")
                p.update(motivo=motivo, datos=datos)
                con.execute("UPDATE proposals SET proposed_json = ?, rationale = ? WHERE id = ?",
                            (json.dumps(p, ensure_ascii=False), motivo, fila["id"]))
                con.commit()
            else:
                avisar.append(_insertar(con, tipo, sym, motivo, datos, puntos.get(sym), lim))
        for clave, fila in pendientes.items():
            if clave not in vistas:
                _mover(con, fila["id"], "propuesta", "rechazada", "retirada")
                _quitar_botones(fila["proposed_json"])
                _anotar("retirada", fila["target"], f"{fila['kind']}: ya no es el mejor cambio", {"id": fila["id"]})
    finally:
        con.close()
    for it in avisar:
        _anotar("propuesta", it["symbol"], f"{it['tipo']}: {it['motivo']}", {"id": it["id"]})
        _avisar_propuesta(it, lim)


def botones_propuesta(pid):
    """Botones de Telegram para decidir una propuesta. El bot (llm_telegram_bot.py) los recibe como 'rot:a:<id>' / 'rot:r:<id>'."""
    return [[{"text": "✅ Aprobar", "callback_data": f"rot:a:{pid}"},
             {"text": "⛔ Rechazar", "callback_data": f"rot:r:{pid}"}]]


def _avisar_propuesta(it, lim):
    """Aviso de una propuesta nueva con sus botones. Guarda el id del mensaje para quitarle los botones luego."""
    msg = _notificar(_texto_propuesta(it, lim), botones_propuesta(it["id"]))
    if not msg:
        return
    con = _db()
    try:
        p = json.loads(con.execute("SELECT proposed_json FROM proposals WHERE id = ?", (it["id"],)).fetchone()["proposed_json"] or "{}")
        p["tg_msg"] = msg
        con.execute("UPDATE proposals SET proposed_json = ? WHERE id = ?", (json.dumps(p, ensure_ascii=False), it["id"]))
        con.commit()
    finally:
        con.close()


def _quitar_botones(proposed_json):
    """Quita los botones del aviso de Telegram de una propuesta ya resuelta (desde la app o por plazo)."""
    msg = json.loads(proposed_json or "{}").get("tg_msg")
    if not msg:
        return
    try:
        import llm_trader as lt
        token, chat = lt._env_telegram()
        if not token or not chat:
            return
        body = json.dumps({"chat_id": chat, "message_id": msg, "reply_markup": {"inline_keyboard": []}}).encode("utf-8")
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/editMessageReplyMarkup", data=body,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:                      # 400 "message is not modified" si el bot ya los quito: no es un fallo
        print(f"[rotacion] no se quitaron los botones: {type(e).__name__}", flush=True)


def decidir_propuesta(pid, aprobar, por="telegram"):
    """Aprueba o rechaza una propuesta pendiente. La aplica la siguiente pasada de riesgo. Lo llama la app y el bot de Telegram."""
    con = _db()
    try:
        fila = con.execute("SELECT * FROM proposals WHERE id = ? AND kind IN (?, ?) AND state = 'propuesta'",
                           (pid, KIND["entrada"], KIND["salida"])).fetchone()
        if fila is None:
            return "ya no esta pendiente (se resolvio o caduco)"
        destino = "aprobada" if aprobar else "rechazada"
        if not _mover(con, pid, "propuesta", destino, por):
            return "ya no esta pendiente (se resolvio o caduco)"
        it = _item(fila)
        _quitar_botones(fila["proposed_json"])
        return f"{'Aprobada' if aprobar else 'Rechazada'}: {it['tipo']} {it['symbol']}"
    finally:
        con.close()


def _texto_propuesta(it, lim):
    if it["tipo"] == "entrada":
        cabecera = f"🔔 Propuesta: <b>ENTRAR {it['symbol']}</b>"
    else:
        cabecera = f"🔔 Propuesta: <b>SALIR {it['symbol']}</b>"
    accion = lim[f"accion_{it['tipo']}"]
    if accion == "pendiente":
        plazo = "queda en la bandeja hasta que decidas"
    else:
        plazo = f"sin respuesta en {lim['ventana_aprobacion_min']} min se {'aprueba y se aplica' if accion == 'aprobar' else 'rechaza'}"
    return f"{cabecera}\n{it['motivo']}\nDecide en la bandeja de propuestas: {plazo}."


def _ejecutar(it, mt5, rank, activos, lim):
    """Aplica una propuesta revalidandola con el estado de ahora. Devuelve 'aplicada' u 'obsoleta: ...'."""
    sym, motivo, datos = it["symbol"], it["motivo"], it.get("datos", {})
    abiertos = {p["symbol"] for p, _ in rank}

    def obsoleta(razon):
        _anotar("obsoleta", sym, f"{it['tipo']} no aplicada: {razon}", {"id": it["id"]})
        return f"obsoleta: {razon}"

    if it["tipo"] == "salida":
        if sym not in activos:
            return obsoleta("ya no esta activo")
        if _posiciones_abiertas(mt5, sym):
            return obsoleta("tiene posicion abierta (la gestiona el revisor)")
        _desactivar(sym, f"propuesta aprobada: {motivo}", datos)
        return "aplicada"
    if sym in activos:
        return obsoleta("ya esta activo")
    if sym not in abiertos:
        return obsoleta("mercado cerrado")
    if sym in BLOQUEADOS:
        return obsoleta("lo paraste a mano")
    if len(activos) >= lim["max_activos"]:
        return obsoleta("sin hueco (aprueba antes la salida pendiente)" if datos.get("sale") else "sin hueco")
    pool = [simbolos_por_nombre(s) for s in activos]
    if not _clase_ok(simbolos_por_nombre(sym), [x for x in pool if x]):
        return obsoleta("cupo de la clase lleno")
    _activar(sym, f"propuesta aprobada: {motivo}", datos)
    return "aplicada"


def _resolver_vencidas():
    """Plazo cumplido sin respuesta: segun accion_entrada / accion_salida se aprueba (y la aplica la siguiente pasada),
    se rechaza, o se queda pendiente sin caducar."""
    ahora = datetime.now()
    lim = _limites()
    con = _db()
    try:
        for fila in _filas(con, "propuesta"):
            it = _item(fila)
            if not it["expira"] or datetime.fromisoformat(it["expira"]) > ahora:
                continue
            accion = lim[f"accion_{it['tipo']}"]
            if accion == "pendiente":
                continue
            if accion == "aprobar":
                _mover(con, it["id"], "propuesta", "aprobada", "auto-plazo")
                _quitar_botones(fila["proposed_json"])
                _notificar(f"⏱ Sin respuesta: {it['tipo']} de <b>{it['symbol']}</b> se aplica")
            else:
                _mover(con, it["id"], "propuesta", "rechazada", "auto-plazo")
                _quitar_botones(fila["proposed_json"])
                _anotar("rechazada", it["symbol"], f"{it['tipo']} sin respuesta en el plazo", {"id": it["id"]})
                _notificar(f"⏱ Sin respuesta: {it['tipo']} de <b>{it['symbol']}</b> rechazada")
    finally:
        con.close()


def _ejecutar_aprobadas(mt5, rank, lim):
    """Aplica las propuestas aprobadas (por ti o por plazo). Si ya no son validas, pasan a rechazada."""
    con = _db()
    try:
        for fila in _filas(con, "aprobada"):
            it = _item(fila)
            estado = _ejecutar(it, mt5, rank, _activos_ahora(), lim)
            if estado == "aplicada":
                _mover(con, it["id"], "aprobada", "viva", "sistema")
            else:
                _mover(con, it["id"], "aprobada", "rechazada", "sistema")
                _notificar(f"⚠ Propuesta de <b>{it['symbol']}</b> no aplicada: {estado.removeprefix('obsoleta: ')}")
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--ranking", action="store_true")
    g.add_argument("--ciclo", action="store_true")
    g.add_argument("--loop", action="store_true")
    args = ap.parse_args()
    mt5 = _mt5()
    if args.ranking:
        ref = mt5.symbol_info_tick(REF_BROKER).time
        for p, s in ranking(mt5, ref):
            print(f"{p['score']:.3f}  {s.symbol:<11} {s.clase:<11} coste {p['coste']:.2f} tend {p['tendencia']:.2f} "
                  f"vol {p['volatilidad']:.2f} hist {p['historial']:.2f} comision {p['comision_r']:.3f} R")
        return
    if args.ciclo:
        print(f"activos tras la pasada: {ciclo(mt5, ranking_toca=True)}")
        return
    bucle()


def bucle():
    """
    Pasada de riesgo cada ciclo_min minutos y de propuestas cada cadencia_ranking_min, hasta que aparezca
    rotacion.STOP. La usan --loop y la app (hilo de fondo al arrancar). Tras cada pasada con algun agente
    activo, arranca revisor y Telegram si no corren (idempotente).
    """
    STOP.unlink(missing_ok=True)
    ultimo_ranking = 0.0
    while not STOP.exists():
        try:
            mt5 = _mt5()
            lim = _limites()
            toca = time.time() - ultimo_ranking >= lim["cadencia_ranking_min"] * 60
            n = ciclo(mt5, ranking_toca=toca)
            if toca:
                ultimo_ranking = time.time()
            if n:
                F = registro._ficheros(None)
                F.iniciar_revisor()
                F.iniciar_bot()
                F.iniciar_mejora()
        except Exception as e:
            print(f"[rotacion] error en la pasada: {e}", flush=True)
        for _ in range(_limites()["ciclo_min"] * 60 // TICK_S):
            if STOP.exists():
                break
            time.sleep(TICK_S)
    print("[rotacion] parado (fichero rotacion.STOP)", flush=True)


if __name__ == "__main__":
    main()
