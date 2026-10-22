from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from typing import List
from pathlib import Path
import json
import os
import sys
import MetaTrader5 as mt5
import asyncio

# Añadir el directorio raíz al path para poder importar PST_Core
if hasattr(sys, '_MEIPASS'):
    # En el ejecutable (PyInstaller), la raíz es sys._MEIPASS
    project_root = sys._MEIPASS
else:
    # En desarrollo, subimos un nivel desde PST_API/
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if project_root not in sys.path:
    sys.path.insert(0, project_root)

from PST_Core.models.database import PSTDatabase
from PST_Core.portfolio.manager import PortfolioManager
from PST_API.schemas import AccountStatus, Trade, SymbolStatus, APIResponse, OHLCBar, ConfigUpdate, ManualOrder, StrategyBreakdown, BotConfigUpdate, PerformanceMetrics, EquityPoint, StrategyPerformance, LoginRequest, RiskProfileRequest, TradeNoteUpdate
from PST_API.habblaud_proxy import router as habblaud_router, start_mirror_in_thread, ensure_habblaud_running, MIRROR_PORT
from PST_API.agents_api import router as agents_router
from PST_API.llm_api import router as llm_router
from PST_API.puntuador_api import router as puntuador_router
from pydantic import BaseModel
from PST_Core.engine.executor import PSTExecutor
import pandas_ta as ta
from dotenv import load_dotenv
import logging
from PST_Core.config import DB_PATH

LLM_DIR = str(Path(__file__).resolve().parents[1] / "PST_LLM")
if LLM_DIR not in sys.path:
    sys.path.insert(0, LLM_DIR)
import rutas  # noqa: E402  (PST_LLM/rutas.py: datos fuera de _MEIPASS en el exe)
PUNTUADOR_JSON = rutas.AGENTS_DATA_DIR / "puntuador.json"

# --- WEBSOCKET MANAGER (FASE 46) ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass

manager = ConnectionManager()

# --- WS LOG HANDLER ---
class WSLogHandler(logging.Handler):
    def __init__(self, manager: ConnectionManager):
        super().__init__()
        self.manager = manager

    def emit(self, record):
        try:
            msg = self.format(record)
            payload = {
                "type": "log",
                "level": record.levelname,
                "message": msg,
                "source": record.name,
                "timestamp": record.created
            }
            if asyncio.get_event_loop().is_running():
                asyncio.create_task(self.manager.broadcast(payload))
        except:
            pass

ws_handler = WSLogHandler(manager)
ws_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("PST_API")

load_dotenv() # Carga variables desde el archivo .env

# Sin valor por defecto a proposito. Antes habia uno hardcodeado aqui, y este repo es
# PUBLICO: cualquiera podia leer la contrasena maestra del panel en GitHub, ademas de
# quedar en el historico de varios commits. Ahora falla cerrado: si AUTH_ENABLED esta
# activo y WEB_PASSWORD no esta definida en .env (que si esta en .gitignore), la app no
# arranca en vez de caer en una credencial publica.
WEB_PASSWORD = os.getenv("WEB_PASSWORD")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestión del ciclo de vida de la aplicación."""
    # Falla cerrado antes de tocar nada: con autenticación activa y sin WEB_PASSWORD, el
    # panel quedaría accesible con cualquier token (o con uno público, como antes).
    if os.getenv("AUTH_ENABLED", "false").lower() != "false" and not WEB_PASSWORD:
        raise RuntimeError(
            "AUTH_ENABLED está activo pero WEB_PASSWORD no está definida. "
            "Añade WEB_PASSWORD=<tu contraseña> al archivo .env (está en .gitignore) "
            "o pon AUTH_ENABLED=false si quieres el panel sin autenticación."
        )
    await db.initialize()
    db_size = os.path.getsize(DB_PATH) / (1024 * 1024) if os.path.exists(DB_PATH) else 0
    logger.info(f"✅ Sentinel v2.0.9: DB Detectada en {DB_PATH} ({db_size:.2f} MB)")
    if not mt5.initialize():
        logger.error("❌ Fallo al inicializar MetaTrader 5 en la API")

    # Registrar el WSLogHandler en el logger root para capturar todo
    logging.getLogger().addHandler(ws_handler)

    # NOTA (2026-09-02): el motor de trading autónomo (orchestrator.start_v6) queda RETIRADO
    # a propósito. El trading real ahora vive en 9 EAs MQL5 confirmados en MT5 (ver
    # ../MT5/README.md); esta web pasa a ser un panel de solo monitorización. No basta con
    # trading_mode='MANUAL' -- ese flag solo bloqueaba aperturas nuevas (antiguo
    # orchestrator.py:669), pero global_trade_management() seguia enviando ordenes reales cada
    # 10s sin mirarlo (trailing/breakeven/cierre de sesion/cierre de panico/reversal-close, ver
    # executor.py). Retirar el arranque del motor es la unica forma de garantizar que esta web
    # no pueda operar por su cuenta. Si algun dia se quiere reactivar el auto-trading, el codigo
    # de orchestrator/executor sigue intacto, solo hay que volver a lanzar start_v6 aqui.

    # Iniciar sincronizador continuo MT5 -> BBDD (posiciones abiertas + cierres), sustituye a
    # sync_trades_task del motor retirado (que solo cubria historial cerrado cada 60s)
    try:
        from PST_Core.engine.mt5_sync import MT5PositionSync
        position_sync = MT5PositionSync(db=db)
        asyncio.create_task(position_sync.run())
        logger.info("✅ [SYNC] Sincronizador de posiciones MT5 iniciado.")
    except Exception as e:
        logger.error(f"Error starting MT5 position sync: {e}")

    # Iniciar Agente Monitor en segundo plano
    try:
        from PST_Core.engine.monitor_agent import PSTMonitorAgent
        from PST_Core.engine.telegram_manager import telegram_bot

        monitor = PSTMonitorAgent(db=db, portfolio=portfolio, telegram_bot=telegram_bot)
        asyncio.create_task(monitor.run())
        # Enlazar el monitor al telegram_bot para que /report y /metrics funcionen
        telegram_bot.monitor_agent = monitor
        logger.info("✅ [MONITOR] Agente de vigilancia iniciado.")
    except Exception as e:
        logger.error(f"Error starting monitor agent: {e}")

    # Avisos por Telegram antes de cada bloqueo del calendario restringido (PST_API/llm_api.py)
    from PST_API.llm_api import alertas_calendario
    asyncio.create_task(alertas_calendario())

    yield

app = FastAPI(
    title="PST Sentinel Trade API (SMC Update)",
    version="2.6.20",
    description="Motor de persistencia, telemetría e histórico de Pails Sentinel Trade.",
    lifespan=lifespan
)

# Configurar CORS para permitir que el Dashboard y Apps se conecten
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Middleware de Autenticación Global (FASE 39)
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # Protegemos todo lo que empiece por /api/ excepto el login y OPTIONS (CORS preflight)
    if request.url.path.startswith("/api/") and request.url.path != "/api/auth/login" and request.method != "OPTIONS":
        # Si AUTH_ENABLED=false en .env, se omite la autenticación
        auth_enabled = os.getenv("AUTH_ENABLED", "false").lower() != "false"
        if auth_enabled:
            token = request.headers.get("X-PST-Token")
            if not token or token != WEB_PASSWORD:
                # Nunca se registra la contrasena esperada ni el token recibido: estos logs
                # van a PST_Startup.log / v2_sentinel_prime.log en disco, y escribir ahi el
                # secreto en claro convertia cualquier log compartido en una filtracion.
                logger.warning(
                    "🔒 Intento de acceso bloqueado al endpoint %s (%s) — token %s",
                    request.url.path, request.method, "ausente" if not token else "incorrecto",
                )
                return JSONResponse(status_code=401, content={"detail": "Unauthorized. Invalid or missing X-PST-Token."})
    return await call_next(request)

@app.get("/api/auth/config", tags=["Auth"])
async def auth_config():
    """Dice al frontend si hace falta pedir contraseña. Sin autenticación, entra directo."""
    return {"auth_enabled": os.getenv("AUTH_ENABLED", "false").lower() != "false"}

@app.post("/api/auth/login", tags=["Auth"])
async def login(req: LoginRequest):
    """Verifica la contraseña maestra para acceder al Dashboard."""
    if req.password == WEB_PASSWORD:
        return {"token": WEB_PASSWORD, "status": "authenticated"}
    raise HTTPException(status_code=401, detail="Contraseña incorrecta")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Mantener conexión viva
            data = await websocket.receive_text()
            # Podríamos procesar comandos desde el Dashboard aquí
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# --- PERSISTENT PATH LOGIC (FASE 45) ---
db = PSTDatabase(db_path=DB_PATH)
portfolio = PortfolioManager(db=db)
executor = PSTExecutor(db=db, portfolio=portfolio)

# Helper para asegurar conexión con MT5 (FASE 42)
def ensure_mt5_connected():
    """Verifica si MT5 está conectado e intenta re-inicializar si es necesario."""
    try:
        # account_info() es una forma rápida de verificar si hay conexión activa
        if mt5.account_info() is None:
            logger.warning("🔄 MT5 desconectado. Intentando re-inicializar...")
            if mt5.initialize():
                logger.info("✅ Re-conexión con MT5 exitosa.")
                return True
            else:
                logger.error("❌ Fallo crítico al re-inicializar MetaTrader 5.")
                return False
        return True
    except Exception as e:
        logger.error(f"⚠️ Error verificando conexión MT5: {e}")
        return False


@app.get("/api/account", response_model=AccountStatus, tags=["Trading"])
async def get_account():
    """Obtiene el estado financiero actual de la cuenta desde MT5."""
    if not ensure_mt5_connected():
        raise HTTPException(status_code=503, detail="MetaTrader 5 not connected")
        
    status = await portfolio.get_account_status()
    if not status:
        raise HTTPException(status_code=503, detail="Error fetching status from MT5")

    # Nota (2026-09-02): la sincronización de historial de deals ya NO se dispara aquí en
    # cada GET (este endpoint se sondea cada 3s desde el frontend) -- lo hace de forma
    # centralizada MT5PositionSync (PST_Core/engine/mt5_sync.py, cada 15s). Llamarlo aquí
    # también generaba una condición de carrera: dos sync_mt5_history() casi simultáneos
    # (este + el de /api/symbols) podían insertar el mismo cierre dos veces antes de que
    # el primero confirmara, dejando duplicados en `trades`.

    from datetime import datetime, time, timedelta
    today_start = datetime.combine(datetime.now().date(), time.min).strftime('%Y-%m-%d %H:%M:%S')

    closed_today = 0.0
    try:
        import aiosqlite
        async with aiosqlite.connect(db.db_path) as conn:
            async with conn.execute("SELECT SUM(profit) FROM trades WHERE time_out >= ?", (today_start,)) as cursor:
                row = await cursor.fetchone()
                closed_today = row[0] if row and row[0] else 0.0
    except Exception as e:
        logger.error(f"❌ Error calculando closed_today en API: {e}")

    profit_p = status["equity"] - status["balance"] # Flotante (posiciones abiertas)
    daily_total = closed_today + profit_p
    margin_p = status.get("margin", 0.0)
    
    return AccountStatus(
        balance=status["balance"],
        equity=status["equity"],
        margin=margin_p,
        margin_free=status["margin_free"],
        margin_level=status.get("margin_level", 0.0),
        daily_pnl=daily_total,
        profit=profit_p,
        active_pnl=profit_p
    )

@app.get("/api/performance", tags=["Performance"])
async def get_performance():
    """Calcula métricas globales de rendimiento."""
    try:
        import aiosqlite
        async with aiosqlite.connect(db.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT profit, time_in, time_out FROM trades WHERE price_out > 0") as cursor:
                trades = await cursor.fetchall()
                
                if not trades:
                    return {"profit_factor": 0.0, "win_rate": 0.0, "total_trades": 0, "net_profit": 0.0, "avg_duration_minutes": 0.0}
                
                total_trades = len(trades)
                wins = [t['profit'] for t in trades if t['profit'] > 0]
                losses = [abs(t['profit']) for t in trades if t['profit'] < 0]
                
                gross_profit = sum(wins)
                gross_loss = sum(losses)
                net_profit = gross_profit - gross_loss
                
                profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)
                win_rate = round((len(wins) / total_trades) * 100, 1)
                
                # Calcular duración media
                durations = []
                from datetime import datetime
                for t in trades:
                    try:
                        t_in = datetime.fromisoformat(t['time_in'])
                        t_out = datetime.fromisoformat(t['time_out'])
                        durations.append((t_out - t_in).total_seconds() / 60)
                    except: continue
                
                avg_duration = round(sum(durations) / len(durations), 1) if durations else 0.0
                
                return {
                    "profit_factor": float(profit_factor),
                    "win_rate": float(win_rate),
                    "total_trades": total_trades,
                    "net_profit": float(round(net_profit, 2)),
                    "avg_duration_minutes": float(avg_duration)
                }
    except Exception as e:
        logger.error(f"Error in get_performance: {e}")
        return {"profit_factor": 0.0, "win_rate": 0.0, "total_trades": 0, "net_profit": 0.0, "avg_duration_minutes": 0.0}

@app.get("/api/performance/equity", response_model=List[EquityPoint], tags=["Performance"])
async def get_equity_curve():
    """Genera la curva de equidad basada en el historial de trades."""
    try:
        import aiosqlite
        async with aiosqlite.connect(db.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute("SELECT profit, time_out FROM trades WHERE price_out > 0 ORDER BY time_out ASC") as cursor:
                trades = await cursor.fetchall()
                
                # Obtener balance actual para proyectar hacia atrás (o usar un base de 10000)
                if not ensure_mt5_connected():
                    initial_balance = 10000.0 # Fallback si MT5 no está
                else:
                    acc = mt5.account_info()
                    initial_balance = acc.balance if acc else 10000.0
                # Descontar profits para encontrar el punto de partida del gráfico
                current_running_balance = initial_balance - sum([t['profit'] for t in trades])
                
                curve = [EquityPoint(time="Start", equity=float(round(current_running_balance, 2)))]
                
                for t in trades:
                    current_running_balance += t['profit']
                    curve.append(EquityPoint(
                        time=t['time_out'].split(' ')[1] if ' ' in t['time_out'] else t['time_out'],
                        equity=float(round(current_running_balance, 2))
                    ))
                return curve
    except Exception as e:
        logger.error(f"Error in get_equity_curve: {e}")
        return []

@app.get("/api/performance/strategies", response_model=List[StrategyPerformance], tags=["Performance"])
async def get_strategy_performance():
    """Desglosa el rendimiento por estrategia."""
    try:
        import aiosqlite
        async with aiosqlite.connect(db.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            query = """
                SELECT strategy_name as strategy, COUNT(*) as trades_count, SUM(profit) as profit 
                FROM trades 
                WHERE price_out > 0 
                GROUP BY strategy_name
            """
            async with conn.execute(query) as cursor:
                rows = await cursor.fetchall()
                return [StrategyPerformance(**dict(r)) for r in rows]
    except Exception as e:
        logger.error(f"Error in get_strategy_performance: {e}")
        return []

@app.get("/api/trades/active", response_model=List[Trade], tags=["Trading"])
async def get_active_trades():
    """Lista operaciones abiertas combinando MT5 (tiempo real) y DB (metadata)."""
    if not ensure_mt5_connected():
        return [] # O podrías lanzar 503, pero para trades vacíos es mejor lista vacía
        
    positions = mt5.positions_get()
    if positions is None:
        return []
        
    db_trades = await db.get_active_trades()
    strategy_map = {t['ticket']: t.get('strategy_name', 'Manual/Unknown') for t in db_trades}
    
    active_trades = []
    for p in positions:
        trade_type = "BUY" if p.type == 0 else "SELL"
        active_trades.append(Trade(
            ticket=p.ticket,
            symbol=p.symbol,
            type=trade_type,
            volume=p.volume,
            price_open=p.price_open,
            price_current=p.price_current,
            sl=p.sl,
            tp=p.tp,
            profit=p.profit,
            time_in=str(p.time),
            strategy=strategy_map.get(p.ticket, "PST-Auto")
        ))
    return active_trades

def calculate_symbol_telemetry(symbol: str) -> str:
    """Calcula un diagnóstico rápido de salud del activo (v1.8.3)."""
    try:
        import MetaTrader5 as mt5
        tick = mt5.symbol_info_tick(symbol)
        if not tick: return "NO-DATA"
        
        info = mt5.symbol_info(symbol)
        if not info: return "UNKNOWN"
        
        # 1. Spread Check (Pips)
        spread = info.spread
        point = info.point
        spread_pips = spread * point if point > 0 else spread
        
        # 2. Market State
        from datetime import datetime
        last_tick_time = datetime.fromtimestamp(tick.time)
        diff = (datetime.now() - last_tick_time).total_seconds()
        
        if diff > 600: return "CLOSED/STALE"
        if spread > 50: return "HIGH-SPREAD" # Umbral conservador
        
        return "HEALTHY"
    except:
        return "ERROR"

SYMBOL_EA_NAMES = {
    # Fuente de verdad: MT5/README.md (migrado desde PailsSentinelMT5TradeV2, 2026-09-02).
    # Que EAs MQL5 reales estan configurados para operar cada simbolo -- puramente informativo,
    # esta web ya no decide ni ejecuta nada de trading.
    'EURGBP': ['PST_MeanReversion_EURGBP_BB'],
    'AUDCAD': ['PST_MeanReversion_EURGBP_BB'],
    'GBPAUD': ['PST_MeanReversion_EURGBP_BB'],
    'GBPUSD': ['PST_MeanReversion_EURGBP_BB'],
    'EURUSD': ['PST_LiquiditySweepFade_EURUSD_M5'],
    'CADJPY': ['PST_IchimokuTrend_GER40_H1'],
    'GER40.cash': ['PST_MacdMomentum_GER40_H1', 'PST_IchimokuTrend_GER40_H1'],
    'UK100.cash': ['PST_VwapReversion_ADX'],
    'JP225.cash': ['PST_MeanReversion_GapFill_D1'],
    'AAPL': ['PST_RelativeStrength_H1', 'PST_DonchianVolumeBreakout_Stocks', 'PST_GapAndGo_AAPL_H1'],
    'MSFT': ['PST_RelativeStrength_H1'],
    'TSLA': ['PST_RelativeStrength_H1', 'PST_DonchianVolumeBreakout_Stocks'],
}


@app.get("/api/symbols", response_model=List[SymbolStatus], tags=["Config"])
async def get_symbols():
    """Lista de simbolos monitorizados con precio/PnL en vivo desde MT5 + BBDD.
    Ya no calcula score/regimen/senal (venian del motor de trading autonomo, retirado
    2026-09-02) -- panel de solo monitorizacion, ver PST_API/main.py lifespan()."""
    if not ensure_mt5_connected():
         raise HTTPException(status_code=503, detail="MetaTrader 5 not connected")

    # Nota (2026-09-02): sync proactivo retirado de aquí (mismo motivo que /api/account) --
    # MT5PositionSync ya lo hace de forma centralizada cada 15s, sin la condición de carrera
    # de dos syncs casi simultáneos duplicando una fila.

    symbols_cfg = await db.get_all_symbols_config()
    today_realized_map = await db.get_today_profit_by_symbol()
    total_realized_map = await db.get_all_time_profit_by_symbol()
    profit_24h_map = await db.get_24h_profit_by_symbol()

    def get_val_tolerant(m, s):
        """Busca un valor en un mapa probando el símbolo tal cual, en mayúsculas, y su base."""
        if not m or not s: return 0.0
        s_up = str(s).upper().strip()
        s_base = s_up.split('.')[0]
        return m.get(s, m.get(s_up, m.get(s_base, 0.0)))

    # Mapeo de P&L flotante y nº de posiciones abiertas (sólo posiciones ABIERTAS)
    positions = await asyncio.to_thread(mt5.positions_get)
    pnl_map = {}
    open_count_map = {}
    if positions:
        for p in positions:
            sym_p = p.symbol.upper().strip()
            pnl_map[sym_p] = pnl_map.get(sym_p, 0.0) + p.profit
            open_count_map[sym_p] = open_count_map.get(sym_p, 0) + 1

    results = []
    proximidad_map = _leer_proximidad()
    for s in symbols_cfg:
        sym = s['symbol']

        tick = await asyncio.to_thread(mt5.symbol_info_tick, sym)
        price = tick.bid if tick else 0.0

        import time
        market_open = True
        if tick and tick.time:
            if (int(time.time()) - tick.time) > 600:
                market_open = False
        else:
            market_open = False

        info = await asyncio.to_thread(mt5.symbol_info, sym)
        spread_price = (info.spread * info.point) if info else 0.0

        daily_change = 0.0
        spark_data = []
        try:
            d1_data = await asyncio.to_thread(mt5.copy_rates_from_pos, sym, mt5.TIMEFRAME_D1, 0, 1)
            if d1_data is not None and len(d1_data) > 0:
                open_price = d1_data[0]['open']
                if open_price > 0:
                    daily_change = ((price - open_price) / open_price) * 100

            m5_data = await asyncio.to_thread(mt5.copy_rates_from_pos, sym, mt5.TIMEFRAME_M5, 0, 20)
            if m5_data is not None:
                spark_data = [float(x['close']) for x in m5_data]
        except:
            pass

        results.append(SymbolStatus(
            symbol=sym,
            type=(s.get('type') or ''),
            is_active=bool(s['is_active']),
            market_open=market_open,
            price=price,
            spread=spread_price,
            open_positions=get_val_tolerant(open_count_map, sym) or 0,
            floating_pnl=get_val_tolerant(pnl_map, sym),
            daily_pnl=get_val_tolerant(today_realized_map, sym),
            total_pnl=get_val_tolerant(total_realized_map, sym),
            profit_24h=get_val_tolerant(profit_24h_map, sym),
            daily_change_pct=daily_change,
            sparkline=spark_data,
            strategies=SYMBOL_EA_NAMES.get(sym, []),
            proximidad=proximidad_map.get(sym),
        ))

    results.sort(key=lambda x: (not x.market_open, x.symbol))

    return results

@app.get("/api/news/upcoming", tags=["News"])
async def get_upcoming_news():
    """Retorna los próximos eventos de alto impacto y el estado de bloqueo actual (Fase 50)."""
    from PST_Core.engine.news_manager import news_guard
    upcoming = news_guard.get_upcoming_high_impact(limit=5)
    active_blocks = news_guard.get_active_blocks()
    
    return {
        "upcoming": [
            {
                "title": e["title"],
                "country": e["country"],
                "time_utc": e["time_utc"].isoformat(),
                "impact": e["impact"]
            } for e in upcoming
        ],
        "active_blocks": [
            {
                "title": e["title"],
                "country": e["country"],
                "time_utc": e["time_utc"].isoformat()
            } for e in active_blocks
        ],
        "is_global_block": len(active_blocks) > 0 
    }

@app.get("/api/ohlc/{symbol}", response_model=List[OHLCBar], tags=["Data"])
async def get_ohlc(symbol: str, timeframe: str = "M5", count: int = 200):
    """Obtiene datos históricos de velas de MetaTrader 5."""
    if not ensure_mt5_connected():
        raise HTTPException(status_code=503, detail="MetaTrader 5 not connected")
        
    tf_map = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1,
        "D1": mt5.TIMEFRAME_D1
    }
    
    selected_tf = tf_map.get(timeframe.upper(), mt5.TIMEFRAME_M5)
    rates = mt5.copy_rates_from_pos(symbol, selected_tf, 0, count)
    
    if rates is None:
        raise HTTPException(status_code=404, detail=f"No data found for symbol {symbol}")
        
    bars = []
    for r in rates:
        bars.append(OHLCBar(
            time=int(r['time']),
            open=float(r['open']),
            high=float(r['high']),
            low=float(r['low']),
            close=float(r['close']),
            volume=int(r['tick_volume'])
        ))
    return bars

def calculate_symbol_telemetry(symbol: str):
    """Calcula RSI(14) y ADX(14) en múltiples timeframes."""
    if not mt5.initialize():
        return {}
        
    import pandas as pd
    tf_map = {
        "M1":  mt5.TIMEFRAME_M1,
        "M5":  mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "H1":  mt5.TIMEFRAME_H1,
    }
    result = {}
    for tf_name, tf_val in tf_map.items():
        try:
            rates = mt5.copy_rates_from_pos(symbol, tf_val, 0, 50)
            if rates is None or len(rates) < 15:
                result[tf_name] = {"rsi": None, "adx": None}
                continue
            df = pd.DataFrame(rates)
            rsi_series = ta.rsi(df['close'], length=14)
            adx_series = ta.adx(df['high'], df['low'], df['close'], length=14)
            rsi_val = round(float(rsi_series.iloc[-1]), 1) if rsi_series is not None and not rsi_series.empty else None
            adx_col = [c for c in adx_series.columns if c.startswith("ADX_")] if adx_series is not None else []
            adx_val = round(float(adx_series[adx_col[0]].iloc[-1]), 1) if adx_col else None
            result[tf_name] = {"rsi": rsi_val, "adx": adx_val}
        except Exception as e:
            result[tf_name] = {"rsi": None, "adx": None}
    return result

@app.get("/api/symbols/{symbol}/telemetry", tags=["Data"])
async def get_telemetry(symbol: str):
    """Calcula RSI(14) y ADX(14) en múltiples timeframes para el panel de telemetría."""
    if not ensure_mt5_connected():
        return {} # Fallback suave
    return calculate_symbol_telemetry(symbol)

@app.post("/api/config/toggle", response_model=APIResponse, tags=["Config"])
async def toggle_symbol(update: ConfigUpdate):
    """Activa o desactiva un símbolo o estrategia en la base de datos."""
    try:
        if update.strategy:
            await db.set_symbol_strategy(
                update.symbol, 
                update.strategy, 
                is_active=update.is_active,
                score_threshold=update.score_threshold,
                sl_mult=update.sl_mult,
                tp_mult=update.tp_mult,
                risk_mode=update.risk_mode,
                risk_value=update.risk_value,
                use_trailing=update.use_trailing,
                use_breakeven=update.use_breakeven,
                be_mult=update.be_mult,
                ts_mult=update.ts_mult,
                min_rr=update.min_rr
            )
        else:
            await db.set_symbol_active(update.symbol, 1 if update.is_active else 0)
            if update.score_threshold is not None:
                await db.update_symbol_params(update.symbol, score_threshold=update.score_threshold)
            
        return APIResponse(status="success", message=f"Updated {update.symbol} success")
    except Exception as e:
        return APIResponse(status="error", message=str(e))

# Alias para compatibilidad con el frontend (App.jsx llama a estos nombres)
@app.post("/api/config/strategy", response_model=APIResponse, tags=["Config"])
async def alias_toggle_strategy(update: ConfigUpdate):
    return await toggle_symbol(update)

@app.post("/api/config/symbol", response_model=APIResponse, tags=["Config"])
async def alias_toggle_symbol(update: ConfigUpdate):
    return await toggle_symbol(update)

@app.get("/api/config/bot/{key}", tags=["Config"])
async def get_bot_config(key: str, default: str = "AUTO"):
    """Obtiene una configuración global del bot."""
    val = await db.get_config(key, default)
    return {"key": key, "value": val}

@app.post("/api/config/bot", response_model=APIResponse, tags=["Config"])
async def update_bot_config(update: BotConfigUpdate):
    """Actualiza una configuración global del bot."""
    success = await db.update_config(update.key, update.value)
    if success:
        return APIResponse(status="success", message=f"Config {update.key} updated to {update.value}")
    else:
        return APIResponse(status="error", message=f"Failed to update config {update.key}")

@app.get("/api/history", response_model=List[Trade], tags=["Trading"])
async def get_history(limit: int = 20):
    """Obtiene el historial de operaciones cerradas desde la base de datos."""
    try:
        import aiosqlite
        async with aiosqlite.connect(db.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM trades WHERE price_out > 0 ORDER BY time_out DESC LIMIT ?", 
                (limit,)
            ) as cursor:
                rows = await cursor.fetchall()
                results = []
                for r in rows:
                    results.append(Trade(
                        ticket=r['ticket'],
                        symbol=r['symbol'],
                        type=r['type'],
                        volume=r['volume'] or 0.0,
                        price_open=r['price_in'] or 0.0,
                        price_current=r['price_out'] or 0.0,
                        sl=r['sl'] or 0.0,
                        tp=r['tp'] or 0.0,
                        profit=r['profit'] or 0.0,
                        time_in=str(r['time_in']),
                        strategy=r['strategy_name'] or "Manual",
                        notes=r['notes'] if 'notes' in r.keys() else None,
                        snapshot_path=r['snapshot_path'] if 'snapshot_path' in r.keys() else None
                    ))
                return results
    except Exception as e:
        logger.error(f"Error en history: {e}")
        return []

@app.get("/api/performance/hourly", tags=["Performance"])
async def get_hourly_performance():
    """Agrupa el rendimiento de operaciones cerradas por hora del día (0-23)."""
    try:
        import aiosqlite
        async with aiosqlite.connect(db.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT profit, time_out FROM trades WHERE price_out > 0 AND time_out IS NOT NULL"
            ) as cursor:
                trades = await cursor.fetchall()

        from datetime import datetime
        buckets: dict[int, dict] = {h: {"hour": h, "pnl": 0.0, "trades": 0, "wins": 0} for h in range(24)}

        for t in trades:
            try:
                time_str = t["time_out"]
                # Soporta formatos: "2026-06-15 09:32:11" y "2026.06.15 09:32:11"
                dt = datetime.fromisoformat(time_str.replace(".", "-"))
                h = dt.hour
                buckets[h]["pnl"]    += float(t["profit"])
                buckets[h]["trades"] += 1
                if t["profit"] > 0:
                    buckets[h]["wins"] += 1
            except Exception:
                continue

        result = []
        for h in range(24):
            b = buckets[h]
            total = b["trades"]
            result.append({
                "hour":     h,
                "pnl":      round(b["pnl"], 2),
                "trades":   total,
                "win_rate": round(b["wins"] / total * 100, 1) if total > 0 else 0.0,
            })
        return result

    except Exception as e:
        logger.error(f"Error en get_hourly_performance: {e}")
        return [{"hour": h, "pnl": 0.0, "trades": 0, "win_rate": 0.0} for h in range(24)]


@app.get("/api/performance/buckets", tags=["Performance"])
async def get_bucket_weights():
    """Retorna los pesos actuales de las cubetas de capital por categoría de estrategia."""
    try:
        from PST_Core.utils.bucket_manager import bucket_manager
        from PST_Core.config import CAPITAL_BUCKETS
        if bucket_manager is not None:
            weights = dict(bucket_manager._weights)
            pnl = await bucket_manager.get_monthly_pnl_by_category()
        else:
            weights = dict(CAPITAL_BUCKETS)
            pnl = {k: 0.0 for k in CAPITAL_BUCKETS}
        return {
            "weights": weights,
            "monthly_pnl": pnl,
            "default_weights": CAPITAL_BUCKETS
        }
    except Exception as e:
        logger.error(f"Error in get_bucket_weights: {e}")
        from PST_Core.config import CAPITAL_BUCKETS
        return {"weights": dict(CAPITAL_BUCKETS), "monthly_pnl": {}, "default_weights": CAPITAL_BUCKETS}

@app.get("/api/performance/analytics", tags=["Performance"])
async def get_analytics():
    """Retorna métricas avanzadas y la curva de equidad (Fase 51)."""
    try:
        metrics = await db.get_advanced_metrics()
        equity_curve = await db.get_equity_curve()
        return {
            "metrics": metrics,
            "equity_curve": equity_curve
        }
    except Exception as e:
        logger.error(f"❌ Error en analytics: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/trades/notes", tags=["Trading"])
async def update_trade_notes(update: TradeNoteUpdate):
    """Guarda comentarios/notas para un trade específico (Fase 53)."""
    success = await db.update_trade_notes(update.ticket, update.notes)
    if not success:
        raise HTTPException(status_code=500, detail="Error actualizando notas")
    return {"status": "success"}

@app.get("/api/trades/{ticket}/snapshot", tags=["Trading"])
async def get_trade_snapshot(ticket: int):
    """Retorna el contexto OHLC guardado. Si no existe, lo genera (Fase 53 y PRO)."""
    try:
        import aiosqlite
        import json
        from datetime import datetime
        async with aiosqlite.connect(db.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            # Intentar obtener el guardado original
            async with conn.execute(
                "SELECT symbol, ohlc_data, timestamp FROM trade_context WHERE ticket = ? ORDER BY id DESC LIMIT 1", 
                (ticket,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        "ticket": ticket,
                        "symbol": row['symbol'],
                        "timestamp": str(row['timestamp']),
                        "ohlc": json.loads(row['ohlc_data'])
                    }

            # ====== FALLBACK DINÁMICO =======
            # Si no existe contexto en DB, buscar el trade para sacar time_in y symbol
            async with conn.execute(
                "SELECT symbol, time_in FROM trades WHERE ticket = ?", 
                (ticket,)
            ) as cursor:
                trade_row = await cursor.fetchone()
                if not trade_row:
                    raise HTTPException(status_code=404, detail="Trade neither has context nor exists in history")
                
                symbol = trade_row['symbol']
                time_in_str = trade_row['time_in']
                
                # Intentar generar el OHLC en vuelo
                if not ensure_mt5_connected():
                    raise HTTPException(status_code=503, detail="MT5 required for historical fallback")
                
                # Parsear la fecha de entrada
                time_in_dt = datetime.strptime(time_in_str, "%Y-%m-%d %H:%M:%S")
                
                # Pedimos las 50 velas de M15 anteriores a la entrada + 10 posteriores por contexto visual
                # Convertimos datetime a timestamp de forma compatible con MT5
                import time
                # time_in_dt ya está en timezone local/broker del string guardado
                rates = mt5.copy_rates_from(symbol, mt5.TIMEFRAME_M15, time_in_dt, 60)
                
                if rates is None or len(rates) == 0:
                    raise HTTPException(status_code=404, detail="Could not fetch historical fallback MT5 data")
                
                ohlc_data = []
                for r in rates:
                    ohlc_data.append({
                        "time": datetime.fromtimestamp(r['time']).strftime("%Y-%m-%d %H:%M"),
                        "open": r['open'],
                        "high": r['high'],
                        "low": r['low'],
                        "close": r['close'],
                        "volume": r['tick_volume']
                    })
                
                return {
                    "ticket": ticket,
                    "symbol": symbol,
                    "timestamp": time_in_str,
                    "ohlc": ohlc_data
                }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error get_trade_snapshot: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/trades/close", response_model=APIResponse, tags=["Trading"])
async def close_trade(payload: dict):
    """Cierra una posición abierta a precio de mercado por su ticket."""
    if not ensure_mt5_connected():
        return APIResponse(status="error", message="MetaTrader 5 not connected")
    try:
        from PST_Core.engine.mt5_async import close_position_async
        ticket = int(payload.get("ticket", 0))
        if not ticket:
            return APIResponse(status="error", message="Ticket invalido")
        
        # Capturar datos de la posicion ANTES de cerrar (precio actual + profit flotante)
        pos_before = mt5.positions_get(ticket=ticket)
        pre_profit = pos_before[0].profit if pos_before else 0.0
        pre_price  = pos_before[0].price_current if pos_before else 0.0
        
        result = await close_position_async(ticket)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            # Actualizar DB inmediatamente con los datos capturados antes del cierre
            await db.update_trade_cierre(ticket, pre_price, pre_profit)
            logger.info(f"Posicion {ticket} cerrada y DB actualizada | PnL: {pre_profit:.2f}")
            return APIResponse(status="success", message=f"Posicion {ticket} cerrada correctamente")
        err = result.comment if result else "Sin respuesta de MT5"
        return APIResponse(status="error", message=f"Error al cerrar: {err}")
    except Exception as e:
        logger.error(f"Error close_trade: {e}")
        return APIResponse(status="error", message=str(e))

@app.post("/api/trades/manual", response_model=APIResponse, tags=["Trading"])
async def execute_manual_trade(order: ManualOrder):
    """Ejecuta una orden de compra o venta manual (Simple o Smart) en MT5."""
    if not ensure_mt5_connected():
        return APIResponse(status="error", message="MetaTrader 5 not connected")
        
    try:
        symbol = order.symbol.upper()
        
        if order.is_smart:
            # --- MODO SMART: Cálculo automático optimizado ---
            tf_m5 = mt5.TIMEFRAME_M5
            rates = mt5.copy_rates_from_pos(symbol, tf_m5, 0, 50)
            if rates is None or len(rates) == 0:
                return APIResponse(status="error", message="Failed to fetch ATR data for Smart order")
            
            import pandas as pd
            df = pd.DataFrame(rates)
            atr_s = ta.atr(df['high'], df['low'], df['close'], length=14)
            current_atr = float(atr_s.iloc[-1]) if atr_s is not None and not atr_s.empty else 0.0
            
            # Fallbacks base (ATR x 2.5 / 5.0)
            sl_atr_fallback = current_atr * 2.5 if current_atr > 0 else 0
            tp_atr_fallback = current_atr * 5.0 if current_atr > 0 else 0
            
            # Construir metadatos para el executor
            trade_metadata = {
                "risk_mode": "MONEY" if order.risk_amount else None,
                "risk_value": order.risk_amount,
                "target_price_sl": order.sl_price,
                "target_price_tp": order.tp_price,
                "rr_ratio": order.rr_ratio
            }
            # Limpiar Nones
            trade_metadata = {k: v for k, v in trade_metadata.items() if v is not None}
            
            result = await executor.execute_trade(
                symbol=symbol,
                signal_type=order.action.upper(),
                stop_loss_atr=sl_atr_fallback,
                take_profit_atr=tp_atr_fallback,
                strategy_name="Web-Smart",
                regime="MANUAL",
                metadata=trade_metadata
            )
            
            if not result:
                return APIResponse(status="error", message="Smart execution rejected by risk manager")
                
            return APIResponse(status="success", message=f"Smart {order.action} executed with Risk Logic")
            
        else:
            # --- MODO SIMPLE: Solo volumen ---
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                return APIResponse(status="error", message=f"Symbol {symbol} not found")
                
            if not symbol_info.visible:
                mt5.symbol_select(symbol, True)

            order_type = mt5.ORDER_TYPE_BUY if order.action.upper() == "BUY" else mt5.ORDER_TYPE_SELL
            tick = mt5.symbol_info_tick(symbol)
            price = tick.ask if order.action.upper() == "BUY" else tick.bid
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": order.volume,
                "type": order_type,
                "price": price,
                "magic": 123456,
                "comment": "PST-Web-Manual",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            result = mt5.order_send(request)
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                return APIResponse(status="error", message=f"MT5 Error: {result.retcode}")
                
            return APIResponse(status="success", message=f"Manual order {symbol} executed")
    except Exception as e:
        return APIResponse(status="error", message=str(e))

@app.get("/api/logs", response_model=APIResponse, tags=["Monitor"])
async def get_system_logs(limit: int = 50):
    """Obtiene los últimos logs del sistema desde la DB."""
    try:
        logs_data = await db.get_latest_logs(limit=limit)
        return APIResponse(status="success", logs=logs_data)
    except Exception as e:
        return APIResponse(status="error", message=str(e))

@app.get("/api/news/upcoming", tags=["Monitor"])
async def get_upcoming_news():
    """Retorna eventos de alto impacto proyectados (Fase 50)."""
    try:
        from PST_Core.engine.news_manager import news_guard
        upcoming = news_guard.get_upcoming_high_impact(limit=3)
        # Convertir objetos datetime a strings ISO para JSON
        safe_upcoming = []
        for e in upcoming:
            safe_upcoming.append({
                "title": e['title'],
                "country": e['country'],
                "time": e['time_utc'].isoformat(),
                "impact": e['impact']
            })
        
        # Verificar si hay bloqueo global activo (para cualquier símbolo clave)
        is_blocked, _ = news_guard.is_news_blocked("ALL")
        
        return {
            "upcoming": safe_upcoming,
            "is_global_block": is_blocked
        }
    except Exception as e:
        logger.error(f"❌ Error en news/upcoming: {e}")
        return {"upcoming": [], "is_global_block": False}

@app.delete("/api/trades/close/{symbol}", response_model=APIResponse, tags=["Trading"])
async def close_symbol_trades(symbol: str):
    """Cierra todas las posiciones abiertas para un símbolo."""
    if not ensure_mt5_connected():
        return APIResponse(status="error", message="MetaTrader 5 not connected")
        
    try:
        positions = mt5.positions_get(symbol=symbol.upper())
        if positions is None or len(positions) == 0:
            return APIResponse(status="success", message=f"No active positions for {symbol}")
            
        closed_count = 0
        for p in positions:
            tick = mt5.symbol_info_tick(p.symbol)
            order_type = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            price = tick.bid if p.type == mt5.ORDER_TYPE_BUY else tick.ask
            
            # Capturar profit actual ANTES de enviar el cierre
            pre_profit = p.profit
            pre_price  = price
            pre_ticket = p.ticket
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": p.symbol,
                "volume": p.volume,
                "type": order_type,
                "position": p.ticket,
                "price": price,
                "magic": p.magic,
                "comment": "PST-Web-Close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            res = mt5.order_send(request)
            if res.retcode == mt5.TRADE_RETCODE_DONE:
                closed_count += 1
                # Actualizar DB inmediatamente con el profit capturado antes del cierre
                await db.update_trade_cierre(pre_ticket, pre_price, pre_profit)
                logger.info(f"Cierre {p.symbol} ticket:{pre_ticket} | PnL: {pre_profit:.2f}")
                
        return APIResponse(status="success", message=f"Closed {closed_count} positions for {symbol}")
    except Exception as e:
        return APIResponse(status="error", message=str(e))

# ── FASE 34: MATRIX EDITOR ────────────────────────────────────────────────────
from pydantic import BaseModel as _BM, Field as _F

class SymbolParamsUpdate(_BM):
    lot_size: float        = _F(None, ge=0.01, le=100.0)
    sl_mult: float         = _F(None, ge=0.5,  le=10.0)
    tp_mult: float         = _F(None, ge=1.0,  le=20.0)
    score_threshold: float = _F(None, ge=0.0,  le=100.0)
    risk_mode: str         = _F(None) # LOTS, PCT, MONEY
    risk_value: float      = _F(None)
    min_rr: float          = _F(None, ge=1.0,  le=5.0)

@app.get("/api/symbols/{symbol}/params", tags=["Config"])
async def get_symbol_params(symbol: str):
    """Devuelve los parámetros de trading individuales de un símbolo (Matrix Editor)."""
    params = await db.get_symbol_params(symbol)
    return {"symbol": symbol, **params}

@app.put("/api/symbols/{symbol}/params", tags=["Config"])
async def update_symbol_params(symbol: str, body: SymbolParamsUpdate):
    """Actualiza los parámetros de trading de un símbolo en tiempo real (Matrix Editor)."""
    ok = await db.update_symbol_params(
        symbol,
        lot_size=body.lot_size,
        sl_mult=body.sl_mult,
        tp_mult=body.tp_mult,
        score_threshold=body.score_threshold,
        risk_mode=body.risk_mode,
        risk_value=body.risk_value,
        min_rr=body.min_rr,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Error actualizando parámetros")
    return {"symbol": symbol, "status": "updated"}

@app.get("/api/matrix", tags=["Config"])
async def get_matrix_data():
    """Devuelve la configuración completa de todos los símbolos para el Matrix Editor."""
    symbols_cfg = await db.get_all_symbols_config()
    radar_data = await db.get_radar_data()
    result = []
    for s in symbols_cfg:
        sym = s["symbol"]
        params = await db.get_symbol_params(sym) # Carga globales del símbolo
        radar = radar_data.get(sym, {})
        
        # Obtener configuración real de estrategias desde la DB (is_active, risk, sl/tp overrides)
        strategies_db = await db.get_symbol_strategies(sym)
        
        # Procesar factores (radar) y mezclar con configuración (DB)
        raw_factors = radar.get('factors', {})
        factors_map = {}
        
        # Primero, asegurar que todas las estrategias registradas en la DB aparezcan
        for s_name, s_cfg in strategies_db.items():
            # Riesgo específico para Scalper Pro: 5€, resto 25€ (si no hay config en DB)
            default_risk = 5.0 if "Scalper" in s_name else 25.0
            
            factors_map[s_name] = {
                "score": 0.0,
                "is_active": bool(s_cfg.get("is_active", 0)), 
                "risk_mode": s_cfg.get("risk_mode", "MONEY"),
                "risk_value": s_cfg.get("risk_value", default_risk),
                "sl_mult": s_cfg.get("sl_mult", 2.5),
                "tp_mult": s_cfg.get("tp_mult", 6.0),
                "score_threshold": s_cfg.get("score_threshold", 80.0),
                "use_trailing": bool(s_cfg.get("use_trailing", 1)), # Activado por defecto (1)
                "use_breakeven": bool(s_cfg.get("use_breakeven", 1)), # Activado por defecto (1)
                "be_mult": s_cfg.get("be_mult", 2.0),
                "ts_mult": s_cfg.get("ts_mult", 2.5),
                "min_rr": s_cfg.get("min_rr", 1.5),
                "filter_profile": s_cfg.get("filter_profile")  # Fase 3: perfil de filtros JSON (PrecisionScalping)
            }
            
        # Segundos, actualizar con puntuaciones reales del radar
        if isinstance(raw_factors, dict):
            for s_name, s_data in raw_factors.items():
                score = s_data.get("score", 0.0) if isinstance(s_data, dict) else 0.0
                if s_name in factors_map:
                    factors_map[s_name]["score"] = score
                else:
                    factors_map[s_name] = {
                        "score": score,
                        "is_active": True # Si no está en DB pero está en radar, por defecto activa
                    }
        
        result.append({
            "symbol": sym,
            "is_active": bool(s.get("is_active", 1)),
            "type": s.get("type", ""),
            **params,
            "factors_map": factors_map
        })
    return result

class SymbolStatusUpdate(_BM):
    symbol: str
    is_active: bool

@app.post("/api/config/symbol", tags=["Config"])
async def update_symbol_status(update: SymbolStatusUpdate):
    """Activa o desactiva un símbolo en la matriz."""
    ok = await db.set_symbol_active(update.symbol, update.is_active)
    if not ok:
        raise HTTPException(status_code=500, detail="Error actualizando el estado del símbolo")
    return {"status": "success"}

class StrategyConfigUpdate(_BM):
    symbol: str
    strategy: str
    is_active: bool = _F(None)
    risk_mode: str = _F(None)
    risk_value: float = _F(None)
    sl_mult: float = _F(None)
    tp_mult: float = _F(None)
    score_threshold: float = _F(None)
    use_trailing: bool = _F(None)
    use_breakeven: bool = _F(None)
    be_mult: float = _F(None)
    ts_mult: float = _F(None)
    min_rr: float = _F(None)
    filter_profile: str = _F(None)   # Fase 3: JSON de perfil de filtros (PrecisionScalping)

@app.post("/api/config/strategy", tags=["Config"])
async def update_strategy_config(update: StrategyConfigUpdate):
    """Actualiza la configuración detallada de una estrategia para un símbolo."""
    # Validación defensiva: si llega filter_profile, debe ser JSON válido.
    if update.filter_profile is not None:
        try:
            import json as _json
            _json.loads(update.filter_profile)
        except Exception:
            raise HTTPException(status_code=400, detail="filter_profile no es JSON válido")
    ok = await db.set_symbol_strategy(
        update.symbol,
        update.strategy,
        is_active=update.is_active,
        risk_mode=update.risk_mode,
        risk_value=update.risk_value,
        sl_mult=update.sl_mult,
        tp_mult=update.tp_mult,
        score_threshold=update.score_threshold,
        use_trailing=update.use_trailing,
        use_breakeven=update.use_breakeven,
        be_mult=update.be_mult,
        ts_mult=update.ts_mult,
        min_rr=update.min_rr,
        filter_profile=update.filter_profile
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Error actualizando estrategia")
    return {"status": "success"}

class UserLevelPoint(BaseModel):
    time: int = None
    logical: int = None
    price: float

class UserLevelLine(BaseModel):
    id: int
    p1: UserLevelPoint
    p2: UserLevelPoint
    mode: str = "BOTH"

class UserLevelsSaveRequest(BaseModel):
    lines: List[UserLevelLine]

@app.get("/api/user_levels/{symbol}", tags=["Trading"])
async def get_user_levels_api(symbol: str):
    """Obtener líneas guardadas dibujadas por el usuario."""
    try:
        levels = await db.get_user_levels(symbol)
        lines = []
        for lvl in levels:
            if lvl['type'] == 'DIAGONAL':
                lines.append({
                    "id": lvl['id'],
                    "p1": {
                        "time": lvl['time1_ts'],
                        "price": lvl['price'],
                        "logical": None
                    },
                    "p2": {
                        "time": lvl['time2_ts'],
                        "price": lvl['price2'],
                        "logical": None
                    },
                    "mode": lvl.get('mode', 'BOTH')
                })
        return lines
    except Exception as e:
        logger.error(f"Error fetching user levels API: {e}")
        return []

@app.post("/api/user_levels/{symbol}", tags=["Trading"])
async def save_user_levels_api(symbol: str, req: UserLevelsSaveRequest):
    """Sobrescribir las líneas dibujadas para un símbolo."""
    try:
        # Convertir r.lines (objetos Pydantic) a lista de dicts para el modelo
        lines_list = [line.dict() for line in req.lines]
        ok = await db.save_trend_lines(symbol, lines_list)
        if ok:
            return {"status": "success"}
        raise HTTPException(status_code=500, detail="Error salvando líneas (DB Locked?)")
    except Exception as e:
        logger.error(f"Error saving user levels api: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

# --- RISK PROFILES (FASE 35) ---
@app.get("/api/profiles", tags=["Configuration"])
async def get_profiles():
    return await db.get_risk_profiles()

@app.post("/api/profiles", tags=["Configuration"])
async def save_profile(req: RiskProfileRequest):
    success = await db.save_risk_profile(req.name, req.config_json)
    if not success: raise HTTPException(status_code=500, detail="Error saving profile")
    return {"status": "success"}

@app.delete("/api/profiles/{profile_id}", tags=["Configuration"])
async def delete_profile(profile_id: int):
    success = await db.delete_risk_profile(profile_id)
    if not success: raise HTTPException(status_code=500, detail="Error deleting profile")
    return {"status": "success"}

# --- FRONTEND (SERVE REACT DIST) ---
# Detección robusta de rutas para PyInstaller (FASE 45)
if hasattr(sys, '_MEIPASS'):
    # En el EXE, los archivos están en la raíz del temporal
    base_dir = sys._MEIPASS
else:
    # En desarrollo, subimos dos niveles desde PST_API/main.py
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

react_dist_path = os.path.join(base_dir, "PST_Web", "dist")

# Proxy JSON de habblaud. OJO AL ORDEN: tiene que registrarse ANTES del catch-all
# `@app.get("/{full_path:path}")` de abajo, que devuelve 404 para todo lo que empiece por
# "api/". Starlette casa rutas por orden de registro, asi que un GET declarado despues del
# catch-all queda tapado (por eso /api/backtest/run, mas abajo, solo funciona al ser POST).
app.include_router(habblaud_router)
app.include_router(agents_router)
app.include_router(llm_router)
app.include_router(puntuador_router)

if os.path.isdir(react_dist_path):
    app.mount("/assets", StaticFiles(directory=os.path.join(react_dist_path, "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    async def serve_root():
        return FileResponse(os.path.join(react_dist_path, "index.html"))

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_react_app(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found")
        
        index_file = os.path.join(react_dist_path, "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        return {"error": "Frontend build not found."}
else:
    logger.warning(f"⚠️ Frontend dist no encontrado en {react_dist_path}")


# ── STRATEGY LAB BACKTESTING ──────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    strategy: str
    symbol: str
    days: int = 30
    timeframe: str = "H1"

@app.post("/api/backtest/run", tags=["StrategyLab"])
async def run_backtest(req: BacktestRequest):
    """Ejecuta un backtest real sobre datos históricos de MT5."""
    if not ensure_mt5_connected():
        raise HTTPException(status_code=503, detail="MetaTrader 5 not connected")

    # PST-AlphaTrend queda fuera del registro (retirada) pero el lab de backtest
    # sigue permitiendo probarla manualmente — entrada a mano, igual que antes.
    from PST_Core.strategies.registry import STRATEGY_REGISTRY, get_class as _get_strategy_class
    _ALPHA_TREND_TFS = ["h1", "m15"]
    if req.strategy != "PST-AlphaTrend" and req.strategy not in STRATEGY_REGISTRY:
        raise HTTPException(status_code=400, detail=f"Estrategia desconocida: {req.strategy}")

    TF_MT5_MAP = {
        "m1":  mt5.TIMEFRAME_M1,
        "m2":  mt5.TIMEFRAME_M2,
        "m3":  mt5.TIMEFRAME_M3,
        "m5":  mt5.TIMEFRAME_M5,
        "m15": mt5.TIMEFRAME_M15,
        "m30": mt5.TIMEFRAME_M30,
        "h1":  mt5.TIMEFRAME_H1,
        "h4":  mt5.TIMEFRAME_H4,
    }

    try:
        import pandas as pd
        from datetime import datetime, timedelta
        from PST_Core.backtesting.engine import PSTBacktestEngine

        # Calcular número de barras a pedir según timeframe primario y días
        bars_per_day = {"m1": 1440, "m2": 720, "m3": 480, "m5": 288, "m15": 96, "m30": 48, "h1": 24, "h4": 6}
        if req.strategy == "PST-AlphaTrend":
            tfs_needed = _ALPHA_TREND_TFS
        else:
            tfs_needed = STRATEGY_REGISTRY[req.strategy]["backtest_timeframes"]
        primary_tf = tfs_needed[0]
        num_bars = bars_per_day.get(primary_tf, 24) * req.days + 250  # +250 warmup

        # Resolver símbolo real en MT5 (puede tener sufijos como .cash, .a)
        def resolve_mt5_symbol(base: str) -> str:
            candidates = [base, base + ".cash", base + ".a", base + "USD"]
            for c in candidates:
                info = mt5.symbol_info(c)
                if info is not None:
                    mt5.symbol_select(c, True)
                    return c
            return base  # fallback: intentar tal cual

        mt5_symbol = resolve_mt5_symbol(req.symbol)

        # Descargar datos de MT5 para cada timeframe requerido
        all_data = {}
        for tf_key in tfs_needed:
            mt5_tf = TF_MT5_MAP.get(tf_key)
            if mt5_tf is None:
                continue
            rates = mt5.copy_rates_from_pos(mt5_symbol, mt5_tf, 0, num_bars)
            if rates is None or len(rates) == 0:
                raise HTTPException(status_code=404, detail=f"Sin datos MT5 para {mt5_symbol}/{tf_key}. Verifica que el símbolo está activo en Market Watch.")
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            all_data[tf_key] = df

        # Instanciar estrategia
        if req.strategy == "PST-AlphaTrend":
            from PST_Core.strategies.pst_alpha_trend import PSTAlphaTrend as strategy_cls
        else:
            strategy_cls = _get_strategy_class(req.strategy)
        strategy_instance = strategy_cls()

        # Ejecutar backtest
        engine = PSTBacktestEngine(score_threshold=75)
        result = await engine.run(strategy_instance, req.symbol, all_data)

        # Construir curva de equity
        equity = 0.0
        curve = []
        for i, t in enumerate(result.closed_trades):
            equity += t.pnl_r
            curve.append({
                "trade": i + 1,
                "equity_r": round(equity, 3),
                "result": t.result,
                "direction": "BUY" if t.direction == 1 else "SELL",
                "entry_time": t.entry_time.strftime("%d/%m %H:%M") if t.entry_time else "",
            })

        return {
            "strategy":      req.strategy,
            "symbol":        req.symbol,
            "days":          req.days,
            "total_trades":  len(result.closed_trades),
            "win_rate":      round(result.win_rate, 1),
            "profit_factor": round(result.profit_factor, 2) if result.profit_factor != float("inf") else 99.0,
            "sharpe":        round(result.sharpe_ratio, 2),
            "max_drawdown_r": round(result.max_drawdown_r, 2),
            "total_r":       round(result.total_r, 2),
            "avg_win_r":     round(result.avg_win_r, 2),
            "avg_loss_r":    round(result.avg_loss_r, 2),
            "period":        f"{result.period_start.strftime('%d/%m/%Y') if result.period_start else '?'} → {result.period_end.strftime('%d/%m/%Y') if result.period_end else '?'}",
            "curve":         curve,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error en backtest: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


def start_app():
    import uvicorn
    import threading
    import webview
    import time
    import socket
    import sys

    # v1.8.3: Puerto Fijo 8000 para mantener persistencia de sesión (localStorage)
    port = int(os.getenv("API_PORT", 8000))
    host = "127.0.0.1"

    def run_server():
        uvicorn.run(app, host=host, port=port, log_level="info")

    # Iniciar servidor en segundo plano
    t = threading.Thread(target=run_server, daemon=True)
    t.start()

    # Espera activa hasta que el puerto esté escuchando (máx 20 segundos)
    server_ready = False
    for _ in range(40):
        try:
            with socket.create_connection((host, port), timeout=0.5):
                server_ready = True
                break
        except (ConnectionRefusedError, TimeoutError, OSError):
            time.sleep(0.5)
            
    if not server_ready:
        print("El servidor falló al iniciar en el puerto", port)
        sys.exit(1)

    # Espejo de habblaud (puerto propio, sin X-Frame-Options) para la pestaña Oficina.
    # Best-effort: si habblaud no esta levantado, el espejo responde 503 y la pestaña lo
    # pinta como "oficina apagada". Ver PST_API/habblaud_proxy.py.
    # ensure_habblaud_running() solo hace algo con HABBLAUD_AUTOSTART=1; lo normal es
    # gobernar habblaud a mano con Tools/habblaud.ps1.
    ensure_habblaud_running()
    start_mirror_in_thread()

    # Agentes LLM que estaban activos al cerrar la app: se relanzan en segundo plano para no
    # retrasar la ventana. Ver PST_LLM/registro.py (arrancar_activos / detener_todos).
    threading.Thread(target=_arrancar_agentes_llm, daemon=True).start()
    # Agente puntuador de la home (PST_LLM/puntuador.py): propio, no es de la flota de trading.
    _arrancar_puntuador()

    # Lanzar ventana nativa única apuntando al puerto dinámico
    webview.create_window(
        f'Pails Sentinel Trade Bot - v{app.version}',
        f'http://{host}:{port}',
        width=1280,
        height=850,
        resizable=True,
        min_size=(1000, 700)
    )
    # private_mode=True es el default de pywebview: borra localStorage/cookies al
    # cerrar la ventana, así que el token de sesión (pst_token) nunca sobrevive a un
    # reinicio y el login se pide siempre. storage_path fija dónde vive ese perfil
    # persistente (junto a la DB, para que sobreviva entre arranques).
    storage_path = os.path.join(os.path.dirname(DB_PATH), "webview_storage")
    os.makedirs(storage_path, exist_ok=True)
    webview.start(private_mode=False, storage_path=storage_path)

    # Ventana cerrada: los agentes paran (sin perder su marca de activo, se relanzan al abrir).
    _detener_agentes_llm()
    _parar_puntuador()


def _arrancar_puntuador():
    try:
        if LLM_DIR not in sys.path:
            sys.path.insert(0, LLM_DIR)
        import puntuador
        puntuador.arrancar()
        logger.info("🎯 Agente puntuador de la home arrancado")
    except Exception:
        logger.exception("❌ No se pudo arrancar el agente puntuador")


def _parar_puntuador():
    try:
        import puntuador
        puntuador.parar()
    except Exception:
        logger.exception("❌ No se pudo parar el agente puntuador")


def _leer_proximidad():
    """Puntuaciones del agente puntuador (.agents/data/puntuador.json). Vacio si aun no hay datos."""
    try:
        return json.loads(PUNTUADOR_JSON.read_text(encoding="utf-8")).get("puntuaciones") or {}
    except Exception:
        return {}


def _arrancar_agentes_llm():
    try:
        import registro       # PST_LLM/registro.py, ruta añadida por PST_API/llm_api.py
        arrancados = registro.arrancar_activos()
        logger.info(f"🤖 Agentes LLM relanzados al abrir la app: {arrancados or 'ninguno'}")
    except Exception:
        logger.exception("❌ No se pudieron relanzar los agentes LLM")


def _detener_agentes_llm():
    try:
        import registro
        registro.detener_todos()
        logger.info("🤖 Agentes LLM parados al cerrar la app")
    except Exception:
        logger.exception("❌ No se pudieron parar los agentes LLM al cerrar")

if __name__ == "__main__":
    start_app()
