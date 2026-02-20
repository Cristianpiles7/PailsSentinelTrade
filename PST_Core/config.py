# PST_Core/config.py
# Configuration constants for PailsSentinelTrade

# TRADING RISK PARAMETERS (FTMO Friendly)
# TRADING RISK PARAMETERS (Dynamic & Conservative)
MAX_RISK_PCT = 0.25          # Riesgo base por operación (0.25%)
MAX_DRAWDOWN_PCT = 3.5       # Kill-switch de Drawdown del día (FTMO Safe)
DAILY_LOSS_EXIT_USD = 400    # STOP DIARIO CRÍTICO ($: Cierre total a los $400 de pérdida)
# MAX_POSITION_COST_PCT = 25  # REMOVED: Usaremos Cubetas de Margen Dinámicas

# DINAMIC PROTECTION (ATR MULTIPLIERS)
BE_ATR_MULTIPLIER = 2.0      # Activar Breakeven a 2.0 ATR
TRAIL_ATR_MULTIPLIER = 2.5   # Trailing Stop a 2.5 ATR de distancia (Más ceñido)
SL_ATR_MULTIPLIER = 2.5      # Multiplicador ATR para Stop Loss (Ratio R:R mejorado)
TP_ATR_MULTIPLIER = 6.0      # Multiplicador ATR para Take Profit (Ratio 1:2)

# TRADING DEFAULT PARAMS
DEFAULT_LOT = 0.01
DEFAULT_TP_PCT = 0.02        # 2% (Legacy/Manual)
DEFAULT_SL_PCT = 0.01        # 1% (Legacy/Manual)

TIMEFRAME_DEFAULT = 5        # M5 base

# ACTIVOS 24/7 (CRIPTO)
CRYPTO_KEYWORDS = ["BTC", "ETH", "ADA", "SOL", "DOT", "LNK", "LTC", "UNI", "XLM", "XRP", "MATIC", "AVAX"]
# STRATEGY MANAGEMENT
# Estrategias activas globalmente (Nombres internos estándar)
ENABLED_STRATEGIES = [
    "PST-EMA-Flow",
    "PST-Channel-Master",
    "PST-Mean-Reversion",
    "PST-AI-Oracle-Gemini",
    "PST-AI-Oracle-Groq",
    "PST-AI-Oracle-Ollama"
]
