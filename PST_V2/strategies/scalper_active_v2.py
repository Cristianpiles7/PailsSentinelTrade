import logging
import pandas as pd
import pandas_ta as ta
from ..core.base_strategy import BaseStrategyV2
from ..models.events import TickEvent
from ..utils.data_store import store

logger = logging.getLogger("PST_V2.ScalperActive")

class ScalperActiveV2(BaseStrategyV2):
    """
    Versión V2 (Event-Driven) de PST-Scalper-Active.
    Escucha ticks y evalúa roturas de EMA con filtros institucionales.
    """
    
    def __init__(self, symbols: list):
        super().__init__("PST-Scalper-Active", symbols)
        self.ema_period = 21
        self.profiles = {
            "CRYPTO": {"vol": 1.1, "atr_sl": 2.0, "hyst": 0.45},
            "INDEX": {"vol": 1.3, "atr_sl": 3.0, "hyst": 0.25},
            "FOREX": {"vol": 1.2, "atr_sl": 2.5, "hyst": 0.20}
        }

    async def calculate_signal(self, tick: TickEvent):
        # 1. Obtener datos históricos necesarios
        df_m1 = await store.get_data(tick.symbol, 1, 100)
        df_m15 = await store.get_data(tick.symbol, 15, 50)
        
        if df_m1 is None or df_m15 is None:
            return None

        # 2. Análisis Técnico
        # EMA y ATR
        df_m1['ema21'] = ta.ema(df_m1['close'], length=self.ema_period)
        df_m1['atr'] = ta.atr(df_m1['high'], df_m1['low'], df_m1['close'], length=14)
        
        c_price = tick.bid # Usamos el precio del tick real
        c_ema21 = df_m1['ema21'].iloc[-1]
        c_atr = df_m1['atr'].iloc[-1]
        
        # 3. Clasificación de Activo
        # (Uso simplificado para el ejemplo V2, integraremos tech_utils después)
        profile = self.profiles["FOREX"]
        if "USD" not in tick.symbol: profile = self.profiles["CRYPTO"]
        
        # 4. Lógica de Rotura (Breakout)
        hysteresis = c_atr * profile['hyst']
        c_open = df_m1['open'].iloc[-1]
        body_size = abs(c_price - c_open)
        
        # Filtros de Mecha (Wick Filter)
        upper_wick = df_m1['high'].iloc[-1] - max(c_open, c_price)
        lower_wick = min(c_open, c_price) - df_m1['low'].iloc[-1]
        
        is_ignition_bull = (c_price > c_open) and (body_size > c_atr * 0.35) and (upper_wick < body_size)
        is_ignition_bear = (c_price < c_open) and (body_size > c_atr * 0.35) and (lower_wick < body_size)

        # 5. Determinación de Señal
        # 5. Determinación de Puntuación de Radar (Live Pulse)
        # Calculamos una intensidad basada en la cercanía a la EMA y el momentum
        dist_ema = c_price - c_ema21
        intensity = min(100, abs(dist_ema / (c_atr + 0.0001)) * 50)
        radar_score = round(intensity, 1)
        radar_dir = "BUY" if dist_ema > 0 else "SELL"

        # 6. Lógica de Señal (Trigger)
        score = radar_score
        sig_type = "NEUTRAL"
        
        if is_ignition_bull and c_price > (c_ema21 + hysteresis):
            score = 80
            sig_type = "BUY"
        elif is_ignition_bear and c_price < (c_ema21 - hysteresis):
            score = 80
            sig_type = "SELL"
            
        return {
            "signal_type": sig_type,
            "score": score,
            "sl": c_price - (c_atr * profile['atr_sl']) if sig_type == "BUY" else c_price + (c_atr * profile['atr_sl']),
            "metadata": {"mode": "EVENT_V2_BREAKOUT", "radar_dir": radar_dir}
        }
