"""
Valida una propuesta de criterio antes de aprobarla.

Vuelve a pasar por el modelo una muestra de decisiones pasadas (el estado exacto que vio el agente) con
dos criterios: el de partida y el propuesto. Cada entrada (compra o venta) se puntúa contra lo que pasó
después en la hora siguiente (contrafactual.desenlace):
  compra con 'arriba' primero = +1, compra con 'abajo' primero = -1; la venta, al revés; resto = 0.
  hold = 0. Gana el criterio con más puntos, con un margen mínimo.

Limitaciones:
  - Por defecto la muestra puede ser la misma que vio el revisor (dentro de muestra). Con --fuera-de-muestra
    solo se usan decisiones posteriores a la propuesta, que el revisor no tenía.
  - Cada decisión cuesta una llamada al modelo: N decisiones x 2 criterios.
  - Es una medida de dirección a una hora, no una simulación con stops ni costes.

Uso:
    python PST_LLM/validacion_propuesta.py --propuesta 29
    python PST_LLM/validacion_propuesta.py --propuesta 29 --n 20 --fuera-de-muestra --guardar
"""
import argparse
import json
import random
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rutas                                   # noqa: E402
import prompts                                 # noqa: E402
import contrafactual as cf                     # noqa: E402
import llm_trader as lt                        # noqa: E402

DATA = rutas.DATOS / ".agents" / "data"
SALA = "Validacion_Prompt"                     # directorio de trabajo fuera del repo, como el resto de agentes
MARGEN = 1.0                                   # diferencia mínima de puntos para declarar un ganador
MIN_MUESTRA = 5                                # por debajo de esto no hay veredicto


def _decisiones(symbol, dias):
    """(ts, model, state_json) de las decisiones llamadas al modelo en la ventana, sin duplicados."""
    desde = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
    filas = []
    for f in sorted(DATA.glob("llm_trader*.db")):
        try:
            c = sqlite3.connect(f, timeout=15)
            filas += c.execute("SELECT ts, model, state_json FROM decisions WHERE symbol=? AND llm_called=1 "
                               "AND state_json IS NOT NULL AND ts>=?", (symbol, desde)).fetchall()
            c.close()
        except sqlite3.Error:
            continue
    vistos, out = set(), []
    for ts, model, sj in sorted(filas, key=lambda r: r[0]):
        if ts not in vistos:
            vistos.add(ts)
            out.append((ts, model, sj))
    return out


def _alias_modelo(model):
    m = (model or "").lower()
    return next((a for a in ("haiku", "sonnet", "opus") if a in m), "haiku")


def _hora_local(created_at_utc):
    """proposals.created_at se guarda en UTC (CURRENT_TIMESTAMP); lo paso a la hora local de las decisiones."""
    utc = datetime.strptime(created_at_utc, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return utc.astimezone().replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def muestra(mt5, symbol, dias, n, desde_local=None, cache=None):
    """Hasta n estados sin posición con su desenlace, elegidos al azar con semilla fija (reproducible)."""
    cache = {} if cache is None else cache
    candidatas = []
    for ts, model, sj in _decisiones(symbol, dias):
        if desde_local and ts <= desde_local:
            continue
        try:
            st = json.loads(sj)
        except ValueError:
            continue
        if st.get("position") is not None:
            continue
        d = cf.desenlace(mt5, symbol, st, cache)
        if d:
            candidatas.append({"ts": ts, "state": st, "modelo": _alias_modelo(model), **d})
    rng = random.Random(7)
    return rng.sample(candidatas, n) if len(candidatas) > n else candidatas


def puntuar(accion, primero):
    if accion not in ("buy", "sell") or primero in (None, "ambos", "ninguno"):
        return 0
    favorable, contraria = ("arriba", "abajo") if accion == "buy" else ("abajo", "arriba")
    return 1 if primero == favorable else -1 if primero == contraria else 0


def _llamar(criterio, state, modelo, esfuerzo):
    """La misma llamada que hace el agente en vivo: criterio completo en el system prompt, estado como mensaje."""
    cmd = lt._comando_base(modelo, esfuerzo, prompt=lt.montar_prompt(criterio)) + \
        ["--output-format", "json", json.dumps(state, separators=(",", ":"))]
    r = subprocess.run(cmd, cwd=str(lt.cwd_agente(SALA)), capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       timeout=180, encoding="utf-8", errors="replace", creationflags=lt._SIN_VENTANA)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p fallo ({r.returncode}): {(r.stderr or r.stdout)[-300:]}")
    return lt._decision(json.loads(r.stdout)).get("action")


def puntuar_criterio(criterio, casos, esfuerzo):
    res = {"compras": 0, "ventas": 0, "holds": 0, "puntos": 0, "errores": 0}
    for c in casos:
        try:
            accion = _llamar(criterio, c["state"], c["modelo"], esfuerzo)
        except Exception as e:
            res["errores"] += 1
            print(f"  [validacion] {c['ts']}: error {type(e).__name__}: {str(e)[:120]}", flush=True)
            continue
        clave = {"buy": "compras", "sell": "ventas"}.get(accion, "holds")
        res[clave] += 1
        res["puntos"] += puntuar(accion, c["primero"])
    return res


def veredicto(base, cand, n_muestra):
    if n_muestra < MIN_MUESTRA:
        return "insuficiente: muestra demasiado pequeña"
    diff = cand["puntos"] - base["puntos"]
    if diff > MARGEN:
        return f"mejora en la muestra (+{diff} puntos)"
    if diff < -MARGEN:
        return f"empeora en la muestra ({diff} puntos)"
    return f"sin diferencia clara ({diff:+} puntos)"


def validar_criterios(mt5, symbol, base_txt, cand_txt, dias=7, n=20, esfuerzo="low", desde_local=None):
    """
    Veredicto de un criterio propuesto frente al de partida, sobre una muestra de decisiones pasadas.
    Lo usan el agente de mejora (al crear la propuesta) y la herramienta de línea de comandos.
    """
    casos = muestra(mt5, symbol, dias, n, desde_local=desde_local)
    if not casos:
        return {"muestra": 0, "fuera_de_muestra": desde_local is not None,
                "veredicto": "sin decisiones con ventana completa", "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    base = puntuar_criterio(base_txt, casos, esfuerzo)
    cand = puntuar_criterio(cand_txt, casos, esfuerzo)
    return {"muestra": len(casos), "fuera_de_muestra": desde_local is not None, "partida": base,
            "propuesto": cand, "veredicto": veredicto(base, cand, len(casos)),
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def main():
    ap = argparse.ArgumentParser(description="Valida una propuesta de criterio con decisiones pasadas")
    ap.add_argument("--propuesta", type=int, required=True, help="id de la propuesta prompt_simbolo")
    ap.add_argument("--dias", type=int, default=7)
    ap.add_argument("--n", type=int, default=20, help="decisiones por criterio (cuesta 2n llamadas)")
    ap.add_argument("--esfuerzo", default="low", choices=["low", "medium", "high"])
    ap.add_argument("--fuera-de-muestra", action="store_true", help="solo decisiones posteriores a la propuesta")
    ap.add_argument("--guardar", action="store_true", help="guarda el resultado en la propuesta (evidence_json)")
    a = ap.parse_args()

    p = prompts.leer_propuesta(a.propuesta)
    if p is None:
        print(f"no existe la propuesta {a.propuesta}")
        return 1
    prop = json.loads(p["proposed_json"] or "{}")
    base_txt = json.loads(p["current_json"] or "{}").get("criterio")
    cand_txt = prop.get("criterio")
    symbol = p["target"]
    if not base_txt or not cand_txt:
        print("la propuesta no tiene criterio de partida o propuesto")
        return 1

    import MetaTrader5 as mt5
    if not mt5.initialize():
        print(f"MT5 no disponible: {mt5.last_error()}")
        return 1
    try:
        desde = _hora_local(p["created_at"]) if a.fuera_de_muestra else None
        val = validar_criterios(mt5, symbol, base_txt, cand_txt, dias=a.dias, n=a.n, esfuerzo=a.esfuerzo,
                                desde_local=desde)
    finally:
        mt5.shutdown()

    print(f"{symbol} · propuesta #{a.propuesta} · muestra {val['muestra']} decisiones "
          f"({'fuera de muestra' if a.fuera_de_muestra else 'puede estar dentro de muestra'})", flush=True)
    if not val["muestra"]:
        print("sin decisiones con ventana completa: nada que validar")
        return 0
    print(f"\npartida:   {val['partida']}")
    print(f"propuesto: {val['propuesto']}")
    print(f"veredicto: {val['veredicto']}")

    if a.guardar:
        ev = json.loads(p["evidence_json"] or "{}")
        ev["validacion"] = val
        con = prompts._con()
        try:
            con.execute("UPDATE proposals SET evidence_json=? WHERE id=?", (json.dumps(ev, ensure_ascii=False), a.propuesta))
            con.commit()
        finally:
            con.close()
        print("guardado en la propuesta")
    return 0


if __name__ == "__main__":
    sys.exit(main())
