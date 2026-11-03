#!/usr/bin/env python
"""
Agente de scalping LLM: un proceso aparte de la flota. Llama al modelo cada minuto sobre pocos indices
sin comision medida (US100, US30, US500) y opera en M1 con stops en ATR de M1.

Separado de la flota a proposito: su propio magic (110101, fuera de MAGICS_FLOTA), su propia config
(.agents/data/llm_scalping.json), su propia BBDD (llm_scalping.db) y su propio prompt. No cuenta para los
topes de la flota ni la usa como cartera. Usa de llm_trader solo funciones puras (indicadores, envio de
ordenes, comision por lote, llamada al modelo).

Arranca en SIMULACION: registra la operacion que habria abierto, la sigue con las velas M1 (stop, objetivo
o tiempo) y mide el R. live=true en la config solo opera en cuenta DEMO.

Uso:
  python PST_LLM/scalping.py            # bucle continuo (una vuelta por minuto)
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
from datetime import datetime

import numpy as np

import calendario
import llm_trader as lt
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


SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["hold", "buy", "sell", "close"]},
        "sl_atr_mult": {"type": "number", "minimum": 1.0, "maximum": 2.0},
        "tp_r": {"type": "number", "minimum": 2.0, "maximum": 3.0},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": ["action", "reason"],
}

PROMPT = """ROLE
You are a discretionary SCALPER on a DEMO account. You trade one index CFD at a time, holding positions for
minutes. You are called once per minute for each symbol. Most calls should be 'hold'.

OUTPUT
Reply ONLY with the JSON decision requested by the schema. 'action' is hold, buy, sell or close. With no open
position choose hold, buy or sell. With an open position choose hold or close.
- 'sl_atr_mult': stop distance in ATR(14) of the M1 timeframe, between 1.0 and 2.0.
- 'tp_r': take profit as a multiple of the stop distance, between 2.0 and 3.0.
- 'reason': one short sentence, in English, naming the structure you trade.

RULES
- M1 includes the candle still forming ('vela_en_formacion': true). You may enter on it at any moment; you do
  not wait for the minute to close. M5 and M15 are closed candles and give the structure.
- Enter only on a PULLBACK: price pulled back to the M1 EMA20 and resumed in the trend direction. Do not enter
  on breakouts or impulse candles.
- M5 must confirm: price on the trade side of M5 EMA20, M5 ADX at least 20, and DI on the trade side.
- Skip choppy markets: M5 ADX below 20 means hold.
- Do not chase: if the last M1 candle already moved more than 0.5 ATR(M1) in your direction, hold.
- With an open position, close only if the M1 structure that justified it broke against you.
- The system closes the position by itself after max_hold_min minutes and enforces spread and cost limits.
  You do not need to manage time.
"""

_ULT_TICK = {}          # simbolo -> (ultimo tick visto, instante local)


leer_config = scalping_config.leer


def simbolos_activos():
    """Simbolos con estrategia `scalping` en la BBDD, dentro de la lista permitida. Se relee en cada vuelta."""
    return [s for s in registro.simbolos_con("scalping") if s in SIMBOLOS_PERMITIDOS]


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
    """)
    return con


def hoy():
    return datetime.now().strftime("%Y-%m-%d")


def r_del_dia(con):
    fila = con.execute("SELECT COALESCE(SUM(r_real),0) FROM operaciones WHERE estado='cerrada' AND cerrada >= ?",
                       (hoy(),)).fetchone()
    return fila[0] or 0.0


def llamadas_hoy(con):
    return con.execute("SELECT COUNT(*) FROM decisiones WHERE llamada=1 AND ts >= ?", (hoy(),)).fetchone()[0]


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


def snapshot(mt5, symbol):
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if tick is None or info is None:
        return None
    marcos, ind = {}, {}
    for tf in ("M1", "M5", "M15"):
        r = mt5.copy_rates_from_pos(symbol, getattr(mt5, f"TIMEFRAME_{tf}"), 0, 320)
        if r is None or len(r) < 250:
            return None
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
    return {"tick": tick, "info": info, "ind": ind, "marcos": marcos}


def pos_mt5(mt5, symbol):
    return [p for p in (mt5.positions_get(symbol=symbol) or []) if p.magic == MAGIC]


def estado_llm(con, symbol, snap, cfg, pos_abierta):
    tick = snap["tick"]
    estado = {
        "symbol": symbol, "call_interval_s": cfg["cadencia_s"], "max_hold_min": cfg["max_hold_min"],
        "spread_price": round(float(tick.ask - tick.bid), snap["info"].digits),
        "timeframes": snap["marcos"], "position": None,
        "day_r": round(r_del_dia(con), 2),
        "own_history": [{"lado": o["lado"], "r": o["r_real"], "motivo": o["motivo_cierre"]}
                        for o in abiertas_recientes(con, symbol)],
    }
    if pos_abierta:
        p = pos_abierta
        floating = (tick.bid - p["precio"]) if p["lado"] == "buy" else (p["precio"] - tick.ask)
        estado["position"] = {"side": p["lado"], "open_price": p["precio"], "sl": p["sl"], "tp": p["tp"],
                              "floating_r": round(floating / p["dist"], 2),
                              "minutes_held": int((tick.time - p["epoch_abre"]) // 60)}
    return estado


def abiertas_recientes(con, symbol, n=5):
    cols = [c[0] for c in con.execute("SELECT * FROM operaciones LIMIT 0").description]
    filas = con.execute("SELECT * FROM operaciones WHERE symbol=? AND estado='cerrada' ORDER BY id DESC LIMIT ?",
                        (symbol, n)).fetchall()
    return [dict(zip(cols, f)) for f in filas]


def preguntar(estado, cfg):
    cmd = [lt._resolve_claude(), "-p", "--model", cfg["model"], "--effort", cfg["effort"],
           "--json-schema", json.dumps(SCHEMA), "--system-prompt", PROMPT, "--tools", "",
           "--strict-mcp-config", "--permission-prompts", "none", "--output-format", "json",
           json.dumps(estado, separators=(",", ":"))]
    t0 = time.time()
    r = lt.ejecutar_claude(cmd, lt.cwd_agente(NOMBRE))
    env = json.loads(r.stdout)
    return lt._decision(env), lt._uso(env, time.time() - t0)


def calcular(mt5, symbol, lado, snap, cfg, sl_atr, tp_r, cuenta):
    """Orden concreta con los limites duros. Devuelve (orden, None) o (None, motivo)."""
    tick, info, atr = snap["tick"], snap["info"], snap["ind"]["M1"]["atr"]
    buy = lado == "buy"
    precio = tick.ask if buy else tick.bid
    sl_atr = min(max(float(sl_atr or cfg["sl_atr"][0]), cfg["sl_atr"][0]), cfg["sl_atr"][1])
    tp_r = min(max(float(tp_r or cfg["tp_r"][0]), cfg["tp_r"][0]), cfg["tp_r"][1])
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


def abrir(mt5, con, cfg, symbol, d, snap, cuenta):
    if len(abiertas(con)) >= cfg["max_abiertas"]:
        return False, f"tope de {cfg['max_abiertas']} operaciones abiertas"
    if r_del_dia(con) <= -cfg["max_perdida_dia_r"]:
        return False, f"parada del dia: {r_del_dia(con):.2f} R"
    o, motivo = calcular(mt5, symbol, d["action"], snap, cfg, d.get("sl_atr_mult"), d.get("tp_r"), cuenta)
    if o is None:
        return False, motivo
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
        pos = pos_mt5(mt5, symbol)
        ticket = max(pos, key=lambda p: p.time).ticket if pos else None
    con.execute("""INSERT INTO operaciones (symbol, modo, lado, abierta, epoch_abre, precio, sl, tp, dist, lotes,
                   riesgo_eur, ticket, estado, coste_r) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'abierta',?)""",
                (symbol, modo, o["lado"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), o["epoch"], o["precio"],
                 o["sl"], o["tp"], o["dist"], o["lotes"], o["riesgo_eur"], ticket, o["coste_r"]))
    con.commit()
    return True, (f"{modo.upper()} {o['lado']} {o['lotes']:.2f} lotes @ {o['precio']:.{o['digits']}f} · "
                  f"SL {o['sl']:.{o['digits']}f} · TP {o['tp']:.{o['digits']}f} · riesgo ~{o['riesgo_eur']:.0f} EUR · "
                  f"coste {o['coste_r'] * 100:.1f}% R")


_ULT_AVISO_ERROR = {"t": 0.0}


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


def vigilar_sim(mt5, con, cfg, fila, snap):
    """Sigue una operacion simulada con las velas M1 cerradas desde su apertura (stop, objetivo o tiempo)."""
    tick = snap["tick"]
    buy = fila["lado"] == "buy"
    velas = mt5.copy_rates_from_pos(fila["symbol"], mt5.TIMEFRAME_M1, 0, 120)
    for v in (velas if velas is not None else []):
        if int(v["time"]) <= fila["epoch_abre"]:
            continue
        bajo, alto = float(v["low"]), float(v["high"])
        if buy:
            if bajo <= fila["sl"]:
                return cerrar_fila(con, fila, fila["sl"], "stop", -1.0) or "stop"
            if alto >= fila["tp"]:
                return cerrar_fila(con, fila, fila["tp"], "objetivo", r_de_precio(fila, fila["tp"])) or "objetivo"
        else:
            if alto >= fila["sl"]:
                return cerrar_fila(con, fila, fila["sl"], "stop", -1.0) or "stop"
            if bajo <= fila["tp"]:
                return cerrar_fila(con, fila, fila["tp"], "objetivo", r_de_precio(fila, fila["tp"])) or "objetivo"
    if tick.time - fila["epoch_abre"] >= cfg["max_hold_min"] * 60:
        precio = tick.bid if buy else tick.ask
        cerrar_fila(con, fila, precio, "tiempo", r_de_precio(fila, precio))
        return "tiempo"
    return None


def vigilar_live(mt5, con, cfg, fila, snap):
    """Operacion real: si MT5 ya no la tiene, la cierra en el registro con el resultado de sus deals."""
    tick = snap["tick"]
    if fila["ticket"] is None:
        return None
    if not mt5.positions_get(ticket=fila["ticket"]):
        deals = mt5.history_deals_get(position=fila["ticket"]) or []
        total = sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0.0) for d in deals)
        salida = [d for d in deals if d.entry == 1]
        motivo = {4: "stop", 5: "objetivo"}.get(salida[-1].reason, "mt5") if salida else "mt5"
        precio = salida[-1].price if salida else fila["precio"]
        cerrar_fila(con, fila, precio, motivo, total / fila["riesgo_eur"] if fila["riesgo_eur"] else 0.0)
        return motivo
    if tick.time - fila["epoch_abre"] >= cfg["max_hold_min"] * 60:
        return cerrar_live(mt5, con, fila, snap, "tiempo")
    return None


def cerrar_live(mt5, con, fila, snap, motivo):
    p = [x for x in pos_mt5(mt5, fila["symbol"]) if x.ticket == fila["ticket"]]
    if not p:
        return None
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


def procesar(mt5, con, cfg, symbol, cuenta):
    snap = snapshot(mt5, symbol)
    if snap is None:
        print(f"[scalping] {symbol}: sin velas suficientes", flush=True)
        return
    tick = snap["tick"]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    modo = "live" if cfg["live"] else "sim"
    # 1. Lo abierto: se vigila antes de decidir nada nuevo.
    for fila in abiertas(con, symbol):
        if fila["modo"] == "sim":
            motivo = vigilar_sim(mt5, con, cfg, fila, snap)
        else:
            motivo = vigilar_live(mt5, con, cfg, fila, snap)
        if motivo:
            print(f"[{ts}] {symbol} cierre: {motivo}", flush=True)
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
    elif llamadas_hoy(con) >= cfg["max_llamadas_dia"]:
        motivo_sin = f"tope de {cfg['max_llamadas_dia']} llamadas/dia de este agente"
    else:
        ok, why, _ = lt.cuota_ok()
        if not ok:
            motivo_sin = f"freno de cuota: {why}"
    if motivo_sin:
        print(f"[{ts}] {symbol} sin llamada: {motivo_sin}", flush=True)
        con.execute("INSERT INTO decisiones (ts, symbol, modo, llamada, motivo, accion, resultado) VALUES (?,?,?,0,?,'-',?)",
                    (ts, symbol, modo, motivo_sin, motivo_sin))
        con.commit()
        return
    # 3. El modelo decide.
    try:
        d, uso = preguntar(estado_llm(con, symbol, snap, cfg, pos), cfg)
    except Exception as e:
        print(f"[{ts}] {symbol} error del agente: {e}", flush=True)
        if time.time() - _ULT_AVISO_ERROR["t"] > 1800:
            _ULT_AVISO_ERROR["t"] = time.time()
            avisar(f"SCALPING · error del agente en {symbol}: {str(e)[:200]}")
        con.execute("INSERT INTO decisiones (ts, symbol, modo, llamada, accion, resultado) VALUES (?,?,?,1,'error',?)",
                    (ts, symbol, modo, str(e)[:300]))
        con.commit()
        return
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
    elif accion in ("buy", "sell") and pos:
        resultado = "ya hay posicion: se ignora abrir"
    con.execute("""INSERT INTO decisiones (ts, symbol, modo, llamada, accion, confianza, sl_atr, tp_r, razon,
                   ejecutada, resultado, coste_usd, tokens_in, tokens_out, duracion_s) VALUES (?,?,?,1,?,?,?,?,?,?,?,?,?,?,?)""",
                (ts, symbol, modo, accion, d.get("confidence"), d.get("sl_atr_mult"), d.get("tp_r"), d.get("reason"),
                 int(ejecutada), str(resultado), uso.get("cost_usd"), uso.get("tokens_in"), uso.get("tokens_out"),
                 uso.get("duration_s")))
    con.commit()
    print(f"[{ts}] {symbol} {accion.upper()} | {d.get('reason', '')} | {resultado} | "
          f"coste={uso.get('cost_usd')}", flush=True)


def resumen():
    con = db()
    print("Operaciones por modo y simbolo (cerradas):")
    for fila in con.execute("""SELECT modo, symbol, COUNT(*), ROUND(AVG(r_real),3), ROUND(SUM(r_real),2),
                               SUM(CASE WHEN r_real>0 THEN 1 ELSE 0 END)
                               FROM operaciones WHERE estado='cerrada' GROUP BY modo, symbol ORDER BY modo, symbol"""):
        print(f"  {fila[0]:<4} {fila[1]:<11} ops {fila[2]:>3}  R medio {fila[3]}  R total {fila[4]}  ganadoras {fila[5]}")
    print("Abiertas:", len(abiertas(con)))
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
            inicio = time.time()
            cuenta = mt5.account_info()
            simbolos = simbolos_activos()
            if not simbolos:
                print("[scalping] ningun simbolo con estrategia scalping", flush=True)
            for symbol in simbolos:
                if not mt5.symbol_select(symbol, True):
                    print(f"[scalping] {symbol} no disponible", flush=True)
                    continue
                try:
                    procesar(mt5, con, cfg, symbol, cuenta)
                except Exception as e:           # un simbolo que falla no para a los demas
                    print(f"[scalping] {symbol} error: {e}", flush=True)
            if args.once:
                break
            espera = cfg["cadencia_s"] - (time.time() - inicio)
            time.sleep(max(float(cfg["pausa_minima_s"]), espera))
        con.close()
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())
