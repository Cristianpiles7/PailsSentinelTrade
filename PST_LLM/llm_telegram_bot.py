#!/usr/bin/env python
"""
Servicio de Telegram de la flota: avisos de cierre + menu con botones. SOLO LECTURA: no abre, cierra
ni modifica nada en MT5; unicamente lee posiciones e historial.

1) AVISO DE CADA CIERRE
   Cuando una posicion de la cuenta se cierra (stop, objetivo, cierre del agente o del revisor, de un
   EA o manual) manda una tarjeta con los datos de la operacion: entrada/salida, duracion, resultado
   neto (bruto, comision), multiplo de riesgo (R), quien la cerro y, si fue un agente o el revisor, su
   razonamiento. Guarda las ya avisadas en una BBDD para no repetir tras un reinicio; la primera vez
   siembra el historico SIN enviarlo (no inunda el chat).

2) MENU CON BOTONES (teclado fijo abajo + boton "Actualizar" en cada informe)
   Abiertas · Cerradas hoy · Ganancias hoy · General · Agentes · Cuenta

Solo contesta al chat de TELEGRAM_CHAT_ID (.env); cualquier otro se ignora. El token nunca se imprime.

IMPORTANTE: escucha con getUpdates, asi que NO puede haber otro proceso escuchando al mismo bot.
El listener del proyecto (PST_Core/engine/telegram_manager.py) solo lo arranca orchestrator.start_v6,
retirado a proposito; si algun dia se reactiva, dara error 409 aqui y habra que elegir uno.

    python PST_LLM/llm_telegram_bot.py            # servicio
    python PST_LLM/llm_telegram_bot.py --probar   # envia el menu y los informes una vez y sale
    Parar: crear .agents/data/llm_telegram.STOP
"""
import argparse
import html as _h
import importlib.util
import json
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import psutil

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

import rutas                                           # rutas de ejecucion: repo o exe (PST_LLM/rutas.py)
REPO_ROOT = rutas.DATOS
DATA = REPO_ROOT / ".agents" / "data"
DB_FILE = DATA / "llm_telegram.db"
STOP_FILE = DATA / "llm_telegram.STOP"
REGISTRO = DATA / "llm_trader_flota.json"
INICIAL = 10000.0                      # balance inicial de la cuenta (limites FTMO sobre este importe)
LIMITE_DIA, LIMITE_TOTAL = 500.0, 1000.0

_spec = importlib.util.spec_from_file_location("lt", rutas.PST_LLM_DIR / "llm_trader.py")
lt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lt)

# magic -> nombre de los EAs de MT5 (MT5/README.md; mismo mapa que PST_Core/engine/mt5_sync.py)
EAS = {100500: "H5 Bollinger+RSI", 100700: "H12 GapFill", 101000: "H20 RelativeStrength", 101100: "H25 VWAP+ADX",
       101300: "H28b MACD", 101800: "H21 LiquiditySweep", 101801: "H21 LiquiditySweep", 101900: "H41 Donchian AAPL",
       101901: "H41 Donchian TSLA", 101902: "H42 GapAndGo", 102001: "H45 Ichimoku GER40", 102003: "H45 Ichimoku CADJPY"}
RAZON = {0: ("✋", "Manual"), 1: ("📱", "Móvil"), 2: ("🌐", "Web"), 3: ("🤖", "Decisión del agente/EA"),
         4: ("🛑", "Stop loss"), 5: ("🎯", "Take profit"), 6: ("⚠️", "Stop-out del broker")}

BOTONES = [["📈 Abiertas", "📋 Cerradas hoy"], ["💰 Ganancias hoy", "📊 General"], ["🤖 Agentes", "💼 Cuenta"]]
INFORMES = {"📈 Abiertas": "abiertas", "📋 Cerradas hoy": "cerradas", "💰 Ganancias hoy": "hoy",
            "📊 General": "general", "🤖 Agentes": "agentes", "💼 Cuenta": "cuenta"}


# ── API de Telegram ──────────────────────────────────────────────────────────
def api(metodo, espera=15, **kw):
    token, _ = lt._env_telegram()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{metodo}", data=json.dumps(kw).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=espera) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:             # sin e.url: lleva el token
        raise RuntimeError(f"HTTP {e.code} {e.reason}") from None
    except Exception as e:
        raise RuntimeError(type(e).__name__) from None


def enviar(texto, teclado_inline=None, teclado_fijo=False):
    _, chat = lt._env_telegram()
    p = {"chat_id": chat, "text": texto[:4000], "parse_mode": "HTML", "disable_web_page_preview": True,
         "disable_notification": 0 <= datetime.now().hour < 8}
    if teclado_fijo:
        p["reply_markup"] = {"keyboard": BOTONES, "resize_keyboard": True, "is_persistent": True}
    elif teclado_inline:
        p["reply_markup"] = {"inline_keyboard": teclado_inline}
    return api("sendMessage", **p)


def inline_actualizar(clave):
    return [[{"text": "🔄 Actualizar", "callback_data": f"r:{clave}"}]]


def teclado_de(ctx, clave):
    """Botones de un informe. En Agentes, un boton de excluir por cada agente activo."""
    if clave != "agentes":
        return inline_actualizar(clave)
    botones = [{"text": f"⛔ {a['symbol'].replace('.cash', '')}", "callback_data": f"x:{a['symbol']}"} for _, a in agentes_activos(ctx)]
    cuadricula = [botones[i:i + 3] for i in range(0, len(botones), 3)]      # 3 por fila: compacto en el movil
    return cuadricula + inline_actualizar(clave)


# ── Datos de MT5 ─────────────────────────────────────────────────────────────
class Ctx:
    """Lo que hace falta para interpretar las horas del broker y los magic."""
    def __init__(self, mt5):
        self.mt5 = mt5
        t = mt5.symbol_info_tick("BTCUSD") or mt5.symbol_info_tick("EURUSD")
        self.off = round((t.time - time.time()) / 1800) * 1800 if t else 0   # hora broker - hora local
        self.ahora_broker = int(time.time() + self.off)
        self.agentes = {}
        try:
            for a in json.loads(REGISTRO.read_text(encoding="utf-8-sig")):
                suf = "" if a["nombre"] == "Agente_LLM" else f"_{a['nombre']}"
                self.agentes[int(a["magic"])] = {"nombre": a["nombre"], "symbol": a["symbol"],
                                                 "db": DATA / f"llm_trader{suf}.db"}
        except Exception:
            pass

    def local(self, t):
        return datetime.fromtimestamp(t - self.off)

    def es_hoy(self, t):
        return self.local(t).date() == datetime.now().date()

    def origen(self, magic, comentario=""):
        if magic in self.agentes:
            return "🤖", self.agentes[magic]["nombre"].replace("Agente_", "Agente ")
        if magic in EAS:
            return "⚙️", EAS[magic]
        if magic == 0:
            return "✋", "Manual"
        return "⚙️", (comentario or f"EA {magic}")[:24]


def dg(mt5, simbolo):
    i = mt5.symbol_info(simbolo)
    return i.digits if i else 5


def cerradas(ctx, dias):
    """Posiciones ya cerradas (por position_id) con su resultado, de los ultimos `dias`."""
    mt5 = ctx.mt5
    deals = mt5.history_deals_get(datetime.now() - timedelta(days=dias), datetime.now() + timedelta(days=1)) or []
    pids = {d.position_id for d in deals if d.entry in (1, 2, 3) and d.type in (0, 1) and d.position_id}
    abiertas = {p.ticket for p in (mt5.positions_get() or [])}
    out = []
    for pid in pids - abiertas:
        ds = mt5.history_deals_get(position=pid) or []
        ent = [d for d in ds if d.entry == 0]
        sal = [d for d in ds if d.entry in (1, 2, 3)]
        if not ent or not sal:
            continue
        e, s = ent[0], sal[-1]
        out.append({"pid": pid, "symbol": e.symbol, "side": "COMPRA" if e.type == 0 else "VENTA", "vol": e.volume,
                    "p_in": e.price, "p_out": s.price, "t_in": e.time, "t_out": s.time, "magic": e.magic,
                    "bruto": sum(d.profit for d in ds), "com": sum(d.commission + getattr(d, "fee", 0.0) for d in ds),
                    "swap": sum(d.swap for d in ds), "neto": sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0.0) for d in ds),
                    "reason": s.reason, "cmt_out": s.comment or "", "cmt_in": e.comment or ""})
    return sorted(out, key=lambda x: x["t_out"])


def quien_cerro(ctx, c):
    """(icono, texto, razonamiento|None): quien cerro y, si fue un agente o el revisor, por que."""
    if c["reason"] == 3:
        if "REVISOR" in c["cmt_out"].upper():
            return "🛡️", "Revisor de posiciones", razon_revisor(c["pid"])
        if c["magic"] in ctx.agentes:
            return "🤖", f"Decisión de {ctx.agentes[c['magic']]['nombre']}", razon_agente(ctx, c)
        return "⚙️", "Cierre del propio EA", None
    return RAZON.get(c["reason"], ("❔", f"Razón {c['reason']}")) + (None,)


def razon_agente(ctx, c):
    db = ctx.agentes.get(c["magic"], {}).get("db")
    if not db or not db.is_file():
        return None
    con = sqlite3.connect(db)
    try:
        f = con.execute("SELECT reason FROM decisions WHERE ticket=? AND action='close' AND executed=1 "
                        "ORDER BY id DESC LIMIT 1", (c["pid"],)).fetchone()
    finally:
        con.close()
    return f[0] if f else None


def razon_revisor(pid):
    db = DATA / "llm_revisor.db"
    if not db.is_file():
        return None
    con = sqlite3.connect(db)
    try:
        f = con.execute("SELECT reason, invalidated_by FROM revisions WHERE ticket=? AND closed=1 ORDER BY id DESC LIMIT 1",
                        (pid,)).fetchone()
    finally:
        con.close()
    return f"{f[0]} (invalidada por: {f[1]})" if f else None


def dur(seg):
    seg = int(max(seg, 0))
    h, m = seg // 3600, (seg % 3600) // 60
    return f"{h} h {m:02d} min" if h else f"{m} min"


def eur(v):
    return f"{v:+,.2f} €"


# ── Tarjeta de cierre ────────────────────────────────────────────────────────
def tarjeta_cierre(ctx, c):
    mt5 = ctx.mt5
    d = dg(mt5, c["symbol"])
    ic_o, nom_o = ctx.origen(c["magic"], c["cmt_in"])
    ic_c, txt_c, razon = quien_cerro(ctx, c)
    sl = tp = None
    for o in (mt5.history_orders_get(position=c["pid"]) or []):
        if o.sl or o.tp:
            sl, tp = o.sl or None, o.tp or None
            break
    r_txt = ""
    if sl:
        riesgo = abs(mt5.order_calc_profit(mt5.ORDER_TYPE_BUY if c["side"] == "COMPRA" else mt5.ORDER_TYPE_SELL,
                                           c["symbol"], c["vol"], c["p_in"], sl) or 0)
        if riesgo:
            r_txt = f" · <b>{c['neto'] / riesgo:+.2f} R</b>"
    mov = (c["p_out"] - c["p_in"]) / c["p_in"] * 100 * (1 if c["side"] == "COMPRA" else -1)
    icono = "✅" if c["neto"] >= 0 else "🔻"
    hoy = [x for x in cerradas(ctx, 2) if ctx.es_hoy(x["t_out"])]
    msg = (f"{icono} <b>POSICIÓN CERRADA</b> · <b>{_h.escape(c['symbol'])}</b>\n"
           f"<i>{c['side'].lower()} #{c['pid']} · {c['vol']:g} lotes · {ic_o} {_h.escape(nom_o)}</i>\n"
           f"━━━━━━━━━━━━━━━\n"
           f"{ic_c} <b>{_h.escape(txt_c)}</b>\n"
           f"▫️ Entrada  <code>{c['p_in']:.{d}f}</code>  ({ctx.local(c['t_in']):%d/%m %H:%M})\n"
           f"▪️ Salida     <code>{c['p_out']:.{d}f}</code>  ({ctx.local(c['t_out']):%d/%m %H:%M})\n"
           + (f"🛑 Stop <code>{sl:.{d}f}</code>" if sl else "") + (f" · 🎯 Objetivo <code>{tp:.{d}f}</code>\n" if tp else ("\n" if sl else ""))
           + f"⏱ Duración {dur(c['t_out'] - c['t_in'])} · movimiento {mov:+.2f}%\n"
           f"━━━━━━━━━━━━━━━\n"
           f"💶 Resultado: <b>{eur(c['neto'])}</b>{r_txt}\n"
           f"<i>bruto {eur(c['bruto'])} · comisión {eur(c['com'])}" + (f" · swap {eur(c['swap'])}" if c["swap"] else "") + "</i>\n")
    if razon:
        msg += f"💬 <i>{_h.escape(razon[:500])}</i>\n"
    msg += f"📅 Hoy: <b>{eur(sum(x['neto'] for x in hoy))}</b> en {len(hoy)} cerradas · 🕐 {datetime.now():%H:%M}"
    return msg


# ── Informes ─────────────────────────────────────────────────────────────────
def r_abiertas(ctx):
    mt5 = ctx.mt5
    pos = mt5.positions_get() or []
    cab = "📈 <b>Posiciones abiertas</b>"
    if not pos:
        return f"{cab}\n\n<i>No hay ninguna ahora mismo.</i>"
    pos = sorted(pos, key=lambda p: -(p.profit + p.swap))
    tot = sum(p.profit + p.swap for p in pos)
    lin = [f"{cab} · {len(pos)} · flotante <b>{eur(tot)}</b>\n"]
    for p in pos[:15]:
        d = dg(mt5, p.symbol)
        ic, nom = ctx.origen(p.magic, p.comment)
        sl = f"<code>{p.sl:.{d}f}</code>" if p.sl else "—"
        tp = f"<code>{p.tp:.{d}f}</code>" if p.tp else "—"
        lin.append(f"{'🟢 COMPRA' if p.type == 0 else '🔴 VENTA'} <b>{_h.escape(p.symbol)}</b> · {p.volume:g} lotes · {ic} {_h.escape(nom)}\n"
                   f"   <code>{p.price_open:.{d}f}</code> → <code>{p.price_current:.{d}f}</code> · ⏱ {dur(ctx.ahora_broker - p.time)}\n"
                   f"   🛑 {sl} · 🎯 {tp} · 💶 <b>{eur(p.profit + p.swap)}</b>")
    if len(pos) > 15:
        lin.append(f"<i>… y {len(pos) - 15} más</i>")
    return "\n".join(lin)


def r_cerradas(ctx):
    hoy = sorted([c for c in cerradas(ctx, 2) if ctx.es_hoy(c["t_out"])], key=lambda c: -c["t_out"])
    cab = "📋 <b>Cerradas hoy</b>"
    if not hoy:
        return f"{cab}\n\n<i>Todavía no se ha cerrado ninguna hoy.</i>"
    ganadas = sum(1 for c in hoy if c["neto"] > 0)
    lin = [f"{cab} · {len(hoy)} · ✅ {ganadas} · 🔻 {len(hoy) - ganadas} · <b>{eur(sum(c['neto'] for c in hoy))}</b>\n"]
    for c in hoy[:20]:
        ic_c, _, _ = quien_cerro(ctx, c)
        ic_o, nom = ctx.origen(c["magic"], c["cmt_in"])
        lin.append(f"{'✅' if c['neto'] >= 0 else '🔻'} {ctx.local(c['t_out']):%H:%M} <b>{_h.escape(c['symbol'])}</b> "
                   f"{c['side'].lower()} · <b>{eur(c['neto'])}</b> · {ic_c} {ic_o} {_h.escape(nom)}")
    if len(hoy) > 20:
        lin.append(f"<i>… y {len(hoy) - 20} más</i>")
    return "\n".join(lin)


def _grupo(cs):
    g = [c["neto"] for c in cs if c["neto"] > 0]
    p = [c["neto"] for c in cs if c["neto"] <= 0]
    return {"n": len(cs), "gan": len(g), "neto": sum(c["neto"] for c in cs), "com": sum(c["com"] for c in cs),
            "pf": (sum(g) / -sum(p)) if p and sum(p) < 0 else None}


def r_hoy(ctx):
    mt5 = ctx.mt5
    cuenta = mt5.account_info()
    hoy = [c for c in cerradas(ctx, 2) if ctx.es_hoy(c["t_out"])]
    flot = sum(p.profit + p.swap for p in (mt5.positions_get() or []))
    flot_ag = sum(p.profit + p.swap for p in (mt5.positions_get() or []) if p.magic in ctx.agentes)
    ag = [c for c in hoy if c["magic"] in ctx.agentes]
    ea = [c for c in hoy if c["magic"] not in ctx.agentes and c["magic"] != 0]
    ma = [c for c in hoy if c["magic"] == 0]
    total = sum(c["neto"] for c in hoy)
    lin = [f"💰 <b>Ganancias de hoy</b> · {datetime.now():%d/%m}\n"]
    for nom, cs, fl in (("🤖 Agentes LLM", ag, flot_ag), ("⚙️ EAs de MT5", ea, flot - flot_ag), ("✋ Manual", ma, 0.0)):
        if not cs and not fl:
            continue
        g = _grupo(cs)
        lin.append(f"{nom}: <b>{eur(g['neto'])}</b> en {g['n']} cerradas" + (f" ({g['gan']}✅ {g['n'] - g['gan']}🔻)" if g["n"] else "")
                   + (f" · flotante {eur(fl)}" if fl else ""))
    lin.append("━━━━━━━━━━━━━━━")
    lin.append(f"Realizado: <b>{eur(total)}</b> · flotante {eur(flot)}")
    lin.append(f"<b>Total del día: {eur(total + flot)}</b>")
    lin.append(f"<i>comisiones de hoy: {eur(sum(c['com'] for c in hoy))}</i>")
    if hoy:
        mejor, peor = max(hoy, key=lambda c: c["neto"]), min(hoy, key=lambda c: c["neto"])
        lin.append(f"🏆 Mejor: {_h.escape(mejor['symbol'])} {eur(mejor['neto'])} · 📉 Peor: {_h.escape(peor['symbol'])} {eur(peor['neto'])}")
    lin.append(f"💼 Balance {cuenta.balance:,.2f} · equity {cuenta.equity:,.2f}")
    return "\n".join(lin)


def r_general(ctx):
    mt5 = ctx.mt5
    cuenta = mt5.account_info()
    todas = cerradas(ctx, 120)
    cab = "📊 <b>Resumen general</b>"
    if not todas:
        return f"{cab}\n\n<i>Sin operaciones cerradas todavía.</i>"
    desde = ctx.local(min(c["t_in"] for c in todas))
    g = _grupo(todas)
    lin = [f"{cab} · desde {desde:%d/%m}\n",
           f"Cerradas <b>{g['n']}</b> · ganadas {g['gan']} (<b>{g['gan'] / g['n'] * 100:.0f}%</b>)"
           + (f" · factor de beneficio <b>{g['pf']:.2f}</b>" if g["pf"] else ""),
           f"Neto <b>{eur(g['neto'])}</b> · comisiones {eur(g['com'])}",
           f"💼 Balance {cuenta.balance:,.2f} ({cuenta.balance - INICIAL:+,.2f} sobre {INICIAL:,.0f}) · equity {cuenta.equity:,.2f}\n",
           "<b>Por origen</b>"]
    por = {}
    for c in todas:
        ic, nom = ctx.origen(c["magic"], c["cmt_in"])
        por.setdefault((ic, nom), []).append(c)
    for (ic, nom), cs in sorted(por.items(), key=lambda kv: -sum(c["neto"] for c in kv[1])):
        gg = _grupo(cs)
        lin.append(f"{ic} {_h.escape(nom)}: {gg['n']} ops · {gg['gan'] / gg['n'] * 100:.0f}% · <b>{eur(gg['neto'])}</b>"
                   + (f" · PF {gg['pf']:.2f}" if gg["pf"] else ""))
    return "\n".join(lin)


def agentes_activos(ctx):
    """[(magic, agente)] de los agentes con el simbolo activo en la BBDD, en el orden del informe."""
    import registro
    activos = {a["symbol"] for a in registro.estado() if a["activo"]}
    return [(magic, a) for magic, a in sorted(ctx.agentes.items()) if a["symbol"] in activos]


def r_agentes(ctx):
    vivos = {}
    for p in psutil.process_iter(["pid", "cmdline"]):
        c = p.info["cmdline"] or []
        if any(a.endswith("llm_trader.py") for a in c):
            vivos["Agente_LLM" if "--nombre" not in c else c[c.index("--nombre") + 1]] = p.info["pid"]
        elif any(a.endswith("llm_revisor.py") for a in c):
            vivos["__revisor"] = p.info["pid"]
    lin = ["🤖 <b>Agentes activos</b> · toca ⛔ para excluir\n"]
    tot_l = tot_t = 0
    pos = ctx.mt5.positions_get() or []
    activos = agentes_activos(ctx)
    if not activos:
        lin.append("Ningún agente activo.")
    for magic, a in activos:
        llam = tok = 0
        ult = "—"
        if a["db"].is_file():
            con = sqlite3.connect(a["db"])
            llam, tok = con.execute("SELECT COUNT(*), COALESCE(SUM(COALESCE(tokens_in,0)+COALESCE(tokens_out,0)+"
                                    "COALESCE(tokens_cache_read,0)+COALESCE(tokens_cache_write,0)),0) FROM decisions WHERE llm_called=1").fetchone()
            u = con.execute("SELECT ts, action FROM decisions WHERE llm_called=1 ORDER BY id DESC LIMIT 1").fetchone()
            ult = f"{u[1].upper()} {u[0][11:16]}" if u else "—"
            con.close()
        tot_l += llam
        tot_t += tok
        abiertas = sum(1 for p in pos if p.magic == magic)
        lin.append(f"{'🟢' if a['nombre'] in vivos else '🔴'} <b>{_h.escape(a['symbol'])}</b> · {llam} llamadas · última {ult}"
                   + (f" · 📈 {abiertas} abierta" if abiertas else ""))
    rv = "—"
    rdb = DATA / "llm_revisor.db"
    if rdb.is_file():
        con = sqlite3.connect(rdb)
        n, cl = con.execute("SELECT COUNT(*), COALESCE(SUM(closed),0) FROM revisions").fetchone()
        con.close()
        rv = f"{n} revisiones · {cl} cierres"
    lin.append(f"{'🟢' if '__revisor' in vivos else '🔴'} <b>Revisor</b> · {rv}")
    lin.append(f"\nTokens acumulados (agentes): {tot_t:,}")
    try:
        q = json.loads((Path.home() / ".habblaud" / "usage" / ".claude.json").read_text(encoding="utf-8"))
        lin.append(f"📊 Cuota Claude: ventana 5h <b>{q['five_hour']['utilization']}%</b> · semanal <b>{q['seven_day']['utilization']}%</b>")
    except Exception:
        pass
    return "\n".join(lin)


def r_cuenta(ctx):
    mt5 = ctx.mt5
    c = mt5.account_info()
    hoy = [x for x in cerradas(ctx, 2) if ctx.es_hoy(x["t_out"])]
    dia = sum(x["neto"] for x in hoy) + c.profit
    total = c.equity - INICIAL
    return (f"💼 <b>Cuenta</b> · {_h.escape(c.server)}\n\n"
            f"Balance <b>{c.balance:,.2f} €</b> · equity <b>{c.equity:,.2f} €</b>\n"
            f"Flotante {eur(c.profit)} · margen usado {c.margin:,.2f} · libre {c.margin_free:,.2f}"
            + (f" · nivel {c.margin_level:,.0f}%" if c.margin else "") + "\n━━━━━━━━━━━━━━━\n"
            f"<b>Límites de la prueba</b>\n"
            f"📅 Resultado del día: <b>{eur(dia)}</b> · límite de pérdida diaria −{LIMITE_DIA:,.0f} € (usado {max(0.0, -dia) / LIMITE_DIA * 100:.0f}%)\n"
            f"📉 Sobre el inicial ({INICIAL:,.0f} €): <b>{eur(total)}</b> · límite de pérdida total −{LIMITE_TOTAL:,.0f} € (usado {max(0.0, -total) / LIMITE_TOTAL * 100:.0f}%)")


GENERADORES = {"abiertas": r_abiertas, "cerradas": r_cerradas, "hoy": r_hoy, "general": r_general,
               "agentes": r_agentes, "cuenta": r_cuenta}


def informe(ctx, clave):
    try:
        return GENERADORES[clave](ctx)
    except Exception as e:
        return f"❌ No pude generar el informe ({_h.escape(type(e).__name__)}: {_h.escape(str(e)[:150])})"


AYUDA = ("🤖 <b>Panel de la flota</b>\n\nUsa los botones de abajo:\n"
         "📈 <b>Abiertas</b> — posiciones abiertas ahora, con flotante\n"
         "📋 <b>Cerradas hoy</b> — lo cerrado hoy y su resultado\n"
         "💰 <b>Ganancias hoy</b> — realizado + flotante, por origen\n"
         "📊 <b>General</b> — todo el historial por agente y EA\n"
         "🤖 <b>Agentes</b> — los activos, gasto y cuota de Claude, con botón para excluir\n"
         "💼 <b>Cuenta</b> — balance, margen y límites de la prueba\n\n"
         "Además te aviso de cada posición que se cierra, con sus datos.\n<i>Solo lectura: desde aquí no se puede operar.</i>")


# ── Avisos de cierre ─────────────────────────────────────────────────────────
def bd():
    con = sqlite3.connect(DB_FILE, timeout=15)
    con.execute("CREATE TABLE IF NOT EXISTS avisadas (position_id INTEGER PRIMARY KEY, enviada TEXT, enviada_ok INTEGER)")
    return con


def vigilar_cierres(ctx, con):
    vistas = {r[0] for r in con.execute("SELECT position_id FROM avisadas")}
    nuevas = [c for c in cerradas(ctx, 3) if c["pid"] not in vistas]
    if not vistas:                       # primera vez: se siembra el historico sin enviarlo
        con.executemany("INSERT OR IGNORE INTO avisadas VALUES (?,?,0)", [(c["pid"], datetime.now().isoformat()) for c in nuevas])
        con.commit()
        print(f"[bot] historico sembrado: {len(nuevas)} cierres anteriores no se avisan", flush=True)
        return
    for c in nuevas:
        try:
            enviar(tarjeta_cierre(ctx, c), inline_actualizar("hoy"))
            ok = 1
        except Exception as e:
            ok = 0
            print(f"[bot] aviso de cierre #{c['pid']} fallido: {e}", flush=True)
        if ok:       # si falla se reintenta en la siguiente vuelta
            con.execute("INSERT OR REPLACE INTO avisadas VALUES (?,?,1)", (c["pid"], datetime.now().isoformat()))
            con.commit()
            print(f"[bot] cierre avisado: #{c['pid']} {c['symbol']} {c['neto']:+.2f}", flush=True)


# ── Bucle ────────────────────────────────────────────────────────────────────
def _rotacion_decidir(q, d, chat_ok):
    """Pulsacion 'rot:a:<id>' (aprobar) o 'rot:r:<id>' (rechazar). Quita los botones del aviso y responde."""
    import rotacion
    try:
        _, accion, pid = d.split(":")
        texto = rotacion.decidir_propuesta(int(pid), accion == "a", por="telegram")
    except (ValueError, RuntimeError) as e:
        texto = f"error: {type(e).__name__}"
    try:
        api("answerCallbackQuery", callback_query_id=q["id"], text=texto[:200])
        api("editMessageReplyMarkup", chat_id=chat_ok, message_id=q["message"]["message_id"],
            reply_markup={"inline_keyboard": []})
    except RuntimeError as e:
        print(f"[bot] error al responder la propuesta: {e}", flush=True)


def enviar_documento(nombre, contenido, caption=""):
    """Envia un fichero de texto (multipart): para textos largos que no caben en un mensaje (4096 caracteres)."""
    import uuid
    token, chat = lt._env_telegram()
    limite = "----pst" + uuid.uuid4().hex
    nl = chr(13) + chr(10)                   # CRLF del formato multipart
    partes = []
    for k, v in (("chat_id", str(chat)), ("caption", caption[:1000])):
        partes.append(f'--{limite}{nl}Content-Disposition: form-data; name="{k}"{nl}{nl}{v}{nl}'.encode("utf-8"))
    partes.append(f'--{limite}{nl}Content-Disposition: form-data; name="document"; filename="{nombre}"{nl}'
                  f'Content-Type: text/plain; charset=utf-8{nl}{nl}'.encode("utf-8") + contenido.encode("utf-8") + nl.encode())
    partes.append(f"--{limite}--{nl}".encode("utf-8"))
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendDocument", data=b"".join(partes),
                                 headers={"Content-Type": f"multipart/form-data; boundary={limite}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:             # sin e.url: lleva el token
        raise RuntimeError(f"HTTP {e.code} {e.reason}") from None


def _prompt_propuesta(q, d, chat_ok):
    """'pr:a:<id>' aprobar y aplicar, 'pr:r:<id>' rechazar, 'pr:v:<id>' ver el texto completo (ver prompts.py)."""
    import prompts
    try:
        _, accion, pid = d.split(":")
        pid = int(pid)
    except ValueError:
        return
    if accion == "v":
        p = prompts.leer_propuesta(pid)
        try:
            api("answerCallbackQuery", callback_query_id=q["id"], text="Enviando el texto completo…" if p else "No existe")
            if p:
                enviar_documento(f"propuesta_{pid}_{p['target'].replace('.', '_')}.txt", prompts.documento(p),
                                 f"Propuesta #{pid} · {p['target']} · estado: {p['state']}")
        except RuntimeError as e:
            print(f"[bot] error al enviar la propuesta completa: {e}", flush=True)
        return
    try:
        texto = prompts.decidir(pid, accion == "a", por="telegram")
    except Exception as e:
        texto = f"error: {type(e).__name__}"
    try:
        api("answerCallbackQuery", callback_query_id=q["id"], text=texto[:200])
        api("editMessageReplyMarkup", chat_id=chat_ok, message_id=q["message"]["message_id"],
            reply_markup={"inline_keyboard": []})
        enviar(("✅ " if accion == "a" and texto.split(": ", 1)[-1].startswith("aplicada") else "ℹ️ ") + _h.escape(texto))
    except RuntimeError as e:
        print(f"[bot] error al responder la propuesta de criterio: {e}", flush=True)


def _excluir_desde_boton(symbol):
    """Excluye el agente en un hilo aparte: desactivar espera a que el proceso pare (hasta 90 s)."""
    import registro

    def trabajo():
        try:
            registro.desactivar(symbol)
            enviar(f"⛔ <b>{_h.escape(symbol)}</b> excluido: su agente se ha parado. "
                   "Para volver a activarlo, hazlo a mano desde la app.")
        except Exception as e:
            enviar(f"No se pudo excluir {_h.escape(symbol)}: {_h.escape(type(e).__name__)} {_h.escape(str(e)[:150])}")

    threading.Thread(target=trabajo, daemon=True).start()


def procesar(ctx, u, chat_ok):
    if "callback_query" in u:
        q = u["callback_query"]
        if str(q["message"]["chat"]["id"]) != str(chat_ok):
            return
        d = q.get("data", "")
        if d.startswith("rot:"):                   # propuesta de rotacion: aprobar o rechazar (ver rotacion.py)
            _rotacion_decidir(q, d, chat_ok)
            return
        if d.startswith("pr:"):                    # propuesta de criterio: aprobar, rechazar o ver (ver prompts.py)
            _prompt_propuesta(q, d, chat_ok)
            return
        try:
            api("answerCallbackQuery", callback_query_id=q["id"])
        except RuntimeError:
            pass
        if d.startswith("x:"):                       # boton Excluir de un agente (ver teclado_de)
            _excluir_desde_boton(d[2:])
            return
        if d.startswith("r:") and d[2:] in GENERADORES:
            try:
                api("editMessageText", chat_id=chat_ok, message_id=q["message"]["message_id"], text=informe(ctx, d[2:])[:4000],
                    parse_mode="HTML", disable_web_page_preview=True, reply_markup={"inline_keyboard": teclado_de(ctx, d[2:])})
            except RuntimeError as e:
                if "400" not in str(e):          # 400 = "message is not modified": el informe no cambio
                    print(f"[bot] error al actualizar: {e}", flush=True)
        return
    m = u.get("message")
    if not m or str(m["chat"]["id"]) != str(chat_ok):
        return                                   # cualquier otro chat se ignora
    t = (m.get("text") or "").strip()
    if t in ("/start", "/menu", "/ayuda", "/help"):
        enviar(AYUDA, teclado_fijo=True)
    elif t.lower().split(" ")[0] in ("/quitar", "quitar", "/poner", "poner", "/excluir", "excluir"):
        partes = t.split()
        orden = partes[0].lstrip("/").lower()
        if len(partes) != 2:
            enviar(f"Uso: /{orden} SIMBOLO (ej. /{orden} EURUSD).\n"
                   "quitar: lo saca con penalizacion (no bloquea). poner: propone meterlo. "
                   "excluir: lo saca y no vuelve hasta que lo actives a mano.")
        elif orden == "excluir":
            import registro
            try:
                registro.desactivar(partes[1].upper())
                enviar(f"{partes[1].upper()} excluido: la rotacion no lo volvera a poner.")
            except (ValueError, RuntimeError) as e:
                enviar(f"No se pudo excluir: {e}")
        else:
            import rotacion
            try:
                fn = rotacion.quitar_simbolo if orden == "quitar" else rotacion.poner_simbolo
                enviar(fn(partes[1].upper(), por="Telegram"))
            except RuntimeError as e:
                enviar(f"No se pudo {orden}: {e}")
    elif t in INFORMES:
        clave = INFORMES[t]
        enviar(informe(ctx, clave), teclado_de(ctx, clave))
    elif t.startswith("/") and t[1:] in GENERADORES:
        enviar(informe(ctx, t[1:]), teclado_de(ctx, t[1:]))


def main():
    ap = argparse.ArgumentParser(description="Servicio de Telegram de la flota (solo lectura)")
    ap.add_argument("--probar", action="store_true", help="enviar el menu y los informes una vez y salir")
    a = ap.parse_args()

    import MetaTrader5 as mt5
    if not mt5.initialize():
        print(f"MT5 no disponible: {mt5.last_error()}", file=sys.stderr)
        return 1
    _, chat = lt._env_telegram()
    if not chat:
        print("Falta TELEGRAM_CHAT_ID / TELEGRAM_BOT_TOKEN en .env", file=sys.stderr)
        return 1
    con = bd()
    try:
        if a.probar:
            ctx = Ctx(mt5)
            enviar(AYUDA, teclado_fijo=True)
            for clave in GENERADORES:
                enviar(informe(ctx, clave), teclado_de(ctx, clave))
            ult = cerradas(ctx, 3)
            if ult:
                enviar("🔎 <b>Así se verá el aviso de cierre</b> (última cerrada)\n\n" + tarjeta_cierre(ctx, ult[-1]))
            print("enviado")
            return 0
        try:
            r = api("getUpdates", offset=-1, timeout=0)          # descarta lo pendiente de cuando no escuchaba
            offset = (r["result"][-1]["update_id"] + 1) if r.get("result") else 0
        except RuntimeError as e:
            print(f"[bot] no pude leer las actualizaciones: {e}", file=sys.stderr)
            return 1
        print("[bot] escuchando · solo lectura · parar: crear llm_telegram.STOP", flush=True)
        enviar("✅ <b>Panel activo</b>. Usa los botones de abajo.", teclado_fijo=True)
        while not STOP_FILE.exists():
            lt.mantener_despierto()
            ctx = Ctx(mt5)
            try:
                vigilar_cierres(ctx, con)
            except Exception as e:
                print(f"[bot] error vigilando cierres: {e}", flush=True)
            try:
                r = api("getUpdates", espera=25, offset=offset, timeout=15, allowed_updates=["message", "callback_query"])
            except RuntimeError as e:
                print(f"[bot] getUpdates: {e}" + (" · ¿otro proceso escucha al mismo bot?" if "409" in str(e) else ""), flush=True)
                time.sleep(30 if "409" in str(e) else 5)
                continue
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                try:
                    procesar(ctx, u, chat)
                except Exception as e:
                    print(f"[bot] error procesando una actualizacion: {type(e).__name__}: {e}", flush=True)
        print("[bot] fichero STOP detectado: parado.", flush=True)
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
