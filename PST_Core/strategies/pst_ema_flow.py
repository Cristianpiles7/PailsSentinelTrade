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

        # 3. DUAL-WINDOW SCORING ENGINE (M5 + M15 Cascading Triggers)
        bull_base = 0
        bear_base = 0
        breakdown = {}
        factors_detailed = [] 
        last_event_idx = 99
        
        # Prep M15 EMAs for scanning
        m15_ema21_s = ta.ema(df_m15['close'], length=21) if df_m15 is not None else None
        m15_ema50_s = ta.ema(df_m15['close'], length=50) if df_m15 is not None else None

        # CONFIG: [Timeframe Name, Current DF, EMAs (21,50), Window Size]
        tf_configs = [
            ("M5", df, ema21_s, ema50_s, 3),
            ("M15", df_m15, m15_ema21_s, m15_ema50_s, 2) # Mas corto para M15 para evitar delay
        ]

        for tf_name, tf_df, tf_ema21, tf_ema50, window in tf_configs:
            if tf_df is None or tf_ema21 is None or len(tf_df) < 55: continue
            
            for i in range(window): 
                idx = -1 - i
                prev_idx = idx - 1
                
                # Data Points
                c_close = tf_df['close'].iloc[idx]; c_open = tf_df['open'].iloc[idx]
                c_ema21 = tf_ema21.iloc[idx]; c_ema50 = tf_ema50.iloc[idx]
                p_ema21 = tf_ema21.iloc[prev_idx]; p_ema50 = tf_ema50.iloc[prev_idx]
                p_close = tf_df['close'].iloc[prev_idx]
                
                # DYNAMIC DECAY & TF MULTIPLIER (M15 is stronger)
                tf_mult = 1.2 if tf_name == "M15" else 1.0
                action_pts = (60 - (i * 10)) * tf_mult
                cross_pts = (20 - (i * 5)) * tf_mult
                
                # Event storage (for logging/detailed view)
                base_event = None
                
                # A. CROSSOVER
                if p_ema21 <= p_ema50 and c_ema21 > c_ema50: # Golden
                    if cross_pts > bull_base:
                        bull_base = cross_pts
                        base_event = {"k": f"G. Cross {tf_name} (-{i})", "v": "Confirmado", "score": round(cross_pts)}
                elif p_ema21 >= p_ema50 and c_ema21 < c_ema50: # Death
                    if cross_pts > bear_base:
                        bear_base = cross_pts
                        base_event = {"k": f"D. Cross {tf_name} (-{i})", "v": "Confirmado", "score": round(bear_base)}
                    
                # B. BREAKOUT / BOUNCE
                c_high = tf_df['high'].iloc[idx]; c_low = tf_df['low'].iloc[idx]
                body_size = abs(c_close - c_open)
                full_range = max(0.00001, c_high - c_low)
                body_ratio = body_size / full_range
                
                avg_body = tf_df['close'].diff().abs().iloc[idx-5:idx].mean() if len(tf_df) > 10 else 0
                is_strong_candle = body_ratio > 0.6 and body_size > avg_body

                # Bull Break / Crossovers
                if (p_close < tf_ema50.iloc[prev_idx] and c_close > c_ema50) and is_strong_candle:
                    if action_pts > bull_base:
                        bull_base = action_pts
                        base_event = {"k": f"Ruptura EMA50 {tf_name}", "v": "Impulso", "score": round(bull_base)}
                    if i < last_event_idx: last_event_idx = i
                
                # EMA 21 Break 
                if (p_close < tf_ema21.iloc[prev_idx] and c_close > c_ema21):
                    pts = action_pts - 10
                    if pts > bull_base:
                        bull_base = pts
                        base_event = {"k": f"Ruptura EMA21 {tf_name}", "v": "Tactico", "score": round(bull_base)}
                    if i < last_event_idx: last_event_idx = i
                
                # Bull Bounce
                touched_21 = tf_df['low'].iloc[idx] <= c_ema21
                is_green = c_close > c_open
                trend_up = c_ema21 > c_ema50
                if trend_up and touched_21 and c_close > c_ema21:
                    lower_wick = min(c_open, c_close) - c_low
                    lower_wick_ratio = lower_wick / full_range
                    if (lower_wick_ratio > 0.3 or body_ratio > 0.5) and is_green:
                        pts = action_pts + (20 if lower_wick_ratio > 0.6 else 0)
                        if pts > bull_base:
                            bull_base = pts
                            base_event = {"k": f"Bounce EMA21 {tf_name}", "v": "Rechazo", "score": round(bull_base)}
                        if i < last_event_idx: last_event_idx = i

                # Bear Break / Loss
                if (p_close > tf_ema50.iloc[prev_idx] and c_close < c_ema50) and is_strong_candle:
                    if action_pts > bear_base:
                        bear_base = action_pts
                        base_event = {"k": f"Fallo EMA50 {tf_name}", "v": "Impulso", "score": round(bear_base)}
                    if i < last_event_idx: last_event_idx = i

                # EMA 21 Loss 
                if (p_close > tf_ema21.iloc[prev_idx] and c_close < c_ema21):
                    pts = action_pts - 10
                    if pts > bear_base:
                        bear_base = pts
                        base_event = {"k": f"Fallo EMA21 {tf_name}", "v": "Tactico", "score": round(bear_base)}
                    if i < last_event_idx: last_event_idx = i
                
                # Bear Bounce
                touched_21_bear = tf_df['high'].iloc[idx] >= c_ema21
                is_red = c_close < c_open
                trend_down = c_ema21 < c_ema50
                if trend_down and touched_21_bear and c_close < c_ema21:
                    upper_wick = c_high - max(c_open, c_close)
                    upper_wick_ratio = upper_wick / full_range
                    if (upper_wick_ratio > 0.3 or body_ratio > 0.5) and is_red:
                        pts = action_pts + (20 if upper_wick_ratio > 0.6 else 0)
                        if pts > bear_base:
                            bear_base = pts
                            base_event = {"k": f"Bounce EMA21 {tf_name}", "v": "Rechazo", "score": round(bear_base)}
                        if i < last_event_idx: last_event_idx = i
                
                # Injection
                if base_event:
                    # Clean previous TF events if this one is better
                    factors_detailed = [f for f in factors_detailed if not f['k'].endswith(f" {tf_name}")]
                    factors_detailed.append(base_event)

        # 4. INDICATORS (MTF Analysis)
        def get_mtr_data(df_in):
            if df_in is None or len(df_in) < 20: return None
            try:
                _rsi = ta.rsi(df_in['close'], length=14).iloc[-1]
                _adx = ta.adx(df_in['high'], df_in['low'], df_in['close'], length=14)['ADX_14'].iloc[-1]
                _v = df_in['tick_volume'].iloc[-1] if 'tick_volume' in df_in else 0
                _v_ma = ta.sma(df_in['tick_volume'], length=20).iloc[-1] if 'tick_volume' in df_in else 1
                return {"rsi": _rsi, "adx": _adx, "vol_rel": _v / _v_ma if _v_ma > 0 else 0}
            except: return None

        mtr_m1 = get_mtr_data(data_input.get('m1')) if isinstance(data_input, dict) else None
        mtr_m5 = get_mtr_data(df)
        mtr_m15 = get_mtr_data(df_m15)
        mtr_h1 = get_mtr_data(data_input.get('h1')) if isinstance(data_input, dict) else None
        
        # --- CALCULATE BASE SCORE ---
        bull_score = bull_base
        bear_score = bear_base
        net_score = 0
        direction = 0 # 1 Bull, -1 Bear
        
        if bull_score > bear_score:
            net_score = bull_score
            direction = 1
            breakdown["Sesgo Técnico"] = f"Alcista ({bull_score})"
        elif bear_score > bull_score:
            net_score = bear_score
            direction = -1
            breakdown["Sesgo Técnico"] = f"Bajista ({bear_score})"
        else:
            return {"entry": 0, "atr": 0, "metadata": {"status": "Neutral (Sin Sesgo)"}, "score": 0}

        # --- NEW MTF MOMENTUM (ADX) ---
        adx_pts = 0
        if mtr_m5 and mtr_m5['adx'] >= 25: adx_pts += 30; factors_detailed.append({"k": "M5 Fuerza", "v": f"Alta ({mtr_m5['adx']:.1f})", "score": 30})
        if mtr_m15 and mtr_m15['adx'] >= 20: adx_pts += 15; factors_detailed.append({"k": "M15 Fuerza", "v": f"OK ({mtr_m15['adx']:.1f})", "score": 15})
        if mtr_h1 and mtr_h1['adx'] >= 20: adx_pts += 10; factors_detailed.append({"k": "H1 Fuerza", "v": f"Trend ({mtr_h1['adx']:.1f})", "score": 10})
        if mtr_m1 and mtr_m1['adx'] >= 30: adx_pts += 5; factors_detailed.append({"k": "M1 Impulso", "v": f"Explosivo ({mtr_m1['adx']:.1f})", "score": 5})

        net_score += adx_pts # SUMAR PUNTOS POSITIVOS
        adx_gate = adx_pts >= 30
        if not adx_gate:
            penalty = 30
            net_score -= penalty
            factors_detailed.append({"k": "Filtro Fuerza", "v": "Insuficiente (MTF)", "score": -penalty})
        
        # --- NEW MTF VOLUME ---
        vol_pts = 0
        if mtr_m5 and mtr_m5['vol_rel'] >= 1.2: vol_pts += 30; factors_detailed.append({"k": "M5 Volumen", "v": f"OK ({mtr_m5['vol_rel']:.1f}x)", "score": 30})
        if mtr_m15 and mtr_m15['vol_rel'] >= 1.2: vol_pts += 20; factors_detailed.append({"k": "M15 Volumen", "v": f"Anomalía ({mtr_m15['vol_rel']:.1f}x)", "score": 20})
        if mtr_h1 and mtr_h1['vol_rel'] >= 1.1: vol_pts += 10; factors_detailed.append({"k": "H1 Volumen", "v": f"Interés ({mtr_h1['vol_rel']:.1f}x)", "score": 10})

        net_score += vol_pts # SUMAR PUNTOS POSITIVOS
        vol_gate = vol_pts >= 10 # FLEXIBILIZADO: Con que H1 o M15 tengan volumen (10-20 pts), abrimos la puerta.
        if not vol_gate:
            penalty = 40
            net_score -= penalty
            factors_detailed.append({"k": "Filtro Vol.", "v": "Sin Interés (MTF)", "score": -penalty})

        # SLOPE GATE (Keep M5 for tactical timing)
        ema50_slope = (ema50_s.iloc[-1] - ema50_s.iloc[-6]) / ema50_s.iloc[-6] * 100 if len(ema50_s) > 6 else 0
        if abs(ema50_slope) < 0.005: 
             factors_detailed.append({"k": "Filtro Pendiente", "v": f"Lateral ({ema50_slope:.4f})", "score": -15})
             net_score -= 15
        else:
             factors_detailed.append({"k": "Filtro Pendiente", "v": "OK", "score": 0})

        # 1. HTF ALIGNMENT (M15 EMA)
        if direction == 1 and htf_filter == -1:
            factors_detailed.append({"k": "Alineación M15", "v": "Contratendencia", "score": -20})
            net_score -= 20
        elif direction == -1 and htf_filter == 1:
            factors_detailed.append({"k": "Alineación M15", "v": "Contratendencia", "score": -20})
            net_score -= 20
        elif htf_filter != 0:
            net_score += 15
            factors_detailed.append({"k": "Alineación M15", "v": "Confirmada", "score": 15})

        # 2. FRESHNESS BONUS
        if last_event_idx == 0: 
            net_score += 10
            factors_detailed.append({"k": "Inmediatez", "v": "Evento Reciente", "score": 10})
        
        # 3. MOMENTUM CONFIRMATION (RSI M5)
        if mtr_m5 is None:
            return {"entry": 0, "atr": 0, "metadata": {"status": "Error: Indicadores M5 no disponibles"}, "score": 0}

        curr_rsi = mtr_m5['rsi']
        if direction == 1:
             if 50 < curr_rsi < 75: 
                 net_score += 15
                 factors_detailed.append({"k": "Impulso RSI", "v": f"Alcista ({curr_rsi:.1f})", "score": 15})
             else:
                 factors_detailed.append({"k": "Zona RSI", "v": f"No óptima ({curr_rsi:.1f})", "score": -10})
                 net_score -= 10
        elif direction == -1:
             if 25 < curr_rsi < 50: 
                 net_score += 15
                 factors_detailed.append({"k": "Impulso RSI", "v": f"Bajista ({curr_rsi:.1f})", "score": 15})
             else:
                 factors_detailed.append({"k": "Zona RSI", "v": f"No óptima ({curr_rsi:.1f})", "score": -10})
                 net_score -= 10

        # --- CRITICAL FINAL PENALTY (Price vs EMA Location) ---
        latest_c = df['close'].iloc[-1]
        latest_ema21 = ema21_s.iloc[-1]
        latest_ema50 = ema50_s.iloc[-1]
        
        pre_penalty_score = net_score
        gate_failed = (not adx_gate) or (not vol_gate)

        if direction == 1:
            if latest_c <= latest_ema21:
                gate_failed = True
                net_score = net_score * 0.3
                penalty = round(pre_penalty_score - net_score)
                factors_detailed.append({"k": "Validación", "v": "Cierre < EMA21", "score": -penalty})
            
            dist_ema50 = (latest_c - latest_ema50) / latest_ema50 * 100
            if dist_ema50 > 1.0:
                gate_failed = True
                factors_detailed.append({"k": "Exceso", "v": "Sobre-extendido", "score": -30})
                net_score -= 30
        elif direction == -1:
            if latest_c >= latest_ema21:
                gate_failed = True
                net_score = net_score * 0.3
                penalty = round(pre_penalty_score - net_score)
                factors_detailed.append({"k": "Validación", "v": "Cierre > EMA21", "score": -penalty})
            
            dist_ema50 = (latest_ema50 - latest_c) / latest_ema50 * 100
            if dist_ema50 > 1.0:
                gate_failed = True
                factors_detailed.append({"k": "Exceso", "v": "Sobre-extendido", "score": -30})
                net_score -= 30

        # --- FINAL DECISION ---
        score = min(100, max(0, round(net_score)))
        entry = 0
        THRESHOLD = 80 
        
        if score >= THRESHOLD and not gate_failed:
            entry = direction
        
        atr = ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-1]

        # Sync Breakdown for dashboard tooltips
        for f in factors_detailed:
             breakdown[f['k']] = f"{f['v']} ({'+' if f['score'] >= 0 else ''}{f['score']})"

        metadata = {
            "strategy": self.STRATEGY_NAME,
            "score": score,
            "total_score": score,
            "score_breakdown": breakdown,
            "factors_detailed": factors_detailed,
            "can_entry": entry != 0,
            "gate_failed": gate_failed,
            "ema21": round(ema21_s.iloc[-1], 2),
            "ema50": round(ema50_s.iloc[-1], 2),
            "adx": round(mtr_m5['adx'] if mtr_m5 else 0, 1),
            "rsi": round(curr_rsi, 1)
        }

        return {
            "entry": entry,
            "atr": atr,
            "metadata": metadata,
            "score": score
        }
