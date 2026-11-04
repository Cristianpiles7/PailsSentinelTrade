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


def _abiertas_scalping():
    if not registro.SCALPING_DB.is_file():
        return {}
    con = sqlite3.connect(f"file:{registro.SCALPING_DB}?mode=ro", uri=True, timeout=5)
    try:
        filas = con.execute("SELECT symbol, lado, setup, modo FROM operaciones WHERE estado='abierta'").fetchall()
    finally:
        con.close()
    return {s: {"lado": l, "setup": st, "modo": m} for s, l, st, m in filas}


def _estado_scalping(symbol, crudo, abierta):
    e = {"agente": "scalping", "regimen": crudo.get("regimen"), "ts": crudo.get("ts"),
         "setup": crudo.get("setup"), "texto": _primera_frase(crudo.get("razon"))}
    if abierta:
        e.update(estado="posicion", lado=abierta["lado"], setup=abierta["setup"] or e["setup"], modo=abierta["modo"])
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


def _calcular(simbolos):
    out = {}
    con_scalping = set(registro.simbolos_con("scalping"))
    try:
        crudos = json.loads(ESTADO_SCALPING.read_text(encoding="utf-8")) if ESTADO_SCALPING.is_file() else {}
    except (ValueError, OSError):
        crudos = {}
    abiertas = _abiertas_scalping()
    resto = []
    for s in simbolos:
        if s in con_scalping:
            out[s] = _estado_scalping(s, crudos.get(s) or {}, abiertas.get(s))
        else:
            resto.append(s)
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
        if e["estado"] not in ("posicion", "sin_dato") and edad is not None and edad > VIEJO_S:
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
