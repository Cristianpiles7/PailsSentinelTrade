import pandas_ta as ta
import logging

logger = logging.getLogger("PST-AlphaTrend")


class PSTAlphaTrend:
    STRATEGY_NAME = "PST-AlphaTrend"
    STRATEGY_TYPE = "TREND"

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

        df_h1 = mtf_data.get("h1")
        df_m15 = mtf_data.get("m15")

        if df_h1 is None or len(df_h1) < 210:
            return {**neutral, "metadata": {"status": "H1 insuficiente (min 210 velas)", "factors_detailed": []}}

        close = df_h1["close"]
        high = df_h1["high"]
        low = df_h1["low"]

        ema21 = ta.ema(close, length=21)
        ema50 = ta.ema(close, length=50)
        ema200 = ta.ema(close, length=200)
        adx_df = ta.adx(high, low, close, length=14)
        atr_series = ta.atr(high, low, close, length=14)
        rsi_series = ta.rsi(close, length=14)
        st_df = ta.supertrend(high, low, close, length=10, multiplier=3.0)

        if any(x is None for x in [ema21, ema50, ema200, adx_df, atr_series, rsi_series, st_df]):
            return {**neutral, "metadata": {"status": "Error calculando indicadores H1", "factors_detailed": []}}

        e21 = float(ema21.iloc[-1])
        e50 = float(ema50.iloc[-1])
        e200 = float(ema200.iloc[-1])
        adx_val = float(adx_df["ADX_14"].iloc[-1])
        dmp = float(adx_df["DMP_14"].iloc[-1])
        dmn = float(adx_df["DMN_14"].iloc[-1])
        atr = float(atr_series.iloc[-1])
        rsi = float(rsi_series.iloc[-1])
        price = float(close.iloc[-1])

        st_dir_col = [c for c in st_df.columns if "SUPERTd" in c]
        if not st_dir_col:
            return {**neutral, "metadata": {"status": "Supertrend no disponible", "factors_detailed": []}}
        st_dir = int(st_df[st_dir_col[0]].iloc[-1])  # 1 = alcista, -1 = bajista

        score = 0
        factors = []
        direction = 0

        # 1. Alineación de EMAs — hasta 30 pts
        bull_align = e21 > e50 > e200
        bear_align = e21 < e50 < e200

        if bull_align:
            score += 30
            direction = 1
            factors.append({"k": "EMA Alignment", "v": "BULL Triple (21>50>200)", "score": 30})
        elif bear_align:
            score += 30
            direction = -1
            factors.append({"k": "EMA Alignment", "v": "BEAR Triple (21<50<200)", "score": 30})
        elif e21 > e50:
            score += 15
            direction = 1
            factors.append({"k": "EMA Alignment", "v": "BULL Soft (21>50)", "score": 15})
        elif e21 < e50:
            score += 15
            direction = -1
            factors.append({"k": "EMA Alignment", "v": "BEAR Soft (21<50)", "score": 15})
        else:
            factors.append({"k": "EMA Alignment", "v": "NEUTRAL", "score": 0})

        if direction == 0:
            return {
                "score": 0, "entry": 0, "atr": atr,
                "metadata": {"status": "EMAs sin alineación", "factors_detailed": factors},
            }

        # 2. ADX — hasta 25 pts; penalizar rango profundo
        if adx_val > 35:
            score += 25
            factors.append({"k": "ADX", "v": f"{adx_val:.1f} (Tendencia fuerte)", "score": 25})
        elif adx_val > 25:
            score += 15
            factors.append({"k": "ADX", "v": f"{adx_val:.1f} (Tendencia moderada)", "score": 15})
        elif adx_val > 20:
            score += 5
            factors.append({"k": "ADX", "v": f"{adx_val:.1f} (Tendencia débil)", "score": 5})
        else:
            score -= 20
            factors.append({"k": "ADX", "v": f"{adx_val:.1f} (Rango — bloqueo)", "score": -20})

        # 3. Supertrend — hasta 20 pts; penalización fuerte si contradice la dirección
        if st_dir == 1 and direction == 1:
            score += 20
            factors.append({"k": "Supertrend", "v": "BULL ✅", "score": 20})
        elif st_dir == -1 and direction == -1:
            score += 20
            factors.append({"k": "Supertrend", "v": "BEAR ✅", "score": 20})
        else:
            score -= 10
            factors.append({"k": "Supertrend", "v": "CONTRA dirección EMA ❌", "score": -10})

        # 4. DI+ / DI- confirma impulso — hasta 15 pts
        if direction == 1 and dmp > dmn:
            score += 15
            factors.append({"k": "DI+/DI-", "v": f"DI+ {dmp:.1f} > DI- {dmn:.1f} ✅", "score": 15})
        elif direction == -1 and dmn > dmp:
            score += 15
            factors.append({"k": "DI+/DI-", "v": f"DI- {dmn:.1f} > DI+ {dmp:.1f} ✅", "score": 15})
        else:
            factors.append({"k": "DI+/DI-", "v": "Sin confirmación DI", "score": 0})

        # 5. RSI — zona de agotamiento penaliza; zona intermedia suma pts
        if direction == 1 and rsi > 70:
            score -= 15
            factors.append({"k": "RSI H1", "v": f"{rsi:.1f} (Sobrecomprado ❌)", "score": -15})
        elif direction == -1 and rsi < 30:
            score -= 15
            factors.append({"k": "RSI H1", "v": f"{rsi:.1f} (Sobrevendido ❌)", "score": -15})
        elif 40 <= rsi <= 60:
            score += 10
            factors.append({"k": "RSI H1", "v": f"{rsi:.1f} (Zona media saludable)", "score": 10})
        else:
            factors.append({"k": "RSI H1", "v": f"{rsi:.1f}", "score": 0})

        # 6. Pullback a EMA21 en H1 — evento de entrada requerido
        # La tendencia puede llevar días activa; solo entramos cuando el precio retrocede
        # a tocar la EMA21 y rebota (distancia <= 0.5 ATR). Sin este evento, bloqueamos entrada.
        pullback_confirmed = False
        if len(df_h1) >= 3:
            prev_low = float(df_h1["low"].iloc[-2])
            prev_high = float(df_h1["high"].iloc[-2])
            touched_ema = (
                (direction == 1 and prev_low <= e21 <= float(df_h1["high"].iloc[-2]) and price > e21) or
                (direction == -1 and prev_high >= e21 >= float(df_h1["low"].iloc[-2]) and price < e21)
            )
            near_ema = abs(price - e21) <= atr * 0.5
            pullback_confirmed = touched_ema or near_ema

        if pullback_confirmed:
            score += 15
            factors.append({"k": "Pullback EMA21 H1", "v": f"Precio cerca/tocó EMA21 ({e21:.5f}) ✅", "score": 15})
        else:
            score -= 15
            factors.append({"k": "Pullback EMA21 H1", "v": f"Precio extendido de EMA21 — no es entrada ❌", "score": -15})

        # Confirmación M15: precio respecto a EMA21 — hasta 5 pts adicionales
        if df_m15 is not None and len(df_m15) >= 22:
            m15_ema21 = ta.ema(df_m15["close"], length=21)
            if m15_ema21 is not None:
                m15_price = float(df_m15["close"].iloc[-1])
                m15_e21 = float(m15_ema21.iloc[-1])
                if direction == 1 and m15_price > m15_e21:
                    score += 5
                    factors.append({"k": "M15 EMA21", "v": f"Precio {m15_price:.5f} > EMA21 {m15_e21:.5f} ✅", "score": 5})
                elif direction == -1 and m15_price < m15_e21:
                    score += 5
                    factors.append({"k": "M15 EMA21", "v": f"Precio {m15_price:.5f} < EMA21 {m15_e21:.5f} ✅", "score": 5})
                else:
                    factors.append({"k": "M15 EMA21", "v": "Sin confirmación M15", "score": 0})

        # 7. Control de spread
        if spread_dist > atr * 0.4:
            score -= 10
            factors.append({"k": "Spread", "v": f"Alto: {spread_dist:.5f} (>{atr*0.4:.5f})", "score": -10})
        else:
            factors.append({"k": "Spread", "v": f"OK: {spread_dist:.5f}", "score": 0})

        score = max(0, min(100, int(score)))
        entry = direction if score >= 70 else 0

        # TP técnico: extensión 1.5x del último swing en H1
        tp_price = 0.0
        if entry != 0 and len(df_h1) >= 20:
            if entry == 1:
                recent_high = float(df_h1["high"].tail(20).max())
                tp_price = price + (recent_high - price) * 1.5
            else:
                recent_low = float(df_h1["low"].tail(20).min())
                tp_price = price - (price - recent_low) * 1.5

        return {
            "score": score,
            "entry": entry,
            "atr": atr,
            "tp_price": tp_price,
            "metadata": {
                "status": f"AlphaTrend | Score {score} | {'BUY' if direction==1 else 'SELL' if direction==-1 else 'NONE'}",
                "factors_detailed": factors,
            },
        }
