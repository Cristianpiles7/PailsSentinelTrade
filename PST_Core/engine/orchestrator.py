import asyncio
import logging
from datetime import datetime
from .mt5_async import init_mt5_async, shutdown_mt5_async, fetch_rates_async, sym_info_async, get_positions_async, get_mtf_data_async, send_order_async
from ..models.classifier import RegimeClassifier, RegimeMode
from ..models.database import PSTDatabase
from .executor import PSTExecutor
from ..strategies.pst_range_breaker import PSTRangeBreaker
from ..strategies.pst_precision_scalping import PSTPrecisionScalping
from ..portfolio.manager import PortfolioManager
from ..utils.news_manager import news_mgr # NEW V3.0
from ..config import SL_ATR_MULTIPLIER, TP_ATR_MULTIPLIER, CRYPTO_KEYWORDS, ENABLED_STRATEGIES, DB_PATH, DAILY_LOSS_PCT
import pandas_ta as ta
import pandas as pd
from typing import List
import json
import numpy as np
from ..utils.cooldown_manager import cooldown_mgr # Cooldown Import
from ..utils.notification_manager import notif_mgr
from .telegram_manager import telegram_bot
from .macro_trend_filter import MacroTrendFilter

# Configuración básica de logs para el Corazón PST
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'
)
# v2.5.9: log persistente a archivo rotativo. El .log llevaba sin escribirse desde
# abril y sin él no se pueden auditar a posteriori errores de ejecución (la DB
# system_logs solo retiene las últimas 500 filas ≈ minutos).
from logging.handlers import RotatingFileHandler
from pathlib import Path as _Path
_log_file = _Path(__file__).resolve().parents[2] / "v2_sentinel_prime.log"
if not any(isinstance(h, RotatingFileHandler) for h in logging.getLogger().handlers):
    _fh = RotatingFileHandler(_log_file, maxBytes=5_000_000, backupCount=2, encoding="utf-8")
    _fh.setLevel(logging.INFO)
    _fh.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s %(name)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
    logging.getLogger().addHandler(_fh)
# Silenciar spam de la API de Telegram y peticiones HTTP a nivel global
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

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
        self.macro_filter = MacroTrendFilter()
        # Instancias de estrategias
        self.range_breaker = PSTRangeBreaker()
        self.precision_scalping = PSTPrecisionScalping()

        # Lista maestra para filtrado dinámico en update_active_strategies()
        self._all_strategies = [
            self.range_breaker,
            self.precision_scalping,
        ]

        # self.strategies se poblará en cada ciclo de run()
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
            self.strategies = []
            for s in self._all_strategies:
                name = s.STRATEGY_NAME
                is_globally_on = name in global_enabled
                is_active = sym_overrides.get(name, True)
                if is_globally_on and is_active:
                    self.strategies.append(s)

        except Exception as e:
            logger.error(f"❌ Error actualizando estrategias para {self.symbol}: {e}")
            # Fallback seguro: solo lo que diga config.py
            self.strategies = [s for s in self._all_strategies if s.STRATEGY_NAME in ENABLED_STRATEGIES]

    async def run(self):
        logger.info(f"🔍 [MONITOR] Iniciando análisis para {self.symbol}")
        while self.running:
            # 0.1 Kill-switch de pérdida diaria — para toda la operativa si se supera el límite
            if await self.portfolio.is_daily_locked():
                logger.warning(f"🔒 [{self.symbol}] Operativa bloqueada por pérdida diaria. Esperando siguiente sesión.")
                await asyncio.sleep(60)
                continue

            # Verificar si debemos activar el bloqueo diario ahora
            try:
                import MetaTrader5 as _mt5
                _acc = _mt5.account_info()
                if _acc is not None and _acc.balance > 0:
                    _acc_status = await self.portfolio.get_account_status()
                    if _acc_status:
                        _daily_pnl = _acc_status.get("daily_pnl", 0.0)
                        _loss_limit = -(_acc.balance * DAILY_LOSS_PCT / 100.0)
                        if _daily_pnl <= _loss_limit:
                            logger.error(
                                f"🔴 [KILL-SWITCH] Pérdida diaria {_daily_pnl:.2f} supera límite "
                                f"{_loss_limit:.2f} ({DAILY_LOSS_PCT}% balance). Bloqueando operativa."
                            )
                            await self.portfolio.set_daily_lock()
                            await notif_mgr.send(
                                f"🔴 KILL-SWITCH ACTIVADO\n"
                                f"Pérdida del día: {_daily_pnl:.2f}\n"
                                f"Límite ({DAILY_LOSS_PCT}%): {_loss_limit:.2f}\n"
                                f"Operativa suspendida hasta mañana."
                            )
                            await asyncio.sleep(60)
                            continue
            except Exception as _e:
                logger.debug(f"[Kill-Switch Check] Error al verificar pérdida diaria: {_e}")

            mtr_m1 = mtr_m5 = mtr_m15 = mtr_h1 = {"rsi": 0, "vol": 0, "adx": 0}
            volatility_factor = 1.0
            is_market_open = True
            user_levels = None 
            trading_mode = 'AUTO'
            mode = RegimeMode.RANGE # Default seguro

            # 0. Actualizar estrategias activas (Dinámico)
            await self.update_active_strategies()

            # 0. Verificar si el símbolo sigue activo globalmente (Dashboard Toggle)
            if not await self.db.is_symbol_active(self.symbol):
                logger.info(f"🛑 Deteniendo tarea para {self.symbol} por desactivación desde Dashboard.")
                self.running = False
                break
            try:
                # 1. Obtener Datos Multi-Timeframe (M1, M5, M15, H1, H4)
                # Si alguna estrategia activa es de tipo SCALPING, priorizamos M1
                needs_m1 = any(getattr(s, 'STRATEGY_TYPE', '') == 'SCALPING' for s in self.strategies)
                mtf_data = await get_mtf_data_async(self.symbol, include_m1=needs_m1)
                mtf_data['symbol'] = self.symbol # Inyectar símbolo para estrategias
                
                # --- NEW: DXY CORRELATION CONTEXT (USD INDEX) ---
                # Si el par contiene USD, inyectamos la tendencia del DXY
                dxy_data = None
                if "USD" in self.symbol.upper():
                    try:
                        # Intentamos obtener USDX (o DXY según broker)
                        dxy_mtf = await fetch_rates_async("USDX", 100, 16385) # M5
                        if dxy_mtf is not None and not dxy_mtf.empty:
                            dxy_ema21 = ta.ema(dxy_mtf['close'], length=21).iloc[-1]
                            dxy_close = dxy_mtf['close'].iloc[-1]
                            dxy_data = {
                                'price': dxy_close,
                                'ema21': dxy_ema21,
                                'trend': 1 if dxy_close > dxy_ema21 else -1,
                                'status': 'BULLISH' if dxy_close > dxy_ema21 else 'BEARISH'
                            }
                    except: pass
                
                mtf_data['dxy'] = dxy_data
                
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
                    
                    # Definir estrategias activas para este ciclo
                    elite_strats = self.strategies

                    # Mapeo de STRATEGY_NAME → nombre legible para el dashboard
                    STRAT_TRANS = {
                        "PST-AlphaTrend":        "AlphaTrend (Tendencia H1)",
                        "PST-RangeBreaker":      "RangeBreaker (Reversión M15)",
                        "PST-PrecisionScalping": "PrecisionScalping (Scalp M1)",
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
                        # --- FIX: Asegurar que el radar se actualice con score 0 si está cerrado ---
                        await self.db.save_radar_snapshot(self.symbol, 0, mode, 0, factors=[])
                        await asyncio.sleep(self.interval)
                        continue

                    current_score = 0
                    strategy_name = None
                    best_metadata = {}
                    all_factors = {} 

                    # PRE-FETCH: Información del Símbolo y Modo de Trading (FASE 69 Fix)
                    symbol_info = await sym_info_async(self.symbol)
                    trading_mode = await self.db.get_config('trading_mode', 'AUTO')

                    for strat in elite_strats:
                        try:
                            # Usar STRATEGY_NAME para sincronizar con la DB (Matrix Editor)
                            strat_id = getattr(strat, 'STRATEGY_NAME', type(strat).__name__)
                            is_strat_active = strat_config.get(strat_id, {}).get('is_active', True)
                            if not is_strat_active:
                                continue

                            # Pausa automática por drawdown semanal de estrategia
                            if await self.db.is_strategy_paused(strat_id):
                                logger.debug(f"⏸️ [{self.symbol}] {strat_id} pausada por drawdown semanal.")
                                continue

                            # 1. Calcular señal
                            # Pasar info extra como spread y configuración de la estrategia
                            spread_pts = symbol_info.spread if symbol_info else 0
                            spread_dist = spread_pts * symbol_info.point if symbol_info else 0
                            
                            # Obtener parámetros específicos de esta estrategia desde la DB
                            s_params_raw = strat_config.get(strat_id, {}).copy()
                            # Limpiar metadatos de DB para evitar colisión de argumentos
                            for key in ['symbol', 'strategy_name', 'id', 'last_update', 'is_active']:
                                s_params_raw.pop(key, None)
                            
                            # --- FIX: ELIMINAR VALORES NONE PARA EVITAR TypeError EN LAS ESTRATEGIAS ---
                            s_params = {k: v for k, v in s_params_raw.items() if v is not None}
                            
                            s_result = await strat.calculate_signal(
                                mtf_data, 
                                mode, 
                                user_levels=user_levels, 
                                spread_points=spread_pts, 
                                spread_dist=spread_dist,
                                symbol=self.symbol,
                                **s_params # Inyectar parámetros: score_threshold, risk_value, etc.
                            )
                            
                            # Usar el ID sincronizado definido al inicio del bucle: strat_id (STRATEGY_NAME)
                            s_score = s_result.get("score", 0)
                            s_meta = s_result.get("metadata", {})
                            s_name_raw = strat_id

                            # --- MACRO TREND FILTER (H1/H4) ---
                            # Aplica antes de evaluar ejecución para reducir entradas contra tendencia macro
                            if s_score > 0 and s_result.get("entry", 0) != 0:
                                _macro = self.macro_filter.analyze(mtf_data)
                                _sig_dir = s_result.get("entry", 0)
                                _strat_type = getattr(strat, "STRATEGY_TYPE", "TREND")
                                _macro_ok, _macro_adj = _macro.allows_direction(_sig_dir, _strat_type)
                                if not _macro_ok:
                                    logger.info(
                                        f"🚫 [MACRO FILTER] {self.symbol}/{strat_id} BLOQUEADO — "
                                        f"señal {'BUY' if _sig_dir==1 else 'SELL'} vs tendencia macro {_macro.reason}"
                                    )
                                    s_score = 0
                                    s_result["entry"] = 0
                                    if isinstance(s_meta, dict):
                                        s_meta["blocked_reason"] = "MACRO_TREND"
                                        s_meta["macro_reason"] = _macro.reason
                                elif _macro_adj != 0:
                                    s_score = max(0, min(100, s_score + _macro_adj))
                                    if isinstance(s_meta, dict):
                                        s_meta["macro_bias"] = _macro.reason
                                    logger.debug(
                                        f"📊 [MACRO FILTER] {self.symbol}/{strat_id} "
                                        f"score ajustado {_macro_adj:+d} → {s_score} ({_macro.reason})"
                                    )

                            # --- NEW: STALKING LOGIC (ACECHO) ---
                            is_stalking_signal = s_result.get("is_stalking", False)
                            if is_stalking_signal:
                                stalk_dir = s_result.get("direction", 0)
                                # Buscamos la EMA21 en M5 como nivel de retroceso ideal
                                stalk_target = s_result.get("target_price", price)
                                self.active_stalking[strat_id] = {
                                    'direction': stalk_dir,
                                    'target_price': stalk_target,
                                    'score': s_score,
                                    'strategy_name': s_name_raw
                                }
                                logger.debug(f"🐺 [STALKING] {self.symbol} vigilando {s_name_raw}. Esperando pullback a {stalk_target:.5f}")

                            # Chequeo de activación de acecho previo
                            if strat_id in self.active_stalking:
                                stalk_data = self.active_stalking[strat_id]
                                s_stalk_dir = stalk_data['direction']
                                s_target = stalk_data['target_price']
                                
                                # Condición de activación: El precio toca o supera el nivel de la EMA21 (pullback)
                                is_triggered = False
                                if s_stalk_dir == 1 and price <= s_target: is_triggered = True
                                elif s_stalk_dir == -1 and price >= s_target: is_triggered = True
                                
                                if is_triggered:
                                    logger.debug(f"⚡ [STALKING TRIGGER] {self.symbol} Pullback completado en {price:.5f}. Disparando {s_name_raw}.")
                                    s_result["entry"] = s_stalk_dir
                                    s_score = max(s_score, 85) # Forzamos score alto por cumplimiento de pullback
                                    del self.active_stalking[strat_id]
                                elif s_score < 40: # Si la señal muere completamente, abortamos acecho
                                    logger.debug(f"🧊 [STALKING CANCEL] {self.symbol} Señal de {s_name_raw} debilitada (Score {s_score}). Abortando acecho.")
                                    del self.active_stalking[strat_id]
                            
                            # Inyectar dirección y nombre limpio en metadata para el Radar/HUD
                            s_meta["direction"] = s_result.get("entry", 0)
                            s_display = STRAT_TRANS.get(s_name_raw, s_name_raw)
                            s_meta["strategy_display"] = s_display
                            s_name = s_name_raw # Mantener ID técnico original para el Executor

                            # Guardar factores y score para el Dashboard (Multi-Strategy Map)
                            all_factors[strat_id] = {
                                "score": s_score,
                                "factors": s_result.get("factors_detailed") or s_result.get("metadata", {}).get("factors_detailed", [])
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
                            
                            # Reglas de compatibilidad de régimen por STRATEGY_TYPE
                            # AlphaTrend: solo en tendencia
                            if strat_type == "TREND" and mode != RegimeMode.TREND:
                                is_strat_in_regime = False
                            # RangeBreaker: rango Y volátil (reversiones de picos)
                            if strat_type == "RANGE" and mode not in [RegimeMode.RANGE, RegimeMode.VOLATILE]:
                                is_strat_in_regime = False
                            # PrecisionScalping: opera en cualquier régimen (ALL implícito por liquidez)
                            if strat_type == "SCALPING":
                                is_strat_in_regime = True

                            # --- NEW: REGIME BLOCK LOGGING & SCORE CAPPING ---
                            # Si la estrategia tiene un score alto pero el régimen no es compatible
                            if not is_strat_in_regime and s_score >= 70:
                                if s_result.get("entry", 0) != 0:
                                    await self.db.log_signal(self.symbol, mode, s_name, "BLOCKED_REGIME", s_score, price, blocked_reason="REGIMEN")
                                
                                s_score = 65 # Visual Cap: régimen incompatible, no ejecutará
                                
                                s_meta["blocked_reason"] = "REGIMEN"

                            # 2. Actualizar mejor score para el HUD
                            if s_score > current_score:
                                current_score = s_score
                                strategy_name = s_name
                                best_metadata = s_meta
                                atr_val = s_result.get("atr", 0)

                            # Diagnostic log before the execution gate
                            logger.debug(f"⚙️ [{self.symbol}] Execution Gate Check: is_strat_in_regime={is_strat_in_regime}, entry_signal={s_result.get('entry', 0)}, trading_mode='{trading_mode}', is_market_open={is_market_open}")
                            
                            if is_strat_in_regime and s_result.get("entry", 0) != 0 and trading_mode == 'AUTO' and is_market_open:
                                
                                # 0. Cooldown Check
                                is_blocked, msg = cooldown_mgr.is_blocked(self.symbol)
                                if is_blocked:
                                    logger.info(f"🧊 [{self.symbol}] BLOQUEO DE SEGURIDAD (Cooldown/Histerésis). {msg}. Evitando operativa circular.")
                                    if s_score >= 70:
                                        await self.db.log_signal(self.symbol, mode, s_name, "BLOCKED_COOLDOWN", s_score, price, blocked_reason="COOLDOWN")
                                        best_metadata["blocked_reason"] = "COOLDOWN"
                                        current_score = 60 # Visual Cap
                                else:
                                    # 1. Ejecución
                                    sig_val = s_result.get("entry", 0)
                                    sig_type_str = "BUY" if sig_val == 1 else "SELL"
                                    
                                    # News Filter
                                    news_window = int(await self.db.get_config('news_block_window', '30'))
                                    if news_mgr.is_news_near(self.symbol, window_minutes=news_window):
                                         logger.warning(f"🛑 [NEWS BLOCK] {self.symbol} signal blocked.")
                                         best_metadata["news_blocked"] = True
                                         best_metadata["blocked_reason"] = "NOTICIAS"
                                         if s_score >= 70:
                                              await self.db.log_signal(self.symbol, mode, s_name, f"BLOCKED_NEWS_{sig_type_str}", s_score, price, blocked_reason="NOTICIAS")
                                              current_score = 60 # Visual Cap
                                    else:
                                        # Check Portfolio Limits & Pyramiding
                                        open_positions = await get_positions_async()
                                        can_trade = await self.portfolio.can_open_trade(self.symbol, sig_type_str, open_positions, s_name)
                                        
                                        if can_trade:
                                            # --- SAFE REVERSAL LOGIC (SAR) ---
                                            # Si hay una posición abierta en la dirección CONTRARIA, exigimos Score >= 85 para girar.
                                            has_opposite = False
                                            if open_positions:
                                                for p in open_positions:
                                                    if p.symbol == self.symbol:
                                                        p_is_buy = p.type == 0
                                                        p_is_sell = p.type == 1
                                                        if (sig_type_str == "BUY" and p_is_sell) or (sig_type_str == "SELL" and p_is_buy):
                                                            has_opposite = True
                                                            break
                                            
                                            entry_threshold = s_meta.get("threshold_used")
                                            if entry_threshold is None:
                                                entry_threshold = s_params.get("score_threshold", 70)
                                            try:
                                                entry_threshold = float(entry_threshold)
                                            except (TypeError, ValueError):
                                                entry_threshold = 70.0

                                            min_score = max(entry_threshold, 85) if has_opposite else entry_threshold
                                            
                                            if s_score >= min_score:
                                                if has_opposite:
                                                    logger.info(f"🔄 [SAFE REVERSAL] {self.symbol} disparando Giro Seguro (Score {s_score} >= 85).")
                                                
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
                                                    
                                                    # Si es simbolo largo (Acciones) o Indices, damos mas aire (Exceptuando Scalping)
                                                    if (len(self.symbol) > 3 or "500" in self.symbol or "30" in self.symbol) and "Scalper" not in s_name:
                                                        sl_mult = 3.5  # Antes 2.0
                                                        tp_mult = 5.0  # Antes 3.0

                                                    # En régimen VOLATILE el precio necesita más espacio para respirar
                                                    if mode == RegimeMode.VOLATILE:
                                                        sl_mult *= 1.5

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

                                                    # v2.5.7: pasar el TP técnico (VWAP/Donchian) al executor para que lo USE.
                                                    # Antes lo calculaba la estrategia pero el executor lo ignoraba y usaba ATR.
                                                    # A/B fiel: el técnico mejora forex/metal/cripto (+0.07..+0.10R); en ÍNDICES
                                                    # el TP-VWAP corta las rachas ganadoras → se deja ATR (no se inyecta).
                                                    if tp_price_target > 0 and isinstance(best_metadata, dict):
                                                        try:
                                                            from ..utils.tech_utils import get_asset_class
                                                            _tp_grp = get_asset_class(self.symbol)
                                                        except Exception:
                                                            _tp_grp = ""
                                                        if _tp_grp != "INDEX":
                                                            best_metadata["target_price_tp"] = tp_price_target

                                                    exec_result = await self.executor.execute_trade(
                                                        self.symbol, sig_type_str, sl_dist, tp_dist, s_name, mode, best_metadata
                                                    )
                                                    if exec_result is not None:
                                                        await self.db.log_signal(self.symbol, mode, s_name, sig_type_str, s_score, price)

                                                        # Marcar visualmente
                                                        signal = sig_val
                                                        strategy_name = s_name
                                                    else:
                                                        # La orden no llegó al mercado (news guard, margen, retcode
                                                        # de MT5...); antes se registraba como ejecutada igualmente
                                                        # y el journal mostraba trades fantasma.
                                                        logger.warning(f"🚫 [EXEC FAIL] {self.symbol} {sig_type_str}: el executor no abrió la orden (ver error anterior).")
                                                        await self.db.log_signal(self.symbol, mode, s_name, f"BLOCKED_EXEC_{sig_type_str}", s_score, price, blocked_reason="EXEC_FAIL")
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
                                                logger.info(f"🧱 [SCORE BLOCK] {self.symbol} {sig_type_str} por {s_name} bloqueado. Score {s_score} < mínimo {min_score}.")
                                                if s_score >= 50:
                                                    await self.db.log_signal(self.symbol, mode, s_name, f"BLOCKED_SCORE_{sig_type_str}", s_score, price, blocked_reason="SCORE")
                                                    best_metadata["blocked_reason"] = "SCORE"
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
                        "market_open": is_market_open,
                        "stalking": {k: {"target": v["target_price"], "dist": abs(price - v["target_price"])} for k, v in self.active_stalking.items()}
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
                    # Log periódico de estado (cada 5 ciclos para no saturar)
                    import random
                    if random.random() < 0.2: # ~20% de probabilidad para latido suave
                        df_m5_dbg = mtf_data.get('m5')
                        price_now = df_m5_dbg['close'].iloc[-1] if df_m5_dbg is not None else 'N/A'
                        logger.info(f"💓 [LATIDO] {self.symbol:<10} | Modo: {str(mode):<8} | Score: {current_score:>3} | Precio: {price_now}")

                await asyncio.sleep(self.interval)
            except Exception as e:
                logger.exception(f"❌ Error en {self.symbol}: {e}")
                await asyncio.sleep(10)

async def sync_trades_task(db: PSTDatabase):
    """Sincroniza operaciones cerradas en MT5 con la DB local, IMPORTANDO las que falten."""
    logger.info("🔄 Iniciando Sincronizador ROBUSTO de Historial (Auto-Discovery)...")
    # Tickets de trades del bot abiertos según la BBDD en la iteración anterior.
    # Los cierres se detectan por TRANSICIÓN abierto→cerrado en BBDD y no por quién
    # estampa el cierre: sync_mt5_history también lo llaman PST_API (el dashboard
    # sondea /api/account y /api/symbols cada pocos segundos) y PST_MCP sobre la
    # misma SQLite, y ganaban la carrera a este loop de 60s — por eso el Telegram
    # de cierre y el cooldown casi nunca se disparaban.
    prev_open_tickets = None
    while True:
        try:
            from datetime import datetime, timedelta
            from .mt5_async import get_history_deals_async
            import MetaTrader5 as mt5 # Necesario para constantes si no están en mt5_async

            # Obtener deals cerrados (Últimos 30 días para cubrir todo el mes)
            # Esto permite "descubrir" operaciones antiguas si se borró la DB o se operó desde el móvil
            deals = await get_history_deals_async(days=30)

            if deals:
                count, closed_bot = await db.sync_mt5_history(deals)
                if count > 0:
                    logger.info(f"🔄 [SYNC SUCCESS] {count} operaciones actualizadas/importadas desde MT5.")
                # Cooldown inmediato para cierres que estampó ESTA pasada (cubre trades
                # que viven menos de 60s y no llegan a verse en la foto de abiertos).
                loss_closures = [(s, p) for (s, p) in closed_bot if p < 0]
                if loss_closures:
                    loss_cd_mins = int(await db.get_config('loss_cooldown_minutes', '15'))
                    for c_sym, c_pnl in loss_closures:
                        cooldown_mgr.register_loss(c_sym, duration_minutes=loss_cd_mins)

            # Cierres por transición: trades que estaban abiertos en la BBDD la pasada
            # anterior y ya no lo están → notificar Telegram y registrar cooldown,
            # aunque el cierre lo haya estampado la API/MCP.
            open_now = {t['ticket'] for t in await db.get_active_trades()}
            if prev_open_tickets is not None:
                for closed_ticket in (prev_open_tickets - open_now):
                    trade = await db.get_trade_by_ticket(closed_ticket)
                    if not trade or (trade.get('price_out') or 0) <= 0:
                        continue  # borrado o cerrado "en falso" por el cleanup, no notificar
                    pnl = trade.get('profit') or 0.0
                    logger.info(f"[SYNC] Cierre bot: {trade['symbol']} (PosID {closed_ticket}) | PnL: {pnl:.2f}")
                    asyncio.create_task(telegram_bot.send_trade_notification(
                        closed_ticket, trade.get('type', ''), trade['symbol'], trade['price_out'], pnl, is_closing=True))

                    # v2.5.9: la config se leía pero register_loss no se llamaba NUNCA
                    # (CooldownManager sin registros → is_blocked siempre False), y el
                    # 2026-07-06 el bot reentró corto en US100 5 min después de un SL.
                    if pnl < 0:
                        loss_cd_mins = int(await db.get_config('loss_cooldown_minutes', '15'))
                        cooldown_mgr.register_loss(trade['symbol'], duration_minutes=loss_cd_mins)
            prev_open_tickets = open_now
            
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
                            # Intentar parsear fecha ISO de DB
                            from datetime import datetime
                            time_in_str = t.get('time_in')
                            if time_in_str:
                                # Truncar si tiene microsegundos para ser compatible
                                time_in = datetime.fromisoformat(time_in_str.split('.')[0])
                                if (datetime.now() - time_in).total_seconds() > 43200: # 12 horas
                                    logger.warning(f"🧹 [CLEANUP] Cerrando trade huérfano en DB: {t['symbol']} (Ticket {ticket})")
                                    # price_out = -1 (sentinela): con 0.0 el trade seguía
                                    # contando como abierto (price_out = 0) y el cleanup
                                    # se repetía cada 60s sin efecto; -1 lo saca de
                                    # get_active_trades y de las métricas (price_out > 0).
                                    await db.update_trade_cierre(ticket, -1.0, 0.0)
                        except Exception as ex:
                            logger.debug(f"DEBUG: Error en cleanup de trade {ticket}: {ex}")
            
            await asyncio.sleep(60) # Sincronizar cada minuto
        except Exception as e:
            logger.error(f"❌ Error en sincronización robusta: {e}")
            await asyncio.sleep(60)

async def global_trade_management(executor: PSTExecutor):
    """Tarea periódica para gestionar todas las posiciones abiertas y el Drawdown Diario."""
    from ..config import DAILY_LOSS_EXIT_USD
    logger.info("🛡️ Iniciando Sistema de Protección Dinámica y Drawdown Diario...")
    while True:
        try:
            # A. Gestión de Trailing/BE individual
            await executor.manage_active_trades()
            
            # A.2 Gestión de Cierre de Seguridad (Sesión/Fin de Semana)
            await executor.run_session_protection()
            
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
    import aiosqlite
    try:
        async with aiosqlite.connect(DB_PATH) as conn:
            async with conn.execute("SELECT symbol FROM symbols_config WHERE is_active = 1") as cursor:
                db_active_symbols = [row[0] for row in await cursor.fetchall()]
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
    
    # Inicializar BucketManager (cubetas de capital por estrategia)
    from ..utils.bucket_manager import BucketManager
    import PST_Core.utils.bucket_manager as _bm_module
    _bm = BucketManager(DB_PATH)
    await _bm.load_weights()
    _bm_module.bucket_manager = _bm
    logger.info(f"💰 [Buckets] Pesos iniciales: { {k: f'{v:.1f}%' for k, v in _bm._weights.items()} }")

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

    # Tarea de watchdog de drawdown semanal por estrategia (cada hora)
    async def _strategy_drawdown_watchdog():
        import MetaTrader5 as _mt5
        from ..config import ENABLED_STRATEGIES, DAILY_LOSS_PCT
        WEEKLY_DRAWDOWN_LIMIT_PCT = 1.5  # 1.5% del balance → pausa la estrategia
        while True:
            try:
                _acc = _mt5.account_info()
                if _acc and _acc.balance > 0:
                    loss_limit = -(_acc.balance * WEEKLY_DRAWDOWN_LIMIT_PCT / 100.0)
                    now_weekday = datetime.now().weekday()  # 0=Lunes

                    for strat_name in ENABLED_STRATEGIES:
                        weekly_pnl = await db.get_strategy_weekly_pnl(strat_name)
                        is_paused = await db.is_strategy_paused(strat_name)

                        # Reactivación automática cada lunes
                        if is_paused and now_weekday == 0:
                            await db.set_strategy_paused(strat_name, False)
                            await notif_mgr.send(
                                f"▶️ {strat_name} REACTIVADA\n"
                                f"Nueva semana — drawdown semanal reseteado."
                            )
                            logger.info(f"▶️ [Watchdog] {strat_name} reactivada (lunes).")
                            continue

                        # Pausar si supera el límite semanal y no está ya pausada
                        if not is_paused and weekly_pnl <= loss_limit:
                            await db.set_strategy_paused(strat_name, True)
                            await notif_mgr.send(
                                f"⏸️ {strat_name} PAUSADA\n"
                                f"PnL semanal: {weekly_pnl:.2f}\n"
                                f"Límite ({WEEKLY_DRAWDOWN_LIMIT_PCT}%): {loss_limit:.2f}\n"
                                f"Se reactivará el próximo lunes."
                            )
                            logger.warning(
                                f"⏸️ [Watchdog] {strat_name} pausada. "
                                f"PnL semanal {weekly_pnl:.2f} ≤ límite {loss_limit:.2f}"
                            )
            except Exception as _we:
                logger.debug(f"[Watchdog] Error en revisión de drawdown semanal: {_we}")
            await asyncio.sleep(3600)

    # Tarea de actualización de correlaciones dinámicas (cada hora)
    async def _correlation_updater():
        from ..utils.correlation_cache import corr_cache
        while True:
            try:
                if corr_cache.is_stale:
                    await corr_cache.update(symbols)
            except Exception as _e:
                logger.debug(f"[CorrCache] Error en tarea de actualización: {_e}")
            await asyncio.sleep(3600)

    # Tarea de rebalanceo mensual de cubetas de capital
    async def _bucket_rebalancer():
        while True:
            try:
                _acc = __import__("MetaTrader5").account_info()
                if _acc:
                    await _bm.rebalance_if_needed(_acc.balance)
            except Exception as _be:
                logger.debug(f"[Buckets] Error en rebalanceo: {_be}")
            await asyncio.sleep(86400)  # Revisar una vez al día

    corr_task      = [_correlation_updater()]
    watchdog_task  = [_strategy_drawdown_watchdog()]
    bucket_task    = [_bucket_rebalancer()]

    tasks = symbol_tasks + management_task + sync_task + corr_task + watchdog_task + bucket_task
    
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
