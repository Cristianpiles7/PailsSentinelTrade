"""
Una fila por operacion cerrada de la flota LLM (tabla `llm_operaciones` de pst_trading.db).
Plan v3 (docs/PLAN_OPERACIONES_SIMBOLOS.md), Fase 0: que cada operacion tenga una fila completa y comparable.

Fuentes:
  * BBDD de cada agente (.agents/data/llm_trader_*.db): decisiones ejecutadas con su ticket, marco del stop,
    sl_atr_mult, tp_r, version del prompt y el estado que vio el agente (para el regimen H1/H4).
  * MT5 (history_deals_get): bruto, comision, swap, motivo de salida, minutos, MAE/MFE en R con velas M1.
    Sin MT5 (fuente='trades') se rellena lo que se puede desde la tabla `trades`: neto y R, sin salida ni MAE/MFE.
    Una fila de MT5 nunca se pisa con una de `trades`.

Uso:
  python PST_LLM/operaciones.py --rellenar [--dias N]     (necesita MT5: lo normal es el exe o el python de la flota)
  python PST_LLM/operaciones.py --rellenar --sin-mt5      (desde `trades`, sin salida ni MAE/MFE)
Los agentes llaman a registrar_agente() cada pocos minutos: solo procesa tickets que aun no tienen fila.
Principio de registro.py: escribir aqui NUNCA puede parar a un agente (registrar_agente no lanza).
"""
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import registro                                               # noqa: E402

DATA = registro.DATA
RAZON_DEAL = {0: "manual", 1: "movil", 2: "web", 3: "agente", 4: "SL", 5: "TP", 6: "stop-out"}

COLUMNAS = [
    ("ticket", "INTEGER PRIMARY KEY"), ("symbol", "TEXT"), ("clase", "TEXT"), ("agente", "TEXT"), ("lado", "TEXT"),
    ("abierta", "TEXT"), ("hora_entrada", "INTEGER"), ("marco_stop", "TEXT"), ("sl_atr_mult", "REAL"),
    ("tp_r", "REAL"), ("riesgo_eur", "REAL"), ("bruto", "REAL"), ("comision", "REAL"), ("swap", "REAL"),
    ("neto", "REAL"), ("r_real", "REAL"), ("motivo_salida", "TEXT"), ("minutos", "INTEGER"), ("mae_r", "REAL"),
    ("mfe_r", "REAL"), ("coste_r", "REAL"), ("prompt_version", "TEXT"), ("regimen_h1_h4", "TEXT"),
    ("fuente", "TEXT"),
]


def tabla(con):
    con.execute(f"CREATE TABLE IF NOT EXISTS llm_operaciones ({', '.join(f'{n} {t}' for n, t in COLUMNAS)})")
    con.execute("CREATE INDEX IF NOT EXISTS ix_llm_operaciones_sym ON llm_operaciones(symbol, abierta)")


def _clases(con):
    import universo
    out = {s.symbol: s.clase for s in universo.UNIVERSO}
    try:
        out.update(dict(con.execute("SELECT symbol, clase FROM llm_agentes")))
    except sqlite3.Error:
        pass
    return out


def regimen(state_json):
    """'H1+/H4-': signo de (ultimo cierre - EMA200) en H1 y H4, segun el estado que vio el agente al entrar."""
    try:
        marcos = json.loads(state_json or "{}").get("timeframes") or {}
    except ValueError:
        return None
    partes = []
    for tf in ("H1", "H4"):
        m = marcos.get(tf)
        try:
            partes.append(f"{tf}{'+' if m['candles_ohlc'][-1][3] > m['ind']['ema200'] else '-'}")
        except (TypeError, KeyError, IndexError):
            partes.append(f"{tf}?")
    return "/".join(partes)


def _decisiones(db_file, solo_tickets=None):
    """Decisiones ejecutadas (buy/sell con ticket) de un agente, la primera por ticket."""
    c = sqlite3.connect(db_file, timeout=15)
    c.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(decisions)")}      # BBDD antiguas sin alguna columna
        opcional = ", ".join(k if k in cols else f"NULL AS {k}" for k in ("horizonte", "prompt_version"))
        filas = c.execute(f"SELECT ts, symbol, action, sl_mult, tp_r, {opcional}, ticket, exec_result, "
                          "state_json FROM decisions WHERE executed=1 AND action IN ('buy','sell') "
                          "AND ticket IS NOT NULL ORDER BY id").fetchall()
    finally:
        c.close()
    vistos, out = set(), []
    for r in filas:
        if r["ticket"] in vistos or (solo_tickets is not None and r["ticket"] not in solo_tickets):
            continue
        vistos.add(r["ticket"])
        out.append(r)
    return out


def _riesgo_y_coste(exec_result):
    riesgo = re.search(r"riesgo ~(\d+)", exec_result or "")
    coste = re.search(r"coste (\d+(?:\.\d+)?)% R", exec_result or "")
    return (float(riesgo.group(1)) if riesgo else None), (float(coste.group(1)) / 100 if coste else None)


def _base(r, agente, clase):
    riesgo, coste = _riesgo_y_coste(r["exec_result"])
    return {"ticket": r["ticket"], "symbol": r["symbol"], "clase": clase, "agente": agente, "lado": r["action"],
            "abierta": r["ts"], "hora_entrada": int(r["ts"][11:13]), "marco_stop": r["horizonte"],
            "sl_atr_mult": r["sl_mult"], "tp_r": r["tp_r"], "riesgo_eur": riesgo, "coste_r": coste,
            "prompt_version": r["prompt_version"], "regimen_h1_h4": regimen(r["state_json"])}


def _fila_mt5(mt5, r, agente, clase):
    """Fila completa desde MT5, o None si la posicion sigue abierta o no hay deals."""
    ticket = r["ticket"]
    if mt5.positions_get(ticket=ticket):
        return None
    deals = mt5.history_deals_get(position=ticket) or []
    ent = [d for d in deals if d.entry == 0]
    sal = [d for d in deals if d.entry in (1, 2, 3)]
    if not ent or not sal:
        return None
    fila = _base(r, agente, clase)
    bruto = sum(d.profit for d in deals)
    comision = sum(d.commission + getattr(d, "fee", 0.0) for d in deals)
    swap = sum(d.swap for d in deals)
    neto = bruto + comision + swap
    s = sal[-1]
    fila.update({"bruto": round(bruto, 2), "comision": round(comision, 2), "swap": round(swap, 2),
                 "neto": round(neto, 2), "minutos": int((s.time - ent[0].time) / 60),
                 "r_real": round(neto / fila["riesgo_eur"], 3) if fila["riesgo_eur"] else None,
                 "motivo_salida": _motivo_salida(s), "fuente": "mt5"})
    fila["mae_r"], fila["mfe_r"] = _mae_mfe(mt5, r, ent[0], s)
    return fila


def _motivo_salida(deal):
    comentario = (deal.comment or "").upper()
    if "REVISOR" in comentario:
        return "revisor"
    if "REGLA" in comentario:
        return "regla"
    if "SESION" in comentario or "SESIÓN" in comentario:
        return "sesion"
    return RAZON_DEAL.get(deal.reason, "otra")


def _mae_mfe(mt5, r, entrada, salida):
    """(MAE, MFE) en R: lo maximo que fue en contra / a favor entre la entrada y la salida, con velas M1.
    R = distancia entrada-stop de la orden de apertura. Sesgo: la vela M1 de la entrada puede traer un extremo
    anterior a la entrada. (None, None) si no hay stop o velas."""
    sl = None
    for o in (mt5.history_orders_get(position=r["ticket"]) or []):
        if o.sl:
            sl = o.sl
            break
    if sl is None:
        m = re.search(r"SL ([\d.]+)", r["exec_result"] or "")
        sl = float(m.group(1)) if m else None
    dist = abs(entrada.price - sl) if sl else 0
    if dist <= 0:
        return None, None
    barras = mt5.copy_rates_range(r["symbol"], mt5.TIMEFRAME_M1, int(entrada.time), int(salida.time))
    if barras is None or not len(barras):
        return None, None
    alto, bajo = float(max(b["high"] for b in barras)), float(min(b["low"] for b in barras))
    if r["action"] == "buy":
        mae, mfe = entrada.price - bajo, alto - entrada.price
    else:
        mae, mfe = alto - entrada.price, entrada.price - bajo
    return round(max(mae, 0) / dist, 3), round(max(mfe, 0) / dist, 3)


def _fila_trades(con, r, agente, clase):
    """Fila sin MT5: neto y R desde la tabla `trades` (profit; no se sabe si incluye comision)."""
    t = con.execute("SELECT profit FROM trades WHERE ticket=? AND strategy_name='LLM_TRADER'", (r["ticket"],)).fetchone()
    if t is None:
        return None
    fila = _base(r, agente, clase)
    fila.update({"neto": round(t[0], 2), "fuente": "trades",
                 "r_real": round(t[0] / fila["riesgo_eur"], 3) if fila["riesgo_eur"] else None})
    return fila


def _guardar(con, fila):
    previa = con.execute("SELECT fuente FROM llm_operaciones WHERE ticket=?", (fila["ticket"],)).fetchone()
    if previa and previa[0] == "mt5" and fila["fuente"] != "mt5":
        return False
    cols = [n for n, _ in COLUMNAS]
    con.execute(f"INSERT OR REPLACE INTO llm_operaciones ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                tuple(fila.get(c) for c in cols))
    return True


def _nombre(db_file):
    return Path(db_file).stem.replace("llm_trader_", "")


def registrar_agente(mt5, db_file, solo_nuevas=True):
    """Anade a llm_operaciones las operaciones cerradas de UN agente que aun no tienen fila. Nunca lanza."""
    try:
        con = registro.central()
        tabla(con)
        clases = _clases(con)
        hechos = {r[0] for r in con.execute("SELECT ticket FROM llm_operaciones WHERE fuente='mt5'")} if solo_nuevas else set()
        nuevas = 0
        for r in _decisiones(db_file):
            if r["ticket"] in hechos:
                continue
            fila = _fila_mt5(mt5, r, _nombre(db_file), clases.get(r["symbol"]))
            if fila and _guardar(con, fila):
                nuevas += 1
        con.commit()
        con.close()
        return nuevas
    except Exception as e:
        print(f"[operaciones] no se pudo registrar {db_file}: {type(e).__name__}: {e}", flush=True)
        return 0


def rellenar(mt5=None, dias=None):
    """Recorre las BBDD de todos los agentes. Con mt5=None usa la tabla `trades`. Devuelve (guardadas, omitidas)."""
    con = registro.central()
    tabla(con)
    clases = _clases(con)
    desde = (datetime.now() - timedelta(days=dias)).strftime("%Y-%m-%d") if dias else None
    guardadas = omitidas = 0
    for db_file in sorted(DATA.glob("llm_trader_Agente_*.db")):
        for r in _decisiones(db_file):
            if desde and r["ts"] < desde:
                continue
            clase = clases.get(r["symbol"])
            fila = (_fila_mt5(mt5, r, _nombre(db_file), clase) if mt5 is not None
                    else _fila_trades(con, r, _nombre(db_file), clase))
            if fila and _guardar(con, fila):
                guardadas += 1
            else:
                omitidas += 1
    con.commit()
    con.close()
    return guardadas, omitidas


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Rellena llm_operaciones (una fila por operacion cerrada)")
    ap.add_argument("--rellenar", action="store_true")
    ap.add_argument("--sin-mt5", dest="sin_mt5", action="store_true", help="usar la tabla trades (sin salida ni MAE/MFE)")
    ap.add_argument("--dias", type=int, default=None)
    args = ap.parse_args()
    if not args.rellenar:
        ap.print_help()
        return 0
    if args.sin_mt5:
        print("guardadas %d, omitidas %d" % rellenar(None, args.dias))
        return 0
    import MetaTrader5 as mt5
    if not mt5.initialize():
        print(f"MT5 no disponible: {mt5.last_error()}", file=sys.stderr)
        return 1
    try:
        print("guardadas %d, omitidas %d" % rellenar(mt5, args.dias))
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
