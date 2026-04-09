import asyncio
import logging
import MetaTrader5 as mt5
from ..core.bus import bus
from ..models.events import SignalEvent, OrderEvent

logger = logging.getLogger("PST_V2.Executor")

class AsyncExecutor:
    """
    Ejecutor V2: Suscribe a SignalEvent y ejecuta órdenes en MT5.
    Incluye gestión de riesgo dinámica (Risk 2.0).
    """
    
    def __init__(self, magic_number: int = 777, risk_per_trade_euro: float = 5.0):
        self.magic = magic_number
        self.risk_euro = risk_per_trade_euro
        self.enabled = True
        logger.info(f"🚀 Ejecutor V2 Online (Magic: {self.magic}, Riesgo: {self.risk_euro}€/trade)")

    async def on_signal(self, signal: SignalEvent):
        if not self.enabled:
            return
            
        logger.info(f"⚡ [EJECUTOR] Señal recibida: {signal.strategy} ({signal.signal_type})")
        
        # 1. Filtro de Control de Exposición
        if not self._check_exposure(signal.symbol):
            logger.warning(f"⚠️ [EXPOSURE] Omitiendo {signal.symbol}: Ya existe posición abierta.")
            return

        # 2. Cálculo de Lotaje Dinámico (Risk 2.0)
        lot = self._calculate_lot(signal)
        if lot <= 0:
            logger.error(f"❌ [RISK] Lotaje inválido calculado: {lot}. Omitiendo orden.")
            return

        # 3. Preparar Orden
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": signal.symbol,
            "volume": lot,
            "type": mt5.ORDER_TYPE_BUY if signal.signal_type == "BUY" else mt5.ORDER_TYPE_SELL,
            "price": signal.entry_price,
            "sl": signal.sl,
            "tp": signal.tp,
            "magic": self.magic,
            "comment": f"V2_{signal.strategy[:5]}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        # 4. Envío Asíncrono
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: mt5.order_send(request))

        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"✅ [V2] ORDEN OK: {signal.symbol} {lot} lots (Ticket: {result.order})")
            await bus.emit("order", OrderEvent(ticket=result.order, symbol=signal.symbol, type="OPENED", price=result.price))
        else:
            reason = result.comment if result else "Timeout/Unknown"
            logger.error(f"❌ [V2] ORDEN FALLIDA: {reason}")
            await bus.emit("order", OrderEvent(symbol=signal.symbol, type="REJECTED", comment=reason))

    def _calculate_lot(self, signal: SignalEvent) -> float:
        """Calcula el lote para perder exactamente 'risk_euro' si toca el SL."""
        try:
            point = mt5.symbol_info(signal.symbol).point
            tick_value = mt5.symbol_info(signal.symbol).trade_tick_value
            tick_size = mt5.symbol_info(signal.symbol).trade_tick_size
            
            sl_dist_points = abs(signal.entry_price - signal.sl) / point
            if sl_dist_points <= 0: return 0.0
            
            # Riesgo en moneda de la cuenta (asumiendo EUR por defecto como pidió el usuario)
            # lote = riesgo_dinero / (distancia_sl_en_puntos * valor_del_punto)
            lot = self.risk_euro / (sl_dist_points * point * (tick_value / tick_size))
            
            # Ajustar a pasos del broker
            lot_step = mt5.symbol_info(signal.symbol).volume_step
            lot = round(lot / lot_step) * lot_step
            
            # Límites de seguridad
            min_lot = mt5.symbol_info(signal.symbol).volume_min
            max_lot = mt5.symbol_info(signal.symbol).volume_max
            return max(min_lot, min(max_lot, round(lot, 2)))
        except Exception as e:
            logger.error(f"Error calculando lotaje: {e}")
            return 0.0

    def _check_exposure(self, symbol: str) -> bool:
        positions = mt5.positions_get(symbol=symbol)
        return positions is None or len(positions) == 0

    def start(self):
        bus.subscribe("signal", self.on_signal)

    def stop(self):
        self.enabled = False
        logger.info("🛑 Ejecutor V2 Detenido")
