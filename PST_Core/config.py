# PST_Core/config.py
# Configuration constants for PailsSentinelTrade

# TRADING RISK PARAMETERS (FTMO Friendly)
# TRADING RISK PARAMETERS (FTMO Friendly)
MAX_RISK_PCT = 1.5           # Riesgo máximo en caso de SL
MAX_DRAWDOWN_PCT = 3.5       # Kill-switch de Drawdown del día
MAX_POSITION_COST_PCT = 25    # Costo máximo de entrada por operación (% del balance)

# DINAMIC PROTECTION (ATR MULTIPLIERS)
BE_ATR_MULTIPLIER = 2.0      # Activar Breakeven a 2.0 ATR
TRAIL_ATR_MULTIPLIER = 3.0   # Trailing Stop a 3.0 ATR de distancia
SL_ATR_MULTIPLIER = 3.0      # Multiplicador ATR para Stop Loss
TP_ATR_MULTIPLIER = 6.0      # Multiplicador ATR para Take Profit (Ratio 1:2)

# TRADING DEFAULT PARAMS
DEFAULT_LOT = 0.01
DEFAULT_TP_PCT = 0.02        # 2% (Legacy/Manual)
DEFAULT_SL_PCT = 0.01        # 1% (Legacy/Manual)

TIMEFRAME_DEFAULT = 5        # M5 base

# ACTIVOS 24/7 (CRIPTO)
CRYPTO_KEYWORDS = ["BTC", "ETH", "ADA", "SOL", "DOT", "LNK", "LTC", "UNI", "XLM", "XRP", "MATIC", "AVAX"]
