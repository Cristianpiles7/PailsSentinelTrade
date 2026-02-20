import logging
import MetaTrader5 as mt5
from typing import Dict, List

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
        import sqlite3
        closed_pnl = 0.0
        try:
            # Usamos la conexión directa o el método de la clase si existiera
            conn = sqlite3.connect("PST_Core/data/pst_trading.db")
            cursor = conn.cursor()
            cursor.execute("SELECT SUM(profit) FROM trades WHERE time_out >= ?", (today_start,))
            row = cursor.fetchone()
            closed_pnl = row[0] if row and row[0] else 0.0
            conn.close()
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
        
        await self.db.save_config(lock_key, "true")
        logger.error(f"🔒 [BLOQUEO] Operativa cerrada por el resto de la sesión ({today_str}).")

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
        # 0. Evitar duplicados por Símbolo (Filtro Estricto y Robusto)
        target_sym = symbol.upper().strip()
        
        if any(s.upper().strip() == target_sym for s in self.in_flight_trades):
            logger.warning(f"🛡️ Bloqueando entrada in-flight para {symbol}. Orden recientemente enviada.")
            return False

        if current_positions:
            for pos in current_positions:
                pos_sym = pos.symbol.upper().strip()
                # Coincidencia exacta o parcial (ej: EURUSD vs EURUSD.cash)
                if pos_sym == target_sym or target_sym in pos_sym or pos_sym in target_sym:
                    logger.debug(f"🛡️ Bloqueando entrada duplicada para {symbol}. Posición '{pos.symbol}' detectada.")
                    return False

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

    def calculate_lot_size(self, balance, risk_per_trade_pct, stop_loss_points, symbol_info, current_atr=None, ma_atr=None, open_positions_count=0):
        """
        Calcula el lotaje usando Lógica Híbrida de Riesgo Dinámico y Cubetas de Margen (FTMO Rules).
        """
        if stop_loss_points <= 0 or symbol_info is None:
            return symbol_info.volume_min if symbol_info else 0.01

        symbol = symbol_info.name
        
        # --- 1. DYNAMIC RISK SCALING (ARRIESGAR MENOS SI HAY EXPOSICIÓN) ---
        # Si ya hay trades abiertos, reducimos el riesgo base para diversificar
        effective_risk = risk_per_trade_pct
        if open_positions_count >= 2:
            effective_risk *= 0.70 # Reducimos al 70% del riesgo (ej: 0.50 -> 0.35)
        if open_positions_count >= 5:
            effective_risk *= 0.50 # Reducimos al 50% (ej: 0.50 -> 0.25)
            
        # --- 2. VOLATILITY ADJUSTMENT ---
        vol_factor = 1.0
        if current_atr and ma_atr and ma_atr > 0:
             vol_factor = ma_atr / current_atr
             effective_risk *= vol_factor
             
        # Cap de seguridad final tras escalado y volatilidad (Garantizar NO superar el límite del usuario)
        effective_risk = max(0.05, min(risk_per_trade_pct, effective_risk))
        
        risk_money = balance * (effective_risk / 100)
        tick_value = symbol_info.trade_tick_value
        
        if tick_value == 0:
            return symbol_info.volume_min

        # Lote Inicial basado en Riesgo (Stop Loss)
        raw_lot = risk_money / (stop_loss_points * tick_value) if stop_loss_points > 0 else symbol_info.volume_min
        
        # --- 3. MARGIN-BUCKET CAP (DYNAMICAL LEVERAGE TIERS) ---
        leverage, margin_bucket, asset_type = self._get_leverage_and_bucket(symbol)
        
        # Cálculo de Margen Requerido para el lote calculado
        price = symbol_info.ask if symbol_info.ask > 0 else symbol_info.last
        contract_size = symbol_info.trade_contract_size if symbol_info.trade_contract_size > 0 else 1
        
        # Fórmula: Margin = (Lots * ContractSize * Price) / Leverage
        required_margin = (raw_lot * contract_size * price) / leverage if leverage > 0 else 0
        
        logger.info(f"⚖️ [LOTES] {symbol} ({asset_type}) | Riesgo {effective_risk:.2f}% | Margen Req: {required_margin:.2f}€ (Lev 1:{leverage})")

        # Si el margen requerido supera el bucket, recortamos el lotaje
        if required_margin > margin_bucket:
            raw_lot = (margin_bucket * leverage) / (contract_size * price)
            logger.warning(f"✂️ [MARGIN CAP] {symbol} superó cubeta de {margin_bucket}€. Recortando lotaje.")

        # Ajustes finales del Broker
        lot = max(symbol_info.volume_min, min(symbol_info.volume_max, raw_lot))
        lot = round(lot / symbol_info.volume_step) * symbol_info.volume_step
        
        logger.info(f"✅ Lot Final para {symbol}: {round(lot, 2)}")
        return round(lot, 2)
