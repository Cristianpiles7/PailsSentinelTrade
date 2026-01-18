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

    def calculate_lot_size(self, balance, risk_per_trade_pct, stop_loss_points, symbol_info):
        """Calcula el lotaje basado en el riesgo monetario."""
        if stop_loss_points <= 0 or symbol_info is None:
            return symbol_info.volume_min if symbol_info else 0.01

        risk_money = balance * (risk_per_trade_pct / 100)
        # Valor de un punto en la moneda del depósito
        point_value = symbol_info.trade_tick_value / symbol_info.trade_tick_size
        
        raw_lot = risk_money / (stop_loss_points * point_value)
        
        # Ajustar a límites del broker
        lot = max(symbol_info.volume_min, min(symbol_info.volume_max, raw_lot))
        lot = round(lot / symbol_info.volume_step) * symbol_info.volume_step
        
        return round(lot, 2)
