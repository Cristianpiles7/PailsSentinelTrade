import sqlite3
import aiosqlite
import os
import logging
import asyncio
from datetime import datetime

logger = logging.getLogger("PST-Database")

class PSTDatabase:
    def __init__(self, db_path="PST_Core/data/pst_trading.db"):
        self.db_path = db_path
        # Asegurar que el directorio existe
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        async def initialize(self):
        """Crea las tablas si no existen y activa modo WAL."""
        async with aiosqlite.connect(self.db_path, timeout=30) as db:
            # Activar modo WAL para permitir lectura/escritura concurrente
            await db.execute("PRAGMA journal_mode=WAL")
            
            # --- TABLA CONFIGURACIÓN SÍMBOLOS (NECESARIA PARA RADAR) ---
            await db.execute('''
                CREATE TABLE IF NOT EXISTS symbols_config (
                    symbol TEXT PRIMARY KEY,
                    type TEXT DEFAULT 'FOREX',
                    is_active INTEGER DEFAULT 1,
                    lot_size REAL DEFAULT 0.01,
                    sl_mult REAL DEFAULT 2.5,
                    tp_mult REAL DEFAULT 6.0,
                    score_threshold REAL DEFAULT 80.0,
                    risk_mode TEXT DEFAULT 'PCT',
                    risk_value REAL DEFAULT 0.25,
                    min_rr REAL DEFAULT 1.5
                )
            ''')
            
            # Insertar símbolos por defecto si la tabla está vacía
            async with db.execute("SELECT COUNT(*) FROM symbols_config") as cursor:
                count = (await cursor.fetchone())[0]
                if count == 0:
                    default_symbols = [
                        ('EURUSD', 'FOREX'), ('GBPUSD', 'FOREX'), ('XAUUSD', 'COMMODITY'),
                        ('NAS100', 'INDEX'), ('BTCUSD', 'CRYPTO'), ('US30', 'INDEX')
                    ]
                    await db.executemany("INSERT INTO symbols_config (symbol, type) VALUES (?, ?)", default_symbols)

            # Tabla de Operaciones (Trades)
            await db.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    type TEXT,
                    volume REAL,
                    price_in REAL,
                    price_out REAL,
                    sl REAL,
                    tp REAL,
                    profit REAL,
                    time_in TIMESTAMP,
                    time_out TIMESTAMP,
                    regime_at_entry TEXT,
                    strategy_name TEXT,
                    ticket INTEGER DEFAULT 0,
                    is_partial_closed INTEGER DEFAULT 0
                )
            ''')
            
            try:
                await db.execute("ALTER TABLE trades ADD COLUMN ticket INTEGER DEFAULT 0")
                await db.execute("ALTER TABLE trades ADD COLUMN is_partial_closed INTEGER DEFAULT 0")
            except: pass # Ya existen
            
            # Tabla de Señales (Para analizar fiabilidad)
            await db.execute('''
                CREATE TABLE IF NOT EXISTS signal_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP,
                    symbol TEXT,
                    regime TEXT,
                    strategy TEXT,
                    signal_type TEXT, -- BUY/SELL/NONE
                    score REAL,
                    price REAL
                )
            ''')
            
            # Tabla de Regímenes (Historial de mercado)
            await db.execute('''
                CREATE TABLE IF NOT EXISTS regime_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP,
                    symbol TEXT,
                    mode TEXT,
                    adx REAL,
                    volatility_factor REAL,
                    tech_data TEXT -- JSON con RSI, EMAs, etc.
                )
            ''')
            # Tabla de Contexto de Gráfico (Fragmentos de OHLC)
            await db.execute('''
                CREATE TABLE IF NOT EXISTS trade_context (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP,
                    symbol TEXT,
                    ticket INTEGER DEFAULT 0,
                    ohlc_data TEXT -- JSON con las velas del momento
                )
            ''')
            try:
                await db.execute("ALTER TABLE trade_context ADD COLUMN ticket INTEGER DEFAULT 0")
            except: pass
            # Tabla de configuración global
            await db.execute('''
                CREATE TABLE IF NOT EXISTS bot_config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            # Valor por defecto
            # Tabla de Niveles del Usuario (Trading Híbrido)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_levels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    price REAL NOT NULL,
                    type TEXT NOT NULL, 
                    label TEXT,
                    is_active INTEGER DEFAULT 1,
                    last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    factors_json TEXT,
                    price2 REAL, -- Para lineas de tendencia
                    time2 TEXT   -- Para lineas de tendencia
                    , time1 TEXT, time1_ts REAL, time2_ts REAL
                )
            """)

            # Migración: Verificar si columnas price2/time2/time1 existen
            try:
                await db.execute("ALTER TABLE user_levels ADD COLUMN price2 REAL")
                await db.execute("ALTER TABLE user_levels ADD COLUMN time2 TEXT")
                await db.execute("ALTER TABLE user_levels ADD COLUMN time1 TEXT")
                await db.execute("ALTER TABLE user_levels ADD COLUMN time1_ts REAL")
                await db.execute("ALTER TABLE user_levels ADD COLUMN time2_ts REAL")
            except: pass # Ya existen
            
            # Tabla de Configuración de Canales (Nuevo)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS channel_config (
                    symbol TEXT PRIMARY KEY,
                    enable_macro INTEGER DEFAULT 1,
                    enable_tactical INTEGER DEFAULT 1
                )
            """)

            # Tabla de Configuración de Estrategias por Símbolo (NEW V3.3)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS symbol_strategies (
                    symbol TEXT,
                    strategy_name TEXT,
                    is_active INTEGER DEFAULT 1,
                    PRIMARY KEY (symbol, strategy_name)
                )
            """)

            # Tabla de Caché de IA Oracle (Persistencia entre procesos)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ai_oracle_state (
                    symbol TEXT PRIMARY KEY,
                    analysis_json TEXT,
                    last_call_ts REAL
                )
            """)

            # --- TABLA RADAR PARA TELEMETRÍA EN TIEMPO REAL ---
            await db.execute("""
                CREATE TABLE IF NOT EXISTS symbol_radar (
                    symbol TEXT PRIMARY KEY,
                    score REAL DEFAULT 0,
                    regime TEXT DEFAULT 'UNKNOWN',
                    signal_direction TEXT DEFAULT 'NONE',
                    last_update TIMESTAMP,
                    factors_json TEXT
                )
            """)

            # --- NUEVA: TABLA DE LOGS DEL SISTEMA ---
            await db.execute("""
                CREATE TABLE IF NOT EXISTS system_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    level TEXT,
                    message TEXT,
                    source TEXT
                )
            """)
            
            # Migraciones Fase 34: Matrix Editor (columnas de parámetros por símbolo)
            for col, col_def in [
                ("lot_size", "REAL DEFAULT 0.01"),
                ("sl_mult", "REAL DEFAULT 2.5"),
                ("tp_mult", "REAL DEFAULT 6.0"),
                ("score_threshold", "REAL DEFAULT 80.0"),
                ("risk_mode", "TEXT DEFAULT 'PCT'"),
                ("risk_value", "REAL DEFAULT 0.25"),
                ("min_rr", "REAL DEFAULT 1.5"),
            ]:
                try:
                    await db.execute(f"ALTER TABLE symbols_config ADD COLUMN {col} {col_def}")
                except: pass  # Ya existe

            # Migraciones Fase 48: Riesgo por Estrategia y BE/TS Configurable
            for col, col_def in [
                ("risk_mode", "TEXT"),
                ("risk_value", "REAL"),
                ("sl_mult", "REAL"),
                ("tp_mult", "REAL"),
                ("score_threshold", "REAL"),
                ("use_trailing", "INTEGER DEFAULT 1"), # Default 1 (Phase 48)
                ("use_breakeven", "INTEGER DEFAULT 1"), # Default 1 (Phase 48)
                ("be_mult", "REAL DEFAULT 2.0"),
                ("ts_mult", "REAL DEFAULT 2.5"),
                ("min_rr", "REAL DEFAULT 1.5"),  # R:R mínimo para ejecutar (Fase 49)
            ]:
                try:
                    await db.execute(f"ALTER TABLE symbol_strategies ADD COLUMN {col} {col_def}")
                except: pass  # Ya existe

            # Migraciones Fase 53: Sentinel Journal (Notas y Snapshots)
            for col, col_def in [
                ("notes", "TEXT"),
                ("snapshot_path", "TEXT")
            ]:
                try:
                    await db.execute(f"ALTER TABLE trades ADD COLUMN {col} {col_def}")
                except: pass # Ya existe


            await db.commit()
            logger.info(f"✅ Base de Datos Inicializada en {self.db_path}")

    async def add_log(self, level, message, source="SYSTEM"):
        """Añade un mensaje de log a la base de datos."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                await db.execute("""
                    INSERT INTO system_logs (level, message, source)
                    VALUES (?, ?, ?)
                """, (level, message, source))
                # Auto-purga: Mantener últimos 500 logs para no inflar la DB
                await db.execute("""
                    DELETE FROM system_logs 
                    WHERE id NOT IN (SELECT id FROM system_logs ORDER BY id DESC LIMIT 500)
                """)
                await db.commit()
        except Exception as e:
            # No usamos logger.error aquí para evitar bucles infinitos si falla el log
            print(f"❌ Error add_log: {e}")

    async def get_latest_logs(self, limit=100):
        """Obtiene los últimos N logs del sistema."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM system_logs ORDER BY id DESC LIMIT ?", (limit,)) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]
        except Exception as e:
            print(f"❌ Error get_latest_logs: {e}")
            return []

    # --- MÉTODOS TRADING HÍBRIDO (Niveles del Usuario) ---
    async def get_user_levels(self, symbol):
        """Obtiene niveles activos para un símbolo."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM user_levels WHERE symbol = ? AND is_active = 1", (symbol,)) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"❌ Error get_user_levels: {e}")
            return []

    async def save_user_level(self, symbol, price, ltype, label=None, price2=None, time2=None, time1=None, time1_ts=None, time2_ts=None):
        """Guarda o actualiza un nivel manual (Line u Horizontal)."""
        async with aiosqlite.connect(self.db_path, timeout=30) as db:
            if label and label.startswith('FIXED_'):
                 # Check if update instead of insert
                 await db.execute("DELETE FROM user_levels WHERE symbol = ? AND label = ?", (symbol, label))

            await db.execute("""
                INSERT INTO user_levels (symbol, price, type, label, price2, time2, time1, time1_ts, time2_ts) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (symbol, float(price), ltype, label, price2, time2, time1, time1_ts, time2_ts))
            await db.commit()

    async def delete_user_level(self, level_id):
        """Elimina un nivel manual por ID."""
        async with aiosqlite.connect(self.db_path, timeout=30) as db:
            await db.execute("DELETE FROM user_levels WHERE id = ?", (level_id,))
            await db.commit()

    async def log_regime(self, symbol, mode, adx, vol_factor=1.0, tech_data=None):
        """Registra el estado del mercado con reintentos."""
        for attempt in range(5):
            try:
                async with aiosqlite.connect(self.db_path, timeout=30) as db:
                    await db.execute('''
                        INSERT INTO regime_history (timestamp, symbol, mode, adx, volatility_factor, tech_data)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (datetime.now(), symbol, mode, adx, vol_factor, tech_data))
                    await db.commit()
                break
            except sqlite3.OperationalError as e:
                if "locked" in str(e):
                    await asyncio.sleep(0.1 * (attempt + 1))
                else: raise

    async def log_signal(self, symbol, regime, strategy, sig_type, score, price):
        """Registra una señal para análisis de métricas con reintentos."""
        for attempt in range(5):
            try:
                async with aiosqlite.connect(self.db_path, timeout=30) as db:
                    await db.execute('''
                        INSERT INTO signal_logs (timestamp, symbol, regime, strategy, signal_type, score, price)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (datetime.now(), symbol, regime, strategy, sig_type, score, price))
                    await db.commit()
                break
            except sqlite3.OperationalError as e:
                if "locked" in str(e):
                    await asyncio.sleep(0.1 * (attempt + 1))
                else: raise

    async def save_trade(self, trade_data: dict):
        """Guarda una operación iniciada o finalizada."""
        if 'is_partial_closed' not in trade_data:
            trade_data['is_partial_closed'] = 0
            
        async with aiosqlite.connect(self.db_path, timeout=30) as db:
            await db.execute('''
                INSERT INTO trades (symbol, type, volume, price_in, price_out, sl, tp, profit, time_in, time_out, regime_at_entry, strategy_name, ticket, is_partial_closed)
                VALUES (:symbol, :type, :volume, :price_in, :price_out, :sl, :tp, :profit, :time_in, :time_out, :regime_at_entry, :strategy_name, :ticket, :is_partial_closed)
            ''', trade_data)
            await db.commit()

    async def update_trade_cierre(self, ticket: int, price_out: float, profit: float):
        """Actualiza el precio de salida, beneficio horario y tiempo de cierre de una operación."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                await db.execute('''
                    UPDATE trades 
                    SET price_out = ?, profit = ?, time_out = ?
                    WHERE ticket = ? AND price_out = 0
                ''', (price_out, profit, str(datetime.now()), ticket))
                await db.commit()
        except Exception as e:
            logger.error(f"❌ Error update_trade_cierre: {e}")

    async def mark_trade_partial_closed(self, ticket: int):
        """Marca una operación como que ya ha tenido un cierre parcial del 50%."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                await db.execute("UPDATE trades SET is_partial_closed = 1 WHERE ticket = ?", (ticket,))
                await db.commit()
        except Exception as e:
            logger.error(f"❌ Error mark_trade_partial_closed: {e}")

    async def update_trade_notes(self, ticket, notes):
        """Actualiza las notas/comentario de un trade."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                await db.execute("UPDATE trades SET notes = ? WHERE ticket = ?", (notes, ticket))
                await db.commit()
                return True
        except Exception as e:
            logger.error(f"❌ Error update_trade_notes: {e}")
            return False

    async def get_advanced_metrics(self):
        """Calcula métricas avanzadas: Profit Factor, Win Rate, Streaks."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                db.row_factory = aiosqlite.Row
                # 1. Win Rate y Profit Factor
                async with db.execute("""
                    SELECT 
                        COUNT(*) as total,
                        SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) as wins,
                        SUM(CASE WHEN profit > 0 THEN profit ELSE 0 END) as gross_profit,
                        SUM(CASE WHEN profit < 0 THEN ABS(profit) ELSE 0 END) as gross_loss
                    FROM trades WHERE price_out > 0
                """) as cursor:
                    res = await cursor.fetchone()
                    total = res['total'] or 0
                    wins = res['wins'] or 0
                    win_rate = (wins / total * 100) if total > 0 else 0
                    gross_profit = res['gross_profit'] or 0
                    gross_loss = res['gross_loss'] or 0
                    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else gross_profit

                # 2. Streaks (Rachas)
                async with db.execute("SELECT profit FROM trades WHERE price_out > 0 ORDER BY id ASC") as cursor:
                    rows = await cursor.fetchall()
                    profits = [r[0] for r in rows]
                    
                    max_win_streak = 0
                    max_loss_streak = 0
                    curr_win_streak = 0
                    curr_loss_streak = 0
                    
                    for p in profits:
                        if p > 0:
                            curr_win_streak += 1
                            curr_loss_streak = 0
                        elif p < 0:
                            curr_loss_streak += 1
                            curr_win_streak = 0
                        max_win_streak = max(max_win_streak, curr_win_streak)
                        max_loss_streak = max(max_loss_streak, curr_loss_streak)

                # 3. Métricas por Estrategia
                async with db.execute("""
                    SELECT 
                        strategy_name,
                        COUNT(*) as total,
                        SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) as wins,
                        SUM(profit) as total_profit
                    FROM trades WHERE price_out > 0
                    GROUP BY strategy_name
                """) as cursor:
                    rows_strat = await cursor.fetchall()
                    by_strategy = {}
                    for r in rows_strat:
                        s_name = r['strategy_name'] or "Manual"
                        s_total = r['total'] or 0
                        s_wins = r['wins'] or 0
                        s_profit = r['total_profit'] or 0.0
                        s_wr = (s_wins / s_total * 100) if s_total > 0 else 0
                        by_strategy[s_name] = {
                            "total": s_total,
                            "wins": s_wins,
                            "win_rate": round(s_wr, 1),
                            "profit": round(s_profit, 2)
                        }

                return {
                    "total_trades": total,
                    "win_rate": round(win_rate, 2),
                    "profit_factor": round(profit_factor, 2),
                    "max_win_streak": max_win_streak,
                    "max_loss_streak": max_loss_streak,
                    "gross_profit": round(gross_profit, 2),
                    "gross_loss": round(gross_loss, 2),
                    "by_strategy": by_strategy
                }
        except Exception as e:
            logger.error(f"❌ Error get_advanced_metrics: {e}")
            return {}

    async def get_equity_curve(self):
        """Genera datos para la curva de equidad."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("""
                    SELECT time_out, profit FROM trades 
                    WHERE price_out > 0 
                    ORDER BY time_out ASC
                """) as cursor:
                    rows = await cursor.fetchall()
                    curve = []
                    cum_profit = 0
                    # Punto inicial
                    curve.append({"time": "Start", "equity": 0})
                    for r in rows:
                        cum_profit += r['profit']
                        curve.append({
                            "time": r['time_out'],
                            "equity": round(cum_profit, 2)
                        })
                    return curve
        except Exception as e:
            logger.error(f"❌ Error get_equity_curve: {e}")
            return []

    async def save_context(self, symbol, ohlc_json, ticket=0):
        """Guarda un fragmento de OHLC para visualización posterior."""
        for attempt in range(5):
            try:
                async with aiosqlite.connect(self.db_path, timeout=30) as db:
                    await db.execute('''
                        INSERT INTO trade_context (timestamp, symbol, ticket, ohlc_data)
                        VALUES (?, ?, ?, ?)
                    ''', (datetime.now(), symbol, ticket, ohlc_json))
                    await db.commit()
                break
            except sqlite3.OperationalError as e:
                if "locked" in str(e):
                    await asyncio.sleep(0.1 * (attempt + 1))
                else: raise

    async def get_open_tickets(self):
        """Obtiene todos los tickets que aún no tienen precio de salida."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                async with db.execute("SELECT ticket FROM trades WHERE price_out = 0 AND ticket > 0") as cursor:
                    rows = await cursor.fetchall()
                    return [r[0] for r in rows]
        except Exception as e:
            logger.error(f"❌ Error getting open tickets: {e}")
            return []

    async def get_active_trades(self):
        """Obtiene detalles de todos los trades que aún no tienen precio de salida."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM trades WHERE price_out = 0 AND ticket > 0") as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"❌ Error getting active trades: {e}")
            return []

    async def check_trade_exists(self, ticket: int) -> bool:
        """Verifica si un trade con este ticket ya existe en la DB."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                async with db.execute("SELECT 1 FROM trades WHERE ticket = ?", (ticket,)) as cursor:
                    result = await cursor.fetchone()
                    return result is not None
        except Exception as e:
            logger.error(f"❌ Error check_trade_exists: {e}")
            return False

    async def is_trade_open(self, ticket: int) -> bool:
        """Verifica si el trade está abierto (price_out = 0)."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                async with db.execute("SELECT 1 FROM trades WHERE ticket = ? AND price_out = 0", (ticket,)) as cursor:
                    result = await cursor.fetchone()
                    return result is not None
        except Exception as e:
            logger.error(f"❌ Error is_trade_open: {e}")
            return False

    async def get_config(self, key, default="AUTO"):
        """Obtiene un valor de configuración global."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                async with db.execute("SELECT value FROM bot_config WHERE key = ?", (key,)) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else default
        except Exception as e:
            logger.error(f"❌ Error getting config {key}: {e}")
            return default

    async def update_config(self, key, value):
        """Actualiza un valor de configuración global."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                await db.execute("INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)", (key, value))
                await db.commit()
            return True
        except Exception as e:
            logger.error(f"❌ Error updating config {key}: {e}")
            return False

    async def clear_all_logs(self):
        """Borra todos los historiales pero mantiene la configuración."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                await db.execute("DELETE FROM trades")
                await db.execute("DELETE FROM signal_logs")
                await db.execute("DELETE FROM regime_history")
                await db.execute("DELETE FROM trade_context")
                await db.execute("DELETE FROM user_levels")      # NUEVO: Limpiar dibujos manuales
                await db.execute("DELETE FROM channel_config")   # NUEVO: Limpiar configuración de canales
                # Reajustar autoincrementales
                await db.execute("DELETE FROM sqlite_sequence WHERE name IN ('trades', 'signal_logs', 'regime_history', 'trade_context', 'user_levels')")
                await db.commit()
                
                # NUEVO: Comprimir base de datos para liberar espacio físico
                await db.execute("VACUUM")
            logger.info("♻️ Base de Datos reseteada (Logs limpiados)")
            return True
        except Exception as e:
            logger.error(f"❌ Error reseteando base de datos: {e}")
            return False

    async def get_all_symbols_config(self):
        """Obtiene toda la configuración de símbolos."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM symbols_config ORDER BY type, symbol") as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"❌ Error getting all symbols config: {e}")
            return []

    async def set_symbol_active(self, symbol, is_active):
        """Establece el estado is_active de un símbolo."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                await db.execute("UPDATE symbols_config SET is_active = ? WHERE symbol = ?", (1 if is_active else 0, symbol))
                await db.commit()
                return True
        except Exception as e:
            logger.error(f"❌ Error setting symbol active {symbol}: {e}")
            return False

    async def is_symbol_active(self, symbol) -> bool:
        """Verifica si un símbolo está activo en la tabla symbols_config."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                async with db.execute("SELECT is_active FROM symbols_config WHERE symbol = ?", (symbol,)) as cursor:
                    row = await cursor.fetchone()
                    return bool(row[0]) if row else True # Default True si no existe
        except Exception as e:
            logger.error(f"❌ Error checking is_symbol_active for {symbol}: {e}")
            return True

    async def get_symbol_strategies(self, symbol) -> dict:
        """Obtiene el mapa de estrategias con sus parámetros para un símbolo."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM symbol_strategies WHERE symbol = ?", (symbol,)) as cursor:
                    rows = await cursor.fetchall()
                    return {r['strategy_name']: dict(r) for r in rows}
        except Exception as e:
            logger.error(f"❌ Error getting strategies for {symbol}: {e}")
            return {}

    async def set_symbol_strategy(self, symbol, strategy_name, is_active=None, risk_mode=None, risk_value=None, sl_mult=None, tp_mult=None, score_threshold=None, use_trailing=None, use_breakeven=None, be_mult=None, ts_mult=None, min_rr=None):
        """Actualiza la configuración de una estrategia específica para un símbolo."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                # Primero verificar si existe
                async with db.execute("SELECT 1 FROM symbol_strategies WHERE symbol = ? AND strategy_name = ?", (symbol, strategy_name)) as cursor:
                    exists = await cursor.fetchone()
                
                if not exists:
                    await db.execute("""
                        INSERT INTO symbol_strategies (symbol, strategy_name, is_active, risk_mode, risk_value, sl_mult, tp_mult, score_threshold, use_trailing, use_breakeven, be_mult, ts_mult, min_rr)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        symbol, strategy_name, 
                        1 if is_active is None or is_active else 0,
                        risk_mode, risk_value, sl_mult, tp_mult, score_threshold,
                        1 if use_trailing else 0 if use_trailing is not None else 1,
                        1 if use_breakeven else 0 if use_breakeven is not None else 1,
                        be_mult if be_mult is not None else 2.0,
                        ts_mult if ts_mult is not None else 2.5,
                        min_rr if min_rr is not None else 1.5
                    ))
                else:
                    fields, vals = [], []
                    if is_active is not None:      fields.append("is_active = ?");      vals.append(1 if is_active else 0)
                    if risk_mode is not None:      fields.append("risk_mode = ?");      vals.append(risk_mode)
                    if risk_value is not None:     fields.append("risk_value = ?");     vals.append(risk_value)
                    if sl_mult is not None:        fields.append("sl_mult = ?");        vals.append(sl_mult)
                    if tp_mult is not None:        fields.append("tp_mult = ?");        vals.append(tp_mult)
                    if score_threshold is not None: fields.append("score_threshold = ?"); vals.append(score_threshold)
                    if use_trailing is not None:   fields.append("use_trailing = ?");   vals.append(1 if use_trailing else 0)
                    if use_breakeven is not None:  fields.append("use_breakeven = ?");  vals.append(1 if use_breakeven else 0)
                    if be_mult is not None:        fields.append("be_mult = ?");        vals.append(be_mult)
                    if ts_mult is not None:        fields.append("ts_mult = ?");        vals.append(ts_mult)
                    if min_rr is not None:         fields.append("min_rr = ?");         vals.append(min_rr)
                    
                    if fields:
                        vals.extend([symbol, strategy_name])
                        await db.execute(f"UPDATE symbol_strategies SET {', '.join(fields)} WHERE symbol = ? AND strategy_name = ?", tuple(vals))
                
                await db.commit()
                return True
        except Exception as e:
            logger.error(f"❌ Error setting symbol strategy {symbol}/{strategy_name}: {e}")
            return False
    # ── FASE 34: MATRIX EDITOR ──────────────────────────────────────────────────
    async def get_symbol_params(self, symbol: str) -> dict:
        """Obtiene los parámetros de trading individuales de un símbolo (globales o por defecto)."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT lot_size, sl_mult, tp_mult, score_threshold, risk_mode, risk_value, min_rr FROM symbols_config WHERE symbol = ?",
                    (symbol,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return dict(row)
                    # Defaults si no existe la fila
                    return {
                        "lot_size": 0.01, "sl_mult": 2.5, "tp_mult": 6.0, 
                        "score_threshold": 65.0, "risk_mode": "PCT", "risk_value": 0.25,
                        "min_rr": 1.5
                    }
        except Exception as e:
            logger.error(f"❌ Error get_symbol_params {symbol}: {e}")
            return {
                "lot_size": 0.01, "sl_mult": 2.5, "tp_mult": 6.0, 
                "score_threshold": 65.0, "risk_mode": "PCT", "risk_value": 0.25,
                "min_rr": 1.5
            }

    async def update_symbol_params(self, symbol: str, lot_size: float = None, sl_mult: float = None, tp_mult: float = None, score_threshold: float = None, risk_mode: str = None, risk_value: float = None, min_rr: float = None) -> bool:
        """Actualiza los parámetros de trading de un símbolo (solo los que se pasen)."""
        try:
            fields, vals = [], []
            if lot_size is not None:       fields.append("lot_size = ?");       vals.append(lot_size)
            if sl_mult is not None:        fields.append("sl_mult = ?");        vals.append(sl_mult)
            if tp_mult is not None:        fields.append("tp_mult = ?");        vals.append(tp_mult)
            if score_threshold is not None: fields.append("score_threshold = ?"); vals.append(score_threshold)
            if risk_mode is not None:      fields.append("risk_mode = ?");      vals.append(risk_mode)
            if risk_value is not None:     fields.append("risk_value = ?");     vals.append(risk_value)
            if min_rr is not None:         fields.append("min_rr = ?");         vals.append(min_rr)
            if not fields:
                return True  # Nada que cambiar
            vals.append(symbol)
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                await db.execute(
                    f"UPDATE symbols_config SET {', '.join(fields)} WHERE symbol = ?",
                    tuple(vals)
                )
                await db.commit()
            return True
        except Exception as e:
            logger.error(f"❌ Error update_symbol_params {symbol}: {e}")
            return False

    async def save_ai_oracle_state(self, symbol, analysis_json, last_call_ts):
        """Guarda el estado actual de la IA para un símbolo."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                await db.execute("""
                    INSERT INTO ai_oracle_state (symbol, analysis_json, last_call_ts)
                    VALUES (?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET 
                        analysis_json=excluded.analysis_json,
                        last_call_ts=excluded.last_call_ts
                """, (symbol, analysis_json, last_call_ts))
                await db.commit()
            return True
        except Exception as e:
            logger.error(f"❌ Error saving AI state for {symbol}: {e}")
            return False

    async def get_ai_oracle_state(self, symbol):
        """Obtiene el último estado guardado de la IA."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                async with db.execute("SELECT analysis_json, last_call_ts FROM ai_oracle_state WHERE symbol = ?", (symbol,)) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        import json
                        return {
                            "analysis": json.loads(row[0]),
                            "ts": row[1]
                        }
            return None
        except Exception as e:
            logger.error(f"❌ Error getting AI state for {symbol}: {e}")
            return None

    async def count_active_strategy_symbols(self, strategy_id: str) -> int:
        """Cuenta cuántos símbolos tienen activada una estrategia específica."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                # La tabla symbol_strategies tiene columnas (symbol, strategy_name, is_active)
                async with db.execute("SELECT COUNT(*) FROM symbol_strategies WHERE strategy_name = ? AND is_active = 1", (strategy_id,)) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else 0
        except Exception as e:
            logger.error(f"❌ Error counting active strategy symbols for {strategy_id}: {e}")
            return 1 # Fallback conservador

    # --- NUEVOS MÉTODOS RADAR ---
    async def save_radar_snapshot(self, symbol, score, regime, direction, factors=None):
        """Actualiza el radar de oportunidad de un símbolo (Fast Upsert)."""
        try:
            import json
            factors_json = json.dumps(factors) if factors else None
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                await db.execute("""
                    INSERT INTO symbol_radar (symbol, score, regime, signal_direction, last_update, factors_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        score=excluded.score,
                        regime=excluded.regime,
                        signal_direction=excluded.signal_direction,
                        last_update=excluded.last_update,
                        factors_json=excluded.factors_json
                """, (symbol, float(score), regime, direction, datetime.now(), factors_json))
                await db.commit()
        except Exception as e:
            logger.error(f"❌ Error saving radar snapshot for {symbol}: {e}")

    async def get_radar_data(self):
        """Obtiene el estado de radar de todos los símbolos."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM symbol_radar") as cursor:
                    rows = await cursor.fetchall()
                    result = {}
                    import json
                    for r in rows:
                        d = dict(r)
                        if d.get('factors_json'):
                            try:
                                d['factors'] = json.loads(d['factors_json'])
                            except:
                                d['factors'] = []
                        else:
                            d['factors'] = []
                        result[r['symbol']] = d
                    return result
        except Exception as e:
            logger.error(f"❌ Error getting radar data: {e}")
            return {}

    # --- RISK PROFILES (FASE 35) ---
    async def get_risk_profiles(self):
        """Devuelve todos los perfiles de riesgo guardados."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM risk_profiles") as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"❌ Error get_risk_profiles: {e}")
            return []

    async def save_risk_profile(self, name, config_json):
        """Guarda o actualiza un perfil de riesgo."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                await db.execute("""
                    INSERT INTO risk_profiles (name, config_json)
                    VALUES (?, ?)
                    ON CONFLICT(name) DO UPDATE SET config_json=excluded.config_json
                """, (name, config_json))
                await db.commit()
                return True
        except Exception as e:
            logger.error(f"❌ Error save_risk_profile: {e}")
            return False

    async def delete_risk_profile(self, profile_id):
        """Elimina un perfil de riesgo por ID."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                await db.execute("DELETE FROM risk_profiles WHERE id = ?", (profile_id,))
                await db.commit()
                return True
        except Exception as e:
            logger.error(f"❌ Error delete_risk_profile: {e}")
            return False
    async def get_24h_profit_by_symbol(self) -> dict:
        """Obtiene el beneficio realizado por cada símbolo en las últimas 24 horas."""
        try:
            from datetime import timedelta
            # Usar formato ISO para comparación robusta en SQLite
            since = (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                db.row_factory = aiosqlite.Row
                # Buscamos trades cerrados (price_out > 0) con time_out en las últimas 24h
                async with db.execute("""
                    SELECT symbol, SUM(profit) as total_profit 
                    FROM trades 
                    WHERE price_out > 0 AND time_out >= ? 
                    GROUP BY symbol
                """, (since,)) as cursor:
                    rows = await cursor.fetchall()
                    pnl_map = {}
                    for r in rows:
                        sym = r['symbol'].upper()
                        pnl_map[sym] = r['total_profit']
                        # Normalización: añadir también la base sin sufijos (.m, .pro, etc)
                        for suffix in [".m", ".pro", ".ecn", ".x", "i"]:
                            if sym.endswith(suffix.upper()):
                                base = sym[:-len(suffix)]
                                pnl_map[base] = pnl_map.get(base, 0.0) + r['total_profit']
                    return pnl_map
        except Exception as e:
            logger.error(f"❌ Error get_24h_profit_by_symbol: {e}")
            return {}
