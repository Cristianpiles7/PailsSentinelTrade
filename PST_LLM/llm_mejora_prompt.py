#!/usr/bin/env python
"""
Agente de mejora del criterio de cada agente LLM. Lee las operaciones cerradas de un simbolo (ganadoras y
perdedoras) y, si ve un patron repetido, PROPONE un cambio del criterio de ese simbolo. Nunca lo aplica:
la propuesta va a la bandeja y a Telegram, y se aplica solo si la apruebas (PST_LLM/prompts.py).

Cuando revisa un simbolo: cuando lleva al menos `min_operaciones` operaciones cerradas nuevas con el
criterio activo desde su ultima revision. Ajustes en Ajustes > Flota LLM (mejora_prompt_config.json).

    python PST_LLM/llm_mejora_prompt.py                 # bucle (cada cadencia_min)
    python PST_LLM/llm_mejora_prompt.py --once          # una pasada ahora
    python PST_LLM/llm_mejora_prompt.py --simbolo US100.cash --forzar --seco
                                                        # revisar ya, sin minimo, e imprimir sin guardar
    Parar: crear .agents/data/llm_mejora_prompt.STOP (o llm_trader.STOP, que apaga todo)
"""
import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rutas              # noqa: E402
import prompts            # noqa: E402
import registro           # noqa: E402
import universo           # noqa: E402
import llm_trader as lt   # noqa: E402
import sesgo              # noqa: E402  vigía de sesgo direccional: ve también los hold

DATA = rutas.DATOS / ".agents" / "data"
STOP_FILE = DATA / "llm_mejora_prompt.STOP"
SALA = "Mejora_Prompt"            # directorio de trabajo fuera del repo (no hereda CLAUDE.md ni memoria)
MAX_POR_PASADA = 3                # simbolos revisados como mucho en cada pasada (cuota)
SESGO_DIAS = 7                    # ventana del sesgo que se le enseña al agente de mejora
SESGO_REPETIR_H = 24              # un mismo sesgo no vuelve a consumir cuota antes de estas horas
AVISO_SESGO_CADA_H = 24           # informe de sesgo a Telegram (una vez al día)
RAZON = {0: "manual", 1: "movil", 2: "web", 3: "agente", 4: "STOP", 5: "TAKE PROFIT", 6: "stop-out"}

SISTEMA = """Eres el revisor del CRITERIO DE ENTRADA de un agente de trading (un LLM) que opera UN símbolo en una
cuenta demo. El agente recibe en cada vela un prompt con una parte FIJA (formato, costes, reglas de cierre,
campos del estado; te la doy como contexto, no la puedes cambiar) y una sección TRADING CRITERIA propia de
este símbolo, que es lo único que puedes proponer cambiar.

Tu trabajo: mirar TODAS las operaciones cerradas con el criterio actual (ganadoras y perdedoras) y decidir si
hay un patrón repetido que un cambio concreto del criterio corregiría. Ejemplos de patrón: varias pérdidas
entrando contra el marco alto, entradas con el precio muy lejos de la EMA20, entradas en un marco donde el
spread pesa mucho, ganadoras que comparten una condición que el criterio no pide.

Reglas:
- Un patrón exige varias operaciones, no una. Mira también si la regla que propones habría quitado
  ganadoras: un filtro que elimina tantas ganadoras como perdedoras no mejora nada.
- Con pocas operaciones o resultados mezclados sin patrón claro, responde cambiar=false. Es una respuesta
  válida y frecuente: no cambies por cambiar.
- Una sola hipótesis por propuesta: añade, cambia o quita como mucho una o dos líneas relacionadas. Prefiere
  quitar o precisar una regla antes que añadir otra.
- Formato del criterio: en español, una regla por línea, cada línea empieza por "- ". Máximo {max_car} caracteres.
- Cada regla debe poder comprobarse con los campos del estado que ve el agente (marcos, ATR, RSI, ADX,
  +DI/-DI, EMA20/50/200, posición en Bollinger, distancia a EMA20 en ATRs, spread/ATR, velas, su historial).
- No escribas sobre tamaño de posición, riesgo por operación, límites diarios ni reglas de cierre: los aplica
  el sistema y ya están en la parte fija.
- Si una versión anterior probó algo parecido y no funcionó, no lo vuelvas a proponer sin evidencia nueva.
- 'sesgo_direccional' (si viene) cuenta TODAS las decisiones, también los hold, por régimen (H1 y H4 alineados
  alcista o bajista). Si el agente casi nunca compra en un régimen alcista, o nunca vende en uno bajista, eso
  es un fallo del criterio aunque no haya pérdidas que lo muestren: propón una entrada CONCRETA para la
  dirección que falta (p. ej. retroceso a la EMA20 de M5/M15 con RSI y +DI/-DI que confirmen, con su
  invalidación), con las mismas exigencias que la dirección contraria. No añadas reglas que solo
  restrinjan más: un criterio que solo bloquea entradas no corrige un sesgo por falta de entradas.
- Una frase de "Por defecto, quédate fuera" no basta para justificar que nunca haya compras: si el régimen
  alcista existe, el criterio debe decir qué lo convierte en entrada.
- 'tickets_evidencia': los tickets de las operaciones que apoyan el cambio (y las ganadoras que afectaría).
- 'resumen': una frase (máx. 200 caracteres) que diga qué cambia. 'motivo': el patrón, con números
  (cuántas operaciones, cuánto ganaron/perdieron), máx. 1200 caracteres.
- 'criterio_nuevo': el criterio COMPLETO propuesto (no solo el cambio). Si cambiar=false, repite el actual."""

SCHEMA = {
    "type": "object",
    "properties": {
        "cambiar": {"type": "boolean"},
        "resumen": {"type": "string"},
        "motivo": {"type": "string"},
        "criterio_nuevo": {"type": "string"},
        "tickets_evidencia": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["cambiar", "resumen", "motivo", "criterio_nuevo"],
}


# ── Operaciones de un simbolo ────────────────────────────────────────────────
def _filas_simbolo(symbol):
    """Entradas ejecutadas de cualquier BBDD de agente (incluidas las de nombres antiguos) para el simbolo."""
    filas = []
    for f in sorted(DATA.glob("llm_trader*.db")):
        try:
            c = sqlite3.connect(f)
            c.row_factory = sqlite3.Row
            filas += [dict(r) for r in c.execute(
                "SELECT ts, action, ticket, horizonte, sl_mult, tp_r, confidence, reason, exec_result, state_json "
                "FROM decisions WHERE symbol=? AND executed=1 AND action IN ('buy','sell') AND ticket IS NOT NULL",
                (symbol,))]
            c.close()
        except sqlite3.Error:
            continue
    vistos, out = set(), []
    for r in sorted(filas, key=lambda r: r["ts"]):
        if r["ticket"] not in vistos:
            vistos.add(r["ticket"])
            out.append(r)
    return out


def _indicadores(state_json):
    """Del estado completo que vio el agente, solo los indicadores por marco (sin velas: pesan mucho)."""
    try:
        st = json.loads(state_json or "{}")
    except ValueError:
        return None
    return {tf: {**(d.get("ind") or {}), "px_vs_ema20_atr": d.get("px_vs_ema20_atr"), "spread_atr": d.get("spread_atr")}
            for tf, d in (st.get("timeframes") or {}).items()}


def operaciones_cerradas(mt5, symbol, desde=None):
    """Operaciones cerradas del simbolo abiertas desde `desde` (hora local, como las BBDD de los agentes)."""
    out = []
    for r in _filas_simbolo(symbol):
        if desde and r["ts"] < desde:
            continue
        if mt5.positions_get(ticket=r["ticket"]):
            continue                                   # sigue abierta
        deals = mt5.history_deals_get(position=r["ticket"]) or []
        ent = [d for d in deals if d.entry == 0]
        sal = [d for d in deals if d.entry in (1, 2, 3)]
        if not ent or not sal:
            continue
        neto = sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0.0) for d in deals)
        m = re.search(r"riesgo ~(\d+)", r["exec_result"] or "")
        riesgo = float(m.group(1)) if m else None
        s = sal[-1]
        salida = "revisor" if "REVISOR" in (s.comment or "").upper() else RAZON.get(s.reason, str(s.reason))
        out.append({"ticket": r["ticket"], "abierta": r["ts"], "lado": r["action"], "marco": r["horizonte"],
                    "sl_atr_mult": r["sl_mult"], "tp_r": r["tp_r"], "confianza": r["confidence"],
                    "salida": salida, "minutos": int((s.time - ent[0].time) / 60), "neto_eur": round(neto, 2),
                    "R": round(neto / riesgo, 2) if riesgo else None, "razon_entrada": r["reason"],
                    "indicadores_entrada": _indicadores(r["state_json"])})
    return out


def resumen(ops):
    return {"operaciones": len(ops), "ganadas": sum(1 for o in ops if o["neto_eur"] > 0),
            "neto_eur": round(sum(o["neto_eur"] for o in ops), 2),
            "r_total": round(sum(o["R"] or 0 for o in ops), 2),
            "por_salida": {k: sum(1 for o in ops if o["salida"] == k) for k in {o["salida"] for o in ops}}}


def simbolos_con_operaciones():
    syms = set()
    for f in DATA.glob("llm_trader*.db"):
        try:
            c = sqlite3.connect(f)
            syms |= {r[0] for r in c.execute("SELECT DISTINCT symbol FROM decisions WHERE executed=1 AND ticket IS NOT NULL")}
            c.close()
        except sqlite3.Error:
            continue
    return sorted(s for s in syms if s)


# ── Revisiones (para contar operaciones nuevas) ──────────────────────────────
def _tickets_revisados(symbol, version):
    con = prompts._con()
    try:
        vistos = set()
        for (t,) in con.execute("SELECT tickets FROM llm_prompt_revisiones WHERE symbol=? AND version=?", (symbol, version)):
            vistos |= set(json.loads(t or "[]"))
        return vistos
    finally:
        con.close()


def _anotar_revision(symbol, version, ops, resultado, pid=None):
    con = prompts._con()
    try:
        con.execute("INSERT INTO llm_prompt_revisiones (ts, symbol, version, operaciones, resultado, proposal_id, tickets) "
                    "VALUES (?,?,?,?,?,?,?)", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), symbol, version, len(ops),
                                               resultado[:500], pid, json.dumps([o["ticket"] for o in ops])))
        con.commit()
    finally:
        con.close()


def _versiones_previas(mt5, symbol, max_n=3):
    """Las ultimas versiones anteriores del criterio con su resultado, para no repetir ideas que no funcionaron."""
    h = prompts.historial(symbol, limite=max_n + 1)
    out = []
    for i, v in enumerate(h[1:], start=1):
        hasta = h[i - 1]["activado"]
        ops = [o for o in operaciones_cerradas(mt5, symbol, v["activado"]) if o["abierta"] < hasta]
        out.append({"activado": v["activado"], "por": v["por"], "texto": v["texto"], "resultado": resumen(ops)})
    return out


# ── Revision de un simbolo ───────────────────────────────────────────────────
def preguntar(entrada, cfg):
    sistema = SISTEMA.replace("{max_car}", str(cfg["max_caracteres"]))
    # El contexto va por stdin: como argumento de línea de comandos supera el límite de Windows (WinError 206)
    cmd = lt._comando_base(cfg["modelo"], cfg["esfuerzo"], prompt=sistema, schema=SCHEMA) + ["--output-format", "json"]
    payload = json.dumps(entrada, ensure_ascii=False, separators=(",", ":"))
    r = subprocess.run(cmd, cwd=str(lt.cwd_agente(SALA)), input=payload, capture_output=True, text=True,
                       timeout=600, encoding="utf-8", errors="replace", creationflags=lt._SIN_VENTANA)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p fallo ({r.returncode}): {(r.stderr or r.stdout)[-400:]}")
    return lt._decision(json.loads(r.stdout))


def _ultima_revision(symbol):
    con = prompts._con()
    try:
        fila = con.execute("SELECT MAX(ts) FROM llm_prompt_revisiones WHERE symbol=?", (symbol,)).fetchone()
        return fila[0] if fila else None
    finally:
        con.close()


def revisar(mt5, symbol, cfg, forzar=False, seco=False):
    act = prompts.activo(symbol)
    ops = operaciones_cerradas(mt5, symbol, act["activado"])
    nuevas = [o for o in ops if o["ticket"] not in _tickets_revisados(symbol, act["version"])]
    # El sesgo cuenta aunque no haya operaciones cerradas: un agente que nunca compra no cierra compras.
    alerta = sesgo.analizar(dias=SESGO_DIAS)["alertas"].get(symbol)
    if alerta:
        ult = _ultima_revision(symbol)
        if ult and (datetime.now() - datetime.strptime(ult, "%Y-%m-%d %H:%M:%S")).total_seconds() < SESGO_REPETIR_H * 3600:
            return f"{symbol}: sesgo ya revisado en las últimas {SESGO_REPETIR_H} h"
    if not forzar and not alerta and len(nuevas) < cfg["min_operaciones"]:
        return f"{symbol}: {len(nuevas)}/{cfg['min_operaciones']} operaciones nuevas, todavía no"
    if not ops and not alerta:
        return f"{symbol}: sin operaciones cerradas con el criterio actual"
    clase = next((s.clase for s in universo.UNIVERSO if s.symbol == symbol), None)
    contexto = ops[-cfg["max_operaciones_contexto"]:]
    entrada = {"symbol": symbol, "clase": clase, "criterio_actual": act["texto"],
               "criterio_activo_desde": act["activado"] or "desde el principio (criterio por defecto)",
               "resumen_con_criterio_actual": resumen(ops), "operaciones": contexto,
               "versiones_anteriores": _versiones_previas(mt5, symbol),
               "parte_fija_del_prompt": lt.PROMPT_FIJO}
    if alerta:
        entrada["sesgo_direccional"] = {"ventana_dias": SESGO_DIAS, "alertas": alerta["marcas"],
                                        "decisiones": alerta["stats"], "por_regimen": alerta["regimen"]}
    d = preguntar(entrada, cfg)
    if seco:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return f"{symbol}: revisado en seco (no se guarda)"
    if not d.get("cambiar") or (d.get("criterio_nuevo") or "").strip() == act["texto"].strip():
        _anotar_revision(symbol, act["version"], nuevas, f"sin cambio: {d.get('motivo', '')}")
        return f"{symbol}: sin cambio ({(d.get('resumen') or '')[:120]})"
    ev = {**resumen(ops), "tickets": d.get("tickets_evidencia") or []}
    try:
        pid = prompts.crear_propuesta(symbol, d["criterio_nuevo"], (d.get("resumen") or "")[:200],
                                      (d.get("motivo") or "")[:1500], ev)
    except ValueError as e:
        _anotar_revision(symbol, act["version"], nuevas, f"propuesta no válida: {e}")
        return f"{symbol}: propuesta no válida ({e})"
    _anotar_revision(symbol, act["version"], nuevas, "propuesta", pid)
    return f"{symbol}: propuesta #{pid} ({d.get('resumen', '')[:120]})"


def pasada(mt5, cfg):
    hechas = 0
    con_sesgo = sorted(sesgo.analizar(dias=SESGO_DIAS)["alertas"])
    candidatos = con_sesgo + [s for s in simbolos_con_operaciones() if s not in con_sesgo]
    for sym in candidatos:
        if hechas >= MAX_POR_PASADA:
            break
        ok, why, _ = lt.cuota_ok()
        if not ok:
            print(f"[mejora] freno de cuota: {why}", flush=True)
            return
        try:
            txt = revisar(mt5, sym, cfg)
        except Exception as e:
            print(f"[mejora] {sym}: error {type(e).__name__}: {e}", flush=True)
            continue
        print(f"[mejora] {txt}", flush=True)
        if not any(f in txt for f in ("todavía no", "sin operaciones", "ya revisado")):
            hechas += 1


def aviso_sesgo():
    """Informe diario de sesgo a Telegram: solo avisa de lo que pasa, la propuesta de prompt va aparte."""
    informe = sesgo.analizar(dias=SESGO_DIAS)
    texto = sesgo.texto(informe)
    print(f"[sesgo] {len(informe['alertas'])} símbolos con sesgo", flush=True)
    if informe["alertas"]:
        lt.telegram_enviar(texto)


def main():
    ap = argparse.ArgumentParser(description="Agente de mejora del criterio de los agentes LLM")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--simbolo")
    ap.add_argument("--forzar", action="store_true", help="revisar aunque no llegue al minimo de operaciones")
    ap.add_argument("--seco", action="store_true", help="imprimir la respuesta sin guardar propuesta")
    a = ap.parse_args()
    import MetaTrader5 as mt5
    if not mt5.initialize():
        print(f"MT5 no disponible: {mt5.last_error()}", file=sys.stderr)
        return 1
    try:
        if a.simbolo:
            print(revisar(mt5, a.simbolo, prompts.ajustes(), forzar=a.forzar, seco=a.seco), flush=True)
            return 0
        if a.once:
            pasada(mt5, prompts.ajustes())
            return 0
        STOP_FILE.unlink(missing_ok=True)
        ultima, ultimo_aviso = 0.0, 0.0
        print("[mejora] en marcha", flush=True)
        while not (STOP_FILE.exists() or lt.STOP_GLOBAL.exists()):
            cfg = prompts.ajustes()
            if cfg["activo"] and time.time() - ultima >= cfg["cadencia_min"] * 60:
                ultima = time.time()
                try:
                    pasada(mt5, cfg)
                except Exception as e:
                    print(f"[mejora] error en la pasada: {e}", flush=True)
            if time.time() - ultimo_aviso >= AVISO_SESGO_CADA_H * 3600:
                ultimo_aviso = time.time()
                try:
                    aviso_sesgo()
                except Exception as e:
                    print(f"[sesgo] error en el aviso: {e}", flush=True)
            time.sleep(20)
        print("[mejora] fichero STOP detectado: parado.", flush=True)
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
