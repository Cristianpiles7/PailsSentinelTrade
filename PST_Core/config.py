# PST_Core/config.py
import os
import sys
from dotenv import load_dotenv

def get_db_path():
    """Calcula la ruta de la base de datos buscando la persistencia de forma inteligente (Autodiscovery v1.8.3)."""
    if hasattr(sys, '_MEIPASS'):
        # En el ejecutable (PyInstaller)
        base_persist_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        # Búsqueda robusta del .env (v1.8.5)
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        base_persist_dir = os.path.dirname(repo_root) # Por compatibilidad con candidatos de abajo
        
        search_dirs = [
            repo_root, 
            base_persist_dir, 
            os.getcwd()
        ]
        for d in search_dirs:
            p = os.path.join(d, ".env")
            if os.path.exists(p):
                print(f"DEBUG: Cargando .env desde {p}")
                load_dotenv(p, override=True)
                break
        else:
            print("DEBUG: No se encontro archivo .env en las rutas buscadas.")
            
    # Candidatos de ruta (v1.8.3 Autodiscovery)
    candidates = [
        os.getenv("DB_PATH"), # 1. Prioridad: Variable de entorno
        os.path.join(base_persist_dir, "PST_Core", "data", "pst_trading.db"), # 2. Ruta calculada estándar
        os.path.join(os.path.dirname(base_persist_dir), "PST_Core", "data", "pst_trading.db"), # 3. Un nivel arriba (Desktop/BOLSA/...)
        os.path.join(os.path.dirname(os.path.dirname(base_persist_dir)), "PST_Core", "data", "pst_trading.db") # 4. Dos niveles arriba
    ]
    
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            # Comprobamos si es la DB pesada (la de 37MB que mencionas)
            if os.path.getsize(candidate) > 1000 * 1024:
                return os.path.abspath(candidate)
    
    # Si no se encuentra ninguna "buena", devolvemos la estándar (se creará de cero si es necesario)
    return os.path.abspath(os.path.join(base_persist_dir, "PST_Core", "data", "pst_trading.db"))

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
MAX_SCALPER_SL_POINTS = 35   # RIESGO MÁXIMO EN PUNTOS (Subido de 15 a 35 para evitar ruido)
MIN_RR_RATIO = 1.8           # Ratio R:R mínimo (Subido de 1.6 a 1.8 para mejorar AvgWin)

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
