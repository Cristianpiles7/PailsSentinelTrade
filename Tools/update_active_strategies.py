#!/usr/bin/env python
"""
Actualiza manual_docs/ESTRATEGIAS_ACTIVAS.md con las estadisticas reales de cada estrategia
desplegada (operaciones, WR, beneficio neto, factor de beneficio, abiertas).

POR QUE UN SCRIPT Y NO UN AGENTE LLM
    Es aritmetica sobre el historial de MT5. Un agente costaria ~130k tokens por pasada solo
    en entrada fija (ver .agents/scheduler.py) para producir exactamente el mismo resultado
    que estas 100 lineas, y la cuota es la restriccion de diseno. Lo lanza el planificador
    de la flota cada noche, sin puerta de cuota.

FUENTE DE VERDAD
    Historial de deals de MT5 agrupado por magic number (no la BBDD: el sync a BBDD es
    reciente y tiene huecos). Una operacion = todos los deals de un position_id; el neto suma
    profit + comision + swap + fee, y una posicion cuenta como cerrada solo si ya no esta
    abierta y tiene algun deal de salida (los parciales se acumulan en la misma operacion).

SOLO se reescribe lo que hay entre los marcadores AUTO:START / AUTO:END. El resto del
documento (catalogo, notas, estado de graficos) es manual.

Uso:  python Tools/update_active_strategies.py
"""
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "manual_docs" / "ESTRATEGIAS_ACTIVAS.md"
START, END = "<!-- AUTO:START -->", "<!-- AUTO:END -->"
HISTORY_DAYS = 400

# magic -> (EA, simbolo, timeframe). Los EAs salen de MT5/despliegues.json (un EA por estrategia x simbolo x
# TF, nombres sin codigos de hipotesis); aqui solo se anaden los historicos y los experimentos de la flota LLM.
def _catalogo_eas():
    import json
    datos = json.loads((REPO_ROOT / "MT5" / "despliegues.json").read_text(encoding="utf-8"))["despliegues"]
    return {int(d["magic"]): (d["id"].removeprefix("PST_"), d["simbolo"], d["tf"]) for d in datos}


CATALOGO = {
    **_catalogo_eas(),
    101801: ("LiquiditySweep_EURUSD_M5 (variante retirada)", "EURUSD", "M5"),
    109001: ("Experimento: agente LLM BTCUSD (PST_LLM/llm_trader.py)", "BTCUSD", "M5"),
    109002: ("Experimento: agente LLM ETHUSD (PST_LLM/llm_trader.py)", "ETHUSD", "M5"),
    109003: ("Experimento: agente LLM XAUUSD (PST_LLM/llm_trader.py)", "XAUUSD", "M15"),
    109004: ("Experimento: agente LLM EURUSD (PST_LLM/llm_trader.py)", "EURUSD", "M15"),
    109005: ("Experimento: agente LLM US500.cash (PST_LLM/llm_trader.py)", "US500.cash", "M15"),
}


def _pst_conectar_mt5(mt5):
    """mt5.initialize() con la ruta del terminal configurado (nunca "el ultimo arrancado").
    Ver PST_Core/utils/terminal_mt5.py y MT5/DESARROLLO_EAS.md (incidente 2026-11-04)."""
    try:
        from PST_Core.utils.terminal_mt5 import conectar
    except ImportError:
        import os as _os
        import sys as _sys
        _d = _os.path.dirname(_os.path.abspath(__file__))
        while not _os.path.isdir(_os.path.join(_d, "PST_Core")) and _os.path.dirname(_d) != _d:
            _d = _os.path.dirname(_d)
        _sys.path.insert(0, _d)
        from PST_Core.utils.terminal_mt5 import conectar
    return conectar(mt5)


def _agrupar_operaciones(deals, abiertas):
    """position_id -> {magic, symbol, net, cerrada, t_out}"""
    ops = {}
    for d in deals:
        if d.type not in (0, 1) or not d.position_id:   # 0/1 = BUY/SELL; fuera balance/credito
            continue
        o = ops.setdefault(d.position_id, {"magic": d.magic, "symbol": d.symbol,
                                           "net": 0.0, "salida": False, "t_out": 0})
        o["net"] += d.profit + d.commission + d.swap + getattr(d, "fee", 0.0)
        if d.entry in (1, 2, 3):                        # OUT / INOUT / OUT_BY
            o["salida"] = True
            o["t_out"] = max(o["t_out"], d.time)
        if d.magic:
            o["magic"] = d.magic
    for pid, o in ops.items():
        o["cerrada"] = o["salida"] and pid not in abiertas
    return ops


def _stats(ops):
    cerradas = [o for o in ops if o["cerrada"]]
    n = len(cerradas)
    gan = [o["net"] for o in cerradas if o["net"] > 0]
    per = [o["net"] for o in cerradas if o["net"] <= 0]
    neto = sum(o["net"] for o in cerradas)
    return {
        "n": n,
        "wr": (len(gan) / n * 100) if n else None,
        "neto": neto,
        "pf": (sum(gan) / -sum(per)) if per and sum(per) < 0 else None,
        "medio": (neto / n) if n else None,
    }


def _fmt(v, f="{:.1f}", vacio="—"):
    return vacio if v is None else f.format(v)


def _tabla(filas, mt5):
    ahora = datetime.now()
    abiertas = {p.ticket: p for p in (mt5.positions_get() or [])}
    desde = ahora - timedelta(days=HISTORY_DAYS)
    deals = mt5.history_deals_get(desde, ahora + timedelta(days=1)) or []
    ops = _agrupar_operaciones(deals, set(abiertas))
    cuenta = mt5.account_info()

    por_magic = defaultdict(list)
    for pid, o in ops.items():
        por_magic[o["magic"]].append(o)
    abiertas_por_magic = defaultdict(list)
    for p in abiertas.values():
        abiertas_por_magic[p.magic].append(p)

    corte7 = int((ahora - timedelta(days=7)).timestamp())
    primera = min((d.time for d in deals), default=None)

    out = []
    out.append(f"_Actualizado: **{ahora:%Y-%m-%d %H:%M}** · cuenta `{cuenta.login}` ({cuenta.server}) · "
               f"balance {cuenta.balance:.2f} {cuenta.currency} · equity {cuenta.equity:.2f}_  ")
    if primera:
        out.append(f"_Historial disponible desde {datetime.fromtimestamp(primera):%Y-%m-%d}. "
                   f"Con pocas operaciones el WR no significa nada: mirar la columna N antes de fiarse._")
    out.append("")
    out.append("| Estrategia | Magic | Símbolos | TF | N cerradas | WR % | Neto € | PF | Medio €/op | N 7d | Neto 7d € | Abiertas | Flotante € | Última op |")
    out.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")

    tot = {"n": 0, "neto": 0.0}
    for magic, (nombre, simbolos, tf) in CATALOGO.items():
        lista = por_magic.get(magic, [])
        s = _stats(lista)
        s7 = _stats([o for o in lista if o["cerrada"] and o["t_out"] >= corte7])
        ab = abiertas_por_magic.get(magic, [])
        ultima = max((o["t_out"] for o in lista if o["cerrada"]), default=None)
        out.append(
            f"| {nombre} | {magic} | {simbolos} | {tf} | {s['n']} | {_fmt(s['wr'])} | {_fmt(s['neto'], '{:+.2f}')} | "
            f"{_fmt(s['pf'], '{:.2f}')} | {_fmt(s['medio'], '{:+.2f}')} | {s7['n']} | {_fmt(s7['neto'], '{:+.2f}')} | "
            f"{len(ab)} | {sum(p.profit for p in ab):+.2f} | "
            f"{datetime.fromtimestamp(ultima):%Y-%m-%d} |" if ultima else
            f"| {nombre} | {magic} | {simbolos} | {tf} | {s['n']} | {_fmt(s['wr'])} | {_fmt(s['neto'], '{:+.2f}')} | "
            f"{_fmt(s['pf'], '{:.2f}')} | {_fmt(s['medio'], '{:+.2f}')} | {s7['n']} | {_fmt(s7['neto'], '{:+.2f}')} | "
            f"{len(ab)} | {sum(p.profit for p in ab):+.2f} | — |")
        tot["n"] += s["n"]
        tot["neto"] += s["neto"]

    conocidos = set(CATALOGO)
    ajenos = [(m, l) for m, l in por_magic.items() if m not in conocidos and any(o["cerrada"] for o in l)]
    for m, l in ajenos:
        s = _stats(l)
        out.append(f"| ⚠ magic desconocido | {m} | — | — | {s['n']} | {_fmt(s['wr'])} | {_fmt(s['neto'], '{:+.2f}')} | — | — | — | — | — | — | — |")
        tot["n"] += s["n"]
        tot["neto"] += s["neto"]

    out.append(f"| **TOTAL** | | | | **{tot['n']}** | | **{tot['neto']:+.2f}** | | | | | {len(abiertas)} | {sum(p.profit for p in abiertas.values()):+.2f} | |")

    # Desglose por simbolo (una estrategia puede operar varios)
    out.append("")
    out.append("**Desglose por símbolo**")
    out.append("")
    out.append("| Estrategia | Símbolo | N | WR % | Neto € |")
    out.append("|---|---|---:|---:|---:|")
    for magic, (nombre, _, _) in CATALOGO.items():
        por_sym = defaultdict(list)
        for o in por_magic.get(magic, []):
            por_sym[o["symbol"]].append(o)
        for sym, l in sorted(por_sym.items()):
            s = _stats(l)
            if s["n"]:
                out.append(f"| {nombre} | {sym} | {s['n']} | {_fmt(s['wr'])} | {_fmt(s['neto'], '{:+.2f}')} |")
    return "\n".join(out)


def main():
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("Falta el paquete MetaTrader5", file=sys.stderr)
        return 1
    if not _pst_conectar_mt5(mt5):
        print(f"MT5 no disponible: {mt5.last_error()}", file=sys.stderr)
        return 1
    try:
        bloque = _tabla(None, mt5)
    finally:
        mt5.shutdown()

    if not DOC.is_file():
        print(f"No existe {DOC}", file=sys.stderr)
        return 1
    texto = DOC.read_text(encoding="utf-8")
    if START not in texto or END not in texto:
        print("Faltan los marcadores AUTO:START / AUTO:END en el documento", file=sys.stderr)
        return 1
    nuevo = re.sub(re.escape(START) + r".*?" + re.escape(END),
                   lambda _: f"{START}\n{bloque}\n{END}", texto, flags=re.S)
    DOC.write_text(nuevo, encoding="utf-8")
    print(f"[estrategias] {DOC.name} actualizado ({datetime.now():%Y-%m-%d %H:%M})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
