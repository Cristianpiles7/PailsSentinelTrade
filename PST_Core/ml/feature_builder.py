"""
FeatureBuilder — única fuente de verdad de features técnicos para el sistema nuevo
de estrategias (PST-MomentumPulse / PST-DivergenceReversion).

Por qué existe: el proyecto ya tuvo bugs de "train/live divergence" (ATR calculado
en M1 en el backtest pero en M5 en el executor, inflando el edge estimado). La única
forma de garantizar que vivo, backtest y entrenamiento offline ven exactamente los
mismos números es que los tres llamen al MISMO código. Nunca reimplementar un
indicador "a mano" fuera de esta clase para las estrategias nuevas.

build_series() hace todo el trabajo (vectorizado, sobre un DataFrame completo).
build() es un envoltorio fino que toma el último valor de cada serie — así viven
en un único camino de cálculo y no pueden divergir entre sí.
"""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pandas_ta as ta

from ..utils.tech_utils import get_asset_class, get_market_session


class FeatureBuilder:
    # Constantes de indicadores — declaradas UNA sola vez. Cualquier estrategia o
    # script de entrenamiento que necesite estos parámetros los lee de aquí.
    RSI_LEN = 14
    ATR_LEN = 14
    ADX_LEN = 14
    CHOP_LEN = 14
    EMA_LENS = (9, 21, 50)
    VOL_MA_LEN = 20
    VOL_ROC_PERIODS = 3
    ATR_PCTRANK_LOOKBACK = 100
    SWING_LOOKBACK = 20
    MIN_BARS = 60

    _ASSET_CLASSES = ("FOREX", "METAL", "INDEX", "EQUITIES", "CRYPTO")
    _SESSIONS = ("ASIAN", "LONDON", "NY", "OVERLAP")

    # Features que salen directamente de build_series() (indicador puro, sobre 1 TF).
    _SERIES_FEATURE_NAMES = [
        "rsi", "rsi_slope",
        "atr_pct_rank",
        "adx", "chop",
        "ema9_dist_atr", "ema21_dist_atr", "ema50_dist_atr",
        "ema_alignment",
        "vol_rel", "vol_roc",
        "candle_body_atr", "candle_wick_upper_atr", "candle_wick_lower_atr",
        "dist_swing_high_atr", "dist_swing_low_atr",
    ]

    # Orden COMPLETO y fijo del vector de features (entrenamiento e inferencia deben
    # construir el vector a partir de esta misma lista, nunca ad hoc).
    FEATURE_NAMES = (
        _SERIES_FEATURE_NAMES
        + ["htf_adx", "htf_chop"]
        + [f"asset_class_{a.lower()}" for a in _ASSET_CLASSES]
        + [f"session_{s.lower()}" for s in _SESSIONS]
    )

    # ------------------------------------------------------------------ #
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #
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

    @staticmethod
    def _pick_adx_col(adx_df):
        if adx_df is None:
            return None
        col = next((c for c in adx_df.columns if c.startswith("ADX")), None)
        return adx_df[col] if col else None

    @staticmethod
    def _normalize(df):
        """Valida columnas OHLC mínimas. No reindexa — build_series solo necesita
        orden temporal correcto, que ya viene garantizado por MT5/el caché."""
        if df is None:
            return None
        required = ("open", "high", "low", "close")
        if not all(c in df.columns for c in required):
            return None
        return df

    @classmethod
    def _bar_time(cls, df, as_of=None):
        if as_of is not None:
            return as_of
        try:
            if isinstance(df.index, pd.DatetimeIndex):
                return df.index[-1].to_pydatetime()
            if "time" in df.columns:
                return pd.to_datetime(df["time"].iloc[-1]).to_pydatetime()
        except Exception:
            pass
        return datetime.now(timezone.utc)

    # ------------------------------------------------------------------ #
    #  Núcleo vectorizado — usado por vivo, backtest Y entrenamiento       #
    # ------------------------------------------------------------------ #
    @classmethod
    def build_series(cls, df: pd.DataFrame) -> dict:
        """Devuelve dict[str, pd.Series] con los indicadores de _SERIES_FEATURE_NAMES
        calculados sobre TODO el df (vectorizado). {} si no hay datos suficientes."""
        df = cls._normalize(df)
        if df is None or len(df) < cls.MIN_BARS:
            return {}

        high, low, close, open_ = df["high"], df["low"], df["close"], df["open"]
        vol = None
        if "tick_volume" in df.columns:
            vol = df["tick_volume"]
        elif "volume" in df.columns:
            vol = df["volume"]

        rsi = ta.rsi(close, length=cls.RSI_LEN)
        atr = ta.atr(high, low, close, length=cls.ATR_LEN)
        adx = cls._pick_adx_col(ta.adx(high, low, close, length=cls.ADX_LEN))
        chop = ta.chop(high, low, close, length=cls.CHOP_LEN)

        if rsi is None or atr is None:
            return {}

        atr_safe = atr.replace(0, np.nan)
        emas = {n: ta.ema(close, length=n) for n in cls.EMA_LENS}

        out = {}
        out["rsi"] = rsi
        out["rsi_slope"] = rsi.diff()
        out["atr_pct_rank"] = (
            atr.rolling(cls.ATR_PCTRANK_LOOKBACK, min_periods=10).rank(pct=True) * 100.0
        )
        out["adx"] = adx if adx is not None else pd.Series(0.0, index=df.index)
        out["chop"] = chop if chop is not None else pd.Series(50.0, index=df.index)

        n_fast, n_mid, n_slow = cls.EMA_LENS
        for n in cls.EMA_LENS:
            out[f"ema{n}_dist_atr"] = (close - emas[n]) / atr_safe

        e_fast, e_mid, e_slow = emas[n_fast], emas[n_mid], emas[n_slow]
        bull = (e_fast > e_mid) & (e_mid > e_slow)
        bear = (e_fast < e_mid) & (e_mid < e_slow)
        out["ema_alignment"] = pd.Series(
            np.where(bull, 1.0, np.where(bear, -1.0, 0.0)), index=df.index
        )

        if vol is not None:
            vol_ma = vol.rolling(cls.VOL_MA_LEN).mean()
            out["vol_rel"] = vol / vol_ma.replace(0, np.nan)
            out["vol_roc"] = vol.pct_change(periods=cls.VOL_ROC_PERIODS)
        else:
            out["vol_rel"] = pd.Series(0.0, index=df.index)
            out["vol_roc"] = pd.Series(0.0, index=df.index)

        body = (close - open_).abs()
        upper_wick = high - pd.concat([open_, close], axis=1).max(axis=1)
        lower_wick = pd.concat([open_, close], axis=1).min(axis=1) - low
        out["candle_body_atr"] = body / atr_safe
        out["candle_wick_upper_atr"] = upper_wick / atr_safe
        out["candle_wick_lower_atr"] = lower_wick / atr_safe

        swing_high = high.rolling(cls.SWING_LOOKBACK).max()
        swing_low = low.rolling(cls.SWING_LOOKBACK).min()
        out["dist_swing_high_atr"] = (swing_high - close) / atr_safe
        out["dist_swing_low_atr"] = (close - swing_low) / atr_safe

        return out

    # ------------------------------------------------------------------ #
    #  Snapshot escalar — vivo y backtest bar-a-bar                       #
    # ------------------------------------------------------------------ #
    @classmethod
    def asset_class_features(cls, symbol: str) -> dict:
        try:
            cls_name = get_asset_class(symbol) if symbol else "FOREX"
        except Exception:
            cls_name = "FOREX"
        return {f"asset_class_{a.lower()}": (1.0 if a == cls_name else 0.0) for a in cls._ASSET_CLASSES}

    @classmethod
    def session_features(cls, bar_time: datetime) -> dict:
        try:
            sess = get_market_session(bar_time)
        except Exception:
            sess = "ASIAN"
        return {f"session_{s.lower()}": (1.0 if s == sess else 0.0) for s in cls._SESSIONS}

    @classmethod
    def build(cls, mtf_data: dict, active_tf: str, symbol: str = "", htf_tf: str = "h1",
              as_of: datetime = None) -> dict:
        """Snapshot escalar 'a cierre de la última vela' del active_tf, con contexto
        HTF (adx/chop) y one-hots de clase de activo/sesión. Mismo código que
        build_series() por debajo — nunca reimplementar el cálculo de indicadores aquí."""
        zeros = {name: 0.0 for name in cls.FEATURE_NAMES}

        df = mtf_data.get(active_tf) if mtf_data else None
        series_map = cls.build_series(df)
        if not series_map:
            return zeros

        snap = {name: cls._last(series_map.get(name)) for name in cls._SERIES_FEATURE_NAMES}

        htf_df = mtf_data.get(htf_tf) if mtf_data else None
        htf_series = cls.build_series(htf_df) if htf_df is not None else {}
        snap["htf_adx"] = cls._last(htf_series.get("adx"))
        snap["htf_chop"] = cls._last(htf_series.get("chop"), 50.0)

        snap.update(cls.asset_class_features(symbol))
        snap.update(cls.session_features(cls._bar_time(df, as_of)))

        return {name: snap.get(name, 0.0) for name in cls.FEATURE_NAMES}

    @classmethod
    def vector(cls, feature_dict: dict) -> list:
        """Vector ordenado según FEATURE_NAMES — el único método que train/inferencia
        deben usar para pasar de dict a fila de matriz numérica."""
        return [float(feature_dict.get(name, 0.0)) for name in cls.FEATURE_NAMES]
