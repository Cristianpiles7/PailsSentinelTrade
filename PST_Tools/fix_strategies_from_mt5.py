import MetaTrader5 as mt5
import sqlite3
import os
import logging
from datetime import datetime, timedelta

# Configuración de Logs
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger("PST-DataFix")

DB_PATH = "PST_Core/data/pst_trading.db"

# Mapeo de Comentarios de MT5 a Nombres de Estrategia del Bot
STRATEGY_MAP = {
    "PST_PST-Mean-Rev": "PST-Mean-Reversion",
    "PST_PST-EMA-Flow": "PST-EMA-Flow",
    "PST_PST-Scalper-Pro": "PST-Scalper-Pro",
    "PST_PST-Channel-Master": "PST-Channel-Master"
}

def fix_strategies():
    if not os.path.exists(DB_PATH):
        logger.error(f"❌ Base de datos no encontrada en {DB_PATH}")
        return

    # 1. Inicializar MT5
    if not mt5.initialize():
        logger.error("❌ Fallo al inicializar MetaTrader 5")
        return

    try:
        # 2. Conectar a DB local
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # 3. Buscar trades marcados con nombres genéricos o antiguos
        cursor.execute("""
            SELECT id, ticket, symbol, strategy_name 
            FROM trades 
            WHERE strategy_name = 'AUTO_SYNC' 
               OR strategy_name = 'Manual/Imported' 
               OR strategy_name LIKE 'Ext:%'
        """)
        trades = cursor.fetchall()
        
        if not trades:
            logger.info("✅ No se encontraron trades con 'AUTO_SYNC'. Todo limpio.")
            return

        logger.info(f"🔍 Encontrados {len(trades)} trades para corregir...")

        updated_count = 0
        for t in trades:
            db_id = t['id']
            ticket = t['ticket']
            
            if not ticket or ticket == 0:
                logger.warning(f"⚠️ Trade ID {db_id} ({t['symbol']}) no tiene ticket. Saltando.")
                continue

            # 4. Buscar trato (Deal) en MT5 para obtener el comentario
            # Buscamos en el historial (ventana amplia por si acaso)
            from_date = datetime.now() - timedelta(days=90)
            deals = mt5.history_deals_get(from_date, datetime.now(), position=ticket)
            
            if deals is None or len(deals) == 0:
                logger.warning(f"⚠️ No se encontró historial en MT5 para el ticket {ticket}. Saltando.")
                continue

            # El comentario suele estar en el deal de entrada
            comment = ""
            for d in deals:
                # Priorizar comentarios que empiecen por PST o HEDGE
                if d.comment and (d.comment.startswith("PST") or d.comment.startswith("HEDGE")):
                    comment = d.comment
                    break
            
            # Si no encontramos uno con patrón, pillar el primero que tenga algo
            if not comment:
                for d in deals:
                    if d.comment:
                        comment = d.comment
                        break
            
            if not comment or comment == "Initial account balance":
                # Intentar buscar el comentario del deal de entrada específicamente
                # (A veces el primer deal de una cuenta es balance y tiene el mismo ticket? Raro)
                logger.info(f"ℹ️ Ticket {ticket} tiene comentarios genéricos. Marcando como 'Manual'.")
                new_name = "Manual/Imported"
            else:
                # 5. Mapear comentario a nombre real
                new_name = STRATEGY_MAP.get(comment, f"Ext: {comment}")
                if comment.startswith("HEDGE"):
                    new_name = "Hedge-Recovery"
                elif "EMA-Flow" in comment:
                    new_name = "PST-EMA-Flow"
                elif "Mean-Rev" in comment:
                    new_name = "PST-Mean-Reversion"

            # 6. Actualizar DB
            cursor.execute("UPDATE trades SET strategy_name = ? WHERE id = ?", (new_name, db_id))
            logger.info(f"✅ Corregido: Ticket {ticket} | '{comment}' -> '{new_name}'")
            updated_count += 1

        conn.commit()
        conn.close()
        logger.info(f"✨ Proceso finalizado. {updated_count} registros actualizados.")

    except Exception as e:
        logger.error(f"❌ Error durante la corrección: {e}")
    finally:
        mt5.shutdown()

if __name__ == "__main__":
    fix_strategies()
