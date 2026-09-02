import pandas as pd
import pandas_ta as ta
import logging

from ..ml.duration_cap import cap_tp_by_duration

logger = logging.getLogger("PST-CryptoRelativeStrength")


class PSTCryptoRelativeStrength:
    """
    Rotación de fuerza relativa BTC<->ETH: cuando uno de los dos ("líder") hace un
    movimiento direccional claro en una ventana reciente y el otro ("seguidor") se
    queda atrás, se apuesta a que el seguidor recupera terreno en la MISMA
    dirección — la señal nace de la RELACIÓN entre los dos precios, no de un
    indicador técnico sobre un único símbolo.

    Por qué esto y no otra variante de TA de un solo símbolo: PST-VolExpansion y,
    antes, micro-reversión/micro-momentum (ver memoria micro_scalping_strategy)
    fallaron los 3 en cripto con la MISMA firma — BTC y ETH divergen porque cada
    uno se puntúa de forma independiente con la misma lógica genérica (RSI/EMA/
    ATR), ya muy arbitrada a esta escala. Aquí la correlación ENTRE ambos ES la
    señal, algo que ninguno de los intentos anteriores usó.

    Gate obligatorio (doble):
      1. El líder se movió >= MIN_LEADER_MOVE_ATR (su propio ATR) en LOOKBACK_BARS.
      2. El seguidor NO ha recorrido más del LAG_RATIO_MAX de ese movimiento en la
         misma dirección (si ya casi ha alcanzado al líder, no queda oportunidad;
         si se ha movido con fuerza en la dirección CONTRARIA, es desacople real,
         no rezago — se veta).

    Sobre ese gate:
      · Fuerza del impulso líder ... bonus si el movimiento es >1.5x su ATR
      · Espacio RSI del seguidor ... no debe estar ya agotado en la dirección
                                      esperada (mismo concepto que DivergenceReversion)
      · Volumen del líder .......... confirmación de participación real
      · Anti-fakeout ............... la última vela del seguidor ya moviéndose en
                                      contra penaliza (aviso de reversión inmediata)
      · Spread (del seguidor, que es el símbolo que se opera) ...... guard

    INTERFAZ DISTINTA A PROPÓSITO: no implementa calculate_signal(mtf_data, ...)
    porque necesita los datos de DOS símbolos a la vez (evaluate_pair). El
    orquestador en vivo evalúa una tarea por símbolo y hoy no tiene forma de
    inyectar el "contrapeso" — conectar esto al bot en real es trabajo de
    arquitectura pendiente (enseñar al orquestador/SymbolTask a pasar el segundo
    símbolo), deliberadamente NO emprendido todavía. Validar primero si la idea
    tiene edge real con Tools/pst_crypto_pair_lab.py; solo si lo tiene vale la
    pena invertir en la integración en vivo. NO está registrada en
    PST_Core/strategies/registry.py por el mismo motivo — registrarla ahí
    implicaría (falsamente) que es plug-compatible con el resto del sistema.

    SL/TP: SL estructural (swing del seguidor, banda 0.8-3.0 ATR). TP por múltiplo
    ATR moderado (no es un trade de "dejar correr", es una recuperación acotada
    hacia el líder) con tope de duración estándar.
    """

    STRATEGY_NAME = "PST-CryptoRelativeStrength"
    STRATEGY_TYPE = "ALL"  # rotación entre activos correlacionados, no depende de régimen TREND/RANGE

    ATR_LEN = 14
    RSI_LEN = 14
    SWING_LOOKBACK = 20
    LOOKBACK_BARS = 12          # ~1h en M5: ventana para medir el impulso del líder
    LAG_RATIO_MAX = 0.4         # el seguidor no debe haber recorrido >40% del movimiento del líder
    OPPOSITE_VETO_RATIO = 0.3   # si el seguidor ya se movió >30% del líder EN CONTRA, se veta (desacople real)
    MIN_LEADER_MOVE_ATR = 1.0   # el líder debe haberse movido >= 1x su propio ATR en la ventana
    ENTRY_THRESHOLD = 50
    MIN_BARS = 60

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

    @classmethod
    def _indicators(cls, df):
        """ATR/RSI/swing sobre 'm5' de un símbolo — mismos parámetros que FeatureBuilder."""
        high, low, close = df["high"], df["low"], df["close"]
        atr = ta.atr(high, low, close, length=cls.ATR_LEN)
        rsi = ta.rsi(close, length=cls.RSI_LEN)
        swing_high = high.rolling(cls.SWING_LOOKBACK).max()
        swing_low = low.rolling(cls.SWING_LOOKBACK).min()
        return atr, rsi, swing_high, swing_low

    async def evaluate_pair(self, follower_symbol, follower_data, leader_symbol, leader_data,
                             spread_dist: float = 0.0) -> dict:
        """
        follower_data / leader_data: dict {"m5": df, ...} — solo se usa "m5" hoy.
        Devuelve el mismo shape que calculate_signal() del resto de estrategias
        (score/entry/atr/tp_price/direction/metadata), pero evaluado sobre el
        FOLLOWER (es el símbolo que se operaría).
        """
        neutral = {"score": 0, "entry": 0, "atr": 0,
                   "metadata": {"status": "Sin datos suficientes", "factors_detailed": []}}

        df_f = follower_data.get("m5") if follower_data else None
        df_l = leader_data.get("m5") if leader_data else None
        if df_f is None or df_l is None or len(df_f) < self.MIN_BARS or len(df_l) < self.MIN_BARS:
            return neutral
        if len(df_f) <= self.LOOKBACK_BARS or len(df_l) <= self.LOOKBACK_BARS:
            return neutral

        atr_f, rsi_f, swing_high_f, swing_low_f = self._indicators(df_f)
        atr_l, _, _, _ = self._indicators(df_l)

        atr_f_val = self._last(atr_f, 0.0)
        atr_l_val = self._last(atr_l, 0.0)
        price_f = self._last(df_f["close"], 0.0)
        price_l = self._last(df_l["close"], 0.0)
        if atr_f_val <= 0 or atr_l_val <= 0 or price_f <= 0 or price_l <= 0:
            return neutral

        close_f_now, close_f_ago = float(df_f["close"].iloc[-1]), float(df_f["close"].iloc[-1 - self.LOOKBACK_BARS])
        close_l_now, close_l_ago = float(df_l["close"].iloc[-1]), float(df_l["close"].iloc[-1 - self.LOOKBACK_BARS])
        if close_f_ago <= 0 or close_l_ago <= 0:
            return neutral

        leader_ret = (close_l_now - close_l_ago) / close_l_ago
        follower_ret = (close_f_now - close_f_ago) / close_f_ago
        leader_move_atr = abs(close_l_now - close_l_ago) / atr_l_val

        score = 0
        factors = []

        # 1. GATE: líder con impulso claro.
        if leader_move_atr < self.MIN_LEADER_MOVE_ATR or leader_ret == 0:
            return {**neutral, "metadata": {
                "status": f"{leader_symbol} sin impulso suficiente ({leader_move_atr:.2f} ATR < {self.MIN_LEADER_MOVE_ATR})",
                "factors_detailed": []}}

        direction = 1 if leader_ret > 0 else -1
        same_dir_component = follower_ret * direction  # retorno del seguidor proyectado en la dirección del líder

        # 2. GATE: seguidor rezagado, no desacoplado ni ya alcanzado.
        if same_dir_component < -self.OPPOSITE_VETO_RATIO * abs(leader_ret):
            return {**neutral, "metadata": {
                "status": f"{follower_symbol} se mueve en CONTRA de {leader_symbol} — desacople real, no rezago",
                "factors_detailed": [{"k": "Veto desacople", "v": "Seguidor en contra", "score": 0}]}}
        if same_dir_component > self.LAG_RATIO_MAX * abs(leader_ret):
            return {**neutral, "metadata": {
                "status": f"{follower_symbol} ya alcanzó a {leader_symbol} — sin oportunidad de rezago",
                "factors_detailed": []}}

        score += 35
        lag_pct = 100 * (1 - same_dir_component / abs(leader_ret)) if leader_ret != 0 else 100
        factors.append({"k": "Rezago", "v": f"{leader_symbol} {leader_ret*100:+.2f}% vs {follower_symbol} "
                                             f"{follower_ret*100:+.2f}% ({lag_pct:.0f}% de rezago) ✅", "score": 35})

        # 3. Fuerza del impulso líder.
        if leader_move_atr > 1.5:
            score += 15
            factors.append({"k": "Impulso líder", "v": f"{leader_move_atr:.2f} ATR — fuerte ✅", "score": 15})
        else:
            factors.append({"k": "Impulso líder", "v": f"{leader_move_atr:.2f} ATR", "score": 0})

        # 4. Espacio RSI del seguidor (no agotado en la dirección esperada).
        rsi_now = self._last(rsi_f, 50.0)
        if direction == 1:
            if rsi_now < 60:
                score += 15
                factors.append({"k": "Espacio RSI", "v": f"{rsi_now:.1f} — margen para subir ✅", "score": 15})
            elif rsi_now > 75:
                score -= 15
                factors.append({"k": "Espacio RSI", "v": f"{rsi_now:.1f} — ya agotado ❌", "score": -15})
            else:
                factors.append({"k": "Espacio RSI", "v": f"{rsi_now:.1f}", "score": 0})
        else:
            if rsi_now > 40:
                score += 15
                factors.append({"k": "Espacio RSI", "v": f"{rsi_now:.1f} — margen para bajar ✅", "score": 15})
            elif rsi_now < 25:
                score -= 15
                factors.append({"k": "Espacio RSI", "v": f"{rsi_now:.1f} — ya agotado ❌", "score": -15})
            else:
                factors.append({"k": "Espacio RSI", "v": f"{rsi_now:.1f}", "score": 0})

        # 5. Volumen del líder — confirmación de participación real.
        vol_col = "tick_volume" if "tick_volume" in df_l.columns else ("volume" if "volume" in df_l.columns else None)
        if vol_col:
            vol_now = self._last(df_l[vol_col])
            vol_ma = self._last(df_l[vol_col].rolling(20).mean())
            if vol_ma > 0 and vol_now / vol_ma > 1.1:
                score += 10
                factors.append({"k": "Volumen líder", "v": "Confirmado ✅", "score": 10})
            else:
                factors.append({"k": "Volumen líder", "v": "Sin confirmar", "score": 0})

        # 6. Anti-fakeout: última vela del seguidor ya en contra.
        last_open_f, last_close_f = float(df_f["open"].iloc[-1]), close_f_now
        against = (last_close_f < last_open_f) if direction == 1 else (last_close_f > last_open_f)
        if against:
            score -= 20
            factors.append({"k": "Anti-Fakeout", "v": "Última vela del seguidor en contra ❌", "score": -20})

        # 7. Spread guard (sobre el seguidor, que es lo que se opera).
        if spread_dist > atr_f_val * 0.4:
            score -= 15
            factors.append({"k": "Spread", "v": f"Alto: {spread_dist:.5f}", "score": -15})
        else:
            factors.append({"k": "Spread", "v": f"OK: {spread_dist:.5f}", "score": 0})

        score = max(0, min(100, int(score)))
        entry = direction if score >= self.ENTRY_THRESHOLD else 0

        # --- SL/TP estructural sobre el seguidor ---
        target_price_sl = 0.0
        tp_price = 0.0
        if entry != 0:
            buf = atr_f_val * 0.2
            swing_low_val = self._last(swing_low_f, price_f - atr_f_val)
            swing_high_val = self._last(swing_high_f, price_f + atr_f_val)
            if direction == 1:
                cand_sl = swing_low_val - buf
                sl_dist = price_f - cand_sl
                if atr_f_val * 0.8 <= sl_dist <= atr_f_val * 3.0:
                    target_price_sl = cand_sl
                tp_price = price_f + max(atr_f_val * 3.0, (price_f - (target_price_sl or cand_sl)) * 1.5)
            else:
                cand_sl = swing_high_val + buf
                sl_dist = cand_sl - price_f
                if atr_f_val * 0.8 <= sl_dist <= atr_f_val * 3.0:
                    target_price_sl = cand_sl
                tp_price = price_f - max(atr_f_val * 3.0, ((target_price_sl or cand_sl) - price_f) * 1.5)

            final_sl_dist = abs(price_f - target_price_sl) if target_price_sl > 0 else sl_dist
            tp_price = cap_tp_by_duration(price_f, direction, final_sl_dist, tp_price, atr_f_val, "m5",
                                           min_rr_floor=1.5)

        metadata = {
            "status": (f"CryptoRelativeStrength | Score {score} | {'BUY' if direction == 1 else 'SELL'} "
                       f"{follower_symbol} (siguiendo a {leader_symbol}) | Rezago {lag_pct:.0f}%"),
            "factors_detailed": factors,
            "leader": leader_symbol, "follower": follower_symbol,
            "leader_ret_pct": leader_ret * 100, "follower_ret_pct": follower_ret * 100,
            "threshold_used": self.ENTRY_THRESHOLD,
        }
        if target_price_sl > 0:
            metadata["target_price_sl"] = target_price_sl

        return {"score": score, "entry": entry, "atr": atr_f_val, "tp_price": tp_price,
                "direction": direction, "metadata": metadata}
