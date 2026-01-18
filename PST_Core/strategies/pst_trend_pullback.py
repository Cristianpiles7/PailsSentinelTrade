import pandas_ta as ta
import numpy as np
from ..models.classifier import RegimeMode

class PSTTrendPullback:
    STRATEGY_NAME = "PST-Trend-Pullback"
    STRATEGY_TYPE = RegimeMode.TREND
    WEIGHT = 1.3

    async def calculate_signal(self, data_input, current_regime, user_levels=None):
        # Adaptador MTF
        if isinstance(data_input, dict):
            df = data_input.get('m15')
        else:
            df = data_input

        # 1. Filtro: SOLO opera en régimen TREND
        if current_regime != RegimeMode.TREND:
            return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        if df is None or len(df) < 50:
            return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        # 2. Indicadores
        ema21 = ta.ema(df['close'], length=21)
        ema50 = ta.ema(df['close'], length=50)
        ema200 = ta.ema(df['close'], length=200)
        rsi = ta.rsi(df['close'], length=14)
        atr_series = ta.atr(df['high'], df['low'], df['close'], length=14)
        
        if ema21 is None or rsi is None: return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        # Valores actuales
        c_now = df['close'].iloc[-1]
        e21 = ema21.iloc[-1]
        e50 = ema50.iloc[-1]
        e200 = ema200.iloc[-1] if ema200 is not None else 0
        r_now = rsi.iloc[-1]
        atr = atr_series.iloc[-1] if atr_series is not None else 0
        
        # --- SCORING ENGINE (Max 100) ---
        score = 0
        breakdown = {}
        
        # A. Trend Structure (Max 40)
        bull_trend = (e21 > e50) and (e50 > e200 if e200 > 0 else True)
        bear_trend = (e21 < e50) and (e50 < e200 if e200 > 0 else True)
        
        trend_dir = 0 # 1 Bull, -1 Bear
        
        if bull_trend:
            score += 40
            trend_dir = 1
            breakdown["Trend Structure"] = "Perfect Bull (+40)"
        elif bear_trend:
            score += 40
            trend_dir = -1
            breakdown["Trend Structure"] = "Perfect Bear (+40)"
        else:
            # Partial trend points
            if (e21 > e50) or (e21 < e50): # Simple alignment
                score += 20
                trend_dir = 1 if e21 > e50 else -1
                breakdown["Trend Structure"] = "Weak/Messy (+20)"
            else:
                 breakdown["Trend Structure"] = "None (0)"

        # B. Value Zone (Max 30) (Proximity to EMA21)
        dist_ema21 = abs(c_now - e21) / e21 * 100
        in_value_zone = dist_ema21 < 0.5
        
        if in_value_zone:
            score += 30
            breakdown["Value Zone"] = "In Zone (+30)"
        elif dist_ema21 < 1.0:
            score += 15
            breakdown["Value Zone"] = "Near Zone (+15)"
        else:
            breakdown["Value Zone"] = "Far (0)"
            
        # C. RSI Pullback (Max 30)
        # Bull logic: RSI oversold in trend (< 45)
        # Bear logic: RSI overbought in trend (> 55)
        if trend_dir == 1:
            if r_now < 45:
                score += 30
                breakdown["RSI Pullback"] = "Optimal (<45) (+30)"
            elif r_now < 55:
                score += 15
                breakdown["RSI Pullback"] = "Moderate (<55) (+15)"
            else:
                breakdown["RSI Pullback"] = "No Pullback (0)"
        elif trend_dir == -1:
             if r_now > 55:
                score += 30
                breakdown["RSI Pullback"] = "Optimal (>55) (+30)"
             elif r_now > 45:
                score += 15
                breakdown["RSI Pullback"] = "Moderate (>45) (+15)"
             else:
                breakdown["RSI Pullback"] = "No Pullback (0)"

        # --- SIGNAL GENERATION ---
        entry = 0
        
        # Entry if Score >= 70 (Strong Trend + Zone OR Pullback)
        if score >= 70:
            if trend_dir == 1:
                entry = 1
            elif trend_dir == -1:
                entry = -1

        metadata = {
            "regime": "TREND",
            "total_score": score,
            "score_breakdown": breakdown,
            "rsi": round(r_now, 1),
            "zone": "YES" if in_value_zone else "NO"
        }

        return {
            "entry": entry,
            "atr": atr,
            "metadata": metadata,
            "score": score
        }
