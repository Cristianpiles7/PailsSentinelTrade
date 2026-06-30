import pandas_ta as ta
import logging

logger = logging.getLogger("PST-RangeBreaker")


class PSTRangeBreaker:
    STRATEGY_NAME = "PST-RangeBreaker"
    STRATEGY_TYPE = "RANGE"

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

        df_m15 = mtf_data.get("m15")
        df_h1 = mtf_data.get("h1")

        if df_m15 is None or len(df_m15) < 50:
            return {**neutral, "metadata": {"status": "M15 insuficiente (min 50 velas)", "factors_detailed": []}}

        close = df_m15["close"]
        high = df_m15["high"]
        low = df_m15["low"]

        bb_df = ta.bbands(close, length=20, std=2.0)
        atr_series = ta.atr(high, low, close, length=14)
        rsi_series = ta.rsi(close, length=14)
        adx_df = ta.adx(high, low, close, length=14)

        if any(x is None for x in [bb_df, atr_series, rsi_series, adx_df]):
            return {**neutral, "metadata": {"status": "Error calculando indicadores M15", "factors_detailed": []}}

        bbl_col = [c for c in bb_df.columns if "BBL" in c]
        bbu_col = [c for c in bb_df.columns if "BBU" in c]
        bbm_col = [c for c in bb_df.columns if "BBM" in c]

        if not bbl_col or not bbu_col or not bbm_col:
            return {**neutral, "metadata": {"status": "Columnas BB no encontradas", "factors_detailed": []}}

        bbl = float(bb_df[bbl_col[0]].iloc[-1])
        bbu = float(bb_df[bbu_col[0]].iloc[-1])
        bbm = float(bb_df[bbm_col[0]].iloc[-1])

        price = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        atr = float(atr_series.iloc[-1])
        rsi = float(rsi_series.iloc[-1])
        adx_val = float(adx_df["ADX_14"].iloc[-1])

        score = 0
        factors = []
        direction = 0

        # 1. ADX confirma rango — hasta 25 pts; tendencia fuerte penaliza
        if adx_val < 15:
            score += 25
            factors.append({"k": "ADX M15", "v": f"{adx_val:.1f} (Rango sólido)", "score": 25})
        elif adx_val < 20:
            score += 15
            factors.append({"k": "ADX M15", "v": f"{adx_val:.1f} (Rango)", "score": 15})
        elif adx_val < 25:
            score += 5
            factors.append({"k": "ADX M15", "v": f"{adx_val:.1f} (Límite)", "score": 5})
        else:
            score -= 20
            factors.append({"k": "ADX M15", "v": f"{adx_val:.1f} (Tendencia — bloqueo ❌)", "score": -20})

        # 2. Toque FRESCO de banda — el evento debe ser de la vela actual o anterior,
        # pero la vela de hace 2 periodos NO debe haber tocado la misma banda (evita señales repetidas).
        curr_low = float(low.iloc[-1])
        curr_high = float(high.iloc[-1])
        prev2_low = float(low.iloc[-3]) if len(low) >= 3 else curr_low
        prev2_high = float(high.iloc[-3]) if len(high) >= 3 else curr_high

        touched_lower = curr_low <= bbl and price > bbl
        touched_upper = curr_high >= bbu and price < bbu

        # Verificar que el toque es fresco (hace 2 velas el precio NO tocaba esa banda)
        stale_lower = prev2_low <= bbl  # Lleva más de 2 velas tocando la banda inferior
        stale_upper = prev2_high >= bbu  # Lleva más de 2 velas tocando la banda superior

        if touched_lower and not stale_lower:
            score += 30
            direction = 1
            factors.append({"k": "BB Lower", "v": f"Toque FRESCO banda inferior ({bbl:.5f}) ✅", "score": 30})
        elif touched_upper and not stale_upper:
            score += 30
            direction = -1
            factors.append({"k": "BB Upper", "v": f"Toque FRESCO banda superior ({bbu:.5f}) ✅", "score": 30})
        elif touched_lower or touched_upper:
            # Toque repetido (señal ya procesada en ciclos anteriores)
            factors.append({"k": "BB Touch", "v": "Toque repetido (señal ya vista, ignorando)", "score": 0})
            return {
                "score": 0, "entry": 0, "atr": atr,
                "metadata": {"status": "BB touch repetido — esperando nuevo evento", "factors_detailed": factors},
            }
        else:
            factors.append({"k": "BB Touch", "v": "Sin toque de banda exterior", "score": 0})
            return {
                "score": 0, "entry": 0, "atr": atr,
                "metadata": {"status": "Sin toque de Bollinger Band", "factors_detailed": factors},
            }

        # 3. RSI en zona extrema confirma sobreventa/sobrecompra — hasta 25 pts
        if direction == 1 and rsi < 30:
            score += 25
            factors.append({"k": "RSI M15", "v": f"{rsi:.1f} (Sobrevendido ✅)", "score": 25})
        elif direction == 1 and rsi < 40:
            score += 12
            factors.append({"k": "RSI M15", "v": f"{rsi:.1f} (Zona baja)", "score": 12})
        elif direction == -1 and rsi > 70:
            score += 25
            factors.append({"k": "RSI M15", "v": f"{rsi:.1f} (Sobrecomprado ✅)", "score": 25})
        elif direction == -1 and rsi > 60:
            score += 12
            factors.append({"k": "RSI M15", "v": f"{rsi:.1f} (Zona alta)", "score": 12})
        else:
            factors.append({"k": "RSI M15", "v": f"{rsi:.1f} (Neutro)", "score": 0})

        # 4. Vela de reversión (la vela de entrada cierra en la dirección esperada) — hasta 10 pts
        if direction == 1 and price > prev_close:
            score += 10
            factors.append({"k": "Vela", "v": "Reversión alcista ✅", "score": 10})
        elif direction == -1 and price < prev_close:
            score += 10
            factors.append({"k": "Vela", "v": "Reversión bajista ✅", "score": 10})
        else:
            factors.append({"k": "Vela", "v": "Sin vela de confirmación", "score": 0})

        # 5. H1 sin tendencia fuerte — hasta 10 pts
        if df_h1 is not None and len(df_h1) >= 22:
            h1_adx_df = ta.adx(df_h1["high"], df_h1["low"], df_h1["close"], length=14)
            if h1_adx_df is not None:
                h1_adx_val = float(h1_adx_df["ADX_14"].iloc[-1])
                if h1_adx_val < 20:
                    score += 10
                    factors.append({"k": "H1 ADX", "v": f"{h1_adx_val:.1f} (Rango H1 ✅)", "score": 10})
                elif h1_adx_val > 30:
                    score -= 15
                    factors.append({"k": "H1 ADX", "v": f"{h1_adx_val:.1f} (Tendencia H1 ❌)", "score": -15})
                else:
                    factors.append({"k": "H1 ADX", "v": f"{h1_adx_val:.1f}", "score": 0})

        # 6. Control de spread (más permisivo que scalping: 35% del ATR)
        if spread_dist > atr * 0.35:
            score -= 10
            factors.append({"k": "Spread", "v": f"Alto: {spread_dist:.5f}", "score": -10})
        else:
            factors.append({"k": "Spread", "v": f"OK: {spread_dist:.5f}", "score": 0})

        score = max(0, min(100, int(score)))
        entry = direction if score >= 70 else 0

        # TP técnico: media de Bollinger (retorno a la media del rango)
        tp_price = bbm if entry != 0 else 0.0

        return {
            "score": score,
            "entry": entry,
            "atr": atr,
            "tp_price": tp_price,
            "metadata": {
                "status": f"RangeBreaker | Score {score} | {'BUY' if direction==1 else 'SELL' if direction==-1 else 'NONE'} | BB Media: {bbm:.5f}",
                "factors_detailed": factors,
            },
        }
