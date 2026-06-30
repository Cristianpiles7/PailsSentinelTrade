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

logger = logging.getLogger("PST-Scalper-Pro")

class PSTScalperPro:
    STRATEGY_NAME = "PST-Scalper-Pro"
    STRATEGY_TYPE = "ALL"
    
    def __init__(self):
        self.ema_mid = 21
        self.rsi_length = 14
        self.bb_length = 20
        self.bb_std = 2.0
        self.min_rr = MIN_RR_RATIO
        self.ASSET_PROFILES = {
            "CRYPTO": {
                "vol_requisite": 1.35,
                "min_rr": 1.80,
                "hysteresis_atr": 0.30,
                "max_extension_atr": 0.95,
                "score_threshold": 84,
            },
            "METAL": {
                "vol_requisite": 1.30,
                "min_rr": 1.85,
                "hysteresis_atr": 0.18,
                "max_extension_atr": 0.90,
                "score_threshold": 86,
            },
            "INDEX": {
                "vol_requisite": 1.35,
                "min_rr": 1.90,
                "hysteresis_atr": 0.22,
                "max_extension_atr": 0.90,
                "score_threshold": 88,
            },
            "FOREX": {
                "vol_requisite": 1.20,
                "min_rr": 1.70,
                "hysteresis_atr": 0.18,
                "max_extension_atr": 1.00,
                "score_threshold": 82,
            },
        }
        self.DEFAULT_PROFILE = self.ASSET_PROFILES["FOREX"]

    async def calculate_signal(self, mtf_data, current_regime=None, user_levels=None, spread_points=0, spread_dist=0, **kwargs):
        """
        Scalper Pro v6.5 (Omni-Timeframe): EMA Breakout + Telemetría Dinámica.
        """
        if current_regime == "OFFLINE":
            return {
                "score": 0,
                "signal": "NEUTRAL",
                "metadata": {"mode": "OFFLINE", "factors_detailed": []},
            }

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
        
        # 1. Evaluar M5 (Prioridad Alta - Señal más limpia)
        if df_m5 is not None and len(df_m5) >= 50:
            trend_df = df_m15 if (df_m15 is not None and len(df_m15) >= 50) else None
            sig_m5 = self._evaluate_tf(df_m5, trend_df, None, spread_dist, "M5", mtf_data=mtf_data, **kwargs)
            if sig_m5['entry'] != 0: return sig_m5
            signals_found.append(sig_m5)

        # 2. Evaluar M3 (Frame Intermedio)
        if df_m3 is not None and len(df_m3) >= 50:
            sig_m3 = self._evaluate_tf(df_m3, df_m15, df_m5, spread_dist, "M3", mtf_data=mtf_data, **kwargs)
            if sig_m3['entry'] != 0: return sig_m3
            signals_found.append(sig_m3)
                
        # 3. Evaluar M1 (Frecuencia)
        if df_m1 is not None and len(df_m1) >= 50:
            sig_m1 = self._evaluate_tf(df_m1, df_m5, df_m3, spread_dist, "M1", mtf_data=mtf_data, **kwargs)
            if sig_m1['entry'] != 0: return sig_m1
            signals_found.append(sig_m1)
            
        # Si nadie ha roto (Sin entry), devolvemos el TF que tenga el 'score' más alto 
        # (El caso más cercano a activarse) para mostrar la información en la UI
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
        asset_class = get_asset_class(symbol)
        profile = self.ASSET_PROFILES.get(asset_class, self.DEFAULT_PROFILE)
        signal_idx = -2

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
            c_vwap = vwap.iloc[signal_idx]
        except Exception as e:
            logger.warning(f"Error calculando VWAP en Pro: {e}")

        try:
            lower_bb = bb.iloc[:, 0]
            mid_bb = bb.iloc[:, 1]
            upper_bb = bb.iloc[:, 2]
            lower_kc = kc.iloc[:, 0]
            upper_kc = kc.iloc[:, 2]
            
            is_squeeze_series = (upper_bb < upper_kc) & (lower_bb > lower_kc)
            is_squeeze = is_squeeze_series.iloc[signal_idx]
            was_squeezed_recently = is_squeeze_series.iloc[-17:signal_idx].any()
        except Exception as e:
            is_squeeze, was_squeezed_recently = False, False
            try:
                lower_bb = bb.iloc[:, 0]
                mid_bb = bb.iloc[:, 1]
                upper_bb = bb.iloc[:, 2]
            except Exception:
                return {"score": 0, "signal": "NEUTRAL", "metadata": {"mode": f"Error_BB_{tf_label}", "factors_detailed": []}}

        c_price = df_base['close'].iloc[signal_idx]
        c_open = df_base['open'].iloc[signal_idx]
        c_rsi = rsi.iloc[signal_idx]
        body_size = abs(c_price - c_open)
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

        # Tendencia Superior
        confirmed_uptrend = False
        confirmed_downtrend = False
        if df_trend1 is not None:
            ema50_t1 = ta.ema(df_trend1['close'], length=50)
            if ema50_t1 is not None and df_trend1['close'].iloc[-2] > ema50_t1.iloc[-2]: confirmed_uptrend = True
            if ema50_t1 is not None and df_trend1['close'].iloc[-2] < ema50_t1.iloc[-2]: confirmed_downtrend = True
            
        if df_trend2 is not None:
            ema50_t2 = ta.ema(df_trend2['close'], length=50)
            if ema50_t2 is not None and df_trend2['close'].iloc[-2] > ema50_t2.iloc[-2]: confirmed_uptrend = True
            if ema50_t2 is not None and df_trend2['close'].iloc[-2] < ema50_t2.iloc[-2]: confirmed_downtrend = True

        # --- NEW: FILTRO ANTI-TECHOS M15 (v2.0.1) ---
        is_overextended_up = False
        is_overextended_down = False
        if df_trend1 is not None and len(df_trend1) >= 50:
            ema50_m15 = ta.ema(df_trend1['close'], length=50).iloc[-2]
            atr_m15 = ta.atr(df_trend1['high'], df_trend1['low'], df_trend1['close'], length=14).iloc[-2]
            if (c_price - ema50_m15) > (atr_m15 * 2.6): # Relajado v2.0.2
                is_overextended_up = True
            if (ema50_m15 - c_price) > (atr_m15 * 2.6):
                is_overextended_down = True

        # --- NUEVO v2.0.3: ALINEACIÓN M1 (MICRO-GOLDEN CROSS) ---
        m1_aligned_up = False
        m1_aligned_down = False
        mtf_data = kwargs.get('mtf_data')
        df_m1 = mtf_data.get('m1') if isinstance(mtf_data, dict) else None
        if df_m1 is not None and len(df_m1) >= 50:
            m1_ema21 = ta.ema(df_m1['close'], length=21).iloc[-2]
            m1_ema50 = ta.ema(df_m1['close'], length=50).iloc[-2]
            if m1_ema21 > m1_ema50: m1_aligned_up = True
            if m1_ema21 < m1_ema50: m1_aligned_down = True

        min_rr = float(kwargs.get("min_rr", profile["min_rr"]) or profile["min_rr"])
        asset_threshold = float(kwargs.get("score_threshold", profile["score_threshold"]) or profile["score_threshold"])
        threshold = max(asset_threshold, profile["score_threshold"])

        score = 0
        entry = 0
        factors_detailed = []
        mode_label = f"ACECHANDO_{tf_label}"

        target_price_tp = 0.0
        target_price_sl = 0.0

        # Filtro VSA (Volume Spread Analysis)
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
            
            if _v_rel > profile["vol_requisite"]:
                if _lower_wick > (_body * 2): recent_buy_abs = True
                if _upper_wick > (_body * 2): recent_sell_abs = True

        is_ignition_bull = (c_price > c_open) and (body_size > curr_atr * 0.5)
        is_ignition_bear = (c_price < c_open) and (body_size > curr_atr * 0.5)
        
        has_volume = rel_vol > profile["vol_requisite"]
        
        # [OPCIÓN B] Hysteresis Dinámico (Agilidad v6.6): 
        hysteresis = curr_atr * profile["hysteresis_atr"]
        
        dist_to_ema = abs(c_price - c_ema21)
        anti_fomo_ok = dist_to_ema <= curr_atr * profile["max_extension_atr"]
        
        prior_slice = slice(len(df_base) - 6, len(df_base) - 2)
        was_below_ema = bool((df_base['close'].iloc[prior_slice] <= ema21.iloc[prior_slice]).all())
        was_above_ema = bool((df_base['close'].iloc[prior_slice] >= ema21.iloc[prior_slice]).all())
        
        # --- NUEVO v2.0.5: TRIPLE BREAKOUT DETECTION ---
        # Detectamos si el precio está rompiendo el conjunto de EMA9, 21 y 50
        ema9_series = ta.ema(df_base['close'], length=9)
        ema50_series = ta.ema(df_base['close'], length=50)
        ema9 = ema9_series.iloc[signal_idx]
        ema50 = ema50_series.iloc[signal_idx]
        
        prev_ema50 = ema50_series.iloc[signal_idx - 1]
        is_breaking_ema50_up = (df_base['close'].iloc[signal_idx - 1] < prev_ema50) and (c_price > ema50)
        is_above_all_emas = c_price > ema9 and c_price > c_ema21 and c_price > ema50
        
        is_perfect_breakout_up = (
            was_below_ema and 
            (c_price > c_ema21 + hysteresis) and 
            (was_squeezed_recently or recent_buy_abs or is_breaking_ema50_up) and 
            is_ignition_bull and 
            (rel_vol > 1.2 or has_volume) and # Endurecido v2.0.6: Mínimo 1.2x siempre
            anti_fomo_ok and
            not is_overextended_up
        )
        
        is_perfect_breakout_down = (
            was_above_ema and 
            (c_price < c_ema21 - hysteresis) and 
            (was_squeezed_recently or recent_sell_abs) and 
            is_ignition_bear and 
            has_volume and 
            anti_fomo_ok and
            not is_overextended_down
        )
        
        if is_perfect_breakout_up:
            mode_label = f"BREAKOUT_UP_V6_{tf_label}"
            # Penalizamos la rotura "Counter-Trend" con 10 puntos menos para requerir confirmación por el usuario
            # v2.0.3: Si M1 ya ha cruzado, le damos permiso para disparar (Score 82)
            if confirmed_uptrend or m1_aligned_up or is_above_all_emas:
                score = 85 if (confirmed_uptrend or is_above_all_emas) else 82
            else:
                score = 75 # Sigue bloqueado si ni M1 ni M15 están a favor
                
            entry = 1
            factors_detailed.append({"k": "TF", "v": tf_label, "score": 0})
            factors_detailed.append({"k": "Estrategia", "v": f"ROTURA EMA21 ↑ ({tf_label})", "score": score})
            
            # --- NUEVO: FILTRO VWAP DIARIO ---
            if c_vwap is not None:
                if c_price < c_vwap:
                    factors_detailed.append({"k": "Filtro VWAP", "v": "Precio bajo VWAP (-15)", "score": -15})
                    score -= 15
                else:
                    factors_detailed.append({"k": "Filtro VWAP", "v": "Precio sobre VWAP (+5)", "score": 5})
                    score += 5

            if is_above_all_emas:
                factors_detailed.append({"k": "Triple Breakout", "v": "SMA9/21/50 Superadas (+10)", "score": 10})
                score += 10
            
            # --- NUEVO v2.0.6: FILTRO DE AGOTAMIENTO RSI ---
            if c_rsi > 65:
                factors_detailed.append({"k": "Agotamiento", "v": f"RSI {c_rsi:.1f} > 65 (-30)", "score": -30})
                score -= 30
                factors_detailed.append({"k": "Peligro Macro", "v": "Contra-Tendencia (-10)", "score": -10})
            elif (m1_aligned_up or is_above_all_emas) and not confirmed_uptrend:
                factors_detailed.append({"k": "Giro Confirmado", "v": "Alineación M1/Triple (+5)", "score": 5})
            if was_squeezed_recently: factors_detailed.append({"k": "Squeeze", "v": "Confirmado", "score": 5})
            factors_detailed.append({"k": "Ignición", "v": f"{(body_size/curr_atr):.1f} ATR", "score": 5})
            factors_detailed.append({"k": "Volumen", "v": f"{(rel_vol):.1f}x", "score": 5})
            if recent_buy_abs: factors_detailed.append({"k": "SMC (VSA)", "v": "Absorción Alcista Previa", "score": 10})
            
            swing_low = df_base['low'].tail(15).min()
            min_sl_dist = curr_atr * 1.0 
            target_price_sl = min(swing_low - (curr_atr * 0.2), c_price - min_sl_dist)
            
            sl_dist = c_price - target_price_sl
            target_price_tp = max(c_upper_bb, c_price + (sl_dist * min_rr))
            
        elif is_perfect_breakout_down:
            mode_label = f"BREAKOUT_DN_V6_{tf_label}"
            score = 85 if confirmed_downtrend else 75
            entry = -1
            factors_detailed.append({"k": "TF", "v": tf_label, "score": 0})
            factors_detailed.append({"k": "Estrategia", "v": f"ROTURA EMA21 ↓ ({tf_label})", "score": score})

            # --- NUEVO: FILTRO VWAP DIARIO ---
            if c_vwap is not None:
                if c_price > c_vwap:
                    factors_detailed.append({"k": "Filtro VWAP", "v": "Precio sobre VWAP (-15)", "score": -15})
                    score -= 15
                else:
                    factors_detailed.append({"k": "Filtro VWAP", "v": "Precio bajo VWAP (+5)", "score": 5})
                    score += 5

            if not confirmed_downtrend: factors_detailed.append({"k": "Peligro Macro", "v": "Contra-Tendencia (-10)", "score": -10})
            if was_squeezed_recently: factors_detailed.append({"k": "Squeeze", "v": "Confirmado", "score": 5})
            factors_detailed.append({"k": "Ignición", "v": f"{(body_size/curr_atr):.1f} ATR", "score": 5})
            factors_detailed.append({"k": "Volumen", "v": f"{(rel_vol):.1f}x", "score": 5})
            if recent_sell_abs: factors_detailed.append({"k": "SMC (VSA)", "v": "Distribución Bajista Previa", "score": 10})
            
            # --- NUEVO v2.0.6: FILTRO DE AGOTAMIENTO RSI (SELL) ---
            if c_rsi < 35:
                factors_detailed.append({"k": "Agotamiento", "v": f"RSI {c_rsi:.1f} < 35 (-30)", "score": -30})
                score -= 30
            
            swing_high = df_base['high'].tail(15).max()
            min_sl_dist = curr_atr * 1.0
            target_price_sl = max(swing_high + (curr_atr * 0.2), c_price + min_sl_dist)
            
            sl_dist = target_price_sl - c_price
            target_price_tp = min(c_lower_bb, c_price - (sl_dist * min_rr))

        if not entry:
            passive_score = 0
            
            # --- EVALUACIÓN DE CHECKLIST (Para el Usuario) ---
            status_msg = ""
            if not (confirmed_uptrend or confirmed_downtrend):
                status_msg = "Esperando Tendencia Clara"
            elif not (was_squeezed_recently or recent_buy_abs or recent_sell_abs):
                status_msg = "Falta Squeeze/Absorción"
            elif not (was_below_ema or was_above_ema):
                status_msg = "Esperando Asentamiento"
            elif not ((c_price > c_ema21 + hysteresis) or (c_price < c_ema21 - hysteresis)):
                status_msg = "Esperando Cruce de EMA"
            elif not has_volume:
                status_msg = "Falta Volumen (>1.5x)"
            elif not (is_ignition_bull or is_ignition_bear):
                status_msg = "Falta Vela de Ignición"
            elif not anti_fomo_ok:
                status_msg = "Precio alejado (FOMO)"
            else:
                status_msg = "Validando..."
                
            factors_detailed.append({"k": f"ESTADO ({tf_label})", "v": status_msg, "score": 0, "desc": "Indica exactamente qué requisito estructural falta para poder disparar la orden."})
            
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
                 zona_desc = "Precio acercándose a la EMA21 buscando rotura direccional. Cuanto más cerca, mayor puntuación."
            else:
                 zona_label = f"Extensión BB ({min_dist_band:.1f} ATR)"
                 zona_desc = "Precio acercándose a las bandas externas buscando volatilidad extrema o rechazo. Piercing = 35 pts."
                 
            factors_detailed.append({"k": f"ZONA ({tf_label})", "v": zona_label, "score": zona_pts, "desc": zona_desc})
            
            adx_pts = round(min(curr_adx, 40) / 40 * 12, 0)
            passive_score += adx_pts
            factors_detailed.append({"k": "Fuerza (ADX)", "v": f"{curr_adx:.0f} de 40", "score": adx_pts, "desc": "Mide la inercia."})
            
            dist_rsi_50 = abs(curr_rsi - 50) 
            rsi_pts = round(min(dist_rsi_50 / 25, 1.0) * 12, 0)
            passive_score += rsi_pts
            factors_detailed.append({"k": "RSI", "v": f"{curr_rsi:.0f}", "score": rsi_pts, "desc": "Presión del precio oscilante."})
            
            vol_pts = round(min(rel_vol, 2.0) / 2.0 * 12, 0)
            passive_score += vol_pts
            factors_detailed.append({"k": "Volumen Rel.", "v": f"{rel_vol:.1f}x", "score": vol_pts, "desc": "Interés institucional."})
            
            trend_pts = 8 if (confirmed_uptrend or confirmed_downtrend) else 0
            passive_score += trend_pts
            factors_detailed.append({"k": "Tendencia", "v": "A Favor" if trend_pts else "Lucha Mixta", "score": trend_pts, "desc": "Respaldo tendencial."})
            
            factors_detailed.append({"k": "Squeeze/Institucional", "v": "SÍ" if (was_squeezed_recently or recent_buy_abs or recent_sell_abs) else "NO", "score": 0, "desc": "Compresión o participación fuerte detectada."})
            factors_detailed.append({"k": "Cuerpo Vela", "v": f"{body_ratio*100:.0f}%", "score": 0, "desc": "Fuerza direccional interna de la vela."})
            
            score = min(max(threshold - 1, 0), passive_score)

        final_score = min(100, max(0, score))

        if entry != 0:
            target_sl_dist = abs(c_price - target_price_sl)
            target_tp_dist = abs(c_price - target_price_tp)
            
            if spread_dist > (target_sl_dist * 0.35):
                final_score -= 30
                entry = 0
                factors_detailed.append({
                    "k": "Coste Spread", "v": "ALTO", "score": -30, 
                    "desc": "Spread demasiado alto que consume el R:R para Scalping."
                })
                mode_label = f"SPREAD_ALTO_{tf_label}"
            else:
                factors_detailed.append({"k": "TP Lógico", "v": "Dinámico", "score": 0})
                factors_detailed.append({"k": "SL Lógico", "v": "Dinámico", "score": 0})

        # Desactivamos el "Stalking" (Acecho). El Scalper Pro SMC no compra support drops,
        # solo interviene si ocurre un Perfect Breakout institucional absoluto. 
        is_stalking = False

        return {
            "strategy": self.STRATEGY_NAME,
            "score": final_score,
            "signal": "BUY" if entry == 1 else ("SELL" if entry == -1 else "NEUTRAL"),
            "entry": entry,
            "is_stalking": is_stalking,
            "direction": 1 if (is_perfect_breakout_up or confirmed_uptrend) else (-1 if (is_perfect_breakout_down or confirmed_downtrend) else 0),
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
        Salida dinámica: cruce de EMA21 en vela cerrada con buffer de seguridad.
        """
        df_m1 = mtf_data.get('m1') if isinstance(mtf_data, dict) else mtf_data
        if df_m1 is None or len(df_m1) < 25:
            return False

        signal_idx = -2
        c_price = df_m1['close'].iloc[signal_idx]
        ema21 = ta.ema(df_m1['close'], length=self.ema_mid)
        atr_series = ta.atr(df_m1['high'], df_m1['low'], df_m1['close'], length=14)

        if ema21 is None or atr_series is None:
            return False

        c_ema21 = ema21.iloc[signal_idx]
        curr_atr = atr_series.iloc[signal_idx]
        exit_buffer = curr_atr * 0.10

        if p_type == "BUY" and c_price < (c_ema21 - exit_buffer):
            return True
        if p_type == "SELL" and c_price > (c_ema21 + exit_buffer):
            return True
        return False
