import pandas_ta as ta
import numpy as np
from ..models.classifier import RegimeMode

class PSTVolatilityBreakout:
    STRATEGY_NAME = "PST-Vol-Breakout"
    STRATEGY_TYPE = RegimeMode.VOLATILE
    WEIGHT = 1.5

    async def calculate_signal(self, data_input, current_regime, user_levels=None):
        # Adaptador MTF
        if isinstance(data_input, dict):
            df = data_input.get('m15')
        else:
            df = data_input

        # 1. Filtro: SOLO opera en régimen VOLATILE
        if current_regime != RegimeMode.VOLATILE:
            return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        if df is None or len(df) < 50:
            return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        # 2. Indicadores
        bb = ta.bbands(df['close'], length=20, std=2.0)
        atr_df = ta.atr(df['high'], df['low'], df['close'], length=14)
        atr = atr_df.iloc[-1] if atr_df is not None else 0
        
        adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
        adx = adx_df['ADX_14'].iloc[-1] if adx_df is not None else 0
        
        rsi_df = ta.rsi(df['close'], length=14)
        rsi = rsi_df.iloc[-1] if rsi_df is not None else 50
        
        close = df['close'].iloc[-1]
        
        # Extract Bands safely
        cols = bb.columns
        bbu_col = next((c for c in cols if c.startswith('BBU')), cols[2])
        bbl_col = next((c for c in cols if c.startswith('BBL')), cols[0])
        upper_bb = bb[bbu_col].iloc[-1]
        lower_bb = bb[bbl_col].iloc[-1]
        
        # Squeeze Detection logic
        bandwidth = (upper_bb - lower_bb) / df['close'].iloc[-1]
        is_squeezing = bandwidth < 0.10
        
        # --- SCORING ENGINE (Max 100) ---
        score = 0
        breakdown = {}
        
        # A. ADX Strength (Max 40)
        if adx > 40:
            score += 40
            breakdown["ADX Power"] = "Super Trend (+40)"
        elif adx > 25:
            score += 20
            breakdown["ADX Power"] = "Trends (+20)"
        else:
            breakdown["ADX Power"] = "Weak (0)"
            
        # B. Squeeze / Expansion (Max 20)
        # We reward squeeze (provisional potential) OR high volatility active
        if is_squeezing:
            score += 20
            breakdown["Squeeze"] = "Compressed (+20)"
        else:
            breakdown["Squeeze"] = "Normal (0)"
            
        # C. Breakout Status (Max 40)
        breakout = 0 # 0, 1 (bull), -1 (bear)
        if close > upper_bb:
            score += 40
            breakout = 1
            breakdown["Breakout"] = "BULL Break (+40)"
        elif close < lower_bb:
            score += 40
            breakout = -1
            breakdown["Breakout"] = "BEAR Break (+40)"
        else:
            breakdown["Breakout"] = "Inside Bands (0)"

        # --- SIGNAL GENERATION ---
        entry = 0
        
        # Requires Breakdown + ADX support
        if breakout == 1:
            if adx > 25 and rsi < 75:
                entry = 1
        elif breakout == -1:
            if adx > 25 and rsi > 25:
                entry = -1
        
        # Force entry if Score is very high (strong breakout + strong adx)
        if score >= 80 and entry == 0:
             if close > upper_bb: entry = 1
             elif close < lower_bb: entry = -1

        metadata = {
            "regime": "VOLATILE",
            "total_score": score,
            "score_breakdown": breakdown,
            "adx": round(adx, 1),
            "squeeze": "YES" if is_squeezing else "NO"
        }

        return {
            "entry": entry,
            "atr": atr,
            "metadata": metadata,
            "score": score
        }
