"""
Horas de MT5 en reloj local.

MT5 devuelve las horas (position.time, deal.time, tick.time) como epoch, pero ese epoch codifica la hora
de pared del SERVIDOR del bróker, no la UTC real. Convertirlo con fromtimestamp() directamente mete el
desfase del bróker en las BBDD (time_in ~+3h, time_out ~+2h, y timeouts que disparan tarde).

Regla única: local = fromtimestamp(epoch - desfase_bróker). El desfase se mide contra el reloj del
sistema con un símbolo que cotiza 24/7 (BTCUSD), así se adapta solo al cambio de hora del bróker.
"""
import time
from datetime import datetime, timezone

SIMBOLO_REFERENCIA = "BTCUSD"     # cripto: cotiza fines de semana, su tick nunca está viejo
_CACHE = {"desfase": None, "medido": 0.0}
_TTL_S = 300


def desfase_broker_s():
    """Segundos que el reloj del bróker va por delante de UTC (p. ej. 10800 para UTC+3)."""
    ahora = time.time()
    if _CACHE["desfase"] is not None and ahora - _CACHE["medido"] < _TTL_S:
        return _CACHE["desfase"]
    import MetaTrader5 as mt5
    tick = mt5.symbol_info_tick(SIMBOLO_REFERENCIA)
    if tick is None:
        if _CACHE["desfase"] is not None:
            return _CACHE["desfase"]              # MT5 pasajeramente sin tick: mantener la última medida
        raise RuntimeError(f"sin tick de {SIMBOLO_REFERENCIA} para medir el desfase del bróker")
    bruto = tick.time - ahora                     # tick.time = hora de pared del bróker codificada como epoch
    _CACHE["desfase"] = int(round(bruto / 900) * 900)   # múltiplos de 15 min: quita el jitter del segundo
    _CACHE["medido"] = ahora
    return _CACHE["desfase"]


def local_desde_mt5(epoch_mt5):
    """Hora local naive (como las BBDD del proyecto) a partir de un epoch devuelto por MT5."""
    return datetime.fromtimestamp(epoch_mt5 - desfase_broker_s())


def broker_desde_mt5(epoch_mt5):
    """Hora de pared del bróker naive (la que muestra MT5). Sirve para etiquetar 'broker time'."""
    return datetime.fromtimestamp(epoch_mt5, tz=timezone.utc).replace(tzinfo=None)
