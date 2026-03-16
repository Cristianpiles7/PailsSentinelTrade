from fastapi import FastAPI, HTTPException, Request
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from typing import List
import os
import sys
import MetaTrader5 as mt5

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
from PST_Core.engine.executor import PSTExecutor
import pandas_ta as ta
from dotenv import load_dotenv
import logging

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("PST_API")

load_dotenv() # Carga variables desde el archivo .env
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "PstAdmin01")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestión del ciclo de vida de la aplicación (FASE 42)."""
    await db.initialize()
    if not mt5.initialize():
        logger.error("❌ Fallo al inicializar MetaTrader 5 en la API")
    yield
    # Lógica de apagado si fuera necesaria
    # mt5.shutdown()

app = FastAPI(
    title="Pails Sentinel Trade API", 
    version="0.1.0",
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
        auth_enabled = os.getenv("AUTH_ENABLED", "true").lower() != "false"
        if auth_enabled:
            token = request.headers.get("X-PST-Token")
            if not token or token != WEB_PASSWORD:
                logger.warning(f"🔒 Intento de acceso bloqueado al endpoint {request.url.path}")
                logger.warning(f"🔧 Recibido: '{token}', Esperado: '{WEB_PASSWORD}', Method: {request.method}")
                return JSONResponse(status_code=401, content={"detail": "Unauthorized. Invalid or missing X-PST-Token."})
    return await call_next(request)

@app.post("/api/auth/login", tags=["Auth"])
async def login(req: LoginRequest):
    """Verifica la contraseña maestra para acceder al Dashboard."""
    if req.password == WEB_PASSWORD:
        return {"token": WEB_PASSWORD, "status": "authenticated"}
    raise HTTPException(status_code=401, detail="Contraseña incorrecta")

# --- PERSISTENT PATH LOGIC (FASE 45) ---
if hasattr(sys, '_MEIPASS'):
    # Si estamos en el EXE, la carpeta de trabajo es donde está el ejecutable
    # sys.executable da la ruta al .exe. Nos interesa su carpeta.
    base_persist_dir = os.path.dirname(sys.executable)
else:
    # En desarrollo, subimos un nivel desde la raíz del proyecto
    base_persist_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DB_PATH = os.getenv("DB_PATH", os.path.join(base_persist_dir, "PST_Core", "data", "pst_trading.db"))

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
    
    # Calcular PnL diario real (Cerrados hoy + Flotante actual)
    from datetime import datetime, time
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

    profit_p = status["equity"] - status["balance"] # Flotante
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

@app.get("/api/symbols", response_model=List[SymbolStatus], tags=["Config"])
async def get_symbols():
    """Obtiene la lista de símbolos y su telemetría de radar en vivo."""
    if not ensure_mt5_connected():
         raise HTTPException(status_code=503, detail="MetaTrader 5 not connected")
         
    symbols_cfg = await db.get_all_symbols_config()
    radar_data = await db.get_radar_data()
    profit_24h_map = await db.get_24h_profit_by_symbol()
    
    # Normalización para evitar fallos por sufijos de broker (.m, .pro, etc)
    positions = mt5.positions_get()
    pnl_map = {}
    if positions:
        for p in positions:
            sym_p = p.symbol.upper()
            pnl_map[sym_p] = pnl_map.get(sym_p, 0.0) + p.profit
            # También guardamos la base sin sufijos comunes si detectamos uno
            for suffix in [".m", ".pro", ".ecn", ".x", "i"]:
                if sym_p.endswith(suffix.upper()):
                    base = sym_p[:-len(suffix)]
                    pnl_map[base] = pnl_map.get(base, 0.0) + p.profit

    results = []
    for s in symbols_cfg:
        sym = s['symbol']
        radar = radar_data.get(sym, {})
        
        # Obtener precio actual y rendimiento diario
        tick = mt5.symbol_info_tick(sym)
        price = tick.bid if tick else 0.0
        
        import time
        market_open = True
        if tick and tick.time:
            # Si el último tick fue hace más de 10 minutos (600 segundos), asume cerrado
            if (int(time.time()) - tick.time) > 600:
                market_open = False
        else:
            market_open = False
        
        # Calcular cambio diario % (basado en la apertura de la vela D1 actual)
        daily_change = 0.0
        spark_data = []
        try:
            d1_data = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 0, 1)
            if d1_data is not None and len(d1_data) > 0:
                open_price = d1_data[0]['open']
                if open_price > 0:
                    daily_change = ((price - open_price) / open_price) * 100
            
            # Sparkline: últimos 20 cierres M5
            m5_data = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 20)
            if m5_data is not None:
                spark_data = [float(x['close']) for x in m5_data]
        except:
            pass

        # Procesar factores (compatibilidad lista vs dict)
        raw_factors = radar.get('factors', [])
        factors_list = []
        factors_map = {}
        
        if isinstance(raw_factors, list):
            factors_list = raw_factors
        elif isinstance(raw_factors, dict):
            # Nuevo formato: {"Strategy": {"factors": [...], "score": 85}}
            strat_states = await db.get_symbol_strategies(sym)
            
            # Normalización para cruzar nombres técnicos vs descriptivos (ej: PSTEMAFlow vs PST-EMA-Flow)
            def norm(n): return n.lower().replace("-","").replace("_","").strip()
            norm_map = {norm(k): v for k, v in strat_states.items()}

            for s_name_tech, s_data in raw_factors.items():
                # Buscar en DB por nombre técnico, descriptivo o normalizado
                db_cfg = strat_states.get(s_name_tech)
                if not db_cfg:
                    db_cfg = norm_map.get(norm(s_name_tech), {})
                
                is_strat_active = bool(db_cfg.get("is_active", 0))
                
                # Importación local para evitar NameError persistente
                from PST_API.schemas import Factor
                
                # Extraemos la configuración real guardada en la base de datos para cada estrategia técnica.
                s_risk_mode = db_cfg.get("risk_mode")
                s_risk_value = db_cfg.get("risk_value")
                s_sl_mult = db_cfg.get("sl_mult")
                s_tp_mult = db_cfg.get("tp_mult")
                s_use_breakeven = db_cfg.get("use_breakeven")
                s_use_trailing = db_cfg.get("use_trailing")
                s_be_mult = db_cfg.get("be_mult")
                s_ts_mult = db_cfg.get("ts_mult")

                if isinstance(s_data, dict) and "factors" in s_data:
                    factors_map[s_name_tech] = StrategyBreakdown(
                        score=s_data.get("score", 0.0),
                        factors=[Factor(**f) if isinstance(f, dict) else f for f in s_data.get("factors", [])],
                        is_active=is_strat_active,
                        risk_mode=s_risk_mode,
                        risk_value=s_risk_value,
                        sl_mult=s_sl_mult,
                        tp_mult=s_tp_mult,
                        use_breakeven=s_use_breakeven,
                        use_trailing=s_use_trailing,
                        be_mult=s_be_mult,
                        ts_mult=s_ts_mult
                    )
                else:
                    # Formato antiguo
                    factors_map[s_name_tech] = StrategyBreakdown(
                        score=0.0,
                        factors=s_data if isinstance(s_data, list) else [],
                        is_active=is_strat_active,
                        risk_mode=s_risk_mode,
                        risk_value=s_risk_value,
                        sl_mult=s_sl_mult,
                        tp_mult=s_tp_mult,
                        use_breakeven=s_use_breakeven,
                        use_trailing=s_use_trailing,
                        be_mult=s_be_mult,
                        ts_mult=s_ts_mult
                    )
            
            if factors_map:
                active_strat = radar.get('active_strategy')
                if active_strat in factors_map:
                    factors_list = factors_map[active_strat].factors
                else:
                    factors_list = list(factors_map.values())[0].factors

        # Calcular score global (máximo de las estrategias activas)
        overall_score = radar.get('score', 0.0)
        if factors_map:
            active_scores = [v.score for k, v in factors_map.items() if v.is_active]
            if active_scores:
                overall_score = max(active_scores)
            else:
                # Si no hay activas, mostrar el máximo general pero con precaución
                overall_score = max([v.score for v in factors_map.values()]) if factors_map else 0.0

        results.append(SymbolStatus(
            symbol=sym,
            is_active=bool(s['is_active']),
            market_open=market_open,
            regime=radar.get('regime', 'UNKNOWN'),
            score=overall_score,
            signal_direction=radar.get('signal_direction', 'NONE'),
            price=price,
            floating_pnl=pnl_map.get(sym, 0.0),
            profit_24h=profit_24h_map.get(sym.upper(), 0.0),
            daily_change_pct=daily_change,
            sparkline=spark_data,
            factors=factors_list,
            factors_map=factors_map,
            telemetry=calculate_symbol_telemetry(sym),
            active_strategy=radar.get("active_strategy", "PST-Auto")
        ))
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
            return APIResponse(status="error", message="Ticket inválido")
        result = await close_position_async(ticket)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"✅ Posición {ticket} cerrada manualmente.")
            return APIResponse(status="success", message=f"Posición {ticket} cerrada correctamente")
        err = result.comment if result else "Sin respuesta de MT5"
        return APIResponse(status="error", message=f"Error al cerrar: {err}")
    except Exception as e:
        logger.error(f"❌ Error close_trade: {e}")
        return APIResponse(status="error", message=str(e))

@app.post("/api/trades/manual", response_model=APIResponse, tags=["Trading"])
async def execute_manual_trade(order: ManualOrder):
    """Ejecuta una orden de compra o venta manual (Simple o Smart) en MT5."""
    if not ensure_mt5_connected():
        return APIResponse(status="error", message="MetaTrader 5 not connected")
        
    try:
        symbol = order.symbol.upper()
        
        if order.is_smart:
            # --- MODO SMART: Cálculo automático ---
            tf_m5 = mt5.TIMEFRAME_M5
            rates = mt5.copy_rates_from_pos(symbol, tf_m5, 0, 50)
            if rates is None or len(rates) == 0:
                return APIResponse(status="error", message="Failed to fetch ATR data for Smart order")
            
            import pandas as pd
            df = pd.DataFrame(rates)
            atr_s = ta.atr(df['high'], df['low'], df['close'], length=14)
            if atr_s is None or atr_s.empty:
                return APIResponse(status="error", message="ATR calculation failed")
                
            current_atr = float(atr_s.iloc[-1])
            sl_atr = current_atr * 3.0
            tp_atr = current_atr * 6.0
            
            result = await executor.execute_trade(
                symbol=symbol,
                signal_type=order.action.upper(),
                stop_loss_atr=sl_atr,
                take_profit_atr=tp_atr,
                strategy_name="Web-Smart",
                regime="MANUAL"
            )
            
            if not result:
                return APIResponse(status="error", message="Smart execution rejected by risk manager")
                
            return APIResponse(status="success", message=f"Smart {order.action} executed with SL/TP")
            
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
                "min_rr": s_cfg.get("min_rr", 1.5)
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

@app.post("/api/config/strategy", tags=["Config"])
async def update_strategy_config(update: StrategyConfigUpdate):
    """Actualiza la configuración detallada de una estrategia para un símbolo."""
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
        min_rr=update.min_rr
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Error actualizando estrategia")
    return {"status": "success"}

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


if __name__ == "__main__":
    import uvicorn
    import threading
    import webview
    import time

    # Usar 127.0.0.1 para evitar alertas de firewall en modo escritorio
    def run_server():
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")

    # Iniciar servidor en segundo plano
    t = threading.Thread(target=run_server, daemon=True)
    t.start()

    # Espera generosa para asegurar que el servidor FastAPI está arriba
    time.sleep(3)

    # Lanzar ventana nativa única
    webview.create_window(
        'Pails Sentinel Trade Bot', 
        'http://127.0.0.1:8000',
        width=1280, 
        height=850,
        resizable=True,
        min_size=(1000, 700)
    )
    webview.start(debug=True)
