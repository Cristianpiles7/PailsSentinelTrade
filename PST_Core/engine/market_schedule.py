import MetaTrader5 as mt5
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import logging

logger = logging.getLogger("PST_Market_Schedule")

# Definir márgenes de seguridad para el cierre automático (en minutos)
CLOSE_MARGIN_MINUTES = 15

_NY_TZ = ZoneInfo("America/New_York")

def _us_stock_close_utc_minutes(now_utc: datetime) -> int:
    """
    Calcula la hora de cierre de NYSE (16:00 hora de Nueva York) en minutos UTC,
    ajustando automáticamente por horario de verano (EDT/EST).
    """
    ny_close = now_utc.astimezone(_NY_TZ).replace(hour=16, minute=0, second=0, microsecond=0)
    ny_close_utc = ny_close.astimezone(timezone.utc)
    return ny_close_utc.hour * 60 + ny_close_utc.minute

def is_closing_soon(symbol: str) -> tuple[bool, str]:
    """
    Evalúa si un activo está a las puertas del cierre de su sesión de mercado.
    Retorna (True, razón) si se debe forzar el cierre de todas las posiciones para este activo.
    """
    info = mt5.symbol_info(symbol)
    if not info:
        return False, "Symbol not found"
        
    path = info.path.upper()
    
    # === 1. CRYPTO ===
    # Las criptomonedas no cierran, cotizan 24/7.
    if "CRYPTO" in path or "BITCOIN" in path or "ETHEREUM" in path:
        return False, "Crypto 24/7"
        
    # Obtener el tiempo UTC actual
    now_utc = datetime.now(timezone.utc)
    day_of_week = now_utc.weekday() # 0 = Lunes, 6 = Domingo
    hour = now_utc.hour
    minute = now_utc.minute
    
    # Tiempo actual en minutos desde medianoche UTC
    current_minutes = (hour * 60) + minute
    
    # === 2. ACCIONES (US STOCKS) ===
    # Generalmente el path contiene "STOCKS", "US EQUITIES" o similares, o no es FOREX/CASH CFD
    # Por seguridad, si es US Stock, cierran a las 21:00 UTC (16:00 EST).
    # Disparamos a las 20:45 UTC (15:45 EST) -> 20*60 + 45 = 1245 minutos.
    is_stock = "STOCK" in path or "EQUITY" in path or "EQUITIES" in path
    if is_stock:
        # Lunes a Viernes
        if day_of_week <= 4:
            closing_time = _us_stock_close_utc_minutes(now_utc) # 16:00 NY time, ajustado a EST/EDT
            action_time = closing_time - CLOSE_MARGIN_MINUTES
            
            if current_minutes >= action_time and current_minutes < closing_time:
                return True, f"US Stock Daily Close ({CLOSE_MARGIN_MINUTES}m warning)"
        return False, "US Stock Open"
        
    # === 3. FOREX Y CFD (ÍNDICES) ===
    # Forex, Metals, Cash CFDs (US30, GER40, etc.)
    # Estos mercados operan casi 24/5. 
    # El cierre crítico es el VIERNES por el fin de semana.
    # Cierran los viernes a las 21:55 UTC aprox (16:55 EST / MT5 Server time 23:55).
    # Lo estandarizamos a 21:55 UTC. Disparamos a las 21:40 UTC.
    is_forex_cfd = "FOREX" in path or "CASH CFD" in path or "METALS" in path or "INDEX" in path
    if True: # Default fallback for any remaining standard asset
        if day_of_week == 4: # Viernes
            closing_time = (21 * 60) + 55 # 21:55 UTC
            action_time = closing_time - CLOSE_MARGIN_MINUTES
            
            if current_minutes >= action_time and current_minutes <= closing_time + 60:
                return True, f"Weekend Close ({CLOSE_MARGIN_MINUTES}m warning)"
                
    return False, "Market Open/Normal"
