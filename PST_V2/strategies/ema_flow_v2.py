import logging
import pandas as pd
import pandas_ta as ta
from ..core.base_strategy import BaseStrategyV2
from ..models.events import TickEvent
from ..utils.data_store import store

logger = logging.getLogger("PST_V2.EMAFlow")

class EMAFlowV2(BaseStrategyV2):
    """
    Versión V2 (Event-Driven) de PST-EMA-Flow.
    Estrategia seguidora de tendencia robusta con filtros multitemporales (M5/M15).
    """
    
    def __init__(self, symbols: list):
        super().__init__("PST-EMA-Flow", symbols)
        self.ema_short = 21
        self.ema_long = 50

    async def calculate_signal(self, tick: TickEvent):
        # 1. Obtener datos históricos
        df_m5 = await store.get_data(tick.symbol, 5, 100)
        df_m15 = await store.get_data(tick.symbol, 15, 100)
        
        if df_m5 is None or df_m15 is None or len(df_m5) < 55:
            return None

        # 2. Análisis Técnico M5
        df_m5['ema21'] = ta.ema(df_m5['close'], length=21)
        df_m5['ema50'] = ta.ema(df_m5['close'], length=50)
        df_m5['adx'] = ta.adx(df_m5['high'], df_m5['low'], df_m5['close'], length=14).iloc[:, 0] # ADX_14
        df_m5['atr'] = ta.atr(df_m5['high'], df_m5['low'], df_m5['close'], length=14)
        
        # 3. Análisis Técnico M15 (Contexto)
        df_m15['ema21'] = ta.ema(df_m15['close'], length=21)
        m15_ema21 = df_m15['ema21'].iloc[-1]
        m15_close = df_m15['close'].iloc[-1]
        m15_trend = 1 if m15_close > m15_ema21 else -1

        # 4. Datos Actuales
        c_price = tick.bid
        c_ema21 = df_m5['ema21'].iloc[-1]
        c_ema50 = df_m5['ema50'].iloc[-1]
        c_adx = df_m5['adx'].iloc[-1]
        c_atr = df_m5['atr'].iloc[-1]
        
        # 5. Puntuación de Radar (Live Pulse)
        score_radar = 0.0
        if c_ema21 > c_ema50 and m15_trend == 1:
            score_radar = min(75, 20 + c_adx) # Hasta 75% por tendencia pura
        elif c_ema21 < c_ema50 and m15_trend == -1:
            score_radar = min(75, 20 + c_adx)

        # 6. Condición de Entrada (Trigger)
        score = score_radar
        sig_type = "NEUTRAL"
        
        # Filtro de Fuerza (ADX) para señales reales
        if c_adx >= 20: 
            # Cruce o Rebote Alcista
            if c_price > c_ema21 > c_ema50 and m15_trend == 1:
                # Confirmación de vela de ignición
                c_open = df_m5['open'].iloc[-1]
                if c_price > c_open and (c_price - c_open) > (c_atr * 0.3):
                    score = 85
                    sig_type = "BUY"
                    
            # Cruce o Rebote Bajista
            elif c_price < c_ema21 < c_ema50 and m15_trend == -1:
                c_open = df_m5['open'].iloc[-1]
                if c_price < c_open and (c_open - c_price) > (c_atr * 0.3):
                    score = 85
                    sig_type = "SELL"

        # Calcular SL/TP preventivo para el radar
        sl_dist = max(c_atr * 2.5, abs(c_price - c_ema50) * 1.2)
        
        return {
            "signal_type": sig_type,
            "score": score,
            "sl": c_price - sl_dist if sig_type == "BUY" else c_price + sl_dist,
            "tp": c_price + (sl_dist * 2.0) if sig_type == "BUY" else c_price - (sl_dist * 2.0),
            "metadata": {
                "mode": "EVENT_V2_TREND",
                "m15_align": (m15_trend == (1 if sig_type == "BUY" else -1 if sig_type == "SELL" else m15_trend)),
                "adx": round(c_adx, 1)
            }
        }
