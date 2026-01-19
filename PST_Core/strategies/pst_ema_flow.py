import pandas_ta as ta
import pandas as pd
import numpy as np
import logging
from ..models.classifier import RegimeMode

logger = logging.getLogger("PST-EMA-Flow")

class PSTEMAFlow:
    STRATEGY_NAME = "PST-EMA-Flow"
    STRATEGY_TYPE = RegimeMode.TREND 
    WEIGHT = 1.2 # Estrategia robusta, peso ligeramente mayor

    async def calculate_signal(self, data_input, current_regime, user_levels=None):
        # 1. Adaptador de Datos (M5 + M15)
        df = None
        df_m15 = None
        if isinstance(data_input, dict):
            df = data_input.get('m5')
            df_m15 = data_input.get('m15')
        else:
            df = data_input

        if df is None or len(df) < 55:
            return {
                "entry": 0, 
                "atr": 0, 
                "metadata": {
                    "strategy": self.STRATEGY_NAME,
                    "score": 0,
                    "score_breakdown": {"Status": "Waiting for Data (Len < 55)"}
                }, 
                "score": 0
            }

        # --- M15 CONTEXT CHECK (High Timeframe Filter) ---
        htf_filter = 0 # 0: Neutral, 1: Bull, -1: Bear
        if df_m15 is not None and len(df_m15) > 50:
             m15_ema21 = ta.ema(df_m15['close'], length=21).iloc[-1]
             m15_close = df_m15['close'].iloc[-1]
             if m15_close > m15_ema21: htf_filter = 1
             elif m15_close < m15_ema21: htf_filter = -1
        
        # 2. Indicadores M5 (Principales)
        ema21_s = ta.ema(df['close'], length=21)
        ema50_s = ta.ema(df['close'], length=50)
        rsi_s = ta.rsi(df['close'], length=14)
        adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
        
        # Volume
        vol_s = df['tick_volume'] if 'tick_volume' in df else pd.Series([0]*len(df))
        vol_ma_s = ta.sma(vol_s, length=20)

        # 3. NET SCORE ENGINE (Bull vs Bear)
        bull_score = 0
        bear_score = 0
        breakdown = {}
        last_event_idx = 99
        
        for i in range(3): 
            idx = -1 - i
            prev_idx = idx - 1
            
            # Data Points
            c_close = df['close'].iloc[idx]; c_open = df['open'].iloc[idx]
            c_ema21 = ema21_s.iloc[idx]; c_ema50 = ema50_s.iloc[idx]
            p_ema21 = ema21_s.iloc[prev_idx]; p_ema50 = ema50_s.iloc[prev_idx]
            p_close = df['close'].iloc[prev_idx]
            
            # DYNAMIC DECAY
            action_pts = 60 - (i * 10) # 60, 50, 40
            cross_pts = 20 - (i * 5)   # 20, 15, 10
            
            # A. CROSSOVER
            if p_ema21 <= p_ema50 and c_ema21 > c_ema50: # Golden
                bull_score += cross_pts
                breakdown[f"Golden Cross (-{i})"] = f"+{cross_pts} (Lag)"
            elif p_ema21 >= p_ema50 and c_ema21 < c_ema50: # Death
                bear_score += cross_pts
                breakdown[f"Death Cross (-{i})"] = f"+{cross_pts} (Lag)"
                
            # B. BREAKOUT (Primary)
            # Bull Break
            if p_close < p_ema50 and c_close > c_ema50:
                bull_score += action_pts
                breakdown[f"EMA50 Break (-{i})"] = f"+{action_pts}"
                if i < last_event_idx: last_event_idx = i
            # Bear Break
            elif p_close > p_ema50 and c_close < c_ema50:
                bear_score += action_pts
                breakdown[f"EMA50 Breakdown (-{i})"] = f"+{action_pts}"
                if i < last_event_idx: last_event_idx = i
                
            # C. BOUNCE (Primary)
            # Bull Bounce
            touched_21 = df['low'].iloc[idx] <= c_ema21
            is_green = c_close > c_open
            trend_up = c_ema21 > c_ema50
            if trend_up and touched_21 and is_green and c_close > c_ema21:
                bull_score += action_pts
                breakdown[f"Bull Bounce (-{i})"] = f"+{action_pts}"
                if i < last_event_idx: last_event_idx = i
            
            # Bear Bounce
            touched_21_bear = df['high'].iloc[idx] >= c_ema21
            is_red = c_close < c_open
            trend_down = c_ema21 < c_ema50
            if trend_down and touched_21_bear and is_red and c_close < c_ema21:
                bear_score += action_pts
                breakdown[f"Bear Bounce (-{i})"] = f"+{action_pts}"
                if i < last_event_idx: last_event_idx = i

        # --- CALCULATE NET SCORE ---
        net_score = 0
        direction = 0 # 1 Bull, -1 Bear
        
        if bull_score > bear_score:
            net_score = bull_score - bear_score
            direction = 1
            breakdown["Net Score"] = f"Bullish ({bull_score} - {bear_score})"
        elif bear_score > bull_score:
            net_score = bear_score - bull_score
            direction = -1
            breakdown["Net Score"] = f"Bearish ({bear_score} - {bull_score})"
        else:
            return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        # 2. HTF FILTER (M15)
        if direction == 1 and htf_filter == -1:
            net_score = 0; breakdown["Filter"] = "M15 Bearish (Block)"
        elif direction == -1 and htf_filter == 1:
            net_score = 0; breakdown["Filter"] = "M15 Bullish (Block)"
        elif htf_filter != 0:
            net_score += 10
            breakdown["M15"] = "Aligned (+10)"

        if net_score < 10: return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        # 3. FRESHNESS BONUS
        if last_event_idx == 0: net_score += 10 # Immediate
        
        # 4. INDICATORS (Current Candle)
        curr_rsi = rsi_s.iloc[-1]
        curr_adx = adx_df['ADX_14'].iloc[-1] if adx_df is not None else 0
        curr_vol = vol_s.iloc[-1] if 'tick_volume' in df else 0
        curr_vol_ma = vol_ma_s.iloc[-1] if vol_ma_s is not None else 1
        
        # Volume
        if curr_vol > curr_vol_ma: net_score += 10; breakdown["Vol"] = "High (+10)"
        # ADX
        if curr_adx > 20: net_score += 10; breakdown["ADX"] = f"Trend (+10)"
        
        # RSI Check
        if direction == 1:
             if 35 < curr_rsi < 70: net_score += 10; breakdown["RSI"] = "OK (+10)"
        elif direction == -1:
             if 30 < curr_rsi < 65: net_score += 10; breakdown["RSI"] = "OK (+10)"

        # --- FINAL DECISION ---
        score = net_score
        entry = 0
        THRESHOLD = 80 
        
        if score >= THRESHOLD:
            entry = direction
        
        atr = ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-1]

        metadata = {
            "strategy": self.STRATEGY_NAME,
            "score": score,
            "total_score": score,
            "score_breakdown": breakdown,
            "ema21": round(ema21_s.iloc[-1], 2),
            "ema50": round(ema50_s.iloc[-1], 2),
            "adx": round(curr_adx, 1),
            "rsi": round(curr_rsi, 1)
        }

        return {
            "entry": entry,
            "atr": atr,
            "metadata": metadata,
            "score": score
        }
