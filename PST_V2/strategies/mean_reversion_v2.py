import logging
import pandas as pd
import pandas_ta as ta
from ..core.base_strategy import BaseStrategyV2
from ..models.events import TickEvent
from ..utils.data_store import store

logger = logging.getLogger("PST_V2.MeanReversion")

class MeanReversionV2(BaseStrategyV2):
    """
    Versión V2 (Event-Driven) de PST-Mean-Reversion (Rubber-Band).
    Estrategia de contra-tendencia basada en bandas de bollinger y RSI.
    """
    
    def __init__(self, symbols: list):
        super().__init__("PST-Mean-Reversion", symbols)
        self.bb_length = 20
        self.bb_std = 2.0
        self.rsi_length = 14

    async def calculate_signal(self, tick: TickEvent):
        # 1. Obtener datos
        df_m5 = await store.get_data(tick.symbol, 5, 100)
        if df_m5 is None or len(df_m5) < 30:
            return None

        # 2. Análisis Técnico
        # Bandas de Bollinger
        bbands = ta.bbands(df_m5['close'], length=self.bb_length, std=self.bb_std)
        if bbands is None or bbands.empty: return None
        
        # Columna nombres varían por versión de pandas_ta
        col_u = f"BBU_{self.bb_length}_{self.bb_std:.1f}"
        col_l = f"BBL_{self.bb_length}_{self.bb_std:.1f}"
        
        upper_band = bbands[col_u].iloc[-1]
        lower_band = bbands[col_l].iloc[-1]
        
        # RSI
        rsi_series = ta.rsi(df_m5['close'], length=self.rsi_length)
        c_rsi = rsi_series.iloc[-1]
        
        # Filtro de Tendencia (EMA 200) - No operar contra tendencia fuerte
        ema200 = ta.ema(df_m5['close'], length=200)
        c_ema200 = ema200.iloc[-1] if ema200 is not None else tick.bid
        
        c_price = tick.bid
        score = 0
        sig_type = "NEUTRAL"
        
        # 3. Lógica Mean-Reversion
        # COMPRA: Precio por debajo de banda inferior + RSI sobrevendido
        if c_price < lower_band and c_rsi < 30:
            # Filtro defensivo: Si estamos muy por debajo de EMA200, es tendencia bajista fuerte, evitar.
            if c_price > c_ema200 * 0.98: # Permitir rebote si no es caída libre
                score = 75
                sig_type = "BUY"
                
        # VENTA: Precio por encima de banda superior + RSI sobrecomprado
        elif c_price > upper_band and c_rsi > 70:
            if c_price < c_ema200 * 1.02:
                score = 75
                sig_type = "SELL"

        if sig_type != "NEUTRAL":
            atr = ta.atr(df_m5['high'], df_m5['low'], df_m5['close'], length=14).iloc[-1]
            return {
                "signal_type": sig_type,
                "score": score,
                "sl": c_price - (atr * 2.0) if sig_type == "BUY" else c_price + (atr * 2.0),
                "tp": c_price + (atr * 3.0) if sig_type == "BUY" else c_price - (atr * 3.0),
                "metadata": {
                    "mode": "EVENT_V2_REVERSION",
                    "rsi": round(c_rsi, 1)
                }
            }
            
        return {"signal_type": "NEUTRAL"}
