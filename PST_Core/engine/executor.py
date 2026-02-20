import logging
import MetaTrader5 as mt5
from datetime import datetime
import pandas as pd
import pandas_ta as ta
from .mt5_async import send_order_async, sym_info_async, get_positions_async, modify_position_async, fetch_rates_async
from ..models.database import PSTDatabase
from ..portfolio.manager import PortfolioManager
from ..config import BE_ATR_MULTIPLIER, TRAIL_ATR_MULTIPLIER

logger = logging.getLogger("PST-Executor")

class PSTExecutor:
    def __init__(self, db: PSTDatabase, portfolio: PortfolioManager):
        self.db = db
        self.portfolio = portfolio

    async def execute_trade(self, symbol, signal_type, stop_loss_atr, take_profit_atr, strategy_name, regime):
        """
        Envía una orden real a MT5 tras validar lotes y riesgos.
        """
        # 0. Evitar duplicados (Check de posiciones abiertas)
        positions = await get_positions_async(symbol=symbol)
        if positions and len(positions) > 0:
            # Ya hay una posición abierta para este símbolo, no entramos de nuevo
            return None

        # 1. Obtener Info del Símbolo
        mt5.symbol_select(symbol, True)
        s_info = await sym_info_async(symbol)
        if not s_info:
            logger.error(f"❌ Imposible obtener info de {symbol} para ejecutar orden.")
            return

        # 2. Obtener Balance
        acc = await self.portfolio.get_account_status()
        if not acc: return

        # 3. Calcular Stop Loss y Lote con Volatilidad
        price = s_info.ask if signal_type == "BUY" else s_info.bid
        sl_points = stop_loss_atr / s_info.point
        tp_points = take_profit_atr / s_info.point

        # Obtener ATR actual y media para escalado de riesgo (V3.0)
        df_m5 = await fetch_rates_async(symbol, 5, 50)
        current_atr = stop_loss_atr / 3.0 # El SL suele ser 3*ATR
        ma_atr = 0
        if df_m5 is not None:
             atr_s = ta.atr(df_m5['high'], df_m5['low'], df_m5['close'], length=14)
             if atr_s is not None:
                 current_atr = atr_s.iloc[-1]
                 ma_atr = atr_s.rolling(20).mean().iloc[-1]

        lot = self.portfolio.calculate_lot_size(
            acc["balance"], 
            self.portfolio.max_risk_pct, 
            sl_points, 
            s_info,
            current_atr=current_atr,
            ma_atr=ma_atr
        )
        
        # 4. Construir el Request de MT5
        sl_price = price - stop_loss_atr if signal_type == "BUY" else price + stop_loss_atr
        tp_price = price + take_profit_atr if signal_type == "BUY" else price - take_profit_atr

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": mt5.ORDER_TYPE_BUY if signal_type == "BUY" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": sl_price,
            "tp": tp_price,
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
        await self.db.save_trade(trade_data)
        logger.info(f"✅ Orden ejecutada con éxito para {symbol}. Ticket: {result.order}")
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

                # Variables de control
                current_price = s_info.bid if p_type == "BUY" else s_info.ask
                profit_points = (current_price - p.price_open) / s_info.point if p_type == "BUY" else (p.price_open - current_price) / s_info.point
                atr_points = atr / s_info.point
                
                new_sl = p.sl

                # A. LÓGICA DE BREAKEVEN (Asegurar capital)
                # Si el beneficio > BE_ATR_MULTIPLIER * ATR y el SL no está aún en BE
                is_sl_at_be = (p_type == "BUY" and p.sl >= p.price_open) or (p_type == "SELL" and p.sl <= p.price_open and p.sl > 0)
                
                if profit_points > (atr_points * BE_ATR_MULTIPLIER) and not is_sl_at_be:
                    # Mover a Breakeven + 2 puntos de margen
                    new_sl = p.price_open + (2 * s_info.point) if p_type == "BUY" else p.price_open - (2 * s_info.point)
                    logger.info(f"🛡️ [BREAKEVEN] {symbol} (Ticket: {ticket}). Asegurando entrada.")

                # B. LÓGICA DE TRAILING STOP (Maximizar tendencia)
                # Si el beneficio > TRAIL_ATR_MULTIPLIER * ATR, el SL sigue al precio
                if profit_points > (atr_points * TRAIL_ATR_MULTIPLIER):
                    # --- NEW: TRAILING AGRESIVO POR ADX ---
                    # Si la tendencia es muy fuerte (ADX > 35), pegamos el SL más al precio (1.2x ATR en vez de 3x)
                    # Obtenemos ADX de H1 (está en el DF de mtf_data, aquí recalculamos por simplicidad o usamos el del símbolo)
                    adx_val = 0
                    adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
                    if adx_df is not None:
                        adx_val = adx_df['ADX_14'].iloc[-1]
                    
                    mult = TRAIL_ATR_MULTIPLIER
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
