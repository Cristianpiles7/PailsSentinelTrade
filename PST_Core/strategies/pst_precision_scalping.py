import pandas_ta as ta
import logging

logger = logging.getLogger("PST-PrecisionScalping")


class PSTPrecisionScalping:
    STRATEGY_NAME = "PST-PrecisionScalping"
    STRATEGY_TYPE = "SCALPING"

    async def calculate_signal(
        self,
        mtf_data,
        current_regime=None,
        user_levels=None,
        spread_points=0,
        spread_dist=0,
        **kwargs,
    ) -> dict:
        neutral = {
            "score": 0,
            "entry": 0,
            "atr": 0,
            "metadata": {"status": "Sin datos suficientes", "factors_detailed": []},
        }

        df_m1 = mtf_data.get("m1")
        df_m5 = mtf_data.get("m5")

        if df_m1 is None or len(df_m1) < 30:
            return {**neutral, "metadata": {"status": "M1 insuficiente (min 30 velas)", "factors_detailed": []}}
        if df_m5 is None or len(df_m5) < 22:
            return {**neutral, "metadata": {"status": "M5 insuficiente (min 22 velas)", "factors_detailed": []}}

        close_m1 = df_m1["close"]
        high_m1 = df_m1["high"]
        low_m1 = df_m1["low"]

        # Indicadores M1
        ema9 = ta.ema(close_m1, length=9)
        ema21 = ta.ema(close_m1, length=21)
        atr_m1 = ta.atr(high_m1, low_m1, close_m1, length=14)

        if any(x is None for x in [ema9, ema21, atr_m1]):
            return {**neutral, "metadata": {"status": "Error indicadores M1", "factors_detailed": []}}

        # VWAP de sesión con reset diario y bandas de desviación estándar
        vol_col = "tick_volume" if "tick_volume" in df_m1.columns else ("volume" if "volume" in df_m1.columns else None)
        typical = (high_m1 + low_m1 + close_m1) / 3

        vwap_val = float(typical.iloc[-1])
        vwap_upper1 = vwap_val
        vwap_lower1 = vwap_val
        vwap_upper2 = vwap_val
        vwap_lower2 = vwap_val

        try:
            # Reset diario: solo velas del día actual
            session_df = df_m1
            if "time" in df_m1.columns:
                today = df_m1["time"].iloc[-1].date()
                session_mask = df_m1["time"].apply(lambda t: t.date() == today)
                if session_mask.sum() >= 5:
                    session_df = df_m1[session_mask]

            s_typical = (session_df["high"] + session_df["low"] + session_df["close"]) / 3

            if vol_col and session_df[vol_col].sum() > 0:
                s_vol = session_df[vol_col]
                cum_vol = s_vol.cumsum()
                cum_tp_vol = (s_typical * s_vol).cumsum()
                vwap_series = cum_tp_vol / cum_vol
                vwap_val = float(vwap_series.iloc[-1])

                # Bandas: desviación estándar del precio típico respecto al VWAP
                variance = ((s_typical - vwap_series) ** 2 * s_vol).cumsum() / cum_vol
                std_dev = float(variance.iloc[-1] ** 0.5)
                vwap_upper1 = vwap_val + std_dev
                vwap_lower1 = vwap_val - std_dev
                vwap_upper2 = vwap_val + 2 * std_dev
                vwap_lower2 = vwap_val - 2 * std_dev
            else:
                # Proxy: EMA 20 del precio típico cuando no hay volumen
                vwap_proxy = ta.ema(s_typical, length=20)
                if vwap_proxy is not None:
                    vwap_val = float(vwap_proxy.iloc[-1])
                    atr_proxy = float(atr_m1.iloc[-1])
                    vwap_upper1 = vwap_val + atr_proxy
                    vwap_lower1 = vwap_val - atr_proxy
                    vwap_upper2 = vwap_val + 2 * atr_proxy
                    vwap_lower2 = vwap_val - 2 * atr_proxy
        except Exception:
            pass

        # Indicadores M5
        rsi_m5 = ta.rsi(df_m5["close"], length=14)
        atr_m5 = ta.atr(df_m5["high"], df_m5["low"], df_m5["close"], length=14)

        e9 = float(ema9.iloc[-1])
        e9_prev = float(ema9.iloc[-2])
        e21_val = float(ema21.iloc[-1])
        e21_prev = float(ema21.iloc[-2])
        atr = float(atr_m1.iloc[-1])
        price = float(close_m1.iloc[-1])

        rsi5 = float(rsi_m5.iloc[-1]) if rsi_m5 is not None else 50.0
        atr5 = float(atr_m5.iloc[-1]) if atr_m5 is not None else atr

        score = 0
        factors = []
        direction = 0

        # 1. Cruce EMA 9/21 en M1 — REQUERIDO para disparar entrada
        # "Sin cruce" solo aporta contexto al HUD pero nunca supera el umbral de entrada (cap a 60)
        cross_bull = e9_prev <= e21_prev and e9 > e21_val
        cross_bear = e9_prev >= e21_prev and e9 < e21_val
        has_fresh_cross = cross_bull or cross_bear

        if cross_bull:
            score += 35
            direction = 1
            factors.append({"k": "EMA 9/21 M1", "v": "Cruce ALCISTA (señal fresca) ✅", "score": 35})
        elif cross_bear:
            score += 35
            direction = -1
            factors.append({"k": "EMA 9/21 M1", "v": "Cruce BAJISTA (señal fresca) ✅", "score": 35})
        elif e9 > e21_val:
            score += 10
            direction = 1
            factors.append({"k": "EMA 9/21 M1", "v": "Posición alcista (sin cruce, solo contexto)", "score": 10})
        elif e9 < e21_val:
            score += 10
            direction = -1
            factors.append({"k": "EMA 9/21 M1", "v": "Posición bajista (sin cruce, solo contexto)", "score": 10})
        else:
            factors.append({"k": "EMA 9/21 M1", "v": "NEUTRAL", "score": 0})

        if direction == 0:
            return {
                "score": 0, "entry": 0, "atr": atr,
                "metadata": {"status": "EMAs M1 sin dirección", "factors_detailed": factors},
            }

        # 2. VWAP de sesión + bandas — hasta 30 pts; contra penaliza
        near_upper2 = price >= vwap_upper2 * 0.998
        near_lower2 = price <= vwap_lower2 * 1.002
        near_upper1 = price >= vwap_upper1 * 0.999
        near_lower1 = price <= vwap_lower1 * 1.001

        if direction == 1:
            if price > vwap_val:
                if near_upper2:
                    # Precio en banda +2σ: sobrecomprado, peor entrada para BUY
                    score += 5
                    factors.append({"k": "VWAP", "v": f"Precio en +2σ ({vwap_upper2:.5f}) — extendido ⚠️", "score": 5})
                elif near_upper1:
                    score += 20
                    factors.append({"k": "VWAP", "v": f"Precio sobre VWAP+1σ ({vwap_upper1:.5f}) ✅", "score": 20})
                else:
                    score += 30
                    factors.append({"k": "VWAP", "v": f"Precio > VWAP ({vwap_val:.5f}) — momentum ✅", "score": 30})
            else:
                score -= 10
                factors.append({"k": "VWAP", "v": f"BUY bajo VWAP ({vwap_val:.5f}) ❌", "score": -10})
        elif direction == -1:
            if price < vwap_val:
                if near_lower2:
                    score += 5
                    factors.append({"k": "VWAP", "v": f"Precio en -2σ ({vwap_lower2:.5f}) — extendido ⚠️", "score": 5})
                elif near_lower1:
                    score += 20
                    factors.append({"k": "VWAP", "v": f"Precio bajo VWAP-1σ ({vwap_lower1:.5f}) ✅", "score": 20})
                else:
                    score += 30
                    factors.append({"k": "VWAP", "v": f"Precio < VWAP ({vwap_val:.5f}) — momentum ✅", "score": 30})
            else:
                score -= 10
                factors.append({"k": "VWAP", "v": f"SELL sobre VWAP ({vwap_val:.5f}) ❌", "score": -10})

        # 3. Momentum M5 (RSI) — hasta 20 pts
        if direction == 1 and rsi5 > 55:
            score += 20
            factors.append({"k": "RSI M5", "v": f"{rsi5:.1f} (Momentum alcista ✅)", "score": 20})
        elif direction == -1 and rsi5 < 45:
            score += 20
            factors.append({"k": "RSI M5", "v": f"{rsi5:.1f} (Momentum bajista ✅)", "score": 20})
        elif direction == 1 and rsi5 > 50:
            score += 10
            factors.append({"k": "RSI M5", "v": f"{rsi5:.1f} (OK)", "score": 10})
        elif direction == -1 and rsi5 < 50:
            score += 10
            factors.append({"k": "RSI M5", "v": f"{rsi5:.1f} (OK)", "score": 10})
        else:
            score -= 10
            factors.append({"k": "RSI M5", "v": f"{rsi5:.1f} (Contra dirección ❌)", "score": -10})

        # 4. Volumen relativo elevado — hasta 10 pts
        if vol_col and len(df_m1) >= 21:
            vol_s = df_m1[vol_col]
            vol_ma = float(vol_s.rolling(20).mean().iloc[-1])
            vol_cur = float(vol_s.iloc[-1])
            if vol_ma > 0 and vol_cur > vol_ma * 1.2:
                score += 10
                factors.append({"k": "Volumen", "v": f"{vol_cur:.0f} > MA20 {vol_ma:.0f} ✅", "score": 10})
            else:
                factors.append({"k": "Volumen", "v": f"{vol_cur:.0f} (Normal)", "score": 0})
        else:
            factors.append({"k": "Volumen", "v": "Sin datos de volumen", "score": 0})

        # 5. Volatilidad M1 activa (no mercado dormido) — hasta 10 pts; dormido penaliza
        if atr5 > 0:
            atr_ratio = atr / atr5
            if atr_ratio >= 0.3:
                score += 10
                factors.append({"k": "Volatilidad", "v": f"ATR M1/M5 ratio {atr_ratio:.2f} ✅", "score": 10})
            elif atr_ratio < 0.2:
                score -= 15
                factors.append({"k": "Volatilidad", "v": f"Mercado dormido (ratio {atr_ratio:.2f}) ❌", "score": -15})
            else:
                factors.append({"k": "Volatilidad", "v": f"ATR ratio {atr_ratio:.2f}", "score": 0})

        # 6. Spread estricto — crítico para scalping (1.5x ATR M1 es el límite)
        if spread_dist > atr * 1.5:
            score -= 20
            factors.append({"k": "Spread", "v": f"CRÍTICO: {spread_dist:.5f} (>{atr*1.5:.5f}) ❌", "score": -20})
        elif spread_dist > atr * 0.8:
            score -= 10
            factors.append({"k": "Spread", "v": f"Alto: {spread_dist:.5f}", "score": -10})
        else:
            factors.append({"k": "Spread", "v": f"OK: {spread_dist:.5f}", "score": 0})

        score = max(0, min(100, int(score)))
        # Sin cruce fresco, no puede superar el umbral de entrada aunque todo lo demás esté bien
        if not has_fresh_cross:
            score = min(score, 65)
        entry = direction if score >= 70 else 0

        # TP técnico: máximo entre 1.5x el rango de la vela actual y 1.0x ATR M1
        tp_price = 0.0
        if entry != 0:
            candle_range = float(high_m1.iloc[-1]) - float(low_m1.iloc[-1])
            tp_dist = max(candle_range * 1.5, atr * 1.0)
            tp_price = price + tp_dist if entry == 1 else price - tp_dist

        return {
            "score": score,
            "entry": entry,
            "atr": atr,
            "tp_price": tp_price,
            "direction": direction,
            "metadata": {
                "status": (
                    f"PrecisionScalping | Score {score} | "
                    f"{'BUY' if direction==1 else 'SELL' if direction==-1 else 'NONE'} | "
                    f"VWAP {vwap_val:.5f} [+1σ {vwap_upper1:.5f} / -1σ {vwap_lower1:.5f}]"
                ),
                "factors_detailed": factors,
                "vwap": vwap_val,
                "vwap_upper1": vwap_upper1,
                "vwap_lower1": vwap_lower1,
                "vwap_upper2": vwap_upper2,
                "vwap_lower2": vwap_lower2,
            },
        }

    def check_exit_signal(self, mtf_data: dict, position_type: str) -> bool:
        """Salida dinámica: precio cruza VWAP de sesión en contra."""
        try:
            df_m1 = mtf_data.get("m1")
            if df_m1 is None or len(df_m1) < 21:
                return False

            high_m1  = df_m1["high"]
            low_m1   = df_m1["low"]
            close_m1 = df_m1["close"]
            typical  = (high_m1 + low_m1 + close_m1) / 3

            # Reset diario para VWAP de sesión
            session_df = df_m1
            if "time" in df_m1.columns:
                today = df_m1["time"].iloc[-1].date()
                mask = df_m1["time"].apply(lambda t: t.date() == today)
                if mask.sum() >= 5:
                    session_df = df_m1[mask]

            s_typical = (session_df["high"] + session_df["low"] + session_df["close"]) / 3
            vol_col = "tick_volume" if "tick_volume" in df_m1.columns else ("volume" if "volume" in df_m1.columns else None)

            vwap_val = float(s_typical.iloc[-1])
            if vol_col and session_df[vol_col].sum() > 0:
                s_vol = session_df[vol_col]
                vwap_val = float((s_typical * s_vol).cumsum().iloc[-1] / s_vol.cumsum().iloc[-1])
            else:
                proxy = ta.ema(s_typical, length=20)
                if proxy is not None:
                    vwap_val = float(proxy.iloc[-1])

            price = float(close_m1.iloc[-1])

            if position_type == "BUY" and price < vwap_val:
                return True
            if position_type == "SELL" and price > vwap_val:
                return True

            return False
        except Exception:
            return False
