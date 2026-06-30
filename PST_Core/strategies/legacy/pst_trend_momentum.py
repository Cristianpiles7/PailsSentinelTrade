import pandas_ta as ta
import numpy as np
from ..models.classifier import RegimeMode

class PSTTrendMomentum:
    STRATEGY_NAME = "PST-Trend-Elite"
    STRATEGY_TYPE = RegimeMode.TREND
    WEIGHT = 1.5

    async def calculate_signal(self, data_input, current_regime, user_levels=None):
        # 1. Adaptador flexible (Dict o DF)
        if isinstance(data_input, dict):
            df_m15 = data_input.get('m15')
            df_h1 = data_input.get('h1')
            df_m5 = data_input.get('m5')
        else:
            # Legacy mode
            df_m15 = data_input
            df_h1 = None
            df_m5 = None

        # Filtro de Régimen
        if current_regime != RegimeMode.TREND:
            return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        if df_m15 is None or len(df_m15) < 50:
            return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        # --- SISTEMA DE PUNTUACIÓN (SCORING ENGINE) ---
        score = 0
        breakdown = {}
        
        # 1. ANÁLISIS H1 (TENDENCIA MACRO) - Max 40 ptos
        if df_h1 is not None and len(df_h1) > 200:
            h1_close = df_h1['close'].iloc[-1]
            h1_ema21 = ta.ema(df_h1['close'], length=21).iloc[-1]
            h1_ema50 = ta.ema(df_h1['close'], length=50).iloc[-1]
            h1_ema200 = ta.ema(df_h1['close'], length=200).iloc[-1]
            
            if h1_close > h1_ema21 > h1_ema50 > h1_ema200:
                score += 40
                breakdown["H1 Trend"] = "Strong Bull (+40)"
            elif h1_close > h1_ema50 > h1_ema200:
                score += 20
                breakdown["H1 Trend"] = "Bull Structure (+20)"
            elif h1_close < h1_ema21 < h1_ema50 < h1_ema200:
                score += 40
                breakdown["H1 Trend"] = "Strong Bear (+40)"
            elif h1_close < h1_ema50 < h1_ema200:
                score += 20
                breakdown["H1 Trend"] = "Bear Structure (+20)"
            else:
                breakdown["H1 Trend"] = "Neutral (0)"
        else:
            # Si no hay H1, asumimos neutro o usas M15
            breakdown["H1 Trend"] = "No Data (0)"

        # 2. ANÁLISIS M15 (TÁCTICO) - Max 30 ptos
        m15_close = df_m15['close'].iloc[-1]
        m15_ema21 = ta.ema(df_m15['close'], length=21).iloc[-1]
        
        adx_df = ta.adx(df_m15['high'], df_m15['low'], df_m15['close'], length=14)
        m15_adx = adx_df['ADX_14'].iloc[-1] if adx_df is not None else 0
        
        if m15_adx > 25:
            score += 20
            breakdown["M15 Momentum"] = f"High ADX {m15_adx:.1f} (+20)"
        elif m15_adx > 20:
            score += 10
            breakdown["M15 Momentum"] = f"Med ADX {m15_adx:.1f} (+10)"
        else:
            breakdown["M15 Momentum"] = f"Low ADX {m15_adx:.1f} (0)"

        if m15_close > m15_ema21: # Alineado
             score += 10
             breakdown["M15 Align"] = "Bull (+10)"
        
        # 3. ANÁLISIS M5 (TRIGGER) - Max 30 ptos
        entry_signal = 0
        vwap_crossover = False # Flag de evento
        
        if df_m5 is not None:
             m5_close = df_m5['close'].iloc[-1]
             m5_prev_close = df_m5['close'].iloc[-2]
             
             m5_vwap_df = ta.vwap(df_m5['high'], df_m5['low'], df_m5['close'], df_m5['tick_volume'])
             m5_vwap = m5_vwap_df.iloc[-1] if m5_vwap_df is not None else 0
             m5_prev_vwap = m5_vwap_df.iloc[-2] if m5_vwap_df is not None else m5_vwap
             
             if m5_close > m5_vwap:
                 score += 10
                 breakdown["M5 VWAP"] = "Above (+10)"
                 # Detección de Cruce Alcista (Eventos)
                 if m5_prev_close <= m5_prev_vwap:
                     vwap_crossover = True
                     breakdown["Trigger"] = "CROSSOVER BUY (+Bonus)"
                     score += 5 # Bonus por frescura
             elif m5_close < m5_vwap:
                 # Detección de Cruce Bajista (Eventos)
                 if m5_prev_close >= m5_prev_vwap:
                     vwap_crossover = True
                     breakdown["Trigger"] = "CROSSOVER SELL (+Bonus)"
                     score += 5 # Bonus por frescura
                 breakdown["M5 VWAP"] = "Below (0)"
        
        # --- DECISIÓN FINAL ---
        # Umbral: 75 puntos para entrar (Más estricto) + REQUISITO DE EVENTO
        # Excepción: Si el score es MUY alto (>85), permitimos entrada de continuación (Strong Trend Join)
        
        direction = 0 
        
        # Detectamos dirección predominante de H1 (o M15 si no hay H1)
        # Nota: La lógica de scoring ya asume dirección implicita (puntos si es Bull/Bear)
        # Pero aquí simplificamos: Si H1 dice Bull, solo compramos.
        
        h1_trend_str = breakdown.get("H1 Trend", "")
        if "Bull" in h1_trend_str:
            direction = 1
        elif "Bear" in h1_trend_str:
            direction = -1
        
        # ATR para TP/SL
        atr_df = ta.atr(df_m15['high'], df_m15['low'], df_m15['close'], length=14)
        atr = atr_df.iloc[-1] if atr_df is not None else 0

        # Metadata final
        metadata = {
            "score_breakdown": breakdown,
            "total_score": score
        }
        
        # REGLA MAESTRA:
        # 1. Score >= 75 (Antes 60)
        # 2. Dirección confirmada
        # 3. Gatillo: O es un cruce fresco (Crossover) O es una tendencia masiva (>85 pts)
        
        is_fresh_signal = vwap_crossover
        is_super_trend = score >= 85
        
        if direction != 0:
            if (is_fresh_signal and score >= 70) or (is_super_trend):
                 entry_signal = direction
            
        return {
            "entry": entry_signal,
            "atr": atr,
            "metadata": metadata,
            "score": score
        }
