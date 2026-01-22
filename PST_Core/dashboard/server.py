from flask import Flask, render_template, jsonify, request
import sqlite3
import os
import MetaTrader5 as mt5
import pandas as pd
import pandas_ta as ta
import numpy as np
from datetime import datetime
from PST_Core.portfolio.manager import PortfolioManager
from PST_Core.utils.tech_utils import calculate_channel_boundary, calculate_manual_score
from ..strategies.pst_channel_master import PSTChannelMaster
from ..strategies.pst_rsi_equities import PSTRSIEquities
from ..strategies.pst_ema_flow import PSTEMAFlow
from PST_Core.models.classifier import RegimeMode
import logging
import asyncio # Added for asyncio.run

# Configurar Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger("DashboardServer")

import logging
# Silence Flask Access Logs
logging.getLogger('werkzeug').setLevel(logging.ERROR)

app = Flask(__name__)
portfolio = PortfolioManager(max_risk_pct=0.5) # Riesgo manual prudente del 0.5%
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "pst_trading.db")

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

# Inicializar MT5 en este proceso para poder leer la cuenta
if not mt5.initialize():
    print("⚠️ [DASHBOARD] No se pudo inicializar MT5 para lectura de cuenta.")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status')
def get_status():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 1. Obtener estados de régimen actuales por símbolo (solo activos)
        cursor.execute('''
            SELECT r1.symbol, r1.mode, r1.adx, r1.tech_data
            FROM regime_history r1
            INNER JOIN (
                SELECT symbol, MAX(id) as max_id
                FROM regime_history
                GROUP BY symbol
            ) r2 ON r1.id = r2.max_id
            JOIN symbols_config s ON r1.symbol = s.symbol
            WHERE s.is_active = 1
        ''')
        regimes_raw = [dict(row) for row in cursor.fetchall()]
        
        # Enriquecer con precio actual
        regimes = []
        for r in regimes_raw:
            # Asegurar que el símbolo esté visible para recibir ticks
            mt5.symbol_select(r['symbol'], True)
            tick = mt5.symbol_info_tick(r['symbol'])
            # Usar Bid si Last es 0 (común en brokers de CFDs)
            r['price'] = tick.bid if tick and tick.bid > 0 else (tick.last if tick else 0.0)
            
            # Detectar si el mercado está abierto
            s_info = mt5.symbol_info(r['symbol'])
            r['market_open'] = (s_info.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL) if s_info else False
            
            # CRITICAL FIX: Parsear tech_data si existe
            if r.get('tech_data'):
                try:
                    import json
                    r['tech_data'] = json.loads(r['tech_data'])
                except:
                    r['tech_data'] = {}
            else:
                r['tech_data'] = {}
            
            # --- ESTRATEGIA MANUAL SCORE INJECTION ---
            r['manual_score'] = 0
            r['manual_action'] = "NEUTRAL"
            r['manual_desc'] = ""
            r['manual_strat_name'] = "Estrategia Manual (Soporte/Resistencia)"
            r['manual_strat_desc'] = "Analiza roturas y rebotes en niveles clave definidos manualmente. Valida las señales con lecturas de RSI y Volumen."
            
            try:
                # 1. Fetch Active Manual Levels
                cursor.execute("SELECT * FROM user_levels WHERE symbol = ? AND is_active = 1", (r['symbol'],))
                levels = [dict(row) for row in cursor.fetchall()]
                
                if levels:
                    best_score = 0
                    best_action = "NEUTRAL"
                    best_desc = ""
                    
                    # Extract Technical context for Manual Score
                    rsi = r['tech_data'].get('mtf', {}).get('m5', {}).get('rsi', r.get('rsi', 50))
                    vol_rel = r['tech_data'].get('mtf', {}).get('m5', {}).get('vol', 1.0)
                    vol_val = vol_rel
                    vol_ma = 1.0 # vol_rel is already compared vs 1.0 (mean)
                    price = r['price']

                    for lvl in levels:
                        # 1. PRICE INTERPOLATION (Trendlines)
                        lvl_price = lvl['price']
                        if lvl.get('price2') and lvl.get('time1') and lvl.get('time2'):
                            try:
                                from dateutil import parser
                                t1_dt = parser.parse(lvl['time1'])
                                t2_dt = parser.parse(lvl['time2'])
                                t1_ts = t1_dt.timestamp()
                                t2_ts = t2_dt.timestamp()
                                
                                # SYNC WITH STRATEGY & MARKET TIME
                                # Use the timestamp of the last TICK/QUOTE for this symbol.
                                tick_info = mt5.symbol_info_tick(r['symbol'])
                                cur_ts = float(tick_info.time if tick_info else datetime.now().timestamp())
                                
                                p1 = lvl['price']
                                p2 = lvl['price2']
                                
                                if abs(t2_ts - t1_ts) > 0:
                                    m = (p2 - p1) / (t2_ts - t1_ts)
                                    lvl_price = p1 + m * (cur_ts - t1_ts)
                            except: pass
                        
                        # USE UNIFIED SCORING
                        strat_score, action, desc_parts = calculate_manual_score(
                            price=price,
                            lvl_price=lvl_price,
                            l_type=lvl['type'],
                            rsi=rsi,
                            vol_val=vol_val,
                            vol_ma=vol_ma
                        )
                        
                        score = round(strat_score)
                        
                        if score > best_score:
                            best_score = score
                            best_action = action
                            best_desc = ", ".join(desc_parts)
                    
                    r['manual_score'] = best_score
                    r['manual_action'] = best_action
                    r['manual_desc'] = best_desc
                    
            except Exception as e:
                logger.error(f"Error calculating manual score for {r['symbol']}: {e}")

            regimes.append(r)
        
        # 2. Obtener últimas 15 operaciones CERRADAS
        cursor.execute('''
            SELECT time_out as timestamp, symbol, regime_at_entry as mode, 
                   strategy_name as strategy, type, price_in, price_out, profit 
            FROM trades 
            WHERE price_out > 0
            ORDER BY id DESC LIMIT 15
        ''')
        history = [dict(row) for row in cursor.fetchall()]
        
        # 3. Datos de la cuenta (Reales desde MT5)
        acc = mt5.account_info()
        if acc is None:
            # Reintento de inicialización si se perdió conexión
            mt5.initialize()
            acc = mt5.account_info()

        account = {
            "drawdown": max(0, (1 - (acc.equity / acc.balance)) * 100) if acc and acc.balance > 0 else 0,
            "balance": acc.balance if acc else 0,
            "equity": acc.equity if acc else 0,
            "status": "ONLINE" if acc else "MT5 OFFLINE"
        }
        
        # 4. Obtener Posiciones Activas reales
        pts = mt5.positions_get()
        positions = []
        if pts:
            for p in pts:
                positions.append({
                    "ticket": p.ticket,
                    "symbol": p.symbol,
                    "type": "BUY" if p.type == 0 else "SELL",
                    "volume": p.volume,
                    "price_open": p.price_open,
                    "price_current": p.price_current,
                    "sl": p.sl,
                    "tp": p.tp,
                    "profit": p.profit,
                    "commission": getattr(p, 'commission', 0.0),
                    "swap": getattr(p, 'swap', 0.0),
                    "comment": p.comment
                })

        conn.close()

        # Limpiar datos para JSON (reemplazar NaNs por None)
        def clean_data(obj):
            if isinstance(obj, list):
                return [clean_data(i) for i in obj]
            elif isinstance(obj, dict):
                return {k: clean_data(v) for k, v in obj.items()}
            elif isinstance(obj, float) and np.isnan(obj):
                return None
            return obj

        response_data = clean_data({
            "regimes": regimes,
            "history": history,
            "account": account,
            "positions": positions
        })

        return jsonify(response_data)
    except Exception as e:
        print(f"❌ Error en /api/status: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/config', methods=['GET', 'POST'])
def bot_config():
    from PST_Core.models.database import PSTDatabase
    import asyncio
    db = PSTDatabase()
    
    if request.method == 'POST':
        data = request.json
        key = data.get('key')
        value = data.get('value')
        if not key or not value:
            return jsonify({"error": "Missing key or value"}), 400
        
        success = asyncio.run(db.update_config(key, value))
        return jsonify({"success": success})
    
    # GET
    key = request.args.get('key', 'trading_mode')
    value = asyncio.run(db.get_config(key))
    return jsonify({"key": key, "value": value})

@app.route('/api/config/symbols', methods=['GET'])
def get_config_symbols():
    from PST_Core.models.database import PSTDatabase
    import asyncio
    db = PSTDatabase()
    symbols_data = asyncio.run(db.get_all_symbols_config())
    return jsonify(symbols_data)

@app.route('/api/config/symbols/toggle', methods=['POST'])
def toggle_symbol_active():
    from PST_Core.models.database import PSTDatabase
    import asyncio
    db = PSTDatabase()
    data = request.json
    symbol = data.get('symbol')
    active = data.get('active')
    
    if symbol is None or active is None:
        return jsonify({"error": "Missing symbol or active status"}), 400
    
    success = asyncio.run(db.set_symbol_active(symbol, active))
    return jsonify({"success": success})

# Removed redundant get_dashboard_data and stats duplicates
@app.route('/api/stats/advanced')
def get_advanced_stats():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 1. Estadísticas Generales
        cursor.execute('''
            SELECT 
                COUNT(*) as total_trades,
                SUM(CASE WHEN profit >= 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN profit < 0 THEN 1 ELSE 0 END) as losses,
                SUM(profit) as net_profit,
                SUM(CASE WHEN profit > 0 THEN profit ELSE 0 END) as gross_profit,
                SUM(CASE WHEN profit < 0 THEN ABS(profit) ELSE 0 END) as gross_loss
            FROM trades 
            WHERE price_out > 0
        ''')
        row = cursor.fetchone()
        
        total_trades = row['total_trades'] or 0
        wins = row['wins'] or 0
        losses = row['losses'] or 0
        net_profit = row['net_profit'] or 0.0
        gross_profit = row['gross_profit'] or 0.0
        gross_loss = row['gross_loss'] or 0.0
        
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

        # 2. Ranking de Símbolos (Mejor y Peor)
        cursor.execute('''
            SELECT 
                symbol, 
                COUNT(*) as count,
                SUM(profit) as total_pnl,
                SUM(CASE WHEN profit >= 0 THEN 1 ELSE 0 END) as wins
            FROM trades 
            WHERE price_out > 0 
            GROUP BY symbol 
            ORDER BY total_pnl DESC
        ''')
        symbol_rows = [dict(r) for r in cursor.fetchall()]
        
        # Procesar símbolos para el frontend
        symbols_data = []
        for s in symbol_rows:
            s_win_rate = (s['wins'] / s['count'] * 100) if s['count'] > 0 else 0
            symbols_data.append({
                "symbol": s['symbol'],
                "pnl": s['total_pnl'],
                "trades": s['count'],
                "win_rate": s_win_rate
            })

        best_symbol = symbols_data[0] if symbols_data else None
        worst_symbol = symbols_data[-1] if symbols_data else None

        conn.close()
        return jsonify({
            "success": True,
            "general": {
                "total_trades": total_trades,
                "wins": wins,
                "losses": losses,
                "win_rate": round(win_rate, 2),
                "net_profit": round(net_profit, 2),
                "profit_factor": round(profit_factor, 2),
                "gross_profit": round(gross_profit, 2),
                "gross_loss": round(gross_loss, 2)
            },
            "best_symbol": best_symbol,
            "worst_symbol": worst_symbol,
            "symbols": symbols_data
        })
    except Exception as e:
        logger.error(f"❌ Error en stats advanced: {e}")
        return jsonify({"success": False, "error": str(e)})
        
    except Exception as e:
        logger.error(f"❌ Error en stats advanced: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/reset', methods=['POST'])
def reset_data():
    from PST_Core.models.database import PSTDatabase
    import asyncio
    db = PSTDatabase()
    success = asyncio.run(db.clear_all_logs())
    return jsonify({"success": success})

def calculate_trendlines(df):
    # 1. Macro Canal (Todo el historial disponible, e.g. 2000 velas)
    u_macro, l_macro, _ = calculate_channel_boundary(df, window=10, projection=60)
    
    # 2. Tactical Canal (Estructura táctica ampliada, e.g. ~4 días)
    # 1200 velas en M5 = ~100 horas. Ventana 30 para detectar pivotes de onda real.
    df_local = df.tail(1200).reset_index(drop=True)
    u_local, l_local, _ = calculate_channel_boundary(df_local, window=30, projection=200, recent_pivots=10)
    
    return u_macro, l_macro, u_local, l_local

@app.route('/api/chart/<symbol>')
def get_chart_data(symbol):
    try:
        # 0. Obtener Timeframe de la request (default: M5)
        tf_str = request.args.get('tf', 'M5')
        
        # Mapping de timeframes
        tf_map = {
            'M1': mt5.TIMEFRAME_M1,
            'M5': mt5.TIMEFRAME_M5,
            'M15': mt5.TIMEFRAME_M15,
            'M30': mt5.TIMEFRAME_M30,
            'H1': mt5.TIMEFRAME_H1,
            'H4': mt5.TIMEFRAME_H4,
            'D1': mt5.TIMEFRAME_D1
        }
        
        selected_tf = tf_map.get(tf_str, mt5.TIMEFRAME_M5)

        # 1. Intentar obtener posición activa
        pos = mt5.positions_get(symbol=symbol)
        trade_info = None
        if pos:
            p = pos[0]
            trade_info = {
                "price": p.price_open,
                "sl": p.sl,
                "tp": p.tp,
                "profit": p.profit,
                "type": "BUY" if p.type == 0 else "SELL"
            }

        # 2. Obtener velas reales para el gráfico
        num_candles = 2000 
        rates = mt5.copy_rates_from_pos(symbol, selected_tf, 0, num_candles)
        
        # --- MTF FETCH FOR STRATEGY HUD (CRITICAL: M5 must be REAL M5) ---
        rates_m1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 500)
        rates_m5_real = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 1000) # Re-fetch M5 specifically
        rates_m15 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M15, 0, 500)
        rates_h1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 500)
 
        # Helper to format DataFrames with datetime index
        def format_df(rates_in):
            if rates_in is None or len(rates_in) == 0: return None
            df_fmt = pd.DataFrame(rates_in)
            df_fmt['time'] = pd.to_datetime(df_fmt['time'], unit='s')
            df_fmt.set_index('time', inplace=True)
            return df_fmt
 
        df = format_df(rates)
        df_m1 = format_df(rates_m1)
        df_m5_real = format_df(rates_m5_real)
        df_m15 = format_df(rates_m15)
        df_h1 = format_df(rates_h1)
 
        if df is None:
            return jsonify({"error": "No data"}), 404
 
        mtf_data = {
            'm1': df_m1,
            'm5': df_m5_real if df_m5_real is not None else df, # Use Real M5 or fallback
            'm15': df_m15,
            'h1': df_h1
        }
        # logger.debug(f"DEBUG: MTF OK. M1:{len(df_m1) if df_m1 is not None else 0} M5:{len(df)} H1:{len(df_h1) if df_h1 is not None else 0}")

        # Calcular Indicadores
        df['ema_21'] = ta.ema(df['close'], length=21)
        df['ema_50'] = ta.ema(df['close'], length=50) 
        
        # Calcular RSIs adicionales para el HUD (M1, M5, H1)
        rsi_m1 = 50
        rsi_m5 = 50
        rsi_h1 = 50
        
        try:
             # RSI M1
             if df_m1 is not None and len(df_m1) > 20:
                 rsi_s = ta.rsi(df_m1['close'], length=14)
                 if rsi_s is not None: rsi_m1 = rsi_s.iloc[-1]
                 
             # RSI M5 (Base DF)
             if len(df) > 20:
                 rsi_s = ta.rsi(df['close'], length=14)
                 if rsi_s is not None: rsi_m5 = rsi_s.iloc[-1]
                 
             # RSI H1
             if df_h1 is not None and len(df_h1) > 20:
                 rsi_s = ta.rsi(df_h1['close'], length=14)
                 if rsi_s is not None: rsi_h1 = rsi_s.iloc[-1]
                 
        except Exception as e:
            logger.debug(f"DEBUG ERROR HUD RSIs: {e}")
        
        # logger.debug("DEBUG: Indicators OK")
        
        # Calcular Trendlines (Canales)
        # Prepare Response Objects
        u_macro, l_macro, u_local, l_local = [], [], [], []
        strategy_analysis = {}
        # Pre-init variables to avoid UnboundLocalError if try block fails early
        rsi_val = rsi_m5
        vol_val = df['tick_volume'].iloc[-1] if 'tick_volume' in df.columns else 0
        vol_ma = df['tick_volume'].rolling(20).mean().iloc[-1] if 'tick_volume' in df.columns else 1
        # REMOVED: df = pd.DataFrame() - caused overwrite of fetched data

        try:
             u_macro, l_macro, u_local, l_local = calculate_trendlines(df)
             # logger.debug(f"DEBUG: Trendlines OK. Macro: {len(u_macro)}, Local: {len(u_local)}")
        except Exception as e:
             logger.debug(f"DEBUG ERROR TRENDLINES: {e}")
             u_macro, l_macro, u_local, l_local = [], [], [], []

        # Convert Time to String for Frontend (Using a copy of index to avoid breaking strategies)
        df_display = df.copy()
        try:
            df_display.reset_index(inplace=True)
            df_display['time'] = df_display['time'].dt.strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
             logger.debug(f"DEBUG ERROR TIME CONV: {e}")

        # Format Trendline Times
        for p in u_macro + l_macro + u_local + l_local:
             if isinstance(p['time'], (int, float, np.int64, np.float64)): 
                 p['time'] = str(datetime.fromtimestamp(p['time']))

        # REMOVED: df = df.replace({np.nan: None}) - Breaks strategies demanding numeric types (NaN)
        # We will handle NaN -> None during JSON serialization only
        
        # 4. Calcular Señales de Estrategia para el HUD
        strategy_analysis = {}
        try:
            # Detectar Régimen para el HUD
            from PST_Core.models.classifier import RegimeClassifier
            classifier = RegimeClassifier()
            mode_str = "TREND" # Default
            
            # Match orchestrator classification logic
            df_regime = df_h1 if (df_h1 is not None and len(df_h1) >= 14) else df
            
            if len(df_regime) >= 14:
                mode, adx = classifier.classify(df_regime)
                mode_str = mode
            
            # Simulamos el objeto regime
            class MockRegime:
                def __init__(self, mode): self.mode = mode
            regime_obj = MockRegime(mode_str) 
            
            # Lista de estrategias por regime (Sync con orchestrator)
            # Enable ALL strategies for display (User Request: Show all even if 0)
            strat_instances = [
                PSTRSIEquities(),
                PSTEMAFlow()
            ]
            
            # Inyectar nombres de estrategias activas
            active_strats_list = [type(s).__name__.replace('PST','') for s in strat_instances] + ["ChannelMaster"]
            logger.debug(f"📊 HUD Analysis for {symbol} | Mode: {mode_str} | Strats: {active_strats_list}")

            # Recuperar niveles manuales del usuario para este símbolo
            conn_lvl = sqlite3.connect(DB_PATH)
            conn_lvl.row_factory = sqlite3.Row
            cursor_lvl = conn_lvl.cursor()
            cursor_lvl.execute("SELECT * FROM user_levels WHERE symbol = ? AND is_active = 1", (symbol,))
            user_levels = [dict(row) for row in cursor_lvl.fetchall()]
            
            # ENSURE SOURCE TAG EXISTS
            for ul in user_levels:
                if 'source' not in ul or not ul['source']:
                    ul['source'] = 'MANUAL'
            cursor_lvl.execute("SELECT * FROM channel_config WHERE symbol = ?", (symbol,))
            row_conf = cursor_lvl.fetchone()
            chan_config = dict(row_conf) if row_conf else {}
            
            # DEBUG SERVER
            # logger.debug(f"DEBUG SERVER [{symbol}]: Manual Levels found: {len(user_levels)}. Config: {chan_config}")
            
            # Get Live Tick Time for Trendline Sync
            tick = mt5.symbol_info_tick(symbol)
            cur_time_live = tick.time if tick else datetime.now().timestamp()
            cur_price_live = tick.bid if tick else df['close'].iloc[-1]
            
            conn_lvl.close()

            # Ejecutar estrategia Channel Master
            strat = PSTChannelMaster()
            user_levels_input = {'levels': user_levels, 'config': chan_config, 'symbol': symbol, 'current_time': cur_time_live, 'current_price': cur_price_live}
            signal_res = asyncio.run(strat.calculate_signal(mtf_data, regime_obj, user_levels=user_levels_input))
            strategy_analysis = signal_res.get('metadata', {})
            logger.debug(f"DEBUG SERVER ANALYSIS [Raw keys]: {list(strategy_analysis.keys())} - Levels: {len(strategy_analysis.get('all_levels_data', [])) if 'all_levels_data' in strategy_analysis else 'MISSING'}")
            strategy_analysis['score'] = signal_res.get('score', 0)
            strategy_analysis['entry'] = signal_res.get('entry', 0)
            
            # --- AUTO STRAT META INJECTION ---
            strategy_analysis['strat_meta'] = {
                "name": "Channel Master + RSI EQ",
                "desc": "Estrategia híbrida que combina canales de regresión dinámica con niveles y RSI Multi-Timeframe.",
                "logic": "Análisis de tendencias en Canales de Regresión + RSI en M1/M5 para entradas precisas."
            }
            # INJECTION OF MTF RSI FOR HUD
            strategy_analysis['rsi_mtf'] = {
                "m1": round(rsi_m1, 1),
                "m5": round(rsi_m5, 1),
                "h1": round(rsi_h1, 1)
            }
            
            # --- AGREGAR SUB-ESTRATEGIAS (Range, Momentum, etc.) ---
            factors = {}
            max_sub_score = 0
            # TRANSLATION MAPS (User Request for clarity)
            STRAT_TRANS = {
                "PST-Range-Sniper": "Francotirador (Rango)",
                "PST-Trend-Elite": "Tendencia Élite",
                "PST-Trend-Pullback": "Tendencia (Pullback)",
                "PST-Vol-Breakout": "Ruptura Volatilidad",
                "PST-Session-Master": "Maestro de Sesión (ORB)",
                "PST-Divergence": "Cazador Divergencias",
                "PST-News-Fade": "Contra-Noticia (Fade)",
                "PSTEmaFlow": "Flujo EMA (Tendencia)",
                "PST-EMA-Flow": "Flujo EMA (Tendencia)",
                "PSTRSIEquities": "RSI Equities (Multi-Asset)",
                "PST-RSI-Equities": "RSI Equities (Multi-Asset)"
            }
            KEY_TRANS = {
                "H1 Trend": "Tendencia H1",
                "M15 Momentum": "Impulso M15",
                "M15 Align": "Alineación M15", 
                "M5 VWAP": "VWAP M5",
                "Trend Structure": "Estructura Tendencia",
                "Value Zone": "Zona de Valor",
                "RSI Pullback": "Retroceso RSI",
                "ADX Power": "Potencia ADX",
                "Squeeze": "Compresión",
                "Breakout": "Ruptura",
                "RSI Extreme": "RSI Extremo",
                "RSI Zone": "Zona RSI",
                "Bollinger Touch": "Toque Bandas",
                "Candle Pattern": "Patrón Velas",
                "ORB Status": "Estado Rango Apertura",
                "Trend Align": "Alineación Tendencia",
                "Pattern": "Patrón Divergencia",
                "RSI Room": "Espacio en RSI",
                "Spike": "Velón Volatilidad",
                "Extension": "Extensión vs Media",
                "Rejection": "Rechazo (Mecha)"
            }

            best_s_name = "PST Strategy Hub"
            # --- STRUCTURED DATA FOR FRONTEND (Grouped View) ---
            grouped_strategies = []
            
            # 1. Sub-Strategies
            for s in strat_instances:
                try:
                    s_res = asyncio.run(s.calculate_signal(mtf_data, mode_str, user_levels=user_levels_input))
                    s_score = s_res.get('score', 0)
                    s_meta = s_res.get('metadata', {})
                    raw_s_name = str(getattr(s, 'STRATEGY_NAME', type(s).__name__))
                    s_name = STRAT_TRANS.get(raw_s_name, raw_s_name)
                    
                    if s_name == "None": s_name = "PST Strategy Hub"
                    
                    if s_score > max_sub_score:
                        max_sub_score = s_score
                        best_s_name = s_name
                    
                    status = "Neutral"
                    if s_score >= 70: status = "¡ACCIÓN!"
                    elif s_score >= 40: status = "Vigilar"
                    
                    # Collecting Factors (Filtered per user request)
                    s_factors = []
                    for b_key, b_val in s_meta.get('score_breakdown', {}).items():
                         val_str = str(b_val)
                         # Show only if it adds/subtracts points or indicates a specific failure/block
                         if any(x in val_str for x in ["+", "-", "Bajo", "Fallo", "Block", "IGNORED", "Wrong"]):
                             trans_key = KEY_TRANS.get(b_key, b_key)
                             s_factors.append({"k": trans_key, "v": val_str})
                         elif "Asset" in b_key: # Mantener info de tipo de activo
                             s_factors.append({"k": b_key, "v": val_str})
                    
                    grouped_strategies.append({
                        "name": s_name,
                        "score": s_score,
                        "status": status,
                        "factors": s_factors
                    })
                    
                    # Legacy Flat Factors (Keep for compatibility if needed, but we rely on grouped now)
                    factors[s_name] = f"{s_score}/100 ({status})"
                    for f in s_factors: factors[f"↳ {f['k']} ({s_name})"] = f['v']
                        
                except Exception as ex:
                    logger.error(f"Error running sub-strat {type(s).__name__}: {ex}")
                    print(f"DEBUG CRITICAL ERROR STRAT {type(s).__name__}: {ex}")
                    import traceback
                    traceback.print_exc()

            # 2. Master Strategy
            master_score = strategy_analysis.get('score', 0)
            # Inclusión forzada: Aunque el score sea 0, queremos ver la Maestra si estamos en análisis manual
            # Pero si el score es > 0 (heredado o real), debe aparecer como activa.
            if master_score > 0 or True: 
                m_status = "¡ACCIÓN!" if master_score >= 70 else ("Vigilar" if master_score >= 40 else "Neutral")
                m_breakdown = strategy_analysis.get('score_breakdown', {})
                m_factors = []
                for b_key, b_val in m_breakdown.items():
                    trans_key = KEY_TRANS.get(b_key, b_key)
                    m_factors.append({"k": trans_key, "v": str(b_val)})
                
                # Add to grouped
                grouped_strategies.append({
                    "name": "Estrategia Maestra (Canales)",
                    "score": master_score,
                    "status": m_status,
                    "factors": m_factors
                })
                
                # Legacy
                factors["Estrategia Maestra (Canales)"] = f"{master_score}/100 ({m_status})"
                for f in m_factors: factors[f"↳ {f['k']} (Maestra)"] = f['v']

            # SYNC SCORES (Independent & Capped)
            strategy_analysis['auto_score'] = min(100, max(0, max_sub_score))
            strategy_analysis['manual_score'] = min(100, max(0, master_score))
            
            # Use Fallback if best_s_name is "None"
            final_best_auto = best_s_name if (best_s_name and str(best_s_name) != "None") else "PST Strategy Hub"
            strategy_analysis['best_auto_strat'] = final_best_auto
            strategy_analysis['active_strategy'] = final_best_auto if max_sub_score >= master_score else "Estrategia Maestra (Canales)"
            
            # Legacy score fallback
            strategy_analysis['score'] = strategy_analysis['auto_score']
            
            # Inject grouped list into return object
            strategy_analysis['grouped_strategies'] = grouped_strategies

            # --- POPULATE FACTORS IF MISSING (CRITICAL FOR UX) ---
            if True: # Always populate/merge factors now
                best_lvl = None
                min_dist = 999
                
                # Check Manual Levels Data from Strategy Response
                if 'all_levels_data' in strategy_analysis:
                     for lvl in strategy_analysis['all_levels_data']:
                         # Find the level responsible for the score
                         if lvl.get('strat_score', 0) == strategy_analysis['score'] and strategy_analysis['score'] > 0:
                             best_lvl = lvl
                             break
                         
                         # Track closest level for Neutral State
                         d = abs(lvl.get('dist', 999))
                         if d < min_dist:
                             min_dist = d
                             best_lvl = lvl

                # DISABLE AUTO MERGING OF LEVEL DETAILS INTO MAIN FACTORS
                # The User wants "Auto Analysis" to show Sub-Strategies Only.
                # "Manual Analysis" is handled by the frontend picking the specific level from data.
                if False and best_lvl:
                    # Check if the BEST LEVEL is contributing to score (Active)
                    # OR if global score is > 0 (Auto Strat active)
                    lvl_score = best_lvl.get('strat_score', 0)
                    is_active_action = lvl_score > 0 or strategy_analysis['score'] > 0
                    
                    if is_active_action:
                        # Action State
                        factors['Acción'] = best_lvl.get('action_reco', 'N/A').replace('WATCH','VIGILAR').replace('BREAK','ROTURA').replace('BOUNCE','REBOTE')
                        factors['Tipo Nivel'] = best_lvl.get('type', 'N/A').replace('RESISTANCE','Resistencia').replace('SUPPORT','Soporte')
                        factors['Distancia'] = f"{best_lvl.get('dist', 0)}%"
                        
                        # Parse Validation Msg
                        val_str = best_lvl.get('validation', '')
                        if val_str:
                            parts = val_str.split(',')
                            for p in parts:
                                p = p.strip()
                                if 'RSI' in p: 
                                    state = p.replace('RSI', '').strip().replace('Bullish','Alcista').replace('Bearish','Bajista').replace('OB','Sobrecompra').replace('OS','Sobrevendida')
                                    factors['RSI'] = state
                                elif 'Vol' in p: 
                                    factors['Volumen'] = "Alto" if 'High' in p else "Normal"
                                else: 
                                    if 'Dist' not in p: factors['Info'] = p
                    else:
                        # Neutral State (Zone 1)
                        factors['Estado'] = "Neutral (Monitoreando)"
                        l_type = best_lvl.get('type','Level').replace('RESISTANCE','Resistencia').replace('SUPPORT','Soporte')
                        factors['Nivel Prox'] = f"{l_type} ({best_lvl.get('dist',0)}%)"
                        
                        # Dynamic Zone Label
                        d = abs(best_lvl.get('dist', 999))
                        z_label = "Lejos (> 0.4%)"
                        if d <= 0.4 and d > 0.08: z_label = "Vigilancia (< 0.4%)"
                        elif d <= 0.08: z_label = "Acción (< 0.08%)"
                        factors['Zona'] = z_label
                else:
                    # Just ensure Factors is assigned even if empty (will contain sub-strats only)
                    pass
                
                strategy_analysis['factors'] = factors
            else:
                strategy_analysis['factors'] = factors # Fallback assignment if logic skipped
            
            # Add common indicators to factors if not present
            if 'RSI' not in factors: factors['RSI (H1)'] = f"{float(rsi_h1):.1f}"
            if 'Regimen' not in factors: factors['Regimen'] = str(mode_str)
            close_val = float(df['close'].iloc[-1])
            rsi_val = float(df['rsi_14'].iloc[-1]) if 'rsi_14' in df else 50.0
            vol_val = int(df['tick_volume'].iloc[-1])
            vol_ma = float(df['tick_volume'].rolling(20).mean().iloc[-1]) if 'tick_volume' in df else float(vol_val)
            
            if 'chan_upper' in strategy_analysis and 'chan_lower' in strategy_analysis:
                strategy_analysis['dist_up_pct'] = round(((strategy_analysis['chan_upper'] - close_val) / close_val) * 100, 3)
                strategy_analysis['dist_low_pct'] = round(((close_val - strategy_analysis['chan_lower']) / close_val) * 100, 3)
                strategy_analysis['dist_up'] = round(strategy_analysis['chan_upper'] - close_val, 5)
                strategy_analysis['dist_low'] = round(close_val - strategy_analysis['chan_lower'], 5)

            # --- STRATEGY BREAKOUT LOGIC FOR LEVELS ---
            if 'all_levels_data' in strategy_analysis:
                # We do NOT loop here to update factors (Done above if needed, but disabled for independence)
                # Just loop to update inner level objects for frontend usage
                for lvl in strategy_analysis['all_levels_data']:
                    lvl_price = lvl['price']
                    # Calculate dynamic price for trendlines if needed (simplified to end price for now or midpoint)
                    # Ideally trendline price at current candle. strategy_analysis usually has this pre-calced as 'dist'?
                    # If not, we use the static price or price2 if available.
                    # For now, let's trust the 'dist' calculation if PSTChannelMaster did it, OR re-calc roughly.
                    
                    # Hack: PSTChannelMaster might have calculated distance to current close already.
                    # Let's assume lvl['price'] is the relevant reference price (or average if trendline).
                    
                    if lvl.get('price2') and lvl.get('time1') and lvl.get('time2'): # It's a Trendline
                        try:
                            # 1. Parse Times
                            # time1/time2 in DB are ISO strings.
                            # current time is 'now'.
                            from dateutil import parser
                            t1_dt = parser.parse(lvl['time1'])
                            t2_dt = parser.parse(lvl['time2'])
                            t_now = datetime.now(t1_dt.tzinfo) # Ensure same TZ if possible or just naive
                            
                            t1_ts = t1_dt.timestamp()
                            t2_ts = t2_dt.timestamp()
                            cur_ts = t_now.timestamp()
                            
                            # 2. Linear Interpolation: y = mx + c
                            # m = (y2 - y1) / (x2 - x1)
                            # y = y1 + m * (x - x1)
                            
                            p1 = lvl['price']
                            p2 = lvl['price2']
                            
                            if abs(t2_ts - t1_ts) > 0: # Avoid div by zero
                                m = (p2 - p1) / (t2_ts - t1_ts)
                                interpolated_price = p1 + m * (cur_ts - t1_ts)
                                lvl_price = interpolated_price # OVERRIDE PRICE for calculation
                                
                                # Update 'price' in object for display correctness if needed, 
                                # but keep original P1 for key.
                                # Let's attach 'current_val' to lvl dict for debugging
                                lvl['current_val'] = interpolated_price
                                # print(f"DEBUG MANUAL INTERP: {symbol} P1={p1} P2={p2} T1={t1_ts} T2={t2_ts} NOW={cur_ts} => VAL={interpolated_price}")
                        except Exception as e:
                            logger.debug(f"DEBUG MANUAL INTERP ERROR: {e}")
                            pass

                    # Determine Relation
                    # dist is usually (Current - Level) or (Level - Current). 
                    # PSTChannelMaster returns 'dist' as % distance.
                    
                    # PRIORITIZE STRATEGY ENGINE VALUES
                    if 'action_reco' in lvl and lvl['action_reco'] != "WAIT":
                         # Already calculated by Strategy Class (PSTChannelMaster)
                         # Trust it completely to match Modal
                         pass 
                    else:
                        # Fallback Logic Sync with get_status
                        action_reco = "WAIT"
                        strat_score = 0
                        val_msg = []
                        
                        # USE UNIFIED SCORING
                        strat_score, action_reco, val_msg = calculate_manual_score(
                            price=close_val,
                            lvl_price=lvl_price,
                            l_type=lvl['type'],
                            rsi=rsi_val,
                            vol_val=vol_val,
                            vol_ma=vol_ma
                        )
                        
                        lvl['action_reco'] = action_reco
                        lvl['strat_score'] = round(strat_score)
                        lvl['validation'] = ", ".join(val_msg) if val_msg else "Standard"

            # logger.debug(f"DEBUG: HUD Analysis OK. Score: {strategy_analysis['score']}")
        except Exception as e:
            logger.error(f"DEBUG ERROR HUD: {e}")

        # Replace NaNs with None for valid JSON serialization
        # Use .where(pd.notnull(df), None) or just handle in creation
        # Use display DF (with string times) for chart
        chart_data = df_display.where(pd.notnull(df_display), None).to_dict('records')

        # Prepare Response
        technical_analysis = {
            "rsi": rsi_val,
            "rsi_h1": rsi_h1,
            "vol": vol_val,
            "active_strategy": strategy_analysis.get('active_strategy', 'PST-Master')
        }

        # logger.debug(f"📊 Sending HUD response for {symbol}. Score: {strategy_analysis.get('score', 0)} Factors: {len(strategy_analysis.get('factors', {}))}")

        # Helper to clean NaNs for JSON (Recursive for entire logic)
        import math
        def recursive_clean(obj):
            if isinstance(obj, dict):
                return {k: recursive_clean(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [recursive_clean(x) for x in obj]
            elif isinstance(obj, float) or isinstance(obj, np.floating):
                if math.isnan(obj) or math.isinf(obj):
                    return None
                return obj
            elif pd.isna(obj): # Handle pandas NA/NaT/NaN
                return None
            return obj

        # Construction of the full response object
        response_data = {
            "symbol": symbol,
            "tf": tf_str,
            "data": chart_data,
            "channel_macro_upper": u_macro, 
            "channel_macro_lower": l_macro, 
            "channel_local_upper": u_local,
            "channel_local_lower": l_local,
            "ema_21": df['ema_21'].tolist() if 'ema_21' in df.columns else [],
            "ema_50": df['ema_50'].tolist() if 'ema_50' in df.columns else [],
            "analysis": strategy_analysis,
            "technical_analysis": technical_analysis,
            "trade": trade_info
        }
        
        # Clean everything in one go
        cleaned_response = recursive_clean(response_data)

        return jsonify(cleaned_response)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/trade/params/<symbol>/<order_type>')
def get_trade_params(symbol, order_type):
    try:
        mt5.symbol_select(symbol, True)
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 100)
        if rates is None or len(rates) < 20:
            return jsonify({"error": "No hay suficientes datos para cálculo ATR"}), 404
            
        df = pd.DataFrame(rates)
        atr_series = ta.atr(df['high'], df['low'], df['close'], length=14)
        atr = atr_series.iloc[-1]
        
        tick = mt5.symbol_info_tick(symbol)
        s_info = mt5.symbol_info(symbol)
        acc = mt5.account_info()
        
        if not tick or not s_info or not acc:
            return jsonify({"error": "Error al obtener datos de mercado"}), 500
            
        price = tick.ask if order_type == 'BUY' else tick.bid
        
        # Inteligencia PST:
        sl_dist = atr * 2.0  # SL de 2 ATRs
        tp_dist = atr * 4.0  # TP de 4 ATRs (1:2 RR)
        
        sl_points = sl_dist / s_info.point
        lot = portfolio.calculate_lot_size(acc.balance, portfolio.max_risk_pct, sl_points, s_info)
        
        sl_price = price - sl_dist if order_type == 'BUY' else price + sl_dist
        tp_price = price + tp_dist if order_type == 'BUY' else price - tp_dist
        
        return jsonify({
            "symbol": symbol,
            "type": order_type,
            "price": price,
            "lot": lot,
            "sl": round(sl_price, s_info.digits),
            "tp": round(tp_price, s_info.digits),
            "atr": round(atr, s_info.digits)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/trade', methods=['POST'])
def execute_manual_trade():
    try:
        data = request.json
        symbol = data.get('symbol')
        order_type = data.get('type')
        lot = data.get('volume', 0.01)
        sl = data.get('sl', 0)
        tp = data.get('tp', 0)
        
        if not symbol or not order_type:
            return jsonify({"error": "Símbolo y tipo requeridos"}), 400
            
        mt5.symbol_select(symbol, True)
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return jsonify({"error": "No se pudo obtener precio actual"}), 404
            
        price = tick.ask if order_type == 'BUY' else tick.bid
        
        request_mt5 = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(lot),
            "type": mt5.ORDER_TYPE_BUY if order_type == 'BUY' else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": float(sl),
            "tp": float(tp),
            "magic": 666,
            "comment": "PST_SMART_MANUAL",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request_mt5)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return jsonify({"error": f"Error MT5: {result.comment} (Code: {result.retcode})"}), 500
            
        # REGISTRAR EN EL HISTORIAL (Para que aparezca en el Dashboard)
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Obtener el régimen actual para el log
            cursor.execute('SELECT mode FROM regime_history WHERE symbol = ? ORDER BY id DESC LIMIT 1', (symbol,))
            row = cursor.fetchone()
            current_mode = row['mode'] if row else "UNKNOWN"
            
            cursor.execute('''
                INSERT INTO signal_logs (timestamp, symbol, regime, strategy, signal_type, score, price)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (str(datetime.now()), symbol, current_mode, "MANUAL_HUD", order_type, 100.0, price))
            
            # También en la tabla de trades para seguimiento de performance
            cursor.execute('''
                INSERT INTO trades (symbol, type, volume, price_in, price_out, sl, tp, profit, time_in, time_out, regime_at_entry, strategy_name, ticket)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (symbol, order_type, float(lot), price, 0.0, float(sl), float(tp), 0.0, str(datetime.now()), "", current_mode, "MANUAL_HUD", result.order))
            
            conn.commit()
            conn.close()
        except Exception as db_err:
            logger.error(f"⚠️ Error al registrar log manual: {db_err}")

        return jsonify({"success": True, "ticket": result.order})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/close', methods=['POST'])
def close_symbol_position():
    try:
        data = request.json
        symbol = data.get('symbol')
        if not symbol:
            return jsonify({"error": "Símbolo requerido"}), 400
            
        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return jsonify({"error": "No hay posición abierta para este símbolo"}), 404
            
        # Cerrar cada posición parcial (en MT5 las posiciones se cierran por ticket)
        for p in positions:
            tick = mt5.symbol_info_tick(symbol)
            request_close = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": p.volume,
                "type": mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                "position": p.ticket,
                "price": tick.bid if p.type == mt5.POSITION_TYPE_BUY else tick.ask,
                "magic": 666,
                "comment": "PST_CLOSE_MANUAL",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request_close)
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                return jsonify({"error": f"Error al cerrar ticket {p.ticket}: {result.comment}"}), 500
            
            # REGISTRO INMEDIATO EN DB PARA EL DASHBOARD
            try:
                conn_db = get_db_connection()
                cursor_db = conn_db.cursor()
                
                # Actualizar el trade local
                # Nota: result (OrderSendResult) no tiene 'profit'. 
                # Se pone a 0 y el historial sincronizado de MetaTrader lo actualizará.
                cursor_db.execute('''
                    UPDATE trades 
                    SET price_out = ?, profit = 0, time_out = ?
                    WHERE ticket = ? AND price_out = 0
                ''', (result.price, str(datetime.now()), p.ticket))
                
                conn_db.commit()
                conn_db.close()
                print(f"✅ Cierre registrado en DB: {symbol} (Ticket: {p.ticket})")
            except Exception as db_err:
                logger.error(f"⚠️ Error al registrar cierre en DB: {db_err}")
                
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/import_history', methods=['POST'])
def import_history_from_mt5():
    """Importa el historial de MT5 (últimos 30 días) a la base de datos local."""
    try:
        from datetime import datetime, timedelta
        
        # 1. Conexión MT5
        if not mt5.initialize():
             return jsonify({"error": "MT5 no disponible"}), 500
             
        # 2. Obtener Deals (30 días)
        end = datetime.now()
        start = end - timedelta(days=30)
        deals = mt5.history_deals_get(start, end)
        
        if deals is None:
             return jsonify({"error": "No se pudieron obtener deals", "count": 0})
             
        count_imported = 0
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 3. Procesar Trades (Agrupar Entry + Exit)
        # Esto es complejo porque MT5 da deals sueltos. Simplificación:
        # Buscamos deals de SALIDA (Entry = 1) y asumimos datos del trade.
        
        for d in deals:
            # Entry 1 = DEAL_ENTRY_OUT (Salida de mercado)
            if d.entry == 1: 
                # Verificar si ya existe en la DB por ticket
                cursor.execute("SELECT id FROM trades WHERE ticket = ?", (d.position_id,))
                exists = cursor.fetchone()
                
                if not exists:
                    # Insertar nuevo registro retroactivo
                    # Nota: No tenemos todos los datos precisos del Entry original sin buscar el deal de entrada,
                    # pero para stats básicas (Profit, Symbol, TimeOut) este deal es suficiente.
                    # Intentamos buscar el precio de entrada en el historial si es posible, sino 0.
                    
                    symbol = d.symbol
                    profit = d.profit + d.swap + d.commission
                    
                    cursor.execute('''
                        INSERT INTO trades (symbol, type, volume, price_in, price_out, sl, tp, profit, time_in, time_out, regime_at_entry, strategy_name, ticket)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        symbol, 
                        "BUY" if d.type == 1 else "SELL", # En salida, type 1(sell) cierra buy? No, type en deal es la accion. 
                                                          # DEAL_TYPE_BUY=0, DEAL_TYPE_SELL=1. Si cierras un BUY, haces un SELL.
                                                          # Entonces si d.type=1 (SELL), era un BUY.
                        d.volume, 
                        0.0, # Price In desconocido simplificado
                        d.price, 
                        0.0, 0.0, 
                        profit, 
                        str(datetime.fromtimestamp(d.time)), # Time In aproximado (usamos el del cierre visualmente o null) -> Mejor ponemos el del cierre en time_out
                        str(datetime.fromtimestamp(d.time)), 
                        "UNKNOWN", 
                        "IMPORTED", 
                        d.position_id
                    ))
                    count_imported += 1
        
        conn.commit()
        conn.close()
        
        return jsonify({"success": True, "count": count_imported})
        
    except Exception as e:
        logger.error(f"❌ Error importando historial: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/close_all', methods=['POST'])
def close_all_positions():
    try:
        positions = mt5.positions_get()
        if not positions:
            return jsonify({"success": True, "message": "No había posiciones abiertas"})
            
        for p in positions:
            tick = mt5.symbol_info_tick(p.symbol)
            request_close = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": p.symbol,
                "volume": p.volume,
                "type": mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                "position": p.ticket,
                "price": tick.bid if p.type == mt5.POSITION_TYPE_BUY else tick.ask,
                "magic": 666,
                "comment": "PST_EMERGENCY_CLOSE",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request_close)
            
            # REGISTRO INMEDIATO EN DB
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                try:
                    from PST_Core.models.database import PSTDatabase
                    db_instance = PSTDatabase()
                    # Actualizar directamente vía sqlite para simplicidad en Flask
                    conn_db = get_db_connection()
                    cursor_db = conn_db.cursor()
                    cursor_db.execute('''
                        UPDATE trades SET price_out = ?, profit = ?, time_out = ?
                        WHERE ticket = ? AND price_out = 0
                    ''', (request_close["price"], p.profit, str(datetime.now()), p.ticket))
                    conn_db.commit()
                    conn_db.close()
                except Exception as e:
                    print(f"Error logging emergency close: {e}")
            
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/levels/<symbol>')
def get_user_levels(symbol):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM user_levels WHERE symbol = ? AND is_active = 1", (symbol,))
        levels = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify(levels)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/levels', methods=['POST'])
def save_user_level():
    try:
        data = request.json
        # logger.debug(f"DEBUG SAVE LEVEL PAYLOAD: {data}")
        symbol = data.get('symbol')
        price = data.get('price')
        ltype = data.get('type', 'CUSTOM') # RESISTANCE, SUPPORT, CUSTOM
        label = data.get('label', '')
        # Campos opcionales para Trendlines
        price2 = data.get('price2') 
        time2 = data.get('time2')
        time1 = data.get('time1')
        
        if not symbol or price is None:
            return jsonify({"error": "Símbolo y precio requeridos"}), 400
            
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Verificar si las columnas existen y añadirlas si no (migración en caliente para SQLite sync)
        try:
            cursor.execute("SELECT user_levels.time1 FROM user_levels LIMIT 1")
        except:
            try: cursor.execute("ALTER TABLE user_levels ADD COLUMN price2 REAL")
            except: pass
            try: cursor.execute("ALTER TABLE user_levels ADD COLUMN time2 TEXT")
            except: pass
            try: cursor.execute("ALTER TABLE user_levels ADD COLUMN time1 TEXT")
            except: pass
            conn.commit()

        # Check if ID exists for Update
        level_id = data.get('id')
        
        if level_id:
            cursor.execute("""
                UPDATE user_levels 
                SET symbol=?, price=?, type=?, label=?, price2=?, time2=?, time1=?
                WHERE id=?
            """, (symbol, float(price), ltype, label, price2, time2, time1, level_id))
            new_id = level_id
            logger.debug(f"UPDATED Level ID={level_id}")
        else:
            cursor.execute("""
                INSERT INTO user_levels (symbol, price, type, label, price2, time2, time1)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (symbol, float(price), ltype, label, price2, time2, time1))
            new_id = cursor.lastrowid
            logger.debug(f"CREATED Level ID={new_id}")
        conn.commit()
        new_id = cursor.lastrowid
        conn.close()
        return jsonify({"success": True, "id": new_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/levels/<string:symbol>', methods=['DELETE'])
def delete_symbol_levels(symbol):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_levels WHERE symbol = ?", (symbol,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/levels/<int:level_id>', methods=['DELETE'])
def delete_user_level(level_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_levels WHERE id = ?", (level_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/channel-config/<string:symbol>', methods=['GET'])
def get_channel_config(symbol):
    try:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM channel_config WHERE symbol = ?", (symbol,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return jsonify(dict(row))
        else:
            # Default: All Enabled
            return jsonify({"enable_macro": 1, "enable_tactical": 1})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/channel-config', methods=['POST'])
def save_channel_config():
    try:
        data = request.json
        symbol = data.get('symbol')
        enable_macro = int(data.get('enable_macro', 1))
        enable_tactical = int(data.get('enable_tactical', 1))

        conn = get_db_connection()
        cursor = conn.cursor()
        
        # INSERT OR REPLACE
        cursor.execute("""
            INSERT INTO channel_config (symbol, enable_macro, enable_tactical) 
            VALUES (?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
            enable_macro=excluded.enable_macro,
            enable_tactical=excluded.enable_tactical
        """, (symbol, enable_macro, enable_tactical))
        
        conn.commit()
        conn.close()
        
        return jsonify({"success": True})
    except Exception as e:
        print(f"DEBUG ERROR saving config: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')
