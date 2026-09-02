import pandas as pd
import pandas_ta as ta
import logging

from ..ml.tf_selector import AdaptiveTFSelector
from ..ml.feature_builder import FeatureBuilder
from ..ml.blend import MLBlendMixin
from ..ml.duration_cap import cap_tp_by_duration
from ..utils.tech_utils import calculate_channel_boundary

logger = logging.getLogger("PST-MomentumPulse")


class PSTMomentumPulse(MLBlendMixin):
    """
    Dirección/momentum: entra a favor del impulso en el timeframe que elige
    AdaptiveTFSelector según la volatilidad relativa del momento — no persigue el
    precio, busca el pullback dentro de una alineación de EMAs ya establecida.

    Usa FeatureBuilder (mismo motor que usará el modelo entrenable en fases
    posteriores) para las distancias EMA/ATR, alineación de EMAs, RSI y calidad de
    tendencia, así el score de reglas y las features del modelo parten exactamente
    de los mismos números — nunca se recalculan por separado.

    Gate obligatorio: alineación de EMA 9/21/50 (bias claro). Sobre ese gate:
      · Pullback ................. precio cerca de la EMA rápida, no extendido
      · Momentum RSI ............. a favor pero sin estar ya agotado (>75/<25)
      · Calidad de tendencia ..... ADX/Choppiness del TF activo
      · Volumen .................. confirmación de participación
      · Anti-spike ............... penaliza velas parabólicas / impulsos agotados
      · Spread ................... guard

    El filtro macro H1/H4 ya se aplica post-hoc a toda estrategia en el
    orquestador — no se duplica aquí.

    Estructura (para SL/TP): swing fractal del TF activo (SL) y borde de canal H1
    opuesto o múltiplo ATR amplio (TP — pensado para dejar correr ganadoras).
    """

    STRATEGY_NAME = "PST-MomentumPulse"
    STRATEGY_TYPE = "TREND"

    ANCHOR_TF = "m5"
    # Punto de partida, no calibrado del todo: 65 se fijó en la Fase 2 (solo-reglas).
    # Al integrar el modelo (Fase 5, peso ML_WEIGHT=0.7 en el blend), el score pasó a
    # dominarlo una probabilidad calibrada — y el modelo estima que los setups que ya
    # pasan el filtro de reglas solo ganan ~15-30% de las veces frente al SL/TP con el
    # que se etiquetó, así que el score combinado rara vez llegaba a 65 (estrategia
    # muda). Un sweep sobre los 4 símbolos piloto (20d) mostró actividad razonable y
    # expectancy mayormente positiva alrededor de 35-45; se deja en 40. Muestra aún
    # pequeña (4-8 trades/símbolo) — recalibrar de verdad en la Fase 7 con más datos.
    ENTRY_THRESHOLD = 40
    MIN_BARS = max(FeatureBuilder.MIN_BARS, 60)

    def __init__(self):
        self._tf_selector = AdaptiveTFSelector(anchor_tf=self.ANCHOR_TF)
        self._init_ml()

    @staticmethod
    def _last(series, default=0.0):
        try:
            if series is None:
                return default
            val = float(series.iloc[-1]) if hasattr(series, "iloc") else float(series)
            if val != val:  # NaN
                return default
            return val
        except Exception:
            return default

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

        symbol = kwargs.get("symbol", "") or ""
        active_tf, tf_diag = self._tf_selector.select(mtf_data)
        df = mtf_data.get(active_tf) if mtf_data else None
        if df is None or len(df) < self.MIN_BARS:
            return {**neutral, "metadata": {"status": f"{active_tf.upper()} insuficiente (min {self.MIN_BARS} velas)", "factors_detailed": []}}

        feats = FeatureBuilder.build(mtf_data, active_tf=active_tf, symbol=symbol, htf_tf="h1")

        atr_series = ta.atr(df["high"], df["low"], df["close"], length=FeatureBuilder.ATR_LEN)
        atr_val = self._last(atr_series, 0.0)
        price = self._last(df["close"], 0.0)
        if atr_val <= 0 or price <= 0:
            return {**neutral, "metadata": {"status": "ATR/precio inválido", "factors_detailed": []}}

        score = 0
        factors = []

        # 1. GATE: alineación de EMA 9/21/50 — define el bias direccional.
        alignment = feats["ema_alignment"]
        if alignment == 1.0:
            direction = 1
            score += 30
            factors.append({"k": "Alineación EMA", "v": "9>21>50 (alcista) ✅", "score": 30})
        elif alignment == -1.0:
            direction = -1
            score += 30
            factors.append({"k": "Alineación EMA", "v": "9<21<50 (bajista) ✅", "score": 30})
        else:
            return {
                "score": 0, "entry": 0, "atr": atr_val,
                "metadata": {"status": f"Sin alineación de EMAs en {active_tf}", "factors_detailed":
                             [{"k": "Alineación EMA", "v": "Mixta/plana", "score": 0}], "active_tf": active_tf},
            }

        # 2. Pullback, no persecución: distancia a EMA9 en unidades de ATR.
        ema9_dist = feats["ema9_dist_atr"] * direction  # >0 = a favor del bias
        if 0 <= ema9_dist <= 1.0:
            score += 20
            factors.append({"k": "Pullback", "v": f"{ema9_dist:.2f} ATR de EMA9 — entrada sana ✅", "score": 20})
        elif ema9_dist > 2.0:
            score -= 20
            factors.append({"k": "Pullback", "v": f"{ema9_dist:.2f} ATR de EMA9 — sobre-extendido, persiguiendo ❌", "score": -20})
        else:
            factors.append({"k": "Pullback", "v": f"{ema9_dist:.2f} ATR de EMA9", "score": 0})

        # 3. Momentum RSI — a favor pero sin estar ya agotado.
        rsi_now = feats["rsi"]
        rsi_slope = feats["rsi_slope"] * direction
        if direction == 1:
            if 50 < rsi_now <= 75:
                pts = 15 if rsi_slope > 0 else 8
                score += pts
                factors.append({"k": "RSI Momentum", "v": f"{rsi_now:.1f} a favor ✅", "score": pts})
            elif rsi_now > 75:
                score -= 15
                factors.append({"k": "RSI Momentum", "v": f"{rsi_now:.1f} sobrecomprado — agotado ❌", "score": -15})
            else:
                score -= 10
                factors.append({"k": "RSI Momentum", "v": f"{rsi_now:.1f} en contra ❌", "score": -10})
        else:
            if 25 <= rsi_now < 50:
                pts = 15 if rsi_slope > 0 else 8
                score += pts
                factors.append({"k": "RSI Momentum", "v": f"{rsi_now:.1f} a favor ✅", "score": pts})
            elif rsi_now < 25:
                score -= 15
                factors.append({"k": "RSI Momentum", "v": f"{rsi_now:.1f} sobrevendido — agotado ❌", "score": -15})
            else:
                score -= 10
                factors.append({"k": "RSI Momentum", "v": f"{rsi_now:.1f} en contra ❌", "score": -10})

        # 4. Calidad de tendencia (ADX/Choppiness) del TF activo.
        adx_val, chop_val = feats["adx"], feats["chop"]
        if adx_val >= 25 and chop_val <= 45:
            score += 15
            factors.append({"k": "Trend Quality", "v": f"ADX {adx_val:.0f}/Chop {chop_val:.0f} — tendencia limpia ✅", "score": 15})
        elif adx_val < 15 or chop_val > 65:
            score -= 15
            factors.append({"k": "Trend Quality", "v": f"ADX {adx_val:.0f}/Chop {chop_val:.0f} — lateral ❌", "score": -15})
        else:
            factors.append({"k": "Trend Quality", "v": f"ADX {adx_val:.0f}/Chop {chop_val:.0f} — aceptable", "score": 0})

        # 5. Volumen — confirmación de participación.
        vol_rel = feats["vol_rel"]
        if vol_rel > 1.1:
            score += 10
            factors.append({"k": "Volumen", "v": f"{vol_rel:.2f}x media — confirmado ✅", "score": 10})
        else:
            factors.append({"k": "Volumen", "v": f"{vol_rel:.2f}x media", "score": 0})

        # 6. Anti-spike: vela parabólica o impulso de 5 velas agotado.
        body_atr = feats["candle_body_atr"]
        if body_atr > 3.0:
            score -= 35
            factors.append({"k": "Anti-Spike", "v": f"Cuerpo {body_atr:.2f} ATR — vela parabólica ❌", "score": -35})

        # 7. Spread guard.
        if spread_dist > atr_val * 0.5:
            score -= 15
            factors.append({"k": "Spread", "v": f"Alto: {spread_dist:.5f}", "score": -15})
        else:
            factors.append({"k": "Spread", "v": f"OK: {spread_dist:.5f}", "score": 0})

        score = max(0, min(100, int(score)))

        # 8. Blend con el modelo entrenado (si existe uno para este grupo de activo;
        # si no, cae a solo-reglas sin cambiar nada de lo anterior).
        score, ml_factor = self._blend_with_model(score, mtf_data, active_tf, symbol)
        if ml_factor:
            factors.append(ml_factor)

        entry = direction if score >= self.ENTRY_THRESHOLD else 0

        # --- SL/TP estructural ---
        target_price_sl = 0.0
        tp_price = 0.0
        if entry != 0:
            swing_low_price = price - feats["dist_swing_low_atr"] * atr_val
            swing_high_price = price + feats["dist_swing_high_atr"] * atr_val
            buf = atr_val * 0.20

            htf_upper = htf_lower = None
            try:
                _, _, h1_params = calculate_channel_boundary(mtf_data.get("h1"), window=10, projection=0, recent_pivots=15)
                if h1_params:
                    last_idx = h1_params["last_idx"]
                    htf_upper = h1_params["slope_h"] * last_idx + h1_params["intercept_h"]
                    htf_lower = h1_params["slope_l"] * last_idx + h1_params["intercept_l"]
            except Exception:
                pass

            if direction == 1:
                cand_sl = swing_low_price - buf
                sl_dist = price - cand_sl
                if atr_val * 0.8 <= sl_dist <= atr_val * 3.5:
                    target_price_sl = cand_sl
                min_tp = price + max(atr_val * 3.5, (price - (target_price_sl or cand_sl)) * 1.5)
                tp_price = max(htf_upper, min_tp) if (htf_upper and htf_upper > price) else min_tp
            else:
                cand_sl = swing_high_price + buf
                sl_dist = cand_sl - price
                if atr_val * 0.8 <= sl_dist <= atr_val * 3.5:
                    target_price_sl = cand_sl
                min_tp = price - max(atr_val * 3.5, ((target_price_sl or cand_sl) - price) * 1.5)
                tp_price = min(htf_lower, min_tp) if (htf_lower and htf_lower < price) else min_tp

            # Tope de duración esperada (~2h por defecto, no un cierre por reloj —
            # ver PST_Core/ml/duration_cap.py): en activos volátiles el TP amplio
            # (pensado para dejar correr ganadoras) puede tardar horas/días en
            # activos como BTCUSD; esto lo acerca sin bajar del R:R mínimo.
            final_sl_dist = abs(price - target_price_sl) if target_price_sl > 0 else sl_dist
            tp_price = cap_tp_by_duration(price, direction, final_sl_dist, tp_price, atr_val, active_tf,
                                           min_rr_floor=1.8)

        metadata = {
            "status": (
                f"MomentumPulse | Score {score} | {'BUY' if direction == 1 else 'SELL'} | "
                f"TF {active_tf.upper()} | RSI {rsi_now:.1f} | ADX {adx_val:.0f}"
            ),
            "factors_detailed": factors,
            "active_tf": active_tf,
            "atr_pct_rank": tf_diag.get("atr_pct_rank"),
            "threshold_used": self.ENTRY_THRESHOLD,
            "bar_time": str(df.index[-1]) if isinstance(df.index, pd.DatetimeIndex) else str(df["time"].iloc[-1]) if "time" in df.columns else "",
        }
        if target_price_sl > 0:
            metadata["target_price_sl"] = target_price_sl

        return {
            "score": score,
            "entry": entry,
            "atr": atr_val,
            "tp_price": tp_price,
            "direction": direction,
            "metadata": metadata,
        }
