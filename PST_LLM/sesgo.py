"""
Vigía de sesgo direccional de los agentes LLM.

Detecta lo que el agente de mejora no ve: el agente de mejora solo mira operaciones CERRADAS, y un agente que
nunca compra no cierra compras, así que nunca llega a revisarlo. Este módulo mira TODAS las decisiones
(también los `hold`), separadas por régimen de tendencia (H1 y H4 alineados arriba o abajo), y marca:

- sin_compras_en_alcista: el marco alto estuvo alcista muchas veces y el agente no compró nunca.
- sin_ventas_en_bajista: lo mismo en sentido contrario.
- todo_un_lado: casi todas las operaciones van en una sola dirección.

Uso:
    python PST_LLM/sesgo.py                  # informe de todos los agentes, últimas 24 h
    python PST_LLM/sesgo.py --dias 3         # ventana distinta
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rutas  # noqa: E402

DATA = rutas.DATOS / ".agents" / "data"
MIN_LLAMADAS_REGIMEN = 20      # llamadas en un régimen para opinar sobre él: menos es ruido
MIN_OPERACIONES = 8            # operaciones ejecutadas para opinar sobre el reparto compra/venta
UMBRAL_LADO = 0.85             # fracción de operaciones en un solo lado que se considera sesgo


def tendencia(estado, marco):
    ind = ((estado.get("timeframes") or {}).get(marco) or {}).get("ind") or {}
    if not ind:
        return None
    if ind["ema20"] > ind["ema50"] > ind["ema200"]:
        return "alcista"
    if ind["ema20"] < ind["ema50"] < ind["ema200"]:
        return "bajista"
    return "mixto"


def regimen(estado):
    """Régimen = H1 y H4 en la misma dirección (si no, 'mixto')."""
    h1, h4 = tendencia(estado, "H1"), tendencia(estado, "H4")
    if h1 is None or h1 != h4 or h1 == "mixto":
        return "mixto"
    return h1


def filas_desde(desde):
    """(symbol, action, executed, state_json) de todas las decisiones de los agentes desde `desde`."""
    out = []
    for f in sorted(DATA.glob("llm_trader*.db")):
        try:
            c = sqlite3.connect(f, timeout=15)
            out += c.execute("SELECT symbol, action, executed, state_json FROM decisions "
                             "WHERE ts >= ? AND llm_called = 1", (desde,)).fetchall()
            c.close()
        except sqlite3.Error:
            continue
    return out


def analizar(dias=1):
    desde = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
    por_simbolo = {}
    for sym, accion, ejecutado, sj in filas_desde(desde):
        s = por_simbolo.setdefault(sym, {"llamadas": 0, "compras": 0, "ventas": 0, "ejec_compra": 0,
                                         "ejec_venta": 0, "reg": {}})
        s["llamadas"] += 1
        try:
            reg = regimen(json.loads(sj or "{}"))
        except ValueError:
            reg = "mixto"
        r = s["reg"].setdefault(reg, {"llamadas": 0, "compras": 0, "ventas": 0})
        r["llamadas"] += 1
        if accion == "buy":
            s["compras"] += 1
            r["compras"] += 1
            s["ejec_compra"] += 1 if ejecutado else 0
        elif accion == "sell":
            s["ventas"] += 1
            r["ventas"] += 1
            s["ejec_venta"] += 1 if ejecutado else 0

    alertas = {}
    for sym, s in por_simbolo.items():
        marcas = []
        alc, baj = s["reg"].get("alcista", {}), s["reg"].get("bajista", {})
        if alc.get("llamadas", 0) >= MIN_LLAMADAS_REGIMEN and alc.get("compras", 0) == 0:
            marcas.append(f"sin_compras_en_alcista ({alc['llamadas']} llamadas en H1+H4 alcistas, 0 compras)")
        if baj.get("llamadas", 0) >= MIN_LLAMADAS_REGIMEN and baj.get("ventas", 0) == 0:
            marcas.append(f"sin_ventas_en_bajista ({baj['llamadas']} llamadas en H1+H4 bajistas, 0 ventas)")
        ops = s["compras"] + s["ventas"]
        if ops >= MIN_OPERACIONES:
            lado = max(s["compras"], s["ventas"]) / ops
            if lado >= UMBRAL_LADO:
                lado_txt = "ventas" if s["ventas"] >= s["compras"] else "compras"
                marcas.append(f"todo_un_lado ({lado_txt} {lado:.0%} de {ops} señales)")
        if marcas:
            alertas[sym] = {"marcas": marcas, "stats": {k: v for k, v in s.items() if k != "reg"},
                            "regimen": s["reg"]}
    return {"desde": desde, "dias": dias, "simbolos": por_simbolo, "alertas": alertas}


def texto(informe):
    lineas = [f"Sesgo direccional, desde {informe['desde']}:"]
    for sym, s in sorted(informe["simbolos"].items()):
        lineas.append(f"- {sym}: {s['llamadas']} llamadas, {s['compras']} compras, {s['ventas']} ventas")
    if not informe["alertas"]:
        lineas.append("Sin sesgo detectado.")
    for sym, a in sorted(informe["alertas"].items()):
        lineas.append(f"⚠ {sym}: " + "; ".join(a["marcas"]))
    return "\n".join(lineas)


def main():
    ap = argparse.ArgumentParser(description="Vigía de sesgo direccional de los agentes LLM")
    ap.add_argument("--dias", type=float, default=1)
    a = ap.parse_args()
    print(texto(analizar(a.dias)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
