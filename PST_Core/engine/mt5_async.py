import asyncio
import MetaTrader5 as mt5
import pandas as pd
from typing import Optional, List

async def init_mt5_async():
    """Inicializa MT5 de forma asíncrona."""
    return await asyncio.to_thread(mt5.initialize)

async def shutdown_mt5_async():
    """Cierra MT5 de forma asíncrona."""
    await asyncio.to_thread(mt5.shutdown)

async def sym_info_async(symbol: str):
    """Obtiene info de símbolo sin bloquear el loop."""
    return await asyncio.to_thread(mt5.symbol_info, symbol)

async def fetch_rates_async(symbol: str, timeframe: int, count: int) -> Optional[pd.DataFrame]:
    """Descarga de datos histórica no bloqueante con mapeo de timeframe."""
    # Asegurar que el símbolo esté seleccionado
    await asyncio.to_thread(mt5.symbol_select, symbol, True)
    
    # Mapeo de entero a constante de MT5
    tf_map = {
        1: mt5.TIMEFRAME_M1,
        3: mt5.TIMEFRAME_M3,
        5: mt5.TIMEFRAME_M5,
        15: mt5.TIMEFRAME_M15,
        30: mt5.TIMEFRAME_M30,
        60: mt5.TIMEFRAME_H1,
        16385: mt5.TIMEFRAME_H1, # Por si ya viene como constante
        240: mt5.TIMEFRAME_H4,
        1440: mt5.TIMEFRAME_D1
    }
    tf_mt5 = tf_map.get(timeframe, mt5.TIMEFRAME_M5)

    rates = await asyncio.to_thread(mt5.copy_rates_from_pos, symbol, tf_mt5, 0, count)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df['time_raw'] = df['time']
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    return df

async def get_positions_async(symbol: str = None):
    """Obtiene posiciones abiertas de forma asíncrona."""
    return await asyncio.to_thread(mt5.positions_get, symbol=symbol) if symbol else await asyncio.to_thread(mt5.positions_get)

async def send_order_async(request: dict):
    """Envía una orden al mercado de forma asíncrona con log de error."""
    res = await asyncio.to_thread(mt5.order_send, request)
    if res is None:
        last_err = mt5.last_error()
        import logging
        logging.getLogger("MT5-Async").error(f"❌ MT5 order_send retornó None. Last Error: {last_err}")
    return res

async def close_position_async(ticket: int):
    """Cierra una posición abierta a precio de mercado usando su ticket."""
    position = await asyncio.to_thread(mt5.positions_get, ticket=ticket)
    if not position:
        return None
    pos = position[0]
    symbol = pos.symbol
    volume = pos.volume
    pos_type = pos.type  # 0=BUY, 1=SELL

    # Para cerrar: orden inversa a la posición
    close_type = mt5.ORDER_TYPE_SELL if pos_type == 0 else mt5.ORDER_TYPE_BUY
    info = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
    price = info.bid if pos_type == 0 else info.ask  # vender al bid, comprar al ask

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       symbol,
        "volume":       volume,
        "type":         close_type,
        "position":     ticket,
        "price":        price,
        "magic":        666,
        "comment":      "PST_CLOSE_MANUAL",
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    return await asyncio.to_thread(mt5.order_send, request)


async def modify_position_async(ticket: int, sl: float, tp: float):
    """Modifica el SL/TP de una posición de forma asíncrona."""
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "sl": sl,
        "tp": tp
    }
    return await asyncio.to_thread(mt5.order_send, request)

async def get_history_deals_async(days=1):
    """Obtiene deals del historial para sincronizar cierres de forma robusta."""
    import time
    # Importante: Algunos brokers tienen el reloj adelantado. Usamos +24h de margen.
    end_time = int(time.time()) + 86400 
    start_time = end_time - (3600 * 24 * days)
    return await asyncio.to_thread(mt5.history_deals_get, start_time, end_time)

async def get_mtf_data_async(symbol: str, include_m1: bool = False):
    """Obtiene datos Multi-Timeframe (M1, M5, M15, H1, H4) en paralelo."""
    # M1: Scalping / Volatilidad (User Request)
    # M5: Disparo / Tendencia Corta
    # M15: Táctica / Estructura
    # H1: Tendencia Principal
    # H4: Macro / Filtro Mayor
    tasks = []
    if include_m1:
        tasks.append(fetch_rates_async(symbol, 1, 200)) # M1 (Aumentado a 200 para Scalper Pro)
    else:
        # Placeholder for index consistency
        tasks.append(asyncio.sleep(0, result=None))
    
    tasks += [
        fetch_rates_async(symbol, 3, 100),   # M3 (NEW)
        fetch_rates_async(symbol, 5, 200),   # M5
        fetch_rates_async(symbol, 15, 200),  # M15
        fetch_rates_async(symbol, 30, 200),  # M30 (NEW)
        fetch_rates_async(symbol, 60, 500),  # H1
        fetch_rates_async(symbol, 240, 500)  # H4
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Manejo de errores seguro
    clean_results = []
    for r in results:
        if isinstance(r, Exception) or r is None:
            clean_results.append(None)
        else:
            clean_results.append(r)
            
    return {
        "m1": clean_results[0],
        "m3": clean_results[1],
        "m5": clean_results[2],
        "m15": clean_results[3],
        "m30": clean_results[4],
        "h1": clean_results[5],
        "h4": clean_results[6]
    }

