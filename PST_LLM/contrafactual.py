"""
Contrafactual de los `hold`: qué habría pasado si el agente hubiera entrado.

El agente de mejora solo veía operaciones tomadas, así que nunca podía ver las entradas que dejó pasar.
Este módulo mira cada decisión `hold` sin posición (con su estado completo) y, usando el cierre de la
última vela del marco del reloj que vio el agente, mira la siguiente hora de ese mismo marco. Para cada decisión marca cuál de los dos lados recorrió primero UMBRAL_ATR ATRs:
  'arriba' (habría ganado una compra), 'abajo' (habría ganado una venta), 'ambos', 'ninguno'.

No es una simulación de la operación (no hay SL ni TP ni costes): es una medida de dirección.
"""
import json
import sqlite3
from datetime import datetime, timedelta

import rutas
import sesgo
import universo
import llm_trader as lt

DATA = rutas.DATOS / ".agents" / "data"
HORIZONTE_SEG = 3600    # una hora de mercado hacia delante, sea cual sea el marco del reloj del agente
UMBRAL_ATR = 1.0        # recorrido que cuenta como "toque", en ATRs del marco del reloj
BARRAS = 12000          # historial de MT5 que se baja por marco (~8 días de M1, ~40 días de M5)


def _holds(symbol, dias):
    """(ts, state_json) de las decisiones hold sin posición abierta, desde hace `dias` días."""
    desde = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
    vistos, out = set(), []
    for f in sorted(DATA.glob("llm_trader*.db")):
        try:
            c = sqlite3.connect(f, timeout=15)
            filas = c.execute("SELECT ts, state_json FROM decisions WHERE symbol=? AND action='hold' AND llm_called=1 "
                              "AND ts>=? AND state_json IS NOT NULL", (symbol, desde)).fetchall()
            c.close()
        except sqlite3.Error:
            continue
        for ts, sj in filas:
            if ts not in vistos:
                vistos.add(ts)
                out.append((ts, sj))
    return out


def _barras(mt5, symbol, tf):
    """Velas de MT5 de un marco indexadas por su hora de broker ('%m-%d %H:%M'), igual que closed_at del estado."""
    rates = mt5.copy_rates_from_pos(symbol, getattr(mt5, f"TIMEFRAME_{tf}"), 0, BARRAS)
    if rates is None:
        return None
    h, l, c = rates["high"].astype(float), rates["low"].astype(float), rates["close"].astype(float)
    idx = {lt.broker_desde_mt5(int(t)).strftime("%m-%d %H:%M"): i for i, t in enumerate(rates["time"])}
    return {"h": h, "l": l, "c": c, "idx": idx, "n": len(rates)}


def _primero(h, l, entrada, atr):
    """Qué lado recorre UMBRAL_ATR primero en la ventana. En la misma vela, 'ambos' (no se sabe el orden)."""
    arriba, abajo = entrada + UMBRAL_ATR * atr, entrada - UMBRAL_ATR * atr
    for hi, lo in zip(h, l):
        up, down = hi >= arriba, lo <= abajo
        if up and down:
            return "ambos"
        if up:
            return "arriba"
        if down:
            return "abajo"
    return "ninguno"


def desenlace(mt5, symbol, st, cache):
    """
    Lo que pasó después de un estado: {'primero', 'arriba', 'abajo'} o None si no hay ventana completa
    (o el estado no sirve). `cache` guarda las velas de MT5 por (símbolo, marco) entre llamadas.
    """
    tf = st.get("call_interval")
    t = (st.get("timeframes") or {}).get(tf) or {}
    if not t.get("closed_at") or not t.get("ind"):
        return None
    clave = (symbol, tf)
    if clave not in cache:
        cache[clave] = _barras(mt5, symbol, tf)
    b = cache[clave]
    if b is None:
        return None
    i = b["idx"].get(t["closed_at"])
    velas = max(1, HORIZONTE_SEG // lt.TF_SEGUNDOS[tf])
    if i is None or i + velas >= b["n"] - 1:          # la última barra de MT5 está en formación
        return None
    atr = t["ind"]["atr"]
    if not atr or atr <= 0:
        return None
    entrada = b["c"][i]
    h, l = b["h"][i + 1:i + 1 + velas], b["l"][i + 1:i + 1 + velas]
    return {"primero": _primero(h, l, entrada, atr),
            "arriba": round((h.max() - entrada) / atr, 2),
            "abajo": round((entrada - l.min()) / atr, 2)}


def evaluar(mt5, symbol, dias=7, cache=None):
    """Lista de {'regimen', 'arriba', 'abajo', 'primero'} por cada hold sin posición con ventana completa."""
    cache = {} if cache is None else cache
    filas = []
    for ts, sj in _holds(symbol, dias):
        try:
            st = json.loads(sj)
        except ValueError:
            continue
        if st.get("position") is not None:
            continue                                   # con posición abierta no es una decisión de entrada
        d = desenlace(mt5, symbol, st, cache)
        if d:
            filas.append({"ts": ts, "regimen": sesgo.regimen(st), **d})
    return filas


def resumen(filas):
    """Totales y por régimen: cuántos holds, recorrido medio hacia cada lado y qué lado tocó primero."""
    def bloque(fs):
        n = len(fs)
        if not n:
            return {"holds": 0}
        pct = lambda k: round(100 * sum(1 for f in fs if f["primero"] == k) / n)
        return {"holds": n, "recorrido_medio_arriba_atr": round(sum(f["arriba"] for f in fs) / n, 2),
                "recorrido_medio_abajo_atr": round(sum(f["abajo"] for f in fs) / n, 2),
                "primero_arriba_pct": pct("arriba"), "primero_abajo_pct": pct("abajo"),
                "ninguno_pct": pct("ninguno"), "ambos_pct": pct("ambos")}
    por_regimen = {r: bloque([f for f in filas if f["regimen"] == r]) for r in sorted({f["regimen"] for f in filas})}
    return {"horizonte_min": HORIZONTE_SEG // 60, "umbral_atr": UMBRAL_ATR, "total": bloque(filas), "por_regimen": por_regimen}


def de_la_clase(symbol):
    """Símbolos del mismo tipo de activo que el dado (incluido él), según el universo."""
    clase = next((s.clase for s in universo.UNIVERSO if s.symbol == symbol), None)
    return clase, [s.symbol for s in universo.UNIVERSO if s.clase == clase and clase is not None]
