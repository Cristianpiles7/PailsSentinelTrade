#!/usr/bin/env python
"""
Revisor de posiciones abiertas por los agentes LLM (magic 109001-109199). Una segunda opinion,
independiente de quien abrio la operacion, cada N minutos (5 por defecto).

SOLO toca posiciones de los agentes: las que abres tu a mano o abren los EAs de MT5 NO se miran
ni se cierran nunca (se filtra por magic).

Por que un agente aparte y no el mismo que abrio la operacion
    El que abre tiende a justificar su propia entrada. El revisor recibe la tesis original (por que
    se abrio, en que marco, donde estan stop y objetivo) y el estado actual, y su trabajo es
    buscar si esa tesis YA NO se sostiene. Por defecto mantiene: no cierra por estar en perdidas
    dentro del stop (para eso esta el stop), solo si la estructura se rompio o hay algo anormal.

Cuesta cero cuando no hay posiciones abiertas (no se llama a Claude).

Tambien avisa por Telegram de las posiciones que se cierran SOLAS (stop, objetivo o stop-out),
con su resultado. Las que cierra un agente o este revisor ya avisan con su propio mensaje.

    python PST_LLM/llm_revisor.py              # bucle
    python PST_LLM/llm_revisor.py --once       # una pasada ahora
    Parar: crear .agents/data/llm_revisor.STOP (o llm_trader.STOP, que apaga todo)
    Configuracion en caliente: .agents/data/llm_revisor.json
"""
import argparse
import html as _h
import importlib.util
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

import rutas                                           # rutas de ejecucion: repo o exe (PST_LLM/rutas.py)
REPO_ROOT = rutas.DATOS
DATA = REPO_ROOT / ".agents" / "data"
DB_FILE = DATA / "llm_revisor.db"
CONFIG_FILE = DATA / "llm_revisor.json"
STOP_FILE = DATA / "llm_revisor.STOP"
REGISTRO = DATA / "llm_trader_flota.json"
NOMBRE_SALA = "Agente_LLM"

_spec = importlib.util.spec_from_file_location("lt", rutas.PST_LLM_DIR / "llm_trader.py")
lt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lt)

REV_PROMPT = (
    "You are an independent RISK REVIEWER for a DEMO account. Another trader opened the position you "
    "are shown; you did not. You receive the position, the ORIGINAL THESIS (why it was opened, on which "
    "timeframe, where stop and target are) and the current multi-timeframe market state. Your job is to "
    "check whether the thesis still holds. Default to 'keep'. Do NOT close just because the floating P&L "
    "is negative inside the stop: that is what the stop is for. Close only if (a) the structure the "
    "thesis relied on has clearly broken on its own timeframe or on a higher one, (b) conditions became "
    "abnormal (volatility spike, spread blow-out, price action that makes the stop/target levels "
    "meaningless), or (c) the trade has gone stale: held much longer than its timeframe justifies with no "
    "progress. Be concrete: name the timeframe and what you saw. 'confidence' is how sure you are of "
    "your decision. A 'close' is rejected by the system unless the trade is at least 30 minutes old, your "
    "confidence is very high (>= 0.85) and 'invalidated_by' names the CONCRETE structure that broke "
    "(timeframe + level or indicator). Past data showed that closing by hand early cost far more than it "
    "saved, so when in doubt answer 'keep'. Keep 'reason' under 300 characters and 'invalidated_by' under 100. "
    "Reply ONLY with the JSON requested by the schema."
)
REV_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["keep", "close"]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
        "invalidated_by": {"type": "string"},
    },
    "required": ["decision", "confidence", "reason"],
}
# confianza_minima 0.85 y min_cierre_min 30 desde el 2026-10-22: su unico cierre de la primera noche
# (US500, a los 5 min, -32 EUR) habria dado +104 EUR de haberlo dejado (ver `llm_trader_flota.py --resultados`).
CONFIG_DEFECTO = {"enabled": True, "intervalo_min": 5, "model": "haiku", "effort": "low", "cerrar": True,
                  "confianza_minima": 0.85, "min_cierre_min": 30, "telegram": True, "reiniciar_cada": 6,
                  "tick_espera_s": 15}
# DEAL_REASON de MT5: quien cerro la posicion
RAZON_CIERRE = {4: "Stop loss", 5: "Take profit", 6: "Stop-out del broker", 0: "Manual", 1: "Móvil", 2: "Web"}


def db():
    con = sqlite3.connect(DB_FILE, timeout=15)
    con.execute("""CREATE TABLE IF NOT EXISTS revisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, ticket INTEGER, symbol TEXT, magic INTEGER,
        decision TEXT, confidence REAL, reason TEXT, invalidated_by TEXT, closed INTEGER, exec_result TEXT,
        floating REAL, tokens_in INTEGER, tokens_out INTEGER, tokens_cache_read INTEGER,
        tokens_cache_write INTEGER, cost_usd REAL, duration_s REAL, quota_5h REAL, quota_7d REAL)""")
    return con


def cargar_config():
    cfg = dict(CONFIG_DEFECTO)
    if CONFIG_FILE.is_file():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig")))
        except Exception as e:
            print(f"[revisor] config ilegible, se usa la anterior/por defecto: {e}", flush=True)
    else:
        CONFIG_FILE.write_text(json.dumps(CONFIG_DEFECTO, indent=2), encoding="utf-8")
    return cfg


def agentes_por_magic():
    """magic -> (nombre, ruta de su BBDD de decisiones), a partir del registro del lanzador."""
    out = {}
    try:
        for a in json.loads(REGISTRO.read_text(encoding="utf-8-sig")):
            suf = "" if a["nombre"] == "Agente_LLM" else f"_{a['nombre']}"
            out[int(a["magic"])] = (a["nombre"], DATA / f"llm_trader{suf}.db")
    except Exception:
        pass
    return out


def tesis_original(agentes, magic, ticket):
    """La decision con la que el agente abrio esta posicion (por ticket): su razon y su marco."""
    if magic not in agentes or not agentes[magic][1].is_file():
        return None
    con = sqlite3.connect(agentes[magic][1])
    try:
        f = con.execute("SELECT ts, action, horizonte, confidence, reason, sl_mult, tp_r FROM decisions "
                        "WHERE ticket=? AND executed=1 AND action IN ('buy','sell') ORDER BY id DESC LIMIT 1",
                        (ticket,)).fetchone()
    finally:
        con.close()
    if not f:
        return None
    return {"opened_by_decision_at": f[0], "side": f[1], "thesis_timeframe": f[2], "confidence": f[3],
            "original_reason": f[4], "sl_atr_mult": f[5], "tp_r": f[6]}


def tick_vivo(mt5, symbol, espera_s):
    """
    False si el simbolo no ha cambiado de precio en `espera_s` segundos: mercado cerrado o feed parado.
    Compara dos lecturas con la hora local, asi que no necesita el reloj del broker (su tick.time va en
    hora de servidor y no sirve para medir antigüedad). Si no hay precio, tampoco se revisa.
    """
    t0 = mt5.symbol_info_tick(symbol)
    if t0 is None:
        return False
    time.sleep(espera_s)
    t1 = mt5.symbol_info_tick(symbol)
    return t1 is not None and t1.time_msc != t0.time_msc


def estado_posicion(mt5, p, tesis, cfg):
    if not tick_vivo(mt5, p.symbol, cfg.get("tick_espera_s", 15)):
        print(f"[revisor] {p.symbol}: sin cambios de precio, mercado cerrado o feed parado: no se revisa", flush=True)
        return None
    snap = lt.snapshot(mt5, p.symbol, "M5", lt.TF_ANALISIS_DEFECTO)
    if snap is None:
        return None
    estado, _, tick, info, _ = snap
    precio = tick.bid if p.type == 0 else tick.ask                   # a que precio se cerraria
    mov = (precio - p.price_open) * (1 if p.type == 0 else -1)
    riesgo = abs(p.price_open - p.sl) if p.sl else None
    return {
        "position": {
            "symbol": p.symbol, "side": "buy" if p.type == 0 else "sell", "open_price": p.price_open,
            "current_price": precio, "sl": p.sl, "tp": p.tp, "floating_pnl_eur": round(p.profit, 2),
            "minutes_held": int((tick.time - p.time) // 60),
            "progress_in_R": round(mov / riesgo, 2) if riesgo else None,   # +1 = ya gana lo arriesgado; -1 = en el stop
        },
        "original_thesis": tesis or "unknown (position not found in the agent's log)",
        "market": {"spread_price": estado["spread_price"], "timeframes": estado["timeframes"]},
    }, tick, info


def cerrar_posicion(mt5, p, tick, info):
    cuenta = mt5.account_info()
    buy = p.type == 0
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume, "position": p.ticket,
           "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY, "price": tick.bid if buy else tick.ask,
           "deviation": 30, "magic": p.magic, "comment": "LLM_REVISOR", "type_filling": lt._filling(mt5, info)}
    return lt.enviar_orden(mt5, req, cuenta)


def msg_cierre_revisor(p, d, ejecutado):
    icono = "🛡️" if ejecutado else "⚠️"
    titulo = "REVISOR CIERRA LA POSICIÓN" if ejecutado else "EL REVISOR CERRARÍA LA POSICIÓN"
    lado = "compra" if p.type == 0 else "venta"
    return (f"{icono} <b>{titulo}</b> · <b>{_h.escape(p.symbol)}</b>\n"
            f"<i>{lado} #{p.ticket} · magic {p.magic}</i>\n━━━━━━━━━━━━━━━\n"
            f"💶 Flotante al cerrar: <b>{p.profit:+,.2f} €</b>\n"
            f"🧭 Confianza {float(d.get('confidence', 0)) * 100:.0f}%\n"
            f"💬 <i>{_h.escape(str(d.get('reason', ''))[:600])}</i>\n"
            f"🔎 Invalidada por: <i>{_h.escape(str(d.get('invalidated_by', '—'))[:200])}</i>\n"
            f"🕐 {datetime.now():%H:%M} · demo")


def avisar_cierres_solos(mt5, tickets_antes, tickets_ahora, magics, cfg):
    """Posiciones que desaparecieron: si las cerro el stop/objetivo/broker, avisa con el resultado."""
    for t in tickets_antes - tickets_ahora:
        deals = mt5.history_deals_get(position=t) or []
        salida = [x for x in deals if x.entry in (1, 2, 3)]
        if not salida:
            continue
        razon = salida[-1].reason
        if razon == 3:                           # DEAL_REASON_EXPERT: la cerro un agente/revisor, que ya avisan
            continue
        neto = sum(x.profit + x.commission + x.swap + getattr(x, "fee", 0.0) for x in deals)
        s = salida[-1]
        icono = "🎯" if razon == 5 else ("🛑" if razon == 4 else "⚠️")
        txt = (f"{icono} <b>{RAZON_CIERRE.get(razon, 'Cerrada')}</b> · <b>{_h.escape(s.symbol)}</b>\n"
               f"<i>posición #{t} · magic {s.magic}</i>\n━━━━━━━━━━━━━━━\n"
               f"💶 Resultado: <b>{neto:+,.2f} €</b>\n🕐 {datetime.now():%H:%M} · demo")
        print(f"[revisor] cierre solo: #{t} {s.symbol} {RAZON_CIERRE.get(razon, razon)} {neto:+.2f}", flush=True)
        if cfg["telegram"]:
            ok, det = lt.telegram_enviar(txt, html=True)
            if not ok:
                print(f"[revisor] aviso Telegram fallido: {det}", flush=True)


def pasada(mt5, cfg, con, sesion, ultima_revision, agentes):
    magics = lt.MAGICS_FLOTA
    posiciones = [p for p in (mt5.positions_get() or []) if p.magic in magics]
    ahora = time.time()
    for p in posiciones:
        if p.ticket not in ultima_revision:      # primera vez que se ve: se revisa pasado un intervalo
            ultima_revision[p.ticket] = ahora    # (no se compara con p.time: es hora del broker, no la local)
            continue
        if ahora - ultima_revision[p.ticket] < cfg["intervalo_min"] * 60:
            continue
        ultima_revision[p.ticket] = ahora
        ok_cuota, why, q = lt.cuota_ok()
        if not ok_cuota:
            print(f"[revisor] sin llamada: {why}", flush=True)
            continue
        tesis = tesis_original(agentes, p.magic, p.ticket)
        r = estado_posicion(mt5, p, tesis, cfg)
        if r is None:
            continue
        estado, tick, info = r
        try:
            d, uso = sesion.preguntar(estado, cfg["model"], cfg["effort"], NOMBRE_SALA, cfg["reiniciar_cada"])
        except Exception as e:
            print(f"[revisor] error del agente: {e}", flush=True)
            continue
        decision = d.get("decision", "keep")
        conf = float(d.get("confidence") or 0)
        cerrada, res = False, "mantiene"
        if decision == "close":
            bloqueo = lt.bloqueo_cierre(p, d.get("invalidated_by"), tick.time, cfg["min_cierre_min"])
            if conf < cfg["confianza_minima"]:
                res = f"quiere cerrar pero confianza {conf:.2f} < {cfg['confianza_minima']}: se mantiene"
            elif bloqueo:
                res = f"quiere cerrar pero {bloqueo}: se mantiene"
            elif cfg["cerrar"]:
                cerrada, res, _ = cerrar_posicion(mt5, p, tick, info)
                res = ("cerrada: " if cerrada else "FALLO al cerrar: ") + res
            else:
                res = "aviso: cerraria (modo solo-aviso)"
            # Si cierra de verdad, el aviso lo da PST_LLM/llm_telegram_bot.py (tarjeta completa con su motivo);
            # aqui solo el modo "solo avisa", donde no hay cierre que detectar.
            if not cfg["cerrar"] and cfg["telegram"]:
                lt.telegram_enviar(msg_cierre_revisor(p, d, cerrada), html=True)
        print(f"[{datetime.now():%H:%M:%S}] #{p.ticket} {p.symbol} -> {decision.upper()} ({conf:.2f}) "
              f"{p.profit:+.2f}€ | {d.get('reason', '')} | {res}", flush=True)
        con.execute("""INSERT INTO revisions (ts, ticket, symbol, magic, decision, confidence, reason, invalidated_by,
                       closed, exec_result, floating, tokens_in, tokens_out, tokens_cache_read, tokens_cache_write,
                       cost_usd, duration_s, quota_5h, quota_7d) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), p.ticket, p.symbol, p.magic, decision, conf,
                     d.get("reason"), d.get("invalidated_by"), int(cerrada), res, p.profit, uso["tokens_in"],
                     uso["tokens_out"], uso["tokens_cache_read"], uso["tokens_cache_write"], uso["cost_usd"],
                     uso["duration_s"], (q or {}).get("cinco_h"), (q or {}).get("semanal")))
        con.commit()
    return {p.ticket for p in posiciones}


def main():
    ap = argparse.ArgumentParser(description="Revisor de posiciones abiertas por los agentes LLM")
    ap.add_argument("--once", action="store_true", help="una pasada ahora (ignora el intervalo) y salir")
    a = ap.parse_args()

    import MetaTrader5 as mt5
    if not mt5.initialize():
        print(f"MT5 no disponible: {mt5.last_error()}", file=sys.stderr)
        return 1
    con = db()
    sesion = lt.SesionContinua(prompt=REV_PROMPT, schema=REV_SCHEMA)
    ultima, abiertos_antes = {}, None
    try:
        cfg = cargar_config()
        print(f"[revisor] cada {cfg['intervalo_min']} min · {'CIERRA de verdad' if cfg['cerrar'] else 'solo avisa'} "
              f"(confianza ≥ {cfg['confianza_minima']}) · solo magic {min(lt.MAGICS_FLOTA)}-{max(lt.MAGICS_FLOTA)}", flush=True)
        if a.once:
            for p in mt5.positions_get() or []:
                ultima[p.ticket] = 0
            pasada(mt5, cfg, con, sesion, ultima, agentes_por_magic())
            return 0
        while not (STOP_FILE.exists() or lt.STOP_GLOBAL.exists()):
            lt.mantener_despierto()
            cfg = cargar_config()
            if cfg["enabled"]:
                try:
                    sesion.asegurar(cfg["model"], cfg["effort"], NOMBRE_SALA)   # sala visible en Habblaud
                    ahora_t = pasada(mt5, cfg, con, sesion, ultima, agentes_por_magic())
                    abiertos_antes = ahora_t     # (los cierres por stop/objetivo ya los avisa llm_telegram_bot.py)
                except Exception as e:          # un fallo puntual no debe tumbar al revisor
                    print(f"[revisor] error en la pasada: {e}", flush=True)
            time.sleep(20)
        print("[revisor] fichero STOP detectado: parado.", flush=True)
    finally:
        sesion.cerrar()
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
