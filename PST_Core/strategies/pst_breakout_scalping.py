import pandas as pd
import pandas_ta as ta
import logging

from ..ml.tf_selector import AdaptiveTFSelector
from ..ml.feature_builder import FeatureBuilder
from ..ml.blend import MLBlendMixin
from ..ml.duration_cap import cap_tp_by_duration

logger = logging.getLogger("PST-BreakoutScalping")


class PSTBreakoutScalping(MLBlendMixin):
    """
    Scalping de momentum: entra en la ruptura de rango corto (swing de 20 velas del
    TF activo, SWING_LOOKBACK de FeatureBuilder) confirmada por cuerpo de vela,
    volumen y alineación de EMAs rápidas — sucesora de PST-PrecisionScalping
    (pausada) pero sobre el framework ML nuevo (FeatureBuilder/AdaptiveTFSelector/
    blend), NO micro-reversión (esa ya se probó y se descartó dos veces con
    evidencia OOS, ver memoria micro_scalping_strategy).

    Corriente C del plan de expansión de estrategias (2026-08-12) — pendiente de:
    smoke test, motor de backtest fiel (comparte el que construye la Corriente A
    para MomentumPulse/DivergenceReversion — misma gestión genérica del executor),
    baseline IS/OOS, y solo entonces activación acotada. NO está en
    ENABLED_STRATEGIES (config.py) — diseño y registro únicamente.

    Nombre deliberado: el executor identifica "posiciones scalper" por substring
    "Scalping"/"Scalper" en el comentario de la orden (PST_Core/engine/executor.py,
    variable `is_scalper_pos`), NO por STRATEGY_TYPE — así hereda breakeven a
    1.5x ATR (en vez de 2.5x genérico), el % de cierre parcial configurado para
    scalpers y el padding de comisión, igual que PrecisionScalping. Sin "Scalping"
    en el nombre, el executor la trataría como una estrategia genérica pese a ser
    de alta frecuencia y SL ceñido.

    Gate obligatorio: ruptura de rango — el cierre de la vela activa queda a
    <=0.15 ATR del swing high/low de 20 velas, con cuerpo >=0.5 ATR y color de
    vela a favor de la ruptura. Sobre ese gate:
      · EMAs rápidas ......... 9/21 (no 50: scalping no espera a que la tendencia
                                 esté plenamente establecida) a favor de la ruptura
      · Momentum RSI ......... a favor, sin agotamiento extremo
      · Volumen .............. confirmación de participación (rupturas sin volumen
                                 suelen ser falsas)
      · Choppiness ........... solo descarta lateralidad extrema — no exige ADX
                                 alto porque es el INICIO del impulso, aún no
                                 establecido
      · Anti-clímax .......... penaliza cuerpo >3.5 ATR (vela de agotamiento, no
                                 ruptura limpia)
      · Spread ............... guard más estricto que MomentumPulse (SL más ceñido
                                 aquí, el spread pesa proporcionalmente más)

    SL/TP: SL justo detrás del swing que originó la ruptura (banda ceñida 0.5-2.0
    ATR — scalping quiere riesgo pequeño, a diferencia de MomentumPulse 0.8-3.5).
    TP por múltiplo ATR moderado con tope de duración CORTO (cap_tp_by_duration a
    45 min, no los 120 por defecto): scalping busca resolución rápida, no dejar
    correr como MomentumPulse.
    """

    STRATEGY_NAME = "PST-BreakoutScalping"
    STRATEGY_TYPE = "SCALPING"

    # Candidatos rápidos únicamente — scalping no debe derivar a TFs lentos aunque
    # la volatilidad caiga; en ese caso, mejor no operar (menos setups) que operar
    # en M30 con lógica pensada para M2-M5.
    TF_CANDIDATES = ("m2", "m3", "m5")
    # Percentil de volatilidad medido en M5, igual que MomentumPulse/DivergenceReversion
    # (mismo ancla entre las tres estrategias nuevas -> "régimen" comparable entre sí).
    ANCHOR_TF = "m5"
    # Punto de partida, sin calibrar (mismo patrón que MomentumPulse/DivergenceReversion,
    # ver comentarios ahí): el gate de ruptura (30) + EMA (15) + RSI (15) + volumen (10)
    # puede sumar hasta 70 en el mejor caso; 45 deja pasar solo rupturas con al menos
    # 2-3 factores de calidad a favor, sin ser tan exigente como para no generar
    # frecuencia. Recalibrar con sweep real (Corriente A/B) antes de producción.
    ENTRY_THRESHOLD = 45
    MIN_BARS = max(FeatureBuilder.MIN_BARS, 60)

    def __init__(self):
        self._tf_selector = AdaptiveTFSelector(candidates=list(self.TF_CANDIDATES), anchor_tf=self.ANCHOR_TF)
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
        open_now = self._last(df["open"], price)
        if atr_val <= 0 or price <= 0:
            return {**neutral, "metadata": {"status": "ATR/precio inválido", "factors_detailed": []}}

        is_green = price > open_now
        is_red = price < open_now
        body_atr = feats["candle_body_atr"]
        dist_high = feats["dist_swing_high_atr"]
        dist_low = feats["dist_swing_low_atr"]

        score = 0
        factors = []

        # 1. GATE: ruptura del rango de 20 velas — el cierre queda pegado al extremo
        # (<=0.15 ATR) con cuerpo suficiente y color a favor de la dirección.
        if dist_high <= 0.15 and body_atr >= 0.5 and is_green:
            direction = 1
            score += 30
            factors.append({"k": "Ruptura", "v": f"Alcista, {dist_high:.2f} ATR del máximo de 20 velas, cuerpo {body_atr:.2f} ATR ✅", "score": 30})
        elif dist_low <= 0.15 and body_atr >= 0.5 and is_red:
            direction = -1
            score += 30
            factors.append({"k": "Ruptura", "v": f"Bajista, {dist_low:.2f} ATR del mínimo de 20 velas, cuerpo {body_atr:.2f} ATR ✅", "score": 30})
        else:
            return {
                "score": 0, "entry": 0, "atr": atr_val,
                "metadata": {"status": f"Sin ruptura limpia en {active_tf}", "factors_detailed":
                             [{"k": "Ruptura", "v": "Sin gate", "score": 0}], "active_tf": active_tf},
            }

        # 2. EMAs rápidas (9/21) a favor de la ruptura.
        ema9_dist = feats["ema9_dist_atr"] * direction
        ema21_dist = feats["ema21_dist_atr"] * direction
        if ema9_dist > 0 and ema21_dist > 0:
            score += 15
            factors.append({"k": "EMAs rápidas", "v": "9 y 21 a favor ✅", "score": 15})
        elif ema9_dist < 0 and ema21_dist < 0:
            score -= 15
            factors.append({"k": "EMAs rápidas", "v": "9 y 21 en contra ❌", "score": -15})
        else:
            factors.append({"k": "EMAs rápidas", "v": "Mixtas", "score": 0})

        # 3. Momentum RSI — a favor de la dirección, sin agotamiento extremo.
        rsi_now = feats["rsi"]
        if direction == 1:
            if 50 < rsi_now <= 80:
                score += 15
                factors.append({"k": "RSI Momentum", "v": f"{rsi_now:.1f} a favor ✅", "score": 15})
            elif rsi_now > 80:
                score -= 10
                factors.append({"k": "RSI Momentum", "v": f"{rsi_now:.1f} muy sobrecomprado ❌", "score": -10})
            else:
                score -= 10
                factors.append({"k": "RSI Momentum", "v": f"{rsi_now:.1f} en contra ❌", "score": -10})
        else:
            if 20 <= rsi_now < 50:
                score += 15
                factors.append({"k": "RSI Momentum", "v": f"{rsi_now:.1f} a favor ✅", "score": 15})
            elif rsi_now < 20:
                score -= 10
                factors.append({"k": "RSI Momentum", "v": f"{rsi_now:.1f} muy sobrevendido ❌", "score": -10})
            else:
                score -= 10
                factors.append({"k": "RSI Momentum", "v": f"{rsi_now:.1f} en contra ❌", "score": -10})

        # 4. Volumen — las rupturas sin volumen suelen ser falsas.
        vol_rel = feats["vol_rel"]
        if vol_rel > 1.2:
            score += 10
            factors.append({"k": "Volumen", "v": f"{vol_rel:.2f}x media — confirmado ✅", "score": 10})
        else:
            factors.append({"k": "Volumen", "v": f"{vol_rel:.2f}x media", "score": 0})

        # 5. Anti-chop suave: no exige ADX alto (es el INICIO del impulso, aún no
        # establecido), solo descarta lateralidad extrema.
        chop_val = feats["chop"]
        if chop_val > 65:
            score -= 15
            factors.append({"k": "Choppiness", "v": f"{chop_val:.0f} — mercado lateral ❌", "score": -15})
        else:
            factors.append({"k": "Choppiness", "v": f"{chop_val:.0f} OK", "score": 0})

        # 6. Anti-clímax: cuerpo excesivo sugiere vela de agotamiento, no ruptura limpia.
        if body_atr > 3.5:
            score -= 25
            factors.append({"k": "Anti-Clímax", "v": f"Cuerpo {body_atr:.2f} ATR — posible agotamiento ❌", "score": -25})

        # 7. Spread guard — más estricto que MomentumPulse: el SL aquí es más ceñido
        # (0.5-2.0 ATR vs 0.8-3.5), así que el mismo spread absoluto pesa más en R.
        if spread_dist > atr_val * 0.4:
            score -= 20
            factors.append({"k": "Spread", "v": f"Alto: {spread_dist:.5f}", "score": -20})
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
            buf = atr_val * 0.15
            if direction == 1:
                swing_low_price = price - feats["dist_swing_low_atr"] * atr_val
                cand_sl = swing_low_price - buf
                sl_dist = price - cand_sl
                if atr_val * 0.5 <= sl_dist <= atr_val * 2.0:
                    target_price_sl = cand_sl
                tp_price = price + max(atr_val * 2.0, (price - (target_price_sl or cand_sl)) * 1.3)
            else:
                swing_high_price = price + feats["dist_swing_high_atr"] * atr_val
                cand_sl = swing_high_price + buf
                sl_dist = cand_sl - price
                if atr_val * 0.5 <= sl_dist <= atr_val * 2.0:
                    target_price_sl = cand_sl
                tp_price = price - max(atr_val * 2.0, ((target_price_sl or cand_sl) - price) * 1.3)

            final_sl_dist = abs(price - target_price_sl) if target_price_sl > 0 else sl_dist
            # Tope de duración CORTO (45 min, no los 120 por defecto de duration_cap):
            # scalping busca resolución rápida, no dejar correr como MomentumPulse.
            tp_price = cap_tp_by_duration(price, direction, final_sl_dist, tp_price, atr_val, active_tf,
                                           max_duration_min=45, min_rr_floor=1.3)

        metadata = {
            "status": (
                f"BreakoutScalping | Score {score} | {'BUY' if direction == 1 else 'SELL'} | "
                f"TF {active_tf.upper()} | RSI {rsi_now:.1f}"
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
