import pandas_ta as ta
import pandas as pd
import numpy as np
import logging
from datetime import datetime, timezone
from ..models.classifier import RegimeMode

from ..utils.tech_utils import get_asset_class

# Helper for momentum metrics (Module Level to avoid scoping issues)
def get_mtr_data(df_in, tf_minutes=5):
    if df_in is None or len(df_in) < 14: return None
    try:
        # DEFENSIVE: Ensure numeric and handle NaNs (Warmup increased for ADX precision)
        df_clean = df_in[['high', 'low', 'close', 'tick_volume']].tail(200).copy()
        df_clean = df_clean.ffill().fillna(0)
        
        if len(df_clean) < 14: return None

        # 1. RSI (Standard)
        _rsi_s = ta.rsi(df_clean['close'], length=14)
        _rsi = _rsi_s.iloc[-1] if _rsi_s is not None and not _rsi_s.empty else 50
        
        # 2. ADX (Robust)
        _adx_df = ta.adx(df_clean['high'], df_clean['low'], df_clean['close'], length=14)
        _adx = 0
        if _adx_df is not None and not _adx_df.empty:
             # Find the ADX column regardless of exact name (ADX_14, ADX_20 etc)
             adx_cols = [c for c in _adx_df.columns if 'ADX' in c]
             if adx_cols:
                  _adx = _adx_df[adx_cols[0]].iloc[-1]
        
        if pd.isna(_adx): _adx = 0

        # 3. VOLUME LOGIC (FAIRNESS)
        # Use the LAST COMPLETED candle for ground truth instead of the current one
        # Current candle volume is unfair in the first minutes.
        if len(df_in) < 2: return {"rsi": _rsi, "adx": _adx, "vol_rel": 0}
        
        v_prev = df_in['tick_volume'].iloc[-2] # Finished candle
        v_ma = ta.sma(df_in['tick_volume'], length=20)
        v_ma_val = v_ma.iloc[-2] if v_ma is not None and len(v_ma) > 1 else 1
        
        # Check current candle too (for spikes)
        v_curr = df_in['tick_volume'].iloc[-1]
        v_curr_rel = v_curr / v_ma_val if v_ma_val > 0 else 0
        
        # If current volume is already high, use that. Otherwise use previous to avoid "start-of-candle" penalty.
        final_vol_rel = max(v_curr_rel, v_prev / v_ma_val if v_ma_val > 0 else 0)
        
        # 4. Bollinger Bands (Overextension Detection)
        bb = ta.bbands(df_clean['close'], length=20, std=2)
        _bb_u = 0; _bb_m = 0; _bb_l = 0
        if bb is not None and not bb.empty:
            # Column names vary: BBU_20_2.0, BBM_20_2.0, BBL_20_2.0
            u_cols = [c for c in bb.columns if 'BBU' in c]
            m_cols = [c for c in bb.columns if 'BBM' in c]
            l_cols = [c for c in bb.columns if 'BBL' in c]
            if u_cols: _bb_u = bb[u_cols[0]].iloc[-1]
            if m_cols: _bb_m = bb[m_cols[0]].iloc[-1]
            if l_cols: _bb_l = bb[l_cols[0]].iloc[-1]

        return {
            "rsi": round(float(_rsi), 1), 
            "adx": round(float(_adx), 1), 
            "vol_rel": round(float(final_vol_rel), 2),
            "bb_u": round(float(_bb_u), 5),
            "bb_m": round(float(_bb_m), 5),
            "bb_l": round(float(_bb_l), 5)
        }
    except Exception as e:
        logger.debug(f"Error in get_mtr_data: {e}")
        return None

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
        
        block_reasons = [] # Trackers de por qué no operamos (Fase 55+)
        
        # OVERRIDES
        if asset_class == "INDEX":
            P_ADX_THR = 35       # Indices need more strength to avoid noise
            P_VOL_MULT = 2.0     # Volume must be clearer
            P_H1_PENALTY = 20    # Respect H1 trend more
        elif asset_class == "METAL":
            P_ATR_MARGIN = 0.20  # Gold wicks are deadly, require 20% breakout
        elif asset_class == "CRYPTO":
            P_ADX_THR = 35       # Crypto needs clear trend (volatile noise)
            P_ATR_MARGIN = 0.35  # Require deeper breakout to avoid whipsaws
            P_VOL_MULT = 1.8     # Volume burst must be significant
            P_H1_PENALTY = 25    # Respect H1 trend strictly
            
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
        current_atr = atr_series.iloc[-1] if atr_series is not None and len(atr_series) > 0 else 0
        min_break_dist = current_atr * P_ATR_MARGIN # DYNAMIC THRESHOLD

        # 3. DUAL-WINDOW SCORING ENGINE (M5 + M15 Cascading Triggers)
        bull_base = 0
        bear_base = 0
        breakdown = {}
        internal_gate_failed = False # Base state
        
        # Consistent UI Grouping
        # Keys: ESTADO, ESTRUCTURA, SETUP, FUERZA, VOLUMEN, MOMENTO, VALIDACION, ADVERTENCIAS
        factor_groups = {
            "LAYOUT_TOP_M5": None,
            "LAYOUT_TOP_M15": None,
            "ESTADO": None,
            "ESTRUCTURA": None,
            "SETUP_GENERIC": None,
            "FUERZA": [],
            "VOLUMEN": [],
            "MOMENTO": [],
            "VALIDACION": [],
            "ADVERTENCIAS": []
        }
        
        last_event_idx = 99
        best_setup = None # Persistent tracker for any detected setup
        
        # Prep M15 EMAs for scanning
        m15_ema21_s = ta.ema(df_m15['close'], length=21) if df_m15 is not None else None
        m15_ema50_s = ta.ema(df_m15['close'], length=50) if df_m15 is not None else None

        # CONFIG: [Timeframe Name, Current DF, EMAs (21,50), Window Size]
        # User Req: "Esperar a que entre volumen". We extend window to catch breaks up to 12 bars ago (1h).
        tf_configs = [
            ("M5", df, ema21_s, ema50_s, 3), # User Req: Compacted from 5 to 3 (Anti-Late)
            ("M15", df_m15, m15_ema21_s, m15_ema50_s, 3) # Compacted from 5 to 3
        ]

        for tf_name, tf_df, tf_ema21, tf_ema50, window in tf_configs:
            if tf_df is None or tf_ema21 is None or len(tf_df) < 55: continue
            
            # Optimization: Calculate momentum metrics once per Timeframe
            mtr_now = get_mtr_data(tf_df, tf_minutes=(5 if tf_name == "M5" else 15))
            has_momentum_ignition = mtr_now and mtr_now['vol_rel'] > 1.2 and mtr_now['adx'] > 25

            for i in range(window): 
                idx = len(tf_df) - 1 - i
                if idx < 1: break
                
                prev_idx = idx - 1
                p_close = tf_df['close'].iloc[prev_idx]
                c_close = tf_df['close'].iloc[idx]
                c_open = tf_df['open'].iloc[idx]
                
                c_ema21 = tf_ema21.iloc[idx]
                c_ema50 = tf_ema50.iloc[idx]
                p_ema21 = tf_ema21.iloc[prev_idx]
                p_ema50 = tf_ema50.iloc[prev_idx]
                
                # --- CUMULATIVE DECAY LOGIC ---
                # A. BASE SETUP POINTS
                # If we find a setup, we start at 45 points (Alerta).
                tf_mult = 1.2 if tf_name == "M15" else 1.0
                # User New Req: Strict timing. -10 pts per candle.
                action_pts = max(0, 45 - (i * 10)) * tf_mult
                
                # B. MOMENTUM IGNITION (User Req) - DISABLED for M5 to avoid late entries
                if has_momentum_ignition and tf_name == "M15":
                    action_pts = 45 * tf_mult
                
                # C. OVEREXTENSION FILTER (Anti-Late Entry)
                dist_ema50 = abs(c_close - c_ema50)
                # User Req: Tightened from 1.5 ATR to 1.2 ATR
                max_dist = current_atr * 1.2 
                if dist_ema50 > max_dist:
                    action_pts = 0 # Too far, opportunity lost
                
                # D. FRESHNESS PENALTY (Strict Immediate Signals)
                if i > 1: # More than 1 bar of delay
                    action_pts -= (i * 15)
                
                cross_pts = max(0, 45 - (i * 15)) * tf_mult
                
                # Event storage (for logging/detailed view)
                base_event = None
                
                # A. CROSSOVER
                if p_ema21 <= p_ema50 and c_ema21 > c_ema50: # Golden
                    if cross_pts > bull_base:
                         bull_base = cross_pts
                         base_event = {"k": f"G. Cross {tf_name} (-{i})", "v": "LONG Confirmado", "score": round(cross_pts)}
                elif p_ema21 >= p_ema50 and c_ema21 < c_ema50: # Death
                    if cross_pts > bear_base:
                         bear_base = cross_pts
                         base_event = {"k": f"D. Cross {tf_name} (-{i})", "v": "SHORT Confirmado", "score": round(bear_base)}
                    
                # B. BREAKOUT / BOUNCE
                c_high = tf_df['high'].iloc[idx]; c_low = tf_df['low'].iloc[idx]
                body_size = abs(c_close - c_open)
                full_range = max(0.00001, c_high - c_low)
                body_ratio = body_size / full_range
                
                avg_body = tf_df['close'].diff().abs().iloc[idx-5:idx].mean() if len(tf_df) > 10 else 0
                
                # --- MEJORA 1: FILTRO DINÁMICO DE VOLATILIDAD (ATR) ---
                # La vela de señal debe tener 'intención', es decir, su cuerpo debe ser relevante comparado con el ATR actual.
                # Threshold: Cuerpo > 60% del ATR actual (o del promedio local si ATR no disponible)
                atr_ref = current_atr if tf_name == "M5" else (current_atr * 2) # Aproximate ATR for M15
                min_body_size = atr_ref * 0.6 
                
                # Si el ATR es 0 (error de datos), usamos el promedio de las ultimas 5 velas
                if min_body_size == 0: min_body_size = avg_body * 1.1

                # Requisito de Cuerpo Sólido (Ratio Cuerpo/Rango > 60% para Crypto)
                min_body_ratio = 0.6 if asset_class == "CRYPTO" else 0.5
                is_strong_candle = body_size > min_body_size and body_ratio > min_body_ratio
                
                # --- NEW TRUTH TABLE LOGIC MAP (User Approved) ---
                
                # Context Definitions
                is_bull_trend = c_ema21 > c_ema50
                is_bear_trend = c_ema21 < c_ema50
                
                # Stability Filter (Origin Check)
                # Confirm we came from the "correct" side 5 bars ago for re-entries AND reversals
                origin_5_close = tf_df['close'].iloc[max(0, idx-5)]
                origin_5_ema21 = tf_ema21.iloc[max(0, idx-5)]
                
                # 1. EMA50 Setup (TYPE A - Reversal / Breakout)
                is_breaking_ema50_up = p_close < p_ema50 and c_close > (p_ema50 + min_break_dist)
                is_breaking_ema50_down = p_close > p_ema50 and c_close < (p_ema50 - min_break_dist)

                # 2. EMA21 Setup (TYPE B - Continuation / Breakout)
                is_breaking_ema21_up = p_close < p_ema21 and c_close > (p_ema21 + min_break_dist)
                is_breaking_ema21_down = p_close > p_ema21 and c_close < (p_ema21 - min_break_dist)

                # --- NEW: DISTANCE FROM BREAKOUT FILTER ---
                # Check how far the current price is from the level where the breakout occurred
                setup_level = 0
                if is_breaking_ema50_up or is_breaking_ema50_down: setup_level = p_ema50
                elif is_breaking_ema21_up or is_breaking_ema21_down: setup_level = p_ema21
                
                if setup_level > 0:
                    curr_price_now = tf_df['close'].iloc[-1]
                    price_gap = abs(curr_price_now - setup_level)
                    if price_gap > (current_atr * 0.7):
                        action_pts -= 20 # Severe penalty for chasing the price

                if is_breaking_ema50_up and is_strong_candle and is_bear_trend:
                     if action_pts > bull_base:
                          bull_base = action_pts
                          if action_pts == 0:
                              base_event = {"k": f"Giro EMA50 {tf_name}", "v": "Perdido (Sobreetendido)", "score": 0}
                          else:
                              base_event = {"k": f"Giro EMA50 {tf_name}", "v": f"LONG Reversal (Type A) {'(-'+str(i)+')' if i>0 else ''}", "score": round(bull_base)}
                     if i < last_event_idx: last_event_idx = i

                elif is_breaking_ema50_down and is_strong_candle and is_bull_trend:
                     if action_pts > bear_base:
                          bear_base = action_pts
                          if action_pts == 0:
                              base_event = {"k": f"Giro EMA50 {tf_name}", "v": "Perdido (Sobreetendido)", "score": 0}
                          else:
                              base_event = {"k": f"Giro EMA50 {tf_name}", "v": f"SHORT Reversal (Type A) {'(-'+str(i)+')' if i>0 else ''}", "score": round(bear_base)}
                     if i < last_event_idx: last_event_idx = i

                elif is_breaking_ema21_up and is_strong_candle and action_pts > 0:
                     # BUY SIGNAL
                     if is_bull_trend and origin_5_close < origin_5_ema21:
                          # TYPE B: Trend Resumption
                          pts = action_pts - 10
                          if pts > bull_base:
                              bull_base = pts
                              base_event = {"k": f"Breakout EMA21 {tf_name}", "v": "Trend (Type B)", "score": round(bull_base)}
                          if i < last_event_idx: last_event_idx = i

                elif is_breaking_ema21_down and is_strong_candle and action_pts > 0:
                     # SELL SIGNAL
                     if is_bear_trend and origin_5_close > origin_5_ema21:
                          # TYPE B: Trend Resumption
                          pts = action_pts - 10
                          if pts > bear_base:
                              bear_base = pts
                              base_event = {"k": f"Breakout EMA21 {tf_name}", "v": "Trend (Type B)", "score": round(bear_base)}
                          if i < last_event_idx: last_event_idx = i

                
                # Injection
                if base_event:
                    # --- NEW: STRICT INVALIDATION CHECK (System Integrity) ---
                    curr_c = tf_df['close'].iloc[-1]
                    curr_ema21 = tf_ema21.iloc[-1]
                    
                    is_long_setup = "LONG" in base_event['v'] or "Trend (Type B)" in base_event['v'] and bull_base > bear_base
                    is_short_setup = "SHORT" in base_event['v'] or "Trend (Type B)" in base_event['v'] and bear_base > bull_base
                    
                    if is_long_setup and curr_c < curr_ema21:
                         base_event['v'] = f"~~{base_event['v']}~~ (Invalido: Precio < EMA21)"
                         base_event['score'] = 0
                         if "LONG" in base_event['v']: bull_base = 0
                    elif is_short_setup and curr_c > curr_ema21:
                         base_event['v'] = f"~~{base_event['v']}~~ (Invalido: Precio > EMA21)"
                         base_event['score'] = 0
                         if "SHORT" in base_event['v']: bear_base = 0

                    if base_event['score'] > 0:
                        best_setup = base_event 
                        # Store in TF-Specific Top Row if it's an EMA50 Reversal
                        if "EMA50" in base_event['k']:
                            factor_groups[f"LAYOUT_TOP_{tf_name}"] = base_event
                        else:
                            factor_groups["SETUP_GENERIC"] = base_event
                    else:
                        # Even if invalidated, keep it in the Top Row for layout stability if it's an EMA50 event
                        if "EMA50" in base_event['k']:
                            factor_groups[f"LAYOUT_TOP_{tf_name}"] = base_event
                        factor_groups["ADVERTENCIAS"].append(base_event)

        mtr_m1 = get_mtr_data(data_input.get('m1'), tf_minutes=1) if isinstance(data_input, dict) else None
        mtr_m5 = get_mtr_data(df, tf_minutes=5)
        mtr_m15 = get_mtr_data(df_m15, tf_minutes=15)
        mtr_h1 = get_mtr_data(data_input.get('h1'), tf_minutes=60) if isinstance(data_input, dict) else None
        
        # --- NEW: SESSION & VSA ANALYSIS (FASE 55) ---
        from ..utils.tech_utils import get_market_session, detect_absorption
        session_name = "UNKNOWN"
        if isinstance(data_input, dict) and 'time' in df.columns:
            last_dt = pd.to_datetime(df['time'].iloc[-1], unit='s', utc=True)
            session_name = get_market_session(last_dt)
        
        abs_type, is_climax = detect_absorption(df)
        
        # --- MTF MOMENTUM (ADX DYNAMIC V2) ---
        adx_pts = 0
        adx_desc = []
        m5_adx = mtr_m5['adx'] if mtr_m5 else 0
        if mtr_m5: adx_desc.append(f"M5:{m5_adx:.1f}")
        if mtr_m15: adx_desc.append(f"M15:{mtr_m15['adx']:.1f}")
        if mtr_h1: adx_desc.append(f"H1:{mtr_h1['adx']:.1f}")
        
        # A. M5 Sliding Scale
        if m5_adx < 15: adx_pts -= 20 # Bloqueo por falta de tendencia
        elif 15 <= m5_adx < 21: adx_pts -= 10 # Debilidad
        elif 21 <= m5_adx < 26: adx_pts += 5  # Aceptable
        elif 26 <= m5_adx < 35: adx_pts += 20 # Bueno
        else: adx_pts += 35 # Muy fuerte

        # B. Macro Context Bonus
        if mtr_m15 and mtr_m15['adx'] >= 25: adx_pts += 10
        if mtr_h1 and mtr_h1['adx'] >= 25: adx_pts += 15

        adx_status = "Fuerte" if adx_pts >= 25 else ("Baja" if adx_pts < 0 else "Neutral")
        if m5_adx < 15: adx_status = "CRÍTICO (<15)"

        factor_groups["FUERZA"] = {
            "k": "Fuerza MTF (ADX)", 
            "v": f"{adx_status} ({' | '.join(adx_desc)})", 
            "score": adx_pts
        }

        adx_gate = m5_adx >= (P_ADX_THR - 5) 
        if not adx_gate:
            internal_gate_failed = True
            reason = f"ADX insuficiente ({m5_adx:.1f} < {P_ADX_THR-5})"
            factor_groups["ADVERTENCIAS"].append({"k": "BLOQUEO ADX", "v": reason, "score": -20})
            block_reasons.append(reason)

        # --- MTF VOLUME (DYNAMIC V2) ---
        vol_pts = 0
        vol_desc = []
        m5_vol = mtr_m5['vol_rel'] if mtr_m5 else 0
        if mtr_m5: vol_desc.append(f"M5:{m5_vol:.1f}x")
        if mtr_m15: vol_desc.append(f"M15:{mtr_m15['vol_rel']:.1f}x")
        
        # A. M5 Sliding Scale
        if m5_vol < 0.6: vol_pts -= 15 # Bloqueo por mercado muerto
        elif 0.6 <= m5_vol < 0.9: vol_pts -= 5
        elif 0.9 <= m5_vol < 1.3: vol_pts += 5
        elif m5_vol >= 1.3: vol_pts += 20

        # B. Institutional Burst Bonus (Macro)
        h1_vol = mtr_h1['vol_rel'] if mtr_h1 else 0
        if h1_vol >= 1.5: vol_pts += 15 # H1 fuerte compensa M5 debil
        if mtr_m15 and mtr_m15['vol_rel'] >= 1.5: vol_pts += 10

        vol_status = "Alto" if vol_pts >= 20 else ("Bajo" if vol_pts < 0 else "Normal")
        if m5_vol < 0.6: vol_status = "MUERTO (<0.6x)"

        factor_groups["VOLUMEN"] = {
            "k": "Volumen MTF", 
            "v": f"{vol_status} ({' | '.join(vol_desc)})", 
            "score": vol_pts
        }

        vol_gate = m5_vol >= 0.8 or h1_vol >= 1.5 
        if not vol_gate:
            internal_gate_failed = True
            reason = f"Sin interés inst. (M5 Vol:{m5_vol:.1f}x)"
            factor_groups["ADVERTENCIAS"].append({"k": "BLOQUEO VOLUMEN", "v": reason, "score": -20})
            block_reasons.append(reason)
            
        # --- SESSION MOMENTUM (FASE 55) ---
        # London y NY/Overlap son los reyes de la tendencia. Asian castiga.
        session_pts = 0
        if session_name in ["OVERLAP", "LONDON", "NY"]:
            session_pts = 10
            factor_groups["MOMENTO"] = [{"k": "Plaza Operativa", "v": f"Alta Liquidez ({session_name})", "score": session_pts}]
        elif session_name == "ASIAN":
            session_pts = -25
            factor_groups["MOMENTO"] = [{"k": "Plaza Operativa", "v": "Baja Liquidez (ASIAN)", "score": session_pts}]
        else:
            factor_groups["MOMENTO"] = []

        # --- VSA INSTITUTIONAL CLIMAX (FASE 55) ---
        vsa_pts = 0
        if abs_type:
            # We don't know the entry direction yet until net_score comparison, but VSA is universally powerful. 
            # We will award points conditionally below. Keep record.
            pass

        # --- PRELIMINARY SCORE CALCULATION ---
        bull_score = bull_base
        bear_score = bear_base
        net_score = 0
        direction = 0 
        is_passive = False
        
        if bull_score > bear_score:
            net_score = bull_score
            direction = 1
            breakdown["Sesgo Técnico"] = f"Alcista ({bull_score})"
        elif bear_score > bull_score:
            net_score = bear_score
            direction = -1
            breakdown["Sesgo Técnico"] = f"Bajista ({bear_score})"
        else:
            # PASSIVE TREND CHECK (Structure Maintenance)
            c_ema21 = ema21_s.iloc[-1]
            c_ema50 = ema50_s.iloc[-1]
            c_close = df['close'].iloc[-1]
            
            p_dir = 0
            if c_ema21 > c_ema50 and c_close > c_ema21: p_dir = 1
            elif c_ema21 < c_ema50 and c_close < c_ema21: p_dir = -1

            if p_dir != 0:
                is_passive = True
                direction = p_dir
                p_type = "Alcista" if p_dir == 1 else "Bajista"
                net_score = 30 # Base Passive Score (Structure only) - UP FROM 25
                factor_groups["ESTRUCTURA"] = {"k": "Estructura", "v": f"Tendencia {p_type} (Pasiva)", "score": 30}
                breakdown["Estructura"] = f"Tendencia {p_type} (+30)"
                factor_groups["ESTADO"] = {"k": "Sin Setup", "v": "Tendencia en Curso", "score": 0}
            else:
                # If truly neutral, still provide some context
                net_score = 0
                direction = 0
                factor_groups["ESTADO"] = {"k": "Estado", "v": "Lateral / Indefinido", "score": 0}
                factor_groups["ESTRUCTURA"] = {"k": "Estructura", "v": "Sin tendencia clara", "score": 0}

        if not factor_groups["ESTADO"]:
            if best_setup:
                # Update status with setup found
                factor_groups["ESTADO"] = {"k": "Estado", "v": "Alerta: Setup Activo", "score": round(net_score)}
            else:
                factor_groups["ESTADO"] = {"k": "Estado", "v": "Estudiando Mercado", "score": 0}
        
        if not factor_groups["ESTRUCTURA"]:
             p_type = "Alcista" if direction == 1 else "Bajista"
             factor_groups["ESTRUCTURA"] = {"k": "Estructura", "v": f"Sesgo {p_type}", "score": 0}
             
        # Eval VSA (Now we know the direction)
        if direction != 0 and abs_type:
            abs_match = (direction == 1 and abs_type == "BUY_ABS") or (direction == -1 and abs_type == "SELL_ABS")
            if abs_match:
                 vsa_pts = 35 if is_climax else 15
                 txt = "CLÍMAX INSTITUCIONAL" if is_climax else "Absorción a Favor"
                 factor_groups["MOMENTO"].append({"k": "VSA Trigger", "v": txt, "score": vsa_pts})

        # --- UNIFIED FILTER EVALUATION ---
        # Ensure we always sum bonuses
        net_score += adx_pts
        net_score += vol_pts
        net_score += session_pts
        net_score += vsa_pts

        # 1. STICKY ALERT: If we have a setup, ensure score doesn't fall below a visible threshold
        if best_setup and net_score < 40 and not internal_gate_failed:
             net_score = 40 # Minimum "Alert" visibility

        # 1. MTF FINAL GATES (Conditional Penalties)
        # If we have a SETUP (best_setup exists), we DON'T subtract for weak ADX/VOL, 
        # so the alert stays yellow (40-60 pts) while waiting.
        if not best_setup:
            if adx_pts < 15: 
                penalty = 10 # REDUCED FROM 15
                net_score -= penalty
                factor_groups["VALIDACION"].append({"k": "Fuerza Estructural", "v": "ADX < 15", "score": -penalty})
            
            if vol_pts == 0:
                penalty = 5 # REDUCED FROM 10
                net_score -= penalty
                factor_groups["VALIDACION"].append({"k": "Volumen Estructural", "v": "Neutro", "score": -penalty})

        # 2a. SLOPE FILTER
        ema50_slope = (ema50_s.iloc[-1] - ema50_s.iloc[-6]) / ema50_s.iloc[-6] * 100 if len(ema50_s) > 6 else 0
        if abs(ema50_slope) < 0.005: 
             penalty = 10 if is_passive else 15
             factor_groups["ADVERTENCIAS"].append({"k": "Curvatura EMA50", "v": "Pendiente Lateral", "score": -penalty})
             net_score -= penalty
        else:
             factor_groups["ADVERTENCIAS"].append({"k": "Curvatura EMA50", "v": "Pendiente OK", "score": 0})

        # --- MEJORA 2: DETECCIÓN DE "SANDWICH" (ZONA DE TRAMPA) ---
        # Si tenemos EMA200, verificar si estamos atrapados entre EMA50 y EMA200
        if len(df) > 200:
             try:
                 ema200_val = ta.ema(df['close'], length=200).iloc[-1]
                 c_close = df['close'].iloc[-1]
                 c_ema50 = ema50_s.iloc[-1]
                 
                 # Definir Zona Sucia: Precio entre EMA50 y EMA200
                 in_sandwich = (c_close > min(c_ema50, ema200_val)) and (c_close < max(c_ema50, ema200_val))
                 # Solo es grave si están cerca (compresión)
                 dist_emas = abs(c_ema50 - ema200_val)
                 is_compressed = dist_emas < (current_atr * 3)
                 
                 if in_sandwich and is_compressed:
                     trap_penalty = 30
                     net_score -= trap_penalty
                     factor_groups["VALIDACION"].append({"k": "Zona de Trampa", "v": "Sandwich EMA50/200", "score": -trap_penalty})
             except: pass

        # 3. HTF ALIGNMENT (M15)
        if direction == 1 and htf_filter == -1:
            factor_groups["ESTRUCTURA"] = {"k": "Estructura", "v": "Contratendencia M15", "score": -20}
            net_score -= 20
        elif direction == -1 and htf_filter == 1:
            factor_groups["ESTRUCTURA"] = {"k": "Estructura", "v": "Contratendencia M15", "score": -20}
            net_score -= 20
        elif htf_filter != 0:
            net_score += 15
            factor_groups["ESTRUCTURA"] = {"k": "Estructura", "v": "M15 Confirmada", "score": 15}

        # 4. H1 FRICTION (FASE 55 - FRACTAL ALIGNMENT)
        df_h1 = data_input.get('h1') if isinstance(data_input, dict) else None
        if mtr_h1 and df_h1 is not None and len(df_h1) > 50:
             h1_ema50 = ta.ema(df_h1['close'], length=50).iloc[-1]
             h1_close = df_h1['close'].iloc[-1]
             h1_trend = 1 if h1_close > h1_ema50 else -1
             h1_adx = mtr_h1['adx'] if 'adx' in mtr_h1 else 0
             
             is_type_a_reversal = base_event and "Type A" in base_event.get("v", "")
             
             if direction != h1_trend:
                 # Si la tendencia contraria de H1 es MUY fuerte (ADX > 30), bloqueo absoluto
                 if h1_adx > 30:
                     internal_gate_failed = True
                     factor_groups["VALIDACION"].append({"k": "Bloqueo Fractal H1", "v": f"Imparable en contra (ADX {h1_adx:.1f})", "score": -50})
                     net_score -= 50
                 elif is_type_a_reversal:
                     factor_groups["VALIDACION"].append({"k": "Fricción H1 (Filtro)", "v": "Ignorada (Giro Tipo A)", "score": 0})
                 else:
                      penalty_h1 = P_H1_PENALTY
                      net_score -= penalty_h1
                      factor_groups["VALIDACION"].append({"k": "Momento H1", "v": "Contratendencia Débil", "score": -penalty_h1})

        # 4b. VOLUME CONFIRMATION (Institutional Backing)
        # User Req: Crisis WR Fix - Require at least 1.2 relative volume
        vol_rel = mtr_m5['vol_rel'] if mtr_m5 else 0
        if vol_rel < 1.2:
            vol_penalty = 20
            net_score -= vol_penalty
            factor_groups["MOMENTO"].append({"k": "Volumen Pobre", "v": f"Relativo ({vol_rel:.2f} < 1.2)", "score": -vol_penalty})
        else:
            net_score += 10
            factor_groups["MOMENTO"].append({"k": "Volumen Confirmado", "v": f"Relativo ({vol_rel:.2f})", "score": 10})

        # 5. FRESHNESS
        if not is_passive and last_event_idx == 0: 
            net_score += 10
            factor_groups["SETUP"] = [factor_groups.get("SETUP", {}), {"k": "Inmediatez", "v": "Evento Reciente", "score": 10}]
        
        # 6. RSI MOMENTUM
        curr_rsi = mtr_m5['rsi'] if mtr_m5 else 50
        if direction == 1:
             if 50 < curr_rsi < 65:  # Tightened from 75 to 65
                 net_score += 15
                 factor_groups["MOMENTO"].append({"k": "Impulso RSI", "v": f"Alcista ({curr_rsi:.1f})", "score": 15})
             else:
                 factor_groups["MOMENTO"].append({"k": "Zona RSI", "v": f"Agotamiento/Debilidad ({curr_rsi:.1f})", "score": -15})
                 net_score -= 15
        elif direction == -1:
             if 35 < curr_rsi < 50: # Tightened from 25 to 35
                 net_score += 15
                 factor_groups["MOMENTO"].append({"k": "Impulso RSI", "v": f"Bajista ({curr_rsi:.1f})", "score": 15})
             else:
                 factor_groups["MOMENTO"].append({"k": "Zona RSI", "v": f"Agotamiento/Debilidad ({curr_rsi:.1f})", "score": -15})
                 net_score -= 15

        # 7. PRICE VS EMAS (VALIDATION)
        latest_c = df['close'].iloc[-1]
        latest_ema21 = ema21_s.iloc[-1]
        latest_ema50 = ema50_s.iloc[-1]
        pre_penalty_score = net_score
        
        internal_gate_failed = (not adx_gate) or (not vol_gate)

        if direction == 1:
            fail_ema21 = latest_c <= latest_ema21
            fail_ema50 = (latest_ema50 > latest_ema21) and (latest_c <= latest_ema50)
            
            if fail_ema21 or fail_ema50:
                internal_gate_failed = True
                reduction = 0.5 if is_passive else 0.7
                net_score = net_score * (1 - reduction)
                penalty = round(pre_penalty_score - net_score)
                cause = "Cierre < EMA21" if fail_ema21 else "Zona de Trampa (Sandwich EMA50)"
                factor_groups["VALIDACION"].append({"k": "Validación", "v": cause, "score": -penalty})
            
            dist_ema50 = (latest_c - latest_ema50) / latest_ema50 * 100
            if dist_ema50 > 1.0:
                internal_gate_failed = True
                factor_groups["ADVERTENCIAS"].append({"k": "Exceso", "v": "Sobre-extendido (EMA)", "score": -40})
                net_score -= 40
            
            # --- NEW: Bollinger Overextension (GOOG Fix) ---
            bb_u = mtr_m5.get('bb_u', 0) if mtr_m5 else 0
            if bb_u > 0 and latest_c >= bb_u:
                internal_gate_failed = True # No bloquea pero penaliza fuertemente para evitar tops
                reason = "Agotamiento (Techo Bollinger)"
                factor_groups["ADVERTENCIAS"].append({"k": "Exceso BB", "v": reason, "score": -35})
                block_reasons.append(reason)
                net_score -= 35
        elif direction == -1:
            fail_ema21 = latest_c >= latest_ema21
            fail_ema50 = (latest_ema50 < latest_ema21) and (latest_c >= latest_ema50)

            if fail_ema21 or fail_ema50:
                internal_gate_failed = True
                reduction = 0.5 if is_passive else 0.7
                net_score = net_score * (1 - reduction)
                penalty = round(pre_penalty_score - net_score)
                cause = "Cierre > EMA21" if fail_ema21 else "Zona de Trampa (Sandwich EMA50)"
                factor_groups["VALIDACION"].append({"k": "Validación", "v": cause, "score": -penalty})
            
            dist_ema50 = (latest_ema50 - latest_c) / latest_ema50 * 100
            if dist_ema50 > 1.0:
                internal_gate_failed = True
                factor_groups["ADVERTENCIAS"].append({"k": "Exceso", "v": "Sobre-extendido (EMA)", "score": -40})
                net_score -= 40

            # --- NEW: Bollinger Overextension (GOOG Fix) ---
            bb_l = mtr_m5.get('bb_l', 0) if mtr_m5 else 0
            if bb_l > 0 and latest_c <= bb_l:
                internal_gate_failed = True
                reason = "Agotamiento (Suelo Bollinger)"
                factor_groups["ADVERTENCIAS"].append({"k": "Exceso BB", "v": reason, "score": -35})
                block_reasons.append(reason)
                net_score -= 35

        # --- FINAL DECISION ---
        score = min(100, max(0, round(net_score)))
        entry = 0
        THRESHOLD = 75 
        
        # --- NEW: HARD EXHAUSTION FILTERS (Anti-Chasing & Hard RSI) ---
        # User Req: Bloqueo rígido para evitar entradas tardías (late-entries).
        curr_rsi = mtr_m5['rsi'] if mtr_m5 else 50
        latest_c = df['close'].iloc[-1]
        latest_ema21 = ema21_s.iloc[-1]
        dist_ema21 = abs(latest_c - latest_ema21)
        
        # Filtro 1: RSI Extremo (Hard Block)
        rsi_exhausted = (direction == 1 and curr_rsi > 70) or (direction == -1 and curr_rsi < 30)
        
        # Filtro 2: Anti-Chasing (Distancia excesiva a la media rápida)
        # Convertimos is_chasing de MODO BLOQUEO a MODO STALKING
        is_chasing = dist_ema21 > (current_atr * 1.2)
        
        if direction != 0:
            if rsi_exhausted:
                internal_gate_failed = True
                cause = "RSI > 70" if direction == 1 else "RSI < 30"
                factor_groups["ADVERTENCIAS"].append({"k": "BLOQUEO RSI", "v": f"Agotamiento ({cause})", "score": -50})
            
            if is_chasing:
                # Ya NO fallamos el internal_gate. Pasamos el score, pero habilitamos is_chasing para triggerear el STALKING posterior.
                factor_groups["ADVERTENCIAS"].append({"k": "PULLBACK REQUERIDO", "v": f"Dist. EMA21 {dist_ema21/current_atr:.1f} ATR", "score": -10})
                net_score -= 10 # Pequeña penalización por esperar, pero mantiene el score alto (>80) para que el user vea el setup

        # ENTRY RULES: Score >= 75 AND not passive AND gates passed
        is_stalking = False
        
        if score >= THRESHOLD and not is_passive and not internal_gate_failed:
             if is_chasing:
                 # En lugar de bloquear la entrada bruscamente, el bot se queda "acechando" un pullback
                 is_stalking = True
                 entry = 0 # No disparamos aun
                 factor_groups["ESTADO"] = {"k": "Estado", "v": "ACECHANDO PULLBACK", "score": 0}
                 block_reasons.append(f"Esperando Pullback EMA21 ({dist_ema21/current_atr:.1f} ATR)")
                 logger.info(f"🐺 [EMA STALKING] {symbol} Score={score}% - Setup OK pero esperando pullback a EMA21 (Dist: {dist_ema21/current_atr:.1f} ATR). No se dispara aun.")
             else:
                 entry = direction
                 factor_groups["ESTADO"] = {"k": "Estado", "v": "ORDEN ACTIVADA", "score": 0}
                 logger.info(f"⚡ [EMA ENTRY] {symbol} Score={score}% - Disparando orden {'+1=BUY' if direction==1 else '-1=SELL'}")
        elif is_passive:
            factor_groups["ESTADO"] = {"k": "Estado", "v": "Mantenimiento Pasivo", "score": 0}
            if score >= 70: logger.info(f"🚫 [EMA BLOQUEADO-PASIVO] {symbol} Score={score}% - Tendencia pasiva, sin setup activo")
        elif internal_gate_failed:
            factor_groups["ESTADO"] = {"k": "Estado", "v": "Bloqueo por Seguridad", "score": 0}
            if score >= 70: logger.info(f"🚫 [EMA BLOQUEADO-GATE] {symbol} Score={score}% - Gate de seguridad activo (RSI/ADX/H1/BB). Ver breakdown.")

        # --- CAP VISUAL: Si no hay entrada real, el score no puede superar 74 ---
        # Razón: Un score >= 75 en el frontend indica "LISTO PARA OPERAR".
        # Si estamos en STALKING, PASIVO o con gate, cappear para no confundir.
        if entry == 0:
            score = min(score, 74)
            # Inyectar motivo principal de bloqueo en el UI si el score es alto
            if score >= 70 or internal_gate_failed or is_stalking:
                txt_reason = ", ".join(block_reasons) if block_reasons else ("Sin setup claro" if not is_passive else "Mantenimiento pasivo")
                factor_groups["BLOCKER"] = {"k": "MOTIVO DE BLOQUEO", "v": txt_reason, "score": 0}


        # --- CONSTRUCT FINAL FACTORS LIST (Explicit Layout & Unique) ---
        factors_detailed = []

        def add_unique_f(f_list, factor):
            if not factor: return
            # Avoid adding the same factor twice (by key and value)
            for existing in f_list:
                if existing['k'] == factor['k'] and existing['v'] == factor['v']:
                    return
            f_list.append(factor)

        # 1. ROW 1: M5 & M15 GIROS (Always present for structure)
        m5_ev = factor_groups.get("LAYOUT_TOP_M5") or {"k": "Giro EMA50 M5", "v": "Neutral", "score": 0}
        m15_ev = factor_groups.get("LAYOUT_TOP_M15") or {"k": "Giro EMA50 M15", "v": "Neutral", "score": 0}
        add_unique_f(factors_detailed, m5_ev)
        add_unique_f(factors_detailed, m15_ev)

        # 2. ROW 2: STATUS & STRUCTURE & BLOCKER
        st_ev = factor_groups.get("ESTADO") or {"k": "Estado", "v": "Analizando", "score": 0}
        str_ev = factor_groups.get("ESTRUCTURA") or {"k": "Estructura", "v": "Sincronizando", "score": 0}
        bl_ev = factor_groups.get("BLOCKER")
        
        add_unique_f(factors_detailed, st_ev)
        add_unique_f(factors_detailed, str_ev)
        if bl_ev: add_unique_f(factors_detailed, bl_ev)

        # 3. THE REST (Ordered)
        ordered_keys = ["FUERZA", "VOLUMEN", "SETUP_GENERIC", "MOMENTO", "VALIDACION", "ADVERTENCIAS"]
        for key in ordered_keys:
            val = factor_groups.get(key)
            if val:
                if isinstance(val, list):
                    for v in val: add_unique_f(factors_detailed, v)
                else:
                    add_unique_f(factors_detailed, val)

        atr = ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-1]
        for f in factors_detailed:
             breakdown[f['k']] = f"{f['v']} ({'+' if f['score'] >= 0 else ''}{f['score']})"

        # Mensaje de Estatus Principal
        status_msg = "Estudiando..."
        if is_passive:
             status_msg = "Esperando Setup (Pasivo)"
        elif internal_gate_failed:
             status_msg = "Filtrado (Seguridad)"
        elif is_stalking:
             status_msg = "Acechando Pullback (STALKING)"
        elif entry != 0:
             status_msg = "Setup Activo"

        metadata = {
            "strategy": self.STRATEGY_NAME,
            "score": score,
            "total_score": score,  # Now strictly the RAW score
            "score_breakdown": breakdown,
            "factors_detailed": factors_detailed,
            "can_entry": entry != 0,
            "gate_failed": internal_gate_failed or is_passive,
            "ema21": round(latest_ema21, 2),
            "ema50": round(latest_ema50, 2),
            "adx": round(mtr_m5['adx'] if mtr_m5 else 0, 1),
            "rsi": round(curr_rsi, 1),
            "bb_u": round(mtr_m5.get('bb_u', 0) if mtr_m5 else 0, 2),
            "bb_l": round(mtr_m5.get('bb_l', 0) if mtr_m5 else 0, 2),
            "status": status_msg,
            "direction": direction
        }

        return {
            "entry": entry,
            "atr": atr,
            "metadata": metadata,
            "score": score
        }

    def check_exit_signal(self, df, position_type):
        """
        Detecta si la tesis de tendencia se ha invalidado.
        Nueva Lógica V7: Cierre por EMA50 + Confirmación de Volumen y ADX (User Req).
        """
        if df is None or len(df) < 50: return False # Necesitamos datos para EMA50
        
        c_close = df['close'].iloc[-1]
        
        # 1. EMAs50 (Resistencia/Soporte Dinámico Institucional)
        ema50_s = ta.ema(df['close'], length=50)
        if ema50_s is None: return False
        c_ema50 = ema50_s.iloc[-1]
        
        # 2. ADX Direction (Tendencia perdiendo fuerza o girando)
        try:
            adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
            adx_curr = adx_df['ADX_14'].iloc[-1]
            adx_prev = adx_df['ADX_14'].iloc[-2]
            is_adx_falling = adx_curr < adx_prev
        except:
            is_adx_falling = False # Fallback conservador
            adx_curr = 0
        
        # 3. Volume Confirmation
        vol_s = df['tick_volume'] if 'tick_volume' in df else pd.Series([0]*len(df))
        vol_ma_s = ta.sma(vol_s, length=20)
        vol_curr = vol_s.iloc[-1]
        vol_ma = vol_ma_s.iloc[-1] if vol_ma_s is not None else 0
        is_high_vol = vol_curr > vol_ma
        
        # Lógica de Salida Robusta: Ruptura EMA50 Y Confirmación (Vol + ADX)
        # Para índices, añadimos un buffer de 1.5 puntos para evitar cierres por ruido prematuro.
        is_index = "EU50" in getattr(df, 'symbol', "") or "US500" in getattr(df, 'symbol', "")
        buffer = 1.5 if is_index else 0
        
        if position_type == "BUY":
            if c_close < (c_ema50 - buffer) and is_high_vol and is_adx_falling:
                logger.info(f"🛑 [EXIT BUY] Ruptura EMA50 con Vol ({vol_curr/vol_ma:.1f}x) y ADX Bajista. (Buffer: {buffer})")
                return True
        elif position_type == "SELL":
            if c_close > (c_ema50 + buffer) and is_high_vol and is_adx_falling:
                logger.info(f"🛑 [EXIT SELL] Recuperación EMA50 con Vol ({vol_curr/vol_ma:.1f}x) y ADX Bajista. (Buffer: {buffer})")
                return True
                
        return False
