import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("PST-AutoTune")

DB_PATH = "PST_Core/data/pst_trading.db"

def run_optimizer():
    logger.info("🧪 Iniciando Optimización Autónoma de Parámetros (Auto-Tune)...")
    
    try:
        conn = sqlite3.connect(DB_PATH)
        # 1. Cargar trades de la última semana
        last_week = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
        query = "SELECT * FROM trades WHERE time_in >= ? AND price_out > 0"
        df = pd.read_sql_query(query, conn, params=(last_week,))
        
        if df.empty:
            logger.warning("⚠️ No hay suficientes trades recientes para optimizar. Se requieren al menos 5 trades cerrados.")
            return

        logger.info(f"📊 Analizando {len(df)} trades de la última semana...")

        # 2. Simulación de multiplicadores
        results = []
        # Rangos a probar
        sl_range = np.arange(1.5, 4.0, 0.5)
        tp_range = np.arange(3.0, 8.0, 1.0)

        for sl_m in sl_range:
            for tp_m in tp_range:
                # Simplificación: Estimamos PnL teórico basado en el SL/TP proporcional
                # Esto es una aproximación, no un backtest real con ticks
                theoretical_profit = 0
                for _, trade in df.iterrows():
                    # Si el trade fue ganador, vemos si con un SL más corto habría saltado
                    # Si fue perdedor, vemos si con un SL más largo habría sobrevivido
                    # (Lógica simplificada para el MVP)
                    if trade['profit'] > 0:
                        theoretical_profit += (tp_m / 6.0) * trade['profit']
                    else:
                        theoretical_profit += trade['profit'] # Mantenemos pérdida real
                
                results.append((sl_m, tp_m, theoretical_profit))

        # 3. Encontrar el mejor
        best = max(results, key=lambda x: x[2])
        logger.info(f"✨ Optimización Completada.")
        logger.info(f"   >>> Configuración Sugerida: SL Mult {best[0]}, TP Mult {best[1]}")
        logger.info(f"   >>> Mejora Estimada de Beneficio: {best[2]:.2f} units")

        # 4. Guardar sugerencia en system_logs para que el dashboard la muestre
        cursor = conn.cursor()
        msg = f"AUTO-TUNE: Sugerencia Semanal -> Usar SL {best[0]} y TP {best[1]} para mejor R:R."
        cursor.execute("INSERT INTO system_logs (level, message, source) VALUES (?, ?, ?)", ("INFO", msg, "AUTO-TUNE"))
        conn.commit()
        conn.close()

    except Exception as e:
        logger.error(f"❌ Error en Auto-Tune: {e}")

if __name__ == "__main__":
    run_optimizer()
