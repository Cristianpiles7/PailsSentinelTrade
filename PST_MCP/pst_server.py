"""
Servidor MCP de PST — la superficie de herramientas de la flota de agentes.

Es lo que los subagentes de Claude Code usan para leer el estado real del sistema y para
escribir propuestas. Sin esto improvisan rutas y comandos, que es el fallo clasico.
Ver manual_docs/PLAN_AGENTES.md seccion 6a.

HISTORIAL IMPORTANTE (2026-10-20): este servidor NUNCA habia funcionado. Tres fallos:

  1. No estaba registrado como servidor MCP en ningun sitio (ahora: .mcp.json del repo).
  2. Construia los Tool() con `input_schema=`, pero el campo del SDK es `inputSchema`:
     lanzaba ValidationError en la primera llamada a tools/list, asi que ninguna de sus
     tools se habia ejecutado jamas.
  3. `get_drawdown_report` y `get_symbol_matrix` estaban declaradas pero sin implementar;
     caian en el "Herramienta no implementada" del final.

Y un cuarto problema de diseno que se corrige aqui: el handler disparaba una sincronizacion
completa de MT5 -> BBDD en CADA llamada a CUALQUIER tool. Un agente que hace 20 consultas
provocaba 20 sincronizaciones con efectos secundarios sobre la BBDD viva. Ahora la
sincronizacion es una tool explicita (`sync_mt5_today`) que el agente invoca si la quiere.

Todas las tools son de SOLO LECTURA salvo dos, que estan marcadas en su descripcion:
`propose_change` (inserta en proposals, estado inicial 'propuesta', nada se aplica) y
`sync_mt5_today`. Ningun agente puede mover estados de propuesta ni enviar ordenes.
"""
import asyncio
import json
import logging
import os
import subprocess
import sqlite3
from typing import Any, Dict, List

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# ── Rutas ─────────────────────────────────────────────────────────────────────
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# DB_PATH sale de PST_Core.config, fuente unica de verdad: resuelve la BBDD probando
# varios niveles de directorio y acertar a mano aqui es como se desincronizan las cosas.
try:
    import sys
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    from PST_Core.config import DB_PATH
except Exception:  # pragma: no cover — fallback si el import falla fuera del repo
    DB_PATH = os.path.join(os.path.dirname(REPO_ROOT), "PST_Core", "data", "pst_trading.db")

load_dotenv(os.path.join(REPO_ROOT, ".env"))

# Gemini queda opcional y solo para la tool de consejo heredada. La flota es Claude.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
IS_AI_ENABLED = bool(GEMINI_API_KEY)
ai_model = None
if IS_AI_ENABLED:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        ai_model = genai.GenerativeModel("gemini-flash-latest")
    except Exception:
        IS_AI_ENABLED = False

# Logging a stderr: stdout es el transporte del protocolo MCP y escribir ahi lo rompe.
logging.basicConfig(level=logging.INFO, stream=__import__("sys").stderr)
logger = logging.getLogger("PST-MCP")

server = Server("PST-Intelligence-Hub")

# Labs que `run_lab` puede ejecutar. Lista blanca cerrada a proposito: una tool que
# aceptase una ruta libre seria ejecucion de codigo arbitrario a traves del MCP.
ALLOWED_LABS = {
    "reconcile":            "Tools/pst_reconcile.py",
    "bt_lab":               "Tools/pst_bt_lab.py",
    "range_lab":            "Tools/pst_range_lab.py",
    "diag":                 "Tools/pst_diag.py",
    "audit_precision":      "audit_precisionscalping.py",
    "export_loss_context":  "PST_Core/tools/export_loss_context.py",
}
LAB_TIMEOUT_S = 900


# ── Acceso a BBDD ─────────────────────────────────────────────────────────────
def query_db(query: str, params: tuple = ()) -> List[Dict[str, Any]]:
    """Lectura en modo READ-ONLY (uri=...?mode=ro): el servidor no puede alterar datos
    por accidente desde una tool de consulta."""
    if not os.path.exists(DB_PATH):
        return [{"error": f"BBDD no encontrada en {DB_PATH}"}]
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=15)
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(query, params).fetchall()]
        con.close()
        return rows
    except Exception as e:
        return [{"error": str(e)}]


def _mt5():
    """MT5 se importa de forma perezosa: el paquete solo existe en Windows con el
    terminal instalado, y la mayoria de las tools no lo necesitan."""
    try:
        import MetaTrader5 as mt5
        return mt5
    except Exception:
        return None


def get_live_account_info():
    mt5 = _mt5()
    if not mt5 or not mt5.initialize():
        return None
    acc = mt5.account_info()
    result = None
    if acc:
        result = {
            "balance": acc.balance, "equity": acc.equity, "profit": acc.profit,
            "margin_free": acc.margin_free, "server": acc.server, "login": acc.login,
        }
    mt5.shutdown()
    return result


def _text(payload) -> List[TextContent]:
    if isinstance(payload, str):
        return [TextContent(type="text", text=payload)]
    return [TextContent(type="text", text=json.dumps(payload, indent=2, ensure_ascii=False, default=str))]


# ── Catalogo de tools ─────────────────────────────────────────────────────────
@server.list_tools()
async def handle_list_tools() -> List[Tool]:
    tools = [
        # --- Estado y cuenta ---
        Tool(
            name="get_trading_stats",
            description="Resumen de cuenta: profit realizado en BBDD vs flotante vivo de MT5, y numero de operaciones abiertas.",
            inputSchema={"type": "object", "properties": {"limit": {"type": "integer", "default": 100}}},
        ),
        Tool(
            name="list_active_trades",
            description="Operaciones abiertas segun la BBDD (tickets MT5), con simbolo, tipo, volumen y precio de entrada.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_drawdown_report",
            description="Profit agregado, numero de operaciones y win rate por simbolo, ordenado de peor a mejor. Para encontrar donde se pierde dinero.",
            inputSchema={"type": "object", "properties": {"days": {"type": "integer", "description": "Ventana en dias; omitir para todo el historico"}}},
        ),
        Tool(
            name="get_symbol_matrix",
            description="Configuracion de riesgo de un simbolo (lote, multiplicadores SL/TP, umbral de score, min_rr) y sus estrategias asignadas.",
            inputSchema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]},
        ),

        # --- Datos crudos para analisis ---
        Tool(
            name="read_trades",
            description="Operaciones de la tabla trades con filtros. Es la fuente para auditar lo que hizo el bot de verdad.",
            inputSchema={"type": "object", "properties": {
                "symbol": {"type": "string"},
                "strategy": {"type": "string"},
                "days": {"type": "integer", "description": "Solo cierres de los ultimos N dias"},
                "limit": {"type": "integer", "default": 200},
            }},
        ),
        Tool(
            name="read_signal_logs",
            description="Senales evaluadas (incluidas las BLOQUEADAS, con blocked_reason). Para ver por que el bot no entro, no solo lo que hizo.",
            inputSchema={"type": "object", "properties": {
                "symbol": {"type": "string"},
                "strategy": {"type": "string"},
                "only_blocked": {"type": "boolean", "default": False},
                "days": {"type": "integer"},
                "limit": {"type": "integer", "default": 200},
            }},
        ),
        Tool(
            name="get_db_schema",
            description="Tablas y columnas de la BBDD. Llamala ANTES de escribir un query_select para no inventarte nombres de columna.",
            inputSchema={"type": "object", "properties": {"table": {"type": "string", "description": "Omitir para ver todas"}}},
        ),
        Tool(
            name="query_select",
            description="SELECT libre de solo lectura sobre la BBDD. Solo se admite una sentencia que empiece por SELECT o WITH. Usa get_db_schema primero.",
            inputSchema={"type": "object", "properties": {
                "sql": {"type": "string"},
                "limit": {"type": "integer", "default": 200, "description": "Se aplica si el SQL no trae su propio LIMIT"},
            }, "required": ["sql"]},
        ),

        # --- Jueces fieles ---
        Tool(
            name="run_lab",
            description=(
                "Ejecuta uno de los jueces/labs del proyecto y devuelve su salida. "
                "Nombres validos: " + ", ".join(sorted(ALLOWED_LABS)) + ". "
                f"Timeout {LAB_TIMEOUT_S}s. Es la unica via legitima de obtener un veredicto fiel."
            ),
            inputSchema={"type": "object", "properties": {
                "lab": {"type": "string", "enum": sorted(ALLOWED_LABS)},
                "args": {"type": "array", "items": {"type": "string"}, "description": "Argumentos CLI, p.ej. ['--symbol','EURUSD','--days','20']"},
            }, "required": ["lab"]},
        ),

        # --- Flota: propuestas ---
        Tool(
            name="list_proposals",
            description="Propuestas ya registradas, mas recientes primero. Consultala antes de proponer para no duplicar lo que ya esta en la bandeja.",
            inputSchema={"type": "object", "properties": {
                "state": {"type": "string", "enum": ["propuesta", "shadow", "aprobada", "viva", "rechazada", "revertida"]},
                "limit": {"type": "integer", "default": 50},
            }},
        ),
        Tool(
            name="propose_change",
            description=(
                "ESCRIBE: registra una propuesta de cambio en estado 'propuesta'. NO aplica nada: "
                "la decision es del usuario desde la bandeja. Adjunta SIEMPRE evidencia y, si tienes "
                "una corrida del juez, su judge_delta_eur; una propuesta sin evidencia se descarta."
            ),
            inputSchema={"type": "object", "properties": {
                "kind": {"type": "string", "description": "disable_symbol | enable_symbol | adjust_param | new_hypothesis | retire_deployment"},
                "target": {"type": "string", "description": "Simbolo, estrategia o id de despliegue (p.ej. 'ETHUSD' o 'H25')"},
                "current_json": {"type": "string", "description": "Estado actual en JSON, necesario para poder revertir"},
                "proposed_json": {"type": "string", "description": "Estado propuesto en JSON"},
                "evidence_json": {"type": "string", "description": "Datos que lo respaldan en JSON (trades, WR, corrida del juez...)"},
                "judge_delta_eur": {"type": "number", "description": "Delta en euros que predice el juez fiel"},
                "rationale": {"type": "string", "description": "Por que, en una o dos frases"},
                "agent_run_id": {"type": "integer", "description": "Omitir: se resuelve a la ejecucion en curso"},
            }, "required": ["kind", "target", "rationale"]},
        ),

        # --- Mantenimiento explicito ---
        Tool(
            name="sync_mt5_today",
            description="ESCRIBE: sincroniza los deals de hoy de MT5 a la BBDD. Antes esto ocurria de forma implicita en cada llamada a cualquier tool; ahora es explicito. Llamalo solo si necesitas datos de hoy al minuto.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]

    if IS_AI_ENABLED:
        tools.append(Tool(
            name="get_ai_trading_advice",
            description="Consejo de gestion de capital generado con Gemini (heredado; la flota de agentes es Claude). Consume cupo de Gemini.",
            inputSchema={"type": "object", "properties": {}},
        ))
    return tools


# ── Implementaciones ──────────────────────────────────────────────────────────
def _days_clause(days, column="time_out"):
    if not days:
        return "", ()
    return f" AND {column} >= datetime('now', ?)", (f"-{int(days)} days",)


async def _tool_get_trading_stats(args):
    limit = int(args.get("limit", 100))
    trades = query_db("SELECT profit, time_out FROM trades ORDER BY id DESC LIMIT ?", (limit,))
    if trades and "error" in trades[0]:
        return trades[0]
    closed = [t for t in trades if t["time_out"]]
    return {
        "profit_realizado_bbdd": round(sum(t["profit"] or 0 for t in closed), 2),
        "operaciones_cerradas": len(closed),
        "operaciones_abiertas": len(trades) - len(closed),
        "cuenta_mt5_viva": get_live_account_info() or "MT5 desconectado",
        "nota": "El realizado sale de la BBDD; el flotante solo existe en MT5 y oscila.",
    }


async def _tool_get_drawdown_report(args):
    clause, params = _days_clause(args.get("days"))
    return query_db(f"""
        SELECT symbol,
               ROUND(SUM(profit), 2)                                        AS profit_total,
               COUNT(*)                                                     AS operaciones,
               ROUND(100.0 * SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
               ROUND(AVG(profit), 2)                                        AS profit_medio
          FROM trades
         WHERE time_out IS NOT NULL AND time_out != ''{clause}
         GROUP BY symbol
         ORDER BY profit_total ASC
    """, params)


async def _tool_get_symbol_matrix(args):
    symbol = args["symbol"]
    return {
        "config": query_db("SELECT * FROM symbols_config WHERE symbol = ?", (symbol,)),
        "estrategias": query_db("SELECT * FROM symbol_strategies WHERE symbol = ?", (symbol,)),
    }


async def _tool_read_trades(args):
    sql = "SELECT * FROM trades WHERE 1=1"
    params = []
    if args.get("symbol"):
        sql += " AND symbol = ?"; params.append(args["symbol"])
    if args.get("strategy"):
        sql += " AND strategy_name = ?"; params.append(args["strategy"])
    if args.get("days"):
        sql += " AND time_out >= datetime('now', ?)"; params.append(f"-{int(args['days'])} days")
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(args.get("limit", 200)))
    return query_db(sql, tuple(params))


async def _tool_read_signal_logs(args):
    sql = "SELECT * FROM signal_logs WHERE 1=1"
    params = []
    if args.get("symbol"):
        sql += " AND symbol = ?"; params.append(args["symbol"])
    if args.get("strategy"):
        sql += " AND strategy = ?"; params.append(args["strategy"])
    if args.get("only_blocked"):
        sql += " AND blocked_reason IS NOT NULL AND blocked_reason != ''"
    if args.get("days"):
        sql += " AND timestamp >= datetime('now', ?)"; params.append(f"-{int(args['days'])} days")
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(args.get("limit", 200)))
    return query_db(sql, tuple(params))


async def _tool_get_db_schema(args):
    table = args.get("table")
    if table:
        return {table: query_db("SELECT name, type FROM pragma_table_info(?)", (table,))}
    tablas = query_db("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    return {
        t["name"]: [c["name"] for c in query_db("SELECT name FROM pragma_table_info(?)", (t["name"],))]
        for t in tablas if "name" in t
    }


async def _tool_query_select(args):
    sql = (args.get("sql") or "").strip().rstrip(";").strip()
    low = sql.lower()
    # Guardas: una sola sentencia y solo lectura. El modo ?mode=ro de query_db ya impide
    # escribir, pero rechazar aqui da un error claro en vez de un fallo de SQLite.
    if not (low.startswith("select") or low.startswith("with")):
        return {"error": "Solo se admiten sentencias SELECT o WITH"}
    if ";" in sql:
        return {"error": "Solo una sentencia por llamada"}
    if " limit " not in low:
        sql += f" LIMIT {int(args.get('limit', 200))}"
    return query_db(sql)


async def _tool_run_lab(args):
    lab = args.get("lab")
    if lab not in ALLOWED_LABS:
        return {"error": f"Lab no permitido: {lab}", "validos": sorted(ALLOWED_LABS)}
    script = os.path.join(REPO_ROOT, ALLOWED_LABS[lab])
    if not os.path.isfile(script):
        return {"error": f"El script no existe: {script}"}

    cmd = ["python", script] + [str(a) for a in (args.get("args") or [])]
    try:
        proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True,
                              timeout=LAB_TIMEOUT_S, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"error": f"'{lab}' excedio el timeout de {LAB_TIMEOUT_S}s"}
    # La salida se recorta: un lab verboso puede devolver megas y agotar el contexto del agente.
    return {
        "lab": lab, "comando": " ".join(cmd), "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-20000:],
        "stderr": (proc.stderr or "")[-4000:],
    }


_db_ready = False


async def _get_db():
    """
    PSTDatabase con las tablas garantizadas. initialize() es idempotente pero corre todas
    las migraciones, asi que se hace una sola vez por proceso.

    Hace falta porque agent_runs/proposals pueden no existir aun: se crean al arrancar PST,
    y un agente puede consultar antes de que el usuario haya reiniciado la app.
    """
    global _db_ready
    from PST_Core.models.database import PSTDatabase
    db = PSTDatabase(db_path=DB_PATH)
    if not _db_ready:
        await db.initialize()
        _db_ready = True
    return db


async def _tool_list_proposals(args):
    db = await _get_db()
    return await db.list_proposals(state=args.get("state"), limit=int(args.get("limit", 50)))


async def _tool_propose_change(args):
    db = await _get_db()

    run_id = args.get("agent_run_id")
    if not run_id:
        # Se cuelga de la ejecucion abierta mas reciente. Si el runner no abrio ninguna,
        # se crea una sobre la marcha para que la propuesta nunca quede huerfana.
        abiertas = query_db("SELECT id FROM agent_runs WHERE status = 'running' ORDER BY id DESC LIMIT 1")
        run_id = abiertas[0]["id"] if abiertas and "id" in abiertas[0] else await db.start_agent_run("mcp-directo")

    pid = await db.add_proposal(
        agent_run_id=run_id, kind=args["kind"], target=args["target"],
        current_json=args.get("current_json"), proposed_json=args.get("proposed_json"),
        evidence_json=args.get("evidence_json"), judge_delta_eur=args.get("judge_delta_eur"),
        rationale=args["rationale"],
    )
    return {
        "proposal_id": pid, "agent_run_id": run_id, "state": "propuesta",
        "nota": "Registrada. No se ha aplicado nada: la decision es del usuario desde la bandeja.",
    }


async def _tool_sync_mt5_today(args):
    mt5 = _mt5()
    if not mt5 or not mt5.initialize():
        return {"error": "MT5 no disponible o desconectado"}
    from datetime import datetime, time as dt_time
    desde = datetime.combine(datetime.now().date(), dt_time.min)
    deals = mt5.history_deals_get(desde, datetime.now())
    mt5.shutdown()
    if not deals:
        return {"sincronizados": 0, "nota": "Sin deals hoy"}
    db = await _get_db()
    synced, _ = await db.sync_mt5_history(deals)
    return {"sincronizados": synced, "deals_vistos": len(deals)}


async def _tool_get_ai_trading_advice(args):
    if not IS_AI_ENABLED or ai_model is None:
        return {"error": "Gemini no configurado (falta GEMINI_API_KEY)"}
    contexto = {
        "cuenta": get_live_account_info() or "MT5 desconectado",
        "por_simbolo": await _tool_get_drawdown_report({"days": 30}),
    }
    prompt = (
        "Eres un gestor de riesgo. Con estos datos reales, di en 5 lineas donde esta el "
        "riesgo y que harias. Sé directo y no inventes cifras.\n\n"
        + json.dumps(contexto, ensure_ascii=False, default=str)
    )
    try:
        return ai_model.generate_content(prompt).text
    except Exception as e:
        return {"error": str(e)}


async def _tool_list_active_trades(args):
    return query_db(
        "SELECT id, ticket, symbol, type, volume, price_in, strategy_name, time_in "
        "FROM trades WHERE time_out = '' OR time_out IS NULL OR price_out = 0.0"
    )


HANDLERS = {
    "get_trading_stats": _tool_get_trading_stats,
    "list_active_trades": _tool_list_active_trades,
    "get_drawdown_report": _tool_get_drawdown_report,
    "get_symbol_matrix": _tool_get_symbol_matrix,
    "read_trades": _tool_read_trades,
    "read_signal_logs": _tool_read_signal_logs,
    "get_db_schema": _tool_get_db_schema,
    "query_select": _tool_query_select,
    "run_lab": _tool_run_lab,
    "list_proposals": _tool_list_proposals,
    "propose_change": _tool_propose_change,
    "sync_mt5_today": _tool_sync_mt5_today,
    "get_ai_trading_advice": _tool_get_ai_trading_advice,
}


@server.call_tool()
async def handle_call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    """
    Despacho por diccionario. Antes era una cadena de if/elif donde dos tools declaradas
    (get_drawdown_report, get_symbol_matrix) no tenian rama y caian en silencio en el
    "no implementada" del final: con un dict, una tool declarada sin handler se nota.
    """
    handler = HANDLERS.get(name)
    if handler is None:
        return _text({"error": f"Tool desconocida: {name}", "disponibles": sorted(HANDLERS)})
    try:
        return _text(await handler(arguments or {}))
    except KeyError as e:
        return _text({"error": f"Falta el argumento obligatorio {e}"})
    except Exception as e:
        logger.exception("Fallo en la tool %s", name)
        return _text({"error": f"{type(e).__name__}: {e}"})


async def main():
    logger.info("PST-MCP arrancando | BBDD: %s | Gemini: %s", DB_PATH, IS_AI_ENABLED)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
