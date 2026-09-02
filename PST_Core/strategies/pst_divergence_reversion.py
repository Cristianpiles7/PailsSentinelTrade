import pandas as pd
import pandas_ta as ta
import logging

from ..ml.tf_selector import AdaptiveTFSelector
from ..ml.blend import MLBlendMixin
from ..ml.duration_cap import cap_tp_by_duration
from ..utils.tech_utils import detect_divergence, calculate_channel_boundary, calculate_manual_score

logger = logging.getLogger("PST-DivergenceReversion")


class PSTDivergenceReversion(MLBlendMixin):
    """
    Suelos/techos por divergencia de RSI, en el timeframe que elige
    AdaptiveTFSelector según la volatilidad relativa del momento (no un TF fijo, ni
    una jerarquía HTF->LTF que confirma tarde).

    Gate obligatorio: divergencia RSI/precio detectada (detect_divergence) en el TF
    activo. Sobre ese gate se apilan filtros de calidad:

      · Veto de momentum ........ última vela en contra de la reversión anula la señal
      · Espacio RSI .............. bonus/penalización según cuánto recorrido le queda
                                    al RSI antes de la zona de sobrecompra/sobreventa
      · Confluencia estructural .. calculate_manual_score() contra el borde de canal
                                    (envelope de calculate_channel_boundary) más cercano
      · Spread ................... guard igual que RangeBreaker (más permisivo que scalping)

    Estructura (para SL/TP): el pivote exacto que originó la divergencia (SL) y el
    borde opuesto del canal (TP técnico).
    """

    STRATEGY_NAME = "PST-DivergenceReversion"
    STRATEGY_TYPE = "RANGE"

    # m2 queda fuera: la detección de pivotes/divergencia necesita más contexto de
    # velas del que un TF tan rápido suele ofrecer de forma limpia.
    TF_CANDIDATES = ("m3", "m5", "m15", "m30")
    ANCHOR_TF = "m5"
    # Punto de partida, no calibrado: el gate de divergencia por sí solo ya vale 40
    # puntos, y la confluencia de canal (el mayor bonus posible) solo aporta cuando
    # el precio coincide exactamente cerca del borde del canal en ese instante — algo
    # que en la práctica no siempre coincide con el pivote de la divergencia. Un
    # smoke test sobre datos reales (XAUUSD/US30/US100/EURUSD, 20d) mostró eventos de
    # divergencia genuinos puntuando 40-55; 65 dejaba el sistema sin ninguna señal.
    # Calibrar de verdad con el sweep de la Fase 3+ (pst_new_strategy_lab.py), no a ojo.
    ENTRY_THRESHOLD = 50
    MIN_BARS = 60

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

        high, low, close, open_ = df["high"], df["low"], df["close"], df["open"]

        rsi_series = ta.rsi(close, length=14)
        atr_series = ta.atr(high, low, close, length=14)
        adx_df = ta.adx(high, low, close, length=14)

        if rsi_series is None or atr_series is None:
            return {**neutral, "metadata": {"status": "Error calculando indicadores", "factors_detailed": []}}

        atr_val = self._last(atr_series, 0.0)
        price = self._last(close, 0.0)
        if atr_val <= 0 or price <= 0:
            return {**neutral, "metadata": {"status": "ATR/precio inválido", "factors_detailed": []}}

        # detect_divergence necesita la columna 'rsi'; se la damos ya calculada para
        # no recomputarla dos veces.
        df_div = df.copy()
        df_div["rsi"] = rsi_series
        div_type, pivot = detect_divergence(df_div, window=5, order=2, return_pivots=True)

        if div_type is None:
            return {
                "score": 0, "entry": 0, "atr": atr_val,
                "metadata": {"status": f"Sin divergencia RSI en {active_tf}", "factors_detailed": [],
                             "active_tf": active_tf, "atr_pct_rank": tf_diag.get("atr_pct_rank")},
            }

        direction = 1 if div_type == "BULLISH" else -1
        score = 40
        factors = [{"k": "Divergencia RSI", "v": f"{div_type} en {active_tf.upper()} (pivote idx {pivot['idx']})", "score": 40}]

        # --- Veto de momentum: la última vela cerrada debe ir a favor de la reversión ---
        last_open, last_close = self._last(open_), price
        is_green = last_close > last_open
        is_red = last_close < last_open
        if direction == 1 and is_red:
            return {
                "score": 0, "entry": 0, "atr": atr_val,
                "metadata": {"status": "Divergencia BULLISH pero última vela roja — VETADO", "factors_detailed":
                             [{"k": "Momentum", "v": "Vela en contra ❌", "score": 0}], "active_tf": active_tf},
            }
        if direction == -1 and is_green:
            return {
                "score": 0, "entry": 0, "atr": atr_val,
                "metadata": {"status": "Divergencia BEARISH pero última vela verde — VETADO", "factors_detailed":
                             [{"k": "Momentum", "v": "Vela en contra ❌", "score": 0}], "active_tf": active_tf},
            }
        factors.append({"k": "Momentum", "v": "Vela a favor ✅", "score": 0})

        # --- Espacio RSI: cuánto recorrido le queda antes de la zona extrema opuesta ---
        rsi_now = self._last(rsi_series, 50.0)
        if direction == 1:
            if rsi_now < 40:
                score += 15
                factors.append({"k": "Espacio RSI", "v": f"{rsi_now:.1f} (mucho recorrido al alza) ✅", "score": 15})
            elif rsi_now > 60:
                score -= 15
                factors.append({"k": "Espacio RSI", "v": f"{rsi_now:.1f} (ya agotado, poco recorrido) ❌", "score": -15})
            else:
                factors.append({"k": "Espacio RSI", "v": f"{rsi_now:.1f} (neutro)", "score": 0})
        else:
            if rsi_now > 60:
                score += 15
                factors.append({"k": "Espacio RSI", "v": f"{rsi_now:.1f} (mucho recorrido a la baja) ✅", "score": 15})
            elif rsi_now < 40:
                score -= 15
                factors.append({"k": "Espacio RSI", "v": f"{rsi_now:.1f} (ya agotado, poco recorrido) ❌", "score": -15})
            else:
                factors.append({"k": "Espacio RSI", "v": f"{rsi_now:.1f} (neutro)", "score": 0})

        # --- Confluencia estructural: borde de canal más cercano a la dirección ---
        adx_val = 0.0
        if adx_df is not None:
            adx_col = next((c for c in adx_df.columns if c.startswith("ADX")), None)
            adx_val = self._last(adx_df[adx_col]) if adx_col else 0.0

        vol_col = "tick_volume" if "tick_volume" in df.columns else ("volume" if "volume" in df.columns else None)
        vol_val = self._last(df[vol_col]) if vol_col else 0.0
        vol_ma = self._last(df[vol_col].rolling(20).mean()) if vol_col else 0.0

        upper_now = lower_now = None
        try:
            _, _, params = calculate_channel_boundary(df, window=10, projection=0, recent_pivots=15)
            if params:
                last_idx = params["last_idx"]
                upper_now = params["slope_h"] * last_idx + params["intercept_h"]
                lower_now = params["slope_l"] * last_idx + params["intercept_l"]
        except Exception:
            pass

        lvl_price = lower_now if direction == 1 else upper_now
        l_type = "SUPPORT" if direction == 1 else "RESISTANCE"
        if lvl_price is not None and lvl_price > 0:
            m_score, m_action, m_factors, m_reco = calculate_manual_score(
                price, lvl_price, l_type, rsi_now, vol_val, vol_ma,
                is_green=is_green, is_red=is_red, trend_slope=0, adx=adx_val, atr_val=atr_val,
            )
            # Se pondera al 40%: es una confirmación de confluencia, no el motor principal
            # (el gate ya es la divergencia). Evita que un canal ruidoso domine el score.
            weighted = round(m_score * 0.4)
            score += weighted
            factors.append({"k": "Confluencia de canal", "v": f"{l_type} {lvl_price:.5f} → {m_action} (score bruto {m_score})", "score": weighted})
            factors.extend(m_factors)
        else:
            factors.append({"k": "Confluencia de canal", "v": "Canal no resoluble (sin pivotes suficientes)", "score": 0})

        # --- Spread guard (igual que RangeBreaker: más permisivo que scalping) ---
        if spread_dist > atr_val * 0.35:
            score -= 10
            factors.append({"k": "Spread", "v": f"Alto: {spread_dist:.5f}", "score": -10})
        else:
            factors.append({"k": "Spread", "v": f"OK: {spread_dist:.5f}", "score": 0})

        score = max(0, min(100, int(score)))

        # Blend con el modelo entrenado (si existe uno para este grupo de activo;
        # si no, cae a solo-reglas sin cambiar nada de lo anterior).
        score, ml_factor = self._blend_with_model(score, mtf_data, active_tf, symbol)
        if ml_factor:
            factors.append(ml_factor)

        entry = direction if score >= self.ENTRY_THRESHOLD else 0

        # --- SL estructural: justo detrás del pivote que originó la divergencia ---
        target_price_sl = 0.0
        tp_price = 0.0
        if entry != 0:
            buf = atr_val * 0.25
            pivot_price = pivot["price"]
            if direction == 1:
                cand_sl = pivot_price - buf
                sl_dist = price - cand_sl
                if atr_val * 0.5 <= sl_dist <= atr_val * 4.0:
                    target_price_sl = cand_sl
                tp_price = upper_now if (upper_now and upper_now > price) else price + atr_val * 2.5
            else:
                cand_sl = pivot_price + buf
                sl_dist = cand_sl - price
                if atr_val * 0.5 <= sl_dist <= atr_val * 4.0:
                    target_price_sl = cand_sl
                tp_price = lower_now if (lower_now and lower_now < price) else price - atr_val * 2.5

            # Tope de duración esperada (~2h por defecto, no un cierre por reloj —
            # ver PST_Core/ml/duration_cap.py): acerca el TP en activos volátiles
            # donde el borde de canal puede tardar horas en alcanzarse, sin bajar
            # del R:R mínimo.
            final_sl_dist = abs(price - target_price_sl) if target_price_sl > 0 else sl_dist
            tp_price = cap_tp_by_duration(price, direction, final_sl_dist, tp_price, atr_val, active_tf,
                                           min_rr_floor=1.5)

        metadata = {
            "status": (
                f"DivergenceReversion | Score {score} | {'BUY' if direction == 1 else 'SELL'} | "
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
