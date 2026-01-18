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
        # Convertir a numeric (segundos) forzando errores a NaN y luego eliminando o rellenando
        t_numeric = pd.to_numeric(t_series, errors='coerce')
        
        # Si la conversión directa falló (ej. Datetime objects), intentar as_datetime
        if t_numeric.isna().any():
            t_numeric = pd.to_datetime(t_series, errors='coerce').astype(np.int64) // 10**9
            # Filtro de seguridad para valores negativos de epoch (si pd.to_datetime falla)
            t_numeric = t_numeric.where(t_numeric > 0, 0)

        # Asegurar que sea float/int nativo de python para evitar ufunc issues
        last_t_num = float(t_numeric.iloc[-1])
        time_delta = float(t_numeric.diff().tail(50).median()) if len(t_numeric) > 1 else 300.0
        last_idx = int(df_copy.index[-1])
        
        # 5. Generar Puntos
        # El canal empieza en el primer pivote usado
        start_idx = int(min(highs.index[0], lows.index[0]))
        upper_line = []
        lower_line = []

        # Históricos
        for i in range(start_idx, last_idx + 1):
            if i in df_copy.index:
                t_val = df_copy.at[i, 'time']
                # Si t_val es datetime, lo guardamos así, el dashboard se encarga
                upper_line.append({"time": t_val, "value": float(slope_h * i + upper_intercept)})
                lower_line.append({"time": t_val, "value": float(slope_l * i + lower_intercept)})

        # Proyección
        for i in range(1, projection + 1):
            idx_fut = last_idx + i
            t_fut = last_t_num + (time_delta * i)
            
            # Formatear salida consistente
            if isinstance(df_copy['time'].iloc[0], str):
                try: t_out = str(datetime.fromtimestamp(int(t_fut)))
                except: t_out = float(t_fut)
            else:
                t_out = float(t_fut) if not isinstance(df_copy['time'].iloc[0], datetime) else datetime.fromtimestamp(int(t_fut))
                
            upper_line.append({"time": t_out, "value": float(slope_h * idx_fut + upper_intercept)})
            lower_line.append({"time": t_out, "value": float(slope_l * idx_fut + lower_intercept)})

        params = {
            "slope_h": slope_h, "intercept_h": upper_intercept,
            "slope_l": slope_l, "intercept_l": lower_intercept,
            "last_idx": last_idx
        }
        return upper_line, lower_line, params

    except Exception as e:
        print(f"Error in calculate_channel_boundary: {e}")
        import traceback
        traceback.print_exc()
        return [], [], None
