import logging
import MetaTrader5 as mt5
import asyncio
from datetime import datetime
import pandas as pd
import pandas_ta as ta
from .mt5_async import send_order_async, sym_info_async, get_positions_async, modify_position_async, fetch_rates_async
from .telegram_manager import telegram_bot
from ..models.database import PSTDatabase
from ..portfolio.manager import PortfolioManager
from ..config import BE_ATR_MULTIPLIER, TRAIL_ATR_MULTIPLIER, TP_ATR_BY_CLASS, STRATEGY_CATEGORIES, MAX_POSITIONS_PER_CATEGORY, MAX_SYMBOL_EXPOSURE_PCT
from ..utils.tech_utils import get_asset_class

logger = logging.getLogger("PST-Executor")

class PSTExecutor:
    def __init__(self, db: PSTDatabase, portfolio: PortfolioManager):
        self.db = db
        self.portfolio = portfolio

    async def execute_trade(self, symbol, signal_type, stop_loss_atr, take_profit_atr, strategy_name, regime, metadata=None):
        """
        Envía una orden real a MT5 tras validar lotes y riesgos.
        Carga parámetros dinámicos de la DB para riesgo y estrategia.
        """
        # --- NEW: NEWS GUARD CHECK (FASE 50) ---
        from .news_manager import news_guard
        is_blocked, news_event = news_guard.is_news_blocked(symbol)
        if is_blocked:
            logger.warning(f"⚠️ [NEWS GUARD] {symbol} Bloqueado por noticia de alto impacto: {news_event['title']} ({news_event['country']})")
            return None

        # 0. Check for Parallelism and Symbol Limits (FASE 67)
        strategy_category = STRATEGY_CATEGORIES.get(strategy_name, "CORE")
        max_for_cat = MAX_POSITIONS_PER_CATEGORY.get(strategy_category, 1)
        
        all_positions = await get_positions_async(symbol=symbol)
        
        # 0.1 Count positions by category
        cat_count = 0
        total_risk_pct = 0
        if all_positions:
            for p in all_positions:
                # Extraer nombre de estrategia del comentario
                p_strat = p.comment.replace("PST_", "").replace("PST-", "")
                # Buscar categoría (usamos coincidencia parcial o limpia)
                p_cat = "CORE"
                for s_name, s_cat in STRATEGY_CATEGORIES.items():
                    if s_name.replace("PST-", "") in p_strat:
                        p_cat = s_cat
                        break
                
                if p_cat == strategy_category:
                    cat_count += 1
                
                # Estimación de riesgo (simplificada por ahora, mejorable en FASE 69)
                total_risk_pct += 0.25 # Asunción de riesgo base
            
            # Bloqueo por categoría
            if cat_count >= max_for_cat:
                logger.debug(f"🚫 [CATEGORY LIMIT] {symbol} ya tiene {cat_count} pos de tipo {strategy_category}. Bloqueando {strategy_name}.")
                return None
            
            # Bloqueo por exposición total
            if total_risk_pct >= MAX_SYMBOL_EXPOSURE_PCT:
                logger.warning(f"⚠️ [SYMBOL GUARD] {symbol} exposición total ({total_risk_pct}%) excede límite ({MAX_SYMBOL_EXPOSURE_PCT}%).")
                return None

        # 1. Obtener Info del Símbolo y Parámetros
        mt5.symbol_select(symbol, True)
        s_info = await sym_info_async(symbol)
        if not s_info:
            logger.error(f"❌ Imposible obtener info de {symbol} para ejecutar orden.")
            return

        # --- NEW: CARGA DE CONFIGURACIÓN DINÁMICA (FASE 46) ---
        # 1.1 Obtener parámetros del símbolo base
        s_params = await self.db.get_symbol_params(symbol)
        
        # 1.2 Obtener parámetros específicos de la estrategia para este símbolo
        all_sym_strats = await self.db.get_symbol_strategies(symbol)
        
        # Normalizar nombre (Orchestrator puede pasar el nombre 'limpio')
        raw_name = strategy_name
        reverse_map = {
            "Scalping Pro (Micro-Reversión)": "PST-Scalper-Pro",
            "Flujo EMA (Tendencia)": "PST-EMA-Flow",
            "Canal Maestro (T. Híbrido)": "PST-Channel-Master",
            "Reversión a la Media (Rangos)": "PST-Mean-Reversion"
        }
        if strategy_name in reverse_map:
            raw_name = reverse_map[strategy_name]
            
        strat_cfg = all_sym_strats.get(raw_name, {})
        
        # JERARQUÍA DE RIESGO: Estrategia > Símbolo > Global
        risk_mode = strat_cfg.get("risk_mode") or s_params.get("risk_mode") or "PCT"
        risk_val = strat_cfg.get("risk_value")
        if risk_val is None:
            risk_val = s_params.get("risk_value", 0.25)

        # JERARQUÍA DE MULTIPLICADORES: Estrategia > Símbolo > Asset Class Default
        a_class = get_asset_class(symbol)
        def_tp_m = TP_ATR_BY_CLASS.get(a_class, 4.0) # Ajustado a 4.0 para R:R 1.2 más realista
        
        sl_m = strat_cfg.get("sl_mult") or s_params.get("sl_mult") or 2.5
        
        # --- PERFECCIÓN SCALPER: SL Ceñido (1.25 ATR) ---
        if "Scalper" in raw_name:
            if not strat_cfg.get("sl_mult") and not s_params.get("sl_mult"):
                sl_m = 1.25 # Default agresivo para scalping para favorecer R:R
                logger.debug(f"📐 [SCALPER PRO] Usando SL Ceñido: {sl_m}x ATR")

        tp_m = strat_cfg.get("tp_mult") or s_params.get("tp_mult") or def_tp_m
        # R:R mínimo aceptable: Bajado a 1.2 por petición de usuario (Prevalece sobre el 1.5 anterior)
        min_rr = strat_cfg.get("min_rr") or s_params.get("min_rr") or 1.2
        
        # --- FORZAR R:R 1.2 PARA SCALPER ---
        if "Scalper" in raw_name:
            min_rr = max(min_rr, 1.2)
        
        # Recalcular SL/TP en base a los multiplicadores reales
        # Nota: stop_loss_atr entrante suele ser (ATR * 2.5) del símbolo. 
        # Pero aquí queremos precisión absoluta. Recalculamos sobre el ATR base.
        # stop_loss_atr / 2.5 nos da el ATR_unitario aproximado.
        atr_unit = stop_loss_atr / 2.5 # Estimación rápida si no recalculamos ATR aquí
        
        real_sl_atr = atr_unit * sl_m
        real_tp_atr = atr_unit * tp_m

        # 1.3 Obtener parámetros TS/BE de la base de datos
        use_trailing = strat_cfg.get("use_trailing", 0) == 1
        use_breakeven = strat_cfg.get("use_breakeven", 0) == 1

        # 2. Obtener Balance
        acc = await self.portfolio.get_account_status()
        if not acc: return

        # 3. Calcular Stop Loss y Lote con Volatilidad
        price = s_info.ask if signal_type == "BUY" else s_info.bid
        
        # --- NEW: OBTENCIÓN DE DATOS PARA CÁLCULOS TÉCNICOS ---
        df_m5 = await fetch_rates_async(symbol, 5, 50)
        current_atr = 0
        ma_atr = 0
        if df_m5 is not None and len(df_m5) >= 20:
            atr_series = ta.atr(df_m5['high'], df_m5['low'], df_m5['close'], length=14)
            current_atr = atr_series.iloc[-1]
            ma_atr = atr_series.rolling(20).mean().iloc[-1]
        
        if current_atr == 0:
            current_atr = stop_loss_atr / 2.5 # Fallback razonable
            ma_atr = current_atr

        # --- NEW: SOPORTE PARA TP TÉCNICO Y SL ESTRUCTURAL ---
        # A. CALCULAR STOP LOSS ESTRUCTURAL (FASE 54)
        # Buscar el último mínimo o máximo relevante en las velas recientes
        sl_price = 0
        structural_sl_found = False
        
        if df_m5 is not None and len(df_m5) >= 15:
            if signal_type == "BUY":
                # Swing Low: min of last 15 bars
                recent_low = df_m5['low'].tail(15).min()
                structural_sl = recent_low - (current_atr * 0.2) # Padding de seguridad
                # Validar la distancia: no puede estar pegado, ni debe ser astronómico
                dist_atr = (price - structural_sl) / current_atr
                if 1.5 <= dist_atr <= (sl_m * 1.5):
                    sl_price = structural_sl
                    structural_sl_found = True
                    logger.debug(f"📐 SL Estructural (BUY) fijado en {sl_price:.5f} (Swing Low)")
            else:
                # Swing High: max of last 15 bars
                recent_high = df_m5['high'].tail(15).max()
                structural_sl = recent_high + (current_atr * 0.2)
                dist_atr = (structural_sl - price) / current_atr
                if 1.5 <= dist_atr <= (sl_m * 1.5):
                    sl_price = structural_sl
                    structural_sl_found = True
                    logger.debug(f"📐 SL Estructural (SELL) fijado en {sl_price:.5f} (Swing High)")

        # Fallback al SL técnico estricto si el estructural es loco o no hay datos
        if not structural_sl_found:
             sl_price = price - real_sl_atr if signal_type == "BUY" else price + real_sl_atr
             logger.debug(f"📐 SL Dinámico (ATR x {sl_m}) fijado en {sl_price:.5f}")

        # Recalcular SL Points reales para el dimensionamiento del lote
        sl_points = abs(price - sl_price) / s_info.point
        
        # --- NEW: SECURITY FLOOR FOR SL POINTS (FASE 56) ---
        # Suelo del 0.08% para Stocks/Forex para evitar lotajes extremos por ruido
        min_sl_dist = price * 0.0008 
        min_sl_points = min_sl_dist / s_info.point
        
        if sl_points < min_sl_points:
            logger.warning(f"⚠️ [SAFETY] SL muy ajustado ({sl_points:.1f} pts). Usando suelo de seguridad ({min_sl_points:.1f} pts).")
            sl_points = min_sl_points
            # Ajustamos sl_price para consistencia
            sl_price = price - min_sl_dist if signal_type == "BUY" else price + min_sl_dist
        
        lot = self.portfolio.calculate_lot_size(
            acc["balance"], 
            self.portfolio.max_risk_pct, 
            sl_points, 
            s_info,
            current_atr=current_atr,
            ma_atr=ma_atr,
            risk_mode=risk_mode,
            risk_value=risk_val
        )

        # B. CALCULAR TAKE PROFIT DINÁMICO POR VOLATILIDAD (FASE 56)
        volatility_ratio = (current_atr / ma_atr) if ma_atr > 0 else 1.0
        # Ajustar el multiplicador de TP: 
        # Si la volatilidad aumenta, alargamos el TP. Si baja, lo acortamos.
        tp_adjustment = 1.0
        if volatility_ratio > 1.2: 
            tp_adjustment = min(1.5, volatility_ratio) # Máximo 50% extra
            logger.debug(f"📈 [TP DYN] Volatilidad en expansión ({volatility_ratio:.2f}x). Ajuste TP: +{int((tp_adjustment-1)*100)}%")
        elif volatility_ratio < 0.8:
            tp_adjustment = max(0.7, volatility_ratio) # Mínimo 70% del original
            logger.debug(f"📉 [TP DYN] Volatilidad en contracción ({volatility_ratio:.2f}x). Ajuste TP: -{int((1-tp_adjustment)*100)}%")
        
        real_tp_atr_adj = real_tp_atr * tp_adjustment

        tp_price = 0
        if metadata and metadata.get("tp_target", 0) > 0:
            tp_price = metadata["tp_target"]
            # Fallback de seguridad: si el tp_price está detrás del precio actual, usar multiplicador
            if (signal_type == "BUY" and tp_price <= price) or (signal_type == "SELL" and tp_price >= price):
                logger.warning(f"⚠️ [TP TÉCNICO] TP objetivo ({tp_price:.5f}) inválido para precio actual ({price:.5f}). Usando mult ({tp_m}x).")
                tp_price = price + real_tp_atr_adj if signal_type == "BUY" else price - real_tp_atr_adj
            else:
                logger.info(f"🎯 [TP TÉCNICO] Usando objetivo de estrategia: {tp_price:.5f}")
        else:
             tp_price = price + real_tp_atr_adj if signal_type == "BUY" else price - real_tp_atr_adj

        # --- CHECK R:R MÍNIMO Y AUTO-AJUSTE PROACTIVO ---
        tp_dist = abs(tp_price - price)
        sl_dist = abs(sl_price - price)
        rr_actual = (tp_dist / sl_dist) if sl_dist > 0 else 0
        
        if rr_actual < min_rr:
            # PROACTIVE AUTO-FIX: En lugar de rechazar, adaptamos la orden
            logger.info(f"⚖️ [R:R PROACTIVO] {symbol} {strategy_name}: Ratio inicial {rr_actual:.2f} inferior al mínimo {min_rr}. Ajustando...")
            
            # 1. Intentar ceñir un poco el SL (hasta un límite seguro de 1.0 ATR)
            min_sl_allowed = current_atr * 1.0
            ideal_sl_dist = tp_dist / min_rr
            
            if ideal_sl_dist >= min_sl_allowed:
                logger.info(f"   📐 Ceñiendo SL: {sl_dist:.5f} -> {ideal_sl_dist:.5f} (Auto-Fix)")
                sl_dist = ideal_sl_dist
                sl_price = price - sl_dist if signal_type == "BUY" else price + sl_dist
                rr_actual = min_rr
            else:
                # 2. Si el SL no se puede ceñir más sin ser peligroso, estirar el TP
                # Incluso si el TP queda lejos, cumplimos con el R:R deseado por el usuario
                new_tp_dist = sl_dist * min_rr
                logger.info(f"   🎯 Estirando TP: {tp_dist:.5f} -> {new_tp_dist:.5f} (R:R {min_rr} Asegurado)")
                tp_dist = new_tp_dist
                tp_price = price + tp_dist if signal_type == "BUY" else price - tp_dist
                rr_actual = min_rr

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": mt5.ORDER_TYPE_BUY if signal_type == "BUY" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": round(sl_price, s_info.digits),
            "tp": round(tp_price, s_info.digits),
            "magic": 666, # Magic Number PST
            "comment": f"PST_{raw_name}"[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        # --- 4.5 PRE-FLIGHT MARGIN CHECK ---
        # Recalcular margen para validación (Lógica espejo de PortfolioManager)
        leverage, _, _ = self.portfolio._get_leverage_and_bucket(symbol)
        contract_size = s_info.trade_contract_size if s_info.trade_contract_size > 0 else 1
        required_margin = (lot * contract_size * price) / leverage if leverage > 0 else 0

        acc = await self.portfolio.get_account_status()
        if acc and required_margin > acc["margin_free"]:
            logger.warning(f"🛑 [MARGIN REJECT] {symbol}: Margen requerido ({required_margin:.2f}€) supera el libre ({acc['margin_free']:.2f}€).")
            return None

        # 5. Envío Asíncrono
        logger.info(f"🚀 [ORDEN] {signal_type} {symbol} | Estrategia: {strategy_name} | Régimen: {regime}")
        logger.info(f"   📊 Detalles: Lote {lot} | SL {sl_price:.5f} | TP {tp_price:.5f} | Margen Est: {required_margin:.2f}€")
        result = await send_order_async(request)

        if result is None:
            logger.error(f"❌ Fallo crítico de conexión MT5 al enviar {symbol}. Result is None.")
            return None

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"❌ Error al ejecutar {symbol}: {result.comment} (code: {result.retcode})")
            return None
        
        # 6. Guardar en Base de Datos
        trade_data = {
            "symbol": symbol,
            "type": signal_type,
            "volume": lot,
            "price_in": price,
            "price_out": 0.0,
            "sl": sl_price,
            "tp": tp_price,
            "profit": 0.0,
            "time_in": str(datetime.now()),
            "time_out": "",
            "regime_at_entry": regime,
            "strategy_name": strategy_name,
            "ticket": result.order,
            "is_partial_closed": 0
        }
        # --- NEW: TRADE CONTEXT CAPTURE (FASE 53) ---
        try:
            # Capturamos contexto de 50 velas M15 para el Journal
            import json
            # context_rates es un DataFrame o None
            df_context = await fetch_rates_async(symbol, 15, 50)
            if df_context is not None and not df_context.empty:
                ohlc_list = []
                # Convertir DataFrame a lista de dicts para iterar filas
                records = df_context.reset_index().to_dict('records')
                for r in records:
                    ohlc_list.append({
                        "time": int(r['time'].timestamp()) if hasattr(r['time'], 'timestamp') else int(r['time']),
                        "open": float(r['open']),
                        "high": float(r['high']),
                        "low": float(r['low']),
                        "close": float(r['close'])
                    })
                await self.db.save_context(symbol, json.dumps(ohlc_list), ticket=result.order)
        except Exception as ex:
            logger.warning(f"⚠️ Error capturando contexto para Journal: {ex}")

        await self.db.save_trade(trade_data)
        logger.info(f"✅ Orden ejecutada con éxito para {symbol}. Ticket: {result.order}")
        
        # --- NEW: TELEGRAM NOTIFICATION (FASE 52) ---
        asyncio.create_task(telegram_bot.send_trade_notification(result.order, signal_type, symbol, price, 0, is_closing=False))
        
        return result

    async def manage_active_trades(self):
        """
        Gestión proactiva de posiciones (Breakeven y Trailing Stop automático).
        """
        positions = await get_positions_async()
        if not positions: return

        for p in positions:
            try:
                symbol = p.symbol
                ticket = p.ticket
                p_type = "BUY" if p.type == 0 else "SELL"
                
                # 1. Obtener ATR actual (M5)
                df = await fetch_rates_async(symbol, 5, 50)
                if df is None: continue
                
                atr_series = ta.atr(df['high'], df['low'], df['close'], length=14)
                atr = atr_series.iloc[-1]
                s_info = await sym_info_async(symbol)
                
                if not atr or not s_info: continue

                # --- NEW: OBTENER PREFERENCIAS DE ESTRATEGIA ---
                # Usamos el comentario directamente (contiene el nombre real como PST-EMA-Flow)
                strat_name_from_comment = p.comment.replace("PST_", "")
                all_sym_strats = await self.db.get_symbol_strategies(symbol)
                strat_cfg = all_sym_strats.get(strat_name_from_comment, {})
                
                use_be = strat_cfg.get("use_breakeven", 0) == 1
                use_ts = strat_cfg.get("use_trailing", 0) == 1
                be_mult = strat_cfg.get("be_mult") or BE_ATR_MULTIPLIER
                ts_mult = strat_cfg.get("ts_mult") or TRAIL_ATR_MULTIPLIER

                # Variables de control
                current_price = s_info.bid if p_type == "BUY" else s_info.ask
                profit_points = (current_price - p.price_open) / s_info.point if p_type == "BUY" else (p.price_open - current_price) / s_info.point
                atr_points = atr / s_info.point
                
                new_sl = p.sl

                # A. LÓGICA DE BREAKEVEN (Condicional)
                if use_be:
                    is_sl_at_be = (p_type == "BUY" and p.sl >= p.price_open) or (p_type == "SELL" and p.sl <= p.price_open and p.sl > 0)
                    
                    # --- NEW: BE DINÁMICO POR R:R (Solo Scalper) ---
                    # Para el Scalper, retrasamos el BE hasta el 1.1 R:R para dar aire
                    if "Scalper" in p.comment:
                        # Recuperar SL inicial de la DB para calcular R:R real
                        active_db_trades = await self.db.get_active_trades()
                        db_trade = next((t for t in active_db_trades if t['ticket'] == ticket), None)
                        if db_trade:
                            sl_in_db = db_trade.get('sl', 0)
                            sl_dist_initial = abs(p.price_open - sl_in_db)
                            if sl_dist_initial > 0:
                                current_rr = (profit_points * s_info.point) / sl_dist_initial
                                if current_rr >= 1.0 and not is_sl_at_be:
                                    new_sl = p.price_open + (2 * s_info.point) if p_type == "BUY" else p.price_open - (2 * s_info.point)
                                    logger.info(f"🛡️ [BREAKEVEN R:R] {symbol} ticket {ticket} alcanzó 1.0 R:R. Protegiendo entrada.")

                    else:
                        # Lógica original por ATR para otras estrategias
                        if profit_points > (atr_points * be_mult) and not is_sl_at_be:
                            new_sl = p.price_open + (2 * s_info.point) if p_type == "BUY" else p.price_open - (2 * s_info.point)
                            logger.info(f"🛡️ [BREAKEVEN ATR] {symbol} (Ticket: {ticket}). Asegurando entrada.")

                # B. LÓGICA DE TRAILING STOP (Condicional)
                if use_ts:
                    if profit_points > (atr_points * ts_mult):
                        # --- NEW: TRAILING AGRESIVO POR ADX ---
                        # Si la tendencia es muy fuerte (ADX > 35), pegamos el SL más al precio (1.2x ATR en vez de 3x)
                        # Obtenemos ADX de H1 (está en el DF de mtf_data, aquí recalculamos por simplicidad o usamos el del símbolo)
                        adx_val = 0
                        adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
                        if adx_df is not None:
                            adx_val = adx_df['ADX_14'].iloc[-1]
                        
                        mult = ts_mult
                        if adx_val > 35:
                            mult = 1.2 # Muy pegado para proteger ante giro violento
                            logger.debug(f"⚡ [AGGRESSIVE TRAIL] {symbol} ADX: {adx_val:.1f}. Ajustando multiplicador a {mult}")
                        
                        trail_sl = current_price - (atr_points * mult * s_info.point) if p_type == "BUY" else current_price + (atr_points * mult * s_info.point)
                        
                        # Solo actualizamos el Trailing si mejora el SL actual
                        if p_type == "BUY" and trail_sl > new_sl:
                            new_sl = trail_sl
                            logger.info(f"📉 [TRAILING] {symbol} (Ticket: {ticket}). Siguiendo tendencia a {new_sl:.5f} (Mult: {mult})")
                        elif p_type == "SELL" and (trail_sl < new_sl or new_sl == 0):
                            new_sl = trail_sl
                            logger.info(f"📉 [TRAILING] {symbol} (Ticket: {ticket}). Siguiendo tendencia a {new_sl:.5f} (Mult: {mult})")

                # C. LÓGICA DE SALIDA DINÁMICA (Estrategias Específicas)
                if "PST_PST-EMA-Flow" in p.comment:
                    from ..strategies.pst_ema_flow import PSTEMAFlow
                    ema_strat = PSTEMAFlow()
                    if ema_strat.check_exit_signal(df, p_type):
                         logger.info(f"🛑 [DYNAMIC EXIT] {symbol} (Ticket: {ticket}) - Tendencia EMA Flow invalidada.")
                         # Cerramos a mercado...
                
                if "PST_PST-Scalper-Pro" in p.comment:
                    from ..strategies.pst_scalper_pro import PSTScalperPro
                    scalper_strat = PSTScalperPro()
                    if scalper_strat.check_exit_signal(df, p_type):
                         logger.info(f"🛑 [SCALPER EXIT] {symbol} (Ticket: {ticket}) - Cruce EMA9 (Trailing Dinámico).")
                         request = {
                             "action": mt5.TRADE_ACTION_DEAL,
                             "position": ticket,
                             "symbol": symbol,
                             "volume": p.volume,
                             "type": mt5.ORDER_TYPE_SELL if p_type == "BUY" else mt5.ORDER_TYPE_BUY,
                             "price": mt5.symbol_info_tick(symbol).bid if p_type == "BUY" else mt5.symbol_info_tick(symbol).ask,
                             "deviation": 20,
                             "magic": p.magic,
                             "comment": f"SCALPER_EMA9_EXIT_{ticket}",
                             "type_time": mt5.ORDER_TIME_GTC,
                             "type_filling": mt5.ORDER_FILLING_IOC,
                         }
                         res = mt5.order_send(request)
                         if res.retcode != mt5.TRADE_RETCODE_DONE:
                             logger.error(f"❌ Error cerrando por EMA9: {res.comment}")
                         else:
                             logger.info(f"✅ [SCALPER EXIT DONE] {symbol} ticket {ticket} cerrado por EMA9.")
                         continue # Siguiente posición, esta ya se cerró

                elif "PST_PST-Mean-Reversion" in p.comment:
                    from ..strategies.pst_mean_reversion import PSTMeanReversion
                    mr_strat = PSTMeanReversion()
                    direction = 1 if p_type == "BUY" else -1
                    new_tp = mr_strat.get_dynamic_targets(df, direction)
                    
                    if new_tp and abs(new_tp - p.tp) > (s_info.point * 2): # Margen de 2 puntos para evitar spam
                        logger.info(f"🎯 [DYNAMIC TP] {symbol} (Ticket: {ticket}). Actualizando objetivo a {new_tp:.5f}")
                        await modify_position_async(ticket, p.sl, round(new_tp, s_info.digits))
                        # Actualizamos el objeto p para que las siguientes comparaciones sean correctas
                        # pero como termina el loop para este p, solo hay que tenerlo en cuenta si hubiera más lógica

                # D. LÓGICA DE CIERRES PARCIALES (FASE 58)
                # Buscamos si el trade ya tuvo un cierre parcial en la DB
                active_db_trades = await self.db.get_active_trades()
                db_trade = next((t for t in active_db_trades if t['ticket'] == ticket), None)
                
                if db_trade and not db_trade.get('is_partial_closed', 0):
                    # Calculamos el el R:R actual
                    sl_dist_initial = abs(p.price_open - p.sl_initial) if hasattr(p, 'sl_initial') else (atr_points * be_mult * s_info.point)
                    # Nota: sl_initial no existe en el objeto position de mt5. 
                    # Lo estimamos o lo recuperamos de la DB.
                    sl_in_db = db_trade.get('sl', 0)
                    sl_dist_initial = abs(p.price_open - sl_in_db)
                    
                    if sl_dist_initial > 0:
                        current_rr = profit_points * s_info.point / sl_dist_initial
                        
                        # R:R de Cierre Parcial: 1.0 (1:1) para Scalper para asegurar ganancias rápido
                        partial_rr_threshold = 1.0 if "Scalper" in p.comment else 1.0
                        
                        if current_rr >= partial_rr_threshold:
                            # ¡DIPARAR CIERRE PARCIAL DEL 50%!
                            partial_vol = round(p.volume / 2, 2)
                            if partial_vol >= s_info.volume_min:
                                logger.info(f"🛡️ [PARTIAL CLOSE] {symbol} alcanzó {partial_rr_threshold} RR. Cerrando {partial_vol} lotes (50%).")
                                
                                close_request = {
                                    "action": mt5.TRADE_ACTION_DEAL,
                                    "position": ticket,
                                    "symbol": symbol,
                                    "volume": partial_vol,
                                    "type": mt5.ORDER_TYPE_SELL if p_type == "BUY" else mt5.ORDER_TYPE_BUY,
                                    "price": s_info.bid if p_type == "BUY" else s_info.ask,
                                    "magic": 666,
                                    "comment": "PST_PartialClose_50",
                                    "type_time": mt5.ORDER_TIME_GTC,
                                    "type_filling": mt5.ORDER_FILLING_IOC,
                                }
                                res = await send_order_async(close_request)
                                if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                                    await self.db.mark_trade_partial_closed(ticket)
                                    # Forzamos Breakeven inmediato
                                    new_sl = p.price_open + (2 * s_info.point) if p_type == "BUY" else p.price_open - (2 * s_info.point)
                                    logger.info(f"🛡️ [PARTIAL BE] {symbol} SL movido a Breakeven tras cierre parcial.")

                # F. LÓGICA DE TIME-OUT (Exclusivo Scalping)
                if "Scalper" in p.comment:
                    # Si el trade lleva más de 45 min abierto, cerramos si no hay profit claro
                    time_open = datetime.now() - datetime.fromtimestamp(p.time_setup if hasattr(p, 'time_setup') else p.time)
                    if time_open.total_seconds() > (45 * 60): # 45 Minutos
                        logger.warning(f"⏳ [TIME-OUT] Scalp {symbol} (Ticket: {ticket}) excedió 45 min. Cerrando por estancamiento.")
                        close_req = {
                            "action": mt5.TRADE_ACTION_DEAL,
                            "position": ticket,
                            "symbol": symbol,
                            "volume": p.volume,
                            "type": mt5.ORDER_TYPE_SELL if p_type == "BUY" else mt5.ORDER_TYPE_BUY,
                            "price": s_info.bid if p_type == "BUY" else s_info.ask,
                            "magic": 666,
                            "comment": "PST_TimeOutExit",
                            "type_time": mt5.ORDER_TIME_GTC,
                            "type_filling": mt5.ORDER_FILLING_IOC,
                        }
                        await send_order_async(close_req)
                        continue

                # E. MODO CIGARRA REMOVED

                # 3. Ejecutar modificación si ha cambiado el SL
                if abs(new_sl - p.sl) > s_info.point:
                    await modify_position_async(ticket, round(new_sl, s_info.digits), p.tp)

            except Exception as e:
                logger.error(f"❌ Error gestionando posición {p.ticket}: {e}")

    async def panic_close_all(self):
        """Cierra todas las posiciones abiertas lo más rápido posible."""
        positions = await get_positions_async()
        if not positions:
            logger.info("🛡️ [PANIC CLOSE] No hay posiciones abiertas para cerrar.")
            return

        logger.critical(f"🚨 [PANIC CLOSE] Iniciando cierre de emergencia de {len(positions)} posiciones!")
        
        tasks = []
        for p in positions:
            p_type = "BUY" if p.type == 0 else "SELL"
            s_info = mt5.symbol_info(p.symbol)
            if not s_info: continue
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "position": p.ticket,
                "symbol": p.symbol,
                "volume": p.volume,
                "type": mt5.ORDER_TYPE_SELL if p_type == "BUY" else mt5.ORDER_TYPE_BUY,
                "price": s_info.bid if p_type == "BUY" else s_info.ask,
                "magic": 666,
                "comment": "PST_PanicClose",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            tasks.append(send_order_async(request))
        
        results = await asyncio.gather(*tasks)
        for res in results:
            if res.retcode != mt5.TRADE_RETCODE_DONE:
                logger.error(f"❌ Fallo en Panic Close ticket {res.order}: {res.comment}")
            else:
                logger.info(f"✅ Cerrada exitosamente posición del ticket {res.order}")

    async def run_session_protection(self):
        """
        Escaneo de horario para cierre de seguridad T-5 min.
        Acciones: Diario. Otros (excepto Cripto): Viernes.
        """
        from ..config import SESSION_PROTECTION, CRYPTO_KEYWORDS
        from ..utils.notification_manager import notif_mgr
        
        # 1. Obtener tiempo actual (CET/Local del bot)
        now = datetime.now()
        current_time_str = now.strftime("%H:%M")
        is_friday = now.weekday() == 4
        
        positions = await get_positions_async()
        if not positions: return

        closable_tickets = []
        
        for p in positions:
            symbol = p.symbol
            a_class = get_asset_class(symbol)
            is_crypto = a_class == "CRYPTO" or any(k in symbol.upper() for k in CRYPTO_KEYWORDS)
            
            if is_crypto: continue # Inmunidad Cripto
            
            should_close = False
            reason = ""
            
            # A. Protección Acciones (EOD Diario)
            if a_class == "INDEX" and any(k in symbol.upper() for k in ["NVDA", "TSLA", "AAPL", "MSFT", "GOOG"]):
                if current_time_str >= SESSION_PROTECTION["STOCK_CLOSE_TIME"]:
                    should_close = True
                    reason = "EOD Stock Protection"
            
            # B. Protección Fin de Semana (Viernes Barrido)
            if is_friday and current_time_str >= SESSION_PROTECTION["WEEKEND_CLOSE_TIME"]:
                should_close = True
                reason = "Weekend Friday Sweep"
                
            if should_close:
                closable_tickets.append((p, reason))

        if not closable_tickets: return

        logger.warning(f"🛡️ [SESSION PROTECTION] Detectadas {len(closable_tickets)} posiciones para cierre de seguridad.")
        
        for p, reason in closable_tickets:
            p_type = "BUY" if p.type == 0 else "SELL"
            s_info = await sym_info_async(p.symbol)
            if not s_info: continue
            
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "position": p.ticket,
                "symbol": p.symbol,
                "volume": p.volume,
                "type": mt5.ORDER_TYPE_SELL if p_type == "BUY" else mt5.ORDER_TYPE_BUY,
                "price": s_info.bid if p_type == "BUY" else s_info.ask,
                "magic": 666,
                "comment": f"PST_{reason.replace(' ', '')}"[:31],
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            res = await send_order_async(req)
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"✅ [PROTECTION DONE] {p.symbol} cerrado por {reason}.")
                await notif_mgr.send_simple_alert(f"🛡️ [SESSION PROTECTION] {p.symbol} cerrado: {reason}")
            else:
                logger.error(f"❌ Error protegiendo {p.symbol}: {res.comment if res else 'None'}")
