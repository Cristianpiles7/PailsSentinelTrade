import pandas_ta as ta
import pandas as pd
import numpy as np
import logging
from datetime import datetime
from ..config import (
    SL_ATR_MULTIPLIER, 
    MAX_SCALPER_SL_POINTS, 
    MIN_RR_RATIO,
    LONDRES_SESSION_START,
    LONDRES_SESSION_END,
    NY_SESSION_START,
    NY_SESSION_END,
    SESSION_SESSIONS_ONLY
)
from ..utils.tech_utils import get_asset_class

logger = logging.getLogger("PST-Scalper-Active")

class PSTScalperActive:
    STRATEGY_NAME = "PST-Scalper-Active"
    STRATEGY_TYPE = "ALL"
    
    def __init__(self):
        self.ema_mid = 21
        self.rsi_length = 14
        self.bb_length = 20
        self.bb_std = 2.0
        
        # Perfiles Dinámicos de Activos
        self.ASSET_PROFILES = {
            "CRYPTO": {
                "vol_requisite": 1.25,      
                "min_rr": 1.45,             
                "hysteresis_atr": 0.35,    
                "sl_margin_atr": 1.5,
                "max_extension_atr": 1.0, # Reducido de 1.4: Evita comprar en techos
                "score_threshold": 72
            },
            "METAL": {
                "vol_requisite": 1.30,      
                "min_rr": 1.5,            
                "hysteresis_atr": 0.15,    
                "sl_margin_atr": 2.0,
                "max_extension_atr": 0.95, # Reducido de 1.2
                "score_threshold": 74
            },
            "INDEX": {
                "vol_requisite": 1.30,
                "min_rr": 1.55,
                "hysteresis_atr": 0.2,
                "sl_margin_atr": 2.5,
                "max_extension_atr": 0.95, # Reducido de 1.2
                "score_threshold": 76
            },
            "EQUITIES": {
                "vol_requisite": 1.35,      # Mayor volumen para confirmar participación
                "min_rr": 1.60,             # Mayor relación Riesgo:Beneficio por volatilidad de acción
                "hysteresis_atr": 0.25,    
                "sl_margin_atr": 2.2,
                "max_extension_atr": 0.90, # Filtro anti-fomo estricto
                "score_threshold": 75
            },
            "FOREX": {
                "vol_requisite": 1.15,
                "min_rr": 1.3,
                "hysteresis_atr": 0.15,
                "sl_margin_atr": 1.5,
                "max_extension_atr": 1.1,
                "score_threshold": 68
            }
        }
        # Fallback profile por si no se identifica
        self.DEFAULT_PROFILE = self.ASSET_PROFILES["FOREX"]

    async def calculate_signal(self, mtf_data, current_regime=None, user_levels=None, spread_points=0, spread_dist=0, **kwargs):
        """
        Scalper V2 (Active): EMA Breakout Relajado + Stalking re-activado para alta frecuencia.
        """
        # Filtro de Régimen Relajado: Ya no bloqueamos totalmente, 
        # dejamos que la lógica interna de la estrategia decida según el volumen y la vela.
        if current_regime == "OFFLINE":
            return {"score": 0, "signal": "NEUTRAL", "metadata": {"mode": "OFFLINE", "factors_detailed": []}}

        # --- NUEVO: FILTRO DE SESIONES HORARIAS INSTITUCIONAL ---
        symbol = kwargs.get("symbol", "").upper()
        asset_class = get_asset_class(symbol)
        if SESSION_SESSIONS_ONLY and asset_class != "CRYPTO":
            now_time = datetime.now().strftime("%H:%M")
            in_london = LONDRES_SESSION_START <= now_time <= LONDRES_SESSION_END
            in_ny = NY_SESSION_START <= now_time <= NY_SESSION_END
            if not (in_london or in_ny):
                return {
                    "score": 0,
                    "signal": "NEUTRAL",
                    "metadata": {
                        "mode": "FUERA_DE_SESION",
                        "factors_detailed": [{"k": "Estado", "v": "Fuera de Sesión Líquida", "score": 0}]
                    }
                }

        df_m1 = mtf_data.get('m1') if isinstance(mtf_data, dict) else mtf_data
        df_m3 = mtf_data.get('m3')
        df_m5 = mtf_data.get('m5')
        df_m15 = mtf_data.get('m15')
        
        signals_found = []
        
        # 1. Evaluar M5
        if df_m5 is not None and len(df_m5) >= 50:
            trend_df = df_m15 if (df_m15 is not None and len(df_m15) >= 50) else None
            sig_m5 = self._evaluate_tf(df_m5, trend_df, None, spread_dist, "M5", current_regime=current_regime, **kwargs)
            if sig_m5['entry'] != 0: return sig_m5
            signals_found.append(sig_m5)

        # 2. Evaluar M3
        if df_m3 is not None and len(df_m3) >= 50:
            sig_m3 = self._evaluate_tf(df_m3, df_m15, df_m5, spread_dist, "M3", current_regime=current_regime, **kwargs)
            if sig_m3['entry'] != 0: return sig_m3
            signals_found.append(sig_m3)
                
        # 3. Evaluar M1
        if df_m1 is not None and len(df_m1) >= 50:
            sig_m1 = self._evaluate_tf(df_m1, df_m5, df_m3, spread_dist, "M1", current_regime=current_regime, **kwargs)
            if sig_m1['entry'] != 0: return sig_m1
            signals_found.append(sig_m1)
            
        if signals_found:
            best_passive_sig = max(signals_found, key=lambda x: x['score'])
            return best_passive_sig
            
        return {
            "score": 0, 
            "signal": "NEUTRAL", 
            "metadata": {
                "mode": "ESPERANDO_DATOS",
                "factors_detailed": [{"k": "Estado", "v": "Esperando Datos (M1/M5)", "score": 0}]
            }
        }

    def _evaluate_tf(self, df_base, df_trend1, df_trend2, spread_dist, tf_label, **kwargs):
        symbol = kwargs.get("symbol", "").upper()
        
        # Uso del clasificador estándar global para TSLA, NVDA, Crypto, etc.
        asset_class = get_asset_class(symbol)
        profile = self.ASSET_PROFILES.get(asset_class, self.DEFAULT_PROFILE)
        min_rr = float(kwargs.get("min_rr", profile["min_rr"]) or profile["min_rr"])
        asset_threshold = float(kwargs.get("score_threshold", profile["score_threshold"]) or profile["score_threshold"])
        entry_threshold = max(asset_threshold, profile["score_threshold"])

        ema21 = ta.ema(df_base['close'], length=self.ema_mid)
        rsi = ta.rsi(df_base['close'], length=self.rsi_length)
        atr = ta.atr(df_base['high'], df_base['low'], df_base['close'], length=14)
        vol_ma = ta.sma(df_base['tick_volume'], length=20)
        adx_df = ta.adx(df_base['high'], df_base['low'], df_base['close'], length=14)
        bb = ta.bbands(df_base['close'], length=self.bb_length, std=self.bb_std)
        kc = ta.kc(df_base['high'], df_base['low'], df_base['close'], length=20, scalar=1.5)
        
        if ema21 is None or rsi is None or atr is None or bb is None or kc is None or vol_ma is None or adx_df is None:
            return {"score": 0, "signal": "NEUTRAL", "metadata": {"mode": f"CALCULANDO_{tf_label}", "factors_detailed": []}}

        # --- NUEVO: VWAP DIARIO DINÁMICO ---
        c_vwap = None
        try:
            df_copy = df_base.copy()
            if 'time' in df_copy.columns:
                df_copy['datetime'] = pd.to_datetime(df_copy['time'])
            else:
                df_copy['datetime'] = pd.to_datetime(df_copy.index)
            df_copy['date_only'] = df_copy['datetime'].dt.date
            typical_price = (df_copy['high'] + df_copy['low'] + df_copy['close']) / 3
            df_copy['tp_vol'] = typical_price * df_copy['tick_volume']
            cum_tp_vol = df_copy.groupby('date_only')['tp_vol'].cumsum()
            cum_vol = df_copy.groupby('date_only')['tick_volume'].cumsum()
            vwap = cum_tp_vol / cum_vol
            c_vwap = vwap.iloc[-2] # alineado con signal_idx = -2
        except Exception as e:
            logger.warning(f"Error calculando VWAP en Active: {e}")

        try:
            lower_bb = bb.iloc[:, 0]
            mid_bb = bb.iloc[:, 1]
            upper_bb = bb.iloc[:, 2]
            lower_kc = kc.iloc[:, 0]
            upper_kc = kc.iloc[:, 2]
            
            is_squeeze_series = (upper_bb < upper_kc) & (lower_bb > lower_kc)
            is_squeeze = is_squeeze_series.iloc[-2]
            was_squeezed_recently = is_squeeze_series.iloc[-16:-2].any()
        except Exception as e:
            is_squeeze, was_squeezed_recently = False, False
            try:
                lower_bb = bb.iloc[:, 0]
                mid_bb = bb.iloc[:, 1]
                upper_bb = bb.iloc[:, 2]
            except Exception:
                return {"score": 0, "signal": "NEUTRAL", "metadata": {"mode": f"Error_BB_{tf_label}", "factors_detailed": []}}

        signal_idx = -2
        c_price = df_base['close'].iloc[signal_idx]
        c_open = df_base['open'].iloc[signal_idx]
        c_high = df_base['high'].iloc[signal_idx]
        c_low = df_base['low'].iloc[signal_idx]
        
        c_ema21 = ema21.iloc[signal_idx]
        curr_atr = atr.iloc[signal_idx]
        curr_rsi = rsi.iloc[signal_idx]
        curr_adx = adx_df['ADX_14'].iloc[signal_idx]

        c_upper_bb = upper_bb.iloc[signal_idx]
        c_lower_bb = lower_bb.iloc[signal_idx]
        
        curr_vol = df_base['tick_volume'].iloc[signal_idx]
        mean_vol = vol_ma.iloc[signal_idx]
        rel_vol = curr_vol / mean_vol if mean_vol > 0 else 1.0
        
        body_size = abs(c_price - c_open)
        total_range = max(0.00001, c_high - c_low)
        body_ratio = body_size / total_range

        # Tendencia Superior Sincronizada y Excluyente
        t1_up = True
        t1_down = True
        if df_trend1 is not None:
            ema50_t1 = ta.ema(df_trend1['close'], length=50)
            if ema50_t1 is not None:
                t1_up = df_trend1['close'].iloc[-2] > ema50_t1.iloc[-2]
                t1_down = df_trend1['close'].iloc[-2] < ema50_t1.iloc[-2]
            else:
                t1_up = False
                t1_down = False
                
        t2_up = True
        t2_down = True
        if df_trend2 is not None:
            ema50_t2 = ta.ema(df_trend2['close'], length=50)
            if ema50_t2 is not None:
                t2_up = df_trend2['close'].iloc[-2] > ema50_t2.iloc[-2]
                t2_down = df_trend2['close'].iloc[-2] < ema50_t2.iloc[-2]
            else:
                t2_up = False
                t2_down = False
                
        confirmed_uptrend = t1_up and t2_up
        confirmed_downtrend = t1_down and t2_down

        score = 0
        entry = 0
        factors_detailed = []
        mode_label = f"ACECHANDO_{tf_label}"

        target_price_tp = 0.0
        target_price_sl = 0.0

        # Filtro VSA
        recent_buy_abs = False
        recent_sell_abs = False
        for i in range(-16, -1):
            idx = len(df_base) + i
            if idx < 0: continue
            _body = abs(df_base['close'].iloc[idx] - df_base['open'].iloc[idx])
            _lower_wick = min(df_base['open'].iloc[idx], df_base['close'].iloc[idx]) - df_base['low'].iloc[idx]
            _upper_wick = df_base['high'].iloc[idx] - max(df_base['open'].iloc[idx], df_base['close'].iloc[idx])
            _v_ma = vol_ma.iloc[idx] if vol_ma is not None else 1
            _v_rel = df_base['tick_volume'].iloc[idx] / _v_ma if _v_ma > 0 else 0
            
            if _v_rel > 1.2:
                if _lower_wick > (_body * 1.5): recent_buy_abs = True
                if _upper_wick > (_body * 1.5): recent_sell_abs = True

        # Ignición endurecida (0.75 ATR) y Filtro Anti-Rechazo (Wicks < 30% cuerpo)
        upper_wick = c_high - max(c_open, c_price)
        lower_wick = min(c_open, c_price) - c_low
        is_ignition_bull = (c_price > c_open) and (body_size > curr_atr * 0.75) and (upper_wick < body_size * 0.3)
        is_ignition_bear = (c_price < c_open) and (body_size > curr_atr * 0.75) and (lower_wick < body_size * 0.3)
        
        # Protección Dinámica contra Ruido Volátil y Filtros en Rango
        curr_regime = kwargs.get('current_regime', 'TREND')
        is_volatile = curr_regime == "VOLATILE"
        is_range = curr_regime == "RANGE"
        
        # Volumen adaptativo según perfil (aumentamos exigencia un 35% en rango lateral para confirmar fuerza real)
        vol_req = profile['vol_requisite'] * 1.35 if is_range else profile['vol_requisite']
        has_volume = rel_vol > vol_req
        
        dist_to_ema = abs(c_price - c_ema21)
        anti_fomo_ok = dist_to_ema <= curr_atr * profile['max_extension_atr']
        
        # Filtro de asentamiento basado solo en velas cerradas previas a la señal.
        prior_slice = slice(len(df_base) - 6, len(df_base) - 2)
        was_below_ema = bool((df_base['close'].iloc[prior_slice] <= ema21.iloc[prior_slice]).all())
        was_above_ema = bool((df_base['close'].iloc[prior_slice] >= ema21.iloc[prior_slice]).all())
        
        # Filtro de Lanzamiento (Anchor): La vela debe nacer cerca de la EMA (Relajado de 0.25 a 0.35 para frecuencia).
        anchor_ok_bull = abs(c_open - c_ema21) < (curr_atr * 0.35)
        anchor_ok_bear = abs(c_open - c_ema21) < (curr_atr * 0.35)
        
        # Filtro de Agotamiento RSI
        rsi_ok_bull = curr_rsi < 70
        rsi_ok_bear = curr_rsi > 30
        
        # Hysteresis dinámica por clase de activo
        hysteresis = curr_atr * profile['hysteresis_atr']
        context_ok_up = was_squeezed_recently or recent_buy_abs
        context_ok_down = was_squeezed_recently or recent_sell_abs
        
        # ADX > 20 global para evitar rangos laterales, > 25 en VOLATILE o RANGE para asegurar impulsos
        adx_ok = curr_adx > 20 if not (is_volatile or is_range) else (curr_adx > 25)
        
        is_breakout_up = (
            was_below_ema and 
            (c_price > c_ema21 + hysteresis) and 
            is_ignition_bull and 
            anti_fomo_ok and
            has_volume and
            adx_ok and
            context_ok_up and
            anchor_ok_bull and
            rsi_ok_bull
        )
        
        is_breakout_down = (
            was_above_ema and 
            (c_price < c_ema21 - hysteresis) and 
            is_ignition_bear and 
            anti_fomo_ok and
            has_volume and
            adx_ok and
            context_ok_down and
            anchor_ok_bear and
            rsi_ok_bear
        )
        
        # --- NUEVA LÓGICA DE STALKING ---
        # Si la tendencia es buena pero la entrada aún está extendida, dejamos al
        # orquestador vigilando el pullback a la EMA21 en lugar de forzar una rotura tardía.
        is_stalking_bull = False
        is_stalking_bear = False
        stalk_extension_limit = curr_atr * (profile['max_extension_atr'] + 1.1)
        if confirmed_uptrend and not is_breakout_up and (c_price > c_ema21) and (dist_to_ema > curr_atr * profile['max_extension_atr']) and (dist_to_ema <= stalk_extension_limit):
            is_stalking_bull = True
        if confirmed_downtrend and not is_breakout_down and (c_price < c_ema21) and (dist_to_ema > curr_atr * profile['max_extension_atr']) and (dist_to_ema <= stalk_extension_limit):
            is_stalking_bear = True

        threshold = entry_threshold

        # Evaluamos
        if is_breakout_up:
            mode_label = f"BREAKOUT_UP_ACTIVE_{tf_label}"
            score = 85 if confirmed_uptrend else 50 # Bloqueo Estricto Contra-Tendencia
            entry = 1
            factors_detailed.append({"k": "TF", "v": tf_label, "score": 0})
            factors_detailed.append({"k": "Estrategia", "v": f"ROTURA ↑ ({tf_label})", "score": score})
            
            # --- NUEVO: FILTRO VWAP DIARIO ---
            if c_vwap is not None:
                if c_price < c_vwap:
                    factors_detailed.append({"k": "Filtro VWAP", "v": "Precio bajo VWAP (-15)", "score": -15})
                    score -= 15
                else:
                    factors_detailed.append({"k": "Filtro VWAP", "v": "Precio sobre VWAP (+5)", "score": 5})
                    score += 5

            if not confirmed_uptrend: factors_detailed.append({"k": "Macro", "v": "Contra-Tendencia", "score": -5})
            if was_squeezed_recently: factors_detailed.append({"k": "Squeeze", "v": "Confirmado", "score": 5})
            factors_detailed.append({"k": "Cuerpo", "v": f"{(body_size/curr_atr):.1f} ATR", "score": 5})
            factors_detailed.append({"k": "Volumen", "v": f"{(rel_vol):.1f}x", "score": 5})
            if recent_buy_abs: factors_detailed.append({"k": "VSA", "v": "Absorción Alcista Previa", "score": 5})
            
            # SL un poco más ajustado para operar rápido
            swing_low = df_base['low'].tail(10).min()
            # Dinámico: Si es volátil, le damos más aire al SL (+50%)
            sl_multiplier = profile['sl_margin_atr'] * 1.5 if is_volatile else profile['sl_margin_atr']
            min_sl_dist = curr_atr * sl_multiplier 
            target_price_sl = min(swing_low - (curr_atr * 0.1), c_price - min_sl_dist)
            
            sl_dist = c_price - target_price_sl
            # TP rápido para scalping alta frecuencia adaptativo
            target_price_tp = c_price + (sl_dist * min_rr)
            
        elif is_breakout_down:
            mode_label = f"BREAKOUT_DN_ACTIVE_{tf_label}"
            score = 85 if confirmed_downtrend else 50 # Bloqueo Estricto Contra-Tendencia
            entry = -1
            factors_detailed.append({"k": "TF", "v": tf_label, "score": 0})
            factors_detailed.append({"k": "Estrategia", "v": f"ROTURA ↓ ({tf_label})", "score": score})

            # --- NUEVO: FILTRO VWAP DIARIO ---
            if c_vwap is not None:
                if c_price > c_vwap:
                    factors_detailed.append({"k": "Filtro VWAP", "v": "Precio sobre VWAP (-15)", "score": -15})
                    score -= 15
                else:
                    factors_detailed.append({"k": "Filtro VWAP", "v": "Precio bajo VWAP (+5)", "score": 5})
                    score += 5

            if not confirmed_downtrend: factors_detailed.append({"k": "Macro", "v": "Contra-Tendencia", "score": -5})
            if was_squeezed_recently: factors_detailed.append({"k": "Squeeze", "v": "Confirmado", "score": 5})
            factors_detailed.append({"k": "Cuerpo", "v": f"{(body_size/curr_atr):.1f} ATR", "score": 5})
            factors_detailed.append({"k": "Volumen", "v": f"{(rel_vol):.1f}x", "score": 5})
            if recent_sell_abs: factors_detailed.append({"k": "VSA", "v": "Distribución Bajista Previa", "score": 5})
            
            swing_high = df_base['high'].tail(10).max()
            # Dinámico: Si es volátil, le damos más aire al SL (+50%)
            sl_multiplier = profile['sl_margin_atr'] * 1.5 if is_volatile else profile['sl_margin_atr']
            min_sl_dist = curr_atr * sl_multiplier
            target_price_sl = max(swing_high + (curr_atr * 0.1), c_price + min_sl_dist)
            
            sl_dist = target_price_sl - c_price
            target_price_tp = c_price - (sl_dist * min_rr)

        elif is_stalking_bull or is_stalking_bear:
            if is_stalking_bull:
                mode_label = f"STALKING_UP_{tf_label}"
                entry = 0
                score = max(58, threshold - 14)
                factors_detailed.append({"k": "Acecho", "v": "Pullback Alcista", "score": 10})
            else:
                mode_label = f"STALKING_DN_{tf_label}"
                entry = 0
                score = max(58, threshold - 14)
                factors_detailed.append({"k": "Acecho", "v": "Pullback Bajista", "score": 10})
            target_price_tp = 0.0
            target_price_sl = 0.0

        if not entry and not (is_stalking_bull or is_stalking_bear):
            passive_score = 0
            
            status_msg = ""
            if not (confirmed_uptrend or confirmed_downtrend):
                status_msg = "Esperando Tendencia Clara"
            elif not (was_below_ema or was_above_ema):
                status_msg = "Esperando Asentamiento en EMA"
            elif not ((c_price > c_ema21 + hysteresis) or (c_price < c_ema21 - hysteresis)):
                status_msg = f"Esperando Cruce +{profile['hysteresis_atr']} ATR"
            elif not (is_ignition_bull or is_ignition_bear):
                status_msg = f"Falta Ignición (>0.65ATR)"
            elif not (context_ok_up or context_ok_down):
                status_msg = "Falta Squeeze/Absorción"
            elif not (anchor_ok_bull or anchor_ok_bear):
                status_msg = "Apertura lejana (Sin Anchor)"
            elif not (rsi_ok_bull or rsi_ok_bear):
                status_msg = "RSI en Agotamiento"
            elif not anti_fomo_ok:
                status_msg = "Precio alejado (FOMO)"
            else:
                status_msg = "Validando..."
                
            factors_detailed.append({"k": f"ESTADO ({tf_label})", "v": status_msg, "score": 0})
            
            dist_ema_atr = dist_to_ema / curr_atr if curr_atr > 0 else 1.0
            
            dist_to_lower = c_price - c_lower_bb
            dist_to_upper = c_upper_bb - c_price
            min_dist_band = min(abs(dist_to_lower), abs(dist_to_upper)) / curr_atr if curr_atr > 0 else 1.0
            
            pts_ema = max(0, (1.0 - dist_ema_atr) * 35)
            pts_bb = max(0, (1.0 - min_dist_band) * 35)
            
            zona_pts = round(max(pts_ema, pts_bb), 0)
            passive_score += zona_pts
            
            if pts_ema >= pts_bb:
                 zona_label = f"Alineación EMA ({dist_ema_atr:.1f} ATR)"
            else:
                 zona_label = f"Extensión BB ({min_dist_band:.1f} ATR)"
                 
            factors_detailed.append({"k": f"ZONA ({tf_label})", "v": zona_label, "score": zona_pts})
            
            adx_pts = round(min(curr_adx, 40) / 40 * 12, 0)
            passive_score += adx_pts
            factors_detailed.append({"k": "Fuerza (ADX)", "v": f"{curr_adx:.0f}/40", "score": adx_pts})
            
            vol_pts = round(min(rel_vol, 2.0) / 2.0 * 12, 0)
            passive_score += vol_pts
            factors_detailed.append({"k": "Volumen Rel.", "v": f"{rel_vol:.1f}x", "score": vol_pts})
            
            trend_pts = 8 if (confirmed_uptrend or confirmed_downtrend) else 0
            passive_score += trend_pts
            factors_detailed.append({"k": "Tendencia", "v": "A Favor" if trend_pts else "Mixta", "score": trend_pts})
            
            score = min(max(threshold - 1, 0), passive_score) # Siempre por debajo del umbral de entrada

        final_score = min(100, max(0, score))

        if entry != 0:
            target_sl_dist = abs(c_price - target_price_sl)
            target_tp_dist = abs(c_price - target_price_tp)
            
            if spread_dist > (target_sl_dist * 0.35):
                final_score -= 30
                entry = 0
                factors_detailed.append({
                    "k": "Coste Spread", "v": "ALTO", "score": -30, 
                    "desc": "Spread demasiado alto que consume el R:R"
                })
                mode_label = f"SPREAD_ALTO_{tf_label}"
            else:
                factors_detailed.append({"k": "TP Lógico", "v": "Rápido (1.3R)", "score": 0})
                factors_detailed.append({"k": "SL Lógico", "v": "Dinámico", "score": 0})

        is_stalking = True if (is_stalking_bull or is_stalking_bear) else False

        return {
            "strategy": self.STRATEGY_NAME,
            "score": final_score,
            "signal": "BUY" if entry == 1 else ("SELL" if entry == -1 else "NEUTRAL"),
            "entry": entry,
            "is_stalking": is_stalking,
            "direction": 1 if (entry == 1 or confirmed_uptrend) else (-1 if (entry == -1 or confirmed_downtrend) else 0),
            "target_price": target_price_tp if target_price_tp > 0 else c_ema21,
            "atr": curr_atr,
            "metadata": {
                "mode": mode_label,
                "factors_detailed": factors_detailed,
                "rsi": round(curr_rsi, 1),
                "adx": round(curr_adx, 1),
                "target_price_tp": round(target_price_tp, 5) if entry != 0 else 0,
                "target_price_sl": round(target_price_sl, 5) if entry != 0 else 0,
                "rr_ratio": round(min_rr, 2),
                "score_threshold": round(threshold, 2),
                "use_breakeven": False, 
                "threshold_used": threshold
            }
        }

    def check_exit_signal(self, mtf_data, p_type: str) -> bool:
        """
        Salida dinámica rápida: Si cruzamos la EMA al revés cerramos.
        """
        df_m1 = mtf_data.get('m1') if isinstance(mtf_data, dict) else mtf_data
        if df_m1 is None or len(df_m1) < 25: return False
        
        signal_idx = -2
        c_price = df_m1['close'].iloc[signal_idx]
        ema21 = ta.ema(df_m1['close'], length=self.ema_mid)
        atr_series = ta.atr(df_m1['high'], df_m1['low'], df_m1['close'], length=14)
        
        if ema21 is None or atr_series is None: return False
        
        c_ema21 = ema21.iloc[signal_idx]
        curr_atr = atr_series.iloc[signal_idx]
        
        # Salida por cruce contrario de EMA21 con Histéresis de seguridad
        # Añadimos un pequeño buffer (10% del ATR) para evitar cierres por ruido/spread
        exit_buffer = curr_atr * 0.10
        
        if p_type == "BUY" and c_price < (c_ema21 - exit_buffer):
            return True
        if p_type == "SELL" and c_price > (c_ema21 + exit_buffer):
            return True
        return False
