import logging
import MetaTrader5 as mt5
import asyncio
from datetime import datetime
import pandas as pd
import pandas_ta as ta
from .mt5_async import send_order_async, sym_info_async, get_positions_async, modify_position_async, fetch_rates_async, close_position_async
from .telegram_manager import telegram_bot
from ..models.database import PSTDatabase
from ..portfolio.manager import PortfolioManager
from ..config import BE_ATR_MULTIPLIER, TRAIL_ATR_MULTIPLIER, TP_ATR_BY_CLASS, STRATEGY_CATEGORIES, MAX_POSITIONS_PER_CATEGORY, MAX_SYMBOL_EXPOSURE_PCT, SCALPER_PARTIAL_CLOSE_ENABLED, SCALPER_PARTIAL_CLOSE_PCT, SCALPER_PARTIAL_BE_COMMISSION_PADDING_PTS, COMMISSION_SPEC, CRYPTO_MAX_COMMISSION_R, SCALPER_PARTIAL_CLOSE_DISABLED_CLASSES
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

        # El strategy_name ya llega como ID técnico (ej: "PST-AlphaTrend") desde el orquestador.
        # Fase 4: se puede añadir aquí un reverse_map si alguna estrategia usa nombre legible.
        raw_name = strategy_name

        # --- CATEGORY & RISK LIMITS (MOVED TO PortfolioManager.can_open_trade) --- 
        # Ya validado en orquestador vía can_open_trade antes de disparar el executor.

        # 1. Obtener Info del Símbolo y Parámetros
        mt5.symbol_select(symbol, True)
        s_info = await sym_info_async(symbol)
        if not s_info:
            logger.error(f"❌ Imposible obtener info de {symbol} para ejecutar orden.")
            return

        # --- NEW: CARGA DE CONFIGURACIÓN DINÁMICA (FASE 46) ---
        # --- PARAMETERS LOOKUP ---
        # s_params vendrán de symbols_config (globales)
        # s_params vendrán de symbols_config (globales)
        s_params = await self.db.get_symbol_params(symbol)
        # strat_cfg vendrán de symbol_strategies (específicos de esta estrategia para este símbolo)
        strat_cfg = (await self.db.get_symbol_strategies(symbol)).get(raw_name, {})
        
        # JERARQUÍA DE RIESGO: Metadata (Manual) > Estrategia > Símbolo > Kelly > Global
        risk_mode = (metadata.get("risk_mode") if metadata else None) or strat_cfg.get("risk_mode") or s_params.get("risk_mode") or "PCT"
        risk_val = (metadata.get("risk_value") if metadata else None)
        if risk_val is None:
            risk_val = strat_cfg.get("risk_value")
        if risk_val is None:
            risk_val = s_params.get("risk_value")

        # Kelly Criterion: si no hay configuración explícita, calcular riesgo óptimo
        if risk_val is None and risk_mode == "PCT":
            try:
                from ..utils.kelly_sizer import KellySizer
                _kelly = KellySizer(self.db.db_path)
                _fallback = 0.25
                risk_val, _kelly_src = await _kelly.get_risk_pct(raw_name, fallback_pct=_fallback)
                if _kelly_src == "KELLY":
                    logger.info(f"📐 [KELLY] {symbol}/{raw_name}: riesgo Half-Kelly = {risk_val:.3f}%")
            except Exception as _ke:
                logger.debug(f"[Kelly] Error calculando Kelly para {raw_name}: {_ke}")
                risk_val = 0.25
        elif risk_val is None:
            risk_val = 0.25

        # JERARQUÍA DE MULTIPLICADORES: Estrategia > Símbolo > Asset Class Default
        a_class = get_asset_class(symbol)
        def_tp_m = TP_ATR_BY_CLASS.get(a_class, 4.0) # Ajustado a 4.0 para R:R 1.2 más realista
        
        sl_m = strat_cfg.get("sl_mult") or s_params.get("sl_mult") or 2.5
        
        # SL Ceñido para estrategias de Scalping (1.25 ATR como default)
        if "Scalping" in raw_name:
            if not strat_cfg.get("sl_mult") and not s_params.get("sl_mult"):
                sl_m = 1.25
                logger.debug(f"📐 [SCALPING SL] Usando SL Ceñido: {sl_m}x ATR")

        tp_m = strat_cfg.get("tp_mult") or s_params.get("tp_mult") or def_tp_m
        min_rr = (metadata.get("rr_ratio") if metadata else None) or strat_cfg.get("min_rr") or s_params.get("min_rr") or 1.2

        # R:R mínimo 1.2 para estrategias de Scalping
        if "Scalping" in raw_name:
            min_rr = max(min_rr, 1.2)
        
        # Recalcular SL/TP en base a los multiplicadores reales
        # Nota: stop_loss_atr entrante suele ser (ATR * 2.5) del símbolo. 
        # Pero aquí queremos precisión absoluta. Recalculamos sobre el ATR base.
        # stop_loss_atr / 2.5 nos da el ATR_unitario aproximado.
        atr_unit = stop_loss_atr / 2.5 # Estimación rápida si no recalculamos ATR aquí
        
        real_sl_atr = atr_unit * sl_m
        real_tp_atr = atr_unit * tp_m

        # --- PROTECCIÓN DE SPREAD (v2.0.3 - Relajado para Rebotes) ---
        # Subimos del 35% al 55% para evitar bloqueos en alta volatilidad
        spread_pts = s_info.spread
        point = s_info.point
        spread_dist = spread_pts * point
        max_spread_allowed = real_sl_atr * 0.55
        
        if spread_dist > max_spread_allowed:
            logger.warning(f"🛑 [SPREAD BLOCK] {symbol} rechazado. Spread {spread_dist:.5f} > Max permitido {max_spread_allowed:.5f} (55% SL)")
            return

        # 1.3 Obtener parámetros TS/BE de la base de datos
        use_trailing = strat_cfg.get("use_trailing", 0) == 1
        use_breakeven = strat_cfg.get("use_breakeven", 0) == 1

        # 2. Obtener Balance (con cubeta de capital por estrategia)
        acc = await self.portfolio.get_account_status()
        if not acc: return

        # Aplicar cubeta de capital si el BucketManager está disponible
        effective_balance = acc["balance"]
        try:
            from ..utils.bucket_manager import bucket_manager
            if bucket_manager is not None:
                effective_balance = bucket_manager.get_bucket_balance(raw_name, acc["balance"])
        except Exception:
            pass

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

        # --- OVERRIDE: SL STRICTO DESDE ESTRATEGIA (v4.0 Scalper) ---
        if metadata and metadata.get("target_price_sl", 0) > 0:
            custom_sl = metadata["target_price_sl"]
            # Validar que esté del lado correcto
            if (signal_type == "BUY" and custom_sl < price) or (signal_type == "SELL" and custom_sl > price):
                sl_price = custom_sl
                structural_sl_found = True
                logger.debug(f"📐 SL Dinámico (Estrategia) fijado en {sl_price:.5f}")

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

        # --- COMMISSION GUARD (v2.6.2): en cripto la comisión es % del nocional, no del SL.
        # Con SL ceñido (scalping) la comisión puede comerse >40% del riesgo por trade
        # (medido: BTCUSD avg 0.43R, ETHUSD avg 0.32R, juez fiel 20d). Bloquea la entrada si
        # la comisión proyectada supera CRYPTO_MAX_COMMISSION_R del riesgo (R) de este trade.
        comm_spec = COMMISSION_SPEC.get(a_class, {})
        pct_notional = comm_spec.get("pct_notional", 0.0)
        if pct_notional > 0:
            sl_dist = abs(price - sl_price)
            commission_r = (pct_notional * price / sl_dist) if sl_dist > 0 else float("inf")
            if commission_r > CRYPTO_MAX_COMMISSION_R:
                logger.warning(f"🛑 [COMMISSION GUARD] {symbol} rechazado. Comisión proyectada "
                               f"{commission_r:.2f}R > máximo {CRYPTO_MAX_COMMISSION_R}R (SL {sl_dist:.2f} muy ceñido).")
                return None

        lot = self.portfolio.calculate_lot_size(
            effective_balance,
            self.portfolio.max_risk_pct,
            sl_points,
            s_info,
            current_atr=current_atr,
            ma_atr=ma_atr,
            risk_mode=risk_mode,
            risk_value=risk_val,
            regime=regime
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
        target_tp = metadata.get("target_price_tp", 0) if metadata else 0
        if not target_tp and metadata:
             target_tp = metadata.get("tp_target", 0)
             
        if target_tp > 0:
            tp_price = target_tp
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
        # --- NEW: STOP & REVERSE LOGIC (HEDGING PROTECTION) ---
        positions = await get_positions_async(symbol=symbol)
        if positions is None:
            # FAIL-SAFE: None = error de terminal MT5, no "sin posiciones". Sin saber
            # si hay posición contraria no es seguro enviar la orden.
            logger.error(f"❌ [FAIL-SAFE] {symbol}: positions_get() devolvió None antes de enviar la orden. Abortando trade.")
            return None
        if positions:
            for opp_p in positions:
                # Si hay una posición en la dirección contraria, la cerramos
                if (signal_type == "BUY" and opp_p.type == 1) or (signal_type == "SELL" and opp_p.type == 0):
                    logger.info(f"🔄 [REVERSAL] Mercado a la contra. Cerrando posición en {symbol} (Ticket: {opp_p.ticket}) antes de invertir la dirección.")
                    res = await close_position_async(opp_p.ticket)
                    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                        logger.info(f"✅ Posición contraria {opp_p.ticket} cerrada con éxito.")
                        profit_val = float((res.price - opp_p.price_open) * opp_p.volume * s_info.trade_tick_value / s_info.point) if opp_p.type==0 else float((opp_p.price_open - res.price) * opp_p.volume * s_info.trade_tick_value / s_info.point)
                        await self.db.update_trade_cierre(opp_p.ticket, res.price, profit_val)
                    else:
                        logger.error(f"❌ Fallo al cerrar posición contraria {opp_p.ticket}: {res.comment if res else 'Unknown'}")

        # --- NEW: COMUNICACIÓN DE TF Y ABREVIATURA PARA MT5 ---
        tf_str = ""
        if metadata and "factors_detailed" in metadata:
            for f in metadata["factors_detailed"]:
                if f.get("k") == "TF":
                    tf_str = f.get("v")
                    break
        
        # Abreviación limpia para el comentario de la orden MT5
        abbrev = raw_name.replace("PST-", "")
        
        comment_raw = f"{abbrev}_{tf_str}" if tf_str else f"{abbrev}"
        
        # Limpiar comentario de caracteres especiales (MT5 es estricto, límite 31 chars)
        clean_comment = "".join(c if c.isalnum() or c == "_" else "_" for c in comment_raw)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": mt5.ORDER_TYPE_BUY if signal_type == "BUY" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": round(sl_price, s_info.digits),
            "tp": round(tp_price, s_info.digits),
            "magic": 666, # Magic Number PST
            "comment": clean_comment[:31],
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
                # El comentario de la orden es el nombre SIN el prefijo "PST-" (ver
                # execute_trade: abbrev = raw_name.replace("PST-", "")), p.ej. "PrecisionScalping_M1".
                # Buscamos qué estrategia registrada en DB coincide por substring (igual que
                # el check "Scalping" in p.comment ya usado más abajo para is_scalper_pos).
                all_sym_strats = await self.db.get_symbol_strategies(symbol)
                strat_name_from_comment = next(
                    (k for k in all_sym_strats if k.replace("PST-", "") in p.comment), None
                )
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
                # Detectar posiciones de tipo Scalping por el comentario del trade
                is_scalper_pos = "Scalping" in p.comment or "Scalper" in p.comment
                
                if use_be or is_scalper_pos:  # Scalpers siempre usan BE
                    is_sl_at_be = (p_type == "BUY" and p.sl >= p.price_open) or (p_type == "SELL" and p.sl <= p.price_open and p.sl > 0)
                    
                    # Suavizamos el BE multiplicándolo por 2.0x mínimo para evitar ser sacados por ruido
                    # Para scalpers usamos 1.5x para reaccionar más rápido
                    safe_be_mult = max(1.5, be_mult) if is_scalper_pos else max(2.5, be_mult)
                    
                    if profit_points > (atr_points * safe_be_mult) and not is_sl_at_be:
                        # Para scalpers: padding de comisión para que el BE cubra costes
                        be_padding_pts = SCALPER_PARTIAL_BE_COMMISSION_PADDING_PTS if is_scalper_pos else 1
                        new_sl = p.price_open + (be_padding_pts * s_info.point) if p_type == "BUY" else p.price_open - (be_padding_pts * s_info.point)
                        logger.info(f"🛡️ [BREAKEVEN] {symbol} (Ticket: {ticket}). Progreso de {safe_be_mult}x ATR alcanzado. Asegurando entrada (Padding: {be_padding_pts} pts).")


                # B. LÓGICA DE TRAILING STOP (Condicional)
                if use_ts:
                    # Trailing también retrasado temporalmente para dejar transpirar
                    safe_ts_mult = max(2.0, ts_mult)
                    
                    if profit_points > (atr_points * safe_ts_mult):
                        # --- NEW: TRAILING AGRESIVO POR ADX ---
                        # Si la tendencia es muy fuerte (ADX > 35), pegamos el SL más al precio (1.2x ATR en vez de 3x)
                        # Obtenemos ADX de H1 (está en el DF de mtf_data, aquí recalculamos por simplicidad o usamos el del símbolo)
                        adx_val = 0
                        adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
                        if adx_df is not None:
                            adx_val = adx_df['ADX_14'].iloc[-1]
                        
                        mult = ts_mult
                        if adx_val > 40: # ADX Endurecido a 40 para ser exigentes
                            mult = 1.5 # Relajado
                            logger.debug(f"⚡ [AGGRESSIVE TRAIL] {symbol} ADX: {adx_val:.1f}. Ajustando multiplicador a {mult}")
                        
                        trail_sl = current_price - (atr_points * mult * s_info.point) if p_type == "BUY" else current_price + (atr_points * mult * s_info.point)
                        
                        # Solo actualizamos el Trailing si mejora el SL actual
                        if p_type == "BUY" and trail_sl > new_sl:
                            new_sl = trail_sl
                            logger.info(f"📉 [TRAILING] {symbol} (Ticket: {ticket}). Siguiendo tendencia a {new_sl:.5f} (Mult: {mult})")
                        elif p_type == "SELL" and (trail_sl < new_sl or new_sl == 0):
                            new_sl = trail_sl
                            logger.info(f"📉 [TRAILING] {symbol} (Ticket: {ticket}). Siguiendo tendencia a {new_sl:.5f} (Mult: {mult})")

                # C. LÓGICA DE SALIDA DINÁMICA
                # PST-PrecisionScalping: salida si el precio cruza el VWAP en contra.
                # Tiempo mínimo de sostenimiento (120s): impide que la salida VWAP cierre
                # la posición en su propia vela de entrada por un simple tick de ruido.
                pos_age_secs = (datetime.now() - datetime.fromtimestamp(p.time_setup if hasattr(p, "time_setup") else p.time)).total_seconds()
                if "PrecisionScalping" in p.comment and pos_age_secs >= 120:
                    from ..strategies.pst_precision_scalping import PSTPrecisionScalping
                    ps_strat = PSTPrecisionScalping()
                    mtf_exit = {"m1": await fetch_rates_async(symbol, 1, 50), "m5": df}
                    if ps_strat.check_exit_signal(mtf_exit, p_type, symbol=symbol, filter_profile=strat_cfg.get("filter_profile")):
                        logger.info(f"🛑 [SCALPING EXIT] {symbol} (Ticket: {ticket}) — Precio cruzó VWAP en contra.")
                        req = {
                            "action": mt5.TRADE_ACTION_DEAL,
                            "position": ticket,
                            "symbol": symbol,
                            "volume": p.volume,
                            "type": mt5.ORDER_TYPE_SELL if p_type == "BUY" else mt5.ORDER_TYPE_BUY,
                            "price": mt5.symbol_info_tick(symbol).bid if p_type == "BUY" else mt5.symbol_info_tick(symbol).ask,
                            "deviation": 20,
                            "magic": p.magic,
                            "comment": f"PScalp_VWAP_EXIT_{ticket}"[:31],
                            "type_time": mt5.ORDER_TIME_GTC,
                            "type_filling": mt5.ORDER_FILLING_IOC,
                        }
                        res = mt5.order_send(req)
                        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                            logger.info(f"✅ [SCALPING EXIT DONE] {symbol} ticket {ticket} cerrado por VWAP.")
                        else:
                            logger.error(f"❌ Error en salida VWAP: {res.comment if res else 'None'}")
                        continue

                # D. LÓGICA DE CIERRES PARCIALES
                # REACTIVADO: Ahora los scalpers también pueden cerrar parciales si está habilitado en config
                partial_class_ok = get_asset_class(symbol) not in SCALPER_PARTIAL_CLOSE_DISABLED_CLASSES
                scalper_partial_ok = is_scalper_pos and SCALPER_PARTIAL_CLOSE_ENABLED and partial_class_ok
                if "Scalper" not in p.comment or scalper_partial_ok:
                    active_db_trades = await self.db.get_active_trades()
                    db_trade = next((t for t in active_db_trades if t['ticket'] == ticket), None)
                    
                    if db_trade and not db_trade.get('is_partial_closed', 0):
                        sl_in_db = db_trade.get('sl', 0)
                        sl_dist_initial = abs(p.price_open - sl_in_db)
                        
                        if sl_dist_initial > 0:
                            current_rr = profit_points * s_info.point / sl_dist_initial
                            partial_rr_threshold = 1.0  # Cerrar parcial al alcanzar 1.0R
                            partial_pct = SCALPER_PARTIAL_CLOSE_PCT if is_scalper_pos else 0.50
                            
                            if current_rr >= partial_rr_threshold:
                                partial_vol = round(p.volume * partial_pct, 2)
                                if partial_vol >= s_info.volume_min:
                                    logger.info(f"🛡️ [PARTIAL CLOSE] {symbol} alcanzó {partial_rr_threshold} RR. Cerrando {partial_vol} lotes ({int(partial_pct*100)}%).")
                                    
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
                                        # Forzamos Breakeven inmediato con padding de comisión para scalpers
                                        be_pad = SCALPER_PARTIAL_BE_COMMISSION_PADDING_PTS if is_scalper_pos else 2
                                        new_sl = p.price_open + (be_pad * s_info.point) if p_type == "BUY" else p.price_open - (be_pad * s_info.point)
                                        logger.info(f"🛡️ [PARTIAL BE] {symbol} SL movido a Breakeven tras cierre parcial (Padding: {be_pad} pts).")

                    # Si el trade lleva más de 30 min abierto, cerramos si no hay profit claro
                    time_open = datetime.now() - datetime.fromtimestamp(p.time_setup if hasattr(p, 'time_setup') else p.time)
                    # --- FIX: Solo cerrar por timeout si el mercado está abierto ---
                    is_full_tradable = s_info.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL if s_info else False
                    if time_open.total_seconds() > (30 * 60) and is_full_tradable: # 30 Minutos
                        logger.warning(f"⏳ [TIME-OUT] Scalp {symbol} (Ticket: {ticket}) excedió 30 min. Cerrando por estancamiento.")
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
                        res = await send_order_async(close_req)
                        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                            logger.info(f"✅ [TIME-OUT EXIT DONE] {symbol} ticket {ticket} cerrado por estancamiento.")
                        else:
                            logger.error(f"❌ [TIME-OUT EXIT FAILED] {symbol} ticket {ticket}: {res.comment if res else 'None'} (retcode={res.retcode if res else 'N/A'})")
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
