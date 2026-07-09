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
DAILY_LOSS_PCT = 2.0         # Kill-switch diario: para si el día pierde más de este % del balance
DAILY_LOSS_EXIT_USD = 999999 # STOP DIARIO DESACTIVADO (Modo Pruebas: $999,999)
# Watchdog de pausa automática por drawdown SEMANAL de estrategia (orchestrator.py):
# desactivado a petición del usuario (cuenta FTMO de prueba, sin dinero real en juego) -
# no queremos que ninguna estrategia se auto-pausee nunca. El kill-switch DIARIO
# (DAILY_LOSS_PCT) y el de drawdown (MAX_DRAWDOWN_PCT) NO se tocan, son otro mecanismo.
WEEKLY_STRATEGY_PAUSE_ENABLED = False
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
    "PST-AlphaTrend":        "TREND",
    "PST-RangeBreaker":      "RANGE",
    "PST-PrecisionScalping": "SCALPING",
}

# PARALLEL OPERATIVE LIMITS
MAX_TOTAL_OPEN_POSITIONS = 999  # Sin límite global (filtrado por estrategia/categoría)
MAX_POSITIONS_PER_CATEGORY = {"TREND": 999, "RANGE": 999, "SCALPING": 999, "ALL": 999}
MAX_SYMBOL_EXPOSURE_PCT = 2.5

# SCALPER DEFAULTS
SCALPER_TARGETS = ["BTCUSD", "ETHUSD", "US500.cash", "XAUUSD", "EURUSD", "NAS100"]
SCALPER_RISK_DEFAULT = 0.08
SCALPER_MAX_LOSS_EUR = 12.0

# CAP GLOBAL DE PÉRDIDA POR OPERACIÓN (modo MONEY)
# Ninguna estrategia puede arriesgar más de este valor en una sola operación.
MAX_LOSS_EUR = 7.0

# FILTROS DE SESIONES HORARIAS (CET/Hora MT5 local del bot)
SESSION_SESSIONS_ONLY = True
LONDRES_SESSION_START = "08:00"
LONDRES_SESSION_END = "16:30"
NY_SESSION_START = "13:30"
NY_SESSION_END = "21:00"

# PARCIALES
SCALPER_PARTIAL_CLOSE_ENABLED = True
SCALPER_PARTIAL_CLOSE_PCT = 0.50
SCALPER_PARTIAL_BE_COMMISSION_PADDING_PTS = 2.0

# PARCIAL POR CLASE DE ACTIVO (v2.6.2): juez fiel 20d confirmó que el cierre parcial a 1R
# recorta el ganador medio de forma desigual por clase — METAL (XAUUSD) mejora mucho al
# quitarlo (avg_rr 1.46→2.34, expectancy igual) porque su TP técnico corre más lejos que 1R;
# FOREX es neutro (EURUSD 1.55→1.59); CRYPTO empeora (BTCUSD 0.70→1.22 en R:R pero el win
# rate cae de 43%→31%, la expectancy ya negativa se hunde más). Clases listadas aquí
# desactivan el parcial; el resto sigue con SCALPER_PARTIAL_CLOSE_ENABLED.
SCALPER_PARTIAL_CLOSE_DISABLED_CLASSES = {"METAL"}

# COMISIÓN DEL BROKER — CALIBRADA CON DATOS REALES de la cuenta (2026-07-03), divisa EUR.
# El motor fiel (FaithfulScalpingEngine) la descuenta de cada trade convertida a R, ADEMÁS
# del spread. Antes se ignoraba → el juez era optimista justo en la magnitud del edge.
# Dos modelos, porque el bróker cobra distinto según clase (verificado en el historial):
#   · "per_lot": comisión FIJA por lote round-turn (forex, metales, acciones).
#   · "pct_notional": PORCENTAJE del nocional round-turn (cripto). Como en cripto el SL está
#     cerca en % del precio, esto equivale a ~0.39R por trade → domina cualquier edge.
# Medidos: BTCUSD 0.08→2.80€ y 0.06→2.11€ (~0.065% nocional) · ETHUSD 0.28→2.72€ (~0.065%)
#   · EUR/GBPUSD ~4.3€/lote · XAUUSD 0.02→0.11€ (~5.5€/lote) · US500 0€ · AAPL ~0€/acción.
COMMISSION_SPEC = {
    "FOREX":     {"per_lot": 4.3},
    "METAL":     {"per_lot": 5.5},
    "COMMODITY": {"per_lot": 5.5},
    "CRYPTO":    {"pct_notional": 0.00065},   # ~0.065% del nocional, round-turn
    "INDEX":     {"per_lot": 0.0},            # el bróker no cobra comisión en índices
    "EQUITIES":  {"per_lot": 0.011},          # ~0.011€/acción (volumen = nº de acciones)
}

# COMMISSION GUARD (v2.6.2): con SL ceñido, la comisión pct_notional de cripto puede comerse
# la mayoría del riesgo por trade. Medido con el juez fiel (20d, sl_mult shippeado 1.6):
# BTCUSD avg 0.43R (edge bruto sano, +36.5R sin comisión → -48.4R con ella) y ETHUSD avg 0.32R
# (edge bruto YA negativo, -37.3R, sin edge independiente de la comisión). Bloquea la entrada si
# la comisión proyectada del trade supera este umbral de R. Ensanchar sl_mult NO sirve de
# palanca aquí: el SL real lo fija casi siempre el swing estructural M5, no el ATR fallback.
CRYPTO_MAX_COMMISSION_R = 0.25

# Estrategias activas globalmente
ENABLED_STRATEGIES = [
    "PST-RangeBreaker",
    "PST-PrecisionScalping",
]

# CUBETAS DE CAPITAL POR TIPO DE ESTRATEGIA
# Porcentaje del balance total asignado a cada categoría.
# La suma debe ser <= 100. El resto queda como reserva.
# NOTA: PST-AlphaTrend (categoría TREND, H1, horizonte multi-hora) fue retirada:
# FTMO no permite posiciones abiertas con el mercado cerrado, y su horizonte
# no encajaba con el cierre forzado de fin de día. Capital reasignado a RANGE/SCALPING.
CAPITAL_BUCKETS = {
    "RANGE":   50.0,   # RangeBreaker — operativa en rango
    "SCALPING": 50.0,  # PrecisionScalping — alta frecuencia, menor riesgo unitario
}
# Rebalanceo automático: si el rendimiento de una cubeta supera este umbral
# respecto a las demás, se redistribuyen los pesos (revisión mensual)
BUCKET_REBALANCE_THRESHOLD_PCT = 15.0
