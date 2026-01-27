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
                    ticket INTEGER DEFAULT 0
                )
            ''')
            
            # Migración: Añadir columna ticket si no existe
            try:
                await db.execute("ALTER TABLE trades ADD COLUMN ticket INTEGER DEFAULT 0")
            except: pass # Ya existe
            
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
                    ohlc_data TEXT -- JSON con las velas del momento
                )
            ''')
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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    price2 REAL, -- Para lineas de tendencia
                    time2 TEXT   -- Para lineas de tendencia
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

            # Migración: Verificar si columnas price2/time2 existen
            try:
                await db.execute("ALTER TABLE user_levels ADD COLUMN price2 REAL")
                await db.execute("ALTER TABLE user_levels ADD COLUMN time2 TEXT")
            except: pass # Ya existen
            
            await db.commit()
            logger.info(f"✅ Base de Datos Inicializada en {self.db_path}")

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
        async with aiosqlite.connect(self.db_path, timeout=30) as db:
            await db.execute('''
                INSERT INTO trades (symbol, type, volume, price_in, price_out, sl, tp, profit, time_in, time_out, regime_at_entry, strategy_name, ticket)
                VALUES (:symbol, :type, :volume, :price_in, :price_out, :sl, :tp, :profit, :time_in, :time_out, :regime_at_entry, :strategy_name, :ticket)
            ''', trade_data)
            await db.commit()

    async def update_trade_cierre(self, ticket, price_out, profit):
        """Registra el cierre de una operación por su ticket."""
        async with aiosqlite.connect(self.db_path, timeout=30) as db:
            await db.execute('''
                UPDATE trades 
                SET price_out = ?, profit = ?, time_out = ?
                WHERE ticket = ? AND price_out = 0
            ''', (price_out, profit, str(datetime.now()), ticket))
            await db.commit()

    async def save_context(self, symbol, ohlc_json):
        """Guarda un fragmento de OHLC para visualización posterior."""
        for attempt in range(5):
            try:
                async with aiosqlite.connect(self.db_path, timeout=30) as db:
                    await db.execute('''
                        INSERT INTO trade_context (timestamp, symbol, ohlc_data)
                        VALUES (?, ?, ?)
                    ''', (datetime.now(), symbol, ohlc_json))
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

