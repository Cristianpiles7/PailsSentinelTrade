"""
Tope de duración esperada para el TP — compartido por las estrategias nuevas.

No es un cierre por reloj (el SL/TP siguen siendo lo único que cierra la
operación, nunca un timeout): estima la velocidad de movimiento reciente del
precio (ATR del timeframe activo / minutos de ese timeframe = distancia típica
por minuto) y, si el TP tal como se calculó tardaría mucho más de
MAX_DURATION_MIN en alcanzarse a ese ritmo, lo acerca — sin bajar nunca de un
R:R mínimo (si acercarlo del todo rompe el R:R, se prioriza el R:R mínimo antes
que el tope de duración). Así las operaciones son "medianamente rápidas" por
defecto sin arriesgar cerrar una ganadora a la fuerza.

BUG FIX (2026-08-13, ver Tools/pst_new_strategy_trace.py): cuando AdaptiveTFSelector
elige un timeframe rápido (M2/M3) para el SL pero la TP referencia una estructura de
escala mayor (borde de canal H1 en MomentumPulse), el techo de duración NO frenaba
nada: extrapolar la velocidad de una vela de 2 min a max_duration_min (120 min por
defecto) da un techo enorme (ej. ATR_M2=36 / 2min * 120min = 2160 puntos) que nunca
se activa. Resultado real observado: SL de 16.5 puntos (M2) con TP a 524 puntos (H1)
= R:R 31:1 en un solo trade que dominó todo el backtest de 20d. Se añade un TOPE DE
R:R MÁXIMO (independiente del tope de duración) que sí frena este caso.
"""
TF_MINUTES = {"m1": 1, "m2": 2, "m3": 3, "m5": 5, "m15": 15, "m30": 30, "h1": 60, "h4": 240}

MAX_DURATION_MIN_DEFAULT = 120  # ~2h
MIN_RR_FLOOR_DEFAULT = 1.5
MAX_RR_CEILING_DEFAULT = 6.0  # ninguna TP estructural puede superar 6R del SL


def tf_to_minutes(tf: str) -> int:
    return TF_MINUTES.get(tf, 5)


def cap_tp_by_duration(price: float, direction: int, sl_dist: float, tp_price: float,
                        atr_val: float, active_tf: str,
                        max_duration_min: float = MAX_DURATION_MIN_DEFAULT,
                        min_rr_floor: float = MIN_RR_FLOOR_DEFAULT,
                        max_rr_ceiling: float = MAX_RR_CEILING_DEFAULT) -> float:
    if tp_price <= 0 or sl_dist <= 0 or atr_val <= 0:
        return tp_price

    tf_minutes = tf_to_minutes(active_tf)
    if tf_minutes <= 0:
        return tp_price

    price_velocity = atr_val / tf_minutes  # distancia típica por minuto, a este TF
    if price_velocity <= 0:
        return tp_price

    max_tp_dist = price_velocity * max_duration_min
    min_tp_dist = sl_dist * min_rr_floor
    max_rr_dist = sl_dist * max_rr_ceiling

    # El tope de duración puede quedar sin efecto (fast TF extrapolado a 2h da un
    # techo enorme) — el tope de R:R es la red de seguridad real contra ese caso:
    # nunca deja pasar una TP que implique más de max_rr_ceiling veces el SL, sin
    # importar de qué estructura/timeframe venga.
    target_tp_dist = min(max_tp_dist, max_rr_dist)
    target_tp_dist = max(target_tp_dist, min_tp_dist)  # el suelo de R:R manda si choca

    current_tp_dist = abs(tp_price - price)
    if current_tp_dist <= target_tp_dist:
        return tp_price
    return price + direction * target_tp_dist
