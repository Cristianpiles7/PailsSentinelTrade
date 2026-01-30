import pandas_ta as ta
import pandas as pd
import numpy as np
import logging
from ..models.classifier import RegimeMode

from ..utils.tech_utils import get_asset_class

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
        
        # --- ASSET PROFILE LOADER ---
        symbol = "UNKNOWN"
        if isinstance(data_input, dict) and 'symbol' in data_input:
             symbol = data_input['symbol']
        # If not in dict, try to infer or default
        asset_class = get_asset_class(symbol)

        # DEFAULT PROFILE (Forex/General)
        P_ADX_THR = 30
        P_ATR_MARGIN = 0.15
        P_VOL_MULT = 1.5
        P_H1_PENALTY = 15
        
        # OVERRIDES
        if asset_class == "INDEX":
            P_ADX_THR = 35       # Indices need more strength to avoid noise
            P_VOL_MULT = 2.0     # Volume must be clearer
            P_H1_PENALTY = 20    # Respect H1 trend more
        elif asset_class == "METAL":
            P_ATR_MARGIN = 0.20  # Gold wicks are deadly, require 20% breakout
        elif asset_class == "CRYPTO":
            pass # Use defaults (Scalping friendly)
            
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

        # --- 0. RISK MANAGER: COOLDOWN & SCHEDULE ---
        current_hour = pd.Timestamp.now().hour
        
        # A. Schedule Check (Smart Sessions)
        is_schedule_ok = True
        schedule_msg = "Abierto"
        
        if asset_class == "INDEX":
             # US Indices: Schedule Restriction Removed by User Request
             # Relying on strategy logic (Momentum/Strong Candle) instead of hard time block.
             pass
        elif asset_class == "FOREX" or asset_class == "CRYPTO":
             pass # 24/7
             
        if not is_schedule_ok:
             return {
                "entry": 0, "atr": 0, 
                "metadata": {"strategy": self.STRATEGY_NAME, "score": 0, "total_score": 0, 
                             "score_breakdown": {"Horario": schedule_msg}, "factors_detailed": []}, 
                "score": 0
             }

        # B. Cooldown Check (Anti-Racha)
        # Necesitamos el último trade. Como no tenemos acceso directo a DB aqui facilmente sin hacerlo async complejo,
        # asumiremos que el Orchestrator pasa 'last_trade_result' o similar.
        # Si no, por simplicidad, lo implementamos en 'orchestrator.py' o aqui si pasamos el dato.
        # DADO QUE NO TENEMOS ACCESO A DB AQUI:
        # La solución robusta es retornar un flag y que el Orchestrator decida, O pasar el last_trade en data_input.
        # Por ahora, implementaremos el filtro horario que es crítico.
        # El Cooldown se debe implementar en el Orchestrator (nivel superior).

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
        
        # 2b. ATR Pre-calc (for dynamic breakout threshold)
        atr_series = ta.atr(df['high'], df['low'], df['close'], length=14)
        current_atr = atr_series.iloc[-1] if atr_series is not None else 0
        atr_series = ta.atr(df['high'], df['low'], df['close'], length=14)
        current_atr = atr_series.iloc[-1] if atr_series is not None else 0
        min_break_dist = current_atr * P_ATR_MARGIN # DYNAMIC THRESHOLD

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
                
                # --- NEW TRUTH TABLE LOGIC MAP (User Approved) ---
                
                # Context Definitions
                is_bull_trend = c_ema21 > c_ema50
                is_bear_trend = c_ema21 < c_ema50
                
                # Stability Filter (Origin Check)
                # Confirm we came from the "correct" side 5 bars ago for re-entries AND reversals
                origin_5_ema21 = tf_ema21.iloc[idx-5]
                origin_5_ema50 = tf_ema50.iloc[idx-5] # NEW: Origin check for EMA50
                origin_5_close = tf_df['close'].iloc[idx-5]
                
                # Estabilidad para BUY (Queremos venir de abajo)
                is_stable_buy_ema21 = origin_5_close < origin_5_ema21 
                is_stable_buy_ema50 = origin_5_close < origin_5_ema50 # NEW
                
                # Estabilidad para SELL (Queremos venir de arriba)
                is_stable_sell_ema21 = origin_5_close > origin_5_ema21
                is_stable_sell_ema50 = origin_5_close > origin_5_ema50 # NEW

                # 1. EMA50 Break (TYPE A - Reversal)
                # STRUCTURE CHECK: Instant Reactivity
                # We require the EMAs to be aligned in the OPPOSITE direction of the break.
                # If buying (breaking Up), we want EMA21 < EMA50 (Bear Structure).
                # BREAKOUT CONFIRMATION: Must exceed min_break_dist (15% ATR)
                if (p_close < tf_ema50.iloc[prev_idx] and c_close > (tf_ema50.iloc[idx] + min_break_dist)) and is_strong_candle and is_bear_trend:
                     if action_pts > bull_base:
                         bull_base = action_pts
                         base_event = {"k": f"Giro EMA50 {tf_name}", "v": "Reversal (Type A)", "score": round(bull_base)}
                     if i < last_event_idx: last_event_idx = i

                elif (p_close > tf_ema50.iloc[prev_idx] and c_close < (tf_ema50.iloc[idx] - min_break_dist)) and is_strong_candle and is_bull_trend:
                     if action_pts > bear_base:
                         bear_base = action_pts
                         base_event = {"k": f"Giro EMA50 {tf_name}", "v": "Reversal (Type A)", "score": round(bear_base)}
                     if i < last_event_idx: last_event_idx = i

                # 2. EMA21 Break (TYPE B vs C)
                # Requires Stability Filter + Trend Alignment + ATR Margin
                elif (p_close < tf_ema21.iloc[prev_idx] and c_close > (tf_ema21.iloc[idx] + min_break_dist)) and is_strong_candle:
                     # BUY SIGNAL
                     if is_bull_trend and is_stable_buy_ema21:
                         # TYPE B: Trend Resumption
                         pts = action_pts - 10
                         if pts > bull_base:
                             bull_base = pts
                             base_event = {"k": f"Re-Entrada EMA21 {tf_name}", "v": "Trend (Type B)", "score": round(bull_base)}
                         if i < last_event_idx: last_event_idx = i
                     elif is_bear_trend:
                         # TYPE C: TRAP (Pullback in Bear Trend)
                         # Ignored
                         pass

                elif (p_close > tf_ema21.iloc[prev_idx] and c_close < (tf_ema21.iloc[idx] - min_break_dist)) and is_strong_candle:
                     # SELL SIGNAL
                     if is_bear_trend and is_stable_sell_ema21:
                         # TYPE B: Trend Resumption
                         pts = action_pts - 10
                         if pts > bear_base:
                             bear_base = pts
                             base_event = {"k": f"Re-Entrada EMA21 {tf_name}", "v": "Trend (Type B)", "score": round(bear_base)}
                         if i < last_event_idx: last_event_idx = i
                     elif is_bull_trend:
                         # TYPE C: TRAP (Pullback in Bull Trend)
                         # Ignored
                         pass

                
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

        # --- NEW MTF MOMENTUM (ADX DYNAMIC) ---
        adx_pts = 0
        if mtr_m5 and mtr_m5['adx'] >= P_ADX_THR: adx_pts += 30; factors_detailed.append({"k": "M5 Fuerza", "v": f"Alta ({mtr_m5['adx']:.1f})", "score": 30})
        if mtr_m15 and mtr_m15['adx'] >= 20: adx_pts += 15; factors_detailed.append({"k": "M15 Fuerza", "v": f"OK ({mtr_m15['adx']:.1f})", "score": 15})
        if mtr_h1 and mtr_h1['adx'] >= 20: adx_pts += 10; factors_detailed.append({"k": "H1 Fuerza", "v": f"Trend ({mtr_h1['adx']:.1f})", "score": 10})
        if mtr_m1 and mtr_m1['adx'] >= 30: adx_pts += 5; factors_detailed.append({"k": "M1 Impulso", "v": f"Explosivo ({mtr_m1['adx']:.1f})", "score": 5})

        net_score += adx_pts # SUMAR PUNTOS POSITIVOS
        adx_gate = adx_pts >= 30 # Keep strict minimum 30pts total from MTF
        if not adx_gate:
            penalty = 30
            net_score -= penalty
            factors_detailed.append({"k": "Filtro Fuerza", "v": "Insuficiente (MTF)", "score": -penalty})
        
        # --- NEW MTF VOLUME (DYNAMIC) ---
        vol_pts = 0
        if mtr_m5 and mtr_m5['vol_rel'] >= P_VOL_MULT: vol_pts += 30; factors_detailed.append({"k": "M5 Volumen", "v": f"OK ({mtr_m5['vol_rel']:.1f}x)", "score": 30})
        if mtr_m15 and mtr_m15['vol_rel'] >= 1.2: vol_pts += 20; factors_detailed.append({"k": "M15 Volumen", "v": f"Anomalía ({mtr_m15['vol_rel']:.1f}x)", "score": 20})
        if mtr_h1 and mtr_h1['vol_rel'] >= 1.1: vol_pts += 10; factors_detailed.append({"k": "H1 Volumen", "v": f"Interés ({mtr_h1['vol_rel']:.1f}x)", "score": 10})

        net_score += vol_pts # SUMAR PUNTOS POSITIVOS
        vol_gate = vol_pts >= 10 # FLEXIBILIZADO: Con que H1 o M15 tengan volumen (10-20 pts), abrimos la puerta.
        if not vol_gate:
            penalty = 40
            net_score -= penalty
            factors_detailed.append({"k": "Filtro Vol.", "v": "Sin Interés (MTF)", "score": -penalty})

        # --- FINAL HARD GATES (MTF CONFIRMATION) ---
        # Instead of just losing points, we force score to 0 if MTF strength is missing.
        # This prevents entries in choppy markets where base score might be high but ADX is dead.
        if adx_pts < 30: # Use the gate we already calculated
             net_score = 0
             factors_detailed.append({"k": "Gate MTF", "v": "ADX Insuficiente (<30)", "score": -100})
             gate_failed = True
        
        if not vol_gate:
             net_score = 0
             factors_detailed.append({"k": "Gate MTF", "v": "Volumen Insuficiente", "score": -100})
             gate_failed = True
        ema50_slope = (ema50_s.iloc[-1] - ema50_s.iloc[-6]) / ema50_s.iloc[-6] * 100 if len(ema50_s) > 6 else 0
        if abs(ema50_slope) < 0.005: 
             factors_detailed.append({"k": "Filtro Pendiente", "v": f"Lateral ({ema50_slope:.4f})", "score": -15})
             net_score -= 15
        else:
             factors_detailed.append({"k": "Filtro Pendiente", "v": "OK", "score": 0})

        # 1. HTF ALIGNMENT (M15 EMA)
        # 1. HTF ALIGNMENT (M15 EMA & H1 Dynamic Filter)
        if direction == 1 and htf_filter == -1:
            factors_detailed.append({"k": "Alineación M15", "v": "Contratendencia", "score": -20})
            net_score -= 20
        elif direction == -1 and htf_filter == 1:
            factors_detailed.append({"k": "Alineación M15", "v": "Contratendencia", "score": -20})
            net_score -= 20
        elif htf_filter != 0:
            net_score += 15
            factors_detailed.append({"k": "Alineación M15", "v": "Confirmada", "score": 15})

        # --- H1 DYNAMIC FILTER (FRICTION) ---
        # Instead of blocking, we apply a penalty if trading against H1 term trend.
        if mtr_h1:
            # Simple H1 Trend Estimation (Price vs EMA50)
            # Cannot calculate EMA on mtr_h1 because it's just a dict, need history series.
            # Assuming orchestration passes H1 EMA50 via mtr_h1 is complex, let's use Price vs EMA50 if we had it.
            # Workaround: Calculate H1 EMA50 here if df_m15 implies H1 flow or use external data.
            # BETTER: Use the `get_mtf_data` H1 dataframe passed in `data_input`.
            df_h1 = data_input.get('h1') if isinstance(data_input, dict) else None
            
            if df_h1 is not None and len(df_h1) > 50:
                 h1_ema50 = ta.ema(df_h1['close'], length=50).iloc[-1]
                 h1_close = df_h1['close'].iloc[-1]
                 
                 h1_trend = 1 if h1_close > h1_ema50 else -1
                 
                 # INTELLIGENT H1 FILTER:
                 # If signal is TYPE A (Reversal Breakout), we IGNORE H1 trend (because we are changing it!)
                 # If signal is TYPE B (Trend Re-Entry), we RESPECT H1 trend.
                 is_type_a_reversal = False
                 if base_event and "Type A" in base_event.get("v", ""):
                     is_type_a_reversal = True

                 if direction != h1_trend:
                     if is_type_a_reversal:
                         # EXEMPTION GRANTED
                         factors_detailed.append({"k": f"Fricción H1 ({asset_class})", "v": "Ignorada (Giro Maestro Tipo A)", "score": 0})
                     else:
                         # PENALTY APPLIED (Type B or other)
                         penalty_h1 = P_H1_PENALTY
                         net_score -= penalty_h1
                         factors_detailed.append({"k": f"Fricción H1 ({asset_class})", "v": "Contratendencia", "score": -penalty_h1})
                 else:
                     # Optional: Small bonus for full alignment
                     pass

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
            # STRICT: Must be above EMA21. If EMA50 > EMA21 (Bearish Cross), MUST be above EMA50 too.
            fail_ema21 = latest_c <= latest_ema21
            fail_ema50 = (latest_ema50 > latest_ema21) and (latest_c <= latest_ema50)
            
            # --- BREAKOUT CONFIRMATION LOGIC ---
            # If previous candle was NOT above EMA21, this is a FRESH breakout.
            # We require either current close > EMA21 + margin OR wait for next candle.
            prev_c = df['close'].iloc[-2]
            prev_ema21 = ema21_s.iloc[-2]

            was_bullish = prev_c > prev_ema21
            is_marginal = (latest_c - latest_ema21) < min_break_dist
            
            if not was_bullish and is_marginal and not fail_ema21:
                 gate_failed = True
                 penalty = round(pre_penalty_score * 0.5)
                 net_score -= penalty
                 factors_detailed.append({"k": "Confirmación", "v": "Marginal (Espere)", "score": -penalty})

            if fail_ema21 or fail_ema50:
                gate_failed = True
                net_score = net_score * 0.3
                penalty = round(pre_penalty_score - net_score)
                # If we fail EMA50 in a downtrend (Orange > Blue), it means we are caught below Orange.
                cause = "Cierre < EMA21" if fail_ema21 else "Zona de Trampa (Sandwich EMA50)"
                factors_detailed.append({"k": "Validación", "v": cause, "score": -penalty})
            
            dist_ema50 = (latest_c - latest_ema50) / latest_ema50 * 100
            if dist_ema50 > 1.0:
                gate_failed = True
                factors_detailed.append({"k": "Exceso", "v": "Sobre-extendido", "score": -30})
                net_score -= 30
        elif direction == -1:
            # STRICT: Must be below EMA21. If EMA50 < EMA21 (Bullish Cross), MUST be below EMA50 too.
            fail_ema21 = latest_c >= latest_ema21
            fail_ema50 = (latest_ema50 < latest_ema21) and (latest_c >= latest_ema50)
            
            # --- BREAKOUT CONFIRMATION LOGIC ---
            prev_c = df['close'].iloc[-2]
            prev_ema21 = ema21_s.iloc[-2]

            was_bearish = prev_c < prev_ema21
            is_marginal = (latest_ema21 - latest_c) < min_break_dist
            
            if not was_bearish and is_marginal and not fail_ema21:
                 gate_failed = True
                 penalty = round(pre_penalty_score * 0.5)
                 net_score -= penalty
                 factors_detailed.append({"k": "Confirmación", "v": "Marginal (Espere)", "score": -penalty})

            if fail_ema21 or fail_ema50:
                gate_failed = True
                net_score = net_score * 0.3
                penalty = round(pre_penalty_score - net_score)
                cause = "Cierre > EMA21" if fail_ema21 else "Zona de Trampa (Sandwich EMA50)"
                factors_detailed.append({"k": "Validación", "v": cause, "score": -penalty})
            
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
