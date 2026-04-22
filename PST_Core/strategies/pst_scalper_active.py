import pandas_ta as ta
import pandas as pd
import numpy as np
import logging
from ..config import (
    SL_ATR_MULTIPLIER, 
    MAX_SCALPER_SL_POINTS, 
    MIN_RR_RATIO
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
                "min_rr": 1.5,             
                "hysteresis_atr": 0.35,    
                "sl_margin_atr": 1.5,
                "max_extension_atr": 1.0 # Reducido de 1.4: Evita comprar en techos
            },
            "METAL": {
                "vol_requisite": 1.25,      
                "min_rr": 1.5,            
                "hysteresis_atr": 0.15,    
                "sl_margin_atr": 2.0,
                "max_extension_atr": 1.0 # Reducido de 1.2
            },
            "INDEX": {
                "vol_requisite": 1.25,
                "min_rr": 1.5,
                "hysteresis_atr": 0.2,
                "sl_margin_atr": 2.5,
                "max_extension_atr": 1.0 # Reducido de 1.2
            },
            "FOREX": {
                "vol_requisite": 1.15,
                "min_rr": 1.3,
                "hysteresis_atr": 0.15,
                "sl_margin_atr": 1.5,
                "max_extension_atr": 1.1
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

        ema21 = ta.ema(df_base['close'], length=self.ema_mid)
        rsi = ta.rsi(df_base['close'], length=self.rsi_length)
        atr = ta.atr(df_base['high'], df_base['low'], df_base['close'], length=14)
        vol_ma = ta.sma(df_base['tick_volume'], length=20)
        adx_df = ta.adx(df_base['high'], df_base['low'], df_base['close'], length=14)
        bb = ta.bbands(df_base['close'], length=self.bb_length, std=self.bb_std)
        kc = ta.kc(df_base['high'], df_base['low'], df_base['close'], length=20, scalar=1.5)
        
        if ema21 is None or rsi is None or atr is None or bb is None or kc is None or vol_ma is None or adx_df is None:
            return {"score": 0, "signal": "NEUTRAL", "metadata": {"mode": f"CALCULANDO_{tf_label}", "factors_detailed": []}}

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
        
        # Volumen adaptativo según perfil
        has_volume = rel_vol > profile['vol_requisite']
        
        dist_to_ema = abs(c_price - c_ema21)
        anti_fomo_ok = dist_to_ema <= curr_atr * profile['max_extension_atr']
        
        # Filtro de asentamiento basado solo en velas cerradas previas a la señal.
        prior_slice = slice(len(df_base) - 6, len(df_base) - 2)
        was_below_ema = bool((df_base['close'].iloc[prior_slice] <= ema21.iloc[prior_slice]).all())
        was_above_ema = bool((df_base['close'].iloc[prior_slice] >= ema21.iloc[prior_slice]).all())
        
        # Filtro de Lanzamiento (Anchor): La vela debe nacer muy cerca de la EMA.
        anchor_ok_bull = abs(c_open - c_ema21) < (curr_atr * 0.25)
        anchor_ok_bear = abs(c_open - c_ema21) < (curr_atr * 0.25)
        
        # Filtro de Agotamiento RSI
        rsi_ok_bull = curr_rsi < 70
        rsi_ok_bear = curr_rsi > 30
        
        # Hysteresis dinámica por clase de activo
        hysteresis = curr_atr * profile['hysteresis_atr']
        context_ok_up = was_squeezed_recently or recent_buy_abs
        context_ok_down = was_squeezed_recently or recent_sell_abs
        
        # Protección Dinámica contra Ruido Volátil (ADX > 22)
        curr_regime = kwargs.get('current_regime', 'TREND')
        is_volatile = curr_regime == "VOLATILE"
        # ADX > 20 global para evitar rangos laterales, > 25 en VOLATILE para asegurar impulsos
        adx_ok = curr_adx > 20 if not is_volatile else (curr_adx > 25)
        
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
        
        # --- NUEVA LÓGICA DE STALKING (DESHABILITADA TRAS AUDITORÍA) ---
        # Se ha demostrado ineficiente absorbiendo ruido y giros falsos.
        # Deshabilitado para forzar a la estrategia a operar solo Breakouts con volumen institutivo.
        is_stalking_bull = False
        is_stalking_bear = False

        threshold = kwargs.get('score_threshold', 70) # Bajar threshold

        # Evaluamos
        if is_breakout_up:
            mode_label = f"BREAKOUT_UP_ACTIVE_{tf_label}"
            score = 85 if confirmed_uptrend else 50 # Bloqueo Estricto Contra-Tendencia
            entry = 1
            factors_detailed.append({"k": "TF", "v": tf_label, "score": 0})
            factors_detailed.append({"k": "Estrategia", "v": f"ROTURA ↑ ({tf_label})", "score": score})
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
            target_price_tp = c_price + (sl_dist * profile['min_rr'])
            
        elif is_breakout_down:
            mode_label = f"BREAKOUT_DN_ACTIVE_{tf_label}"
            score = 85 if confirmed_downtrend else 50 # Bloqueo Estricto Contra-Tendencia
            entry = -1
            factors_detailed.append({"k": "TF", "v": tf_label, "score": 0})
            factors_detailed.append({"k": "Estrategia", "v": f"ROTURA ↓ ({tf_label})", "score": score})
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
            target_price_tp = c_price - (sl_dist * profile['min_rr'])

        if not entry:
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
            
            score = min(74, passive_score) # Maximo 74 si no hay entry

        final_score = min(100, max(0, score))

        if entry != 0:
            target_sl_dist = abs(c_price - target_price_sl)
            target_tp_dist = abs(c_price - target_price_tp)
            
            if spread_dist > (target_sl_dist * 0.40):  # Mayor tolerancia al spread (hasta un 40% del SL en vez del TP)
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

        is_stalking = True if (entry != 0 and 'STALKING' in mode_label) else False

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
        
        c_price = df_m1['close'].iloc[-1]
        ema21 = ta.ema(df_m1['close'], length=self.ema_mid)
        atr_series = ta.atr(df_m1['high'], df_m1['low'], df_m1['close'], length=14)
        
        if ema21 is None or atr_series is None: return False
        
        c_ema21 = ema21.iloc[-1]
        curr_atr = atr_series.iloc[-1]
        
        # Salida por cruce contrario de EMA21 con Histéresis de seguridad
        # Añadimos un pequeño buffer (10% del ATR) para evitar cierres por ruido/spread
        exit_buffer = curr_atr * 0.10
        
        if p_type == "BUY" and c_price < (c_ema21 - exit_buffer):
            return True
        if p_type == "SELL" and c_price > (c_ema21 + exit_buffer):
            return True
        return False
