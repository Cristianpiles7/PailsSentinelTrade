import logging
import MetaTrader5 as mt5
import aiosqlite
from typing import Dict, List
from ..utils.tech_utils import get_asset_class
from ..config import SCALPER_MAX_LOSS_EUR, STRATEGY_CATEGORIES

logger = logging.getLogger("PST-Portfolio")

class PortfolioManager:
    def __init__(self, db=None, max_risk_pct=0.25, max_drawdown_pct=3.5):
        self.db = db
        self.max_risk_pct = max_risk_pct
        self.max_drawdown_pct = max_drawdown_pct
        self._start_balance = None
        self._last_balance_check_date = None
        
        # Mapa de correlaciones (Actualizado: Sin restricción USD a petición del usuario)
        self.correlation_groups = {
            "TECH": ["NVDA", "TSLA", "GOOG", "AAPL", "MSFT", "US500.cash"],
            "EURO": ["EURGBP", "EURJPY", "EU50.cash"]
        }
        # NEW: Bloqueo de duplicados "In-Flight" (Para evitar Race Conditions entre ciclos)
        self.in_flight_trades = set() 

    def register_in_flight(self, symbol: str):
        """Registra que se ha enviado una orden para este símbolo."""
        self.in_flight_trades.add(symbol)
        logger.debug(f"📝 [IN-FLIGHT] Registro temporal para {symbol}. Bloqueando duplicados.")

    def clear_in_flight(self, symbol: str):
        """Libera el bloqueo temporal de un símbolo."""
        if symbol in self.in_flight_trades:
            self.in_flight_trades.remove(symbol)
            logger.debug(f"🔓 [IN-FLIGHT] Liberado bloqueo para {symbol}.")

    async def get_account_status(self):
        """Obtiene el estado de la cuenta de forma thread-safe."""
        acc = mt5.account_info()
        if acc is None:
            return None
            
        status = {
            "balance": acc.balance,
            "equity": acc.equity,
            "margin_free": acc.margin_free,
            "drawdown": (1 - (acc.equity / acc.balance)) * 100 if acc.balance > 0 else 0
        }
        
        # Monitorización de pérdida diaria
        daily_loss = await self.get_daily_pnl(acc.balance, acc.equity)
        status["daily_pnl"] = daily_loss
        return status

    async def get_daily_pnl(self, current_balance, current_equity):
        """Calcula el PnL del día (Cerrado hoy + Flotante actual)."""
        if not self.db: 
            return current_equity - current_balance # Fallback pobre si no hay DB
            
        from datetime import datetime, time
        today_start = datetime.combine(datetime.now().date(), time.min).strftime('%Y-%m-%d %H:%M:%S')
        
        # 1. Obtener PnL de trades cerrados hoy desde la DB
        closed_pnl = 0.0
        try:
            async with aiosqlite.connect(self.db.db_path) as conn:
                async with conn.execute("SELECT SUM(profit) FROM trades WHERE time_out >= ?", (today_start,)) as cursor:
                    row = await cursor.fetchone()
                    closed_pnl = row[0] if row and row[0] else 0.0
        except Exception as e:
            logger.error(f"❌ Error consultando PnL hoy: {e}")
            
        # 2. Obtener PnL Flotante (Equity - Balance)
        floating_pnl = current_equity - current_balance
        
        total_pnl = closed_pnl + floating_pnl
        return total_pnl

    async def is_daily_locked(self):
        """Verifica si la operativa está bloqueada por haber alcanzado el límite diario."""
        if not self.db: return False
        
        from datetime import datetime
        today_str = datetime.now().strftime('%Y-%m-%d')
        lock_key = f"daily_lock_{today_str}"
        
        lock_val = await self.db.get_config(lock_key, default="false")
        return lock_val.lower() == "true"

    async def set_daily_lock(self):
        """Activa el bloqueo persistente para el resto del día."""
        if not self.db: return
        
        from datetime import datetime
        today_str = datetime.now().strftime('%Y-%m-%d')
        lock_key = f"daily_lock_{today_str}"
        
        await self.db.update_config(lock_key, "true")
        logger.error(f"🔒 [BLOQUEO] Operativa cerrada por el resto de la sesión ({today_str}).")

    def get_symbol_group(self, symbol: str) -> List[str]:
        """Identifica a qué grupo de correlación pertenece un símbolo."""
        for group, symbols in self.correlation_groups.items():
            if symbol in symbols:
                return group
        return "OTHERS"

    async def can_open_trade(self, symbol: str, signal_type: str, current_positions, strategy_name: str = None):
        """
        Lógica de control de riesgo global (FTMO Friendly) y Pyramiding Institucional.
        """
        # --- NUEVO: LÍMITES GLOBALES POR CATEGORÍA (v1.8.7) ---
        from ..config import STRATEGY_CATEGORIES, MAX_POSITIONS_PER_CATEGORY, MAX_TOTAL_OPEN_POSITIONS

        # Traducir nombre legible si es necesario
        raw_strat_name = strategy_name or ""
        reverse_map = {
            "Scalping Pro (Micro-Reversión)": "PST-Scalper-Pro",
            "Flujo EMA (Tendencia)": "PST-EMA-Flow",
            "Canal Maestro (T. Híbrido)": "PST-Channel-Master",
            "TrendMaster (Line Breakout)": "PST-TrendMaster",
            "Reversión a la Media (Rangos)": "PST-Mean-Reversion",
            "Liquidez Sentinel (Institucional)": "PST-Liquidity-Hunter"
        }
        if raw_strat_name in reverse_map:
            raw_strat_name = reverse_map[raw_strat_name]

        strategy_category = STRATEGY_CATEGORIES.get(raw_strat_name, "CORE")
        max_for_cat = MAX_POSITIONS_PER_CATEGORY.get(strategy_category, 1)

        # --- LÍMITE GLOBAL ABSOLUTO (Race Condition Fix) ---
        # Cuenta posiciones reales + las que están en vuelo (aún no registradas en MT5)
        n_real = len(current_positions) if current_positions else 0
        n_inflight = len(self.in_flight_trades)
        if n_real + n_inflight >= MAX_TOTAL_OPEN_POSITIONS:
            logger.warning(f"🚫 [GLOBAL CAP] {n_real} abiertas + {n_inflight} en vuelo >= límite {MAX_TOTAL_OPEN_POSITIONS}. Bloqueando {symbol}.")
            return False

        # Contar posiciones globales de esta categoría (reales + in-flight aproximadas)
        global_cat_count = 0
        if current_positions:
            for p in current_positions:
                p_strat = p.comment.replace("PST_", "").replace("PST-", "")
                p_cat = "CORE"
                for s_name_cfg, s_cat in STRATEGY_CATEGORIES.items():
                    if s_name_cfg.replace("PST-", "") in p_strat:
                        p_cat = s_cat
                        break
                if p_cat == strategy_category:
                    global_cat_count += 1

        # Los in-flight se cuentan como si fueran de la misma categoría (conservador)
        global_cat_count += n_inflight

        if global_cat_count >= max_for_cat:
            logger.warning(f"🚫 [GLOBAL LIMIT] Límite de {max_for_cat} pos para {strategy_category} alcanzado. Bloqueando {symbol}.")
            return False

        # --- BLOQUEO DIARIO (v1.8.7) ---
        if await self.is_daily_locked():
             logger.warning(f"🔒 [BLOQUEO DIARIO] No se permiten más entradas hoy en {symbol}.")
             return False

        # 0. Evitar duplicados por Símbolo (Filtro Estricto y Robusto)
        target_sym = symbol.upper().strip()
        
        if any(s.upper().strip() == target_sym for s in self.in_flight_trades):
            logger.warning(f"🛡️ Bloqueando entrada in-flight para {symbol}. Orden recientemente enviada.")
            return False

        if current_positions:
            target_class = get_asset_class(target_sym)
            for pos in current_positions:
                pos_sym = pos.symbol.upper().strip()
                pos_class = get_asset_class(pos_sym)
                pos_is_buy = getattr(pos, 'type', -1) == 0
                pos_is_sell = getattr(pos, 'type', -1) == 1
                
                # --- RELAXED: ANTI-HEDGING CATEGÓRICO REMOVIDO ---
                # Ya no bloqueamos activos distintos (ej: BTC vs LINK) por ser de la misma clase.
                # Solo bloquearemos si es el mismísimo símbolo (manejado abajo) o si el usuario
                # Coincidencia exacta o parcial (ej: EURUSD vs EURUSD.cash)
                if pos_sym == target_sym or target_sym in pos_sym or pos_sym in target_sym:
                    pos_comment = getattr(pos, 'comment', "")
                    is_scalper = "Scalper" in strategy_name if strategy_name else False
                    
                    # NUEVO: Permitir pruebas simultáneas de múltiples estrategias de scalping en el mismo activo
                    # Mapeo de abreviaturas utilizadas en los comentarios de MT5
                    abbrev_map = {
                        "PST-Scalper-Pro": ["ScPro", "Scalper-Pro"],
                        "PST-Scalper-Active": ["ScV2", "Scalper-Active"],
                        "PST-Scalper-OrderFlow": ["ScOF", "OrderFlow", "Scalper-OrderFlow"]
                    }
                    
                    pos_is_different_scalper = False
                    if is_scalper:
                        current_abbrevs = abbrev_map.get(strategy_name, [])
                        is_pos_scalper = any(x in pos_comment for x in ["ScPro", "ScV2", "ScOF", "Scalper"])
                        is_same_strategy = any(abbrev in pos_comment for abbrev in current_abbrevs)
                        
                        if is_pos_scalper and not is_same_strategy:
                            pos_is_different_scalper = True
                            logger.info(f"⚖️ [PARALLEL SCALPING] {symbol} tiene posición activa de otra estrategia ({pos_comment}). Permitiendo señal paralela de {strategy_name} para testeo.")
                            continue # Omitimos el bloqueo de duplicado y continuamos evaluando reglas

                    # --- PYRAMIDING LOGIC (FASE 55) ---
                    # Comprobamos si la posición existente está libre de riesgo (Break-Even)
                    is_buy = pos_is_buy
                    is_sell = pos_is_sell
                    sig_is_buy = signal_type == "BUY"
                    sig_is_sell = signal_type == "SELL"
                    
                    price_open = getattr(pos, 'price_open', 0)
                    sl = getattr(pos, 'sl', 0)
                    
                    is_risk_free = False
                    if is_buy and sl >= price_open and price_open > 0:
                        is_risk_free = True
                    elif is_sell and sl > 0 and sl <= price_open:
                        is_risk_free = True
                        
                    # Solo piramidamos a favor de la misma dirección si la original es segura
                    # EXCEPCIÓN 1: Permitimos reversiones (señal contraria) para que el Orquestador decida si gira la posición.
                    if (is_buy and sig_is_sell) or (is_sell and sig_is_buy):
                        logger.info(f"🔄 [REVERSAL DETECTED] {symbol} tiene señal contraria. Permitiendo evaluación de Giro Seguro.")
                        continue 
 
                    # EXCEPCIÓN 2: Desactivamos piramidado para SCALPING para evitar sobre-exposición
                    if is_risk_free and ((is_buy and sig_is_buy) or (is_sell and sig_is_sell)) and not is_scalper:
                        logger.info(f"📈 [PYRAMIDING] Permitiendo reingreso en {symbol}. La posición original ya está en Break-Even.")
                        continue # Seguimos validando el resto de las reglas
                    else:
                        reason = "SCALPING NO-PYRAMID" if is_scalper else "RISK IN POS"
                        logger.info(f"🛡️ Bloqueando entrada duplicada para {symbol}. Razón: {reason}.")
                        return False

        # --- CORRELACIÓN DINÁMICA: no abrir si hay una posición altamente correlacionada ---
        if current_positions:
            try:
                from ..utils.correlation_cache import corr_cache
                for pos in current_positions:
                    pos_sym = pos.symbol.upper().strip()
                    if pos_sym == target_sym:
                        continue  # Duplicado ya manejado arriba
                    if corr_cache.is_correlated(target_sym, pos_sym, threshold=0.82):
                        corr_val = corr_cache.get_correlation(target_sym, pos_sym)
                        pos_dir = "BUY" if getattr(pos, "type", -1) == 0 else "SELL"
                        # Solo bloqueamos si la dirección propuesta es la misma que la correlacionada
                        if pos_dir == signal_type:
                            logger.warning(
                                f"🔗 [CORR BLOCK] {target_sym} correlacionado con {pos_sym} "
                                f"({corr_val:.2f}) en la misma dirección {signal_type}. Bloqueando."
                            )
                            return False
            except Exception as _ce:
                logger.debug(f"[CorrCache] Error en verificación de correlación: {_ce}")

        acc = await self.get_account_status()
        if not acc: return False

        # 1. Kill-Switch por Drawdown Global (Seguro FTMO al 3.5%)
        if acc["drawdown"] >= self.max_drawdown_pct:
            logger.warning(f"🛑 KILL-SWITCH FTMO: Drawdown del {acc['drawdown']:.2f}% (Límite: {self.max_drawdown_pct}%)")
            return False

        # MODO PRUEBAS: solo bloqueamos si el símbolo ya tiene una posición abierta
        if current_positions:
            target_sym = symbol.upper().strip()
            for pos in current_positions:
                if pos.symbol.upper().strip() == target_sym:
                    logger.info(f"🛡️ [{symbol}] Ya tiene posición abierta. Bloqueando nueva entrada.")
                    return False

        return True

        # 2. Filtro de Cierre de Mercado (Solo para Acciones/Índices si aplica)
        # Si faltan menos de 20 min para el cierre, no abrimos compra
        if await self.is_near_market_close(symbol, 20):
            logger.info(f"⏳ Cierre de mercado próximo para {symbol}. Omitiendo entrada.")
            return False

        # 3. Límite de Exposición por Grupo (Sin restricción USD)
        symbol_group = self.get_symbol_group(symbol)
        if symbol_group == "OTHERS": return True # Sin límites para otros activos
        
        group_exposure = 0
        if current_positions:
            for pos in current_positions:
                if self.get_symbol_group(pos.symbol) == symbol_group:
                    pos_type = "BUY" if pos.type == 0 else "SELL"
                    if pos_type == signal_type:
                        group_exposure += 1
        
        # if group_exposure >= 2:
        #     logger.info(f"🛡️ Exposición máxima grupo {symbol_group} alcanzada.")
        #     return False

        return True

    async def is_near_market_close(self, symbol: str, minutes_before: int = 20):
        # ... existing code ...
        from datetime import datetime
        now = datetime.now()
        
        # Lógica simplificada para Acciones (NASDAQ/NYSE cierran a las 22:00 hora MT5 aprox)
        if any(stock in symbol for stock in ["AAPL", "NVDA", "TSLA", "GOOG", "MSFT"]):
            if now.hour == 21 and now.minute >= (60 - minutes_before):
                return True
        return False

    def _get_leverage_and_bucket(self, symbol: str):
        """Devuelve el apalancamiento y cubeta de margen según el tipo de activo."""
        from ..config import CRYPTO_KEYWORDS
        s_up = symbol.upper()
        
        # 1. CRIPTO (1:2)
        if any(k in s_up for k in CRYPTO_KEYWORDS):
            return 2, 3000, "CRIPTO"
            
        # 2. ACCIONES (FTMO Stocks 1:3)
        if any(stock in s_up for stock in ["AAPL", "NVDA", "TSLA", "GOOG", "MSFT", "AMZN", "META"]):
            return 3, 7000, "STOCK"
            
        # 3. ÍNDICES Y ORO (1:50)
        # Comprobamos palabras clave comunes de índices y materias primas
        indices_commods = ["XAU", "GOLD", "US30", "NAS100", "US100", "GER40", "DAX", "EU50", "SPX500", "US500", "USOIL", "UKOIL"]
        if any(k in s_up for k in indices_commods):
            return 50, 7000, "INDEX/COMMOD"
            
        # 4. FOREX (Default 1:100)
        return 100, 7000, "FOREX"

    def calculate_lot_size(self, balance, risk_per_trade_pct, stop_loss_points, symbol_info, current_atr=None, ma_atr=None, open_positions_count=0, risk_mode="LOTS", risk_value=None, regime="TREND"):
        """
        Calcula el lotaje usando Lógica Híbrida de Riesgo Dinámico y Cubetas de Margen (FTMO Rules).
        Soporta modos: LOTS (fijo), PCT (% balance), MONEY (nominal €).
        """
        if stop_loss_points <= 0 or symbol_info is None:
            return symbol_info.volume_min if symbol_info else 0.01

        symbol = symbol_info.name
        
        # --- 1. DETERMINAR DINERO EN RIESGO ---
        # Si no hay risk_value, usamos el risk_per_trade_pct global como fallback
        risk_money = 0.0
        
        if risk_mode == "PCT":
            risk_pct = risk_value if risk_value is not None else risk_per_trade_pct
            risk_money = balance * (risk_pct / 100)
        elif risk_mode == "MONEY":
            risk_money = risk_value if risk_value is not None else (balance * (risk_per_trade_pct / 100))
        else: # "LOTS"
            # Si el modo es LOTS, risk_value es el lotaje directamente
            if risk_value is not None and risk_value > 0:
                return max(symbol_info.volume_min, min(symbol_info.volume_max, round(risk_value / symbol_info.volume_step) * symbol_info.volume_step))
            # Fallback a cálculo por riesgo global
            risk_money = balance * (risk_per_trade_pct / 100)

        # --- NEW: NOMINAL CAP FOR SCALPING (v2.0.1) ---
        # Si arriesgar el % del balance supera el tope nominal, usamos el tope.
        if risk_money > SCALPER_MAX_LOSS_EUR:
             # Necesitamos saber si es scalping. El orquestador pasa el risk_per_trade_pct específico.
             # Si el riesgo base es el de scalper (0.08), aplicamos el cap.
             if abs(risk_per_trade_pct - 0.08) < 0.001: 
                 logger.info(f"🛡️ [SCALP CAP] Riesgo de {risk_money:.2f}€ excede el máximo de {SCALPER_MAX_LOSS_EUR}€. Ajustando nomina a {SCALPER_MAX_LOSS_EUR}€.")
                 risk_money = SCALPER_MAX_LOSS_EUR

        # --- 2. DYNAMIC RISK SCALING (ARRIESGAR MENOS SI HAY EXPOSICIÓN) ---
        # Reducción de riesgo si hay muchos trades abiertos
        if open_positions_count >= 2:
            risk_money *= 0.70
        if open_positions_count >= 5:
            risk_money *= 0.50
            
        # --- NEW: REGIME RISK ADJUSTMENT (v2.0.1) ---
        if regime == "VOLATILE":
            logger.info("⚡ [VOLATILE RISK] Reduciendo riesgo un 25% por régimen volátil.")
            risk_money *= 0.75
            
        # --- 3. VOLATILITY ADJUSTMENT ---
        if current_atr and ma_atr and ma_atr > 0:
             vol_factor = ma_atr / current_atr
             # AJUSTE: Si es modo MONEY, el ajuste de volatilidad solo puede reducir el riesgo, nunca subirlo del nominal (v1.8.7)
             if risk_mode == "MONEY":
                 vol_factor = min(1.0, vol_factor)
             risk_money *= vol_factor
             
        # Cap de seguridad de riesgo monetario (no arriesgar más del triple del riesgo base config)
        base_risk_money = balance * (risk_per_trade_pct / 100)
        risk_money = min(risk_money, base_risk_money * 3)

        # CAP FINAL ESTRICTO para modo MONEY (Petición de usuario: No superar el valor nominal)
        if risk_mode == "MONEY" and risk_value is not None:
            risk_money = min(risk_money, risk_value)
            logger.info(f"💰 [RISK CAP] Aplicado cap de {risk_value}€ para modo MONEY. Riesgo final: {risk_money:.2f}€")

        tick_value = symbol_info.trade_tick_value
        if tick_value == 0:
            return symbol_info.volume_min

        # Lote Inicial basado en Riesgo (Stop Loss)
        raw_lot = risk_money / (stop_loss_points * tick_value) if stop_loss_points > 0 else symbol_info.volume_min
        
        # --- 4. MARGIN-BUCKET CAP (DYNAMICAL LEVERAGE TIERS) ---
        leverage, margin_bucket, asset_type = self._get_leverage_and_bucket(symbol)
        
        price = symbol_info.ask if symbol_info.ask > 0 else symbol_info.last
        contract_size = symbol_info.trade_contract_size if symbol_info.trade_contract_size > 0 else 1
        
        required_margin = (raw_lot * contract_size * price) / leverage if leverage > 0 else 0
        
        logger.info(f"⚖️ [LOTES] {symbol} ({asset_type}) | Riesgo €{risk_money:.2f} | Margen Req: {required_margin:.2f}€")

        if required_margin > margin_bucket:
            raw_lot = (margin_bucket * leverage) / (contract_size * price)
            logger.warning(f"✂️ [MARGIN CAP] {symbol} superó cubeta de {margin_bucket}€. Recortando lotaje.")

        # Ajustes finales del Broker
        lot = max(symbol_info.volume_min, min(symbol_info.volume_max, raw_lot))
        lot = round(lot / symbol_info.volume_step) * symbol_info.volume_step
        
        logger.info(f"✅ Lot Final para {symbol}: {round(lot, 2)} (Basado en {risk_mode} {risk_value}€, SL {stop_loss_points} pts)")
        return round(lot, 2)
