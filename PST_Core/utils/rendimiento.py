"""
Operaciones cerradas para la pantalla de rendimiento, en un solo formato.

Dos fuentes en la misma BBDD:
  - llm_operaciones: operaciones de los agentes LLM. Traen R realizado, MAE/MFE y motivo de salida.
  - trades: el resto (EAs, manuales y sincronizadas desde MT5). Sin R ni MAE/MFE.
Una operacion de agente aparece una sola vez, con fuente LLM (se descarta su fila de trades por ticket).
"""
import sqlite3
from datetime import datetime

CLASES = {
    "indice": "Índices", "INDEX": "Índices",
    "accion_eeuu": "Acciones", "accion_eu": "Acciones", "EQUITIES": "Acciones",
    "cripto": "Cripto", "CRYPTO": "Cripto",
    "materias": "Materias", "METAL": "Materias",
    "forex": "Forex", "FOREX": "Forex",
}
FUENTES_EXTERNAS = {"AUTO_SYNC": "Externa (sync)", None: "Manual", "": "Manual"}


def _iso(s):
    """'2026.06.15 09:32:11' o '2026-06-15 09:32:11' -> datetime. None si no se puede leer."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s[:10].replace(".", "-") + s[10:])
    except ValueError:
        return None


def _minutos(entrada, salida):
    if entrada and salida:
        return round((salida - entrada).total_seconds() / 60, 1)
    return None


def _fila_llm(r):
    return {
        "ticket": r["ticket"], "symbol": r["symbol"], "clase": CLASES.get(r["clase"], "Otros"), "fuente": "LLM",
        "agente": r["agente"], "lado": r["lado"], "abierta": r["abierta"], "cerrada": None,
        "neto": r["neto"], "coste": round((r["comision"] or 0) + (r["swap"] or 0), 2),
        "r": r["r_real"], "mae_r": r["mae_r"], "mfe_r": r["mfe_r"], "minutos": r["minutos"],
        "motivo": r["motivo_salida"],
    }


def _fila_trade(r):
    entrada, salida = _iso(r["time_in"]), _iso(r["time_out"])
    fuente = r["strategy_name"] or None
    return {
        "ticket": r["ticket"], "symbol": r["symbol"], "clase": _clase_de(r["symbol"]),
        "fuente": FUENTES_EXTERNAS.get(fuente, fuente), "agente": None, "lado": None,
        "abierta": r["time_in"], "cerrada": r["time_out"],
        "neto": r["profit"], "coste": round((r["commission"] or 0) + (r["swap"] or 0), 2),
        "r": None, "mae_r": None, "mfe_r": None, "minutos": _minutos(entrada, salida), "motivo": None,
    }


def _clase_de(symbol):
    try:
        from PST_Core.utils.tech_utils import get_asset_class
        return CLASES.get(get_asset_class(symbol), "Otros")
    except Exception:
        return "Otros"


def leer_operaciones(db_path, limite=5000):
    """Operaciones cerradas, mas recientes primero. Sin tablas o sin datos devuelve lista vacia."""
    con = sqlite3.connect(db_path, timeout=15)
    con.row_factory = sqlite3.Row
    try:
        llm = []
        try:
            llm = list(con.execute(
                "SELECT ticket, symbol, clase, agente, lado, abierta, neto, comision, swap, r_real, mae_r, mfe_r, "
                "minutos, motivo_salida FROM llm_operaciones WHERE neto IS NOT NULL ORDER BY abierta DESC LIMIT ?",
                (limite,)))
        except sqlite3.OperationalError:
            pass                                          # la tabla se crea al rellenar las operaciones
        tickets_llm = {r["ticket"] for r in llm}
        trades = con.execute(
            "SELECT ticket, symbol, profit, commission, swap, time_in, time_out, strategy_name FROM trades "
            "WHERE price_out > 0 AND time_out IS NOT NULL ORDER BY time_out DESC LIMIT ?", (limite,)).fetchall()
    finally:
        con.close()

    filas = [_fila_llm(r) for r in llm]
    filas += [_fila_trade(r) for r in trades if r["ticket"] not in tickets_llm]
    filas.sort(key=lambda f: f["cerrada"] or f["abierta"] or "", reverse=True)
    return filas[:limite]
