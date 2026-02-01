import pandas_ta as ta
import numpy as np

class RegimeMode:
    TREND = "TREND"
    RANGE = "RANGE"
    RANGING = "RANGE" # Alias para compatibilidad
    VOLATILE = "VOLATILE"  # Alta volatilidad pero sin dirección (Caos)
    UNKNOWN = "UNKNOWN"

class RegimeClassifier:
    def __init__(self, adx_threshold=25, volatility_window=20):
        self.adx_threshold = adx_threshold
        self.vol_window = volatility_window

    def classify(self, df):
        if df is None or len(df) < 50:
            return RegimeMode.UNKNOWN, 0.0

        # 1. Fuerza de Tendencia (ADX)
        adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
        if adx_df is None or 'ADX_14' not in adx_df.columns:
            return RegimeMode.UNKNOWN, 0.0
            
        current_adx = adx_df['ADX_14'].iloc[-1] if 'ADX_14' in adx_df.columns else 0
        
        # 2. Volatilidad Relativa (ATR vs Media de ATR)
        atr = ta.atr(df['high'], df['low'], df['close'], length=14)
        if atr is None or len(atr) < self.vol_window:
            return RegimeMode.UNKNOWN, current_adx
            
        current_vol = atr.iloc[-1]
        mean_vol = atr.rolling(window=self.vol_window).mean().iloc[-1]
        
        # Factor de volatilidad (1.0 = normal, >1.5 = alta)
        vol_factor = current_vol / mean_vol if mean_vol > 0 else 1.0

        # --- LÓGICA DE CLASIFICACIÓN ---
        
        # CASO A: TENDENCIA (ADX alto y volatilidad controlada)
        if current_adx >= self.adx_threshold:
            if vol_factor < 2.0:
                return RegimeMode.TREND, current_adx
            else:
                return RegimeMode.VOLATILE, current_adx # Demasiado explosivo/peligroso

        # CASO B: RANGO (ADX bajo y volatilidad baja/normal)
        if current_adx < 20: 
            if vol_factor < 1.3:
                return RegimeMode.RANGE, current_adx
            else:
                return RegimeMode.VOLATILE, current_adx # Movimientos ruidosos sin dirección

        # CASO C: TRANSICIÓN O INCIDUMBRE
        return RegimeMode.VOLATILE, current_adx
