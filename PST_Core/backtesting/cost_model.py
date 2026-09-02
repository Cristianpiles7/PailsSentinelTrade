"""
Modelado de costes reales para el motor de backtest GENÉRICO (PSTBacktestEngine).

Generaliza la comisión de faithful_engine.py::_commission_r (que hoy solo cubre a
PrecisionScalping) y añade coste de spread de salida y, NUEVO, coste de swap/overnight
— los motores fieles actuales lo omiten porque el scalping rara vez mantiene
posiciones de un día para otro, pero una estrategia de momentum/tendencia sí puede
hacerlo. Ataca directamente la brecha histórica juez-vs-vivo documentada en memoria
del proyecto (primeras 24h en vivo: -39€ reales vs +25€ esperados por el juez, al
omitir comisión/swap/spread de salida del modelo).
"""
from ..config import COMMISSION_SPEC, CRYPTO_MAX_COMMISSION_R


def commission_r(asset_class: str, entry_price: float, sl_dist: float, value_per_price: float = None) -> float:
    """Comisión round-turn expresada en múltiplos de R (espejo de
    faithful_engine.py::_commission_r, generalizado para cualquier estrategia).

    · pct_notional (cripto): no necesita tick data — el nocional y el lote se cancelan.
    · per_lot (forex/metal/acción/equities): necesita value_per_price = tick_value/tick_size.
    """
    spec = COMMISSION_SPEC.get(asset_class, {})
    if not spec or sl_dist <= 0:
        return 0.0
    if "pct_notional" in spec:
        pct = float(spec["pct_notional"])
        return pct * entry_price / sl_dist if pct > 0 else 0.0
    per_lot = float(spec.get("per_lot", 0.0))
    if per_lot > 0 and value_per_price:
        return per_lot / (sl_dist * value_per_price)
    return 0.0


def exit_spread_r(spread_dist: float, sl_dist: float) -> float:
    """Coste del medio spread cruzado AL SALIR, en R (el de entrada se modela aparte,
    normalmente ajustando el precio de fill; esto es solo la pata de salida)."""
    if sl_dist <= 0 or spread_dist <= 0:
        return 0.0
    return (spread_dist / 2.0) / sl_dist


def swap_r(direction: int, nights_held: int, swap_long_points: float, swap_short_points: float,
           point: float, sl_dist: float) -> float:
    """Coste (positivo) o crédito (negativo) de swap/overnight, en múltiplos de R.

    Aproximación deliberada: cobra 1x el swap diario por cada noche mantenida — no
    modela el triple de miércoles/viernes que aplican muchos brokers (MT5 lo expone
    como swap_mode/días, pero replicarlo exacto no cambia la conclusión: el punto es
    dejar de omitir el coste por completo, no igualar el céntimo). El valor por lote
    (tick_value/tick_size) se cancela igual que en pct_notional — no hace falta tick data:
        swap_money_por_lote = swap_points · point · value_per_price · noches
        swap_R = swap_money_por_lote / (sl_dist_precio · value_per_price) = swap_points·point·noches / sl_dist
    """
    if nights_held <= 0 or sl_dist <= 0 or point <= 0:
        return 0.0
    swap_points = swap_long_points if direction == 1 else swap_short_points
    swap_price = swap_points * point * nights_held
    return swap_price / sl_dist


def nights_held(entry_time, exit_time) -> int:
    """Número de fronteras de medianoche cruzadas entre entrada y salida (proxy de
    cuántos rollovers de swap se cobraron)."""
    if entry_time is None or exit_time is None:
        return 0
    try:
        return max(0, (exit_time.date() - entry_time.date()).days)
    except Exception:
        return 0


def commission_guard_ok(asset_class: str, entry_price: float, sl_dist: float) -> bool:
    """Espejo del guard de comisión del executor: bloquea la entrada si la comisión
    proyectada (solo aplica a pct_notional / cripto) supera CRYPTO_MAX_COMMISSION_R."""
    spec = COMMISSION_SPEC.get(asset_class, {})
    if spec and "pct_notional" in spec and sl_dist > 0:
        proj = float(spec["pct_notional"]) * entry_price / sl_dist
        return proj <= CRYPTO_MAX_COMMISSION_R
    return True
