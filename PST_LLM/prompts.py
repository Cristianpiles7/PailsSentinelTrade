"""
Criterio de trading de cada agente LLM, guardado en BBDD y editable (a mano o por propuesta aprobada).

El prompt de un agente tiene dos partes:
  - FIJA (en llm_trader.py, solo cambia con codigo): formato de respuesta, costes, reglas de cierre y campos
    del estado. Los limites de riesgo los aplica el script, no el texto.
  - CRITERIO (aqui, uno por simbolo): como decide entrar o quedarse fuera. Es lo unico que puede tocar
    el agente de mejora (llm_mejora_prompt.py) y lo que editas desde Ajustes.

Tablas (BBDD central):
  llm_criterios      historial: cada version del criterio de cada simbolo (la ultima fila es la activa).
  llm_prompt_revisiones  cada revision del agente de mejora (con o sin propuesta), para contar operaciones
                     nuevas desde la ultima.
Propuestas: tabla proposals, kind 'prompt_simbolo'. No caducan. Una propuesta nueva del mismo simbolo deja
la anterior como 'sustituida'. Cada propuesta se escribe sobre el criterio activo (base_version): si al
aprobarla el criterio ya cambio (editado a mano u otra aprobada), queda 'obsoleta' y no se aplica.

Principio: leer el criterio NUNCA puede parar a un agente. Si la BBDD falla, se usa el criterio por defecto.
"""
import difflib
import hashlib
import json
import sqlite3
import urllib.request
from datetime import datetime

import registro
import rutas

KIND = "prompt_simbolo"
CONFIG = rutas.DATOS / ".agents" / "data" / "mejora_prompt_config.json"
POR_DEFECTO = {"activo": True, "min_operaciones": 20, "modelo": "sonnet", "esfuerzo": "medium",
               "cadencia_min": 60, "max_caracteres": 3000, "max_operaciones_contexto": 60,
               "validar": True, "validacion_n": 20}
TIPOS = {"activo": bool, "min_operaciones": int, "modelo": str, "esfuerzo": str, "cadencia_min": int,
         "max_caracteres": int, "max_operaciones_contexto": int, "validar": bool, "validacion_n": int}

CRITERIO_DEFECTO = """- Por defecto, quédate fuera (hold). La mayoría de las velas no tienen ventaja: entra solo con una razón clara y concreta.
- Entra solo en una estructura concreta (un nivel o una lectura de indicador con nombre) del marco que elijas, y di en 'Riesgo:' qué invalidaría la idea.
- Usa los marcos altos para la dirección y los bajos para el momento de entrada.
- No operes contra los marcos altos sin una razón clara."""


# ── Ajustes ──────────────────────────────────────────────────────────────────
def ajustes():
    a = dict(POR_DEFECTO)
    try:
        guardado = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, ValueError):
        return a
    for k, v in guardado.items():
        if k in TIPOS:
            try:
                a[k] = TIPOS[k](v)
            except (TypeError, ValueError):
                pass
    return a


def guardar_ajustes(cambios):
    a = ajustes()
    for k, v in cambios.items():
        if v is None:
            continue
        if k not in TIPOS:
            raise ValueError(f"ajuste desconocido: {k}")
        a[k] = TIPOS[k](v)
    if not 20 <= a["min_operaciones"] <= 500:
        raise ValueError("min_operaciones debe estar entre 20 y 500 (con menos, cualquier patron es ruido: plan v3 fase 5)")
    if a["modelo"] not in ("haiku", "sonnet", "opus"):
        raise ValueError("modelo debe ser haiku, sonnet u opus")
    if a["esfuerzo"] not in ("low", "medium", "high"):
        raise ValueError("esfuerzo debe ser low, medium o high")
    if not 10 <= a["cadencia_min"] <= 1440:
        raise ValueError("cadencia_min debe estar entre 10 y 1440 minutos")
    if not 500 <= a["max_caracteres"] <= 10000:
        raise ValueError("max_caracteres debe estar entre 500 y 10000")
    if not 10 <= a["max_operaciones_contexto"] <= 300:
        raise ValueError("max_operaciones_contexto debe estar entre 10 y 300")
    if not 5 <= a["validacion_n"] <= 100:
        raise ValueError("validacion_n debe estar entre 5 y 100")
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(a, indent=2), encoding="utf-8")
    return a


# ── Criterio por simbolo ─────────────────────────────────────────────────────
def _con():
    con = registro.central()
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS llm_criterios (
        id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, version TEXT NOT NULL, texto TEXT NOT NULL,
        activado TEXT NOT NULL, por TEXT, proposal_id INTEGER)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_llm_criterios_sym ON llm_criterios(symbol, id)")
    con.execute("""CREATE TABLE IF NOT EXISTS llm_prompt_revisiones (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, version TEXT, operaciones INTEGER,
        resultado TEXT, proposal_id INTEGER, tickets TEXT)""")
    try:
        con.execute("ALTER TABLE llm_prompt_revisiones ADD COLUMN tickets TEXT")   # tablas creadas antes de esto
    except sqlite3.OperationalError:
        pass
    return con


def version_de(texto):
    return hashlib.sha1(texto.encode("utf-8")).hexdigest()[:10]


def activo(symbol):
    """{'texto', 'version', 'activado', 'por', 'defecto'}. El por defecto no tiene fila: activado None."""
    con = _con()
    try:
        f = con.execute("SELECT * FROM llm_criterios WHERE symbol=? ORDER BY id DESC LIMIT 1", (symbol,)).fetchone()
    finally:
        con.close()
    if f is None:
        return {"texto": CRITERIO_DEFECTO, "version": version_de(CRITERIO_DEFECTO), "activado": None,
                "por": "defecto", "defecto": True, "proposal_id": None}
    return {"texto": f["texto"], "version": f["version"], "activado": f["activado"], "por": f["por"],
            "defecto": False, "proposal_id": f["proposal_id"]}


def texto_seguro(symbol):
    """Lo que usa el agente en cada llamada. Nunca lanza."""
    try:
        return activo(symbol)["texto"]
    except Exception as e:
        print(f"[prompts] no se pudo leer el criterio de {symbol}, uso el de por defecto: {e}", flush=True)
        return CRITERIO_DEFECTO


def historial(symbol, limite=30):
    con = _con()
    try:
        return [dict(f) for f in con.execute(
            "SELECT id, version, texto, activado, por, proposal_id FROM llm_criterios WHERE symbol=? "
            "ORDER BY id DESC LIMIT ?", (symbol, limite))]
    finally:
        con.close()


def validar(texto, max_caracteres=None):
    texto = (texto or "").replace("\r\n", "\n").strip()
    lim = max_caracteres or ajustes()["max_caracteres"]
    if not texto:
        raise ValueError("el criterio no puede estar vacío")
    if len(texto) > lim:
        raise ValueError(f"el criterio tiene {len(texto)} caracteres (máximo {lim})")
    return texto


def guardar(symbol, texto, por="usuario", proposal_id=None):
    """Activa un criterio nuevo para el simbolo. Las propuestas pendientes de ese simbolo quedan obsoletas
    (estaban escritas sobre el criterio anterior), salvo la que se esta aplicando."""
    texto = validar(texto)
    if texto == activo(symbol)["texto"]:
        return activo(symbol)
    con = _con()
    try:
        con.execute("INSERT INTO llm_criterios (symbol, version, texto, activado, por, proposal_id) VALUES (?,?,?,?,?,?)",
                    (symbol, version_de(texto), texto, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), por, proposal_id))
        con.commit()
        pendientes = con.execute("SELECT id, proposed_json FROM proposals WHERE kind=? AND target=? AND state='propuesta'"
                                 " AND id != ?", (KIND, symbol, proposal_id or -1)).fetchall()
        for p in pendientes:
            _mover(con, p["id"], "propuesta", "rechazada", "obsoleta")
            _quitar_botones(p["proposed_json"], "⚪ Obsoleta: el criterio cambió antes de decidir.")
    finally:
        con.close()
    return activo(symbol)


def restaurar_defecto(symbol, por="usuario"):
    return guardar(symbol, CRITERIO_DEFECTO, por=por)


# ── Diferencias ──────────────────────────────────────────────────────────────
def diff(actual, nuevo):
    """Lista de {'t': '=', '+' o '-', 'l': linea} con TODO el texto (para verlo entero con los cambios marcados)."""
    out = []
    for l in difflib.ndiff(actual.splitlines(), nuevo.splitlines()):
        if l.startswith("? "):
            continue
        out.append({"t": {" ": "=", "+": "+", "-": "-"}[l[0]], "l": l[2:]})
    return out


# ── Propuestas ───────────────────────────────────────────────────────────────
def _mover(con, pid, desde, hacia, por):
    sello = ", applied_at = CURRENT_TIMESTAMP" if hacia == "viva" else ""
    cur = con.execute(f"UPDATE proposals SET state=?, decided_by=?, decided_at=CURRENT_TIMESTAMP{sello} "
                      f"WHERE id=? AND state=?", (hacia, por, pid, desde))
    con.commit()
    return cur.rowcount == 1


def crear_propuesta(symbol, nuevo, resumen, motivo, evidencia, agent_run_id=None):
    """Guarda la propuesta, sustituye las pendientes del mismo simbolo y avisa por Telegram. Devuelve el id."""
    act = activo(symbol)
    nuevo = validar(nuevo)
    if nuevo == act["texto"]:
        raise ValueError("la propuesta no cambia nada")
    cambios = diff(act["texto"], nuevo)
    con = _con()
    try:
        cur = con.execute(
            "INSERT INTO proposals (agent_run_id, kind, target, current_json, proposed_json, evidence_json, rationale, state)"
            " VALUES (?,?,?,?,?,?,?, 'propuesta')",
            (agent_run_id, KIND, symbol, json.dumps({"criterio": act["texto"], "version": act["version"]}, ensure_ascii=False),
             json.dumps({"criterio": nuevo, "base_version": act["version"], "resumen": resumen, "diff": cambios},
                        ensure_ascii=False),
             json.dumps(evidencia, ensure_ascii=False), motivo))
        pid = cur.lastrowid
        con.commit()
        viejas = con.execute("SELECT id, proposed_json FROM proposals WHERE kind=? AND target=? AND state='propuesta' "
                             "AND id != ?", (KIND, symbol, pid)).fetchall()
        for p in viejas:
            _mover(con, p["id"], "propuesta", "rechazada", "sustituida")
            _quitar_botones(p["proposed_json"], f"⚪ Sustituida por la propuesta #{pid}.")
    finally:
        con.close()
    _avisar(pid)
    return pid


def leer_propuesta(pid):
    con = _con()
    try:
        f = con.execute("SELECT * FROM proposals WHERE id=? AND kind=?", (pid, KIND)).fetchone()
    finally:
        con.close()
    return dict(f) if f else None


def aplicar(pid, por="usuario"):
    """Aplica una propuesta 'aprobada'. Si el criterio cambio desde que se escribio, la deja obsoleta."""
    p = leer_propuesta(pid)
    if p is None or p["state"] != "aprobada":
        return "no está aprobada"
    prop = json.loads(p["proposed_json"] or "{}")
    if activo(p["target"])["version"] != prop.get("base_version"):
        con = _con()
        try:
            _mover(con, pid, "aprobada", "rechazada", "obsoleta")
        finally:
            con.close()
        return "obsoleta: el criterio cambió desde que se propuso; no se aplica"
    guardar(p["target"], prop["criterio"], por=f"propuesta #{pid} ({por})", proposal_id=pid)
    con = _con()
    try:
        _mover(con, pid, "aprobada", "viva", por)
    finally:
        con.close()
    return f"aplicada: nuevo criterio de {p['target']} activo"


def decidir(pid, aprobar, por="telegram"):
    """Aprobar (y aplicar) o rechazar una propuesta pendiente. Lo usan Telegram y la app."""
    p = leer_propuesta(pid)
    if p is None or p["state"] != "propuesta":
        estado = p["state"] if p else "no existe"
        return f"ya no está pendiente ({estado}{' · ' + p['decided_by'] if p and p.get('decided_by') else ''})"
    con = _con()
    try:
        ok = _mover(con, pid, "propuesta", "aprobada" if aprobar else "rechazada", por)
    finally:
        con.close()
    if not ok:
        return "ya no está pendiente"
    if not aprobar:
        return f"Rechazada: criterio de {p['target']} sin cambios"
    return f"{p['target']}: {aplicar(pid, por)}"


def revertir(pid, por="usuario"):
    """Tras pasar una propuesta 'viva' a 'revertida' (bandeja): vuelve al criterio anterior si sigue siendo el activo."""
    p = leer_propuesta(pid)
    if p is None:
        return "no existe"
    act = activo(p["target"])
    if act.get("proposal_id") != pid:
        return "el criterio ya cambió después: no se toca"
    previo = json.loads(p["current_json"] or "{}").get("criterio") or CRITERIO_DEFECTO
    guardar(p["target"], previo, por=f"revertida #{pid} ({por})")
    return "revertida: vuelve el criterio anterior"


def pendiente_de(symbol):
    con = _con()
    try:
        f = con.execute("SELECT id FROM proposals WHERE kind=? AND target=? AND state='propuesta' ORDER BY id DESC LIMIT 1",
                        (KIND, symbol)).fetchone()
    finally:
        con.close()
    return f["id"] if f else None


# ── Telegram ─────────────────────────────────────────────────────────────────
def _tg(metodo, **kw):
    import llm_trader as lt
    token, chat = lt._env_telegram()
    if not token or not chat:
        return None
    kw.setdefault("chat_id", chat)
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{metodo}", data=json.dumps(kw).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read()).get("result")


def botones(pid):
    """El bot (llm_telegram_bot.py) recibe 'pr:a|r|v:<id>'."""
    return [[{"text": "✅ Aprobar", "callback_data": f"pr:a:{pid}"}, {"text": "⛔ Rechazar", "callback_data": f"pr:r:{pid}"}],
            [{"text": "📄 Ver completo", "callback_data": f"pr:v:{pid}"}]]


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def texto_aviso(p):
    prop = json.loads(p["proposed_json"] or "{}")
    ev = json.loads(p["evidence_json"] or "{}")
    cab = (f"🧠 <b>Propuesta de criterio · {_esc(p['target'])}</b> (#{p['id']})\n"
           f"{_esc(prop.get('resumen', ''))}\n\n")
    if ev:
        cab += (f"Con el criterio actual: {ev.get('operaciones', '?')} ops · {ev.get('ganadas', '?')} ganadas · "
                f"{ev.get('neto_eur', 0):+.0f} € · {ev.get('r_total', 0):+.1f} R\n\n")
    val = ev.get("validacion")
    if val:
        if val.get("muestra"):
            texto = (f"{val['veredicto']} (muestra de {val['muestra']} decisiones; puntos con el criterio actual "
                     f"{val['partida']['puntos']}, con el propuesto {val['propuesto']['puntos']})")
        else:
            texto = val.get("veredicto") or val.get("error") or "sin datos"
        cab += f"Validación: {_esc(texto)}\n\n"
    pie = "\n<i>No caduca. Si llega otra propuesta para este símbolo, esta queda sustituida.</i>"
    # Solo las lineas que cambian: el texto entero va en «Ver completo» (Telegram corta a 4096 caracteres).
    lineas = [("🟢 " if d["t"] == "+" else "🔴 ") + d["l"].removeprefix("- ") for d in prop.get("diff", []) if d["t"] != "="]
    hueco = 3800 - len(cab) - len(pie) - 40
    cuerpo, usado = [], 0
    for l in lineas:
        if usado + len(l) + 1 > hueco:
            cuerpo.append(f"… ({len(lineas) - len(cuerpo)} líneas más en «Ver completo»)")
            break
        cuerpo.append(l)
        usado += len(l) + 1
    return cab + "<b>Cambios</b> (🟢 añade · 🔴 quita)\n" + _esc("\n".join(cuerpo)) + "\n" + pie


def documento(p):
    """Texto plano para «Ver completo»: motivo, criterio actual, propuesto y cambios marcados."""
    prop = json.loads(p["proposed_json"] or "{}")
    act = json.loads(p["current_json"] or "{}")
    marcas = "\n".join({"=": "        ", "+": "[AÑADE] ", "-": "[QUITA] "}[d["t"]] + d["l"] for d in prop.get("diff", []))
    return (f"PROPUESTA #{p['id']} · {p['target']} · {p['created_at']}\n\n"
            f"RESUMEN\n{prop.get('resumen', '')}\n\nMOTIVO\n{p.get('rationale') or ''}\n\n"
            f"EVIDENCIA\n{json.dumps(json.loads(p['evidence_json'] or '{}'), ensure_ascii=False, indent=2)}\n\n"
            f"CAMBIOS (texto completo; [AÑADE] y [QUITA] marcan lo que cambia)\n{marcas}\n\n"
            f"CRITERIO ACTUAL\n{act.get('criterio', '')}\n\nCRITERIO PROPUESTO\n{prop.get('criterio', '')}\n")


def _avisar(pid):
    p = leer_propuesta(pid)
    try:
        msg = _tg("sendMessage", text=texto_aviso(p), parse_mode="HTML", disable_web_page_preview=True,
                  reply_markup={"inline_keyboard": botones(pid)})
    except Exception as e:
        print(f"[prompts] aviso de Telegram no enviado: {type(e).__name__}", flush=True)
        return
    if not msg:
        return
    con = _con()
    try:
        prop = json.loads(con.execute("SELECT proposed_json FROM proposals WHERE id=?", (pid,)).fetchone()[0] or "{}")
        prop["tg_msg"] = msg["message_id"]
        con.execute("UPDATE proposals SET proposed_json=? WHERE id=?", (json.dumps(prop, ensure_ascii=False), pid))
        con.commit()
    finally:
        con.close()


def _quitar_botones(proposed_json, nota=None):
    """Quita los botones del aviso (decidida en la app, sustituida u obsoleta) y, si se da, responde con una nota."""
    msg = json.loads(proposed_json or "{}").get("tg_msg")
    if not msg:
        return
    try:
        _tg("editMessageReplyMarkup", message_id=msg, reply_markup={"inline_keyboard": []})
        if nota:
            _tg("sendMessage", text=nota, reply_to_message_id=msg, disable_notification=True)
    except Exception as e:                       # 400 si el bot ya los quito: no es un fallo
        print(f"[prompts] no se quitaron los botones: {type(e).__name__}", flush=True)


def quitar_botones_de(pid, nota=None):
    p = leer_propuesta(pid)
    if p:
        _quitar_botones(p["proposed_json"], nota)
