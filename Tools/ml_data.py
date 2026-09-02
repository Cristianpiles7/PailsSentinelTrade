"""
Descarga + caché determinista de datos multi-TF para el sistema ML nuevo
(PST-MomentumPulse / PST-DivergenceReversion). Mismo patrón que
Tools/pst_bt_lab.py::get_data(), pero con el set completo de timeframes que usa
AdaptiveTFSelector/FeatureBuilder (m2/m3/m5/m15/m30/h1/h4 — sin m1, ver
PST_Core/ml/tf_selector.py::LADDER) y caché propia en Tools/.ml_cache/ para no
colisionar con el caché de PrecisionScalping.
"""
import os
import pickle
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(REPO_ROOT, "Tools", ".ml_cache")

# Espejo del set de timeframes de get_mtf_data_async (PST_Core/engine/mt5_async.py),
# sin m1 — el nuevo sistema no lo necesita (ver AdaptiveTFSelector.LADDER).
_TIMEFRAMES = ("m2", "m3", "m5", "m15", "m30", "h1", "h4")


def get_full_mtf_data(symbol: str, days: int, refresh: bool = False) -> dict:
    """Devuelve dict {"point", "spread_dist", "tick_value", "tick_size", "m2": df, ...}.
    Cachea en Tools/.ml_cache/{symbol}_{days}d.pkl. None si no hay datos."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{symbol}_{days}d.pkl")
    if os.path.exists(path) and not refresh:
        with open(path, "rb") as f:
            return pickle.load(f)

    import MetaTrader5 as mt5
    import pandas as pd

    if not mt5.initialize():
        print(f"    WARN: MT5 init fallo: {mt5.last_error()}")
        return None
    mt5.symbol_select(symbol, True)

    info = mt5.symbol_info(symbol)
    point = info.point if info else None
    spread_dist = (info.spread * info.point) if info else 0.0
    tick_value = getattr(info, "trade_tick_value", None) if info else None
    tick_size = getattr(info, "trade_tick_size", None) if info else None
    swap_long = getattr(info, "swap_long", 0.0) if info else 0.0
    swap_short = getattr(info, "swap_short", 0.0) if info else 0.0

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days)
    out = {"point": point, "spread_dist": spread_dist, "tick_value": tick_value, "tick_size": tick_size,
           "swap_long": swap_long, "swap_short": swap_short}

    tf_const = {
        "m2": mt5.TIMEFRAME_M2, "m3": mt5.TIMEFRAME_M3, "m5": mt5.TIMEFRAME_M5,
        "m15": mt5.TIMEFRAME_M15, "m30": mt5.TIMEFRAME_M30,
        "h1": mt5.TIMEFRAME_H1, "h4": mt5.TIMEFRAME_H4,
    }
    for name in _TIMEFRAMES:
        rates = mt5.copy_rates_range(symbol, tf_const[name], start_dt, end_dt)
        if rates is None or len(rates) == 0:
            print(f"    {symbol} {name}: sin datos")
            return None
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        out[name] = df
        print(f"    {symbol} {name:>3}: {len(df):>6} barras")

    with open(path, "wb") as f:
        pickle.dump(out, f)
    return out
