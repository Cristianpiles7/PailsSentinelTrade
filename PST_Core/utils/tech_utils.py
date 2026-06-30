import numpy as np
import pandas as pd
from datetime import datetime

def calculate_channel_boundary(df, window=10, projection=30, recent_pivots=None):
    """
    Versión ROBÚSTA de cálculo de canales (Envelope).
    recent_pivots: Si se especifica, solo usa los últimos N pivotes para la regresión.
    """
    try:
        if df is None or len(df) < window * 2:
            return [], [], None

        # 0. NORMALIZACIÓN CRÍTICA
        df_copy = df.copy()
        
        # Asegurar que 'time' sea una columna y el índice sea 0, 1, 2...
        if 'time' not in df_copy.columns:
            df_copy = df_copy.reset_index()
            # Identificar columna de tiempo tras reset
            potential_time = [c for c in df_copy.columns if 'time' in str(c).lower() or 'index' in str(c).lower() or 'level' in str(c).lower()]
            if potential_time:
                df_copy = df_copy.rename(columns={potential_time[0]: 'time'})
        else:
            df_copy = df_copy.reset_index(drop=True)

        # 1. Detectar Pivotes
        df_copy['is_pivot_high'] = df_copy['high'].rolling(window=window, center=True).max() == df_copy['high']
        df_copy['is_pivot_low'] = df_copy['low'].rolling(window=window, center=True).min() == df_copy['low']
        
        highs = df_copy[df_copy['is_pivot_high']]
        lows = df_copy[df_copy['is_pivot_low']]
        
        if len(highs) < 2 or len(lows) < 2:
            return [], [], None

        # 1.1 Filtrar Pivotes Recientes si se solicita
        if recent_pivots:
            highs = highs.tail(recent_pivots)
            lows = lows.tail(recent_pivots)

        # 2. Pendientes Independientes
        x_h = highs.index.values.astype(float)
        y_h = highs['high'].values
        slope_h, _ = np.polyfit(x_h, y_h, 1)

        x_l = lows.index.values.astype(float)
        y_l = lows['low'].values
        slope_l, _ = np.polyfit(x_l, y_l, 1)

        # 3. Interceptos Frontera (Envelope)
        intercepts_h = y_h - slope_h * x_h
        upper_intercept = np.max(intercepts_h)

        intercepts_l = y_l - slope_l * x_l
        lower_intercept = np.min(intercepts_l)

        # 4. Preparar Tiempos Numéricos para Proyección (ROBUSTO)
        t_series = df_copy['time']
        
        # Helper to get seconds from anything
        def to_seconds(val):
            if isinstance(val, (int, float, np.integer, np.floating)):
                # If it's a huge number, it's probably nanoseconds (common in pandas/numpy)
                if val > 1e12: return val / 1e9 
                return val
            if isinstance(val, datetime):
                return val.timestamp()
            if isinstance(val, (pd.Timestamp, np.datetime64)):
                return pd.Timestamp(val).timestamp()
            return 0

        # Convert entire series to seconds
        t_numeric = t_series.apply(to_seconds)
        
        # Asegurar que sea float/int nativo de python para evitar ufunc issues
        last_t_num = float(t_numeric.iloc[-1])
        time_delta = float(t_numeric.diff().tail(50).median()) if len(t_numeric) > 1 else 300.0
        last_idx = int(df_copy.index[-1])
        
        # 5. Generar Puntos
        start_idx = int(min(highs.index[0], lows.index[0]))
        upper_line = []
        lower_line = []

        # Históricos
        for i in range(start_idx, last_idx + 1):
            if i in df_copy.index:
                t_val = df_copy.at[i, 'time']
                upper_line.append({"time": t_val, "value": float(slope_h * i + upper_intercept)})
                lower_line.append({"time": t_val, "value": float(slope_l * i + lower_intercept)})

        # Proyección
        for i in range(1, projection + 1):
            idx_fut = last_idx + i
            t_fut = last_t_num + (time_delta * i)
            t_out = float(t_fut)
            upper_line.append({"time": t_out, "value": float(slope_h * idx_fut + upper_intercept)})
            lower_line.append({"time": t_out, "value": float(slope_l * idx_fut + lower_intercept)})

        params = {
            "slope_h": slope_h, "intercept_h": upper_intercept,
            "slope_l": slope_l, "intercept_l": lower_intercept,
            "slope_m": (slope_h + slope_l) / 2, 
            "intercept_m": (upper_intercept + lower_intercept) / 2, 
            "last_idx": last_idx
        }
        return upper_line, lower_line, params

    except Exception as e:
        print(f"Error in calculate_channel_boundary: {e}")
        return [], [], None

def calculate_manual_score(price, lvl_price, l_type, rsi, vol_val, vol_ma, is_green=None, is_red=None, trend_slope=0, adx=0, atr_val=0):
    """
    Motor de puntuacion ULTRA-LÓGICO y ESTRICTO.
    Retorna: (total_score, action_name, factor_details_list, recommendations)
    """
    if price <= 0: return 0, "NEUTRAL", []
    
    dist_p = price - lvl_price
    dist_pct = (dist_p / price * 100)
    abs_dist_pct = abs(dist_pct)
    
    # Thresholds para visibilidad en HUD
    THR_NEUTRAL = 2.50   
    
    score = 0
    action = "WAIT"
    factors = [] # List of {"k": label, "v": value, "score": pts}
    
    is_res = l_type == 'RESISTANCE'
    is_sup = l_type == 'SUPPORT'
    
    # --- DETERMINAR ESTADO ---
    is_breakout = (is_res and dist_p > 0) or (is_sup and dist_p < 0)
    is_approaching = not is_breakout

    # 1. LÓGICA DE APROXIMACIÓN (REBOTE)
    if is_approaching and abs_dist_pct <= THR_NEUTRAL:
        action = "VIGILAR"
        
        # --- FILTRO MOMENTUM: Si nos acercamos a resistencia pero la vela es roja, NO es relevante ---
        if is_res and is_red: return 0, "CAÍDA", [{"k": "Momentum", "v": "🛑 Opuesto (Vela Roja)", "score": 0}]
        if is_sup and is_green: return 0, "REBOTE", [{"k": "Momentum", "v": "🛑 Opuesto (Vela Verde)", "score": 0}]

        progress = (THR_NEUTRAL - abs_dist_pct) / THR_NEUTRAL
        base_score = 10 + (progress * 50)
        score += base_score
        factors.append({
            "k": "Proximidad", 
            "v": f"{abs_dist_pct:.2f}%", 
            "score": round(base_score),
            "desc": "Mide qué tan cerca está el precio del nivel objetivo. A menor distancia, mayor es la probabilidad de interacción inmediata."
        })
        
        # RSI Context
        rsi_ok = (is_res and rsi > 60) or (is_sup and rsi < 40)
        if rsi_ok:
            score += 15
            factors.append({
                "k": "RSI Context", 
                "v": f"OK ({rsi:.1f})", 
                "score": 15,
                "desc": "El RSI confirma que el precio tiene espacio para moverse hacia el nivel o que está mostrando la presión adecuada para un rebote/rotura."
            })
        else:
            factors.append({"k": "RSI Context", "v": f"Neutral ({rsi:.1f})", "score": 0})
            
        # ADX Context (Rebotes)
        if adx >= 30:
            penalty = 15
            score -= penalty
            factors.append({"k": "Riesgo ADX", "v": f"Fuerte ({adx:.1f})", "score": -penalty})
        elif adx < 20 and adx > 0:
            bonus = 10
            score += bonus
            factors.append({"k": "Filtro ADX", "v": f"Rango ({adx:.1f})", "score": bonus})

    # 2. LÓGICA DE RUPTURA (BREAKOUT)
    elif is_breakout and abs_dist_pct <= THR_NEUTRAL:
        action = "ROTURA"
        
        # --- MOMENTUM KILL ABSOLUTO: Si la vela es del color contrario, el score es 0 ---
        if (is_res and is_red): return 0, "FALSO-BREAK", [{"k": "Cierre", "v": "🛑 Vela Roja (Bajista)", "score": 0}]
        if (is_sup and is_green): return 0, "FALSO-BREAK", [{"k": "Cierre", "v": "🛑 Vela Verde (Alcista)", "score": 0}]

        score += 25 # Base
        factors.append({"k": "Base Ruptura", "v": f"Confirmada ({abs_dist_pct:.2f}%)", "score": 25})
        
        # FILTROS ESTRICTOS
        if vol_val > vol_ma:
            score += 25
            factors.append({
                "k": "Volumen", 
                "v": "Confirmado (Vol > MA)", 
                "score": 25,
                "desc": "El volumen superior a la media de 20 periodos valida la rotura como un movimiento con participación institucional real."
            })
        else:
            factors.append({"k": "Volumen", "v": "Bajo (No Confirmado)", "score": 0})
        
        rsi_bias_ok = (is_res and rsi > 55) or (is_sup and rsi < 45)
        if rsi_bias_ok:
            score += 15
            factors.append({"k": "Impulso RSI", "v": f"A favor ({rsi:.1f})", "score": 15})
        else:
            factors.append({"k": "Impulso RSI", "v": f"Neutro ({rsi:.1f})", "score": 0})
            
        # ADX Strength (Crucial para rupturas)
        if adx >= 25:
            bonus = 20
            score += bonus
            factors.append({
                "k": "Fuerza ADX", 
                "v": f"Alta ({adx:.1f})", 
                "score": bonus,
                "desc": "Un ADX por encima de 25 indica una tendencia con inercia, fundamental para que la ruptura no sea un falso movimiento."
            })
        elif adx < 18 and adx > 0:
            penalty = 15
            score -= penalty
            factors.append({"k": "ADX Débil", "v": f"Lateral ({adx:.1f})", "score": -penalty})
            
        momentum_ok = (is_res and is_green) or (is_sup and is_red)
        if momentum_ok:
            score += 15
            factors.append({"k": "Vela", "v": "Fuerza Confirmada", "score": 15})
        else:
            factors.append({"k": "Vela", "v": "Indecisión", "score": 0})

    # 3. Lógica de Riesgos (NUEVO Ph2)
    reco = {"sl_dist": 0, "tp_dist": 0}
    if atr_val > 0:
        # Recomendamos 1.5 ATR para SL y 2.5 para TP por defecto
        reco["sl_dist"] = round(atr_val * 1.5, 5)
        reco["tp_dist"] = round(atr_val * 2.5, 5)

    return round(min(100, max(0, score))), action, factors, reco

    return round(min(100, score)), action, factors

def get_asset_class(symbol: str) -> str:
    """
    Identifica la clase de activo basada en el símbolo.
    Retorna: 'CRYPTO', 'INDEX', 'METAL', 'FOREX'
    """
    s = symbol.upper()
    
    # METALES
    if "XAU" in s or "XAG" in s or "GOLD" in s:
        return "METAL"
    
    # INDICES (Tratados como activos de alta correlación con el mercado)
    indices_keywords = [
        "US500", "SPX", "NAS100", "US30", "GER30", "DAX", "EU50", "STOXX50"
    ]
    if any(k in s for k in indices_keywords):
        return "INDEX"
        
    # ACCIONES INDIVIDUALES (EQUITIES)
    equities_keywords = [
        "NVDA", "TSLA", "AAPL", "MSFT", "GOOG", "AMZN", "META", "NFLX"
    ]
    if any(k in s for k in equities_keywords):
        return "EQUITIES"
        
    # CRYPTO
    crypto_keywords = [
        "BTC", "ETH", "SOL", "ADA", "XRP", "LTC", "DOT", "UNI", 
        "LINK", "LNK", "XLM", "MATIC", "AVAX", "DOGE", "SHIB"
    ]
    if any(k in s for k in crypto_keywords):
        return "CRYPTO"
    
    # Default fallback
    return "FOREX"

def detect_divergence(df, window=5, order=2):
    """
    Detecta divergencias entre Precio y RSI.
    Retorna: 'BULLISH', 'BEARISH' o None
    """
    if len(df) < 50: return None
    
    # 1. Asegurar RSI
    import pandas_ta as ta
    if 'rsi' not in df.columns:
        df['rsi'] = ta.rsi(df['close'], length=14)
    
    # Identificar picos y valles (Pivots)
    def is_pivot(series, idx, w, is_high=True):
        if idx < w or idx >= len(series) - w: return False
        val = series.iloc[idx]
        subset = series.iloc[idx-w : idx+w+1]
        return val == subset.max() if is_high else val == subset.min()

    # Buscamos los 2 últimos pivotes
    pivots_price = [] # (index, type, value)
    pivots_rsi = []
    
    # Escaneamos las últimas 40 velas buscando pivotes
    for i in range(len(df)-window-1, len(df)-40, -1):
        if i < window: break
        # Altos (Para Bearish)
        if is_pivot(df['high'], i, window, True):
            pivots_price.append(('H', i, df['high'].iloc[i]))
            pivots_rsi.append(('H', i, df['rsi'].iloc[i]))
        # Bajos (Para Bullish)
        if is_pivot(df['low'], i, window, False):
            pivots_price.append(('L', i, df['low'].iloc[i]))
            pivots_rsi.append(('L', i, df['rsi'].iloc[i]))
        
        if len(pivots_price) >= order: break

    if len(pivots_price) < 2: return None

    # Lógica de Divergencia
    # BULLISH: Precio hace mínimo más bajo, RSI hace mínimo más alto
    lows = [p for p in pivots_price if p[0] == 'L']
    if len(lows) >= 2:
        p2, p1 = lows[0], lows[1] # p2 es más reciente
        r2 = df['rsi'].iloc[p2[1]]
        r1 = df['rsi'].iloc[p1[1]]
        if p2[2] < p1[2] and r2 > r1: return "BULLISH"

    # BEARISH: Precio hace máximo más alto, RSI hace máximo más bajo
    highs = [p for p in pivots_price if p[0] == 'H']
    if len(highs) >= 2:
        p2, p1 = highs[0], highs[1]
        r2 = df['rsi'].iloc[p2[1]]
        r1 = df['rsi'].iloc[p1[1]]
        if p2[2] > p1[2] and r2 < r1: return "BEARISH"

    return None

def get_market_session(dt_utc: datetime) -> str:
    """
    Identifica la sesión operativa activa basada en la hora UTC.
    Retorna: 'ASIAN', 'LONDON', 'NY', 'OVERLAP' (London + NY)
    """
    hour = dt_utc.hour
    
    # Horarios simplificados (invierno/verano estándar)
    # Tokyo: ~00:00 a 09:00 UTC
    # London: ~08:00 a 16:30 UTC
    # NY: ~13:30 a 20:00 UTC
    
    if 13 <= hour < 16:
        return "OVERLAP" # London + NY (Máxima liquidez)
    elif 8 <= hour < 13:
        return "LONDON"
    elif 16 <= hour <= 20:
        return "NY"
    else:
        return "ASIAN" # Poca liquidez

def detect_absorption(df, vol_rel_threshold=1.5):
    """
    Detecta absorción institucional: Alto volumen + Mecha grande + Cuerpo pequeño.
    Retorna: 'BUY_ABS' (Absorción en suelo), 'SELL_ABS' (Absorción en techo) o None
    """
    if len(df) < 20: return None, False
    
    last = df.iloc[-1]
    import pandas_ta as ta
    vol_ma = ta.sma(df['tick_volume'], length=20).iloc[-1] if 'tick_volume' in df.columns else 1
    vol_rel = last['tick_volume'] / vol_ma if vol_ma > 0 else 0
    
    if vol_rel < vol_rel_threshold: return None, False
    
    range_total = last['high'] - last['low']
    body = abs(last['close'] - last['open'])
    upper_wick = last['high'] - max(last['open'], last['close'])
    lower_wick = min(last['open'], last['close']) - last['low']
    
    if range_total == 0: return None, False
    
    is_climax = vol_rel >= 3.0 # Considerado Clímax Institucional (>300% volumen promedio)
    
    # ABSORCIÓN EN SUELO (Martillo con volumen)
    if lower_wick > (body * 2) and vol_rel > vol_rel_threshold:
        return "BUY_ABS", is_climax
    
    # ABSORCIÓN EN TECHO (Shooting star con volumen)
    if upper_wick > (body * 2) and vol_rel > vol_rel_threshold:
        return "SELL_ABS", is_climax
        
    return None, False

def detect_fvg(df):
    """
    Detecta Fair Value Gaps (FVG) entre 3 velas.
    Un FVG ocurre cuando el Bajo de la vela 1 no alcanza el Alto de la vela 3 (Bullish)
    o el Alto de la vela 1 no alcanza el Bajo de la vela 3 (Bearish).
    Retorna: Lista de dicts con {'type': 'BULLISH'/'BEARISH', 'top': float, 'bottom': float, 'index': int}
    """
    if len(df) < 5: return []
    
    fvgs = []
    # Analizamos las últimas 20 velas para buscar huecos recientes
    for i in range(len(df) - 1, len(df) - 20, -1):
        if i < 2: break
        
        # Velas i-2 (1), i-1 (2), i (3)
        c1 = df.iloc[i-2]
        c2 = df.iloc[i-1]
        c3 = df.iloc[i]
        
        # Bullish FVG (Hueco alcista)
        if c1['high'] < c3['low']:
            fvgs.append({
                'type': 'BULLISH',
                'top': c3['low'],
                'bottom': c1['high'],
                'index': i-1,
                'time': c2.get('time', i-1)
            })
            
        # Bearish FVG (Hueco bajista)
        elif c1['low'] > c3['high']:
            fvgs.append({
                'type': 'BEARISH',
                'top': c1['low'],
                'bottom': c3['high'],
                'index': i-1,
                'time': c2.get('time', i-1)
            })
            
    return fvgs

def detect_order_blocks(df, window=20):
    """
    Detecta Order Blocks (OB) institucionales.
    Un Bullish OB es la última vela bajista antes de un movimiento impulsivo alcista fuerte.
    Un Bearish OB es la última vela alcista antes de un movimiento impulsivo bajista fuerte.
    """
    if len(df) < window: return []
    
    obs = []
    import pandas_ta as ta
    
    # 1. Definir movimiento impulsivo (Cuerpo > 1.5 ATR y volumen alto)
    atr = ta.atr(df['high'], df['low'], df['close'], length=14)
    vol_ma = ta.sma(df['tick_volume'], length=20)
    
    for i in range(len(df) - 1, len(df) - window, -1):
        if i < 2: break
        
        curr = df.iloc[i]
        prev = df.iloc[i-1]
        
        c_body = abs(curr['close'] - curr['open'])
        c_atr = atr.iloc[i] if atr is not None else 0
        c_vol = curr['tick_volume']
        c_vma = vol_ma.iloc[i] if vol_ma is not None else 1
        
        # Requisito de Impulso: Cuerpo fuerte + Volumen > Media
        is_impulsive = c_body > (c_atr * 1.5) and c_vol > c_vma
        
        if is_impulsive:
            # Bullish OB (Vela previa roja)
            if curr['close'] > curr['open'] and prev['close'] < prev['open']:
                obs.append({
                    'type': 'BULLISH',
                    'top': prev['high'],
                    'bottom': prev['low'],
                    'index': i-1,
                    'time': prev.get('time', i-1),
                    'mitigated': False # TODO: Check if price has returned to this zone
                })
            # Bearish OB (Vela previa verde)
            elif curr['close'] < curr['open'] and prev['close'] > prev['open']:
                obs.append({
                    'type': 'BEARISH',
                    'top': prev['high'],
                    'bottom': prev['low'],
                    'index': i-1,
                    'time': prev.get('time', i-1),
                    'mitigated': False
                })
                
    # 2. Verificar mitigación (¿El precio ya regresó a esta zona?)
    for ob in obs:
        ob_range_low = ob['bottom']
        ob_range_high = ob['top']
        
        # Chequear desde el índice de la vela impulsiva hasta el final
        start_idx = ob['index'] + 2
        if start_idx < len(df):
            test_data = df.iloc[start_idx:]
            if ob['type'] == 'BULLISH':
                # Si algún Low bajó a la zona, está mitigado
                if (test_data['low'] <= ob_range_high).any():
                    ob['mitigated'] = True
            else:
                # Si algún High subió a la zona, está mitigado
                if (test_data['high'] >= ob_range_low).any():
                    ob['mitigated'] = True
                    
    return obs
