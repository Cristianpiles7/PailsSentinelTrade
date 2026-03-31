import pandas_ta as ta
import pandas as pd
import numpy as np
import logging
from ..config import (
    SL_ATR_MULTIPLIER, 
    MAX_SCALPER_SL_POINTS, 
    MIN_RR_RATIO
)

logger = logging.getLogger("PST-Scalper-Pro")

class PSTScalperPro:
    STRATEGY_NAME = "PST-Scalper-Pro"
    STRATEGY_TYPE = "ALL"
    
    def __init__(self):
        self.ema_mid = 21
        self.rsi_length = 14
        self.bb_length = 20
        self.bb_std = 2.0
        # Mínimo R:R exigido internamente (Sync con config.py)
        self.min_rr = 1.8

    async def calculate_signal(self, mtf_data, current_regime=None, user_levels=None, spread_points=0, spread_dist=0, **kwargs):
        """
        Scalper Pro v4.0 (Dual Mode): EMA Breakout + Bollinger Mean Reversion.
        """
        # 1. Extracción de datos
        df_m1 = mtf_data.get('m1') if isinstance(mtf_data, dict) else mtf_data
        df_m3 = mtf_data.get('m3')
        df_m5 = mtf_data.get('m5')
        
        if df_m1 is None or len(df_m1) < 50:
            return {
                "score": 0, 
                "signal": "NEUTRAL", 
                "metadata": {
                    "mode": "ESPERANDO_DATOS",
                    "factors_detailed": [{"k": "Estado", "v": "Esperando Datos (M1)", "score": 0}]
                }
            }

        # 2. Cálculo de Indicadores M1
        ema21 = ta.ema(df_m1['close'], length=self.ema_mid)
        rsi = ta.rsi(df_m1['close'], length=self.rsi_length)
        atr = ta.atr(df_m1['high'], df_m1['low'], df_m1['close'], length=14)
        vol_ma = ta.sma(df_m1['tick_volume'], length=20)
        adx_df = ta.adx(df_m1['high'], df_m1['low'], df_m1['close'], length=14)
        bb = ta.bbands(df_m1['close'], length=self.bb_length, std=self.bb_std)
        kc = ta.kc(df_m1['high'], df_m1['low'], df_m1['close'], length=20, scalar=1.5)
        
        if ema21 is None or rsi is None or atr is None or bb is None or kc is None or vol_ma is None or adx_df is None:
            return {
                "score": 0, 
                "signal": "NEUTRAL", 
                "metadata": {
                    "mode": "CALCULANDO",
                    "factors_detailed": [{"k": "Estado", "v": "Calculando Indicadores", "score": 0}]
                }
            }

        try:
            lower_bb = bb.iloc[:, 0]
            mid_bb = bb.iloc[:, 1]
            upper_bb = bb.iloc[:, 2]
            
            lower_kc = kc.iloc[:, 0]
            upper_kc = kc.iloc[:, 2]
            
            # Error fixed: must use bitwise '&' for pandas series operations
            # Compresión reciente requerida por la Regla 1 (v5.0)
            is_squeeze_series = (upper_bb < upper_kc) & (lower_bb > lower_kc)
            is_squeeze = is_squeeze_series.iloc[-1]
            was_squeezed_recently = is_squeeze_series.iloc[-15:-1].any()
        except Exception as e:
            is_squeeze = False
            was_squeezed_recently = False

            try:
                # Intento de rescate de Bollinger si falla Keltner
                lower_bb = bb.iloc[:, 0]
                mid_bb = bb.iloc[:, 1]
                upper_bb = bb.iloc[:, 2]
            except Exception:
                return {"score": 0, "signal": "NEUTRAL", "metadata": {"mode": "CALCULANDO_BB", "factors_detailed": []}}

        # 3. Datos en Punto de Decisión (M1)
        c_price = df_m1['close'].iloc[-1]
        p_c_price = df_m1['close'].iloc[-2]
        p_p_c_price = df_m1['close'].iloc[-3]
        
        c_ema21 = ema21.iloc[-1]
        p_ema21 = ema21.iloc[-2]
        p_p_ema21 = ema21.iloc[-3]
        
        curr_atr = atr.iloc[-1]
        curr_rsi = rsi.iloc[-1]
        p_rsi = rsi.iloc[-2]
        curr_adx = adx_df['ADX_14'].iloc[-1]

        c_upper_bb = upper_bb.iloc[-1]
        c_lower_bb = lower_bb.iloc[-1]
        p_upper_bb = upper_bb.iloc[-2]
        p_lower_bb = lower_bb.iloc[-2]
        
        # Volumen
        curr_vol = df_m1['tick_volume'].iloc[-1]
        mean_vol = vol_ma.iloc[-2]
        rel_vol = curr_vol / mean_vol if mean_vol > 0 else 1.0
        
        # Velas
        c_open = df_m1['open'].iloc[-1]
        c_high = df_m1['high'].iloc[-1]
        c_low = df_m1['low'].iloc[-1]
        body_size = abs(c_price - c_open)
        total_range = max(0.00001, c_high - c_low)
        body_ratio = body_size / total_range

        # 4. Filtro de Tendencia Superior (M3 y M5)
        trend_m5 = 0
        if df_m5 is not None and len(df_m5) >= 50:
            ema50_m5 = ta.ema(df_m5['close'], length=50)
            if ema50_m5 is not None:
                trend_m5 = 1 if df_m5['close'].iloc[-1] > ema50_m5.iloc[-1] else -1

        trend_m3 = 0
        if df_m3 is not None and len(df_m3) >= 50:
            ema50_m3 = ta.ema(df_m3['close'], length=50)
            if ema50_m3 is not None:
                trend_m3 = 1 if df_m3['close'].iloc[-1] > ema50_m3.iloc[-1] else -1

        # Si no hay datos suficientes de una, usamos la otra para confirmar robustez
        confirmed_uptrend = (trend_m5 == 1 or trend_m3 == 1)
        confirmed_downtrend = (trend_m5 == -1 or trend_m3 == -1)

        score = 0
        entry = 0
        factors_detailed = []
        mode_label = "ACECHANDO"

        # Lógica de TP y SL dinámico
        target_price_tp = 0.0
        target_price_sl = 0.0

        # --- REGLAS MAESTRAS SCALPER PRO v5.0 (SMC Upgraded) ---
        # 0. Filtro VSA (Volume Spread Analysis): ¿Hubo acumulación/distribución institucional reciente?
        recent_buy_abs = False
        recent_sell_abs = False
        for i in range(-15, 0):
            idx = len(df_m1) + i
            if idx < 0: continue
            _body = abs(df_m1['close'].iloc[idx] - df_m1['open'].iloc[idx])
            _lower_wick = min(df_m1['open'].iloc[idx], df_m1['close'].iloc[idx]) - df_m1['low'].iloc[idx]
            _upper_wick = df_m1['high'].iloc[idx] - max(df_m1['open'].iloc[idx], df_m1['close'].iloc[idx])
            _v_ma = vol_ma.iloc[idx] if vol_ma is not None else 1
            _v_rel = df_m1['tick_volume'].iloc[idx] / _v_ma if _v_ma > 0 else 0
            
            if _v_rel > 1.5: # Clímax
                if _lower_wick > (_body * 2): recent_buy_abs = True
                if _upper_wick > (_body * 2): recent_sell_abs = True

        # 1. Filtro Squeeze: ¿Hubo compresión reciente en las últimas 15 velas? (was_squeezed_recently)
        # 2. Ignición: Cuerpo de vela rotura > 0.5 ATR
        # 3. Volumen: Volumen relativo > 1.5x
        # 4. Anti-FOMO: Distancia de cierre a la EMA < 1.5 ATR

        is_ignition_bull = (c_price > c_open) and (body_size > curr_atr * 0.5)
        is_ignition_bear = (c_price < c_open) and (body_size > curr_atr * 0.5)
        
        has_volume = rel_vol > 1.5
        
        dist_to_ema = abs(c_price - c_ema21)
        anti_fomo_ok = dist_to_ema <= curr_atr * 1.5
        
        # Verificamos que estaba claramente al otro lado en las velas anteriores (Asentamiento)
        was_below_ema = all(df_m1['close'].iloc[-5:-1] <= ema21.iloc[-5:-1])
        was_above_ema = all(df_m1['close'].iloc[-5:-1] >= ema21.iloc[-5:-1])
        
        # Opcional: Hysteresis (superar EMA por un pequeño margen)
        hysteresis = curr_atr * 0.2
        
        # --- FILTROS POR PUNTUACIÓN (TIERS) ---
        # Tier 1 (Perfect): Squeeze + Volumen > 1.5x + Ignición 0.5 ATR
        # Tier 2 (Strong): Volumen > 1.2x + Ignición 0.3 ATR + Squeeze opcional
        
        is_perfect_breakout_up = (
            was_below_ema and 
            (c_price > c_ema21 + hysteresis) and 
            confirmed_uptrend and 
            (was_squeezed_recently or recent_buy_abs) and # SMC: Squeeze o Absorción Institucional
            is_ignition_bull and 
            has_volume and 
            anti_fomo_ok
        )
        
        is_strong_breakout_up = (
            was_below_ema and 
            (c_price > c_ema21 + (hysteresis * 0.5)) and 
            confirmed_uptrend and 
            (rel_vol > 1.2) and 
            (body_size > curr_atr * 0.3) and 
            anti_fomo_ok
        )

        is_perfect_breakout_down = (
            was_above_ema and 
            (c_price < c_ema21 - hysteresis) and 
            confirmed_downtrend and 
            (was_squeezed_recently or recent_sell_abs) and # SMC: Squeeze o Distribución Institucional
            is_ignition_bear and 
            has_volume and 
            anti_fomo_ok
        )

        is_strong_breakout_down = (
            was_above_ema and 
            (c_price < c_ema21 - (hysteresis * 0.5)) and 
            confirmed_downtrend and 
            (rel_vol > 1.2) and 
            (body_size > curr_atr * 0.3) and 
            anti_fomo_ok
        )

        # --- MODOS DE REVERSIÓN (V5.1 New Entry) ---
        is_reversion_buy = (c_price < c_lower_bb) and (curr_rsi < 30) and (p_rsi < curr_rsi) # Rebote RSI
        is_reversion_sell = (c_price > c_upper_bb) and (curr_rsi > 70) and (p_rsi > curr_rsi) # Rechazo RSI

        # --- EVALUACIÓN GLOBAL ---
        threshold = kwargs.get('score_threshold', 80)

        if is_perfect_breakout_up or is_strong_breakout_up:
            is_tier1 = is_perfect_breakout_up
            mode_label = "BREAKOUT_ALZA_V5" if is_tier1 else "BREAKOUT_ALZA_S2"
            score = 85 if is_tier1 else 81
            entry = 1
            factors_detailed.append({"k": "Estrategia", "v": f"ROTURA EMA21 {'V5.0' if is_tier1 else 'S2'} ↑", "score": score})
            if was_squeezed_recently: factors_detailed.append({"k": "Squeeze", "v": "Confirmado", "score": 5})
            factors_detailed.append({"k": "Ignición", "v": f"{(body_size/curr_atr):.1f} ATR", "score": 5})
            factors_detailed.append({"k": "Volumen", "v": f"{(rel_vol):.1f}x", "score": 5})
            if recent_buy_abs: factors_detailed.append({"k": "SMC (VSA)", "v": "Absorción Alcista Previa", "score": 10})
            
            # SMC SL Dinámico: Swing Low real (15 velas)
            swing_low = df_m1['low'].tail(15).min()
            # Mínimo de distancia ATR para no asfixiar el trade en ruido
            min_sl_dist = curr_atr * 1.0 
            target_price_sl = min(swing_low - (curr_atr * 0.2), c_price - min_sl_dist)
            
            # TP Dinámico: Banda Superior Bollinger (o mín 1.8x SL para cumplir core config)
            sl_dist = c_price - target_price_sl
            target_price_tp = max(c_upper_bb, c_price + (sl_dist * self.min_rr))
            
        elif is_perfect_breakout_down or is_strong_breakout_down:
            is_tier1 = is_perfect_breakout_down
            mode_label = "BREAKOUT_BAJA_V5" if is_tier1 else "BREAKOUT_BAJA_S2"
            score = 85 if is_tier1 else 81
            entry = -1
            factors_detailed.append({"k": "Estrategia", "v": f"ROTURA EMA21 {'V5.0' if is_tier1 else 'S2'} ↓", "score": score})
            if was_squeezed_recently: factors_detailed.append({"k": "Squeeze", "v": "Confirmado", "score": 5})
            factors_detailed.append({"k": "Ignición", "v": f"{(body_size/curr_atr):.1f} ATR", "score": 5})
            factors_detailed.append({"k": "Volumen", "v": f"{(rel_vol):.1f}x", "score": 5})
            if recent_sell_abs: factors_detailed.append({"k": "SMC (VSA)", "v": "Distribución Bajista Previa", "score": 10})
            
            # SMC SL Dinámico: Swing High real (15 velas)
            swing_high = df_m1['high'].tail(15).max()
            min_sl_dist = curr_atr * 1.0
            target_price_sl = max(swing_high + (curr_atr * 0.2), c_price + min_sl_dist)
            
            sl_dist = target_price_sl - c_price
            target_price_tp = min(c_lower_bb, c_price - (sl_dist * self.min_rr))

        elif is_reversion_buy:
            mode_label = "REVERSION_ALZA"
            score = 82
            entry = 1
            factors_detailed.append({"k": "Estrategia", "v": "REVERSION BB ↓↑", "score": 82})
            factors_detailed.append({"k": "RSI EXTREMO", "v": f"{curr_rsi:.0f}", "score": 10})
            
            target_price_sl = c_price - (curr_atr * 1.5)
            target_price_tp = mid_bb.iloc[-1] # TP a la media de Bollinger
            
            # Asegurar RR 1.8
            sl_dist = c_price - target_price_sl
            if (target_price_tp - c_price) < sl_dist * self.min_rr:
                target_price_tp = c_price + (sl_dist * self.min_rr)

        elif is_reversion_sell:
            mode_label = "REVERSION_BAJA"
            score = 82
            entry = -1
            factors_detailed.append({"k": "Estrategia", "v": "REVERSION BB ↑↓", "score": 82})
            factors_detailed.append({"k": "RSI EXTREMO", "v": f"{curr_rsi:.0f}", "score": 10})
            
            target_price_sl = c_price + (curr_atr * 1.5)
            target_price_tp = mid_bb.iloc[-1] # TP a la media de Bollinger
            
            # Asegurar RR 1.8
            sl_dist = target_price_sl - c_price
            if (c_price - target_price_tp) < sl_dist * self.min_rr:
                target_price_tp = c_price - (sl_dist * self.min_rr)


        if not entry:
            # Puntuación pasiva para mostrar el dashboard (Acechando) de forma progresiva
            passive_score = 0
            
            # --- ZONA PROGRESIVA (Max 35 pts) ---
            dist_ema_atr = dist_to_ema / curr_atr if curr_atr > 0 else 1.0
            
            # Distancia a las bandas de Bollinger (si perfora, es < 0, por lo que es extremo perfecto)
            dist_to_lower = c_price - c_lower_bb
            dist_to_upper = c_upper_bb - c_price
            min_dist_band = min(abs(dist_to_lower), abs(dist_to_upper)) / curr_atr if curr_atr > 0 else 1.0
            
            # Calcular proximidad. Si el precio está a 0 ATR, ptos máximos. A medida que se aleja a 1 ATR o más, ptos 0.
            pts_ema = max(0, (1.0 - dist_ema_atr) * 35)
            # Para las bandas, medimos la extensión general
            pts_bb = max(0, (1.0 - min_dist_band) * 35)
            
            # Se queda con la estructura que mejor esté posicionada (rotura vs reversión)
            zona_pts = round(max(pts_ema, pts_bb), 0)
            passive_score += zona_pts
            
            if pts_ema >= pts_bb:
                 zona_label = f"Alineación EMA ({dist_ema_atr:.1f} ATR)"
                 zona_desc = "Precio acercándose a la EMA21 buscando rotura direccional. Cuanto más cerca, mayor puntuación."
            else:
                 zona_label = f"Extensión BB ({min_dist_band:.1f} ATR)"
                 zona_desc = "Precio acercándose a las bandas externas buscando volatilidad extrema o rechazo. Piercing = 35 pts."
                 
            factors_detailed.append({"k": "ZONA", "v": zona_label, "score": zona_pts, "desc": zona_desc})
            
            # --- ADX PROGRESIVO (Max 12 pts) ---
            # El ADX por debajo de 20 es malo, en 40 es espléndido.
            adx_pts = round(min(curr_adx, 40) / 40 * 12, 0)
            passive_score += adx_pts
            factors_detailed.append({"k": "Fuerza (ADX)", "v": f"{curr_adx:.0f} de 40", "score": adx_pts, "desc": "Mide la inercia del mercado. Se busca > 20 para validar movimientos técnicos sostenidos."})
            
            # --- RSI PROGRESIVO (Max 12 pts) ---
            # Queremos que la sobrecompra o sobreventa (distante de 50) sume puntos.
            dist_rsi_50 = abs(curr_rsi - 50) 
            rsi_pts = round(min(dist_rsi_50 / 25, 1.0) * 12, 0)
            passive_score += rsi_pts
            factors_detailed.append({"k": "RSI", "v": f"{curr_rsi:.0f}", "score": rsi_pts, "desc": "Presión del precio oscilante. Esta puntuación sube armónicamente a medida que el RSI se acerca a excesos (<35 o >65)."})
            
            # --- VOLUMEN PROGRESIVO (Max 12 pts) ---
            # El volumen relativo sobre 1 incrementa
            vol_pts = round(min(rel_vol, 2.0) / 2.0 * 12, 0)
            passive_score += vol_pts
            factors_detailed.append({"k": "Volumen Rel.", "v": f"{rel_vol:.1f}x", "score": vol_pts, "desc": "Interés institucional. Se compara el volumen de 1 min frente a su propia media. Mayores participaciones dan mayores puntos."})
            
            # --- TENDENCIA SUPERIOR (Max 8 pts) ---
            trend_pts = 8 if (confirmed_uptrend or confirmed_downtrend) else 0
            passive_score += trend_pts
            factors_detailed.append({"k": "M3/M5", "v": "Alineados" if trend_pts else "Lucha Mixta", "score": trend_pts, "desc": "Se otorgan 8 puntos instantáneos si los marcos temporales de M3 y M5 respaldan una misma dirección global."})
            
            # Visual Info Auxiliar
            factors_detailed.append({"k": "Squeeze", "v": "ACTIVO" if is_squeeze else "No", "score": 0, "desc": "Compresión. Anuncia alta probabilidad de volatilidad inminente en ambas direcciones."})
            factors_detailed.append({"k": "Cuerpo", "v": f"{body_ratio*100:.0f}%", "score": 0, "desc": "Porcentaje de cuerpo vs mecha en la vela de 1M. Un cuerpo sólido (>50%) demuestra gran intención."})
            
            score = min(79, passive_score)

        # Ajustes finales y formato
        final_score = min(100, max(0, score))

        # Check spread safety validation (Mantenemos la protección de spread alta)
        if entry != 0:
            target_sl_dist = abs(c_price - target_price_sl)
            target_tp_dist = abs(c_price - target_price_tp)
            
            if spread_dist > (target_tp_dist * 0.25):
                final_score -= 30
                entry = 0
                factors_detailed.append({
                    "k": "Coste Spread", "v": "ALTO", "score": -30, 
                    "desc": "Spread demasiado alto que consume el R:R para Scalping."
                })
                mode_label = "SPREAD_ALTO"
            else:
                factors_detailed.append({"k": "TP Lógico", "v": "Dinámico", "score": 0})
                factors_detailed.append({"k": "SL Lógico", "v": "Dinámico", "score": 0})

        is_stalking = False
        if entry == 0 and final_score >= 65:
            is_stalking = True

        return {
            "strategy": self.STRATEGY_NAME,
            "score": final_score,
            "signal": "BUY" if entry == 1 else ("SELL" if entry == -1 else "NEUTRAL"),
            "entry": entry,
            "is_stalking": is_stalking,
            "direction": 1 if (is_perfect_breakout_up or is_strong_breakout_up or is_reversion_buy or confirmed_uptrend) else (-1 if (is_perfect_breakout_down or is_strong_breakout_down or is_reversion_sell or confirmed_downtrend) else 0),
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
        Salida dinámica: Rotura invertida clara (Cierre al otro lado de EMA21).
        """
        df_m1 = mtf_data.get('m1') if isinstance(mtf_data, dict) else mtf_data
        if df_m1 is None or len(df_m1) < 25: return False
        
        ema_exit = ta.ema(df_m1['close'], length=21)
        if ema_exit is None: return False
        
        last_close = df_m1['close'].iloc[-2]
        last_ema = ema_exit.iloc[-2]
        
        if p_type == "BUY" and last_close < last_ema:
            logger.info("🚪 [EXIT] Cierre por debajo de EMA21 (M1 Confirmado)")
            return True
        if p_type == "SELL" and last_close > last_ema:
            logger.info("🚪 [EXIT] Cierre por encima de EMA21 (M1 Confirmado)")
            return True
        return False
