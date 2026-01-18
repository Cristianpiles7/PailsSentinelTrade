import pandas_ta as ta
import pandas as pd
import numpy as np
import logging
from ..models.classifier import RegimeMode
from ..utils.tech_utils import calculate_channel_boundary

logger = logging.getLogger("ChannelMaster")

class PSTChannelMaster:
    STRATEGY_NAME = "PST-Channel-Master"
    STRATEGY_TYPE = RegimeMode.TREND # Funciona bien en ambos, pero M5 suele estar en tendencia/rango inclinado
    WEIGHT = 2.0

    async def calculate_signal(self, data_input, current_regime, user_levels=None):
        # La visualización es mejor en M5 como pidió el usuario
        if isinstance(data_input, dict):
            df = data_input.get('m5')
        else:
            df = data_input

        if df is None or len(df) < 100:
            return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        # 1. Chequeo de Configuración del Usuario (Habilitar/Deshabilitar Canales)
        # Se espera que el 'user_levels' contenga una lista "special" o algo similar, pero 
        # mejor usamos el argumento extra que añadiremos al método
        config = user_levels.get('config', {}) if isinstance(user_levels, dict) else {}
        enable_tactical = config.get('enable_tactical', True)
        enable_macro = config.get('enable_macro', True)

        # 1. Calcular Canales (TÁCTICO y MACRO)
        # 2000 velas para Macro, 1200 para Táctico
        # Si df es pequeño, tail devuelve todo.
        _, _, p_tac = calculate_channel_boundary(df.tail(1200), window=30, projection=0, recent_pivots=10) if enable_tactical else (None, None, None)
        _, _, p_mac = calculate_channel_boundary(df.tail(2000), window=15, projection=0, recent_pivots=15) if enable_macro else (None, None, None)
        
        # DEBUG: Print params status
        if p_tac is None and p_mac is None: 
            print(f"⚠️ [PSTChannelMaster] Failed to calc channels for len(df)={len(df)}")
        else:
            print(f"✅ [PSTChannelMaster] Channels OK. Tac={p_tac is not None}, Mac={p_mac is not None}")
        
        if p_tac is None and not user_levels:
            return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        # 2. Extraer parámetros (Usamos el TÁCTICO para señales por defecto)
        params = p_tac if p_tac else p_mac
        slope_h = params['slope_h'] if params else 0
        intercept_h = params['intercept_h'] if params else 0
        slope_l = params['slope_l'] if params else 0
        intercept_l = params['intercept_l'] if params else 0
        last_idx = params['last_idx'] if params else 0

        # 3. Precios Actuales
        close = df['close'].iloc[-1]
        
        # Override Close with Live Tick if provided
        if isinstance(user_levels, dict) and user_levels.get('current_price'):
            close = float(user_levels['current_price'])
            # logger.info(f"💲 Using Externally Provided Price: {close}")
            
        high = df['high'].iloc[-1]
        low = df['low'].iloc[-1]
        prev_close = df['close'].iloc[-2]
        
        # 4. Valores del Canal en la vela actual
        chan_upper = slope_h * last_idx + intercept_h
        chan_lower = slope_l * last_idx + intercept_l
        
        # Track source of bounds
        source_upper = "AUTO"
        source_lower = "AUTO"
        
        # --- OVERRIDE CON NIVELES MANUALES (Trading Híbrido) ---
        manual_levels_list = user_levels.get('levels', []) if isinstance(user_levels, dict) else (user_levels if user_levels else [])
        chan_config = user_levels.get('config', {}) if isinstance(user_levels, dict) else {}
        logger.info(f"🔍 DEBUG MANUAL LEVELS: {len(manual_levels_list)} levels found. Data: {manual_levels_list}")
        
        # Prepare Timestamp for Trendlines
        # 4. Determinar Timestamp Actual para Interpolación
        current_ts = 0
        try:
            # CHECK EXPLICIT OVERRIDE FIRST (From Server Live Tick)
            if isinstance(user_levels, dict) and user_levels.get('current_time'):
                current_ts = float(user_levels['current_time'])
                logger.info(f"🕒 Using Externally Provided Time: {current_ts}")
            
            # Fallback to Dataframe Index
            elif isinstance(df.index, pd.DatetimeIndex):
                 current_ts = df.index[-1].timestamp()
                 
            # Check for 'time' column
            elif 'time' in df.columns:
                val = df['time'].iloc[-1]
                if isinstance(val, (int, float)):
                     current_ts = float(val)
                else: 
                     current_ts = pd.to_datetime(val).timestamp()
            else:
                idx_val = df.index[-1]
                if isinstance(idx_val, (int, float)) and idx_val > 1000000000:
                    current_ts = float(idx_val)
                else:
                    logger.warning(f"⚠️ Could not determine timestamp from DF. Index={idx_val}")

        except Exception as e:
            logger.error(f"Error converting index to timestamp: {e}")
            current_ts = 0
            
        logger.info(f"DEBUG TIME: Last Index={df.index[-1]} TimeCol={df['time'].iloc[-1] if 'time' in df.columns else 'N/A'} Calculated TS={current_ts}")

        def get_level_price(lvl):
            if lvl.get('price2') and lvl.get('time1') and lvl.get('time2'):
                try:
                    # Parse times (handle both string ISO and float/int)
                    t1_val = pd.to_datetime(lvl['time1']).timestamp()
                    t2_val = pd.to_datetime(lvl['time2']).timestamp()
                    
                    p1 = float(lvl['price'])
                    p2 = float(lvl['price2'])
                    
                    if t2_val != t1_val:
                        m = (p2 - p1) / (t2_val - t1_val)
                        result_price = p1 + m * (current_ts - t1_val)
                        logger.info(f"DEBUG MATH: p1={p1} p2={p2} t1={t1_val} t2={t2_val} cur={current_ts} m={m} res={result_price}")
                        return result_price
                except Exception as e:
                    logger.error(f"Error calculando trendline para lvl {lvl.get('id')}: {e}")
            return float(lvl['price'])
        
        if manual_levels_list:

            # Buscar el nivel de resistencia más cercano por encima del precio
            res_lvls = [get_level_price(l) for l in manual_levels_list if l['type'] == 'RESISTANCE']
            if res_lvls:
                # Consider only Manual Resistances ABOVE the active auto-channel (or fallback if None)
                # Logic: If user draws a resistance, they likely want it to be the NEW ceiling, 
                # but only if it's "in play". For simplicity, let's say ANY manual resistance 
                # overrides the auto one if it is closer to the price than the auto one?
                # Or just STRICT OVERRIDE: If manual, use manual.
                
                # Logic V2: STRICT OVERRIDE - The "Closest Manual Level" becomes the de-facto channel boundary.
                closest_res = min(res_lvls, key=lambda x: abs(x - close))
                
                # Only use if it's somewhat reasonable? No, user knows best.
                chan_upper = closest_res
                source_upper = "MANUAL"
                logger.debug(f"📝 Usando RESISTENCIA MANUAL: {chan_upper}")
                
            # Buscar el nivel de soporte más cercano por debajo del precio
            sup_lvls = [get_level_price(l) for l in manual_levels_list if l['type'] == 'SUPPORT']
            if sup_lvls:
                closest_sup = min(sup_lvls, key=lambda x: abs(x - close))
                chan_lower = closest_sup
                source_lower = "MANUAL"
                logger.debug(f"📝 Usando SOPORTE MANUAL: {chan_lower}")
        
        # 5. Indicadores de apoyo (Confirmación)
        rsi = ta.rsi(df['close'], length=14).iloc[-1]
        adx = ta.adx(df['high'], df['low'], df['close'], length=14)['ADX_14'].iloc[-1]
        atr = ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-1]
        
        score = 0
        entry = 0
        signal_type = "None"
        breakdown = {}

        # --- LÓGICA DE RUPTURA (BREAKOUT) ---
        if close > chan_upper and prev_close <= chan_upper:
            # Ruptura Alcista
            if adx > 25 and rsi > 55:
                entry = 1
                signal_type = "BULL_BREAKOUT"
                score = 80
                breakdown["Type"] = "Breakout UP"
                breakdown["Momentum"] = f"ADX {adx:.1f} Confirmado"
        
        elif close < chan_lower and prev_close >= chan_lower:
            # Ruptura Bajista
            if adx > 25 and rsi < 45:
                entry = -1
                signal_type = "BEAR_BREAKOUT"
                score = 80
                breakdown["Type"] = "Breakout DOWN"
                breakdown["Momentum"] = f"ADX {adx:.1f} Confirmado"

        # --- LÓGICA DE REBOTE (REJECTION) ---
        if entry == 0:
            # Distancia a los bordes
            dist_upper = (chan_upper - high) / atr if atr > 0 else 999
            dist_lower = (low - chan_lower) / atr if atr > 0 else 999
            
            # Rebote en Techo (Venta)
            if high >= chan_upper * 0.999 and close < prev_close:
                if rsi > 65: # Sobrecompra relativa al canal
                    entry = -1
                    signal_type = "UPPER_REJECTION"
                    score = 70
                    breakdown["Type"] = "Techo Tocado"
                    breakdown["RSI"] = f"{rsi:.1f} (Bearish)"
                    
            # Rebote en Suelo (Compra)
            elif low <= chan_lower * 1.001 and close > prev_close:
                if rsi < 35: # Sobreventa relativa al canal
                    entry = 1
                    signal_type = "LOWER_REJECTION"
                    score = 70
                    breakdown["Type"] = "Suelo Tocado"
                    breakdown["RSI"] = f"{rsi:.1f} (Bullish)"

        # Helper to get points (Using Slice Context)
        def get_channel_points(params, df_slice):
            if not params: return None
            
            # Use local length of the slice used for calculation
            length = len(df_slice)
            
            # P2 is current (end of slice)
            x2 = length - 1 # This maps to the last index of the slice (0..N-1)
            
            # Since calculate_channel_boundary uses integer indexing 0..N on the slice,
            # params['last_idx'] should be equal to x2 (or close).
            # Y = slope * x + intercept (where intercept is Y at x=0)
            
            # Calculate Y using the linear equation
            # Note: params['intercept_h'] is the Y-intercept at x=0 of the slice
            y2_h = params['slope_h'] * x2 + params['intercept_h']
            y2_l = params['slope_l'] * x2 + params['intercept_l']
            
            # Point 1 (Back 50 candles relative to slice end)
            x1 = x2 - 50
            if x1 < 0: x1 = 0
            y1_h = params['slope_h'] * x1 + params['intercept_h']
            y1_l = params['slope_l'] * x1 + params['intercept_l']
            
            # Get Times (from the slice index)
            # Ensure df_slice has a valid DateTime index or we can map it
            t2 = str(df_slice.index[x2]) if x2 < len(df_slice) else str(df_slice.index[-1])
            t1 = str(df_slice.index[x1]) if x1 < len(df_slice) else str(df_slice.index[0])
            
            return {
                "p1_h": float(y1_h), "t1": t1, "p2_h": float(y2_h), "t2": t2,
                "p1_l": float(y1_l), "p2_l": float(y2_l)
            }

        # Calculate using the specific slices (df_tac and df_mac logic)
        # Note: In section 1 we did calculate_channel_boundary(df.tail(...))
        # We need to reconstruct those slices or use the same logic to pass to get_channel_points
        
        df_tac = df.tail(1200) # Re-slice to match logic in step 1
        df_mac = df.tail(2000) # Re-slice to match logic in step 1
        
        tac_pts = get_channel_points(p_tac, df_tac)
        mac_pts = get_channel_points(p_mac, df_mac)

        metadata = {
            "strategy": "Channel Master",
            "type": signal_type,
            "chan_upper": round(chan_upper, 5),
            "chan_lower": round(chan_lower, 5),
            "source_upper": source_upper,
            "source_lower": source_lower,
            "tac_upper": round(slope_h * last_idx + intercept_h, 5) if p_tac else None,
            "tac_lower": round(slope_l * last_idx + intercept_l, 5) if p_tac else None,
            "all_levels_data": [], # To be populated below
            "mac_upper": round(p_mac['slope_h'] * p_mac['last_idx'] + p_mac['intercept_h'], 5) if p_mac else None,
            "mac_lower": round(p_mac['slope_l'] * p_mac['last_idx'] + p_mac['intercept_l'], 5) if p_mac else None,
            "dist_atr": round(min(dist_upper if 'dist_upper' in locals() else 99, dist_lower if 'dist_lower' in locals() else 99), 2),
            "score_breakdown": breakdown,
            "tac_data": tac_pts,
            "mac_data": mac_pts
        }

        # --- POPULATE "ALL LEVELS" DATA FOR HUD LIST ---
        # 1. Manual Levels
        for lvl in manual_levels_list:
            lvl_price = get_level_price(lvl)
            dist_pct = (lvl_price - close) / close * 100
            dist_val = close - lvl_price # + means Price > Level
            
            # --- EVALUATE LOGIC (Progressive Scoring) ---
            action_reco = "WAIT"
            score_boost = 0
            val_msg = []
            strat_score = 0
            
            is_res = lvl['type'] == 'RESISTANCE'
            is_sup = lvl['type'] == 'SUPPORT'
            vol_val = df['tick_volume'].iloc[-1] if 'tick_volume' in df else 0
            vol_ma = df['tick_volume'].rolling(20).mean().iloc[-1] if 'tick_volume' in df else (vol_val or 1)
            
            # --- CONFIGURABLE STRICKNESS (3-ZONE SYSTEM) ---
            THR_NEUTRAL = 0.25  # Stricter (0.40 -> 0.25)
            THR_ACTION = 0.05   # Much Stricter (0.08 -> 0.05)
            
            # Absolute distance pct for zoning
            abs_dist_pct = abs(dist_pct)
            
            # ZONE 1: FAR (> THR_NEUTRAL %)
            if abs_dist_pct > THR_NEUTRAL:
                action_reco = "NEUTRAL"
                strat_score = 0
                
            # ZONE 2: WATCH (THR_ACTION to THR_NEUTRAL)
            elif THR_ACTION < abs_dist_pct <= THR_NEUTRAL:
                action_reco = "WATCH"
                # Linear Interpolation (10 to 45)
                # Multiplier = (45-10) / (THR_NEUTRAL - THR_ACTION) = 35 / 0.20 = 175.0
                proximity_score = 10 + (THR_NEUTRAL - abs_dist_pct) * 175.0
                strat_score = round(proximity_score)
                val_msg.append(f"Dist: {abs_dist_pct:.2f}%")
                
            # ZONE 3: ACTION (< THR_ACTION)
            else: 
                # BASE ACTION SCORE = 50
                strat_score = 50
                
                # BREAKOUT CHECK (Crossed Level)
                if is_res and dist_val > 0:
                    action_reco = "BREAK BUY"
                    strat_score += 20
                    if rsi > 55: strat_score += 10; val_msg.append("RSI Bullish")
                    if vol_val > vol_ma: strat_score += 15; val_msg.append("Vol High")
                elif is_sup and dist_val < 0:
                    action_reco = "BREAK SELL"
                    strat_score += 20
                    if rsi < 45: strat_score += 10; val_msg.append("RSI Bearish")
                    if vol_val > vol_ma: strat_score += 15; val_msg.append("Vol High")
                
                # BOUNCE CHECK (Touching Level but not crossed/sustained)
                elif is_res: 
                    action_reco = "BOUNCE SELL"
                    strat_score += 10
                    # Proximity Bonus within Action Zone (Max +15) -> 15 / THR_ACTION = 15 / 0.05 = 300.0
                    strat_score += (THR_ACTION - abs_dist_pct) * 300.0
                    if rsi > 70: strat_score += 15; val_msg.append("RSI OB")
                elif is_sup:
                    action_reco = "BOUNCE BUY"
                    strat_score += 10
                    # Proximity Bonus within Action Zone (Max +15)
                    strat_score += (THR_ACTION - abs_dist_pct) * 187.5
                    if rsi < 30: strat_score += 15; val_msg.append("RSI OS")

            metadata["all_levels_data"].append({
                "type": lvl['type'],
                "source": "MANUAL",
                "price": round(lvl_price, 5),
                "dist": round(dist_pct, 2),
                "id": lvl['id'],
                "action_reco": action_reco,
                "strat_score": round(strat_score),
                "validation": ", ".join(val_msg) if val_msg else "Standard"
            })
            
            # (Reverted) Manual Score does not overwrite Global Score here.
        
        if len(metadata["all_levels_data"]) > 0:
            logger.info(f"✅ Metadata populated with {len(metadata['all_levels_data'])} levels.")
            # logger.info(f"Sample: {metadata['all_levels_data'][0]}")
        else:
             logger.info("⚠️ Metadata all_levels_data is EMPTY after loop.")

        # 2. Auto Levels (if enabled)
        # Check config (default to TRUE/1 if not present)
        show_tac = chan_config.get('enable_tactical', 1) in [1, True, '1', 'true']
        show_mac = chan_config.get('enable_macro', 1) in [1, True, '1', 'true']
        
        if p_tac and show_tac:
             tac_h = slope_h * last_idx + intercept_h
             tac_l = slope_l * last_idx + intercept_l
             metadata["all_levels_data"].append({
                 "type": "RESISTANCE", "source": "AUTO (Tac)", "price": round(tac_h, 5), "dist": round((tac_h - close)/close*100, 2)
             })
             metadata["all_levels_data"].append({
                 "type": "SUPPORT", "source": "AUTO (Tac)", "price": round(tac_l, 5), "dist": round((tac_l - close)/close*100, 2)
             })

        if p_mac and show_mac:
             mac_h = p_mac['slope_h'] * p_mac['last_idx'] + p_mac['intercept_h']
             mac_l = p_mac['slope_l'] * p_mac['last_idx'] + p_mac['intercept_l']
             metadata["all_levels_data"].append({
                 "type": "RESISTANCE", "source": "AUTO (Mac)", "price": round(mac_h, 5), "dist": round((mac_h - close)/close*100, 2)
             })
             metadata["all_levels_data"].append({
                 "type": "SUPPORT", "source": "AUTO (Mac)", "price": round(mac_l, 5), "dist": round((mac_l - close)/close*100, 2)
             })

        # Sort by distance to price (absolute value) for better readability
        metadata["all_levels_data"].sort(key=lambda x: abs(x['dist']))

        return {
            "entry": entry,
            "atr": atr,
            "metadata": metadata,
            "score": score
        }
