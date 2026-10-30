"""
Salidas por reglas en vivo (plan v3, fase 3, regla C). Las mismas que simulo simulacion_salidas.py:

  * a +1 R el stop pasa a la entrada (break-even);
  * a +1,5 R se cierra el 50 % de la posicion y el resto queda con el stop en +0,5 R;
  * el objetivo (TP) original no se toca.

R = distancia entrada-stop de la ORDEN DE APERTURA (el stop se mueve despues, asi que se lee del historial).
Solo mueve el stop a favor (nunca lo empeora) y es idempotente: se puede llamar cada pocos segundos.
Si el precio ya esta por debajo del stop que tocaria (se dio la vuelta justo tras el parcial) cierra el resto a mercado,
que es lo que habria hecho el stop. Solo en cuenta DEMO. Lo llama el revisor en cada vuelta (flag `salidas_reglas`).
"""
import math
from datetime import datetime

BE_R = 1.0
PARCIAL_R = 1.5
RESTO_STOP_R = 0.5
FRACCION = 0.5
COMENTARIO = "LLM_REGLA"
_INICIAL = {}          # ticket -> (entrada, distancia al stop, volumen inicial)


def _inicial(mt5, p):
    if p.ticket in _INICIAL:
        return _INICIAL[p.ticket]
    ent = [d for d in (mt5.history_deals_get(position=p.ticket) or []) if d.entry == 0]
    sl = next((o.sl for o in (mt5.history_orders_get(position=p.ticket) or []) if o.sl), None)
    if not ent or not sl or abs(ent[0].price - sl) <= 0:
        return None            # sin historial de la apertura no se sabe cuanto era 1 R: no se toca
    _INICIAL[p.ticket] = (ent[0].price, abs(ent[0].price - sl), ent[0].volume)
    return _INICIAL[p.ticket]


def _filling(mt5, info):
    if info.filling_mode & 2:
        return mt5.ORDER_FILLING_IOC
    if info.filling_mode & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def _cerrar(mt5, p, info, tick, volumen):
    buy = p.type == 0
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": volumen, "position": p.ticket,
           "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY, "price": tick.bid if buy else tick.ask,
           "deviation": 30, "magic": p.magic, "comment": COMENTARIO, "type_filling": _filling(mt5, info)}
    r = mt5.order_send(req)
    return r is not None and r.retcode == mt5.TRADE_RETCODE_DONE, getattr(r, "comment", "")


def gestionar(mt5, magics, log=print):
    """Aplica la regla a las posiciones abiertas de `magics`. Devuelve la lista de acciones hechas (para tests y log)."""
    cuenta = mt5.account_info()
    if cuenta is None or cuenta.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        return []
    hechas = []
    for p in mt5.positions_get() or []:
        if p.magic not in magics:
            continue
        ini = _inicial(mt5, p)
        info, tick = mt5.symbol_info(p.symbol), mt5.symbol_info_tick(p.symbol)
        if ini is None or info is None or tick is None:
            continue
        entrada, dist, vol0 = ini
        buy = p.type == 0
        signo = 1 if buy else -1
        precio = tick.bid if buy else tick.ask               # al que se cerraria
        ganado = signo * (precio - entrada) / dist
        tp_r = abs(p.tp - entrada) / dist if p.tp else math.inf
        parcial = p.volume < vol0 * 0.99
        if ganado >= PARCIAL_R and not parcial and tp_r > PARCIAL_R:
            paso = info.volume_step
            vol = math.floor(p.volume * FRACCION / paso + 1e-9) * paso
            if vol >= info.volume_min and p.volume - vol >= info.volume_min:
                ok, txt = _cerrar(mt5, p, info, tick, round(vol, 8))
                hechas.append(f"{p.symbol} #{p.ticket}: parcial {vol} a {ganado:.2f} R · {'ok' if ok else 'FALLO ' + txt}")
                parcial = parcial or ok
        nivel_r = RESTO_STOP_R if parcial else (0.0 if ganado >= BE_R else None)
        if nivel_r is None:
            continue
        nuevo = entrada + signo * nivel_r * dist
        mejora = (nuevo - p.sl) * signo > info.point if p.sl else True
        if not mejora:
            continue
        if (precio - nuevo) * signo <= 0:                    # ya cruzo ese nivel: el stop habria saltado
            ok, txt = _cerrar(mt5, p, info, tick, p.volume)
            hechas.append(f"{p.symbol} #{p.ticket}: cierre del resto (precio bajo +{nivel_r:g} R) · {'ok' if ok else 'FALLO ' + txt}")
            continue
        if (precio - nuevo) * signo < info.trade_stops_level * info.point:
            continue                                         # demasiado cerca del precio para el broker: se reintenta
        r = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "symbol": p.symbol, "position": p.ticket,
                            "sl": round(nuevo, info.digits), "tp": p.tp, "magic": p.magic})
        ok = r is not None and r.retcode == mt5.TRADE_RETCODE_DONE
        hechas.append(f"{p.symbol} #{p.ticket}: stop a {nivel_r:+g} R ({round(nuevo, info.digits)}) con {ganado:.2f} R · "
                      f"{'ok' if ok else 'FALLO ' + str(getattr(r, 'comment', None))}")
    for h in hechas:
        log(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [salidas] {h}")
    return hechas
