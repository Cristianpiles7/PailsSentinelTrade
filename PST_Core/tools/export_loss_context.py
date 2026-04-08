import sqlite3
import json
import os
import argparse
from datetime import datetime

# Definir rutas base asumiendo estructura de PST
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(BASE_DIR, "PST_Core", "data", "pst_trading.db")
# Fallback si el entorno está duplicado de carpeta
if not os.path.exists(DB_PATH):
    PARENT_BASE = os.path.dirname(BASE_DIR)
    DB_PATH = os.path.join(PARENT_BASE, "PST_Core", "data", "pst_trading.db")

OUTPUT_DIR = os.path.join(BASE_DIR, ".agents", "data")

def export_losses(limit=10, strategy=None, parse_today=False):
    if not os.path.exists(DB_PATH):
        print(f"Error: Base de datos no encontrada en {DB_PATH}")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_file = os.path.join(OUTPUT_DIR, "recent_losses.json")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Buscar operaciones con pérdida
    query = """
        SELECT ticket, symbol, type, strategy_name, price_in, price_out, sl, tp, profit, time_in, time_out, regime_at_entry
        FROM trades 
        WHERE profit < 0 AND price_out > 0 
    """
    params = []
    
    if parse_today:
        query += " AND date(time_out) = date('now')"
        
    if strategy and strategy.upper() != "ALL":
        query += " AND strategy_name = ?"
        params.append(strategy)
        
    query += " ORDER BY time_out DESC"
    
    if not parse_today:
        query += " LIMIT ?"
        params.append(limit)

    cursor.execute(query, params)
    losses = cursor.fetchall()
    
    if not losses:
        print(f"No se encontraron pérdidas recientes en la base de datos (Estrategia: {strategy if strategy else 'TODAS'}).")
        with open(out_file, 'w', encoding='utf-8') as f:
            json.dump({"losses": []}, f, indent=4)
        return

    export_data = []

    for trade in losses:
        trade_dict = dict(trade)
        ticket = trade_dict['ticket']
        
        # Buscar contexto OHLC
        cursor.execute("""
            SELECT ohlc_data 
            FROM trade_context 
            WHERE ticket = ? 
            ORDER BY id DESC LIMIT 1
        """, (ticket,))
        
        context_row = cursor.fetchone()
        
        if context_row and context_row['ohlc_data']:
            try:
                trade_dict['ohlc_context'] = json.loads(context_row['ohlc_data'])
            except:
                trade_dict['ohlc_context'] = "Error parseando JSON de DB"
        else:
            trade_dict['ohlc_context'] = "Sin contexto guardado"

        export_data.append(trade_dict)

    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump({
            "export_time": datetime.now().isoformat(),
            "strategy_filter": strategy if strategy else "ALL",
            "record_count": len(export_data),
            "losses": export_data
        }, f, indent=4)

    print(f"✅ Éxito: Se exportaron {len(export_data)} registros de operaciones en pérdida a {out_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Exporta el contexto (OHLC) de las últimas operaciones fallidas.")
    parser.add_argument("--limit", type=int, default=10, help="Número máximo de pérdidas a exportar si no se usa --today.")
    parser.add_argument("--strategy", type=str, default=None, help="Filtrar por nombre de estrategia (ej: PST-Scalper-Active).")
    parser.add_argument("--today", action="store_true", help="Extraer todas las pérdidas del día de hoy en lugar de un límite fijo.")
    
    args = parser.parse_args()
    
    print(f"⏳ Exportando Autopsias...")
    export_losses(limit=args.limit, strategy=args.strategy, parse_today=args.today)
