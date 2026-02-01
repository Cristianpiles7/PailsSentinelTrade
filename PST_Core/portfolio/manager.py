import logging
import MetaTrader5 as mt5
from typing import Dict, List

logger = logging.getLogger("PST-Portfolio")

class PortfolioManager:
    def __init__(self, max_risk_pct=1.5, max_drawdown_pct=3.5):
        self.max_risk_pct = max_risk_pct
        self.max_drawdown_pct = max_drawdown_pct
        
        # Mapa de correlaciones (Actualizado: Sin restricción USD a petición del usuario)
        self.correlation_groups = {
            "TECH": ["NVDA", "TSLA", "GOOG", "AAPL", "MSFT", "US500.cash"],
            "EURO": ["EURGBP", "EURJPY", "EU50.cash"]
        }

    async def get_account_status(self):
        """Obtiene el estado de la cuenta de forma thread-safe."""
        acc = mt5.account_info()
        if acc is None:
            return None
        return {
            "balance": acc.balance,
            "equity": acc.equity,
            "margin_free": acc.margin_free,
            "drawdown": (1 - (acc.equity / acc.balance)) * 100 if acc.balance > 0 else 0
        }

    def get_symbol_group(self, symbol: str) -> List[str]:
        """Identifica a qué grupo de correlación pertenece un símbolo."""
        for group, symbols in self.correlation_groups.items():
            if symbol in symbols:
                return group
        return "OTHERS"

    async def can_open_trade(self, symbol: str, signal_type: str, current_positions):
        """
        Lógica de control de riesgo global (FTMO Friendly).
        """
        acc = await self.get_account_status()
        if not acc: return False
        
        # 1. Kill-Switch por Drawdown Global (Seguro FTMO al 3.5%)
        if acc["drawdown"] >= self.max_drawdown_pct:
            logger.warning(f"🛑 KILL-SWITCH FTMO: Drawdown del {acc['drawdown']:.2f}% (Límite: {self.max_drawdown_pct}%)")
            return False

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
        """Comprueba si falta poco para el cierre de la sesión actual."""
        # Nota: La implementación exacta requiere symbol_info_get pero MT5 no siempre 
        # devuelve la sesión de cierre correctamente en todas las VPS.
        # Por seguridad en PST, usamos la hora actual vs horas de mercado estándar si es Acción.
        # O podemos usar una simplificación: si es viernes tarde para Forex, etc.
        from datetime import datetime
        now = datetime.now()
        
        # Lógica simplificada para Acciones (NASDAQ/NYSE cierran a las 22:00 hora MT5 aprox)
        # TODO: Refinar con mt5.symbol_info_get(symbol).session_close si el broker lo reporta bien
        if any(stock in symbol for stock in ["AAPL", "NVDA", "TSLA", "GOOG", "MSFT"]):
            if now.hour == 21 and now.minute >= (60 - minutes_before):
                return True
        return False

    def calculate_lot_size(self, balance, risk_per_trade_pct, stop_loss_points, symbol_info, current_atr=None, ma_atr=None):
        """
        Calcula el lotaje basado en el riesgo monetario, CAP de exposición y VOLATILIDAD.
        """
        
        if stop_loss_points <= 0 or symbol_info is None:
            return symbol_info.volume_min if symbol_info else 0.01

        # --- VOLATILITY RISK ADJUSTMENT (NEW V3.0) ---
        adjusted_risk = risk_per_trade_pct
        vol_factor = 1.0
        if current_atr and ma_atr and ma_atr > 0:
             vol_factor = ma_atr / current_atr
             # Ajustar el riesgo: Si hay pánico (ATR > MA), bajamos riesgo. Si hay calma, subimos un poco.
             # Rango de riesgo: 1.5% base -> min 0.75%, max 1.8%
             adjusted_risk = risk_per_trade_pct * vol_factor
             adjusted_risk = max(0.75, min(1.8, adjusted_risk))
             logger.info(f"🧮 [VOLATILITY] Factor: {vol_factor:.2f} -> Riesgo ajustado: {adjusted_risk:.2f}%")

        risk_money = balance * (adjusted_risk / 100)
        
        # FIX: Usar directamente trade_tick_value (Valor de 1 punto/tick de movimiento)
        tick_value = symbol_info.trade_tick_value
        
        # Debug crítico para el usuario
        logger.info(f"🧮 [DEBUG LOTS] Balance: {balance} | Risk: {adjusted_risk:.2f}% (${risk_money:.2f})")
        logger.info(f"   ℹ️ Stats: TickVal={tick_value} | SL Points={stop_loss_points}")

        if tick_value == 0:
            logger.error("❌ Error: Tick Value es 0. Usando lote mínimo.")
            return symbol_info.volume_min

        # Fórmula Correcta: Risk = Lots * Points * TickValue
        try:
            raw_lot = risk_money / (stop_loss_points * tick_value)
        except ZeroDivisionError:
             raw_lot = symbol_info.volume_min
             
        # --- APPLIED CAP LOGIC (V3.1 Dynamic Balance %) ---
        from ..config import MAX_POSITION_COST_PCT
        
        estimated_price = symbol_info.ask if symbol_info.ask > 0 else symbol_info.last
        contract_size = symbol_info.trade_contract_size if symbol_info.trade_contract_size > 0 else 1
        
        logger.info(f"   🔍 [DEBUG CAP] Sym: {symbol_info.name} | Price: {estimated_price} | Contract: {contract_size} | TickVal: {tick_value}")
        
        if estimated_price > 0:
             # El CAP ahora es un porcentaje del balance para asegurar "slots" multiactivo
             nominal_cap_money = balance * (MAX_POSITION_COST_PCT / 100)
             denom = estimated_price * contract_size
             max_lots_by_cap = nominal_cap_money / denom if denom > 0 else 0
             
             if max_lots_by_cap > 0 and raw_lot > max_lots_by_cap:
                 # FALLBACK: Si el CAP es demasiado bajo para operar siquiera el mínimo (ej. Oro),
                 # permitimos al menos el volumen mínimo del broker para que no se bloquee.
                 safe_lot_cap = max(max_lots_by_cap, symbol_info.volume_min)
                 if raw_lot > safe_lot_cap:
                    logger.info(f"   ✂️ CAP DINÁMICO ({MAX_POSITION_COST_PCT}%): {raw_lot:.2f} lotes -> {safe_lot_cap:.4f} lotes")
                    raw_lot = safe_lot_cap
        
        logger.info(f"   ⚖️ Lot Final (Pre-Broker): {raw_lot}")
             
        # Ajustar a límites del broker
        lot = max(symbol_info.volume_min, min(symbol_info.volume_max, raw_lot))
        lot = round(lot / symbol_info.volume_step) * symbol_info.volume_step
        
        return round(lot, 2)
