import pandas_ta as ta
import numpy as np
from ..models.classifier import RegimeMode

class PSTRangeReversion:
    STRATEGY_NAME = "PST-Range-Sniper"
    STRATEGY_TYPE = RegimeMode.RANGE
    WEIGHT = 1.2

    async def calculate_signal(self, data_input, current_regime, user_levels=None):
        # Adaptador MTF: Extraemos M15 para la lógica principal
        if isinstance(data_input, dict):
            df = data_input.get('m15')
        else:
            df = data_input

        # 1. Filtro de Régimen
        if current_regime != RegimeMode.RANGE:
            return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        if df is None or len(df) < 50:
            return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        # 2. Key Indicators
        bb = ta.bbands(df['close'], length=20, std=2.0)
        atr = ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-1]
        rsi = ta.rsi(df['close'], length=14).iloc[-1]
        
        # Extract Bands safely
        cols = bb.columns
        bbu_col = next((c for c in cols if c.startswith('BBU')), cols[2])
        bbl_col = next((c for c in cols if c.startswith('BBL')), cols[0])
        upper_band = bb[bbu_col].iloc[-1]
        lower_band = bb[bbl_col].iloc[-1]
        
        c = df['close'].iloc[-1]
        o = df['open'].iloc[-1]
        h = df['high'].iloc[-1]
        l = df['low'].iloc[-1]
        
        # --- SCORING ENGINE (Max 100) ---
        score = 0
        breakdown = {}
        
        # A. RSI Extreme (Max 30)
        # Oversold in Range = BUY potential | Overbought in Range = SELL potential
        if rsi < 30 or rsi > 70:
            score += 30
            breakdown["RSI Extreme"] = "Strong (+30)"
        elif rsi < 40 or rsi > 60:
            score += 15
            breakdown["RSI Zone"] = "Moderate (+15)"
        else:
            breakdown["RSI Zone"] = "Neutral (0)"
            
        # B. Bollinger Interaction (Max 30)
        dist_low = abs(c - lower_band)
        dist_high = abs(c - upper_band)
        range_span = upper_band - lower_band
        # Touching or very close (within 5% of width)
        touching_low = (c <= lower_band) or ((c - lower_band) < range_span * 0.05)
        touching_high = (c >= upper_band) or ((upper_band - c) < range_span * 0.05)
        
        if touching_low or touching_high:
            score += 30
            breakdown["Bollinger Touch"] = "Yes (+30)"
        else:
            breakdown["Bollinger Touch"] = "No (0)"
            
        # C. Candle Pattern (Pinbar) (Max 40)
        body = abs(c - o)
        lower_wick = min(c, o) - l
        upper_wick = h - max(c, o)
        
        is_bull_pin = (lower_wick > body * 1.5) and (lower_wick > upper_wick)
        is_bear_pin = (upper_wick > body * 1.5) and (upper_wick > lower_wick)
        
        if is_bull_pin or is_bear_pin:
            score += 40
            breakdown["Candle Pattern"] = "Pinbar (+40)"
        else:
             breakdown["Candle Pattern"] = "None (0)"

        # --- SIGNAL GENERATION ---
        entry = 0
        # Buy: Score High + Logic Match
        if score >= 50:
            if (touching_low or rsi < 40) and (is_bull_pin or score >= 70):
                entry = 1
            elif (touching_high or rsi > 60) and (is_bear_pin or score >= 70):
                entry = -1

        metadata = {
            "regime": "RANGE",
            "total_score": score,
            "score_breakdown": breakdown,
            "rsi": round(rsi, 1),
            "pattern": "PINBAR" if (is_bull_pin or is_bear_pin) else "NONE"
        }

        return {
            "entry": entry,
            "atr": atr,
            "metadata": metadata,
            "score": score
        }
