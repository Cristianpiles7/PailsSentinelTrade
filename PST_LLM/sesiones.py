"""
Paradas recurrentes del mercado, sacadas del propio historial de MT5 (sin horarios escritos a mano).

Una parada es un hueco sin velas de mas de PARADA_MIN minutos. Solo cuentan las que se repiten en varias
semanas a la misma hora: el fin de semana y el corte diario de la sesion. Un festivo suelto no se repite,
asi que no se detecta aqui (ver pendientes).

Con las paradas largas (mas de PARADA_LARGA_S) la regla es:
  - Si hay posicion y faltan menos de 'margen' para que empiece una: se cierra antes, sin esperar al modelo.
  - Si no hay posicion y falta menos de 'margen' o ya estamos dentro: no se llama al modelo ni se abre nada.
Los cortes diarios de 2 h o menos no hacen nada.
"""
import time
from datetime import datetime, timedelta, timezone

DIA = 86400
SEMANA = 7 * DIA
PARADA_MIN = 120                  # hueco sin velas que cuenta como parada (minutos)
PARADA_LARGA_S = 2 * 3600         # mas de esto = parada larga: se actua antes
DIARIA_MAX_S = 12 * 3600          # una pausa que se repite cada dia y dura menos de esto es un corte diario
SEMANAS = 5                       # historial que se mira
MIN_SEMANAS = 2                   # una parada debe repetirse en al menos esta cantidad de semanas
TTL_CACHE_S = 6 * 3600            # el horario cambia poco: se recalcula cada 6 h

_cache = {}                       # simbolo -> (instante del calculo, paradas)


def segundos_de_semana(ahora):
    """Segundos desde el domingo 00:00 de la semana de 'ahora' (datetime, hora del servidor)."""
    domingo = (ahora.weekday() + 1) % 7                    # Python: lunes = 0 ... domingo = 6
    return domingo * DIA + ahora.hour * 3600 + ahora.minute * 60 + ahora.second


def paradas_desde_tiempos(tiempos, parada_min=PARADA_MIN, min_semanas=MIN_SEMANAS):
    """
    Paradas recurrentes [(inicio, fin)] en segundos de semana, a partir de los instantes (epoch) de las velas.
    Pura: no toca MT5, asi que se puede probar con datos inventados.
    """
    tiempos = sorted(int(t) for t in tiempos)
    huecos = []                                            # (semana, inicio_en_semana, duracion)
    for a, b in zip(tiempos, tiempos[1:]):
        if b - a > parada_min * 60:
            ini = datetime.fromtimestamp(a + 300, tz=timezone.utc).replace(tzinfo=None)
            domingo = (ini - timedelta(days=(ini.weekday() + 1) % 7)).date()
            huecos.append((domingo, segundos_de_semana(ini), b - a - 300))
    # Cortes diarios: la misma hora del dia en 3 o mas dias de una misma semana, y cortos.
    # Indices, oro, etc. paran cada noche: eso no es una parada larga aunque pase de 2 h.
    por_hora_dia = {}
    for semana, ini_s, dur in huecos:
        if dur < DIARIA_MAX_S:
            por_hora_dia.setdefault(round((ini_s % DIA) / 1800), {}).setdefault(semana, set()).add(ini_s // DIA)
    diarias = {h for h, semanas in por_hora_dia.items() if max(len(d) for d in semanas.values()) >= 3}
    # Agrupar por hora de inicio (ventanas de 30 min) y contar en cuantas semanas aparece cada una.
    grupos = {}
    for semana, ini_s, dur in huecos:
        grupos.setdefault(round(ini_s / 1800), []).append((semana, ini_s, dur))
    paradas = []
    for filas in grupos.values():
        semanas = {f[0] for f in filas}
        if len(semanas) < min_semanas:
            continue
        ini_s = sorted(f[1] for f in filas)[len(filas) // 2]
        dur = sorted(f[2] for f in filas)[len(filas) // 2]
        if dur < DIARIA_MAX_S and round((ini_s % DIA) / 1800) in diarias:
            continue                                       # corte diario: no es una parada que cierre
        paradas.append((ini_s, ini_s + dur))
    return sorted(paradas)


def a_naive(epoch):
    """Epoch en hora del servidor del broker -> datetime naive (la misma convencion que usan las velas de MT5)."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(tzinfo=None)


def paradas(mt5, symbol, t_ref):
    """
    Paradas recurrentes del simbolo leidas de MT5. t_ref = tick.time (hora del broker).
    Cacheadas unas horas. Lista vacia si no hay datos suficientes.
    """
    ahora_ts = time.time()
    if symbol in _cache and ahora_ts - _cache[symbol][0] < TTL_CACHE_S:
        return _cache[symbol][1]
    hasta = int(t_ref) + 3600
    desde = hasta - SEMANAS * SEMANA
    velas = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, a_naive(desde), a_naive(hasta))
    if velas is None or len(velas) < 100:
        return []
    res = paradas_desde_tiempos([v["time"] for v in velas])
    _cache[symbol] = (ahora_ts, res)
    return res


def evaluar(paradas_semana, ahora, margen_s, parada_s=PARADA_LARGA_S):
    """
    Decide si hay que cerrar ya. Pura, para probarla sin MT5.
    Devuelve {"cerrar": bool, "motivo": str, "en_s": segundos hasta la parada (0 si ya esta dentro)}.
    """
    ahora_s = segundos_de_semana(ahora)
    mejor = None
    for ini, fin in paradas_semana:
        dur = fin - ini
        if dur <= parada_s:
            continue
        if (ahora_s - ini) % SEMANA < dur:                 # ya dentro de la parada
            return {"cerrar": True, "motivo": f"parada de {dur / 3600:.1f} h en curso", "en_s": 0}
        en_s = (ini - ahora_s) % SEMANA
        if mejor is None or en_s < mejor:
            mejor = en_s
    if mejor is not None and mejor <= margen_s:
        return {"cerrar": True, "motivo": f"parada larga en {mejor // 60} min", "en_s": mejor}
    return {"cerrar": False, "motivo": "sin parada larga inminente", "en_s": mejor}


def estado(mt5, symbol, t_ref, margen_s):
    """Lo que usa el agente en cada vuelta. t_ref = tick.time. Sin datos de horario, no decide nada (cerrar=False)."""
    ps = paradas(mt5, symbol, t_ref)
    if not ps:
        return {"cerrar": False, "motivo": "sin datos de horario", "en_s": None}
    return evaluar(ps, a_naive(t_ref), margen_s)
