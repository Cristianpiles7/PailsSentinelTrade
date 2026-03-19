# PST_Core/config.py
import os
import sys

def get_db_path():
    if hasattr(sys, '_MEIPASS'):
        # En el ejecutable (PyInstaller), la base es la carpeta del EXE
        base_persist_dir = os.path.dirname(os.path.abspath(sys.executable))
        print(f"[DEBUG-EXE] sys.executable: {sys.executable}")
        print(f"[DEBUG-EXE] base_persist_dir: {base_persist_dir}")
    else:
        # En desarrollo, la base es la raíz del proyecto
        # Subimos dos niveles desde PST_Core/config.py
        current_file = os.path.abspath(__file__)
        base_persist_dir = os.path.dirname(os.path.dirname(current_file))
        print(f"[DEBUG-DEV] base_persist_dir: {base_persist_dir}")
    
    # Construir ruta por defecto: [Base]/PST_Core/data/pst_trading.db
    default_path = os.path.join(base_persist_dir, "PST_Core", "data", "pst_trading.db")
    
    # Prioridad: Variable de entorno > Ruta calculada
    final_path = os.getenv("DB_PATH", default_path)
    
    # Asegurar que sea una ruta absoluta real
    return os.path.abspath(final_path)

DB_PATH = get_db_path()

# Configuration constants for PailsSentinelTrade

# TRADING RISK PARAMETERS (FTMO Friendly)
# TRADING RISK PARAMETERS (Dynamic & Conservative)
MAX_RISK_PCT = 0.25          # Riesgo base por operación (0.25%)
MAX_DRAWDOWN_PCT = 3.5       # Kill-switch de Drawdown del día (FTMO Safe)
DAILY_LOSS_EXIT_USD = 400    # STOP DIARIO CRÍTICO ($: Cierre total a los $400 de pérdida)
# MAX_POSITION_COST_PCT = 25  # REMOVED: Usaremos Cubetas de Margen Dinámicas

# DINAMIC PROTECTION (ATR MULTIPLIERS)
BE_ATR_MULTIPLIER = 2.0      # Activar Breakeven a 2.0 ATR (Restaurado)
TRAIL_ATR_MULTIPLIER = 2.5   # Trailing Stop a 2.5 ATR
SL_ATR_MULTIPLIER = 2.5      # Multiplicador ATR para Stop Loss
TP_ATR_MULTIPLIER = 6.0      # Fallback global (Restaurado)
MAX_SCALPER_SL_POINTS = 15   # RIESGO MÁXIMO EN PUNTOS (Subido de 10 a 15 por User Req)
MIN_RR_RATIO = 1.4           # Ratio R:R mínimo (Subido de 1.3 a 1.4 por User Req)

TP_ATR_BY_CLASS = {
    "CRYPTO": 4.0,           
    "INDEX": 5.5,            
    "METAL": 3.5,           
    "FOREX": 6.0             
}

# SESSION PROTECTION (Safety Closer)
SESSION_PROTECTION = {
    "STOCK_CLOSE_TIME": "21:55",    # Cierre diario acciones (CET)
    "WEEKEND_CLOSE_TIME": "21:55",  # Cierre viernes general (CET)
    "CLOSE_MINUTES_BEFORE": 5,
    "EXCLUDE_CATEGORIES": ["CRYPTO"] # Nunca cerrar cripto por horario
}

# TRADING DEFAULT PARAMS
DEFAULT_LOT = 0.01
DEFAULT_TP_PCT = 0.02        # 2% (Legacy/Manual)
DEFAULT_SL_PCT = 0.01        # 1% (Legacy/Manual)

TIMEFRAME_DEFAULT = 5        # M5 base

# ACTIVOS 24/7 (CRIPTO)
CRYPTO_KEYWORDS = ["BTC", "ETH", "ADA", "SOL", "DOT", "LNK", "LTC", "UNI", "XLM", "XRP", "MATIC", "AVAX"]
# STRATEGY MANAGEMENT
STRATEGY_CATEGORIES = {
    "PST-EMA-Flow": "CORE",
    "PST-Channel-Master": "CORE",
    "PST-Mean-Reversion": "CORE",
    "PST-Liquidity-Hunter": "CORE",
    "PST-Scalper-Pro": "SCALPING",
    "PST-AI-Oracle-gemini": "AI",
    "PST-AI-Oracle-groq": "AI",
    "PST-AI-Oracle-ollama": "AI"
}

# PARALLEL OPERATIVE LIMITS
MAX_POSITIONS_PER_CATEGORY = {"CORE": 1, "SCALPING": 1, "AI": 1}
MAX_SYMBOL_EXPOSURE_PCT = 1.25  # Riesgo total sumado por activo

# SCALPER DEFAULTS
SCALPER_TARGETS = ["BTCUSD", "ETHUSD", "US500.cash", "XAUUSD", "EURUSD", "NAS100"]
SCALPER_RISK_DEFAULT = 0.08 # Reducido a 0.08% para mantener pérdidas entre 5-10€

# Estrategias activas globalmente (Nombres internos estándar)
ENABLED_STRATEGIES = [
    "PST-EMA-Flow",
    "PST-Channel-Master",
    "PST-Liquidity-Hunter",
    "PST-Scalper-Pro", # NEW
    "PST-AI-Oracle-gemini",
    "PST-AI-Oracle-groq",
    "PST-AI-Oracle-ollama"
]
