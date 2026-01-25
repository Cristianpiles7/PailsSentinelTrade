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

def calculate_manual_score(price, lvl_price, l_type, rsi, vol_val, vol_ma, is_green=None, is_red=None, trend_slope=0):
    """
    Motor de puntuacion ULTRA-LÓGICO y ESTRICTO.
    Retorna: (total_score, action_name, factor_details_list)
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
        factors.append({"k": "Proximidad", "v": f"{abs_dist_pct:.2f}%", "score": round(base_score)})
        
        # RSI Context
        rsi_ok = (is_res and rsi > 60) or (is_sup and rsi < 40)
        if rsi_ok:
            score += 20
            factors.append({"k": "RSI Context", "v": f"OK ({rsi:.1f})", "score": 20})
        else:
            factors.append({"k": "RSI Context", "v": f"Neutral ({rsi:.1f})", "score": 0})

    # 2. LÓGICA DE RUPTURA (BREAKOUT)
    elif is_breakout and abs_dist_pct <= THR_NEUTRAL:
        action = "ROTURA"
        
        # --- MOMENTUM KILL ABSOLUTO: Si la vela es del color contrario, el score es 0 ---
        if (is_res and is_red): return 0, "FALSO-BREAK", [{"k": "Cierre", "v": "🛑 Vela Roja (Bajista)", "score": 0}]
        if (is_sup and is_green): return 0, "FALSO-BREAK", [{"k": "Cierre", "v": "🛑 Vela Verde (Alcista)", "score": 0}]

        score += 30 # Base
        factors.append({"k": "Base Ruptura", "v": f"Confirmada ({abs_dist_pct:.2f}%)", "score": 30})
        
        # FILTROS ESTRICTOS
        if vol_val > vol_ma:
            score += 35
            factors.append({"k": "Volumen", "v": "Confirmado (Vol > MA)", "score": 35})
        else:
            factors.append({"k": "Volumen", "v": "Bajo (No Confirmado)", "score": 0})
        
        rsi_bias_ok = (is_res and rsi > 50) or (is_sup and rsi < 50)
        if rsi_bias_ok:
            score += 15
            factors.append({"k": "Impulso RSI", "v": f"A favor ({rsi:.1f})", "score": 15})
        else:
            factors.append({"k": "Impulso RSI", "v": f"Neutro ({rsi:.1f})", "score": 0})
            
        momentum_ok = (is_res and is_green) or (is_sup and is_red)
        if momentum_ok:
            score += 15
            factors.append({"k": "Vela", "v": "Fuerza Confirmada", "score": 15})
        else:
            factors.append({"k": "Vela", "v": "Indecisión", "score": 0})

    return round(min(100, score)), action, factors
