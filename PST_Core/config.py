# PST_Core/config.py
# Configuration constants for PailsSentinelTrade

# TRADING RISK PARAMETERS (FTMO Friendly)
MAX_RISK_PCT = 1.5           # Riesgo máximo por operación
MAX_DRAWDOWN_PCT = 3.5       # Kill-switch de Drawdown del día (FTMO)

# DINAMIC PROTECTION (ATR MULTIPLIERS)
BE_ATR_MULTIPLIER = 1.5      # Activar Breakeven a 1.5 ATR
TRAIL_ATR_MULTIPLIER = 2.0   # Trailing Stop a 2.0 ATR de distancia

# TRADING DEFAULT PARAMS
DEFAULT_LOT = 0.01
DEFAULT_TP_PCT = 0.02        # 2% (Legacy/Manual)
DEFAULT_SL_PCT = 0.01        # 1% (Legacy/Manual)

TIMEFRAME_DEFAULT = 5        # M5 base
