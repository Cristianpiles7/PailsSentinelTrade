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
        
        # Previous values for Crossover Check
        prev_upper_bb = bb[bbu_col].iloc[-2]
        prev_lower_bb = bb[bbl_col].iloc[-2]
        prev_close = df['close'].iloc[-2]
        
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
        # Modified to prioritize EVENT (Fresh Breakout) over STATE (Outside Bands)
        breakout = 0 # 0, 1 (bull), -1 (bear)
        is_fresh_breakout = False
        
        if close > upper_bb:
             score += 30
             # Check for FRESH breakout (Event)
             if prev_close <= prev_upper_bb:
                 score += 10 # Bonus for freshness
                 is_fresh_breakout = True
                 breakdown["Breakout"] = "FRESH BULL BREAK (+40)"
             else:
                 breakdown["Breakout"] = "Above Bands (+30)"
             breakout = 1
             
        elif close < lower_bb:
             score += 30
             # Check for FRESH breakout (Event)
             if prev_close >= prev_lower_bb:
                 score += 10
                 is_fresh_breakout = True
                 breakdown["Breakout"] = "FRESH BEAR BREAK (+40)"
             else:
                 breakdown["Breakout"] = "Below Bands (+30)"
             breakout = -1
        else:
            breakdown["Breakout"] = "Inside Bands (0)"

        # --- SIGNAL GENERATION ---
        entry = 0
        
        # REGLA MAESTRA (STRICT):
        # 1. Breakout activo (1 o -1)
        # 2. ADX > 25 (Tendencia real)
        # 3. GATILLO: Debe ser una ruptura FRESCA (Event) O un score altísimo (>85) 
        
        if breakout == 1:
            if adx > 25 and rsi < 75:
                if is_fresh_breakout or score >= 85:
                    entry = 1
        elif breakout == -1:
            if adx > 25 and rsi > 25:
                 if is_fresh_breakout or score >= 85:
                    entry = -1

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
