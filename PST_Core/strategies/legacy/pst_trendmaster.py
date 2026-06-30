import pandas as pd
import numpy as np
import logging

logger = logging.getLogger("PST.TrendMaster")

class PSTTrendMaster:
    STRATEGY_NAME = "PST-TrendMaster"
    STRATEGY_TYPE = "ALL"  # Opera en cualquier régimen donde haya líneas

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period=14):
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        return true_range.rolling(period).mean()

    async def calculate_signal(self, mtf_data, current_regime=None, user_levels=None, spread_points=0, spread_dist=0, **kwargs) -> dict:
        """
        Analiza las reglas de Breakout para un símbolo dado contra todas sus líneas puestas por el usuario.
        Retorna signal compatible con PST: {"score": 0-100, "entry": 1 | -1 | 0, "atr": float, "metadata": dict}
        """
        df = mtf_data.get('m5') if isinstance(mtf_data, dict) else mtf_data
        
        if df is None or len(df) < 20:
            return {
                "score": 0.0, 
                "entry": 0, 
                "metadata": {
                    "status": "ESPERANDO_DATOS",
                    "factors_detailed": [{"k": "Estado", "v": "Esperando Datos", "score": 0}]
                }
            }
            
        # Extraer user_lines (El orquestador pasa un dict con {levels, config, symbol})
        user_lines = user_levels.get('levels', []) if user_levels else []
        
        # Filtramos solo líneas diagonales (o las que tengan dos puntos completos)
        lines = [l for l in user_lines if l.get('type') in ['DIAGONAL', 'HORIZONTAL'] and l.get('time1_ts') and l.get('time2_ts')]
        
        atr_series = self.calculate_atr(df, 14)
        current_atr = atr_series.iloc[-2] if len(atr_series) > 1 and not pd.isna(atr_series.iloc[-2]) else 0.0

        if not lines:
            return {
                "score": 0.0, 
                "entry": 0, 
                "atr": current_atr, 
                "metadata": {
                    "status": "SIN LÍNEAS",
                    "factors_detailed": [{"k": "Estado", "v": "Sin Líneas", "score": 0, "desc": "No hay líneas dibujadas por el usuario para este símbolo."}]
                }
            }

        # Trabajar siempre con la última vela cerrada para evitar falsos breakouts intrabar.
        current_candle = df.iloc[-2]
        previous_candle = df.iloc[-3]
        
        # El DataFrame usa la fecha como índice (DatetimeIndex), obtenemos el Unix timestamp en segundos
        current_time_ts = int(current_candle.name.timestamp())
        previous_time_ts = int(previous_candle.name.timestamp())
        
        best_score = 0.0
        best_entry = 0
        triggered_line_id = None
        status_msg = "ANALIZANDO"

        for line in lines:
            t1 = line['time1_ts']
            p1 = line['price']
            t2 = line['time2_ts']
            p2 = line['price2']

            if t1 == t2: continue # Evitar division por cero
            
            # Si horizontal, slope = 0, p2 igual da lo mismo, usamos p1
            slope = (p2 - p1) / (t2 - t1) if line.get('type') == 'DIAGONAL' else 0

            # Proyectar precio de la línea
            line_price_current = slope * (current_time_ts - t1) + p1
            line_price_previous = slope * (previous_time_ts - t1) + p1

            body_size = abs(current_candle['close'] - current_candle['open'])
            candle_range = max(0.00001, current_candle['high'] - current_candle['low'])
            body_ratio = body_size / candle_range
            breakout_buffer = max(current_atr * 0.10, spread_dist * 2 if spread_dist > 0 else 0.0)
            has_breakout_intent = body_size >= (current_atr * 0.35) and body_ratio >= 0.50

            # Breakout Detection: exigir cierre confirmado más allá de la línea y una vela con intención.
            is_breakout_up = (
                (previous_candle['close'] <= line_price_previous) and
                (current_candle['close'] > line_price_current + breakout_buffer) and
                has_breakout_intent
            )
            is_breakout_down = (
                (previous_candle['close'] >= line_price_previous) and
                (current_candle['close'] < line_price_current - breakout_buffer) and
                has_breakout_intent
            )

            # Filtro por Modo de Línea
            line_mode = line.get('mode', 'BOTH').upper()
            
            allow_buy = line_mode in ['BOTH', 'RESISTANCE']
            allow_sell = line_mode in ['BOTH', 'SUPPORT']

            if is_breakout_up and allow_buy:
                best_score = 100.0
                best_entry = 1
                triggered_line_id = line['id']
                status_msg = f"BREAKOUT ALCISTA ({'Resistencia' if line_mode=='RESISTANCE' else 'Línea'} {line['id']})"
                break
            elif is_breakout_down and allow_sell:
                best_score = 100.0
                best_entry = -1
                triggered_line_id = line['id']
                status_msg = f"BREAKOUT BAJISTA ({'Soporte' if line_mode=='SUPPORT' else 'Línea'} {line['id']})"
                break

            # Puntuación Progresiva por Proximidad
            if current_atr > 0:
                distance = abs(current_candle['close'] - line_price_current)
                dist_in_atr = distance / current_atr
                
                # Scoring exponencial: 80% a 1 ATR, declina suavemente hasta ~10% a 5 ATR
                score = 85.0 * np.exp(-0.5 * dist_in_atr)
                
                if score > best_score:
                    best_score = score
                    # La proximidad solo informa; no debe disparar orden real.
                    best_entry = 0
                    status_msg = f"ACECHANDO (Dist: {dist_in_atr:.2f} ATR)"

        factors_detailed = [
            {"k": "Estado", "v": status_msg, "score": 0},
            {"k": "ATR (M5)", "v": f"{current_atr:.5f}", "score": 0}
        ]
        
        # Añadir detalles de todas las líneas detectadas (o la mejor)
        if triggered_line_id:
            factors_detailed.append({"k": "Línea Ejecutada", "v": f"ID {triggered_line_id}", "score": 100, "desc": "Se ha detectado un cruce del precio sobre la línea."})
        elif best_score > 0:
            factors_detailed.append({"k": "Proximidad", "v": f"{round(best_score, 1)}%", "score": round(best_score), "desc": f"Cercanía a la mejor línea proyectada."})

        return {
            "score": round(best_score, 1),
            "entry": best_entry,
            "atr": current_atr,
            "metadata": {
                "status": status_msg,
                "triggered_line_id": triggered_line_id,
                "factors_detailed": factors_detailed
            }
        }
