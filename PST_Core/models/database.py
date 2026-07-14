import sqlite3
import aiosqlite
import os
import json
import logging
import asyncio
from datetime import datetime

from ..config import DB_PATH

logger = logging.getLogger("PST-Database")

class PSTDatabase:
    def __init__(self, db_path=DB_PATH):
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
                    min_rr REAL DEFAULT 1.6
                )
            ''')
            
            # Insertar símbolos por defecto si la tabla está vacía
            async with db.execute("SELECT COUNT(*) FROM symbols_config") as cursor:
                count = (await cursor.fetchone())[0]
                if count == 0:
                    # Universo activo por defecto (is_active=1 por la columna). Debe coincidir con
                    # enabled_by_default de la siembra maestra para no dejar símbolos activos de más.
                    default_symbols = [
                        ('EURUSD', 'FOREX'), ('GBPUSD', 'FOREX'),
                        ('XAUUSD', 'COMMODITY'),
                        ('BTCUSD', 'CRYPTO'), ('ETHUSD', 'CRYPTO'),
                        ('US500.cash', 'INDEX'), ('EU50.cash', 'INDEX'),
                        ('AAPL', 'STOCK')
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
                    price REAL,
                    blocked_reason TEXT -- NUEVO: Motivo del bloqueo si aplica
                )
            ''')
            try:
                await db.execute("ALTER TABLE signal_logs ADD COLUMN blocked_reason TEXT")
            except: pass
            
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
            # Valores por defecto
            await db.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES ('loss_cooldown_minutes', '15')")
            await db.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES ('hysteresis_minutes', '15')")
            await db.execute("INSERT OR IGNORE INTO bot_config (key, value) VALUES ('daily_drawdown_locked', '0')")
            await db.commit()
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
                    , time1 TEXT, time1_ts REAL, time2_ts REAL,
                    mode TEXT DEFAULT 'BOTH'
                )
            """)

            # Migración: Verificar si columnas price2/time2/time1 existen
            try:
                await db.execute("ALTER TABLE user_levels ADD COLUMN price2 REAL")
                await db.execute("ALTER TABLE user_levels ADD COLUMN time2 TEXT")
                await db.execute("ALTER TABLE user_levels ADD COLUMN time1 TEXT")
                await db.execute("ALTER TABLE user_levels ADD COLUMN time1_ts REAL")
                await db.execute("ALTER TABLE user_levels ADD COLUMN time2_ts REAL")
                await db.execute("ALTER TABLE user_levels ADD COLUMN mode TEXT DEFAULT 'BOTH'")
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
                ("filter_profile", "TEXT"),      # Fase 2: perfil de filtros JSON por símbolo/grupo
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

            # Migración v3.0: Reemplazar estrategias legacy por las nuevas
            legacy_strategies = ['PST-EMA-Flow', 'PST-TrendMaster', 'PST-Scalper-Pro', 'PST-Scalper-Active']
            for legacy in legacy_strategies:
                await db.execute("DELETE FROM symbol_strategies WHERE strategy_name = ?", (legacy,))

            # Migración 2026-07-04: nombres de símbolo que NO existen en FTMO (filas inertes,
            # jamás pudieron operar). Los reales son US30.cash y US100.cash (Nasdaq) — se
            # borran los muertos y la siembra maestra re-inserta con el nombre correcto.
            for dead in ('US30', 'NAS100.cash'):
                await db.execute("DELETE FROM symbols_config WHERE symbol = ?", (dead,))
                await db.execute("DELETE FROM symbol_strategies WHERE symbol = ?", (dead,))

            # --- NUEVO: SIEMBRA MAESTRA DE 32 SÍMBOLOS Y ESTRATEGIAS (v1.4.2+) ---
            # Configuración Maestra Final (Sincronizada v1.4.4)
            master_config = [
                ('EURUSD', 'FOREX'), ('GBPUSD', 'FOREX'), ('USDJPY', 'FOREX'),
                ('AUDUSD', 'FOREX'), ('USDCHF', 'FOREX'), ('USDCAD', 'FOREX'),
                ('NZDUSD', 'FOREX'), ('EURGBP', 'FOREX'), ('EURJPY', 'FOREX'),
                ('XAUUSD', 'COMMODITY'), ('XAGUSD', 'COMMODITY'), ('XTIUSD', 'COMMODITY'),
                ('XNGUSD', 'COMMODITY'), ('BTCUSD', 'CRYPTO'), ('ETHUSD', 'CRYPTO'),
                ('SOLUSD', 'CRYPTO'), ('ADAUSD', 'CRYPTO'), ('DOTUSD', 'CRYPTO'),
                ('LNKUSD', 'CRYPTO'), ('LTCUSD', 'CRYPTO'), ('UNIUSD', 'CRYPTO'),
                ('XLMUSD', 'CRYPTO'), ('XRPUSD', 'CRYPTO'), ('US100.cash', 'INDEX'),
                ('US30.cash', 'INDEX'), ('US500.cash', 'INDEX'), ('GER40.cash', 'INDEX'),
                ('EU50.cash', 'INDEX'), ('UK100.cash', 'INDEX'), ('TSLA', 'STOCK'),
                ('NVDA', 'STOCK'), ('AAPL', 'STOCK'), ('AMZN', 'STOCK'),
                ('GOOG', 'STOCK'), ('META', 'STOCK'), ('MSFT', 'STOCK'),
                # v2.6.4: expansión de universo (juez fiel 20d, ver enabled_by_default abajo).
                ('AUS200.cash', 'INDEX'), ('FRA40.cash', 'INDEX'), ('HK50.cash', 'INDEX'),
                ('JP225.cash', 'INDEX'),
                ('BA', 'STOCK'), ('XOM', 'STOCK'), ('AMD', 'STOCK'), ('KO', 'STOCK'),
                # v2.6.5: 2ª tanda de acciones (juez fiel 20d, ver enabled_by_default abajo).
                ('ZM', 'STOCK'), ('MCD', 'STOCK'), ('CVX', 'STOCK'), ('INTC', 'STOCK'),
                ('RTX', 'STOCK'), ('PLTR', 'STOCK'),
                # v2.6.6: 3ª tanda de acciones (juez fiel 20d, ver enabled_by_default abajo).
                ('AVGO', 'STOCK'), ('BABA', 'STOCK'), ('GM', 'STOCK'), ('BRK.B', 'STOCK'),
                ('NFLX', 'STOCK'), ('FDX', 'STOCK'), ('ASML', 'STOCK'), ('ARM', 'STOCK'),
            ]
            
            # Universo ACTIVO por defecto (whitelist). Solo estos arrancan operando; el resto
            # queda desactivado hasta activarlo desde el Matrix Editor. Son los símbolos con
            # perfil de scalping tuneado y validado en el harness fiel (entry_threshold 72).
            # Universo re-validado a lookback 200 con el motor fiel YA consciente de COSTES
            # reales (comisión del bróker + spread de salida) y con el SL/TP dimensionados
            # como el executor (ATR de M5, no M1). A esa escala, con costes:
            #   · EURUSD fuera: negativo tras comisión (−0.06R; forex clavado al suelo, fiable).
            #   · NVDA fuera (negativo en 2 ventanas previas).
            # ETHUSD reactivado (v2.6.2): con el guard de comisión cripto + entry_threshold
            # 72→80 (juez fiel 20d, perfil real shippeado) pasa de −3.51R a +1.57R. Antes
            # estaba fuera por perder −0.18R con el perfil viejo (threshold 72, sin guard).
            # Expansión 2026-07-04: barrido COMPLETO del universo inactivo (22 símbolos) a
            # lookback 200 con costes reales, en 2 ventanas independientes (15d y 30d;
            # baselines en Tools/bt_baselines/scan200_*). Confirmados los 4 índices nuevos
            # + MSFT (positivos en AMBAS ventanas); UK100 entra como EXPERIMENTAL (PF 1.19,
            # el más fino). Todo lo demás quedó descartado por costes: cruces forex
            # (−0.47..−1.04R), XAGUSD, petróleo, altcoins (comisión pct_notional) y el
            # resto de acciones (GOOG/META/AMZN/TSLA negativas).
            enabled_by_default = {
                'GBPUSD',                     # FOREX (+0.20R con costes)
                'XAUUSD',                     # COMMODITY (+0.24R con costes)
                'BTCUSD',                     # CRYPTO (breakeven ~0.00R; vigilar)
                'ETHUSD',                     # CRYPTO (v2.6.2: +1.57R@20d con guard+threshold80)
                'US500.cash', 'EU50.cash',    # INDEX (+0.16 / +0.05R con costes)
                'US100.cash',                 # INDEX (+0.29R@30d PF1.86 — el mejor del barrido)
                'US30.cash',                  # INDEX (+0.20R@30d PF1.73 DD3.6R)
                'GER40.cash',                 # INDEX (+0.19R@30d PF1.56; +0.46R@15d)
                'UK100.cash',                 # INDEX EXPERIMENTAL (+0.07R@30d PF1.19 — vigilar)
                'AAPL',                       # STOCK (+0.06R con costes; NVDA fuera)
                'MSFT',                       # STOCK (+0.14R@30d PF1.47)
                # v2.6.4: expansión de universo (juez fiel 20d, guard comisión + no-partial
                # METAL + threshold shippeado ya activos). 4 índices nuevos, TODOS positivos:
                'AUS200.cash',                # INDEX (+9.3R@20d avg_rr 1.14 wr 58.5%)
                'FRA40.cash',                 # INDEX (+2.7R@20d avg_rr 1.09 wr 56.5%)
                'HK50.cash',                  # INDEX (+7.7R@20d avg_rr 1.11 wr 60.0%)
                'JP225.cash',                 # INDEX (+13.9R@20d avg_rr 0.90 wr 68.8%)
                # 4 acciones ganadoras de 11 probadas (DIS/WMT/IBM/QCOM/PFE/JPM/CSCO
                # negativas o casi neutras, quedan fuera):
                'BA',                         # STOCK (+13.3R@20d avg_rr 1.97 wr 58.8%)
                'XOM',                        # STOCK (+7.1R@20d avg_rr 1.91 wr 46.7%)
                'AMD',                        # STOCK (+4.1R@20d avg_rr 1.84 wr 42.9%)
                'KO',                         # STOCK (+3.0R@20d avg_rr 1.94 wr 40.9%)
                # v2.6.5: 2ª tanda de acciones (6 ganadoras de 12 probadas; JNJ/NKE/SBUX/BAC
                # negativas y SNOW/LMT marginales +1.2R quedan fuera, muestra pequeña):
                'ZM',                         # STOCK (+7.9R@20d avg_rr 1.48 wr 53.8%)
                'MCD',                        # STOCK (+6.7R@20d avg_rr 1.52 wr 53.3%)
                'CVX',                        # STOCK (+6.2R@20d avg_rr 1.76 wr 50.0%)
                'INTC',                       # STOCK (+4.9R@20d avg_rr 1.59 wr 50.0%)
                'RTX',                        # STOCK (+4.8R@20d avg_rr 1.13 wr 66.7%, n=18 chico)
                'PLTR',                       # STOCK (+2.2R@20d avg_rr 1.34 wr 46.7%)
                # v2.6.6: 3ª tanda de acciones (8 ganadoras de 10 probadas; GME/AZN negativas
                # quedan fuera):
                'AVGO',                       # STOCK (+15.8R@20d avg_rr 1.83 wr 64.5%)
                'BABA',                       # STOCK (+8.1R@20d avg_rr 2.20 wr 45.0%)
                'GM',                         # STOCK (+5.1R@20d avg_rr 1.24 wr 52.5%)
                'BRK.B',                      # STOCK (+4.5R@20d avg_rr 1.41 wr 55.0%)
                'NFLX',                       # STOCK (+2.8R@20d avg_rr 2.16 wr 36.4%)
                'FDX',                        # STOCK (+2.4R@20d avg_rr 1.22 wr 50.0%)
                'ASML',                       # STOCK (+2.1R@20d avg_rr 1.66 wr 41.7%)
                'ARM',                        # STOCK (+1.8R@20d avg_rr 1.37 wr 44.7%)
            }
            
            # Fase 3: DEFAULTS ÓPTIMOS de PST-PrecisionScalping POR GRUPO de activo.
            # Fuente única de verdad para la creación desde cero: cada grupo arranca con su set
            # de parámetros. Hoy comparten los valores validados y solo difiere el filtro de ruido
            # (cripto relaja el ADX/Chop porque tiene impulsos válidos con ADX bajo). Estructurado
            # para divergir por grupo cuando se afine con el harness. Editable por símbolo (modal).
            _SCALP_BASE = dict(risk_mode='MONEY', risk_value=7.0, use_breakeven=1, use_trailing=0,
                               be_mult=3.5, ts_mult=2.5, min_rr=1.8, sl_mult=1.6, tp_mult=2.5)
            GROUP_DEFAULTS = {
                # FOREX: entry_threshold 72 (más selectivo) — validado en harness fiel sobre
                # EURUSD/GBPUSD/USDJPY: sube WR y baja drawdown en los 3, rescata USDJPY.
                # adx_ok 18→21 (group sweep 2026-07-04): GBPUSD confirmado en 2 ventanas
                # (30d Δexp +0.026R, 15d Δexp +0.057R), mismo hallazgo y misma dirección que
                # INDEX y METAL — 4 confirmaciones independientes en 3 grupos distintos.
                "FOREX":     {**_SCALP_BASE, "filter_profile": '{"noise_mode": "on", "entry_threshold": 72, "adx_ok": 21}'},
                # COMMODITY/METAL: entry_threshold 72 — validado en XAUUSD: recorta drawdown
                # ~38% (6.3→3.9R) con expectancy/Sharpe estables.
                # adx_ok 18→21: XAUUSD confirmado en 2 ventanas (30d Δexp +0.020R, 15d +0.027R).
                "COMMODITY": {**_SCALP_BASE, "filter_profile": '{"noise_mode": "on", "entry_threshold": 72, "adx_ok": 21}'},
                "METAL":     {**_SCALP_BASE, "filter_profile": '{"noise_mode": "on", "entry_threshold": 72, "adx_ok": 21}'},
                # INDEX: entry_threshold 72 + vwap_exit OFF — validado en US500.cash: la salida
                # dinámica corta rachas ganadoras antes de tiempo (A/B fiel), mejor dejarlas correr.
                # adx_ok 18→21 (más exigente: solo cuenta "tendencia aceptable" con ADX M5 más
                # fuerte) — group sweep 2026-07-04, muestra agregada de 162 trades (US500/US100/
                # US30/GER40, 15d): Δexp +0.059R/Δsharpe +1.13, único ganador limpio de 20
                # palancas barridas. Confirmado en 2ª ventana (30d, los 4 símbolos: todos mejoran,
                # +0.026 a +0.099R) y en holdout EU50.cash (+0.119R, nunca vio el tuning). Único
                # fallo: UK100.cash (−0.035R) → override explícito abajo mantiene su 18 original.
                "INDEX":     {**_SCALP_BASE, "filter_profile": '{"noise_mode": "on", "entry_threshold": 72, "vwap_exit": "off", "adx_ok": 21}'},
                # STOCK: sin A/B propio de vwap_exit (empate en AAPL) → se mantiene "on" por defecto.
                "STOCK":     {**_SCALP_BASE, "filter_profile": '{"noise_mode": "on", "entry_threshold": 72}'},
                # CRYPTO: ruido soft + entry_threshold 72 + vwap_exit OFF — validado en BTC/ETH:
                # la salida dinámica corta rachas ganadoras antes de tiempo; sin ella, ETH reduce
                # su pérdida ~29% y BTC/expectancy mejoran. Recorta drawdown de ETH ~39% (Fase 2).
                "CRYPTO":    {**_SCALP_BASE, "filter_profile": '{"noise_mode": "soft", "entry_threshold": 72, "vwap_exit": "off"}'},
            }
            _SCALP_FALLBACK = {**_SCALP_BASE, "filter_profile": '{"noise_mode": "on", "entry_threshold": 72}'}

            # Overrides de filter_profile por SÍMBOLO (no por grupo): para ajustes que el
            # backtest fiel validó como específicos de un símbolo y que, aplicados a todo el
            # grupo, perjudican a otros miembros. m1_eff_mode (filtro de eficiencia de
            # tendencia M1 / whipsaw) ayuda a ETHUSD pero empeoró a BTCUSD en el A/B pese a
            # ser ambos CRYPTO — A/B 10d motor fiel: expectancy -0.169R→-0.132R, total
            # -30.5R→-18.5R en ETHUSD, sin tocar BTCUSD/resto del universo.
            # entry_threshold 72→80 (v2.6.2, juez fiel 20d): casi toda la pérdida de ETHUSD
            # venía de señales de score 70-79 (score>=80: -2.55R: score<80: -26.40R). Con
            # threshold=80 el total pasa de -28.95R a -2.91R (con guard de comisión ya
            # aplicado) — no llega a positivo pero recorta el 90% del daño. Vigilar antes de
            # reactivar en enabled_by_default.
            SYMBOL_FILTER_OVERRIDES = {
                'ETHUSD': {'m1_eff_mode': 'on', 'entry_threshold': 80},
                # UK100.cash: mantiene el adx_ok=18 ORIGINAL del grupo INDEX (no el 21 nuevo),
                # + entry_threshold 76 y m1_eff_mode on (sweep 2026-07-08, ver abajo).
                # Es el único de los 6 índices donde adx_ok=21 empeoró (−0.035R@15d) en el
                # group sweep — símbolo ya marcado como el más fino/experimental del universo.
                'UK100.cash': {'adx_ok': 18, 'entry_threshold': 76, 'm1_eff_mode': 'on'},
                # US30.cash: filtro de eficiencia M1 anti-whipsaw — validado en 2 ventanas
                # (sweep 15d: exp +0.212→+0.382; confirmación 30d: +0.201→+0.295, WR 58→66%,
                # PF 1.73→2.21, DD 3.6→2.6R). NO es señal de grupo: falla en US100/UK100
                # (mismo patrón símbolo-específico que ETHUSD vs BTCUSD).
                # + entry_threshold 76 (sweep 2026-07-08, ver abajo).
                'US30.cash': {'m1_eff_mode': 'on', 'entry_threshold': 76},
                # US500.cash: entry_threshold 68 (más laxo que el 72 del grupo INDEX) — validado a
                # lookback 200 (fiel a producción) en 2 ventanas: 10d exp +0.048→+0.150, 20d
                # +0.043→+0.081. El 72 (calibrado a lookback 600, no fiel) era demasiado exigente
                # para el índice a la escala real de 200 barras. NO tocado por el sweep de
                # entry_threshold=76 (2026-07-08): ya está en su óptimo (68), 76 empeoró (−0.031R).
                'US500.cash': {'entry_threshold': 68},
                # entry_threshold 72→76 (sweep motor fiel, 2026-07-08, 20d/11 símbolos): más
                # selectivo, sube expectancy limpio y consistente en FOREX/METAL/INDEX/CRYPTO
                # (GBPUSD +0.095R, XAUUSD +0.077R, EU50 +0.066R, US100 +0.060R, GER40 +0.060R,
                # UK100 +0.102R con m1_eff_mode, US30 +0.017R, BTCUSD +0.007R con m1_eff_mode).
                # Agregado: expectancy +0.134R→+0.200R, sharpe 2.30→3.50, WR 57.8%→60.7%,
                # -35% trades (filtra ruido). AAPL/MSFT/US500 sin señal fiable → sin tocar.
                'GBPUSD': {'entry_threshold': 76},
                'XAUUSD': {'entry_threshold': 76},
                'EU50.cash': {'entry_threshold': 76},
                'US100.cash': {'entry_threshold': 76},
                # GER40.cash: entry_threshold 76 + m1_eff_mode on (sweep 2026-07-08, Δexp +0.060R).
                'GER40.cash': {'entry_threshold': 76, 'm1_eff_mode': 'on'},
                # BTCUSD: entry_threshold 76 + m1_eff_mode on (sweep 2026-07-08, Δexp +0.007R;
                # BTC sigue negativo en agregado −0.149R→−0.142R, vigilar).
                'BTCUSD': {'entry_threshold': 76, 'm1_eff_mode': 'on'},
            }

            # RangeBreaker NO comparte el mismo edge que PrecisionScalping por símbolo — el
            # juez fiel (pst_range_lab.py, FaithfulRangeEngine) confirmó que en la expansión
            # v2.6.4-6 (22 símbolos nuevos + ETHUSD) apenas genera señales (0-1 trade/20d en
            # 16 de 18 acciones — su lógica M15+macro H1/H4 no encaja con esa dinámica) y es
            # negativo/neutro donde sí opera (AUS200 -4.0R, ETHUSD -1.6R, JP225 +0.2R). Se
            # desactiva SOLO para RangeBreaker en estos símbolos; PrecisionScalping sigue activo.
            RANGEBREAKER_DISABLED_SYMBOLS = {
                'ETHUSD', 'AUS200.cash', 'FRA40.cash', 'HK50.cash', 'JP225.cash',
                'BA', 'XOM', 'AMD', 'KO', 'ZM', 'MCD', 'CVX', 'INTC', 'RTX', 'PLTR',
                'AVGO', 'BABA', 'GM', 'BRK.B', 'NFLX', 'FDX', 'ASML', 'ARM',
                # v2.6.9: la tanda de expansión del 4-jul se validó SOLO para
                # PrecisionScalping (pst_bt_lab); RangeBreaker quedó activo por descuido
                # en estos 4 y nunca pasó por su juez fiel (pst_range_lab). Live desde
                # el 6-jul: UK100 0/4 (-13.12€), US100 0/1, US30 0/1 → desactivado hasta
                # validarlos. GER40.cash se mantiene: mismo origen, pero live positivo
                # (3/5, +1.17€) — pendiente de pasar por pst_range_lab igualmente.
                'UK100.cash', 'US100.cash', 'US30.cash', 'MSFT',
            }

            # v2.6.9: juez fiel (pst_bt_lab.py, 20d, con el fix del suelo de SL ya
            # incorporado al motor) sobre los 22 símbolos de la expansión v2.6.4-6.
            # 18/22 salen con expectancy positiva sana (0.05-0.34R) — el día -93.73€ del
            # 2026-07-10 fue sobre todo ruido de muestra pequeña + los bugs de ejecución
            # ya corregidos, no falta de edge. Pero 3 salen negativos limpios (sin trades
            # reales aún que lo desmientan) y ASML es marginal en el juez (+0.023R, dentro
            # de su propia tolerancia de 0.02R) Y el peor símbolo en vivo (0/4, -37.30€,
            # un SL a los 9s). Ver Tools/bt_baselines/stocks_expansion_v269_check.json.
            PRECISIONSCALPING_DISABLED_SYMBOLS = {
                'RTX',            # -0.057R
                'FDX',            # -0.048R
                'FRA40.cash',     # -0.130R
                'ASML',           # +0.023R (ruido) + live 0/4 -37.30€
                # v2.6.10: auditoría BBDD completa 13-14 jul (61 trades del test de
                # fidelidad, WR 34.4% vs 48.2% esperado por el juez):
                'ETHUSD',         # el propio baseline (universe_full_v269_20d.json) lo da
                                  # NEGATIVO: exp -0.220R en 108 trades. Nunca debió estar
                                  # activo con el criterio aplicado a RTX/FDX/FRA40. Live 0/1.
                'JP225.cash',     # PAUSA hasta reconciliar: juez WR 55.8% (86 tr) vs live
                                  # 1/8 (-27.75€) — la mayor brecha juez-vivo del universo.
                                  # Juez fuerte + vivo desastroso = coste no modelado
                                  # (spread asiático de madrugada), correr pst_reconcile.py
                                  # sobre los trades reales antes de decidir si vuelve.
                'HK50.cash',      # PAUSA hasta reconciliar: juez WR 46.3% (54 tr) vs live
                                  # 0/4 (-23.48€). Mismo patrón asiático que JP225.
            }

            for sym, stype in master_config:
                is_active = 1 if sym in enabled_by_default else 0
                rb_active = is_active and sym not in RANGEBREAKER_DISABLED_SYMBOLS
                ps_active = is_active and sym not in PRECISIONSCALPING_DISABLED_SYMBOLS
                g = GROUP_DEFAULTS.get(stype, _SCALP_FALLBACK)
                filter_profile = g['filter_profile']
                sym_override = SYMBOL_FILTER_OVERRIDES.get(sym)
                if sym_override:
                    merged = json.loads(filter_profile)
                    merged.update(sym_override)
                    filter_profile = json.dumps(merged)

                # 1. Asegurar símbolo en config global con multiplicadores visuales (Header)
                await db.execute("""
                    INSERT OR IGNORE INTO symbols_config (symbol, type, is_active, sl_mult, tp_mult, score_threshold, min_rr)
                    VALUES (?, ?, ?, 2.5, 3.5, 80.0, 1.6)
                """, (sym, stype, is_active))

                # 3. PST-RangeBreaker — Rango en M15 (7€ base, cap global MAX_LOSS_EUR)
                await db.execute("""
                    INSERT OR IGNORE INTO symbol_strategies
                    (symbol, strategy_name, is_active, risk_mode, risk_value, use_breakeven, use_trailing, be_mult, ts_mult, min_rr, sl_mult, tp_mult)
                    VALUES (?, 'PST-RangeBreaker', ?, 'MONEY', 7.0, 1, 1, 2.0, 2.5, 1.8, 2.5, 3.5)
                """, (sym, rb_active))

                # 4. PST-PrecisionScalping — Scalping en M1 con defaults ÓPTIMOS por grupo
                await db.execute("""
                    INSERT OR IGNORE INTO symbol_strategies
                    (symbol, strategy_name, is_active, risk_mode, risk_value, use_breakeven, use_trailing, be_mult, ts_mult, min_rr, sl_mult, tp_mult, filter_profile)
                    VALUES (?, 'PST-PrecisionScalping', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (sym, ps_active, g['risk_mode'], g['risk_value'], g['use_breakeven'], g['use_trailing'],
                      g['be_mult'], g['ts_mult'], g['min_rr'], g['sl_mult'], g['tp_mult'], filter_profile))

                # Backfill para BBDD ya existentes: fija el perfil de grupo si está vacío O si tiene
                # un perfil AUTO-sembrado de una fase anterior (solo noise_mode, o noise+entry_threshold
                # sin vwap_exit). Así el tuneo llega a instalaciones previas. NO pisa ediciones
                # manuales del usuario (que tendrían otro contenido, p.ej. distinto entry_threshold).
                await db.execute("""
                    UPDATE symbol_strategies SET filter_profile = ?
                    WHERE symbol = ? AND strategy_name = 'PST-PrecisionScalping'
                      AND (filter_profile IS NULL OR filter_profile = ''
                           OR filter_profile = '{"noise_mode": "on"}'
                           OR filter_profile = '{"noise_mode": "soft"}'
                           OR filter_profile = '{"noise_mode": "on", "entry_threshold": 72}'
                           OR filter_profile = '{"noise_mode": "soft", "entry_threshold": 72}'
                           OR filter_profile = '{"noise_mode": "on", "entry_threshold": 72, "vwap_exit": "off"}'
                           OR filter_profile = '{"noise_mode": "soft", "entry_threshold": 72, "vwap_exit": "off"}')
                """, (filter_profile, sym))

            # --- MIGRACIÓN DE UNIVERSO (one-time, versionada) ---
            # El seed con INSERT OR IGNORE NO cambia is_active en BBDD existentes (para no pisar
            # toggles manuales). Pero cuando cambiamos DELIBERADAMENTE el universo (enabled_by_default)
            # queremos que llegue a instalaciones ya existentes UNA vez. Guardado por un flag con
            # versión: se aplica una sola vez por versión; los toggles manuales POSTERIORES persisten.
            # Bumpear UNIVERSE_VERSION cada vez que cambie enabled_by_default.
            UNIVERSE_VERSION = "2026-07-14-a"  # PrecisionScalping desactivado en ETHUSD (baseline negativo -0.220R) y JP225.cash/HK50.cash (pausa: brecha juez-vivo 1/12, -51€, pendiente pst_reconcile)
            async with db.execute("SELECT value FROM bot_config WHERE key='universe_migration_applied'") as cur:
                _uni_row = await cur.fetchone()
            if not _uni_row or _uni_row[0] != UNIVERSE_VERSION:
                for sym, stype in master_config:
                    want = 1 if sym in enabled_by_default else 0
                    want_rb = want and sym not in RANGEBREAKER_DISABLED_SYMBOLS
                    want_ps = want and sym not in PRECISIONSCALPING_DISABLED_SYMBOLS
                    await db.execute("UPDATE symbols_config SET is_active = ? WHERE symbol = ?", (want, sym))
                    await db.execute(
                        "UPDATE symbol_strategies SET is_active = ? WHERE symbol = ? AND strategy_name = 'PST-RangeBreaker'",
                        (want_rb, sym))
                    await db.execute(
                        "UPDATE symbol_strategies SET is_active = ? WHERE symbol = ? AND strategy_name = 'PST-PrecisionScalping'",
                        (want_ps, sym))
                await db.execute(
                    "INSERT OR REPLACE INTO bot_config (key, value) VALUES ('universe_migration_applied', ?)",
                    (UNIVERSE_VERSION,))
                logger.info(f"🔄 [UNIVERSE] Universo sincronizado a enabled_by_default ({UNIVERSE_VERSION}): "
                            f"{sorted(enabled_by_default)}")

            # --- MIGRACIÓN DE FILTER_PROFILE: adx_ok 18→21 (one-time, versionada) ---
            # El backfill de más arriba solo dispara si el filter_profile actual coincide
            # EXACTO con un string "viejo" conocido — no alcanza a símbolos con override propio
            # (US30.cash/ETHUSD ya tienen su clave extra). Se fija el filter_profile EXACTO
            # (grupo+override) para TODOS los símbolos de la siembra, una sola vez por versión;
            # toggles/ediciones manuales POSTERIORES a esta migración persisten.
            PROFILE_MIGRATION_VERSION = "2026-07-09-a"  # ETHUSD entry_threshold 72->80 (juez fiel 20d: -28.95R->-2.91R)
            async with db.execute("SELECT value FROM bot_config WHERE key='filter_profile_migration_applied'") as cur:
                _prof_row = await cur.fetchone()
            if not _prof_row or _prof_row[0] != PROFILE_MIGRATION_VERSION:
                for sym, stype in master_config:
                    g = GROUP_DEFAULTS.get(stype, _SCALP_FALLBACK)
                    merged = json.loads(g["filter_profile"])
                    sym_override = SYMBOL_FILTER_OVERRIDES.get(sym)
                    if sym_override:
                        merged.update(sym_override)
                    await db.execute(
                        "UPDATE symbol_strategies SET filter_profile=? WHERE symbol=? AND strategy_name='PST-PrecisionScalping'",
                        (json.dumps(merged), sym))
                await db.execute(
                    "INSERT OR REPLACE INTO bot_config (key, value) VALUES ('filter_profile_migration_applied', ?)",
                    (PROFILE_MIGRATION_VERSION,))
                logger.info(f"🔄 [FILTER_PROFILE] Migración {PROFILE_MIGRATION_VERSION} aplicada a todo el universo")

            await db.commit()
            logger.info(f"✅ Base de Datos Inicializada y Sembrada (MAESTRA v3.0) en {self.db_path}")

    async def add_log(self, level, message, source="SYSTEM"):
        """Añade un mensaje de log a la base de datos."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                # timestamp explícito con reloj LOCAL: el default CURRENT_TIMESTAMP de
                # SQLite es UTC y descuadraba system_logs (-2h) vs trades/signal_logs.
                await db.execute("""
                    INSERT INTO system_logs (timestamp, level, message, source)
                    VALUES (?, ?, ?, ?)
                """, (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), level, message, source))
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

    async def save_user_level(self, symbol, price, ltype, label=None, price2=None, time2=None, time1=None, time1_ts=None, time2_ts=None, mode='BOTH'):
        """Guarda o actualiza un nivel manual (Line u Horizontal)."""
        async with aiosqlite.connect(self.db_path, timeout=30) as db:
            if label and label.startswith('FIXED_'):
                 # Check if update instead of insert
                 await db.execute("DELETE FROM user_levels WHERE symbol = ? AND label = ?", (symbol, label))

            await db.execute("""
                INSERT INTO user_levels (symbol, price, type, label, price2, time2, time1, time1_ts, time2_ts, mode) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (symbol, float(price), ltype, label, price2, time2, time1, time1_ts, time2_ts, mode))
            await db.commit()

    async def save_trend_lines(self, symbol: str, lines: list):
        """Guarda un set completo de líneas de tendencia con reintentos para evitar el bloqueo de la DB."""
        for attempt in range(10):
            try:
                async with aiosqlite.connect(self.db_path, timeout=60) as conn:
                    # Purgar antiguas
                    await conn.execute("DELETE FROM user_levels WHERE symbol = ? AND type = 'DIAGONAL'", (symbol,))
                    # Insertar nuevas
                    for line in lines:
                        t1 = int(line.get('p1', {}).get('time', 0) or 0)
                        t2 = int(line.get('p2', {}).get('time', 0) or 0)
                        p1 = float(line.get('p1', {}).get('price', 0))
                        p2 = float(line.get('p2', {}).get('price', 0))
                        mode = line.get('mode', 'BOTH')
                        
                        await conn.execute("""
                            INSERT INTO user_levels (symbol, price, type, label, price2, time1_ts, time2_ts, mode, is_active) 
                            VALUES (?, ?, 'DIAGONAL', 'USER_LINE', ?, ?, ?, ?, 1)
                        """, (symbol, p1, p2, t1, t2, mode))
                    await conn.commit()
                return True
            except Exception as e:
                logger.error(f"❌ Error en save_trend_lines (Intento {attempt+1}) para {symbol}: {e}")
                if ("locked" in str(e).lower() or "busy" in str(e).lower()) and attempt < 9:
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                raise e
        return False

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

    async def log_signal(self, symbol, regime, strategy, sig_type, score, price, blocked_reason=None):
        """Registra una señal para análisis de métricas con reintentos."""
        for attempt in range(5):
            try:
                async with aiosqlite.connect(self.db_path, timeout=30) as db:
                    await db.execute('''
                        INSERT INTO signal_logs (timestamp, symbol, regime, strategy, signal_type, score, price, blocked_reason)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (datetime.now(), symbol, regime, strategy, sig_type, score, price, blocked_reason))
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
                    WHERE ticket = ? AND (price_out = 0 OR price_out IS NULL)
                ''', (price_out, profit, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), ticket))
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
        """Verifica si el trade está abierto (price_out = 0 o NULL)."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                async with db.execute("SELECT 1 FROM trades WHERE ticket = ? AND (price_out = 0 OR price_out IS NULL)", (ticket,)) as cursor:
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

    async def get_strategy_weekly_pnl(self, strategy_name: str) -> float:
        """Retorna el PnL total de la estrategia en los últimos 7 días."""
        try:
            from datetime import datetime, timedelta
            since = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
            async with aiosqlite.connect(self.db_path, timeout=10) as db:
                async with db.execute(
                    "SELECT COALESCE(SUM(profit), 0) FROM trades "
                    "WHERE strategy_name = ? AND time_out >= ?",
                    (strategy_name, since),
                ) as cursor:
                    row = await cursor.fetchone()
                    return float(row[0]) if row else 0.0
        except Exception as e:
            logger.error(f"❌ Error get_strategy_weekly_pnl({strategy_name}): {e}")
            return 0.0

    async def is_strategy_paused(self, strategy_name: str) -> bool:
        """Comprueba si una estrategia está pausada por drawdown semanal."""
        key = f"strategy_paused_{strategy_name}"
        val = await self.get_config(key, default="false")
        return val.lower() == "true"

    async def set_strategy_paused(self, strategy_name: str, paused: bool):
        """Activa o desactiva la pausa de drawdown semanal para una estrategia."""
        key = f"strategy_paused_{strategy_name}"
        await self.update_config(key, "true" if paused else "false")
        logger.warning(
            f"{'⏸️ PAUSA' if paused else '▶️ REACTIVACIÓN'} de estrategia por drawdown semanal: {strategy_name}"
        )

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

    async def set_symbol_strategy(self, symbol, strategy_name, is_active=None, risk_mode=None, risk_value=None, sl_mult=None, tp_mult=None, score_threshold=None, use_trailing=None, use_breakeven=None, be_mult=None, ts_mult=None, min_rr=None, filter_profile=None):
        """Actualiza la configuración de una estrategia específica para un símbolo."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                # Primero verificar si existe
                async with db.execute("SELECT 1 FROM symbol_strategies WHERE symbol = ? AND strategy_name = ?", (symbol, strategy_name)) as cursor:
                    exists = await cursor.fetchone()

                if not exists:
                    await db.execute("""
                        INSERT INTO symbol_strategies (symbol, strategy_name, is_active, risk_mode, risk_value, sl_mult, tp_mult, score_threshold, use_trailing, use_breakeven, be_mult, ts_mult, min_rr, filter_profile)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        symbol, strategy_name,
                        1 if is_active is None or is_active else 0,
                        risk_mode, risk_value, sl_mult, tp_mult, score_threshold,
                        1 if use_trailing else 0 if use_trailing is not None else (0 if strategy_name == 'PST-PrecisionScalping' else 1),
                        1 if use_breakeven else 0 if use_breakeven is not None else 1,
                        be_mult if be_mult is not None else (3.5 if strategy_name == 'PST-PrecisionScalping' else 2.0),
                        ts_mult if ts_mult is not None else 2.5,
                        min_rr if min_rr is not None else 1.5,
                        filter_profile
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
                    if filter_profile is not None: fields.append("filter_profile = ?"); vals.append(filter_profile)
                    
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

    async def get_all_time_profit_by_symbol(self) -> dict:
        """Obtiene el beneficio total acumulado histórico por cada símbolo."""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("""
                    SELECT symbol, SUM(profit) as total_profit 
                    FROM trades 
                    WHERE price_out > 0 
                    GROUP BY symbol
                """) as cursor:
                    rows = await cursor.fetchall()
                    return {r['symbol'].upper(): r['total_profit'] for r in rows}
        except Exception as e:
            logger.error(f"❌ Error get_all_time_profit_by_symbol: {e}")
            return {}

    async def get_today_profit_by_symbol(self) -> dict:
        """Obtiene el beneficio acumulado solo desde las 00:00:00 de hoy por símbolo."""
        try:
            from datetime import datetime, time
            today_str = datetime.combine(datetime.now().date(), time.min).strftime('%Y-%m-%d %H:%M:%S')
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("""
                    SELECT symbol, SUM(profit) as total_profit 
                    FROM trades 
                    WHERE price_out > 0 AND time_out >= ? 
                    GROUP BY symbol
                """, (today_str,)) as cursor:
                    rows = await cursor.fetchall()
                    return {r['symbol'].upper(): r['total_profit'] for r in rows}
        except Exception as e:
            logger.error(f"❌ Error get_today_profit_by_symbol: {e}")
            return {}

    async def sync_mt5_history(self, deals, days_back=2):
        """
        Sincroniza deals de MT5 con la tabla trades. 
        Maneja trades del bot (por position_id) y externos/manuales.
        """
        if not deals:
            return 0, []

        count_synced = 0
        count_imported = 0
        closed_bot_trades = []  # [(symbol, pnl)] para que el caller registre cooldowns

        try:
            from datetime import datetime, timezone
            import MetaTrader5 as mt5
            
            async with aiosqlite.connect(self.db_path, timeout=30) as db:
                for d in deals:
                    # 1. Filtrar solo deals de SALIDA (Cierres totales o parciales)
                    # ENTRY_OUT=1, ENTRY_INOUT=2 (reversión), ENTRY_OUT_BY=3 (cierre por contra)
                    if d.entry not in [1, 2, 3]: 
                        continue
                    
                    # d.time codifica la hora de pared del SERVIDOR del bróker como epoch
                    # "UTC"; fromtimestamp() local le añadía el offset local encima
                    # (time_out salía ~+3h vs reloj local). Conversión sin doble salto:
                    time_out_dt = datetime.fromtimestamp(d.time, tz=timezone.utc).replace(tzinfo=None)
                    time_out_str = time_out_dt.strftime('%Y-%m-%d %H:%M:%S')
                    total_pnl = d.profit + d.swap + d.commission
                    
                    # PASO 1: ¿Es un trade del BOT? (Buscamos ticket = position_id)
                    # El bot guarda apertura con ticket = position_id
                    async with db.execute(
                        "SELECT id FROM trades WHERE ticket = ? AND (price_out IS NULL OR price_out = 0)",
                        (d.position_id,)
                    ) as cur:
                        bot_trade = await cur.fetchone()
                    
                    if bot_trade:
                        # Es un trade del bot → ACTUALIZAR con datos de cierre.
                        # time_out con reloj LOCAL (el mismo que time_in): el sync corre
                        # cada 60s, así que en operación normal el error es ≤ ~1 min.
                        await db.execute("""
                            UPDATE trades SET price_out = ?, profit = ?, time_out = ?
                            WHERE id = ?
                        """, (d.price, float(total_pnl), datetime.now().strftime('%Y-%m-%d %H:%M:%S'), bot_trade[0]))
                        await db.commit()
                        count_synced += 1
                        closed_bot_trades.append((d.symbol, float(total_pnl)))
                        logger.info(f"✅ [SYNC-BOT] Cerrado {d.symbol} (Ticket {d.position_id}) | PnL: {total_pnl:.2f}")
                        continue
                    
                    # PASO 2: ¿Ya importamos este deal específico anteriormente?
                    # Buscamos en el ticket si es un trade de AUTO_SYNC ya existente
                    # O si por casualidad ya se cerró un bot trade con este position_id
                    async with db.execute("SELECT 1 FROM trades WHERE ticket = ? OR (ticket = ? AND price_out > 0)", (d.ticket, d.position_id)) as cur:
                        already_exists = await cur.fetchone()
                    
                    if already_exists:
                        continue
                    
                    # PASO 3: Trade externo/manual → Importar buscando su apertura para ser PRO
                    trade_type = "BUY" if d.type == 1 else "SELL" # El deal OUT tiene tipo opuesto a la posición
                    
                    # Intentar buscar el deal de apertura (ENTRY_IN) para tener price_in y time_in reales
                    time_in_str = time_out_str
                    price_in = 0.0
                    
                    try:
                        import time as _time
                        # Buscamos deals de entrada para esta posición
                        # Ampliamos el rango a 30 días para la apertura
                        h_end = d.time + 10
                        h_start = d.time - (3600 * 24 * 30)
                        pos_deals = mt5.history_deals_get(h_start, h_end, position=d.position_id)
                        if pos_deals:
                            for pd in pos_deals:
                                if pd.entry == 0: # ENTRY_IN
                                    price_in = pd.price
                                    # Hora de pared del servidor, sin doble desplazamiento
                                    time_in_str = datetime.fromtimestamp(pd.time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                                    break
                    except:
                        pass # Fallback a time_out si falla la búsqueda

                    await db.execute("""
                        INSERT INTO trades (symbol, type, volume, price_in, price_out, profit, time_in, time_out, ticket, strategy_name, is_partial_closed)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (d.symbol, trade_type, d.volume, price_in, d.price, float(total_pnl), time_in_str, time_out_str, d.ticket, "AUTO_SYNC", 0))
                    await db.commit()
                    count_imported += 1
                    logger.info(f"📥 [SYNC-EXT] Importado manual/externo: {d.symbol} (ID {d.position_id}) | PnL: {total_pnl:.2f}")
            
            return count_synced + count_imported, closed_bot_trades
        except Exception as e:
            logger.error(f"❌ Error sync_mt5_history: {e}")
            return 0, closed_bot_trades
