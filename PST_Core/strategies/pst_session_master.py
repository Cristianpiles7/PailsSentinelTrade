import pandas_ta as ta
import numpy as np
from ..models.classifier import RegimeMode
from datetime import datetime, time

class PSTSessionMaster:
    STRATEGY_NAME = "PST-Session-Master"
    STRATEGY_TYPE = RegimeMode.VOLATILE # Fits well with Volatile/Breakout regimes
    WEIGHT = 2.0 # High priority as it is time-specific

    async def calculate_signal(self, data_input, current_regime, user_levels=None):
        # Adaptador MTF: Preferiblemente M5 para granularidad
        if isinstance(data_input, dict):
            df = data_input.get('m5')
            # Fallback a M15 si M5 es muy corto
            if df is None or len(df) < 20: 
                df = data_input.get('m15')
        else:
            df = data_input

        # 1. Filtro: Se puede ejecutar en TREND o VOLATILE. En Range no tiene sentido operar rupturas.
        if current_regime == RegimeMode.RANGE:
             return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        if df is None or len(df) < 50:
            return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        # 2. Setup de Tiempo (Detectar Aperturas)
        # Asumimos que el DF tiene index datetime o columna time
        # TODO: Ajustar zona horaria si servidor no es UTC. Asumimos UTC/Server time.
        
        last_time = df.index[-1]
        current_h = last_time.hour
        current_m = last_time.minute
        
        # Definir Ventanas de Apertura (Margin: +/- 2 horas desde open)
        # Europe (London): 08:00 - 10:00 (Aprox)
        # US (NY): 14:30 - 16:30 (Aprox) - Asumiendo 15:30 apertura stock, 13:30 futuros
        
        is_london_open = 8 <= current_h < 11
        is_ny_open = 14 <= current_h < 17
        
        # Si NO estamos en ventana de sesión, score 0 (o muy bajo)
        if not (is_london_open or is_ny_open):
             # Fuera de horas clave
             return {"entry": 0, "atr": 0, "metadata": {"status": "Fuera de Horario (ORB)"}, "score": 0}

        # 3. Calcular Rango de Apertura (Opening Range)
        # Simplificación: Tomamos las últimas N velas como proxy del rango reciente si estamos en la hora H
        # Ideal: High/Low de la primera hora.
        # Aproximación robusta: High/Low de la ultima hora (12 velas M5)
        
        lookback = 12 # 1 hora
        range_high = df['high'].iloc[-lookback-1:-1].max()
        range_low = df['low'].iloc[-lookback-1:-1].min()
        
        c = df['close'].iloc[-1]
        v = df['tick_volume'].iloc[-1] if 'tick_volume' in df else 0
        v_ma = df['tick_volume'].rolling(20).mean().iloc[-1] if 'tick_volume' in df else 1
        
        atr = ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-1]
        
        # --- SCORING ENGINE (Max 100) ---
        score = 0
        breakdown = {}
        
        # A. Breakout Check (Max 50)
        breakout_dir = 0
        if c > range_high:
            score += 50
            breakout_dir = 1
            breakdown["ORB Status"] = "Bull Break (+50)"
        elif c < range_low:
            score += 50
            breakout_dir = -1
            breakdown["ORB Status"] = "Bear Break (+50)"
        else:
            # Inside Range
            dist_h = (range_high - c) / atr if atr > 0 else 0
            dist_l = (c - range_low) / atr if atr > 0 else 0
            
            if dist_h < 1.0 or dist_l < 1.0:
                 breakdown["ORB Status"] = "Testing Level... (0)"
            else:
                 breakdown["ORB Status"] = "Inside Range (0)"
        
        # B. Volume Confirmation (Max 30)
        if breakout_dir != 0:
            if v > v_ma * 1.5:
                score += 30
                breakdown["Volume"] = "Explosive (+30)"
            elif v > v_ma:
                score += 15
                breakdown["Volume"] = "High (+15)"
            else:
                breakdown["Volume"] = "Low (0)"
        
        # C. Trend Alignment (Max 20)
        ema50 = ta.ema(df['close'], length=50).iloc[-1]
        if breakout_dir == 1 and c > ema50:
            score += 20
            breakdown["Trend Align"] = "Bullish (+20)"
        elif breakout_dir == -1 and c < ema50:
            score += 20
            breakdown["Trend Align"] = "Bearish (+20)"
        else:
            breakdown["Trend Align"] = "Contrarian (0)"

        # --- SIGNAL GENERATION ---
        entry = 0
        # High conviction breakout
        if score >= 70:
            entry = breakout_dir

        metadata = {
            "regime": "VOLATILE",
            "total_score": score,
            "score_breakdown": breakdown,
            "range_top": round(range_high, 2),
            "range_bot": round(range_low, 2)
        }

        return {
            "entry": entry,
            "atr": atr,
            "metadata": metadata,
            "score": score
        }
