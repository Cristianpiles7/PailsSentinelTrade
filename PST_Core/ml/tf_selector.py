"""
AdaptiveTFSelector — elige el timeframe "reactivo" (disparo de entrada + detección
de pivotes/divergencia) según el estado de volatilidad ACTUAL del símbolo, en vez de
una jerarquía fija HTF→LTF. Volatilidad relativa alta → timeframe rápido (para no
llegar tarde a una reversión); volatilidad baja → timeframe lento (para no operar
ruido de un mercado dormido).

Un objeto de esta clase se instancia UNA vez por estrategia por símbolo (mismo ciclo
de vida que RegimeClassifier hoy) para que la histéresis persista entre ciclos.
"""
from collections import deque, Counter

import pandas_ta as ta


class AdaptiveTFSelector:
    # Escalera rápido -> lento. m1 queda deliberadamente fuera por defecto: exigiría
    # activar include_m1=True en get_mtf_data_async para todo símbolo con alguna
    # estrategia nueva activa, igual que hoy solo se activa para SCALPING. Añadirla
    # es un cambio de una línea si algún día hace falta reactividad sub-2-minutos.
    LADDER = ("m2", "m3", "m5", "m15", "m30")

    ATR_LEN = 14

    def __init__(self, candidates=None, anchor_tf="m5", lookback=100, smoothing=3):
        self.candidates = list(candidates) if candidates else list(self.LADDER)
        self.anchor_tf = anchor_tf
        self.lookback = lookback
        self.smoothing = smoothing
        self._history = deque(maxlen=smoothing)

    def _atr_percentile(self, df):
        if df is None or len(df) < self.ATR_LEN + 5:
            return None
        atr = ta.atr(df["high"], df["low"], df["close"], length=self.ATR_LEN)
        if atr is None:
            return None
        window = atr.tail(self.lookback).dropna()
        if len(window) < 5:
            return None
        try:
            pct = float(window.rank(pct=True).iloc[-1] * 100.0)
        except Exception:
            return None
        return pct

    def _tf_for_percentile(self, pct: float) -> str:
        """Percentil alto (volatilidad relativa elevada) -> primer candidato (rápido).
        Percentil bajo -> último candidato (lento). Reparto en n cubos iguales."""
        n = len(self.candidates)
        if n <= 1:
            return self.candidates[0]
        bucket_width = 100.0 / n
        raw_idx = int(pct // bucket_width)
        raw_idx = max(0, min(n - 1, raw_idx))
        idx = (n - 1) - raw_idx
        return self.candidates[idx]

    def select(self, mtf_data: dict) -> tuple:
        """Devuelve (tf_elegido, diagnostics). Con histéresis: el TF devuelto es la
        moda de las últimas `smoothing` selecciones crudas, para no oscilar cada
        ciclo de 10s del orquestador."""
        anchor_df = mtf_data.get(self.anchor_tf) if mtf_data else None
        pct = self._atr_percentile(anchor_df)

        if pct is None:
            fallback = self.anchor_tf if self.anchor_tf in self.candidates else self.candidates[len(self.candidates) // 2]
            return fallback, {"atr_pct_rank": None, "raw_tf": fallback, "chosen_tf": fallback, "reason": "insufficient_data"}

        raw_tf = self._tf_for_percentile(pct)
        self._history.append(raw_tf)
        chosen_tf = Counter(self._history).most_common(1)[0][0]

        return chosen_tf, {"atr_pct_rank": pct, "raw_tf": raw_tf, "chosen_tf": chosen_tf}

    def reset(self):
        """Limpia la histéresis (útil en tests o si el símbolo cambia de contexto)."""
        self._history.clear()
