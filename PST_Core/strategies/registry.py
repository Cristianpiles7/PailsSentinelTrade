"""
STRATEGY_REGISTRY — fuente única de verdad para conectar una estrategia al sistema
en vivo. Antes de este archivo, añadir una estrategia tocaba 4+ sitios hardcodeados
por separado (orchestrator.py, config.py, database.py, PST_API/main.py). El refactor
es MECÁNICO: preserva el comportamiento exacto de las estrategias existentes, solo
cambia de dónde sale la lista.

PST-AlphaTrend queda fuera a propósito (retirada, no vive en ninguna lista activa
desde antes de este refactor — ver nota en PST_Core/config.py).

PST-BreakoutScalping y PST-VolExpansion (Corriente C, 2026-08-12): registradas para
que el sistema las reconozca (backtest/lab/Matrix Editor) pero DELIBERADAMENTE fuera
de ENABLED_STRATEGIES (config.py) — diseño y registro únicamente, pendientes del
mismo pipeline de validación (smoke -> motor fiel -> baseline IS/OOS) que exige el
proyecto antes de arriesgar capital real. No tocar ENABLED_STRATEGIES por ellas sin
completar esa validación.
"""
from .pst_range_breaker import PSTRangeBreaker
from .pst_precision_scalping import PSTPrecisionScalping
from .pst_momentum_pulse import PSTMomentumPulse
from .pst_divergence_reversion import PSTDivergenceReversion
from .pst_breakout_scalping import PSTBreakoutScalping
from .pst_vol_expansion import PSTVolExpansion

STRATEGY_REGISTRY = {
    "PST-RangeBreaker": {
        "class": PSTRangeBreaker, "category": "RANGE", "capital_bucket": "RANGE",
        "backtest_timeframes": ["m15", "h1"],
    },
    "PST-PrecisionScalping": {
        "class": PSTPrecisionScalping, "category": "SCALPING", "capital_bucket": "SCALPING",
        "backtest_timeframes": ["m1", "m5"],
    },
    "PST-MomentumPulse": {
        "class": PSTMomentumPulse, "category": "TREND", "capital_bucket": "TREND",
        "backtest_timeframes": ["m2", "m3", "m5", "m15", "m30", "h1"],
    },
    "PST-DivergenceReversion": {
        "class": PSTDivergenceReversion, "category": "RANGE", "capital_bucket": "RANGE",
        "backtest_timeframes": ["m3", "m5", "m15", "m30", "h1"],
    },
    "PST-BreakoutScalping": {
        "class": PSTBreakoutScalping, "category": "SCALPING", "capital_bucket": "SCALPING",
        "backtest_timeframes": ["m2", "m3", "m5", "h1"],
    },
    "PST-VolExpansion": {
        "class": PSTVolExpansion, "category": "TREND", "capital_bucket": "TREND",
        "backtest_timeframes": ["m5", "m15", "m30", "h1"],
    },
}


def get_class(name: str):
    entry = STRATEGY_REGISTRY.get(name)
    return entry["class"] if entry else None


def instantiate_all() -> dict:
    """{STRATEGY_NAME: instancia} — una instancia nueva por llamada (SymbolTask crea
    una por símbolo, igual que el patrón hardcodeado anterior)."""
    return {name: entry["class"]() for name, entry in STRATEGY_REGISTRY.items()}


def category_map() -> dict:
    return {name: entry["category"] for name, entry in STRATEGY_REGISTRY.items()}


def backtest_timeframes_map() -> dict:
    return {name: entry["backtest_timeframes"] for name, entry in STRATEGY_REGISTRY.items()}
