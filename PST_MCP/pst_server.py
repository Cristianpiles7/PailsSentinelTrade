import asyncio
import sqlite3
import os
import json
import logging
from typing import List, Dict, Any
from dotenv import load_dotenv
import google.generativeai as genai
import MetaTrader5 as mt5
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Configuración de Rutas (v1.7.5 LIVE)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(REPO_ROOT)
DB_PATH = os.path.join(PROJECT_ROOT, "PST_Core", "data", "pst_trading.db")

# Cargar variables de entorno
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
IS_AI_ENABLED = bool(GEMINI_API_KEY)

# Motor de IA de Vanguardia (v1.7.5 Lite)
if IS_AI_ENABLED:
    genai.configure(api_key=GEMINI_API_KEY)
    ai_model = genai.GenerativeModel('gemini-3.1-flash-lite-preview')

logging.basicConfig(level=logging.ERROR)
server = Server("PST-Intelligence-Hub")

def query_db(query: str, params: tuple = ()) -> List[Dict[str, Any]]:
    if not os.path.exists(DB_PATH):
        return [{"error": "DB no encontrada"}]
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        result = [dict(row) for row in rows]
        conn.close()
        return result
    except Exception as e:
        return [{"error": str(e)}]

def get_live_account_info():
    """Obtiene datos vivos de MetaTrader 5."""
    if not mt5.initialize():
        return None
    acc = mt5.account_info()
    mt5.shutdown()
    if acc:
        return {
            "balance": acc.balance,
            "equity": acc.equity,
            "profit": acc.profit,
            "margin_free": acc.margin_free
        }
    return None

@server.list_tools()
async def handle_list_tools() -> List[Tool]:
    tools = [
        Tool(name="get_trading_stats", description="Estadísticas LIVE: Profit Realizado vs Flotante MT5.", input_schema={"type": "object", "properties": {"limit": {"type": "integer", "default": 100}}}),
        Tool(name="get_drawdown_report", description="Análisis de pérdidas por símbolo.", input_schema={"type": "object", "properties": {}}),
        Tool(name="get_symbol_matrix", description="Ver configuración de riesgo de un par.", input_schema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}),
        Tool(name="list_active_trades", description="Ver operaciones abiertas en curso (Tickets MT5).", input_schema={"type": "object", "properties": {}})
    ]
    if IS_AI_ENABLED:
        tools.append(Tool(name="get_ai_trading_advice", description="Sentinel IA: Análisis del riesgo y cuenta REAL-TIME.", input_schema={"type": "object", "properties": {}}))
    return tools

@server.call_tool()
async def handle_call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    live_acc = get_live_account_info()
    
    # --- NEW: Sincronización de historial al vuelo (v1.8.0) ---
    if live_acc:
        try:
            from datetime import datetime, time
            today_start_dt = datetime.combine(datetime.now().date(), time.min)
            if mt5.initialize():
                deals = mt5.history_deals_get(today_start_dt, datetime.now())
                if deals:
                    # Usar una conexión directa temporal para sincronizar
                    import aiosqlite
                    from PST_Core.models.database import PSTDatabase
                    db_temp = PSTDatabase(DB_PATH)
                    await db_temp.sync_mt5_history(deals)
        except Exception as e:
            logging.error(f"⚠️ Error sincronización Sentinel: {e}")

    if name == "get_ai_trading_advice":
        all_trades = query_db("SELECT symbol, type, profit, price_out, time_out FROM trades ORDER BY id DESC LIMIT 50")
        strat_config = query_db("SELECT symbol, strategy_name, risk_value FROM symbol_strategies LIMIT 15")
        closed = [t for t in all_trades if t['time_out']]
        open_tr = [t for t in all_trades if not t['time_out']]
        
        prompt = (
            "Actúa como un experto en gestión de capital y riesgo institucional.\n"
            f"ESTADO CUENTA MT5 (LIVE): {json.dumps(live_acc) if live_acc else 'MT5 Desconectado'}\n"
            f"DATOS RECIENTES (DB): {json.dumps(all_trades[:10])}\n"
            f"CONFIGURACIÓN MATRIX: {json.dumps(strat_config)}\n\n"
            "MISION v1.7.5:\n"
            "1. Informa el 'Profit Vivo' (Flotante) que ves en los datos de LIVE con el nombre técnico de MT5.\n"
            "2. Si el profit de LIVE es negativo, advierte sobre la exposición y el margen libre.\n"
            "3. No digas que el beneficio es 0 si MT5 reporta un valor flotante.\n"
            "4. Sé muy directo sobre el estado de la cuenta."
        )
        try:
            response = ai_model.generate_content(prompt)
            return [TextContent(type="text", text=f"🧠 SENTINEL IA v1.7.5 (LIVE):\n\n{response.text}")]
        except Exception as e:
            return [TextContent(type="text", text=str(e))]

    if name == "get_trading_stats":
        trades = query_db("SELECT profit, time_out FROM trades ORDER BY id DESC LIMIT 100")
        closed = [t for t in trades if t['time_out']]
        open_count = len([t for t in trades if not t['time_out']])
        total_realizado = sum(t['profit'] for t in closed if t['profit'])
        
        live_info = ""
        if live_acc:
            live_info = (
                f"- BENEFICIO VIVO (MT5): {live_acc['profit']:.2f}€ ⚠️\n"
                f"- Equidad Actual: {live_acc['equity']:.2f}€\n"
                f"- Margen Libre: {live_acc['margin_free']:.2f}€"
            )
        else:
            live_info = "- MetaTrader 5: DESCONECTADO (Muestra balance estático)"
            
        res_text = (
            f"📊 REPORTE SENTINEL v1.7.5 (Profit Vivo):\n"
            f"---------------------------------\n"
            f"- Beneficio Realizado: {total_realizado:.2f}€\n"
            f"{live_info}\n"
            f"- Operaciones Abiertas: {open_count}\n"
            f"---------------------------------\n"
            f"⚠️ El balance solo se fija al cerrar. Tu resultado está oscilando ahora mismo en MT5."
        )
        return [TextContent(type="text", text=res_text)]

    elif name == "list_active_trades":
        active = query_db("SELECT id, symbol, type, volume, price_in FROM trades WHERE time_out = '' OR time_out IS NULL OR price_out = 0.0")
        return [TextContent(type="text", text=json.dumps(active, indent=2) if active else "No hay operaciones abiertas.")]

    return [TextContent(type="text", text="Herramienta no implementada en v1.7.5")]

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
