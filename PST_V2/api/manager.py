from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
import os
import logging
import json
import time
import MetaTrader5 as mt5
from dotenv import load_dotenv
from pathlib import Path

# Cargar configuración
load_dotenv()
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "PstAdmin01")

# Determinar rutas absolutas usando Pathlib
api_file = Path(__file__).resolve()
api_dir = api_file.parent
project_root = api_dir.parent  # PST_V2/
root_dir = project_root.parent # PailsSentinelTrade/
dist_path = root_dir / "PST_Web" / "dist"

logger = logging.getLogger("PST_V2.API")

class LoginRequest(BaseModel):
    password: str


class SocketManager:
    """Gestiona conexiones WebSocket y difusión de eventos (Broadcasting)."""
    
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"🌐 WebSocket: Nueva conexión establecida. (Total: {len(self.active_connections)})")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info("🌐 WebSocket: Conexión cerrada.")

    async def broadcast(self, message: dict):
        """Envía un mensaje a todos los clientes conectados."""
        if not self.active_connections:
            return
            
        data = json.dumps(message)
        disconnected = []
        
        for connection in self.active_connections:
            try:
                await connection.send_text(data)
            except Exception:
                disconnected.append(connection)
                
        for conn in disconnected:
            self.disconnect(conn)

socket_manager = SocketManager()
app = FastAPI(title="Sentinel V2 API Bridge")

# Configurar CORS por si acaso (aunque sea mismo origen)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await socket_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        socket_manager.disconnect(websocket)
    except Exception as e:
        socket_manager.disconnect(websocket)

# --- ENDPOINTS SEGURIDAD ---

@app.post("/api/auth/login")
async def login(req: LoginRequest):
    if req.password == WEB_PASSWORD:
        return {"token": WEB_PASSWORD, "status": "authenticated"}
    raise HTTPException(status_code=401, detail="Invalid access protocol")

# --- ENDPOINTS COMPATIBILIDAD V2 ---

@app.get("/api/account")
async def get_account():
    acc = mt5.account_info()
    if not acc:
        raise HTTPException(status_code=503, detail="MT5 not connected")
    return {
        "balance": acc.balance,
        "equity": acc.equity,
        "margin": acc.margin,
        "margin_free": acc.margin_free,
        "margin_level": acc.margin_level,
        "daily_pnl": 0.0, # WIP: Sincronizar con DB
        "active_pnl": acc.equity - acc.balance
    }

@app.get("/api/symbols")
async def get_symbols():
    # Versión ligera para V2
    symbols = ["EURUSD", "BTCUSD", "ETHUSD", "XAUUSD", "DE40.CASH", "US30.CASH"]
    results = []
    for sym in symbols:
        tick = mt5.symbol_info_tick(sym)
        if tick:
            results.append({
                "symbol": sym,
                "price": tick.bid,
                "is_active": True,
                "market_open": True,
                "score": 0.0,
                "signal_direction": "NONE",
                "daily_change_pct": 0.0,
                "floating_pnl": 0.0,
                "daily_pnl": 0.0,
                "total_pnl": 0.0,
                "profit_24h": 0.0,
                "regime": "UNKNOWN",
                "sparkline": [],
                "factors": [],
                "factors_map": {},
                "telemetry": {"spread": 0.0, "health": "HEALTHY"}
            })
    return results

@app.get("/api/trades/active")
async def get_active_trades():
    pos = mt5.positions_get()
    results = []
    if pos:
        for p in pos:
            results.append({
                "ticket": p.ticket,
                "symbol": p.symbol,
                "type": "BUY" if p.type == 0 else "SELL",
                "volume": p.volume,
                "price_open": p.price_open,
                "price_current": p.price_current,
                "sl": p.sl,
                "tp": p.tp,
                "profit": p.profit,
                "time_in": str(p.time),
                "strategy": "PST-Auto"
            })
    return results

# --- ENDPOINTS DE COMPATIBILIDAD (EVITAN CRASHES EN WEB) ---

@app.get("/api/logs")
async def get_logs():
    return {"logs": []}

@app.get("/api/news/upcoming")
async def get_news():
    return {"upcoming": [], "is_global_block": False}

@app.get("/api/performance")
async def get_performance():
    return {
        "profit_factor": 0.0,
        "win_rate": 0.0,
        "total_trades": 0,
        "net_profit": 0.0,
        "avg_duration_minutes": 0.0
    }

@app.get("/api/performance/equity")
async def get_equity():
    return []

@app.get("/api/performance/strategies")
async def get_strat_perf():
    return []

@app.get("/api/config/bot/trading_mode")
async def get_bot_mode():
    return {"value": "AUTO"}

@app.get("/api/matrix")
async def get_matrix():
    return []

@app.get("/api/performance/analytics")
async def get_analytics():
    return {
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
        "avg_trade": 0.0,
        "sharpe_ratio": 0.0,
        "max_drawdown": 0.0,
        "recovery_factor": 0.0
    }

@app.get("/api/ohlc/{symbol}")
async def get_ohlc(symbol: str, timeframe: str = "M5"):
    tf_map = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "D1": mt5.TIMEFRAME_D1
    }
    tf = tf_map.get(timeframe.upper(), mt5.TIMEFRAME_M5)
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, 100)
    if rates is None:
        return []
    
    results = []
    for r in rates:
        results.append({
            "time": int(r['time']),
            "open": float(r['open']),
            "high": float(r['high']),
            "low": float(r['low']),
            "close": float(r['close']),
            "volume": int(r['tick_volume'])
        })
    return results

import pandas as pd
import pandas_ta as ta

@app.get("/api/symbols/{symbol}/telemetry")
async def get_telemetry(symbol: str):
    timeframes = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1
    }
    
    results = {}
    for name, tf in timeframes.items():
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, 50)
        if rates is not None and len(rates) > 20:
            df = pd.DataFrame(rates)
            rsi_series = ta.rsi(df['close'], length=14)
            rsi_val = rsi_series.iloc[-1] if not rsi_series.empty else None
            
            # Cálculo simple de tendencia/puntuación (ADX proxy o momentum)
            results[name] = {
                "rsi": round(float(rsi_val), 1) if rsi_val is not None else None,
                "adx": round(float(abs(df['close'].iloc[-1] - df['close'].iloc[-10])), 2),
                "health": "OK"
            }
        else:
            results[name] = {"rsi": None, "adx": None, "health": "FAIL"}
            
    return results

@app.get("/api/user_levels/{symbol}")
async def get_user_levels(symbol: str):
    return []

@app.post("/api/user_levels/{symbol}")
async def save_user_levels(symbol: str, data: dict):
    return {"status": "saved"}

@app.get("/api/history")
async def get_history():
    return []

@app.get("/health")
async def health_check():
    return {"status": "online", "version": "2.0.0-alpha"}

# Servir el Frontend (Sentinel Dashboard) - DIAGNÓSTICO DE RUTAS
logger.info(f"🔎 Buscando Frontend en: {dist_path}")
if dist_path.exists():
    assets_dir = dist_path / "assets"
    if assets_dir.exists():
        logger.info(f"✅ Carpeta de Assets detectada: {assets_dir}")
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
    else:
        logger.error(f"❌ ERROR: Carpeta de Assets NO ENCONTRADA en {assets_dir}")
    
    # Ruta para el favicon y otros raíz
    @app.get("/vite.svg")
    async def get_vite():
        return FileResponse(str(dist_path / "vite.svg"))

    # El catch-all debe ir AL FINAL de todo para que no robe peticiones /api
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        return FileResponse(str(dist_path / "index.html"))
else:
    logger.warning(f"⚠️ Frontend no encontrado en {dist_path}")
    @app.get("/")
    async def root():
        return {"message": "Sentinel V2 API Online. (Dashboard dist folder not found)"}
