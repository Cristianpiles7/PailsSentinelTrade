import MetaTrader5 as mt5
import sqlite3
import os
import logging
from datetime import datetime, timedelta

# Configuración de Logs
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger("PST-DataSync")

DB_PATH = "PST_Core/data/pst_trading.db"

# Mapeo de fragmentos de Comentarios de MT5 a Nombres Oficiales
MAPPING = {
    "Mean-Rev": "PST-Mean-Reversion",
    "EMA-Flow": "PST-EMA-Flow",
    "Scalper-Pro": "PST-Scalper-Pro",
    "Channel-Master": "PST-Channel-Master"
}

def sync_data():
    if not mt5.initialize():
        logger.error("❌ MT5 Init Failed")
        return

    try:
        # 1. Obtener todos los deals de los últimos 15 días para asegurar cobertura
        from_date = datetime.now() - timedelta(days=15)
        deals = mt5.history_deals_get(from_date, datetime.now())
        
        if deals is None:
            logger.error("❌ No se pudieron recuperar deals de MT5")
            return

        # 2. Construir mapa de Ticket a Estrategia REAL
        ticket_to_strategy = {}
        for d in deals:
            if not d.comment: continue
            
            # Buscamos patrones conocidos en el comentario
            strategy_found = None
            if "HEDGE" in d.comment:
                strategy_found = "Hedge-Recovery"
            else:
                for key, val in MAPPING.items():
                    if key in d.comment:
                        strategy_found = val
                        break
            
            if strategy_found:
                # El position_id en MT5 es el ticket en nuestra DB
                ticket_to_strategy[d.position_id] = strategy_found

        if not ticket_to_strategy:
            logger.info("ℹ️ No se encontraron comentarios PST útiles en el historial de MT5.")
            return

        logger.info(f"📊 Mapa de estrategias construido con {len(ticket_to_strategy)} entradas únicas.")

        # 3. Actualizar la Base de Datos
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        updated_total = 0
        for ticket, strategy in ticket_to_strategy.items():
            # Actualizamos cualquier trade que coincida con este ticket
            cursor.execute("UPDATE trades SET strategy_name = ? WHERE ticket = ?", (strategy, ticket))
            if cursor.rowcount > 0:
                logger.info(f"✅ Ticket {ticket} -> {strategy}")
                updated_total += cursor.rowcount

        conn.commit()
        conn.close()
        logger.info(f"✨ Limpieza completada. {updated_total} trades actualizados con su estrategia real.")

    except Exception as e:
        logger.error(f"❌ Error en sync: {e}")
    finally:
        mt5.shutdown()

if __name__ == "__main__":
    sync_data()
