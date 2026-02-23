import asyncio
import logging
from .mt5_async import init_mt5_async, shutdown_mt5_async, fetch_rates_async, sym_info_async, get_positions_async, get_mtf_data_async, send_order_async
from ..models.classifier import RegimeClassifier, RegimeMode
from ..models.database import PSTDatabase
from .executor import PSTExecutor
from ..strategies.pst_channel_master import PSTChannelMaster
from ..strategies.pst_rsi_equities import PSTRSIEquities
from ..strategies.pst_ema_flow import PSTEMAFlow
from ..strategies.pst_mean_reversion import PSTMeanReversion # NEW V3.2
from ..strategies.pst_liquidity_hunter import PSTLiquidityHunter # FASE 55
from ..strategies.pst_ai_oracle import PSTAIOracle # NEW V6.5 AI
from ..portfolio.manager import PortfolioManager
from ..utils.news_manager import news_mgr # NEW V3.0
from ..config import SL_ATR_MULTIPLIER, TP_ATR_MULTIPLIER, CRYPTO_KEYWORDS, ENABLED_STRATEGIES
import pandas_ta as ta
import pandas as pd
from typing import List
import json
import numpy as np
from ..utils.cooldown_manager import cooldown_mgr # Cooldown Import
from ..utils.notification_manager import notif_mgr
from .telegram_manager import telegram_bot

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
        self.active_stalking = {} # FASE 56: {strategy_id: {'direction': 1/-1, 'target_price': float, 'best_score': int}}
        self.classifier = RegimeClassifier()
        # Instanciar estrategias
        self.channel_master = PSTChannelMaster()
        self.ema_flow = PSTEMAFlow()
        self.mean_reversion = PSTMeanReversion()
        self.liquidity_hunter = PSTLiquidityHunter()
        
        # IA Dinámica (Selector por Símbolo)
        # Motor de IA Oráculo (Multi-Instancia para Competición)
        self.ai_oracle_gemini = PSTAIOracle(db=self.db, provider_type="gemini")
        self.ai_oracle_groq = PSTAIOracle(db=self.db, provider_type="groq")
        self.ai_oracle_ollama = PSTAIOracle(db=self.db, provider_type="ollama")

        # Lista maestra de todas las posibles estrategias instanciadas
        self._all_strategies = [
            self.ema_flow, 
            self.mean_reversion,
            self.liquidity_hunter,
            self.ai_oracle_gemini,
            self.ai_oracle_groq,
            self.ai_oracle_ollama
        ]
        
        # self.strategies se poblará dinámicamente en cada ciclo de run()
        self.strategies = []

    async def update_active_strategies(self):
        """Filtra las estrategias activas combinando config global y DB."""
        try:
            # 1. Obtener estados globales de la DB para todas las estrategias conocidas
            # Usamos ENABLED_STRATEGIES como la lista maestra inicial.
            global_enabled = []
            for s_name in ENABLED_STRATEGIES:
                db_val = await self.db.get_config(f"enabled_{s_name}", default="true")
                if db_val.lower() == "true":
                    global_enabled.append(s_name)

            # 2. Obtener estrategias específicas del símbolo (desde la tabla symbol_strategies)
            # Esto devuelve un dict {strategy_name: bool}
            sym_overrides = await self.db.get_symbol_strategies(self.symbol)
            
            # 3. Filtrar instancias finales
            # Combinamos _all_strategies y channel_master para el filtrado uniforme
            pool = self._all_strategies + [self.channel_master]
            
            self.strategies = []
            for s in pool:
                name = s.STRATEGY_NAME
                
                # Habilitado si:
                # 1. Está en el Whitelist Global (ENABLED_STRATEGIES) Y está activo en DB globalmente
                is_globally_on = name in global_enabled
                
                # 2. NO está desactivado específicamente para este símbolo
                is_active = sym_overrides.get(name, True)
                
                if is_globally_on and is_active:
                    self.strategies.append(s)

            # Mantener orden: Channel Master primero si está activo
            if self.channel_master in self.strategies:
                self.strategies.remove(self.channel_master)
                self.strategies.insert(0, self.channel_master)

        except Exception as e:
            logger.error(f"❌ Error actualizando estrategias para {self.symbol}: {e}")
            # Fallback seguro: solo lo que diga config.py
            self.strategies = [s for s in self._all_strategies if s.STRATEGY_NAME in ENABLED_STRATEGIES]

    async def run(self):
        logger.debug(f"🚀 Iniciando tarea para {self.symbol}")
        while self.running:
            # 0.1 Check for Daily Drawdown Lock
            if await self.portfolio.is_daily_locked():
                logger.warning(f"🔒 [{self.symbol}] Operativa bloqueada por Drawdown Diario. Esperando...")
                await asyncio.sleep(60)
                continue

            mtr_m1 = mtr_m5 = mtr_m15 = mtr_h1 = {"rsi": 0, "vol": 0, "adx": 0}
            volatility_factor = 1.0
            is_market_open = True
            user_levels = None 

            # 0. Actualizar estrategias activas (Dinámico)
            await self.update_active_strategies()

            # 0. Verificar si el símbolo sigue activo globalmente (Dashboard Toggle)
            if not await self.db.is_symbol_active(self.symbol):
                logger.info(f"🛑 Deteniendo tarea para {self.symbol} por desactivación desde Dashboard.")
                self.running = False
                break
            try:
                # 1. Obtener Datos Multi-Timeframe (M5, M15, H1, H4)
                mtf_data = await get_mtf_data_async(self.symbol)
                mtf_data['symbol'] = self.symbol # Inyectar símbolo para estrategias
                
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
                    is_crypto = any(k in self.symbol for k in CRYPTO_KEYWORDS)
                    
                    if is_crypto: 
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
                    if not is_crypto:
                        if now.weekday() == 4 and now.hour >= 23:
                            is_market_open = False

                    # Fallback de seguridad: Si la data es muy vieja (> 2h), seguro está cerrado
                    last_tick_time = df_regime.index[-1]
                    if (now - last_tick_time).total_seconds() > 7200: # 2 horas
                         is_market_open = False

                    # === 3.1b AUTO-CIERRE ESTRATÉGICO POR FIN DE SESIÓN ===
                    from .market_schedule import is_closing_soon
                    is_closing, closing_reason = is_closing_soon(self.symbol)
                    if is_closing:
                        # Auto-liquidar y omitir trading
                        positions = await get_positions_async()
                        if positions:
                            sym_positions = [p for p in positions if p.symbol == self.symbol]
                            for p in sym_positions:
                                p_type = "BUY" if p.type == 0 else "SELL"
                                logger.info(f"🛑 [{self.symbol}] CIERRE FORZADO ANTES DE SESIÓN: {closing_reason}. Liquidando ticket {p.ticket} ({p_type}).")
                                import MetaTrader5 as mt5
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
                                    "comment": f"Auto {closing_reason[:15]}",
                                    "type_time": mt5.ORDER_TIME_GTC,
                                    "type_filling": mt5.ORDER_FILLING_IOC,
                                }
                                res = await send_order_async(request)
                                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                    logger.info(f"✅ [{self.symbol}] Liquidación completada: {res.deal}")
                                    try:
                                        await self.db.update_trade_exit(
                                            ticket=p.ticket,
                                            price_out=price,
                                            profit=p.profit
                                        )
                                    except Exception as e:
                                        logger.error(f"Error actualizando trade en DB: {e}")
                                else:
                                    logger.error(f"⚠️ [{self.symbol}] Fallo al liquidar: {res.comment if res else 'Unknown error'}")

                        logger.debug(f"💤 [{self.symbol}] Mercado cerrando ({closing_reason}). Omitiendo señales.")
                        await asyncio.sleep(60)
                        continue

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

                    # 4. Ejecutar Estrategias (Unificado para Reporte + Señal)
                    signal = 0
                    strategy_name = "PST Strategy Hub"
                    atr_val = 0.0
                    current_score = 0
                    best_metadata = {}
                    
                    # Definir estrategias "Élite" que siempre queremos monitorear
                    elite_strats = [self.channel_master] + self.strategies # PSTRSIEquities() Desactivada
                    
                    # Mapeo de nombres para consistencia
                    STRAT_TRANS = {
                        "PSTChannelMaster": "Canal Maestro (T. Híbrido)",
                        "PSTRSIEquities": "RSI Equities (Multi-Asset)",
                        "PSTEMAFlow": "Flujo EMA (Tendencia)",
                        "PSTMeanReversion": "Reversión a la Media (Rangos)",
                        "PSTAIOracle_gemini": "🤖 IA Gemini (Cloud)",
                        "PSTAIOracle_groq": "🚀 IA Groq (Super Sónica)",
                        "PSTAIOracle_ollama": "🏠 IA Ollama (Local)"
                    }

                    # Obtener configuración de estrategias para este símbolo (NEW V3.3)
                    # Si no existe configuración, se asume True. 
                    # strat_config es un dict: {'PSTEMAFlow': False, ...}
                    strat_config = await self.db.get_symbol_strategies(self.symbol)
                    
                    # DEBUG CRITICO: Verificar tipo de strat_config
                    if isinstance(strat_config, list):
                        logger.warning(f"⚠️ [TYPE FIX] strat_config for {self.symbol} is LIST, converting to DICT. Val: {strat_config}")
                        strat_config = {}

                    # --- PRE-CALCULO DE MÉTRICAS (Necesario para el Dashboard incluso si cerrado) ---
                    # Calcular factor de volatilidad
                    atr_val = get_safe(atr_series) if 'atr_series' in locals() else 0.0
                    atr_mean = atr_series.rolling(window=20).mean().iloc[-1] if len(atr_series) > 20 else atr_val
                    volatility_factor = (atr_val / atr_mean) if atr_mean > 0 else 1.0

                    # DEFINIR PRECIO ACTUAL (Corrección NameError)
                    # Prioridad: M1 -> M5 -> H1 (df_regime)
                    price = price_h1 
                    if mtf_data.get('m1') is not None and len(mtf_data['m1']) > 0:
                        price = float(mtf_data['m1']['close'].iloc[-1])
                    elif mtf_data.get('m5') is not None and len(mtf_data['m5']) > 0:
                        price = float(mtf_data['m5']['close'].iloc[-1])

                    # Helper para calcular métricas de un DF
                    def calc_metrics(df_in):
                        if df_in is None or len(df_in) < 20: return {"rsi": 0, "vol": 0, "adx": 0}
                        try:
                            _rsi = ta.rsi(df_in['close'], length=14).iloc[-1]
                            # Vol Relativo: Vol Actual / Media 20
                            _v = df_in['tick_volume'].iloc[-1] if 'tick_volume' in df_in else 0
                            _v_ma = df_in['tick_volume'].rolling(20).mean().iloc[-1] if 'tick_volume' in df_in else 1
                            if _v_ma == 0: _v_ma = 1
                            _vol_rel = round(_v / _v_ma, 1) if 'tick_volume' in df_in else 0
                            _adx = ta.adx(df_in['high'], df_in['low'], df_in['close'], length=14)['ADX_14'].iloc[-1]
                            return {"rsi": round(_rsi, 1), "vol": _vol_rel, "adx": round(_adx, 1)}
                        except:
                            return {"rsi": 0, "vol": 0, "adx": 0}

                    mtr_m1 = calc_metrics(mtf_data.get('m1'))
                    mtr_m5 = calc_metrics(mtf_data.get('m5'))
                    mtr_m15 = calc_metrics(mtf_data.get('m15'))
                    mtr_h1 = calc_metrics(mtf_data.get('h1'))

                    # --- CORTOCIRCUITO POR MERCADO CERRADO ---
                    # Si el mercado está cerrado, saltamos la ejecución de estrategias (incluye IA)
                    if not is_market_open:
                        # Creamos un tech_data mínimo para alimentar el dashboard sin procesar estrategias
                        tech_data = {
                            "rsi": rsi_val, "adx": adx,
                            "mtf": {"m1": mtr_m1, "m5": mtr_m5, "m15": mtr_m15, "h1": mtr_h1},
                            "ema_alignment": ema_alignment,
                            "dist_ema21": float((price_h1 - ema21) / ema21 * 100) if ema21 > 0 else 0,
                            "dist_ema50": float((price_h1 - ema50) / ema50 * 100) if ema50 > 0 else 0,
                            "dist_ema200": float((price_h1 - ema200) / ema200 * 100) if ema200 > 0 else 0,
                            "atr_val": atr_val,
                            "volatility_factor": volatility_factor,
                            "strat_status": {},
                            "score": 0,
                            "active_strategy": "MERCADO CERRADO", 
                            "direction": 0,
                            "signal_direction": "NONE", 
                            "market_open": False
                        }
                        await self.db.log_regime(self.symbol, mode, adx, tech_data=json.dumps(tech_data))
                        await asyncio.sleep(self.interval)
                        continue

                    current_score = 0
                    strategy_name = None
                    best_metadata = {}
                    all_factors = {} # NEW: Multi-Strategy factors

                    for strat in elite_strats:
                        try:
                            # Usar el ID específico si existe (para Multi-IA) o el nombre de clase
                            strat_id = getattr(strat, 'STRAT_ID', type(strat).__name__)
                            is_strat_active = strat_config.get(strat_id, True)
                            if not is_strat_active:
                                continue

                            # 1. Calcular señal
                            s_result = await strat.calculate_signal(mtf_data, mode, user_levels=user_levels)
                            
                            s_score = s_result.get("score", 0)
                            s_meta = s_result.get("metadata", {})
                            s_name_raw = getattr(strat, 'STRATEGY_NAME', type(strat).__name__)
                            strat_id = getattr(strat, 'STRAT_ID', type(strat).__name__)

                            # --- NEW: STALKING LOGIC (ACECHO) ---
                            is_stalking_signal = s_result.get("is_stalking", False)
                            if is_stalking_signal:
                                direction = s_result.get("direction", 0)
                                # Buscamos la EMA21 en M5 como nivel de retroceso ideal
                                target_price = ema21_series.iloc[-1] if 'ema21_series' in locals() else price
                                self.active_stalking[strat_id] = {
                                    'direction': direction,
                                    'target_price': target_price,
                                    'score': s_score,
                                    'strategy_name': s_name_raw
                                }
                                logger.info(f"🐺 [STALKING] {self.symbol} vigilando {s_name_raw}. Esperando pullback a {target_price:.5f}")

                            # Chequeo de activación de acecho previo
                            if strat_id in self.active_stalking:
                                stalk_data = self.active_stalking[strat_id]
                                stalk_dir = stalk_data['direction']
                                target = stalk_data['target_price']
                                
                                # Condición de activación: El precio toca o supera el nivel de la EMA21 (pullback)
                                is_triggered = False
                                if stalk_dir == 1 and price <= target: is_triggered = True
                                elif stalk_dir == -1 and price >= target: is_triggered = True
                                
                                if is_triggered:
                                    logger.info(f"⚡ [STALKING TRIGGER] {self.symbol} Pullback completado en {price:.5f}. Disparando {s_name_raw}.")
                                    s_result["entry"] = stalk_dir
                                    s_score = max(s_score, 85) # Forzamos score alto por cumplimiento de pullback
                                    del self.active_stalking[strat_id]
                                elif s_score < 40: # Si la señal muere completamente, abortamos acecho
                                    logger.info(f"🧊 [STALKING CANCEL] {self.symbol} Señal de {s_name_raw} debilitada. Abortando acecho.")
                                    del self.active_stalking[strat_id]
                            
                            # Inyectar nombre limpio en metadata
                            s_meta["strategy_display"] = STRAT_TRANS.get(s_name_raw, s_name_raw)
                            s_name = STRAT_TRANS.get(s_name_raw, s_name_raw)

                            # Guardar factores y score para el Dashboard (Multi-Strategy Map)
                            all_factors[strat_id] = {
                                "score": s_score,
                                "factors": s_meta.get("factors_detailed", [])
                            }

                            # 3. Evaluar Ejecución (Trading Automático)
                            # Verificamos si la estrategia es apta para el régimen actual
                            strat_type = getattr(strat, 'STRATEGY_TYPE', "UNKNOWN")
                            is_strat_in_regime = False
                            
                            # Lógica de Compatibilidad de Régimen (Crisis WR Fix)
                            if strat_type == mode: 
                                is_strat_in_regime = True
                            elif strat_type == "ALL":
                                is_strat_in_regime = True
                            
                            # EMA Flow: Estrictamente Tendencia (PROHIBIDO en Volátil o Rango)
                            if "PSTEMAFlow" in type(strat).__name__ and mode != RegimeMode.TREND:
                                is_strat_in_regime = False

                            # Si es la Maestra, siempre monitorea
                            if "ChannelMaster" in type(strat).__name__:
                                is_strat_in_regime = True

                            # --- NEW: REGIME BLOCK LOGGING & SCORE CAPPING ---
                            # Si la estrategia tiene un score alto pero el régimen no es compatible
                            if not is_strat_in_regime and s_score >= 70:
                                if s_result.get("entry", 0) != 0:
                                    await self.db.log_signal(self.symbol, mode, s_name, "BLOCKED_REGIME", s_score, price)
                                s_score = 60 # Visual Cap (User Req)
                                s_meta["blocked_reason"] = "REGIMEN"

                            # 2. Actualizar mejor score para el HUD
                            if s_score > current_score:
                                # --- NEW: TELEGRAM SIGNAL ALERT (FASE 52) ---
                                # if s_score >= 80 and s_score > current_score:
                                #     sig_type_str_alert = "BUY" if s_result.get("entry", 0) == 1 else "SELL" if s_result.get("entry", 0) == -1 else "ALERT"
                                #     # Solo alertar si hay dirección clara
                                #     if sig_type_str_alert != "ALERT":
                                #         asyncio.create_task(telegram_bot.send_signal_alert(self.symbol, sig_type_str_alert, s_score, price, s_name))

                                current_score = s_score
                                strategy_name = s_name
                                best_metadata = s_meta
                                atr_val = s_result.get("atr", 0)

                            # Check rápido de trading mode
                            trading_mode = await self.db.get_config('trading_mode', 'AUTO')
                            
                            # Diagnostic log before the execution gate
                            logger.debug(f"⚙️ [{self.symbol}] Execution Gate Check: is_strat_in_regime={is_strat_in_regime}, entry_signal={s_result.get('entry', 0)}, trading_mode='{trading_mode}', is_market_open={is_market_open}")
                            
                            if is_strat_in_regime and s_result.get("entry", 0) != 0 and trading_mode == 'AUTO' and is_market_open:
                                
                                # 0. Cooldown Check
                                is_blocked, msg = cooldown_mgr.is_blocked(self.symbol)
                                if is_blocked:
                                    logger.info(f"🧊 [{self.symbol}] BLOQUEO DE SEGURIDAD (Cooldown/Histerésis). {msg}. Evitando operativa circular.")
                                    if s_score >= 70:
                                        await self.db.log_signal(self.symbol, mode, s_name, "BLOCKED_COOLDOWN", s_score, price)
                                        best_metadata["blocked_reason"] = "COOLDOWN"
                                        current_score = 60 # Visual Cap
                                else:
                                    # 1. Ejecución
                                    sig_val = s_result.get("entry", 0)
                                    sig_type_str = "BUY" if sig_val == 1 else "SELL"
                                    
                                    # News Filter
                                    if news_mgr.is_news_near(self.symbol, window_minutes=30):
                                         logger.warning(f"🛑 [NEWS BLOCK] {self.symbol} signal blocked.")
                                         best_metadata["news_blocked"] = True
                                         best_metadata["blocked_reason"] = "NOTICIAS"
                                         if s_score >= 70:
                                              await self.db.log_signal(self.symbol, mode, s_name, f"BLOCKED_NEWS_{sig_type_str}", s_score, price)
                                              current_score = 60 # Visual Cap
                                    else:
                                        # Check Portfolio Limits & Pyramiding
                                        open_positions = await get_positions_async()
                                        can_trade = await self.portfolio.can_open_trade(self.symbol, sig_type_str, open_positions, s_name)
                                        
                                        if can_trade and s_score >= 70:
                                            # FIRE!
                                            logger.info(f"⚡ [TRADE] {self.symbol} {sig_type_str} by {s_name} (Score: {s_score})")
                                            
                                            # Bloc de seguridad in-flight (PST v7.0)
                                            self.portfolio.register_in_flight(self.symbol)
                                            
                                            try:
                                                # SL/TP dinámico basado en ATR (si la estrategia lo provee o default)
                                                atr_current = s_result.get("atr", 0)
                                                if atr_current == 0: atr_current = atr_val # Fallback 1: Market ATR

                                                # Fallback 2: Absolute Price fallback if ATR is still 0 (Crucial for Stocks/Indices)
                                                if atr_current <= 0 and price > 0:
                                                    logger.warning(f"⚠️ [SAFETY] ATR 0 detected for {self.symbol}. Using 0.5% price fallback.")
                                                    atr_current = price * 0.005 
                                                
                                                # SAFETY PAD: Aumentar distancia para Acciones/Indices para evitar "Invalid Stops"
                                                sl_mult = SL_ATR_MULTIPLIER
                                                tp_mult = TP_ATR_MULTIPLIER
                                                
                                                # Si es simbolo largo (Acciones) o Indices, damos mas aire
                                                if len(self.symbol) > 3 or "500" in self.symbol or "30" in self.symbol:
                                                    sl_mult = 3.5  # Antes 2.0
                                                    tp_mult = 5.0  # Antes 3.0
                                                
                                                sl_dist = atr_current * sl_mult 
                                                
                                                # --- NEW: SOPORTE PARA TP TÉCNICO ---
                                                tp_price_target = s_result.get("tp_price", 0)
                                                if tp_price_target > 0:
                                                    # Calcular distancia exacta al objetivo técnico
                                                    tp_dist = abs(price - tp_price_target)
                                                    logger.info(f"🎯 [TECHNICAL TP] {self.symbol} usando objetivo: {tp_price_target:.5f} (Dist: {tp_dist:.5f})")
                                                else:
                                                    tp_dist = atr_current * tp_mult 

                                                # Asegurar distancia mínima absoluta para evitar code 10016 (Invalid Stops)
                                                # En lugar de hardcodear 0.20, usamos un % del precio para ser compatible con Crypto y Forex
                                                min_dist = price * 0.0015 # 0.15% del precio como mínimo absoluto
                                                if sl_dist < min_dist: sl_dist = min_dist
                                                if tp_dist < min_dist: tp_dist = min_dist
                                                
                                                await self.executor.execute_trade(
                                                    self.symbol, sig_type_str, sl_dist, tp_dist, s_name, mode, best_metadata
                                                )
                                                await self.db.log_signal(self.symbol, mode, s_name, sig_type_str, s_score, price)
                                                
                                                # Marcar visualmente
                                                signal = sig_val
                                                strategy_name = s_name
                                            except Exception as e:
                                                logger.error(f"❌ Fallo crítico en ejecución para {self.symbol}: {e}")
                                            finally:
                                                # Liberamos tras un tiempo prudencial (30s) para asegurar que MT5 refleje la posición
                                                # y evitar que el siguiente ciclo de 10s dispare de nuevo si hay latencia.
                                                async def delayed_clear(sym):
                                                    await asyncio.sleep(30)
                                                    self.portfolio.clear_in_flight(sym)
                                                
                                                asyncio.create_task(delayed_clear(self.symbol))
                                        else:
                                            # Bloc por Riesgo/Portafolio
                                            if s_score >= 70:
                                                 await self.db.log_signal(self.symbol, mode, s_name, f"BLOCKED_{sig_type_str}", s_score, price)
                                                 best_metadata["blocked_reason"] = "RIESGO/PORTFOLIO"
                                                 current_score = 60 # Visual Cap
                        
                        except Exception as e:
                            logger.exception(f"❌ [{self.symbol}] Error estrat {type(strat).__name__}: {e}")

                    # Fallback de nombre si sigue siendo None o similar
                    if not strategy_name or str(strategy_name) == "None":
                        strategy_name = "PST Strategy Hub"


                    tech_data = {
                        "rsi": rsi_val, # Legacy H1
                        "adx": adx,     # Legacy H1
                        "mtf": { 
                            "m1": mtr_m1, # NEW
                            "m5": mtr_m5,
                            "m15": mtr_m15,
                            "h1": mtr_h1
                        },
                        "ema_alignment": ema_alignment,
                        "dist_ema21": float((price_h1 - ema21) / ema21 * 100) if ema21 > 0 else 0,
                        "dist_ema50": float((price_h1 - ema50) / ema50 * 100) if ema50 > 0 else 0,
                        "dist_ema200": float((price_h1 - ema200) / ema200 * 100) if ema200 > 0 else 0,
                        "atr_val": atr_val,
                        "volatility_factor": volatility_factor,
                        "strat_status": make_serializable(best_metadata),
                        "score": current_score,
                        "active_strategy": strategy_name, 
                        "direction": best_metadata.get("direction", 0),
                        "signal_direction": "BUY" if signal > 0 else ("SELL" if signal < 0 else "NONE"), 
                        "market_open": is_market_open 
                    }
                    
                    if rsi_val == 0:
                        logger.warning(f"⚠️ [{self.symbol}] RSI es 0.0. Velas: {history_len}. Close[-1]: {price_h1}")

                    # --- NEW: SENTINEL RADAR TELEMETRY ---
                    # Guardamos un snapshot para que el Dashboard vea la intención operativa (incluso antes de disparar)
                    best_dir_val = best_metadata.get("direction", 0)
                    sig_direction = "BUY" if best_dir_val > 0 else ("SELL" if best_dir_val < 0 else "NONE")
                    
                    await self.db.save_radar_snapshot(
                        symbol=self.symbol,
                        score=current_score,
                        regime=str(mode),
                        direction=sig_direction,
                        factors=all_factors # PASS ALL FACTORS DICT
                    )

                    # Log en la Base de Datos para historial
                    await self.db.log_regime(self.symbol, mode, adx, tech_data=json.dumps(tech_data))
                    
                # 2. SECCIÓN DE TRADING (SYNC CON HUD) -> MIGRADO AL BUCLE 1
                # Bloque eliminado por redundancia y error de tipos.

                # Log de latido (Status Monitor)
                status_icon = "📈" if mode == RegimeMode.TREND else "↕️" if mode == RegimeMode.RANGE else "⚠️"
                if trading_mode == 'MANUAL':
                    status_msg = "[MODO MANUAL] - Monitoreo activo"
                    if not is_market_open: status_msg = "⛔ [MERCADO CERRADO]"
                    logger.debug(f"{status_icon} {self.symbol:<10} | MODO: {mode:<10} | {status_msg}")
                else:
                    # aquí solo logueamos el estado general en debug
                    df_m5_dbg = mtf_data.get('m5')
                    logger.debug(f"{status_icon} {self.symbol:<10} | MODO: {mode:<10} | Precio: {df_m5_dbg['close'].iloc[-1] if df_m5_dbg is not None else 'N/A'}")

                await asyncio.sleep(self.interval)
            except Exception as e:
                logger.exception(f"❌ Error en {self.symbol}: {e}")
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
                                total_pnl = d.profit + d.swap + d.commission
                                await db.update_trade_cierre(d.position_id, d.price, total_pnl)
                                logger.info(f"✅ Sincronizado CIERRE: {d.symbol} (Ticket {d.position_id}) | PnL: {total_pnl:.2f}")
                                
                                # --- NEW: TELEGRAM CLOSURE ALERT (FASE 52) ---
                                trade_type_str = "BUY" if d.type == 1 else "SELL" # DEAL_TYPE_BUY=0, SELL=1 (Cierre de un BUY es un SELL deal)
                                asyncio.create_task(telegram_bot.send_trade_notification(d.position_id, trade_type_str, d.symbol, d.price, total_pnl, is_closing=True))
                                
                                # COOLDOWN TRIGGER: Si fue pérdida REAL (superando tolerancia de -2.0 para BE sucio), registrar en CooldownManager
                                if total_pnl < -2.0: # TOLERANCIA BE: Perdonamos pérdidas menores a 2€ (comisiones/swap)
                                    from ..utils.cooldown_manager import cooldown_mgr
                                    cooldown_mgr.register_loss(d.symbol, duration_minutes=60)
                                else:
                                    # Hysteresis para trades en ganancia/BE (Evita Hyper-trading)
                                    from ..utils.cooldown_manager import cooldown_mgr
                                    cooldown_mgr.register_trade_finish(d.symbol, duration_minutes=15)
                                    
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
                
                # --- NEW: STALE TRADES AUTO-CLEANUP ---
                # Buscamos trades que la DB cree que están abiertos
                open_db_trades = await db.get_active_trades()
                if open_db_trades:
                    import MetaTrader5 as mt5 # Ref
                    current_positions = mt5.positions_get()
                    active_tickets = [p.ticket for p in current_positions] if current_positions else []
                    
                    for t in open_db_trades:
                        ticket = t.get('ticket')
                        if ticket and ticket not in active_tickets:
                            # El trade no está en MT5. Si es viejo (> 12h), lo cerramos "en falso" para liberar el bot
                            try:
                                time_in = datetime.fromisoformat(t['time_in'])
                                if (datetime.now() - time_in).total_seconds() > 43200: # 12 horas
                                    logger.warning(f"🧹 [CLEANUP] Cerrando trade huérfano en DB: {t['symbol']} (Ticket {ticket})")
                                    await db.update_trade_cierre(ticket, 0.0, 0.0)
                            except:
                                pass # Formato de fecha inv. o error
            else:
                 logger.debug("🔍 Escaneando historial mt5: 0 deals encontrados.")
            
            await asyncio.sleep(60) # Sincronizar cada minuto
        except Exception as e:
            logger.error(f"❌ Error en sincronización robusta: {e}")
            import traceback
            traceback.print_exc()
            await asyncio.sleep(30)

async def global_trade_management(executor: PSTExecutor):
    """Tarea periódica para gestionar todas las posiciones abiertas y el Drawdown Diario."""
    from ..config import DAILY_LOSS_EXIT_USD
    logger.info("🛡️ Iniciando Sistema de Protección Dinámica y Drawdown Diario...")
    while True:
        try:
            # A. Gestión de Trailing/BE individual
            await executor.manage_active_trades()
            
            # B. Monitorización de PnL Diario (Seguro FTMO)
            # Solo chequeamos si no estamos ya bloqueados
            is_locked = await executor.portfolio.is_daily_locked()
            if not is_locked:
                acc = await executor.portfolio.get_account_status()
                if acc and "daily_pnl" in acc:
                    daily_pnl = acc["daily_pnl"]
                    
                    # Si la pérdida diaria (negativa) supera el límite
                    if daily_pnl <= -DAILY_LOSS_EXIT_USD:
                        logger.critical(f"🛑 [DRAWDOWN DIARIO] Pérdida de ${abs(daily_pnl):.2f} alcanzada! Límite: ${DAILY_LOSS_EXIT_USD}")
                        
                        # 1. Cierre de Emergencia
                        await executor.panic_close_all()
                        
                        # 2. Bloqueo Persistente
                        await executor.portfolio.set_daily_lock()
                        
                        # 3. Notificación (Opcional, se puede añadir Telegram aquí)
                        from ..utils.notification_manager import notif_mgr
                        await notif_mgr.send_simple_alert(f"🚨 [PANIC CLOSE] Operativa cerrada. Pérdida Diaria: ${abs(daily_pnl):.2f}. Bot BLOQUEADO hasta mañana.")
            
            await asyncio.sleep(10) # Revisión recurrente
        except Exception as e:
            logger.error(f"❌ Error en gestión global de trades/drawdown: {e}")
            await asyncio.sleep(10)

async def start_v6(symbols: List[str]):
    """Punto de entrada principal para el bot PST."""
    # Inicializar Componentes Core
    db = PSTDatabase()
    await db.initialize()
    
    from ..config import MAX_RISK_PCT, MAX_DRAWDOWN_PCT
    portfolio = PortfolioManager(db=db, max_risk_pct=MAX_RISK_PCT, max_drawdown_pct=MAX_DRAWDOWN_PCT)

    success = await init_mt5_async()
    if not success:
        logger.error("❌ Falló la conexión con MT5")
        return

    # 1. Obtener Símbolos Activos desde DB si no se pasan (o para sobreescribir)
    import sqlite3
    try:
        conn = sqlite3.connect("PST_Core/data/pst_trading.db")
        cursor = conn.cursor()
        cursor.execute("SELECT symbol FROM symbols_config WHERE is_active = 1")
        db_active_symbols = [row[0] for row in cursor.fetchall()]
        conn.close()
        if db_active_symbols:
            logger.info(f"📋 Cargando {len(db_active_symbols)} símbolos desde DB Configurator.")
            symbols = db_active_symbols
    except Exception as e:
        logger.warning(f"⚠️ Error cargando símbolos desde DB: {e}. Usando lista por defecto.")

    logger.info("💎 PST ASYNC CORE ONLINE 💎")
    
    # --- CONFIGURACIÓN DE LOGS EN DB ---
    try:
        from ..utils.logger_utils import DBLogHandler
        db_handler = DBLogHandler(db)
        db_handler.setLevel(logging.INFO)
        # Añadir al logger del orquestador y opcionalmente al root
        logger.addHandler(db_handler)
        logging.getLogger().addHandler(db_handler) # Capturar todo el sistema
        logger.info("📝 Live Log Streaming: ACTIVE")
    except Exception as e:
        logger.warning(f"⚠️ No se pudo inicializar DBLogHandler: {e}")

    # Initialize Notification Manager with DB
    from ..utils.notification_manager import notif_mgr
    notif_mgr.db = db
    
    executor = PSTExecutor(db, portfolio)
    
    # --- NEW: INITIALIZE TELEGRAM LISTENER (FASE 52.2) ---
    try:
        from .telegram_manager import telegram_bot
        # Pasamos executor porque contiene acceso a db, portfolio y funciones de trade
        asyncio.create_task(telegram_bot.start_listener(executor))
    except Exception as e:
        logger.error(f"❌ Error lanzando Telegram Listener: {e}")

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
