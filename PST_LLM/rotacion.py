"""
Rotacion de la flota LLM: que simbolos operan a la vez (maximo max_activos, por defecto 10), elegidos del pool
de universo.py segun lo que este abierto ahora mismo y su puntuacion.

REGLAS (decididas con el usuario, 2026-10-22):
  - Lo activado a mano cuenta igual que lo de la rotacion: si da perdidas, sale y entra otro.
    Lo que paraste a mano queda bloqueado: la rotacion no lo vuelve a poner.
  - Solo cuentan los mercados ABIERTOS. Un simbolo con mercado cerrado y sin posicion libera su hueco.
  - Max max_activos agentes a la vez y max max_por_clase por clase (indice, accion_eeuu).
    Ambos en .agents/data/rotacion_config.json (ver _limites). Lo activado a mano no se frena con ellos.
  - Se rellenan los huecos libres con los mejores abiertos.
  - Sustitucion por perdidas: 3 perdidas seguidas en el dia, o -1,5 R en el dia (1 R = 0,5 % equity),
    en ese simbolo. Max MAX_SUSTITUCIONES_DIA al dia. Un simbolo sustituido no vuelve ese dia.
  - Nunca se cierra una posicion al rotar: el simbolo sale del grupo y su posicion la gestiona el revisor.
  - Reseleccion programada a las 08:00 y 15:00 (hora local): entra un abierto solo si puntua
    MARGEN_CAMBIO por encima del peor activo. Histeresis para no rotar por ruido.

PUNTUACION (0-1, pesos fijos, sin LLM):
  0,35 coste   : spread frente a la distancia de stop (1,5 ATR). 1 si <=5 %, 0 si >=15 %.
  0,25 tendencia: ADX(14)/30, limitado a 1.
  0,15 volatilidad: ATR/precio entre 0,3 % y 2,5 % = 1; fuera = 0,5.
  0,25 historial : (ganadoras 7 d + 1)/(operaciones 7 d + 2). Con pocas operaciones tiende a 0,5.

Uso:
    python PST_LLM/rotacion.py --ranking        # puntua lo abierto ahora, sin cambiar nada
    python PST_LLM/rotacion.py --ciclo          # una pasada (cierres, sustituciones, relleno)
    python PST_LLM/rotacion.py --loop           # pasada cada 5 min (proceso de fondo)
"""
import argparse
import json
import sqlite3
import sys
import time
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
# Ajustes editables desde la app (rotacion_config.json). Se relee en cada pasada.
LIMITES_POR_DEFECTO = {"max_activos": 10, "max_por_clase": 5,
                       "ciclo_min": 5, "horas_reseleccion": [8, 16],
                       "margen_cambio": 0.10, "max_sustituciones_dia": 2,
                       "penalizacion_cripto": 0.15}
TIPOS = {"max_activos": int, "max_por_clase": int, "ciclo_min": int,
         "horas_reseleccion": lambda v: sorted({int(h) for h in v}),
         "margen_cambio": float, "max_sustituciones_dia": int,
         "penalizacion_cripto": float}
PERDIDAS_SEGUIDAS = 3
PERDIDA_DIA_R = -1.5
RIESGO_R_PCT = 0.5
PESOS = {"coste": 0.35, "tendencia": 0.25, "volatilidad": 0.15, "historial": 0.25}


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


def _estado_dia():
    hoy = datetime.now().strftime("%Y-%m-%d")
    e = json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.is_file() else {}
    if e.get("dia") != hoy:
        e = {"dia": hoy, "sustituciones": 0, "fuera_hoy": [], "reseleccion": []}
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


def ranking(mt5, ref_time):
    """Lista de (puntuacion, Simbolo) de los simbolos abiertos, de mejor a peor."""
    out = []
    penal = _limites()["penalizacion_cripto"]
    for s in universo.UNIVERSO:
        if not abierto(mt5, s.symbol, ref_time):
            continue
        p = puntuar(mt5, s.symbol)
        if p:
            if s.clase == "cripto":          # menos prioridad para quedarse (comisiones)
                p = {**p, "score": p["score"] - penal}
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
    if not lim["horas_reseleccion"] or any(not 0 <= h <= 23 for h in lim["horas_reseleccion"]):
        raise ValueError("horas_reseleccion: lista de horas entre 0 y 23")
    if not 0 <= lim["margen_cambio"] <= 1:
        raise ValueError("margen_cambio debe estar entre 0 y 1")
    if not 0 <= lim["max_sustituciones_dia"] <= 10:
        raise ValueError("max_sustituciones_dia debe estar entre 0 y 10")
    if not 0 <= lim["penalizacion_cripto"] <= 0.5:
        raise ValueError("penalizacion_cripto debe estar entre 0 y 0,5")
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
        _anotar("activa", symbol, motivo, {**datos, "pid": r.get("pid")})
    except Exception as e:
        _anotar("error_activar", symbol, str(e), datos)


def _desactivar(symbol, motivo, datos):
    try:
        registro.desactivar(symbol, origen="rotacion")
        _anotar("desactiva", symbol, motivo, datos)
    except Exception as e:
        _anotar("error_desactivar", symbol, str(e), datos)


def ciclo(mt5, reseleccion_permitida=False):
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
    lim = _limites()
    e = _estado_dia()
    ref = mt5.symbol_info_tick(REF_BROKER)
    if ref is None:
        raise RuntimeError(f"{REF_BROKER} no disponible: no hay referencia de hora del broker")
    ref_time = ref.time
    estado_flota = registro.estado()
    activos = {x["symbol"]: x for x in estado_flota if x["activo"]}
    BLOQUEADOS.clear()
    BLOQUEADOS.update(x["symbol"] for x in estado_flota if x["bloqueado"])
    simbolos = {s.symbol: s for s in universo.UNIVERSO}
    rank = ranking(mt5, ref_time)
    abiertos = {p["symbol"] for p, _ in rank}

    # 1) Mercado cerrado sin posicion: libera el hueco.
    for sym in list(activos):
        if sym not in abiertos and _posiciones_abiertas(mt5, sym) == 0:
            _desactivar(sym, "mercado cerrado y sin posicion", {})
            del activos[sym]

    # 2) Perdidas: 3 seguidas o -1,5 R en el dia. Se sustituye por el mejor abierto no activo.
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

    # 3) Relleno de huecos libres (arranque y aperturas de mercado).
    max_activos = lim["max_activos"]
    while len(activos) < max_activos:
        entra = _mejor_candidato(rank, activos, e)
        if not entra:
            break
        _activar(entra, "hueco libre: mejor simbolo abierto", {})
        activos[entra] = True

    # 4) Reseleccion programada: cambia solo si el candidato puntua margen_cambio mas que el peor activo.
    hora = datetime.now().hour
    clave = f"{datetime.now():%Y-%m-%d}-{hora}"
    if reseleccion_permitida and hora in lim["horas_reseleccion"] and clave not in e["reseleccion"]:
        e["reseleccion"].append(clave)
        puntos = {p["symbol"]: p["score"] for p, _ in rank}
        peores = sorted([s for s in activos if s in puntos], key=lambda s: puntos[s])
        for sym in peores:
            if _posiciones_abiertas(mt5, sym):
                continue
            entra = _mejor_candidato(rank, activos, e)
            if not entra or puntos[entra] < puntos[sym] + lim["margen_cambio"]:
                break
            _desactivar(sym, f"reseleccion: {entra} puntua {puntos[entra]:.2f} vs {puntos[sym]:.2f}", {})
            del activos[sym]
            _activar(entra, f"reseleccion {hora:02d}:00", {"sale": sym})
            activos[entra] = True
        _guardar_estado(e)
    _guardar_estado(e)
    return len(activos)


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
                  f"vol {p['volatilidad']:.2f} hist {p['historial']:.2f}")
        return
    if args.ciclo:
        print(f"activos tras la pasada: {ciclo(mt5, reseleccion_permitida=True)}")
        return
    bucle()


def bucle():
    """
    Una pasada cada ciclo_min minutos hasta que aparezca rotacion.STOP. La usan --loop y la app (hilo de fondo al
    arrancar). Tras cada pasada con algun agente activo, arranca revisor y Telegram si no corren (idempotente).
    """
    STOP.unlink(missing_ok=True)
    while not STOP.exists():
        try:
            mt5 = _mt5()
            if ciclo(mt5, reseleccion_permitida=True):
                F = registro._ficheros(None)
                F.iniciar_revisor()
                F.iniciar_bot()
        except Exception as e:
            print(f"[rotacion] error en la pasada: {e}", flush=True)
        for _ in range(_limites()["ciclo_min"] * 60 // 5):
            if STOP.exists():
                break
            time.sleep(5)
    print("[rotacion] parado (fichero rotacion.STOP)", flush=True)


if __name__ == "__main__":
    main()
