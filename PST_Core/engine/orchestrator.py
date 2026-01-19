import asyncio
import logging
from .mt5_async import init_mt5_async, shutdown_mt5_async, fetch_rates_async, sym_info_async, get_positions_async, get_mtf_data_async
from ..models.classifier import RegimeClassifier, RegimeMode
from ..models.database import PSTDatabase
from .executor import PSTExecutor
from ..strategies.pst_channel_master import PSTChannelMaster
from ..strategies.pst_rsi_equities import PSTRSIEquities
from ..strategies.pst_ema_flow import PSTEMAFlow
from ..portfolio.manager import PortfolioManager
import pandas_ta as ta
import pandas as pd
import pandas as pd
from typing import List
import json
import numpy as np

# Configuración básica de logs para el Corazón PST
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("PST-Orchestrator")

class SymbolTask:
    """Representa el ciclo de vida de un símbolo individual."""
    def __init__(self, symbol: str, db: PSTDatabase, portfolio: PortfolioManager, executor: PSTExecutor, interval: int = 10):
        self.symbol = symbol
        self.db = db
        self.portfolio = portfolio
        self.executor = executor
        self.interval = interval
        self.running = True
        self.classifier = RegimeClassifier()
        # Instanciar estrategias élite
        # SIMPLIFICACIÓN ESTRATÉGICA: Master + RSI Equities + EMA Flow
        self.strategies = {
            RegimeMode.TREND: [PSTChannelMaster(), PSTEMAFlow()],
            RegimeMode.RANGE: [PSTChannelMaster(), PSTRSIEquities()],
            RegimeMode.VOLATILE: [PSTChannelMaster(), PSTRSIEquities(), PSTEMAFlow()]
        }

    async def run(self):
        logger.debug(f"🚀 Iniciando tarea para {self.symbol}")
        while self.running:
            user_levels = None # Inicialización de seguridad
            try:
                # 1. Obtener Datos Multi-Timeframe (M5, M15, H1, H4)
                mtf_data = await get_mtf_data_async(self.symbol)
                
                # Obtener niveles manuales del usuario (Crítico para Trading Híbrido)
                user_levels_list = await self.db.get_user_levels(self.symbol)
                
                # Retrieve Channel Config
                import json
                raw_cfg = await self.db.get_config(f"channel_cfg_{self.symbol}", default="{}")
                channel_config = json.loads(raw_cfg) if raw_cfg else {}

                # Convert to dict format expected by strategy
                user_levels = {
                    'levels': user_levels_list,
                    'config': channel_config,
                    'symbol': self.symbol
                }
                
                # Validación básica: Necesitamos al menos M15 y H1
                if mtf_data is None or mtf_data['m15'] is None or mtf_data['h1'] is None:
                    if mtf_data is None:
                         logger.warning(f"⚠️ [{self.symbol}] No se obtuvieron datos mtf.")
                    elif mtf_data['h1'] is None:
                         logger.warning(f"⚠️ [{self.symbol}] No hay datos H1 disponibles.")
                    await asyncio.sleep(5)
                    continue

                # Usamos H1 para clasificación de régimen (Trend/Range)
                df_regime = mtf_data['h1'] # Mantenemos H1 como base para régimen
                
                if df_regime is not None and len(df_regime) >= 14:
                    mode, adx = self.classifier.classify(df_regime)
                    
                    # Función auxiliar para extraer valores de forma segura
                    def get_safe(series, default=0.0):
                        try:
                            val = series.iloc[-1]
                            return float(val) if not pd.isna(val) else default
                        except: return default

                    # Log de depuración
                    history_len = len(df_regime)
                    
                    # Helper para serializar tipos de numpy (bool_, int64, etc)
                    def make_serializable(obj):
                        if isinstance(obj, dict):
                            return {k: make_serializable(v) for k, v in obj.items()}
                        elif isinstance(obj, list):
                            return [make_serializable(v) for v in obj]
                        elif isinstance(obj, (np.int_, np.intc, np.intp, np.int8,
                                            np.int16, np.int32, np.int64, np.uint8,
                                            np.uint16, np.uint32, np.uint64)):
                            return int(obj)
                        elif isinstance(obj, (np.float16, np.float32, np.float64)):
                            return float(obj)
                        elif isinstance(obj, (np.bool_, bool)): # Handle numpy bool and standard bool just in case
                            return bool(obj)
                        elif isinstance(obj, np.ndarray):
                            return make_serializable(obj.tolist())
                        return obj
                    logger.debug(f"📊 [{self.symbol}] Procesando {history_len} velas H1...")

                    # Calcular indicadores de forma directa (más seguro que asignar al DF)
                    rsi_series = ta.rsi(df_regime['close'], length=14)
                    ema21_series = ta.ema(df_regime['close'], length=21)
                    ema50_series = ta.ema(df_regime['close'], length=50)
                    ema200_series = ta.ema(df_regime['close'], length=200)
                    atr_series = ta.atr(df_regime['high'], df_regime['low'], df_regime['close'], length=14)
                    
                    # DEBUG: Verificar si RSI se calculó correctamente
                    if rsi_series is None:
                        logger.error(f"❌ [{self.symbol}] pandas_ta.rsi() devolvió None")
                        rsi_val = 0.0
                    elif len(rsi_series) == 0:
                        logger.error(f"❌ [{self.symbol}] RSI series está vacía")
                        rsi_val = 0.0
                    else:
                        # Usar iloc[-2] en vez de [-1] para evitar la vela actual incompleta
                        rsi_val = get_safe(rsi_series, default=0.0)
                        # Si sigue siendo 0, intentar con valores anteriores
                        if rsi_val == 0.0 and len(rsi_series) > 20:
                            for i in range(2, min(10, len(rsi_series))):
                                test_val = rsi_series.iloc[-i]
                                if not pd.isna(test_val) and test_val > 0:
                                    rsi_val = float(test_val)
                                    logger.debug(f"🔍 [{self.symbol}] RSI encontrado en posición -{i}: {rsi_val:.1f}")
                                    break
                    
                    price_h1 = float(df_regime['close'].iloc[-1])
                    ema21 = get_safe(ema21_series)
                    ema50 = get_safe(ema50_series)
                    ema50 = get_safe(ema50_series)
                    ema200 = get_safe(ema200_series)

                    # 3.1 Chequeo de Mercado Abierto/Cerrado (Hardcoded Schedule)
                    import datetime
                    now = datetime.datetime.now()
                    is_market_open = True
                    
                    # Reglas por tipo de activo
                    if "BTC" in self.symbol or "ETH" in self.symbol: 
                        # Cripto 24/7
                        is_market_open = True
                    elif "EU50" in self.symbol:
                        # Indices Europeos (08:00 - 22:00 aprox)
                        current_hour = now.hour + (now.minute / 60)
                        if now.weekday() >= 5: is_market_open = False
                        elif not (8.0 <= current_hour < 22.0): is_market_open = False
                    elif "US500" in self.symbol:
                        # Indices USA (Futuros/CFD casi 24h, solo cierra finde)
                        if now.weekday() >= 5: is_market_open = False
                    elif any(s in self.symbol for s in ["AAPL", "TSLA", "NVDA", "GOOG", "AMZN", "MSFT"]):
                        # Acciones USA (15:30 - 22:00)
                        current_hour = now.hour + (now.minute / 60)
                        if now.weekday() >= 5: is_market_open = False
                        elif not (15.5 <= current_hour < 22.0): is_market_open = False
                    else:
                        # Forex / Commodities (XAU, XAG, EURUSD...)
                        # Cerrado fines de semana
                        if now.weekday() >= 5: # Sábado=5, Domingo=6
                            is_market_open = False

                    # --- VERIFICACIONES GLOBALES DE CIERRE ---
                    
                    # 1. Regla Específica para Metales e Índices Globales (Fin de Semana)
                    for s_chk in ["XAU", "XAG", "GOLD", "SILVER", "US500", "EU50"]:
                        if s_chk in self.symbol and now.weekday() >= 5:
                            is_market_open = False
                            
                    # 2. Cierre Viernes Noche (para evitar gaps de finde)
                    # Si es Viernes (4) y son más de las 23:00 (hora local del servidor), marcar como cerrado
                    # EXCEPCION: Criptomonedas (24/7)
                    if not ("BTC" in self.symbol or "ETH" in self.symbol):
                        if now.weekday() == 4 and now.hour >= 23:
                            is_market_open = False

                    # Fallback de seguridad: Si la data es muy vieja (> 2h), seguro está cerrado
                    last_tick_time = df_regime.index[-1]
                    if (now - last_tick_time).total_seconds() > 7200: # 2 horas
                         is_market_open = False

                    # Alineación de EMAs (RESTORADO)
                    ema_alignment = "NEUTRAL"
                    if ema21 > 0 and ema50 > 0:
                        # Si no hay ema200 (pocos datos), usamos solo 21 y 50
                        if ema200 > 0:
                            if ema21 > ema50 > ema200: ema_alignment = "BULL"
                            elif ema21 < ema50 < ema200: ema_alignment = "BEAR"
                        else:
                            if ema21 > ema50: ema_alignment = "BULL (Soft)"
                            elif ema21 < ema50: ema_alignment = "BEAR (Soft)"

                    # 4. Ejecutar Estrategias del Régimen
                    signal = 0
                    strategy_name = "None"
                    atr_val = 0.0
                    current_score = 0  # <--- NEW: Score para dashboard
                    best_metadata = {}
                    
                    if mode in self.strategies:
                        for strat in self.strategies[mode]:
                            try:
                                # Validación extra
                                if isinstance(strat, str) or type(strat).__name__ == 'str': continue
                                if not hasattr(strat, 'calculate_signal'): continue

                                # Ejecutar
                                s_result = await strat.calculate_signal(mtf_data, mode, user_levels=user_levels)
                                s_signal = s_result.get("entry", 0)
                                s_atr = s_result.get("atr", 0)
                                s_meta = s_result.get("metadata", {})
                                s_score = s_result.get("score", 0)
                                
                                # Si hay señal, priorizamos
                                if s_signal != 0:
                                    signal = s_signal
                                    strategy_name = strat.STRATEGY_NAME
                                    atr_val = s_atr
                                    best_metadata = s_meta
                                    current_score = s_score  # <--- Capturamos score
                                    break 
                                
                                # Si no hay señal, guardamos la metadata y score de la primera estrategia válida (para visualizar)
                                if not best_metadata and s_score > 0:
                                    best_metadata = s_meta
                                    current_score = s_score
                            except Exception as e:
                                logger.error(f"❌ [{self.symbol}] Error estrat {strat}: {e}")

                    # Calcular factor de volatilidad para el dashboard
                    atr_mean = atr_series.rolling(window=20).mean().iloc[-1] if len(atr_series) > 20 else atr_val
                    volatility_factor = (atr_val / atr_mean) if atr_mean > 0 else 1.0

                    tech_data = {
                        "rsi": rsi_val,
                        "ema_alignment": ema_alignment,
                        "dist_ema21": float((price_h1 - ema21) / ema21 * 100) if ema21 > 0 else 0,
                        "dist_ema50": float((price_h1 - ema50) / ema50 * 100) if ema50 > 0 else 0,
                        "dist_ema200": float((price_h1 - ema200) / ema200 * 100) if ema200 > 0 else 0,
                        "atr_val": atr_val,
                        "volatility_factor": volatility_factor,
                        "strat_status": make_serializable(best_metadata),
                        "score": current_score,
                        "active_strategy": strategy_name, 
                        "signal_direction": "BUY" if signal > 0 else ("SELL" if signal < 0 else "NONE"), # <--- NEW: Dirección Explicita
                        "market_open": is_market_open 
                    }
                    
                    if rsi_val == 0:
                        logger.warning(f"⚠️ [{self.symbol}] RSI es 0.0. Velas: {history_len}. Close[-1]: {price_h1}")

                    # Log en la Base de Datos para historial
                    await self.db.log_regime(self.symbol, mode, adx, tech_data=json.dumps(tech_data))
                    
                # 2. SECCIÓN DE TRADING (SYNC CON HUD)
                # Solo operamos si el mercado está abierto y tenemos datos M5
                df_m5 = mtf_data.get('m5')
                trading_mode = await self.db.get_config('trading_mode', 'AUTO')
                
                if df_m5 is not None and is_market_open and trading_mode == 'AUTO':
                    price = df_m5['close'].iloc[-1]
                    total_score_buy = 0
                    total_score_sell = 0
                    
                    # Solo iteramos sobre las estrategias del régimen ACTUAL (Evita señales fantasma)
                    active_strats = self.strategies.get(mode, [])
                    for strat in active_strats:
                        try:
                            # Reutilizamos el mtf_data para consistencia total
                            sig = await strat.calculate_signal(mtf_data, mode, user_levels=user_levels)
                            
                            if sig["entry"] != 0:
                                sig_type = "BUY" if sig["entry"] == 1 else "SELL"
                                open_positions = await get_positions_async()
                                can_trade = await self.portfolio.can_open_trade(self.symbol, sig_type, open_positions)
                                
                                score = sig.get("score", 0)
                                if can_trade:
                                    if score >= 70: # Umbral de ejecución
                                        logger.info(f"⚡ [TRADE] {self.symbol} disparado por {strat.STRATEGY_NAME} (Score: {score})")
                                        await self.executor.execute_trade(self.symbol, sig_type, sig["atr"]*1.5, sig["atr"]*3.0, strat.STRATEGY_NAME, mode)
                                        await self.db.log_signal(self.symbol, mode, strat.STRATEGY_NAME, sig_type, score, price)
                                    else:
                                        logger.debug(f"🔍 [SIGNAL] {self.symbol} {sig_type} descartada por score bajo ({score})")
                                else:
                                    # Solo loguear si el score era alto para no spammear
                                    if score >= 70:
                                        await self.db.log_signal(self.symbol, mode, strat.STRATEGY_NAME, f"BLOCKED_{sig_type}", score, price)
                        except Exception as e:
                            logger.error(f"❌ Error ejecutando estrategia {strat} para {self.symbol}: {e}")

                # Log de latido (Status Monitor)
                status_icon = "📈" if mode == RegimeMode.TREND else "↕️" if mode == RegimeMode.RANGE else "⚠️"
                if trading_mode == 'MANUAL':
                    status_msg = "[MODO MANUAL] - Monitoreo activo"
                    if not is_market_open: status_msg = "⛔ [MERCADO CERRADO]"
                    logger.debug(f"{status_icon} {self.symbol:<10} | MODO: {mode:<10} | {status_msg}")
                else:
                    # En modo AUTO, el log lo da la estrategia si dispara, 
                    # aquí solo logueamos el estado general en debug
                    logger.debug(f"{status_icon} {self.symbol:<10} | MODO: {mode:<10} | Precio: {df_m5['close'].iloc[-1] if df_m5 is not None else 'N/A'}")

                await asyncio.sleep(self.interval)
            except Exception as e:
                logger.error(f"❌ Error en {self.symbol}: {e}")
                await asyncio.sleep(10)

async def sync_trades_task(db: PSTDatabase):
    """Sincroniza operaciones cerradas en MT5 con la DB local, IMPORTANDO las que falten."""
    logger.info("🔄 Iniciando Sincronizador ROBUSTO de Historial (Auto-Discovery)...")
    while True:
        try:
            from datetime import datetime, timedelta
            from .mt5_async import get_history_deals_async
            import MetaTrader5 as mt5 # Necesario para constantes si no están en mt5_async
            
            # Obtener deals cerrados (Últimos 30 días para cubrir todo el mes)
            # Esto permite "descubrir" operaciones antiguas si se borró la DB o se operó desde el móvil
            deals = await get_history_deals_async(days=30)
            
            if deals:
                logger.debug(f"🔍 Escaneando historial mt5 (Num deals: {len(deals)})...") 
                count_synced = 0
                count_imported = 0
                
                for d in deals:
                    # Buscamos deals de SALIDA:
                    # Entry=1 (DEAL_ENTRY_OUT)
                    # Entry=2 (DEAL_ENTRY_INOUT) - Reversiones
                    if d.entry in [1, 2]:
                        # 1. Verificar si existe el trade en DB local
                        trade_exists = await db.check_trade_exists(d.position_id)
                        
                        if trade_exists:
                            # Si existe, verificamos si está abierto (price_out = 0) para cerrarlo
                            is_open = await db.is_trade_open(d.position_id)
                            if is_open:
                                await db.update_trade_cierre(d.position_id, d.price, d.profit + d.swap + d.commission)
                                logger.info(f"✅ Sincronizado CIERRE: {d.symbol} (Ticket {d.position_id}) | PnL: {d.profit}")
                                count_synced += 1
                        else:
                            # 2. Si NO existe, es una operación externa/antigua -> IMPORTAR
                            trade_data = {
                                "symbol": d.symbol,
                                "type": "BUY" if d.type == 1 else "SELL", # DEAL_TYPE_BUY=0, SELL=1. Si cierras un BUY(0), el deal es SELL(1).
                                "volume": d.volume,
                                "price_in": 0.0, # Desconocido sin buscar deal entrada
                                "price_out": d.price,
                                "sl": 0.0,
                                "tp": 0.0,
                                "profit": d.profit + d.swap + d.commission,
                                "time_in": str(datetime.fromtimestamp(d.time)), # Usamos time salida como aprox
                                "time_out": str(datetime.fromtimestamp(d.time)),
                                "regime_at_entry": "EXTERNAL",
                                "strategy_name": "AUTO_SYNC",
                                "ticket": d.position_id
                            }
                            await db.save_trade(trade_data)
                            logger.info(f"📥 Importado AUTOMÁTICO: {d.symbol} (Ticket {d.position_id}) | PnL: {d.profit}")
                            count_imported += 1
                
                if count_synced > 0 or count_imported > 0:
                    logger.info(f"🔄 Sync Report: {count_synced} cerrados, {count_imported} importados.")
            else:
                 logger.debug("🔍 Escaneando historial mt5: 0 deals encontrados.")
            
            await asyncio.sleep(60) # Sincronizar cada minuto
        except Exception as e:
            logger.error(f"❌ Error en sincronización robusta: {e}")
            import traceback
            traceback.print_exc()
            await asyncio.sleep(30)

async def global_trade_management(executor: PSTExecutor):
    """Tarea periódica para gestionar todas las posiciones abiertas."""
    logger.info("🛡️ Iniciando Sistema de Protección Dinámica (Trailing/BE)...")
    while True:
        try:
            await executor.manage_active_trades()
            await asyncio.sleep(10) # Revisión cada 10 segundos
        except Exception as e:
            logger.error(f"❌ Error en gestión global de trades: {e}")
            await asyncio.sleep(10)

async def start_v6(symbols: List[str]):
    """Punto de entrada principal para el bot PST."""
    # Inicializar Componentes Core
    db = PSTDatabase()
    await db.initialize()
    
    portfolio = PortfolioManager()

    success = await init_mt5_async()
    if not success:
        logger.error("❌ Falló la conexión con MT5")
        return

    logger.info("💎 PST ASYNC CORE ONLINE 💎")
    
    executor = PSTExecutor(db, portfolio)
    # Crear tareas para cada símbolo pasando DB y Portfolio
    symbol_tasks = [SymbolTask(sym, db, portfolio, executor).run() for sym in symbols]
    
    # Tareas de gestión global y sincronización
    management_task = [global_trade_management(executor)]
    sync_task = [sync_trades_task(db)]
    
    tasks = symbol_tasks + management_task + sync_task
    
    try:
        # Ejecutar todas las tareas en paralelo de forma indefinida
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("🛑 Deteniendo el bot...")
    finally:
        await shutdown_mt5_async()

if __name__ == "__main__":
    # Test rápido
    asyncio.run(start_v6(["EURUSD", "BTCUSD", "US500.cash"]))
