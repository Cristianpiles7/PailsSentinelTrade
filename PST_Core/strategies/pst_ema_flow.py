import pandas_ta as ta
import pandas as pd
import numpy as np
import logging
from datetime import datetime, timezone
from ..models.classifier import RegimeMode

from ..utils.tech_utils import get_asset_class

# Helper for momentum metrics (Module Level to avoid scoping issues)
def get_mtr_data(df_in, tf_minutes=5):
    if df_in is None or len(df_in) < 20: return None
    try:
        _rsi = ta.rsi(df_in['close'], length=14).iloc[-1]
        _adx = ta.adx(df_in['high'], df_in['low'], df_in['close'], length=14)['ADX_14'].iloc[-1]
        
        # VOLUME LOGIC
        _v = df_in['tick_volume'].iloc[-1] if 'tick_volume' in df_in else 0
        _v_ma = ta.sma(df_in['tick_volume'], length=20).iloc[-1] if 'tick_volume' in df_in else 1
        
        # PRO-RATA PROJECTION (for current candle)
        if hasattr(df_in, 'index') and isinstance(df_in.index, pd.DatetimeIndex):
             last_time = df_in.index[-1]
             now_utc = datetime.now(timezone.utc)
             elapsed_sec = (now_utc - last_time).total_seconds()
             
             # If we are within the candle duration
             if 0 < elapsed_sec < (tf_minutes * 60):
                  ratio = (tf_minutes * 60) / elapsed_sec
                  # Cap projection at 3x current volume to avoid early noise
                  _v = _v * min(3.0, ratio)
        
        return {"rsi": _rsi, "adx": _adx, "vol_rel": _v / _v_ma if _v_ma > 0 else 0}
    except: return None

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
        current_atr = atr_series.iloc[-1] if atr_series is not None and len(atr_series) > 0 else 0
        min_break_dist = current_atr * P_ATR_MARGIN # DYNAMIC THRESHOLD

        # 3. DUAL-WINDOW SCORING ENGINE (M5 + M15 Cascading Triggers)
        bull_base = 0
        bear_base = 0
        breakdown = {}
        
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
            ("M5", df, ema21_s, ema50_s, 12), 
            ("M15", df_m15, m15_ema21_s, m15_ema50_s, 5) 
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
                # User Req: Slower decay to allow waiting for volume (i * 3 instead of 8)
                action_pts = max(0, 45 - (i * 3)) * tf_mult
                
                # B. MOMENTUM IGNITION (User Req)
                # If confirmation arrived within the window, restore base points to 45
                if has_momentum_ignition:
                    action_pts = 45 * tf_mult
                
                # C. OVEREXTENSION FILTER
                dist_ema50 = abs(c_close - c_ema50)
                max_dist = current_atr * 3.0
                if dist_ema50 > max_dist:
                    action_pts = 0 # Too far, opportunity lost
                
                cross_pts = max(0, 45 - (i * 5)) * tf_mult
                
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
                is_strong_candle = body_ratio > 0.6 and body_size > avg_body
                
                # --- NEW TRUTH TABLE LOGIC MAP (User Approved) ---
                
                # Context Definitions
                is_bull_trend = c_ema21 > c_ema50
                is_bear_trend = c_ema21 < c_ema50
                
                # Stability Filter (Origin Check)
                # Confirm we came from the "correct" side 5 bars ago for re-entries AND reversals
                origin_5_close = tf_df['close'].iloc[max(0, idx-5)]
                origin_5_ema21 = tf_ema21.iloc[max(0, idx-5)]
                
                # 1. EMA50 Setup (TYPE A - Reversal / Bounce)
                is_breaking_ema50_up = p_close < p_ema50 and c_close > (p_ema50 + min_break_dist)
                is_breaking_ema50_down = p_close > p_ema50 and c_close < (p_ema50 - min_break_dist)
                
                # NEW: Wick Bounce EMA50
                is_bouncing_ema50_up = is_bull_trend and c_low <= (c_ema50 + (current_atr * 0.1)) and c_close > c_ema50
                is_bouncing_ema50_down = is_bear_trend and c_high >= (c_ema50 - (current_atr * 0.1)) and c_close < c_ema50

                # 2. EMA21 Setup (TYPE B - Continuation / Bounce)
                is_breaking_ema21_up = p_close < p_ema21 and c_close > (p_ema21 + min_break_dist)
                is_breaking_ema21_down = p_close > p_ema21 and c_close < (p_ema21 - min_break_dist)
                
                # NEW: Wick Bounce EMA21
                is_bouncing_ema21_up = is_bull_trend and c_low <= (c_ema21 + (current_atr * 0.1)) and c_close > c_ema21
                is_bouncing_ema21_down = is_bear_trend and c_high >= (c_ema21 - (current_atr * 0.1)) and c_close < c_ema21

                if (is_breaking_ema50_up or is_bouncing_ema50_up) and is_strong_candle and is_bear_trend:
                     if action_pts > bull_base:
                          bull_base = action_pts
                          if action_pts == 0:
                              base_event = {"k": f"Giro EMA50 {tf_name}", "v": "Perdido (Sobreetendido)", "score": 0}
                          else:
                              etype = "Giro" if is_breaking_ema50_up else "Rebote"
                              base_event = {"k": f"{etype} EMA50 {tf_name}", "v": f"LONG Reversal (Type A) {'(-'+str(i)+')' if i>0 else ''}", "score": round(bull_base)}
                     if i < last_event_idx: last_event_idx = i

                elif (is_breaking_ema50_down or is_bouncing_ema50_down) and is_strong_candle and is_bull_trend:
                     if action_pts > bear_base:
                          bear_base = action_pts
                          if action_pts == 0:
                              base_event = {"k": f"Giro EMA50 {tf_name}", "v": "Perdido (Sobreetendido)", "score": 0}
                          else:
                              etype = "Giro" if is_breaking_ema50_down else "Rebote"
                              base_event = {"k": f"{etype} EMA50 {tf_name}", "v": f"SHORT Reversal (Type A) {'(-'+str(i)+')' if i>0 else ''}", "score": round(bear_base)}
                     if i < last_event_idx: last_event_idx = i

                elif (is_breaking_ema21_up or is_bouncing_ema21_up) and is_strong_candle and action_pts > 0:
                     # BUY SIGNAL
                     if is_bull_trend and origin_5_close < origin_5_ema21:
                          # TYPE B: Trend Resumption
                          pts = action_pts - 10
                          if pts > bull_base:
                              bull_base = pts
                              etype = "Ruptura" if is_breaking_ema21_up else "Rebote"
                              base_event = {"k": f"{etype} EMA21 {tf_name}", "v": "Trend (Type B)", "score": round(bull_base)}
                          if i < last_event_idx: last_event_idx = i

                elif (is_breaking_ema21_down or is_bouncing_ema21_down) and is_strong_candle and action_pts > 0:
                     # SELL SIGNAL
                     if is_bear_trend and origin_5_close > origin_5_ema21:
                          # TYPE B: Trend Resumption
                          pts = action_pts - 10
                          if pts > bear_base:
                              bear_base = pts
                              etype = "Ruptura" if is_breaking_ema21_down else "Rebote"
                              base_event = {"k": f"{etype} EMA21 {tf_name}", "v": "Trend (Type B)", "score": round(bear_base)}
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
        
        # --- MTF MOMENTUM (ADX DYNAMIC) ---
        adx_pts = 0
        adx_desc = []
        if mtr_m5: adx_desc.append(f"M5:{mtr_m5['adx']:.1f}")
        if mtr_m15: adx_desc.append(f"M15:{mtr_m15['adx']:.1f}")
        if mtr_h1: adx_desc.append(f"H1:{mtr_h1['adx']:.1f}")
        
        if mtr_m5 and mtr_m5['adx'] >= P_ADX_THR: adx_pts += 30
        if mtr_m15 and mtr_m15['adx'] >= 20: adx_pts += 15
        if mtr_h1 and mtr_h1['adx'] >= 20: adx_pts += 10
        if mtr_m1 and mtr_m1['adx'] >= 30: adx_pts += 5

        factor_groups["FUERZA"] = {
            "k": "Fuerza MTF (ADX)", 
            "v": f"{'OK' if adx_pts >= 30 else 'Baja'} ({' | '.join(adx_desc)})", 
            "score": adx_pts
        }

        adx_gate = adx_pts >= 30
        
        # --- MTF VOLUME (DYNAMIC) ---
        vol_pts = 0
        vol_desc = []
        if mtr_m5: vol_desc.append(f"M5:{mtr_m5['vol_rel']:.1f}x")
        if mtr_m15: vol_desc.append(f"M15:{mtr_m15['vol_rel']:.1f}x")
        
        if mtr_m5 and mtr_m5['vol_rel'] >= P_VOL_MULT: vol_pts += 30
        if mtr_m15 and mtr_m15['vol_rel'] >= 1.2: vol_pts += 20
        if mtr_h1 and mtr_h1['vol_rel'] >= 1.1: vol_pts += 10

        factor_groups["VOLUMEN"] = {
            "k": "Volumen MTF", 
            "v": f"{'Alto' if vol_pts >= 30 else 'Neutro'} ({' | '.join(vol_desc)})", 
            "score": vol_pts
        }

        vol_gate = vol_pts >= 10

        # --- PRELIMINARY SCORE CALCULATION ---
        bull_score = bull_base
        bear_score = bear_base
        net_score = 0
        direction = 0 
        is_passive = False
        internal_gate_failed = False # Safe Initialization
        
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

        # --- UNIFIED FILTER EVALUATION ---
        # Ensure we always sum bonuses
        net_score += adx_pts
        net_score += vol_pts

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

        # 2. SLOPE FILTER
        ema50_slope = (ema50_s.iloc[-1] - ema50_s.iloc[-6]) / ema50_s.iloc[-6] * 100 if len(ema50_s) > 6 else 0
        if abs(ema50_slope) < 0.005: 
             penalty = 10 if is_passive else 15
             factor_groups["ADVERTENCIAS"].append({"k": "Curvatura EMA50", "v": "Pendiente Lateral", "score": -penalty})
             net_score -= penalty
        else:
             factor_groups["ADVERTENCIAS"].append({"k": "Curvatura EMA50", "v": "Pendiente OK", "score": 0})

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

        # 4. H1 FRICTION
        df_h1 = data_input.get('h1') if isinstance(data_input, dict) else None
        if mtr_h1 and df_h1 is not None and len(df_h1) > 50:
             h1_ema50 = ta.ema(df_h1['close'], length=50).iloc[-1]
             h1_close = df_h1['close'].iloc[-1]
             h1_trend = 1 if h1_close > h1_ema50 else -1
             is_type_a_reversal = base_event and "Type A" in base_event.get("v", "")
             
             if direction != h1_trend:
                 if is_type_a_reversal:
                     factor_groups["VALIDACION"].append({"k": "Fricción H1 (Filtro)", "v": "Ignorada (Giro Tipo A)", "score": 0})
                 else:
                     penalty_h1 = P_H1_PENALTY
                     net_score -= penalty_h1
                     factor_groups["VALIDACION"].append({"k": "Fricción H1 (Filtro)", "v": "Contratendencia", "score": -penalty_h1})

        # 5. FRESHNESS
        if not is_passive and last_event_idx == 0: 
            net_score += 10
            factor_groups["SETUP"] = [factor_groups.get("SETUP", {}), {"k": "Inmediatez", "v": "Evento Reciente", "score": 10}]
        
        # 6. RSI MOMENTUM
        curr_rsi = mtr_m5['rsi'] if mtr_m5 else 50
        if direction == 1:
             if 50 < curr_rsi < 75: 
                 net_score += 15
                 factor_groups["MOMENTO"].append({"k": "Impulso RSI", "v": f"Alcista ({curr_rsi:.1f})", "score": 15})
             else:
                 factor_groups["MOMENTO"].append({"k": "Zona RSI", "v": f"No óptima ({curr_rsi:.1f})", "score": -10})
                 net_score -= 10
        elif direction == -1:
             if 25 < curr_rsi < 50: 
                 net_score += 15
                 factor_groups["MOMENTO"].append({"k": "Impulso RSI", "v": f"Bajista ({curr_rsi:.1f})", "score": 15})
             else:
                 factor_groups["MOMENTO"].append({"k": "Zona RSI", "v": f"No óptima ({curr_rsi:.1f})", "score": -10})
                 net_score -= 10

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
                factor_groups["ADVERTENCIAS"].append({"k": "Exceso", "v": "Sobre-extendido", "score": -40})
                net_score -= 40
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
                factor_groups["ADVERTENCIAS"].append({"k": "Exceso", "v": "Sobre-extendido", "score": -40})
                net_score -= 40

        # --- FINAL DECISION ---
        score = min(100, max(0, round(net_score)))
        entry = 0
        THRESHOLD = 75 
        
        # ENTRY RULES: Score >= 75 AND not passive AND gates passed
        if score >= THRESHOLD and not is_passive and not internal_gate_failed:
            entry = direction
            factor_groups["ESTADO"] = {"k": "Estado", "v": "ORDEN ACTIVADA", "score": 0}
        
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

        # 2. ROW 2: STATUS & STRUCTURE
        st_ev = factor_groups.get("ESTADO") or {"k": "Estado", "v": "Analizando", "score": 0}
        str_ev = factor_groups.get("ESTRUCTURA") or {"k": "Estructura", "v": "Sincronizando", "score": 0}
        add_unique_f(factors_detailed, st_ev)
        add_unique_f(factors_detailed, str_ev)

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

        metadata = {
            "strategy": self.STRATEGY_NAME,
            "score": score,
            "total_score": score,
            "score_breakdown": breakdown,
            "factors_detailed": factors_detailed,
            "can_entry": entry != 0,
            "gate_failed": internal_gate_failed or is_passive,
            "ema21": round(latest_ema21, 2),
            "ema50": round(latest_ema50, 2),
            "adx": round(mtr_m5['adx'] if mtr_m5 else 0, 1),
            "rsi": round(curr_rsi, 1),
            "status": "Esperando Setup (Pasivo)" if is_passive else ("Setup Activo" if entry != 0 else "Filtros fallidos"),
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
        Si BUY: Cerrar si precio cierra por debajo de EMA21.
        Si SELL: Cerrar si precio cierra por encima de EMA21.
        """
        if df is None or len(df) < 2: return False
        
        c_close = df['close'].iloc[-1]
        ema21_s = ta.ema(df['close'], length=21)
        if ema21_s is None: return False
        
        c_ema21 = ema21_s.iloc[-1]
        
        if position_type == "BUY" and c_close < c_ema21:
            logger.info("🛑 [EXIT] Cierre por debajo de EMA21 (Tendencia Invalidada)")
            return True
        elif position_type == "SELL" and c_close > c_ema21:
            logger.info("🛑 [EXIT] Cierre por encima de EMA21 (Tendencia Invalidada)")
            return True
            
        return False
