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
from ..config import BE_ATR_MULTIPLIER, TRAIL_ATR_MULTIPLIER

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

        # 0. Evitar duplicados (Check de posiciones abiertas)
        positions = await get_positions_async(symbol=symbol)
        if positions and len(positions) > 0:
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
        clean_strat_name = strategy_name.replace("PST-", "").replace("PST_", "")
        all_sym_strats = await self.db.get_symbol_strategies(symbol)
        strat_cfg = all_sym_strats.get(clean_strat_name, {})
        
        # JERARQUÍA DE RIESGO: Estrategia > Símbolo > Global
        risk_mode = strat_cfg.get("risk_mode") or s_params.get("risk_mode") or "PCT"
        risk_val = strat_cfg.get("risk_value")
        if risk_val is None:
            risk_val = s_params.get("risk_value", 0.25)

        # JERARQUÍA DE MULTIPLICADORES: Estrategia > Símbolo > Default
        sl_m = strat_cfg.get("sl_mult") or s_params.get("sl_mult") or 2.5
        tp_m = strat_cfg.get("tp_mult") or s_params.get("tp_mult") or 6.0
        # R:R mínimo aceptable: si el TP no cubre min_rr veces el riesgo, se rechaza la orden
        min_rr = strat_cfg.get("min_rr") or s_params.get("min_rr") or 1.5
        
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
                if 0.5 <= dist_atr <= (sl_m * 1.5):
                    sl_price = structural_sl
                    structural_sl_found = True
                    logger.debug(f"📐 SL Estructural (BUY) fijado en {sl_price:.5f} (Swing Low)")
            else:
                # Swing High: max of last 15 bars
                recent_high = df_m5['high'].tail(15).max()
                structural_sl = recent_high + (current_atr * 0.2)
                dist_atr = (structural_sl - price) / current_atr
                if 0.5 <= dist_atr <= (sl_m * 1.5):
                    sl_price = structural_sl
                    structural_sl_found = True
                    logger.debug(f"📐 SL Estructural (SELL) fijado en {sl_price:.5f} (Swing High)")

        # Fallback al SL técnico estricto si el estructural es loco o no hay datos
        if not structural_sl_found:
             sl_price = price - real_sl_atr if signal_type == "BUY" else price + real_sl_atr
             logger.debug(f"📐 SL Dinámico (ATR x {sl_m}) fijado en {sl_price:.5f}")

        # Recalcular SL Points reales para el dimensionamiento del lote
        sl_points = abs(price - sl_price) / s_info.point
        
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

        # B. CALCULAR TAKE PROFIT TÉCNICO
        tp_price = 0
        if metadata and metadata.get("tp_target", 0) > 0:
            tp_price = metadata["tp_target"]
            # Fallback de seguridad: si el tp_price está detrás del precio actual, usar multiplicador
            if (signal_type == "BUY" and tp_price <= price) or (signal_type == "SELL" and tp_price >= price):
                logger.warning(f"⚠️ [TP TÉCNICO] TP objetivo ({tp_price:.5f}) inválido para precio actual ({price:.5f}). Usando mult ({tp_m}x).")
                tp_price = price + real_tp_atr if signal_type == "BUY" else price - real_tp_atr
            else:
                logger.info(f"🎯 [TP TÉCNICO] Usando objetivo de estrategia: {tp_price:.5f}")
        else:
             tp_price = price + real_tp_atr if signal_type == "BUY" else price - real_tp_atr

        # --- CHECK R:R MÍNIMO ---
        tp_dist = abs(tp_price - price)
        sl_dist = abs(sl_price - price)
        rr_actual = (tp_dist / sl_dist) if sl_dist > 0 else 0
        if rr_actual < min_rr:
            logger.warning(
                f"⚠️ [R:R RECHAZADO] {symbol} {strategy_name}: "
                f"R:R={rr_actual:.2f} < mín={min_rr:.1f} "
                f"(TP dist={tp_dist:.4f} / SL dist={sl_dist:.4f}). Orden cancelada."
            )
            return None

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": mt5.ORDER_TYPE_BUY if signal_type == "BUY" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": round(sl_price, s_info.digits),
            "tp": round(tp_price, s_info.digits),
            "magic": 666, # Magic Number PST
            "comment": f"PST_{strategy_name}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        # 5. Envío Asíncrono
        logger.info(f"🚀 [ORDEN] {signal_type} {symbol} | Estrategia: {strategy_name} | Régimen: {regime}")
        logger.info(f"   📊 Detalles: Lote {lot} | SL {sl_price:.5f} | TP {tp_price:.5f}")
        result = await send_order_async(request)

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
            "ticket": result.order
        }
        # --- NEW: TRADE CONTEXT CAPTURE (FASE 53) ---
        try:
            # Capturamos contexto de 50 velas M15 para el Journal
            import json
            # time_out=0 para obtener los más recientes
            context_rates = await fetch_rates_async(symbol, mt5.TIMEFRAME_M15, 0, 50)
            if context_rates is not None:
                ohlc_list = []
                for r in context_rates:
                    ohlc_list.append({
                        "time": int(r['time']),
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
                # Asumimos que el comment tiene el formato PST_NombreEstrategia
                strat_name_from_comment = p.comment.replace("PST_", "")
                all_sym_strats = await self.db.get_symbol_strategies(symbol)
                strat_cfg = all_sym_strats.get(strat_name_from_comment.replace("PST-", ""), {})
                
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
                    if profit_points > (atr_points * be_mult) and not is_sl_at_be:
                        new_sl = p.price_open + (2 * s_info.point) if p_type == "BUY" else p.price_open - (2 * s_info.point)
                        logger.info(f"🛡️ [BREAKEVEN] {symbol} (Ticket: {ticket}). Asegurando entrada.")

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
                         logger.info(f"🛑 [DYNAMIC EXIT] {symbol} (Ticket: {ticket}) - Tendencia invalidada.")
                         # Cerramos a mercado
                         request = {
                             "action": mt5.TRADE_ACTION_DEAL,
                             "position": ticket,
                             "symbol": symbol,
                             "volume": p.volume,
                             "type": mt5.ORDER_TYPE_SELL if p_type == "BUY" else mt5.ORDER_TYPE_BUY,
                             "price": s_info.bid if p_type == "BUY" else s_info.ask,
                             "magic": 666,
                             "comment": "PST_DynamicExit",
                             "type_time": mt5.ORDER_TIME_GTC,
                             "type_filling": mt5.ORDER_FILLING_IOC,
                         }
                         await send_order_async(request)
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
