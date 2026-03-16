import pandas_ta as ta
import pandas as pd
import numpy as np
import logging
from ..models.classifier import RegimeMode
from ..utils.tech_utils import calculate_channel_boundary, calculate_manual_score

logger = logging.getLogger("ChannelMaster")

class PSTChannelMaster:
    STRATEGY_NAME = "PST-Channel-Master"
    STRATEGY_TYPE = RegimeMode.TREND 
    WEIGHT = 2.0

    async def calculate_signal(self, data_input, current_regime, user_levels=None, **kwargs):
        if isinstance(data_input, dict):
            df = data_input.get('m5')
        else:
            df = data_input

        if df is None or len(df) < 100:
            return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        config = user_levels.get('config', {}) if isinstance(user_levels, dict) else {}
        enable_tactical = config.get('enable_tactical', False)
        enable_macro = config.get('enable_macro', False)

        _, _, p_tac = calculate_channel_boundary(df.tail(1200), window=30, projection=0, recent_pivots=10) if enable_tactical else (None, None, None)
        _, _, p_mac = calculate_channel_boundary(df.tail(2000), window=15, projection=0, recent_pivots=15) if enable_macro else (None, None, None)
        
        has_manual = False
        if isinstance(user_levels, dict) and user_levels.get('levels'):
             if len(user_levels['levels']) > 0: has_manual = True
        elif isinstance(user_levels, list) and len(user_levels) > 0:
             has_manual = True
             
        if p_tac is None and p_mac is None and not has_manual:
             return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        params = p_tac if p_tac else p_mac
        slope_h = params['slope_h'] if params else 0
        intercept_h = params['intercept_h'] if params else 0
        slope_l = params['slope_l'] if params else 0
        intercept_l = params['intercept_l'] if params else 0
        last_idx = params['last_idx'] if params else 0

        close = df['close'].iloc[-1]
        if isinstance(user_levels, dict) and user_levels.get('current_price'):
            close = float(user_levels['current_price'])
            
        high = df['high'].iloc[-1]
        low = df['low'].iloc[-1]
        
        chan_upper = slope_h * last_idx + intercept_h
        chan_lower = slope_l * last_idx + intercept_l
        
        vol_val = df['tick_volume'].iloc[-1] if 'tick_volume' in df.columns else 0
        vol_ma = df['tick_volume'].rolling(20).mean().iloc[-1] if 'tick_volume' in df.columns else (vol_val or 1)
        is_green_candle = close > df['open'].iloc[-1] if 'open' in df.columns else False
        is_red_candle = close < df['open'].iloc[-1] if 'open' in df.columns else False

        source_upper = "AUTO"
        source_lower = "AUTO"
        
        manual_levels_list = user_levels.get('levels', []) if isinstance(user_levels, dict) else (user_levels if user_levels else [])
        
        current_ts = 0
        try:
            if isinstance(user_levels, dict) and user_levels.get('current_time'):
                current_ts = float(user_levels['current_time'])
            elif 'time_raw' in df.columns:
                current_ts = float(df['time_raw'].iloc[-1])
            elif isinstance(df.index, pd.DatetimeIndex):
                 current_ts = df.index[-1].timestamp()
            elif 'time' in df.columns:
                val = df['time'].iloc[-1]
                current_ts = float(val) if isinstance(val, (int, float)) else pd.to_datetime(val).timestamp()
            else:
                current_ts = float(df.index[-1])
        except:
            current_ts = 0
            
        def get_level_price(lvl):
            base_p = float(lvl['price'])
            if lvl.get('price2') and (lvl.get('time1') or lvl.get('time1_ts')):
                try:
                    # Prioritize exact Numeric Timestamps (Native Broker Seconds)
                    if lvl.get('time1_ts') and lvl.get('time2_ts'):
                        t1_val = float(lvl['time1_ts'])
                        t2_val = float(lvl['time2_ts'])
                    else:
                        # Fallback for old levels (Caution: TZ mismatch possible)
                        t1_val = pd.to_datetime(lvl['time1']).timestamp()
                        t2_val = pd.to_datetime(lvl['time2']).timestamp()
                        
                    p1 = float(lvl['price'])
                    p2 = float(lvl['price2'])
                    
                    # Extension Buffer (2 hours)
                    buffer_sec = 7200 
                    if current_ts > (max(t1_val, t2_val) + buffer_sec): return None 
                    
                    if abs(t2_val - t1_val) > 0.1:
                        m = (p2 - p1) / (t2_val - t1_val)
                        return p1 + m * (current_ts - t1_val)
                except Exception as e:
                    logger.error(f"Error in strategy interpolation: {e}")
                    pass
            return base_p
        
        if manual_levels_list:
            res_candidates = []
            for l in manual_levels_list:
                if l['type'] == 'RESISTANCE':
                    lp = get_level_price(l)
                    if lp is not None: res_candidates.append(lp)

            if res_candidates:
                chan_upper = min(res_candidates, key=lambda x: abs(x - close))
                source_upper = "MANUAL"
                
            sup_candidates = []
            for l in manual_levels_list:
                if l['type'] == 'SUPPORT':
                    lp = get_level_price(l)
                    if lp is not None: sup_candidates.append(lp)

            if sup_candidates:
                chan_lower = min(sup_candidates, key=lambda x: abs(x - close))
                source_lower = "MANUAL"
        
        adx = ta.adx(df['high'], df['low'], df['close'], length=14)['ADX_14'].iloc[-1]
        rsi = ta.rsi(df['close'], length=14).iloc[-1]
        atr = ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-1]
        trend_slope = params['slope_m'] if params and 'slope_m' in params else 0
        
        score_res, act_res, desc_res = calculate_manual_score(
            price=close, lvl_price=chan_upper, l_type='RESISTANCE', 
            rsi=rsi, vol_val=vol_val, vol_ma=vol_ma, is_green=is_green_candle, is_red=is_red_candle,
            trend_slope=trend_slope, adx=adx
        )
        
        score_sup, act_sup, desc_sup = calculate_manual_score(
            price=close, lvl_price=chan_lower, l_type='SUPPORT', 
            rsi=rsi, vol_val=vol_val, vol_ma=vol_ma, is_green=is_green_candle, is_red=is_red_candle,
            trend_slope=trend_slope, adx=adx
        )

        score = 0
        entry = 0
        signal_type = "None"
        breakdown = {}
        # Inicializar metadata para evitar errores de scope
        metadata = {
            "strategy": "Channel Master",
            "type": signal_type,
            "chan_upper": round(chan_upper, 5),
            "chan_lower": round(chan_lower, 5),
            "source_upper": source_upper,
            "source_lower": source_lower,
            "score_breakdown": breakdown,
            "all_levels_data": [],
            "factors_detailed": []
        }

        # 6. Filtro de Sobre-Extensión (Anti-Agotamiento)
        ema50 = df['ema_50'].iloc[-1] if 'ema_50' in df.columns else (df['close'].rolling(50).mean().iloc[-1] if len(df) >= 50 else close)
        dist_ema50_pct = abs(close - ema50) / ema50 * 100
        is_overextended = dist_ema50_pct > 0.8 # Umbral conservador: 0.8% de distancia a la EMA 50

        # Determinar Señal de Entrada (Solo Rupturas COHERENTES + MOMENTUM + NO SOBREEXTENDIDO)
        threshold = kwargs.get('score_threshold') or 80
        
        if score_res >= threshold and act_res == "ROTURA":
            # RESISTENCIA ROTA -> COMPRA. 
            if close > chan_upper and is_green_candle:
                if is_overextended:
                    logger.warning(f"⚠️ [ChannelMaster] BLOQUEO: Sobre-Extensión detectada ({dist_ema50_pct:.2f}%)")
                    breakdown["Bloqueo"] = "Sobre-Extensión"
                else:
                    score = score_res
                    signal_type = f"UPPER {act_res}"
                    entry = 1 
                    for f in desc_res:
                        breakdown[f['k']] = f['v']
                    metadata["factors_detailed"] = desc_res
                    logger.info(f"🎯 [ChannelMaster] SEÑAL COMPRA | Ruptura Confirmada @ {chan_upper:.2f}")

        elif score_sup >= threshold and act_sup == "ROTURA":
            # SOPORTE ROTO -> VENTA.
            if close < chan_lower and is_red_candle:
                if is_overextended:
                    logger.warning(f"⚠️ [ChannelMaster] BLOQUEO: Sobre-Extensión detectada ({dist_ema50_pct:.2f}%)")
                    breakdown["Bloqueo"] = "Sobre-Extensión"
                else:
                    score = score_sup
                    signal_type = f"LOWER {act_sup}"
                    entry = -1
                    for f in desc_sup:
                        breakdown[f['k']] = f['v']
                    metadata["factors_detailed"] = desc_sup
                    logger.info(f"🎯 [ChannelMaster] SEÑAL VENTA | Pérdida Confirmada @ {chan_lower:.2f}")

        if entry == 0:
            score = max(score_res, score_sup)
            best_act = act_res if score_res >= score_sup else act_sup
            best_factors = desc_res if score_res >= score_sup else desc_sup
            signal_type = f"HUD {best_act}"
            breakdown["Status"] = f"{best_act} (Score: {score})"
            for f in best_factors:
                breakdown[f['k']] = f['v']
            metadata["factors_detailed"] = best_factors

        metadata["score_breakdown"] = breakdown
        metadata["all_levels_data"] = [] # Asegurar que esté vacío antes de rellenar

        # 7. UNIFICAR SCORE: El score de la estrategia debe ser el máximo entre el canal y cualquier nivel manual
        best_manual_score = 0
        best_manual_factors = []
        best_manual_act = "WAIT"

        for lvl in manual_levels_list:
            lvl_price = get_level_price(lvl)
            if lvl_price is None: continue
            
            strat_score, action_reco, val_msg_base = calculate_manual_score(
                price=close, lvl_price=lvl_price, l_type=lvl['type'],
                rsi=rsi, vol_val=vol_val, vol_ma=vol_ma, is_green=is_green_candle, is_red=is_red_candle,
                trend_slope=trend_slope, adx=adx
            )
            
            s_score = round(strat_score)
            if s_score > best_manual_score:
                best_manual_score = s_score
                best_manual_factors = val_msg_base
                best_manual_act = action_reco

            metadata["all_levels_data"].append({
                "type": lvl['type'],
                "source": "MANUAL",
                "price": round(lvl_price, 5),
                "dist": round((lvl_price - close) / close * 100, 2),
                "id": lvl['id'],
                "action_reco": action_reco,
                "strat_score": s_score,
                "validation": ", ".join([f"{f['k']}: {f['v']}" for f in val_msg_base]) if val_msg_base else "Normal",
                "factors": val_msg_base
            })
        
        # El score final es el mejor entre lo que ya teníamos (canal) y el mejor manual global
        if best_manual_score > score:
            score = best_manual_score
            signal_type = f"LEVEL {best_manual_act}"
            metadata["factors_detailed"] = best_manual_factors
            # Actualizar breakdown para el HUD
            breakdown = {"Status": f"{best_manual_act} (Nivel Manual)"}
            for f in best_manual_factors:
                breakdown[f['k']] = f['v']
            metadata["score_breakdown"] = breakdown

        metadata["all_levels_data"].sort(key=lambda x: abs(x['dist']))
        metadata["score"] = score

        return {
            "entry": entry,
            "atr": atr,
            "metadata": metadata,
            "score": score
        }
