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
                    "total_score": 0,
                    "score_breakdown": {"Estado": "Esperando Historial (M5 < 55 velas)"}
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
            # Candle Stats for Quality Check
            c_high = df['high'].iloc[idx]; c_low = df['low'].iloc[idx]
            body_size = abs(c_close - c_open)
            full_range = max(0.00001, c_high - c_low)
            body_ratio = body_size / full_range
            
            # Avg Body for Momentum Check (Last 5)
            avg_body = df['close'].diff().abs().iloc[idx-5:idx].mean() if len(df) > 10 else 0
            is_strong_candle = body_ratio > 0.6 and body_size > avg_body
            
            # Bull Break
            if p_close < p_ema50 and c_close > c_ema50:
                if is_strong_candle:
                    bull_score += action_pts
                    breakdown[f"EMA50 Break (-{i})"] = f"+{action_pts}"
                else:
                    breakdown[f"EMA50 Break (-{i})"] = "IGNORED (Weak Candle)"
                if i < last_event_idx: last_event_idx = i
            # Bear Break
            elif p_close > p_ema50 and c_close < c_ema50:
                if is_strong_candle:
                    bear_score += action_pts
                    breakdown[f"EMA50 Breakdown (-{i})"] = f"+{action_pts}"
                else:
                    breakdown[f"EMA50 Breakdown (-{i})"] = "IGNORED (Weak Candle)"
                if i < last_event_idx: last_event_idx = i
                
            # C. BOUNCE (Primary)
            # Candle Wicks for Rejection Check
            lower_wick = min(c_open, c_close) - c_low
            upper_wick = c_high - max(c_open, c_close)
            lower_wick_ratio = lower_wick / full_range
            upper_wick_ratio = upper_wick / full_range

            # Bull Bounce
            touched_21 = df['low'].iloc[idx] <= c_ema21
            is_green = c_close > c_open
            trend_up = c_ema21 > c_ema50
            if trend_up and touched_21 and c_close > c_ema21:
                # 1. Quality Check: Rejection Wick or Strong Body
                is_rejection = lower_wick_ratio > 0.3 or body_ratio > 0.5
                if is_green and is_rejection:
                    pts = action_pts
                    # Pin Bar Bonus
                    if lower_wick_ratio > 0.6: 
                        pts += 15
                        breakdown[f"Bull PinBar (-{i})"] = "+15"
                    
                    bull_score += pts
                    breakdown[f"Bull Bounce (-{i})"] = f"+{pts}"
                    if i < last_event_idx: last_event_idx = i
            
            # Bear Bounce
            touched_21_bear = df['high'].iloc[idx] >= c_ema21
            is_red = c_close < c_open
            trend_down = c_ema21 < c_ema50
            if trend_down and touched_21_bear and c_close < c_ema21:
                is_rejection_bear = upper_wick_ratio > 0.3 or body_ratio > 0.5
                if is_red and is_rejection_bear:
                    pts = action_pts
                    # Pin Bar Bonus
                    if upper_wick_ratio > 0.6: 
                        pts += 15
                        breakdown[f"Bear PinBar (-{i})"] = "+15"

                    bear_score += pts
                    breakdown[f"Bear Bounce (-{i})"] = f"+{pts}"
                    if i < last_event_idx: last_event_idx = i

        # 4. INDICATORS (Current Candle)
        curr_rsi = rsi_s.iloc[-1]
        curr_adx = adx_df['ADX_14'].iloc[-1] if adx_df is not None else 0
        curr_vol = vol_s.iloc[-1] if 'tick_volume' in df else 0
        curr_vol_ma = vol_ma_s.iloc[-1] if vol_ma_s is not None else 1
        
        # --- NEW STRICT FILTERS (The Gates) ---
        gate_failed = False
        # 1. ADX GATE: Must have enough strength (ADX > 25)
        if curr_adx < 25:
             breakdown["Filtro ADX"] = f"Bajo ({curr_adx:.1f} < 25)"
             gate_failed = True
        else:
             breakdown["Filtro ADX"] = f"OK ({curr_adx:.1f})"
            
        # 2. VOLUME GATE: Must have fresh interest (Vol > 1.2x MA20)
        vol_ratio = curr_vol / curr_vol_ma if curr_vol_ma > 0 else 0
        if vol_ratio < 1.2:
             breakdown["Filtro Vol."] = f"Bajo ({vol_ratio:.2f}x)"
             gate_failed = True
        else:
             breakdown["Filtro Vol."] = f"OK ({vol_ratio:.2f}x)"

        # 3. SLOPE GATE: EMA50 must not be flat
        ema50_slope = (ema50_s.iloc[-1] - ema50_s.iloc[-6]) / ema50_s.iloc[-6] * 100 if len(ema50_s) > 6 else 0
        if abs(ema50_slope) < 0.005: 
             breakdown["Filtro Pendiente"] = f"Lateral ({ema50_slope:.4f})"
             gate_failed = True
        else:
             breakdown["Filtro Pendiente"] = f"OK ({ema50_slope:.4f})"

        # --- CALCULATE NET SCORE ---
        net_score = 0
        direction = 0 # 1 Bull, -1 Bear
        
        if bull_score > bear_score:
            net_score = bull_score - bear_score
            direction = 1
            breakdown["Sesgo Técnico"] = f"Alcista ({bull_score} - {bear_score})"
        elif bear_score > bull_score:
            net_score = bear_score - bull_score
            direction = -1
            breakdown["Sesgo Técnico"] = f"Bajista ({bear_score} - {bull_score})"
        else:
            return {"entry": 0, "atr": 0, "metadata": {"status": "Neutral (Sin Sesgo)"}, "score": 0}

        # --- CRITICAL CONFIRMATION (Price Location) ---
        latest_c = df['close'].iloc[-1]
        latest_ema21 = ema21_s.iloc[-1]
        latest_ema50 = ema50_s.iloc[-1]
        
        if direction == 1:
            if latest_c <= latest_ema21:
                # No ponemos a 0 el net_score, solo bloqueamos entry
                gate_failed = True
                breakdown["Validación"] = "Fallo (Cierre < EMA21)"
            
            dist_ema50 = (latest_c - latest_ema50) / latest_ema50 * 100
            if dist_ema50 > 1.0:
                gate_failed = True
                breakdown["Validación"] = "Sobre-extendido (Bull)"
        elif direction == -1:
            if latest_c >= latest_ema21:
                gate_failed = True
                breakdown["Validación"] = "Fallo (Cierre > EMA21)"
            
            dist_ema50 = (latest_ema50 - latest_c) / latest_ema50 * 100
            if dist_ema50 > 1.0:
                gate_failed = True
                breakdown["Validación"] = "Sobre-extendido (Bear)"

        # 2. HTF FILTER (M15)
        if direction == 1 and htf_filter == -1:
            gate_failed = True
            breakdown["Filtro M15"] = "Bajista (Bloqueo)"
        elif direction == -1 and htf_filter == 1:
            gate_failed = True
            breakdown["Filtro M15"] = "Alcista (Bloqueo)"
        elif htf_filter != 0:
            net_score += 15
            breakdown["Alineación M15"] = "+15"

        # 3. FRESHNESS BONUS
        if last_event_idx == 0: 
            net_score += 10
            breakdown["Inmediatez"] = "+10"
        
        # 4. MOMENTUM CONFIRMATION (RSI)
        if direction == 1:
             if 50 < curr_rsi < 75: 
                 net_score += 15
                 breakdown["Impulso RSI"] = "+15"
             else:
                 gate_failed = True
                 breakdown["Zona RSI"] = "No óptima para Compra"
        elif direction == -1:
             if 25 < curr_rsi < 50: 
                 net_score += 15
                 breakdown["Impulso RSI"] = "+15"
             else:
                 gate_failed = True
                 breakdown["Zona RSI"] = "No óptima para Venta"

        # --- FINAL DECISION ---
        # Cap the score at 100
        score = min(100, max(0, net_score))
        entry = 0
        THRESHOLD = 80 
        
        # Only allow entry if Technical Score is high AND gates passed
        if score >= THRESHOLD and not gate_failed:
            entry = direction
        elif score >= THRESHOLD and gate_failed:
            breakdown["Estado Operativo"] = "Filtros Activos (Bloqueo)"
        
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
