#!/usr/bin/env python
"""
Agente de scalping LLM: un proceso aparte de la flota. Cada vuelta reune los simbolos que necesitan
decision (US100, US30, US500) y los manda en UNA sola llamada al modelo; la respuesta trae una decision
por simbolo. Opera en M1 con stops en ATR de M1.

Separado de la flota a proposito: su propio magic (110101, fuera de MAGICS_FLOTA), su propia config
(.agents/data/llm_scalping.json), su propia BBDD (llm_scalping.db) y su propio prompt. No cuenta para los
topes de la flota ni la usa como cartera. Usa de llm_trader solo funciones puras (indicadores, envio de
ordenes, comision por lote, llamada al modelo).

Arranca en SIMULACION: registra la operacion que habria abierto, la sigue con las velas M1 (stop, objetivo
o tiempo) y mide el R. live=true en la config solo opera en cuenta DEMO.

Uso:
  python PST_LLM/scalping.py            # bucle continuo (vigila cada cadencia_vigilancia_s; llama al modelo segun las puertas)
  python PST_LLM/scalping.py --once     # una vuelta y sale
  python PST_LLM/scalping.py --estado   # resumen de operaciones y R, sin tocar MT5
Parar: crear .agents/data/llm_scalping.STOP
"""
import argparse
import json
import math
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np

import calendario
import llm_trader as lt
import prompts
import registro
import scalping_config

NOMBRE = "Scalping_LLM"
MAGIC = 110101                       # fuera de lt.MAGICS_FLOTA (109001-109199): no cuenta para la flota
COMMENT = "SCALP_LLM"
DATA = lt.DATA_DIR
DB_FILE = DATA / "llm_scalping.db"
CONFIG_FILE = scalping_config.CONFIG_FILE
STOP_FILE = DATA / "llm_scalping.STOP"

SIMBOLOS_PERMITIDOS = registro.SIMBOLOS_SCALPING      # medidos sin comision (2026-11-02, MT5); lista unica en registro


# Tipo de mercado que el modelo nombra en cada decision. Se guarda para medir cada setup por regimen.
REGIMENES = ["tendencia_fuerte", "tendencia", "rango", "ruptura"]
# Setups de reversion: objetivo mas corto (el centro o el otro lado del rango no da 2-3 R).
SETUPS_REVERSION = ("reversion", "rechazo_nivel")

SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "action": {"type": "string", "enum": ["hold", "buy", "sell", "close"]},
                    "sl_atr_mult": {"type": "number", "minimum": 1.0, "maximum": 2.0},
                    "tp_r": {"type": "number", "minimum": 1.0, "maximum": 3.0},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "regimen": {"type": "string", "enum": REGIMENES},
                    "setup": {"type": "string", "enum": ["breakout", "pullback", "rechazo_nivel", "reversion", "ninguno"]},
                    "esperando": {"type": "string", "maxLength": 80},
                    "orden": {
                        "type": "object",
                        "properties": {
                            "side": {"type": "string", "enum": ["buy", "sell"]},
                            "price": {"type": "number"},
                            "invalidacion": {"type": "number"},
                            "setup": {"type": "string", "enum": ["breakout", "pullback", "rechazo_nivel", "reversion"]},
                            "sl_atr_mult": {"type": "number", "minimum": 1.0, "maximum": 2.0},
                            "tp_r": {"type": "number", "minimum": 1.0, "maximum": 3.0},
                            "valid_min": {"type": "number", "minimum": 2, "maximum": 30},
                        },
                        "required": ["side", "price", "invalidacion"],
                    },
                    "invalidacion": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["symbol", "action", "regimen", "reason"],
            },
        },
    },
    "required": ["decisions"],
}

PROMPT_FIJO = """ROLE
You are a discretionary SCALPER on a DEMO account. You trade index CFDs, holding positions for minutes.
You are called once per cycle with one state per symbol. Decide each symbol on its own. Every regime has
its own setup: hold when the setup of the current regime is not there yet, not because the regime is wrong.

OUTPUT
Reply ONLY with the JSON requested by the schema: 'decisions', one entry per symbol of the input 'symbols'
list, each with its 'symbol' name. 'action' is hold, buy, sell or close. With no open position choose hold,
buy or sell. With an open position choose hold or close.
- 'sl_atr_mult': stop distance in ATR(14) of the M1 timeframe, between 1.0 and 2.0.
- 'tp_r': take profit as a multiple of the stop distance. Trend setups (breakout, pullback): 2.0 to 3.0.
  Range setups (reversion, rechazo_nivel): 1.2 to 2.0. The system clamps it to these ranges.
- 'regimen': the market regime you read: tendencia_fuerte, tendencia, rango or ruptura. Always required.
- 'setup': the pattern you trade: breakout, pullback, rechazo_nivel or reversion. 'ninguno' when you hold without one.
- 'orden': optional, only with hold and no position. A pending entry that the system triggers by itself, at
  tick speed, when price reaches 'price': {side, price, invalidacion, setup, sl_atr_mult, tp_r, valid_min}.
  Your answer takes seconds to arrive, so when the setup needs price to reach a level (break of a pause
  high/low, touch of the M1 EMA20, a range extreme) leave the order there instead of waiting for the next call.
  A buy above the current price triggers on the breakout; a buy below triggers on the pullback (same for sell).
  If price trades through 'invalidacion' first, the order is cancelled. 'valid_min' 2 to 30, default 10.
  The state shows your current 'pending_order': repeat it to keep it, change it, or leave 'orden' out to cancel it.
- 'esperando': with hold, what you wait for, in SPANISH, at most 8 words with the price
  (e.g. "compra si rompe 53089", "venta en rechazo de 7692"). Empty when nothing is close.
- 'invalidacion': the price at which your thesis is wrong. Required for buy and sell.
- 'reason': one short sentence, in English, naming the structure you trade.

RULES
- M1 includes the candle still forming ('vela_en_formacion': true). You may enter on it at any moment; you do
  not wait for the minute to close. M5 and M15 are closed candles and give the structure.
- 'niveles' holds the prior day high and low, the M15 swing high and low of the last 20 closed candles and the
  relative volume of the last closed M1 candle. The system computes them: use them as given, do not recompute.
- With an open position, 'thesis' is why you entered. Close when price reaches 'invalidation' or the thesis no
  longer holds.
- The system closes the position by itself after max_hold_min minutes and enforces spread and cost limits.
  You do not need to manage time.
- The index CFDs move together. Do not open the same direction on several symbols just because one setup
  appears; each symbol needs its own structure.

CRITERIO POR SIMBOLO
Each symbol has its own entry criterion below. Apply to each symbol only its own criterion, and only the
symbols in the input 'symbols' list.
{criterios}
"""


def montar_prompt(simbolos):
    """Prompt del lote: la parte fija y el criterio de cada simbolo, leido de la BBDD en cada llamada."""
    bloques = [f"{s}:\n{prompts.texto_seguro(prompts.clave_scalping(s)).strip()}" for s in simbolos]
    return PROMPT_FIJO.replace("{criterios}", "\n\n".join(bloques))


_ULT_TICK = {}          # simbolo -> (ultimo tick visto, instante local)
_ULT_AVISO_ERROR = {"t": 0.0}
_ULTIMA_LLAMADA = {}    # simbolo -> {"precio", "t" (instante local), "m5" (epoch de la M5 cerrada usada)}
_ULT_MOTIVO = {}        # simbolo -> ultimo motivo de no llamar al modelo


leer_config = scalping_config.leer


_ULT_SIMBOLOS = {"lista": None}


def simbolos_activos():
    """Simbolos con estrategia `scalping` en la BBDD, dentro de la lista permitida. Se relee en cada vuelta.
    Si la BBDD central falla (bloqueada o dañada), sigue con la ultima lista leida: sin eso, un 'database is
    locked' tumbaba el proceso entero con posiciones abiertas (4 nov 2026)."""
    try:
        _ULT_SIMBOLOS["lista"] = [s for s in registro.simbolos_con("scalping") if s in SIMBOLOS_PERMITIDOS]
    except Exception as e:
        print(f"[scalping] no pude leer los simbolos de la BBDD ({e}); sigo con {_ULT_SIMBOLOS['lista']}", flush=True)
    return _ULT_SIMBOLOS["lista"] or []


# Columnas añadidas despues de la primera version: se agregan a una BBDD existente sin perder su historia.
COLUMNAS_NUEVAS = {
    "decisiones": {"setup": "TEXT", "invalidacion": "REAL", "regimen": "TEXT", "esperando": "TEXT"},
    "operaciones": {"setup": "TEXT", "motivo_entrada": "TEXT", "invalidacion": "REAL", "be_movido": "INTEGER DEFAULT 0",
                    "regimen": "TEXT", "origen": "TEXT", "mfe_r": "REAL", "mae_r": "REAL"},
    "sombras": {"origen": "TEXT", "motivo": "TEXT", "setup": "TEXT"},
}


def _migrar(con):
    for tabla, cols in COLUMNAS_NUEVAS.items():
        existentes = {fila[1] for fila in con.execute(f"PRAGMA table_info({tabla})")}
        for col, tipo in cols.items():
            if col not in existentes:
                con.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}")
    con.commit()


def db():
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_FILE, timeout=15)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS decisiones (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, modo TEXT, llamada INTEGER,
            motivo TEXT, accion TEXT, confianza REAL, sl_atr REAL, tp_r REAL, razon TEXT,
            ejecutada INTEGER, resultado TEXT, coste_usd REAL, tokens_in INTEGER, tokens_out INTEGER,
            duracion_s REAL);
        CREATE TABLE IF NOT EXISTS operaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, modo TEXT, lado TEXT, abierta TEXT,
            epoch_abre INTEGER, precio REAL, sl REAL, tp REAL, dist REAL, lotes REAL, riesgo_eur REAL,
            ticket INTEGER, estado TEXT, cerrada TEXT, precio_cierre REAL, motivo_cierre TEXT,
            r_real REAL, coste_r REAL);
        CREATE TABLE IF NOT EXISTS sombras (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, ts TEXT, epoch INTEGER, lado TEXT, precio REAL,
            sl REAL, tp REAL, dist REAL, estado TEXT, motivo_cierre TEXT, r_real REAL);
        CREATE TABLE IF NOT EXISTS ordenes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, ts TEXT, modo TEXT, lado TEXT, tipo TEXT,
            precio REAL, invalidacion REAL, setup TEXT, regimen TEXT, sl_atr REAL, tp_r REAL, confianza REAL,
            razon TEXT, atr REAL, expira REAL, estado TEXT, resuelta TEXT, resultado TEXT);
    """)
    _migrar(con)
    return con


def hoy():
    return datetime.now().strftime("%Y-%m-%d")


def r_del_dia(con):
    fila = con.execute("SELECT COALESCE(SUM(r_real),0) FROM operaciones WHERE estado='cerrada' AND cerrada >= ?",
                       (hoy(),)).fetchone()
    return fila[0] or 0.0


def abiertas(con, symbol=None):
    sql = "SELECT * FROM operaciones WHERE estado='abierta'"
    args = ()
    if symbol:
        sql += " AND symbol=?"
        args = (symbol,)
    cols = [c[0] for c in con.execute("SELECT * FROM operaciones LIMIT 0").description]
    return [dict(zip(cols, f)) for f in con.execute(sql, args).fetchall()]


def mercado_vivo(symbol, tick):
    """Cotizacion nueva en los ultimos 20 min de reloj local, por simbolo (lt.mercado_vivo es global de flota)."""
    ahora = time.time()
    previo = _ULT_TICK.get(symbol)
    if previo is None or previo[0] != tick.time:
        _ULT_TICK[symbol] = (tick.time, ahora)
        return True
    return ahora - previo[1] < 1200


def calcular_niveles(mt5, symbol, digits, m15, m1):
    """Niveles que el modelo no debe recalcular: dia anterior, swing de M15 de las ultimas 20 velas cerradas
    y volumen relativo de la ultima vela M1 cerrada frente a las 20 anteriores."""
    d1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 3)
    ayer = d1[-2] if d1 is not None and len(d1) >= 3 else None      # d1[-1] es el dia en curso
    ultimas = m15[:-1][-20:]
    vol = m1["tick_volume"].astype(float)
    base = vol[-22:-2].mean()
    return {
        "prev_day_high": round(float(ayer["high"]), digits) if ayer is not None else None,
        "prev_day_low": round(float(ayer["low"]), digits) if ayer is not None else None,
        "m15_swing_high_20": round(float(ultimas["high"].max()), digits),
        "m15_swing_low_20": round(float(ultimas["low"].min()), digits),
        "vol_rel_m1": round(float(vol[-2] / base), 2) if base else None,
    }


def snapshot(mt5, symbol):
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if tick is None or info is None:
        return None
    marcos, ind, crudas = {}, {}, {}
    for tf in ("M1", "M5", "M15"):
        r = mt5.copy_rates_from_pos(symbol, getattr(mt5, f"TIMEFRAME_{tf}"), 0, 320)
        if r is None or len(r) < 250:
            return None
        crudas[tf] = r
        # M1 incluye la vela en formacion (la ultima): se opera en cuanto aparece el setup, sin esperar al
        # cierre del minuto. M5 y M15 usan solo velas cerradas: son la estructura, no el disparador.
        en_formacion = tf == "M1"
        velas = r if en_formacion else r[:-1]
        o, h, l, c = (velas[k].astype(float) for k in ("open", "high", "low", "close"))
        i = lt.indicadores(o, h, l, c)
        ind[tf] = i
        marcos[tf] = {
            "vela_en_formacion": en_formacion,
            "candles_ohlc": [[round(float(x), info.digits) for x in (o[k], h[k], l[k], c[k])] for k in range(-12, 0)],
            "ind": {k: round(float(v), info.digits if k in ("atr", "ema20", "ema50", "ema200") else 2)
                    for k, v in i.items()},
            "px_vs_ema20_atr": round(float((c[-1] - i["ema20"]) / i["atr"]), 2),
        }
    return {"tick": tick, "info": info, "ind": ind, "marcos": marcos,
            "niveles": calcular_niveles(mt5, symbol, info.digits, crudas["M15"], crudas["M1"]),
            "m5_epoch": int(crudas["M5"][-2]["time"])}


def pos_mt5(mt5, symbol):
    return [p for p in (mt5.positions_get(symbol=symbol) or []) if p.magic == MAGIC]


def estado_llm(con, symbol, snap, cfg, pos_abierta):
    tick = snap["tick"]
    estado = {
        "symbol": symbol, "call_interval_s": cfg["cadencia_s"], "max_hold_min": cfg["max_hold_min"],
        "spread_price": round(float(tick.ask - tick.bid), snap["info"].digits),
        "timeframes": snap["marcos"], "niveles": snap["niveles"], "position": None,
        "pending_order": _orden_para_modelo(orden_pendiente(con, symbol)),
        "day_r": round(r_del_dia(con), 2),
        "own_history": [{"lado": o["lado"], "setup": o["setup"], "tesis": o["motivo_entrada"], "r": o["r_real"],
                         "regimen": o["regimen"], "cierre": o["motivo_cierre"]} for o in abiertas_recientes(con, symbol)],
    }
    if pos_abierta:
        p = pos_abierta
        floating = (tick.bid - p["precio"]) if p["lado"] == "buy" else (p["precio"] - tick.ask)
        estado["position"] = {"side": p["lado"], "open_price": p["precio"], "sl": p["sl"], "tp": p["tp"],
                              "floating_r": round(floating / p["dist"], 2),
                              "minutes_held": int((tick.time - p["epoch_abre"]) // 60),
                              "setup": p["setup"], "regimen": p["regimen"], "thesis": p["motivo_entrada"], "invalidation": p["invalidacion"],
                              "breakeven_moved": bool(p["be_movido"])}
    return estado


def abiertas_recientes(con, symbol, n=5):
    cols = [c[0] for c in con.execute("SELECT * FROM operaciones LIMIT 0").description]
    filas = con.execute("SELECT * FROM operaciones WHERE symbol=? AND estado='cerrada' ORDER BY id DESC LIMIT ?",
                        (symbol, n)).fetchall()
    return [dict(zip(cols, f)) for f in filas]


def preguntar(estados, cfg):
    """Una llamada para todos los simbolos del lote. Devuelve (decision del lote, uso de la llamada)."""
    cmd = [lt._resolve_claude(), "-p", "--model", cfg["model"], "--effort", cfg["effort"],
           "--json-schema", json.dumps(SCHEMA), "--system-prompt", montar_prompt([e["symbol"] for e in estados]), "--tools", "",
           "--strict-mcp-config", "--permission-prompts", "none", "--output-format", "json",
           json.dumps({"symbols": estados}, separators=(",", ":"))]
    t0 = time.time()
    r = lt.ejecutar_claude(cmd, lt.cwd_agente(NOMBRE))
    env = json.loads(r.stdout)
    return lt._decision(env), lt._uso(env, time.time() - t0)


def decisiones_por_simbolo(lote):
    """{simbolo: decision}. Lo que el modelo no contesta se trata como hold en el que lo llame."""
    return {x.get("symbol"): x for x in (lote.get("decisions") or []) if isinstance(x, dict)}


def repartir_uso(uso, n):
    """El coste y los tokens de una llamada de lote se reparten entre los simbolos que la compartieron."""
    return {k: (v if k == "duration_s" or v is None else v / n) for k, v in uso.items()}


def limitar_sl_tp(cfg, sl_atr, tp_r, setup):
    """Stop (en ATR de M1) y objetivo (en R) dentro de los rangos de la config. Reversion tiene su rango de objetivo."""
    sl_atr = min(max(float(sl_atr or cfg["sl_atr"][0]), cfg["sl_atr"][0]), cfg["sl_atr"][1])
    rango_tp = cfg["tp_r_reversion"] if setup in SETUPS_REVERSION else cfg["tp_r"]
    tp_r = min(max(float(tp_r or rango_tp[0]), rango_tp[0]), rango_tp[1])
    return sl_atr, tp_r


def calcular(mt5, symbol, lado, snap, cfg, sl_atr, tp_r, cuenta, setup=None):
    """Orden concreta con los limites duros. Devuelve (orden, None) o (None, motivo)."""
    tick, info, atr = snap["tick"], snap["info"], snap["ind"]["M1"]["atr"]
    buy = lado == "buy"
    precio = tick.ask if buy else tick.bid
    sl_atr, tp_r = limitar_sl_tp(cfg, sl_atr, tp_r, setup)
    dist = atr * sl_atr
    spread = tick.ask - tick.bid
    if spread > cfg["max_spread_sobre_stop"] * dist:
        return None, f"spread {spread:.4g} es el {spread / dist * 100:.0f}% del stop (maximo {cfg['max_spread_sobre_stop'] * 100:.0f}%)"
    sl = precio - dist if buy else precio + dist
    tp = precio + dist * tp_r if buy else precio - dist * tp_r
    tipo = mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL
    perdida_1lote = mt5.order_calc_profit(tipo, info.name, 1.0, precio, sl)
    if not perdida_1lote:
        return None, "no se pudo calcular el riesgo por lote"
    coste_r = spread / dist + lt.comision_por_lote(mt5, info.name) / abs(perdida_1lote)
    if coste_r > cfg["max_coste_r"]:
        return None, f"coste {coste_r * 100:.1f}% del R (maximo {cfg['max_coste_r'] * 100:.0f}%)"
    riesgo = cuenta.equity * scalping_config.riesgo_de(info.name, cfg) / 100
    paso = info.volume_step
    lotes = max(info.volume_min, min(info.volume_max, math.floor(riesgo / abs(perdida_1lote) / paso) * paso))
    if lotes * abs(perdida_1lote) > riesgo * 2:
        return None, f"lote minimo arriesga {lotes * abs(perdida_1lote):.2f} > 2x objetivo {riesgo:.2f}"
    margen = mt5.order_calc_margin(tipo, info.name, lotes, precio)
    if margen and margen > lt.MARGEN_MAX_ORDEN * cuenta.equity:
        lotes = math.floor(lotes * lt.MARGEN_MAX_ORDEN * cuenta.equity / margen / paso) * paso
        if lotes < info.volume_min:
            return None, "margen: ni el lote minimo cabe en el tope por operacion"
    return {"lado": lado, "precio": precio, "sl": sl, "tp": tp, "dist": dist, "lotes": round(lotes, 2),
            "riesgo_eur": lotes * abs(perdida_1lote), "coste_r": coste_r, "epoch": tick.time,
            "digits": info.digits}, None


def abrir(mt5, con, cfg, symbol, d, snap, cuenta, origen="mercado"):
    if len(abiertas(con)) >= cfg["max_abiertas"]:
        return False, f"tope de {cfg['max_abiertas']} operaciones abiertas"
    if r_del_dia(con) <= -cfg["max_perdida_dia_r"]:
        return False, f"parada del dia: {r_del_dia(con):.2f} R"
    confianza = d.get("confidence")
    if confianza is not None and float(confianza) < cfg["confianza_minima"]:
        return False, f"confianza {float(confianza):.2f} bajo el minimo {cfg['confianza_minima']}"
    if cfg["bloquear_misma_direccion"] and any(x["lado"] == d["action"] for x in abiertas(con) if x["symbol"] != symbol):
        return False, "ya hay una operacion en el mismo sentido en otro simbolo"
    o, motivo = calcular(mt5, symbol, d["action"], snap, cfg, d.get("sl_atr_mult"), d.get("tp_r"), cuenta, d.get("setup"))
    if o is None:
        return False, motivo
    inv = d.get("invalidacion") or None
    if inv and (float(inv) >= o["precio"] if o["lado"] == "buy" else float(inv) <= o["precio"]):
        return False, f"invalidacion {inv} del lado equivocado para entrar en {o['precio']}"
    modo = "live" if cfg["live"] else "sim"
    ticket = None
    if cfg["live"]:
        info = snap["info"]
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": o["lotes"],
               "type": mt5.ORDER_TYPE_BUY if o["lado"] == "buy" else mt5.ORDER_TYPE_SELL,
               "price": o["precio"], "sl": round(o["sl"], info.digits), "tp": round(o["tp"], info.digits),
               "deviation": 30, "magic": MAGIC, "comment": COMMENT, "type_filling": lt._filling(mt5, info)}
        ok, txt, _ = lt.enviar_orden(mt5, req, cuenta)
        if not ok:
            return False, f"orden no enviada: {txt}"
        pos = []
        for _ in range(10):      # el broker tarda un momento en mostrar la posicion
            pos = pos_mt5(mt5, symbol)
            if pos:
                break
            time.sleep(0.5)
        ticket = max(pos, key=lambda p: p.time).ticket if pos else None   # None: vigilar_live lo recupera
    con.execute("""INSERT INTO operaciones (symbol, modo, lado, abierta, epoch_abre, precio, sl, tp, dist, lotes,
                   riesgo_eur, ticket, estado, coste_r, setup, motivo_entrada, invalidacion, regimen, origen, mfe_r, mae_r)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'abierta',?,?,?,?,?,?,0,0)""",
                (symbol, modo, o["lado"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), o["epoch"], o["precio"],
                 o["sl"], o["tp"], o["dist"], o["lotes"], o["riesgo_eur"], ticket, o["coste_r"],
                 d.get("setup"), d.get("reason"), inv, d.get("regimen"), origen))
    con.commit()
    return True, (f"{modo.upper()} {o['lado']} {o['lotes']:.2f} lotes @ {o['precio']:.{o['digits']}f} · "
                  f"SL {o['sl']:.{o['digits']}f} · TP {o['tp']:.{o['digits']}f} · riesgo ~{o['riesgo_eur']:.0f} EUR · "
                  f"coste {o['coste_r'] * 100:.1f}% R")


def avisar(texto):
    """Aviso por Telegram (texto plano; de 00:00 a 08:00 llega en silencio). Si Telegram falla, el agente sigue."""
    try:
        ok, detalle = lt.telegram_enviar(texto)
        if not ok:
            print(f"[scalping] telegram no enviado: {detalle}", flush=True)
    except Exception as e:
        print(f"[scalping] telegram error: {type(e).__name__}", flush=True)


def cerrar_fila(con, fila, precio, motivo, r):
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con.execute("""UPDATE operaciones SET estado='cerrada', cerrada=?, precio_cierre=?, motivo_cierre=?, r_real=?
                   WHERE id=?""", (ahora, precio, motivo, round(r, 3), fila["id"]))
    con.commit()
    total_r = con.execute("SELECT COALESCE(SUM(r_real),0) FROM operaciones WHERE estado='cerrada'").fetchone()[0]
    avisar(f"SCALPING · cierre {fila['symbol']} {fila['lado'].upper()} · {motivo} · {r:+.2f} R · "
           f"abierta {fila['abierta'][11:16]}, cerrada {ahora[11:16]} · acumulado {total_r:+.2f} R ({fila['modo']})")


def r_de_precio(fila, precio):
    signo = 1 if fila["lado"] == "buy" else -1
    return signo * (precio - fila["precio"]) / fila["dist"]


def marcar_be(con, fila, nuevo_sl):
    """Deja el stop en `nuevo_sl` (la entrada) y lo anota: la operacion ya no puede perder mas de 0 R."""
    fila["sl"], fila["be_movido"] = nuevo_sl, 1
    con.execute("UPDATE operaciones SET sl=?, be_movido=1 WHERE id=?", (nuevo_sl, fila["id"]))
    con.commit()


def toca_be(cfg, fila, v=None, precio=None):
    """True si el precio ya llego a be_r R de beneficio y el stop aun no esta en la entrada."""
    if fila["be_movido"] or not cfg["be_r"]:
        return False
    if v is not None:       # vela M1 cerrada o en formacion: se mira su extremo favorable
        extremo = float(v["high"]) if fila["lado"] == "buy" else float(v["low"])
    else:
        extremo = precio
    return r_de_precio(fila, extremo) >= cfg["be_r"]


def vigilar_sim(mt5, con, cfg, fila, snap):
    """Sigue una operacion simulada con las velas M1 desde su apertura: stop, break-even, objetivo o tiempo."""
    tick = snap["tick"]
    buy = fila["lado"] == "buy"
    velas = mt5.copy_rates_from_pos(fila["symbol"], mt5.TIMEFRAME_M1, 0, 120)
    for v in (velas if velas is not None else []):
        if int(v["time"]) <= fila["epoch_abre"]:
            continue
        bajo, alto = float(v["low"]), float(v["high"])
        sl = fila["sl"]
        if (bajo <= sl) if buy else (alto >= sl):
            motivo = "be" if fila["be_movido"] else "stop"
            cerrar_fila(con, fila, sl, motivo, r_de_precio(fila, sl))
            return motivo
        if (alto >= fila["tp"]) if buy else (bajo <= fila["tp"]):
            cerrar_fila(con, fila, fila["tp"], "objetivo", r_de_precio(fila, fila["tp"]))
            return "objetivo"
        if toca_be(cfg, fila, v=v):
            marcar_be(con, fila, fila["precio"])
    if tick.time - fila["epoch_abre"] >= cfg["max_hold_min"] * 60:
        precio = tick.bid if buy else tick.ask
        cerrar_fila(con, fila, precio, "tiempo", r_de_precio(fila, precio))
        return "tiempo"
    return None


def _cerrar_desde_historial(mt5, con, fila):
    """La posicion real ya no esta en MT5: cierra la fila con los deals de MT5. Si la fila no tiene ticket
    (no se pudo leer al abrir), lo saca del deal de entrada mas cercano a su apertura.
    Devuelve el motivo de cierre, o None si MT5 todavia no tiene la salida (se reintenta en la siguiente vuelta)."""
    # Limite superior con un dia de margen: la hora del broker va por delante de la local (2 h en este broker)
    # y con "ahora + 60 s" el cierre no aparecia hasta 2 h despues (bucle de 'close' sin posicion, nov 2026).
    deals = [d for d in (mt5.history_deals_get(fila["epoch_abre"] - 300, int(time.time()) + 86400) or [])
             if d.magic == MAGIC and d.symbol == fila["symbol"]]
    if fila["ticket"] is None:
        entradas = [d for d in deals if d.entry == 0 and d.time >= fila["epoch_abre"] - 120]
        if not entradas:
            return None
        fila["ticket"] = min(entradas, key=lambda d: abs(d.time - fila["epoch_abre"])).position_id
        con.execute("UPDATE operaciones SET ticket=? WHERE id=?", (fila["ticket"], fila["id"]))
        con.commit()
    deals = [d for d in deals if d.position_id == fila["ticket"]]
    salida = [d for d in deals if d.entry == 1]
    if not salida:
        return None
    total = sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0.0) for d in deals)
    motivo = {4: "stop", 5: "objetivo"}.get(salida[-1].reason, "mt5")
    if motivo == "stop" and fila["be_movido"]:
        motivo = "be"
    cerrar_fila(con, fila, salida[-1].price, motivo, total / fila["riesgo_eur"] if fila["riesgo_eur"] else 0.0)
    return motivo


def vigilar_live(mt5, con, cfg, fila, snap):
    """Operacion real: si MT5 ya no la tiene, la cierra en el registro con el resultado de sus deals."""
    tick = snap["tick"]
    if fila["ticket"] is None:
        abiertas_mt5 = pos_mt5(mt5, fila["symbol"])
        if not abiertas_mt5:
            return _cerrar_desde_historial(mt5, con, fila)
        fila["ticket"] = abiertas_mt5[0].ticket
        con.execute("UPDATE operaciones SET ticket=? WHERE id=?", (fila["ticket"], fila["id"]))
        con.commit()
    if not mt5.positions_get(ticket=fila["ticket"]):
        return _cerrar_desde_historial(mt5, con, fila)
    if toca_be(cfg, fila, precio=tick.bid if fila["lado"] == "buy" else tick.ask):
        mover_sl_live(mt5, con, fila, snap, fila["precio"])
    if tick.time - fila["epoch_abre"] >= cfg["max_hold_min"] * 60:
        return cerrar_live(mt5, con, fila, snap, "tiempo")
    return None


def mover_sl_live(mt5, con, fila, snap, nuevo_sl):
    """Mueve el stop de una operacion real (SLTP sobre la posicion). Si el broker lo rechaza, se reintenta en la siguiente vuelta."""
    info = snap["info"]
    req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": fila["symbol"], "position": fila["ticket"],
           "sl": round(nuevo_sl, info.digits), "tp": round(fila["tp"], info.digits), "magic": MAGIC, "comment": COMMENT}
    ok, txt, _ = lt.enviar_orden(mt5, req, mt5.account_info())
    if ok:
        marcar_be(con, fila, nuevo_sl)
    else:
        print(f"[scalping] break-even no aplicado en {fila['symbol']}: {txt}", flush=True)
    return ok


def registrar_sombras(con, cfg, symbol, snap, ts):
    """Un hold no tiene resultado. Para saber si estamos siendo pasivos se mide la entrada hipotetica en cada
    sentido, con el stop y el objetivo medios de la config. No se opera, solo se sigue."""
    tick = snap["tick"]
    dist = snap["ind"]["M1"]["atr"] * sum(cfg["sl_atr"]) / 2
    tp_r = sum(cfg["tp_r"]) / 2
    for lado in ("buy", "sell"):
        buy = lado == "buy"
        precio = tick.ask if buy else tick.bid
        sl = precio - dist if buy else precio + dist
        tp = precio + dist * tp_r if buy else precio - dist * tp_r
        con.execute("""INSERT INTO sombras (symbol, ts, epoch, lado, precio, sl, tp, dist, estado, origen)
                       VALUES (?,?,?,?,?,?,?,?,'abierta','hold')""", (symbol, ts, tick.time, lado, precio, sl, tp, dist))
    con.commit()


def registrar_sombra_frenada(con, cfg, symbol, snap, d, motivo):
    """El modelo pidio entrar y un limite lo freno (confianza, spread, tope...). Se sigue esa entrada como si se
    hubiera hecho, con su lado, stop y objetivo, para saber si el limite quita perdedoras o ganadoras."""
    tick = snap["tick"]
    lado = d.get("action")
    if lado not in ("buy", "sell"):
        return
    sl_atr, tp_r = limitar_sl_tp(cfg, d.get("sl_atr_mult"), d.get("tp_r"), d.get("setup"))
    dist = snap["ind"]["M1"]["atr"] * sl_atr
    buy = lado == "buy"
    precio = tick.ask if buy else tick.bid
    sl = precio - dist if buy else precio + dist
    tp = precio + dist * tp_r if buy else precio - dist * tp_r
    con.execute("""INSERT INTO sombras (symbol, ts, epoch, lado, precio, sl, tp, dist, estado, origen, motivo, setup)
                   VALUES (?,?,?,?,?,?,?,?,'abierta','frenada',?,?)""",
                (symbol, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tick.time, lado, precio, sl, tp, dist,
                 str(motivo)[:200], d.get("setup")))
    con.commit()


def actualizar_excursion(mt5, con, fila):
    """Lo maximo que fue a favor (mfe_r) y en contra (mae_r) una operacion abierta, en R, con las velas M1
    desde su apertura. Sirve para ajustar objetivo y stop con datos (p. ej. llegan a +1,5 R y vuelven al stop)."""
    velas = mt5.copy_rates_from_pos(fila["symbol"], mt5.TIMEFRAME_M1, 0, 120)
    vs = [v for v in (velas if velas is not None else []) if int(v["time"]) > fila["epoch_abre"]]
    if not vs:
        return
    alto = max(float(v["high"]) for v in vs)
    bajo = min(float(v["low"]) for v in vs)
    buy = fila["lado"] == "buy"
    mfe = r_de_precio(fila, alto if buy else bajo)
    mae = r_de_precio(fila, bajo if buy else alto)
    tope = abs(fila["tp"] - fila["precio"]) / fila["dist"] if fila["dist"] else 3.0
    mfe = max(float(fila.get("mfe_r") or 0.0), min(max(mfe, 0.0), tope))
    mae = min(float(fila.get("mae_r") or 0.0), max(min(mae, 0.0), -1.0))
    fila["mfe_r"], fila["mae_r"] = mfe, mae
    con.execute("UPDATE operaciones SET mfe_r=?, mae_r=? WHERE id=?", (round(mfe, 3), round(mae, 3), fila["id"]))
    con.commit()


def _filas(con, sql, args=()):
    cur = con.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, f)) for f in cur.fetchall()]


def orden_pendiente(con, symbol):
    filas = _filas(con, "SELECT * FROM ordenes WHERE estado='pendiente' AND symbol=? ORDER BY id DESC LIMIT 1", (symbol,))
    return filas[0] if filas else None


def _orden_para_modelo(o):
    if not o:
        return None
    return {"side": o["lado"], "type": o["tipo"], "price": o["precio"], "invalidacion": o["invalidacion"],
            "setup": o["setup"], "sl_atr_mult": o["sl_atr"], "tp_r": o["tp_r"],
            "minutes_left": max(0, int((o["expira"] - time.time()) // 60))}


def resolver_orden(con, oid, estado, resultado):
    con.execute("UPDATE ordenes SET estado=?, resuelta=?, resultado=? WHERE id=?",
                (estado, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(resultado)[:300], oid))
    con.commit()


def gestionar_orden(con, cfg, symbol, d, snap, ts):
    """Tras cada decision sin posicion. La 'orden' de un hold sustituye a la pendiente (o la mantiene si es la
    misma); sin 'orden', la pendiente se cancela. Devuelve un texto para el registro o None."""
    previa = orden_pendiente(con, symbol)
    o = d.get("orden") if d.get("action", "hold") == "hold" else None
    if not isinstance(o, dict) or o.get("side") not in ("buy", "sell") or not o.get("price"):
        if previa:
            resolver_orden(con, previa["id"], "cancelada",
                           "el agente no la repitio" if d.get("action", "hold") == "hold" else f"el agente eligio {d.get('action')}")
            return "orden cancelada"
        return None
    tick, atr, digits = snap["tick"], snap["ind"]["M1"]["atr"], snap["info"].digits
    lado, precio = o["side"], float(o["price"])
    inv = float(o["invalidacion"]) if o.get("invalidacion") else None
    buy = lado == "buy"
    ref = tick.ask if buy else tick.bid
    tipo = "stop" if (precio > ref if buy else precio < ref) else "limit"
    motivo = None
    if abs(precio - ref) > cfg["orden_max_dist_atr"] * atr:
        motivo = f"precio {precio} a {abs(precio - ref) / atr:.1f} ATR del actual (maximo {cfg['orden_max_dist_atr']})"
    elif inv is None or (inv >= precio if buy else inv <= precio):
        motivo = f"invalidacion {inv} del lado equivocado para {lado} en {precio}"
    valido = min(max(float(o.get("valid_min") or 10), 2), cfg["orden_max_min"])
    setup = o.get("setup") or d.get("setup")
    if previa and not motivo and previa["lado"] == lado and abs(previa["precio"] - precio) <= 0.1 * atr \
            and previa["invalidacion"] == inv:
        con.execute("UPDATE ordenes SET expira=? WHERE id=?", (time.time() + valido * 60, previa["id"]))
        con.commit()
        return f"orden mantenida: {lado} {tipo} {precio:.{digits}f}"
    if previa:
        resolver_orden(con, previa["id"], "cancelada", "sustituida por otra orden")
    con.execute("""INSERT INTO ordenes (symbol, ts, modo, lado, tipo, precio, invalidacion, setup, regimen, sl_atr, tp_r,
                   confianza, razon, atr, expira, estado, resultado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (symbol, ts, "live" if cfg["live"] else "sim", lado, tipo, precio, inv, setup, d.get("regimen"),
                 o.get("sl_atr_mult") or d.get("sl_atr_mult"), o.get("tp_r") or d.get("tp_r"), d.get("confidence"),
                 d.get("reason"), atr, time.time() + valido * 60, "rechazada" if motivo else "pendiente", motivo))
    con.commit()
    return f"orden rechazada: {motivo}" if motivo else f"orden {lado} {tipo} {precio:.{digits}f} · inv {inv} · {valido:.0f} min"


def revisar_ordenes(mt5, con, cfg, cuenta):
    """Cada pocos segundos, sin el modelo: dispara, caduca o invalida las ordenes pendientes con el tick actual."""
    for o in _filas(con, "SELECT * FROM ordenes WHERE estado='pendiente'"):
        symbol = o["symbol"]
        if time.time() > o["expira"]:
            resolver_orden(con, o["id"], "caducada", "no llego al precio a tiempo")
            continue
        if abiertas(con, symbol):
            resolver_orden(con, o["id"], "cancelada", "ya hay posicion en el simbolo")
            continue
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            continue
        buy = o["lado"] == "buy"
        ref = tick.ask if buy else tick.bid           # precio al que se entraria
        salida = tick.bid if buy else tick.ask         # precio que tocaria la invalidacion
        if o["invalidacion"] is not None and ((salida <= o["invalidacion"]) if buy else (salida >= o["invalidacion"])):
            resolver_orden(con, o["id"], "invalidada", f"el precio cruzo la invalidacion {o['invalidacion']}")
            continue
        if o["tipo"] == "stop":
            disparo = ref >= o["precio"] if buy else ref <= o["precio"]
        else:
            disparo = ref <= o["precio"] if buy else ref >= o["precio"]
        if not disparo:
            continue
        bloqueo = calendario.bloqueo_entrada(symbol)
        if bloqueo[0]:
            resolver_orden(con, o["id"], "rechazada", bloqueo[1])
            continue
        if enfriamiento_tras_stop(con, symbol, cfg):
            resolver_orden(con, o["id"], "rechazada", "enfriamiento tras stop")
            continue
        info = mt5.symbol_info(symbol)
        snap = {"tick": tick, "info": info, "ind": {"M1": {"atr": o["atr"]}}}
        d = {"action": o["lado"], "sl_atr_mult": o["sl_atr"], "tp_r": o["tp_r"], "confidence": o["confianza"],
             "setup": o["setup"], "regimen": o["regimen"], "reason": o["razon"], "invalidacion": o["invalidacion"]}
        ok, res = abrir(mt5, con, cfg, symbol, d, snap, cuenta, origen="orden")
        resolver_orden(con, o["id"], "disparada" if ok else "rechazada", res)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] {symbol} ORDEN {o['lado'].upper()} {o['tipo']} {o['precio']} -> {res}", flush=True)
        if ok:
            avisar(f"SCALPING · orden disparada {symbol} {o['lado'].upper()} ({o['tipo']} {o['precio']}) · {res}")
        else:
            registrar_sombra_frenada(con, cfg, symbol, snap, d, res)


def vigilar_sombras(mt5, con, cfg, symbol, tick):
    """Resuelve las sombras abiertas de este simbolo con las velas M1 posteriores (stop, objetivo o tiempo)."""
    filas = con.execute("SELECT id, lado, precio, sl, tp, dist, epoch FROM sombras WHERE estado='abierta' AND symbol=?",
                        (symbol,)).fetchall()
    if not filas:
        return
    velas = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 120)
    for id_, lado, precio, sl, tp, dist, epoch in filas:
        buy = lado == "buy"
        salida = None
        for v in (velas if velas is not None else []):
            if int(v["time"]) <= epoch:
                continue
            bajo, alto = float(v["low"]), float(v["high"])
            if (bajo <= sl) if buy else (alto >= sl):
                salida = (sl, "stop")
                break
            if (alto >= tp) if buy else (bajo <= tp):
                salida = (tp, "objetivo")
                break
        if salida is None and tick.time - epoch >= cfg["max_hold_min"] * 60:
            salida = (tick.bid if buy else tick.ask, "tiempo")
        if salida:
            precio_salida, motivo = salida
            r = (1 if buy else -1) * (precio_salida - precio) / dist
            con.execute("UPDATE sombras SET estado='cerrada', motivo_cierre=?, r_real=? WHERE id=?",
                        (motivo, round(r, 3), id_))
    con.commit()


def cerrar_live(mt5, con, fila, snap, motivo):
    p = [x for x in pos_mt5(mt5, fila["symbol"]) if x.ticket == fila["ticket"]]
    if not p:
        return _cerrar_desde_historial(mt5, con, fila)
    p = p[0]
    tick, info = snap["tick"], snap["info"]
    compra = p.type == 0
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": fila["symbol"], "volume": p.volume, "position": p.ticket,
           "type": mt5.ORDER_TYPE_SELL if compra else mt5.ORDER_TYPE_BUY,
           "price": tick.bid if compra else tick.ask, "deviation": 30, "magic": MAGIC, "comment": COMMENT,
           "type_filling": lt._filling(mt5, info)}
    ok, txt, _ = lt.enviar_orden(mt5, req, mt5.account_info())
    return f"{motivo} {'ok' if ok else 'NO enviado: ' + txt}"


def enfriamiento_tras_stop(con, symbol, cfg):
    """True si el ultimo cierre de este simbolo fue un stop hace menos de cooldown_tras_stop_min minutos."""
    minutos = cfg.get("cooldown_tras_stop_min", 0)
    if not minutos:
        return False
    fila = con.execute("SELECT motivo_cierre, cerrada FROM operaciones WHERE estado='cerrada' AND symbol=? "
                       "ORDER BY id DESC LIMIT 1", (symbol,)).fetchone()
    if not fila or fila[0] != "stop":
        return False
    return (datetime.now() - datetime.strptime(fila[1], "%Y-%m-%d %H:%M:%S")).total_seconds() < minutos * 60


ESTADO_FILE = DATA / "llm_scalping_estado.json"


def publicar_estado(symbol, d, ts, motivo=None, bloqueado=False):
    """Ultimo estado de cada simbolo para la home (lo lee /api/symbols). Bloqueado conserva el regimen de la
    ultima decision. Si falla, el agente sigue."""
    try:
        todos = json.loads(ESTADO_FILE.read_text(encoding="utf-8")) if ESTADO_FILE.is_file() else {}
    except (ValueError, OSError):
        todos = {}
    if bloqueado:
        todos[symbol] = {**(todos.get(symbol) or {}), "bloqueado": True, "motivo": motivo, "ts": ts}
    else:
        todos[symbol] = {"ts": ts, "accion": d.get("action", "hold"), "regimen": d.get("regimen"),
                         "setup": d.get("setup"), "esperando": d.get("esperando") or None, "razon": d.get("reason"),
                         "confianza": d.get("confidence"), "bloqueado": False, "motivo": motivo}
    try:
        tmp = ESTADO_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(todos, ensure_ascii=False), encoding="utf-8")
        tmp.replace(ESTADO_FILE)
    except OSError as e:
        print(f"[scalping] no pude publicar el estado de {symbol}: {e}", flush=True)


def sin_llamada(con, symbol, modo, ts, motivo):
    """Anota por que no se llama al modelo. Con la vigilancia cada 10 s, solo se registra cuando cambia el motivo."""
    if _ULT_MOTIVO.get(symbol) == motivo:
        return
    _ULT_MOTIVO[symbol] = motivo
    print(f"[{ts}] {symbol} sin llamada: {motivo}", flush=True)
    publicar_estado(symbol, None, ts, motivo, bloqueado=True)
    con.execute("INSERT INTO decisiones (ts, symbol, modo, llamada, motivo, accion, resultado) VALUES (?,?,?,0,?,'-',?)",
                (ts, symbol, modo, motivo, motivo))
    con.commit()


def perdidas_seguidas_hoy(con, symbol):
    filas = con.execute("SELECT r_real FROM operaciones WHERE estado='cerrada' AND symbol=? AND cerrada >= ? "
                        "ORDER BY id DESC", (symbol, hoy())).fetchall()
    n = 0
    for (r,) in filas:
        if r is None or r >= 0:
            break
        n += 1
    return n


def necesita_llamar(symbol, snap, cfg, pos):
    """Con posicion: se llama cada cadencia_s para que el modelo siga la tesis. Sin posicion, solo si algo cambio:
    el precio se movio umbral_llamada_atr desde la ultima llamada, se cerro una M5 nueva, o paso max_llamada_s."""
    previa = _ULTIMA_LLAMADA.get(symbol)
    if previa is None:
        return True
    tick = snap["tick"]
    ahora = time.time()
    transcurrido = ahora - previa["t"]
    if pos:
        return transcurrido >= cfg["cadencia_s"]
    if transcurrido >= cfg["max_llamada_s"] or snap["m5_epoch"] != previa["m5"]:
        return True
    precio = (tick.bid + tick.ask) / 2
    return abs(precio - previa["precio"]) >= cfg["umbral_llamada_atr"] * snap["ind"]["M1"]["atr"]


def preparar(mt5, con, cfg, symbol):
    """Vigila lo abierto y las sombras, y mira si este simbolo necesita decision en esta vuelta.
    Devuelve (symbol, snap, pos) si la necesita, o None."""
    snap = snapshot(mt5, symbol)
    if snap is None:
        print(f"[scalping] {symbol}: sin velas suficientes", flush=True)
        return None
    tick = snap["tick"]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    modo = "live" if cfg["live"] else "sim"
    # 1. Lo abierto: se vigila antes de decidir nada nuevo. Las sombras de los holds tambien se siguen.
    for fila in abiertas(con, symbol):
        actualizar_excursion(mt5, con, fila)
        if fila["modo"] == "sim":
            motivo = vigilar_sim(mt5, con, cfg, fila, snap)
        else:
            motivo = vigilar_live(mt5, con, cfg, fila, snap)
        if motivo:
            print(f"[{ts}] {symbol} cierre: {motivo}", flush=True)
    vigilar_sombras(mt5, con, cfg, symbol, tick)
    pos = abiertas(con, symbol)
    pos = pos[0] if pos else None
    # 2. Puertas antes de llamar al modelo (sin llamada = sin coste).
    motivo_sin = None
    if not mercado_vivo(symbol, tick):
        motivo_sin = "mercado sin cotizaciones nuevas (20 min)"
    elif not pos and calendario.bloqueo_entrada(symbol)[0]:
        motivo_sin = calendario.bloqueo_entrada(symbol)[1]
    elif not pos and enfriamiento_tras_stop(con, symbol, cfg):
        motivo_sin = f"enfriamiento: stop en {symbol} hace menos de {cfg['cooldown_tras_stop_min']} min"
    elif not pos and cfg["max_perdidas_seguidas"] and perdidas_seguidas_hoy(con, symbol) >= cfg["max_perdidas_seguidas"]:
        motivo_sin = f"{cfg['max_perdidas_seguidas']} perdidas seguidas hoy en {symbol}"
    if motivo_sin:
        sin_llamada(con, symbol, modo, ts, motivo_sin)
        return None
    _ULT_MOTIVO.pop(symbol, None)
    # 3. Sin nada que decidir (el precio no se ha movido ni hay vela M5 nueva): no se llama.
    if not necesita_llamar(symbol, snap, cfg, pos):
        return None
    return symbol, snap, pos


def ejecutar(mt5, con, cfg, symbol, d, snap, pos, cuenta, uso, ts):
    """Aplica la decision del modelo para un simbolo y la anota en la BBDD."""
    tick = snap["tick"]
    modo = "live" if cfg["live"] else "sim"
    accion = d.get("action", "hold")
    ejecutada, resultado = False, "-"
    if accion == "close" and pos:
        if cfg["live"]:
            resultado = cerrar_live(mt5, con, pos, snap, "agente") or "sin posicion en MT5"
            ejecutada = True
        else:
            precio = tick.bid if pos["lado"] == "buy" else tick.ask
            cerrar_fila(con, pos, precio, "agente", r_de_precio(pos, precio))
            resultado, ejecutada = "SIM cierre por el agente", True
    elif accion in ("buy", "sell") and not pos:
        ejecutada, resultado = abrir(mt5, con, cfg, symbol, d, snap, cuenta)
        if ejecutada:
            avisar(f"SCALPING · apertura {symbol} {accion.upper()} · {resultado}")
        else:
            registrar_sombra_frenada(con, cfg, symbol, snap, d, resultado)
    elif accion in ("buy", "sell") and pos:
        resultado = "ya hay posicion: se ignora abrir"
    if accion == "hold" and not pos:
        registrar_sombras(con, cfg, symbol, snap, ts)
    if not pos:
        texto_orden = gestionar_orden(con, cfg, symbol, d, snap, ts)
        if texto_orden:
            resultado = texto_orden if resultado == "-" else f"{resultado} · {texto_orden}"
    con.execute("""INSERT INTO decisiones (ts, symbol, modo, llamada, accion, confianza, sl_atr, tp_r, razon, setup,
                   invalidacion, ejecutada, resultado, coste_usd, tokens_in, tokens_out, duracion_s, regimen, esperando)
                   VALUES (?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ts, symbol, modo, accion, d.get("confidence"), d.get("sl_atr_mult"), d.get("tp_r"), d.get("reason"),
                 d.get("setup"), d.get("invalidacion") or None, int(ejecutada), str(resultado), uso.get("cost_usd"),
                 uso.get("tokens_in"), uso.get("tokens_out"), uso.get("duration_s"), d.get("regimen"),
                 d.get("esperando") or None))
    con.commit()
    # Entrada pedida pero frenada por un limite: la home lo muestra como "frenada" con el motivo.
    publicar_estado(symbol, d, ts, str(resultado) if accion in ("buy", "sell") and not ejecutada else None)
    print(f"[{ts}] {symbol} {accion.upper()} | {d.get('reason', '')} | {resultado} | "
          f"coste={uso.get('cost_usd')}", flush=True)


def lanzar_lote(con, cfg, pendientes, ejecutor):
    """Una sola llamada al modelo para todos los simbolos pendientes, en un hilo aparte: mientras el modelo
    piensa, el bucle sigue vigilando posiciones y disparando ordenes. El hilo solo ejecuta el CLI (sin MT5 ni BBDD)."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for symbol, snap, _ in pendientes:
        tick = snap["tick"]
        _ULTIMA_LLAMADA[symbol] = {"precio": (tick.bid + tick.ask) / 2, "t": time.time(), "m5": snap["m5_epoch"]}
    estados = [estado_llm(con, s, snap, cfg, pos) for s, snap, pos in pendientes]
    return {"futuro": ejecutor.submit(preguntar, estados, cfg), "pendientes": pendientes, "ts": ts}


def terminar_lote(mt5, con, cfg, vuelo, cuenta):
    """Aplica la respuesta del modelo con el precio y la posicion de AHORA (la llamada tarda segundos)."""
    ts, pendientes = vuelo["ts"], vuelo["pendientes"]
    modo = "live" if cfg["live"] else "sim"
    try:
        lote, uso = vuelo["futuro"].result()
    except Exception as e:
        print(f"[{ts}] error del agente: {e}", flush=True)
        if time.time() - _ULT_AVISO_ERROR["t"] > 1800:
            _ULT_AVISO_ERROR["t"] = time.time()
            avisar(f"SCALPING · error del agente: {str(e)[:200]}")
        for symbol, _, _ in pendientes:
            con.execute("INSERT INTO decisiones (ts, symbol, modo, llamada, accion, resultado) VALUES (?,?,?,1,'error',?)",
                        (ts, symbol, modo, str(e)[:300]))
        con.commit()
        return
    por_simbolo = decisiones_por_simbolo(lote)
    reparto = repartir_uso(uso, len(pendientes))
    for symbol, snap, _ in pendientes:
        tick = mt5.symbol_info_tick(symbol)
        if tick is not None:
            snap["tick"] = tick
        pos = abiertas(con, symbol)
        pos = pos[0] if pos else None
        d = por_simbolo.get(symbol, {"action": "hold", "reason": "el modelo no contesto a este simbolo"})
        ejecutar(mt5, con, cfg, symbol, d, snap, pos, cuenta, reparto, ts)


def resumen():
    con = db()
    print("Operaciones por modo y simbolo (cerradas):")
    for fila in con.execute("""SELECT modo, symbol, COUNT(*), ROUND(AVG(r_real),3), ROUND(SUM(r_real),2),
                               SUM(CASE WHEN r_real>0 THEN 1 ELSE 0 END)
                               FROM operaciones WHERE estado='cerrada' GROUP BY modo, symbol ORDER BY modo, symbol"""):
        print(f"  {fila[0]:<4} {fila[1]:<11} ops {fila[2]:>3}  R medio {fila[3]}  R total {fila[4]}  ganadoras {fila[5]}")
    print("Abiertas:", len(abiertas(con)))
    print("Acciones del modelo (llamadas):")
    for fila in con.execute("SELECT modo, accion, COUNT(*) FROM decisiones WHERE llamada=1 GROUP BY modo, accion ORDER BY modo, accion"):
        print(f"  {fila[0]:<4} {fila[1]:<6} {fila[2]:>4}")
    print("Por setup (cerradas):")
    for fila in con.execute("""SELECT COALESCE(setup, '(sin setup)'), COUNT(*), ROUND(AVG(r_real),3),
                               ROUND(AVG(CASE WHEN r_real>0 THEN 1.0 ELSE 0 END),2)
                               FROM operaciones WHERE estado='cerrada' GROUP BY 1 ORDER BY 2 DESC"""):
        print(f"  {fila[0]:<14} ops {fila[1]:>3}  R medio {fila[2]}  acierto {fila[3]}")
    print("Holds, medidos como si se hubiera entrado (stop y objetivo medios):")
    for fila in con.execute("""SELECT lado, COUNT(*), ROUND(AVG(r_real),3),
                               ROUND(AVG(CASE WHEN motivo_cierre='objetivo' THEN 1.0 ELSE 0 END),2)
                               FROM sombras WHERE estado='cerrada' GROUP BY lado ORDER BY lado"""):
        print(f"  {fila[0]:<5} ops {fila[1]:>4}  R medio {fila[2]}  objetivo {fila[3]}")
    print("Entradas frenadas por un limite, medidas como si se hubieran hecho:")
    for fila in con.execute("""SELECT substr(motivo, 1, 30), COUNT(*), ROUND(AVG(r_real),3) FROM sombras
                               WHERE origen='frenada' AND estado='cerrada' GROUP BY 1 ORDER BY 2 DESC"""):
        print(f"  {fila[0]:<30} ops {fila[1]:>4}  R medio {fila[2]}")
    print("Ordenes pendientes por estado:")
    for fila in con.execute("SELECT estado, COUNT(*) FROM ordenes GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {fila[0]:<11} {fila[1]:>4}")
    print("Excursion media (cerradas): a favor / en contra, en R:")
    for fila in con.execute("""SELECT COALESCE(setup,'(sin setup)'), COUNT(*), ROUND(AVG(mfe_r),2), ROUND(AVG(mae_r),2)
                               FROM operaciones WHERE estado='cerrada' AND mfe_r IS NOT NULL GROUP BY 1"""):
        print(f"  {fila[0]:<14} ops {fila[1]:>3}  a favor {fila[2]}  en contra {fila[3]}")
    print("Ultimas decisiones:")
    for fila in con.execute("SELECT ts, symbol, modo, accion, resultado FROM decisiones ORDER BY id DESC LIMIT 8"):
        print("  ", fila)
    con.close()


def main():
    ap = argparse.ArgumentParser(description="Agente de scalping LLM (aparte de la flota)")
    ap.add_argument("--once", action="store_true", help="una vuelta y salir")
    ap.add_argument("--estado", action="store_true", help="resumen sin tocar MT5")
    args = ap.parse_args()
    if args.estado:
        resumen()
        return 0
    import MetaTrader5 as mt5
    if not mt5.initialize():
        print(f"MT5 no disponible: {mt5.last_error()}", file=sys.stderr)
        return 1
    try:
        cuenta = mt5.account_info()
        es_demo = cuenta.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
        con = db()
        print(f"[scalping] cuenta {cuenta.login} · magic {MAGIC} · simbolos se leen de la BBDD (estrategia scalping)", flush=True)
        avisar(f"SCALPING · agente arrancado (cuenta {cuenta.login}, {'DEMO' if es_demo else 'NO demo'})")
        ejecutor = ThreadPoolExecutor(max_workers=1)
        vuelo = None            # llamada al modelo en curso: {"futuro", "pendientes", "ts"}
        ult_vigilancia = 0.0
        while True:
            cfg = leer_config()
            if cfg["live"] and not es_demo:
                print("[scalping] live solo en cuenta DEMO: se queda en simulacion", flush=True)
                cfg["live"] = False
            if not cfg["enabled"] or STOP_FILE.exists():
                print("[scalping] apagado (enabled=false o STOP)", flush=True)
                if args.once or STOP_FILE.exists():
                    break
                time.sleep(60)
                continue
            cuenta = mt5.account_info()
            # 1. Ordenes pendientes: en cada vuelta corta, con el tick, sin esperar al modelo.
            try:
                revisar_ordenes(mt5, con, cfg, cuenta)
            except Exception as e:
                print(f"[scalping] ordenes error: {e}", flush=True)
            # 2. Respuesta del modelo lista: se aplica y se vuelve a vigilar enseguida (sin esperar la cadencia).
            if vuelo and vuelo["futuro"].done():
                try:
                    terminar_lote(mt5, con, cfg, vuelo, cuenta)
                except Exception as e:
                    print(f"[scalping] lote error: {e}", flush=True)
                vuelo = None
                ult_vigilancia = 0.0
            # 3. Vigilancia de lo abierto y puertas; con una llamada en curso no se lanza otra.
            if time.time() - ult_vigilancia >= cfg["cadencia_vigilancia_s"]:
                ult_vigilancia = time.time()
                simbolos = simbolos_activos()
                if not simbolos:
                    print("[scalping] ningun simbolo con estrategia scalping", flush=True)
                pendientes = []
                for symbol in simbolos:
                    if not mt5.symbol_select(symbol, True):
                        print(f"[scalping] {symbol} no disponible", flush=True)
                        continue
                    try:
                        listo = preparar(mt5, con, cfg, symbol)
                        if listo and vuelo is None:
                            pendientes.append(listo)
                    except Exception as e:           # un simbolo que falla no para a los demas
                        print(f"[scalping] {symbol} error: {e}", flush=True)
                if pendientes:
                    ok, why, _ = lt.cuota_ok()       # solo se mira si hay algo que decidir; no en cada vuelta
                    if not ok:
                        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        for symbol, _, _ in pendientes:
                            sin_llamada(con, symbol, "live" if cfg["live"] else "sim", ts, f"freno de cuota: {why}")
                    else:
                        try:
                            vuelo = lanzar_lote(con, cfg, pendientes, ejecutor)
                        except Exception as e:
                            print(f"[scalping] lote error: {e}", flush=True)
            if args.once:
                if vuelo:
                    vuelo["futuro"].exception()          # espera a que termine la llamada
                    terminar_lote(mt5, con, cfg, vuelo, cuenta)
                break
            time.sleep(max(0.2, float(cfg["cadencia_disparo_s"])))
        ejecutor.shutdown(wait=False)
        con.close()
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())
