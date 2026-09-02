import pandas as pd
import pandas_ta as ta
import logging

from ..ml.tf_selector import AdaptiveTFSelector
from ..ml.feature_builder import FeatureBuilder
from ..ml.blend import MLBlendMixin
from ..ml.duration_cap import cap_tp_by_duration

logger = logging.getLogger("PST-VolExpansion")


class PSTVolExpansion(MLBlendMixin):
    """
    Ruptura de expansión de volatilidad: exige que el ATR actual esté en un
    percentil alto de su propia historia reciente (régimen cambiando de tranquilo a
    agitado) ANTES de aceptar una ruptura direccional del rango de 20 velas —
    revisita la tesis de Opening Range Breakout / gap-fade que ya se probó y
    falló en TA pura (ver memoria strategy_exploration_lessons: ORB 24/24 combos
    negativos, ORBFADE 4/4 negativos, ambas direcciones perdiendo a la vez = firma
    de mercado eficiente a esa escala, no de lógica mal elegida). La apuesta de esta
    versión es que el gate de calidad del modelo ML (blend.py, AUC>=0.52 sobre
    >=20 ejemplos de test) puede filtrar el ruido que hundió la versión puramente
    de reglas — pero la carga de la prueba es alta: TRÁTESE CON ESCEPTICISMO hasta
    tener validación IS/OOS fiel (Corriente A/B del plan) con resultado positivo
    consistente entre símbolos y ventanas temporales, igual que se exigió (y no se
    consiguió) para la versión de reglas puras.

    Corriente C del plan de expansión de estrategias (2026-08-12) — pendiente de
    smoke test, motor de backtest fiel, baseline IS/OOS. NO está en
    ENABLED_STRATEGIES (config.py) — diseño y registro únicamente.

    STRATEGY_TYPE="TREND": comparte régimen de ejecución (TREND/VOLATILE) y, si
    llega a producción, cubeta de capital con PST-MomentumPulse — a diferencia de
    esa estrategia, no exige alineación de EMA previa (busca el INICIO de un
    movimiento, no un pullback dentro de una tendencia ya establecida). Si ambas
    maduran a producción, decidir entonces si comparten cubeta TREND o si esta
    necesita una cubeta propia (ver Corriente D del plan, capital_bucket de
    registry.py, hoy sin conectar a BucketManager).

    Gate obligatorio (doble): percentil de ATR (atr_pct_rank, ventana de 100
    velas) >= ATR_PCTRANK_GATE Y ruptura del rango de 20 velas (mismo criterio de
    "cierre pegado al extremo" que PST-BreakoutScalping, pero en un timeframe más
    lento — esto busca el arranque de un movimiento de escala sesión/multi-hora,
    no un scalp). Sobre ese gate:
      · Fuerza de la expansión ... bonus si el percentil es extremo (>85)
      · Calidad de tendencia ..... ADX/Choppiness ya empezando a ordenarse (no
                                     exige consolidación: es el arranque)
      · Volumen .................. confirmación de participación
      · Confluencia HTF .......... H1 no debe estar en choppiness extremo (mismo
                                     movimiento visible en la escala mayor)
      · Anti-fakeout ............. mecha larga en contra penaliza (rechazo del
                                     nivel de ruptura, no ruptura limpia)
      · Spread ................... guard

    SL/TP: SL justo detrás del swing que originó la ruptura (banda 0.8-3.0 ATR).
    TP por múltiplo ATR amplio (proyección de continuación — no hay canal opuesto
    que apuntar, se está saliendo de un rango, no volviendo a él), con el tope de
    duración por DEFECTO (120 min: puede ser un movimiento más lento que un scalp).
    """

    STRATEGY_NAME = "PST-VolExpansion"
    STRATEGY_TYPE = "TREND"

    TF_CANDIDATES = ("m5", "m15", "m30")
    ANCHOR_TF = "m15"
    ATR_PCTRANK_GATE = 65.0
    # Punto de partida, sin calibrar — deliberadamente MÁS exigente que las otras
    # dos estrategias nuevas (45 BreakoutScalping / 40 MomentumPulse / 50
    # DivergenceReversion) porque esta revisita una tesis que ya falló sin ML:
    # mejor pecar de poco frecuente que reabrir el mismo agujero con blend ML de
    # por medio. Recalibrar SOLO con evidencia IS/OOS positiva y consistente.
    ENTRY_THRESHOLD = 55
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
        pct_rank = feats["atr_pct_rank"]
        body_atr = feats["candle_body_atr"]
        dist_high = feats["dist_swing_high_atr"]
        dist_low = feats["dist_swing_low_atr"]
        wick_up = feats["candle_wick_upper_atr"]
        wick_low = feats["candle_wick_lower_atr"]

        score = 0
        factors = []

        # 1a. GATE 1: expansión de volatilidad — sin esto, ni se evalúa la ruptura.
        if pct_rank < self.ATR_PCTRANK_GATE:
            return {
                "score": 0, "entry": 0, "atr": atr_val,
                "metadata": {"status": f"Sin expansión de volatilidad (percentil {pct_rank:.0f} < {self.ATR_PCTRANK_GATE:.0f})",
                             "factors_detailed": [{"k": "Expansión ATR", "v": f"Percentil {pct_rank:.0f}", "score": 0}],
                             "active_tf": active_tf},
            }

        # 1b. GATE 2: ruptura direccional del rango de 20 velas.
        if dist_high <= 0.2 and body_atr >= 0.4 and is_green:
            direction = 1
        elif dist_low <= 0.2 and body_atr >= 0.4 and is_red:
            direction = -1
        else:
            return {
                "score": 0, "entry": 0, "atr": atr_val,
                "metadata": {"status": f"Volatilidad expandida pero sin ruptura limpia en {active_tf}",
                             "factors_detailed": [{"k": "Expansión ATR", "v": f"Percentil {pct_rank:.0f} ✅ pero sin ruptura", "score": 0}],
                             "active_tf": active_tf},
            }

        score += 25
        factors.append({"k": "Expansión ATR", "v": f"Percentil {pct_rank:.0f} (>={self.ATR_PCTRANK_GATE:.0f}) ✅", "score": 25})
        if pct_rank > 85:
            score += 10
            factors.append({"k": "Expansión extrema", "v": f"Percentil {pct_rank:.0f} ✅", "score": 10})
        score += 20
        factors.append({"k": "Ruptura de rango", "v": f"{'Alcista' if direction == 1 else 'Bajista'}, cuerpo {body_atr:.2f} ATR ✅", "score": 20})

        # 2. Calidad de tendencia — ya empezando a ordenarse, no exige consolidación.
        adx_val, chop_val = feats["adx"], feats["chop"]
        if adx_val >= 20 and chop_val <= 55:
            score += 15
            factors.append({"k": "Trend Quality", "v": f"ADX {adx_val:.0f}/Chop {chop_val:.0f} — ordenándose ✅", "score": 15})
        elif chop_val > 70:
            score -= 15
            factors.append({"k": "Trend Quality", "v": f"ADX {adx_val:.0f}/Chop {chop_val:.0f} — aún lateral ❌", "score": -15})
        else:
            factors.append({"k": "Trend Quality", "v": f"ADX {adx_val:.0f}/Chop {chop_val:.0f}", "score": 0})

        # 3. Volumen.
        vol_rel = feats["vol_rel"]
        if vol_rel > 1.2:
            score += 10
            factors.append({"k": "Volumen", "v": f"{vol_rel:.2f}x media ✅", "score": 10})
        else:
            factors.append({"k": "Volumen", "v": f"{vol_rel:.2f}x media", "score": 0})

        # 4. Confluencia HTF — H1 no debe estar en choppiness extremo.
        htf_chop = feats["htf_chop"]
        if htf_chop <= 55:
            score += 10
            factors.append({"k": "Confluencia H1", "v": f"Chop {htf_chop:.0f} OK ✅", "score": 10})
        else:
            factors.append({"k": "Confluencia H1", "v": f"Chop {htf_chop:.0f} lateral", "score": 0})

        # 5. Anti-fakeout: mecha larga en contra sugiere rechazo del nivel, no ruptura limpia.
        wick_against = wick_low if direction == 1 else wick_up
        if wick_against > 1.0:
            score -= 20
            factors.append({"k": "Anti-Fakeout", "v": f"Mecha en contra {wick_against:.2f} ATR — posible rechazo ❌", "score": -20})

        # 6. Spread guard.
        if spread_dist > atr_val * 0.5:
            score -= 15
            factors.append({"k": "Spread", "v": f"Alto: {spread_dist:.5f}", "score": -15})
        else:
            factors.append({"k": "Spread", "v": f"OK: {spread_dist:.5f}", "score": 0})

        score = max(0, min(100, int(score)))

        # 7. Blend con el modelo entrenado (si existe uno para este grupo de activo;
        # si no, cae a solo-reglas sin cambiar nada de lo anterior).
        score, ml_factor = self._blend_with_model(score, mtf_data, active_tf, symbol)
        if ml_factor:
            factors.append(ml_factor)

        entry = direction if score >= self.ENTRY_THRESHOLD else 0

        # --- SL/TP estructural ---
        target_price_sl = 0.0
        tp_price = 0.0
        if entry != 0:
            buf = atr_val * 0.2
            if direction == 1:
                swing_low_price = price - feats["dist_swing_low_atr"] * atr_val
                cand_sl = swing_low_price - buf
                sl_dist = price - cand_sl
                if atr_val * 0.8 <= sl_dist <= atr_val * 3.0:
                    target_price_sl = cand_sl
                tp_price = price + max(atr_val * 3.0, (price - (target_price_sl or cand_sl)) * 1.5)
            else:
                swing_high_price = price + feats["dist_swing_high_atr"] * atr_val
                cand_sl = swing_high_price + buf
                sl_dist = cand_sl - price
                if atr_val * 0.8 <= sl_dist <= atr_val * 3.0:
                    target_price_sl = cand_sl
                tp_price = price - max(atr_val * 3.0, ((target_price_sl or cand_sl) - price) * 1.5)

            final_sl_dist = abs(price - target_price_sl) if target_price_sl > 0 else sl_dist
            tp_price = cap_tp_by_duration(price, direction, final_sl_dist, tp_price, atr_val, active_tf,
                                           min_rr_floor=1.5)

        metadata = {
            "status": (
                f"VolExpansion | Score {score} | {'BUY' if direction == 1 else 'SELL'} | "
                f"TF {active_tf.upper()} | ATR%ile {pct_rank:.0f}"
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
