import pandas_ta as ta
import pandas as pd
import numpy as np
import logging
from ..config import (
    SL_ATR_MULTIPLIER, 
    MAX_SCALPER_SL_POINTS, 
    MIN_RR_RATIO
)

logger = logging.getLogger("PST-Scalper-Pro")

class PSTScalperPro:
    STRATEGY_NAME = "PST-Scalper-Pro"
    STRATEGY_TYPE = "ALL"
    
    def __init__(self):
        # Medias base para el sistema de rotura
        self.ema_fast = 9
        self.ema_mid = 21
        self.rsi_length = 14
        self.adx_length = 14
        # Ratio R:R Mínimo para Scalping
        self.min_rr = MIN_RR_RATIO 
        # Filtro de Distancia (Anti-Chasing): No entrar si el precio está a más de 0.8 ATR de la media (Reduce riesgo, mejora R:R)
        self.max_dist_atr = 0.8 

    async def calculate_signal(self, mtf_data, current_regime=None, user_levels=None, spread_points=0, spread_dist=0, **kwargs):
        """
        Scalper Pro v3.0 (Clean Slate): Estrategia minimalista basada en rotura de EMA 21.
        """
        # 1. Extracción de datos (M1 para scalper)
        df = mtf_data.get('m1') if isinstance(mtf_data, dict) else mtf_data
        if df is None: df = mtf_data.get('m5') if isinstance(mtf_data, dict) else None
        
        if df is None or len(df) < 50:
            return {
                "score": 0, 
                "signal": "NEUTRAL", 
                "metadata": {"mode": "ESPERANDO_DATOS", "factors_detailed": [{"k": "Estado", "v": "Cargando Velas", "score": 0}]}
            }

        # 2. Cálculo de Indicadores
        ema9 = ta.ema(df['close'], length=self.ema_fast)
        ema21 = ta.ema(df['close'], length=self.ema_mid)
        ema50 = ta.ema(df['close'], length=50) # Filtro Institucional
        rsi = ta.rsi(df['close'], length=self.rsi_length)
        adx_df = ta.adx(df['high'], df['low'], df['close'], length=self.adx_length)
        atr = ta.atr(df['high'], df['low'], df['close'], length=14)
        vol_ma = ta.sma(df['tick_volume'], length=20)
        
        # --- NUEVO: CÁLCULOS PARA SQUEEZE (Bollinger vs Keltner) ---
        bb = ta.bbands(df['close'], length=20, std=2.0)
        kc = ta.kc(df['high'], df['low'], df['close'], length=20, scalar=1.5)
        
        if bb is None or kc is None or len(bb) < 1 or len(kc) < 1:
            return {"score": 0, "signal": "NEUTRAL", "metadata": {"mode": "CALCULANDO_SQUEEZE"}}
        
        # Acceso robusto por posición (BBL=0, BBM=1, BBU=2 | KCL=0, KCB=1, KCU=2)
        try:
            lower_bb = bb.iloc[:, 0]
            upper_bb = bb.iloc[:, 2]
            lower_kc = kc.iloc[:, 0]
            upper_kc = kc.iloc[:, 2]
            
            # Un Squeeze ocurre cuando las bandas de Bollinger están dentro de los canales de Keltner
            is_squeeze = (upper_bb < upper_kc) and (lower_bb > lower_kc)
            is_squeeze = is_squeeze.iloc[-1]
        except Exception as e:
            # Fallback si fallan las posiciones
            is_squeeze = False

        if ema21 is None or rsi is None or adx_df is None or vol_ma is None:
            return {"score": 0, "signal": "NEUTRAL", "metadata": {"mode": "CALCULANDO"}}

        # 3. Datos en Punto de Decisión
        c_price = df['close'].iloc[-1]
        p_price = df['close'].iloc[-2]
        c_ema21 = ema21.iloc[-1]
        p_ema21 = ema21.iloc[-2]
        c_ema9 = ema9.iloc[-1]
        curr_rsi = rsi.iloc[-1]
        curr_adx = adx_df['ADX_14'].iloc[-1]
        prev_adx = adx_df['ADX_14'].iloc[-2]
        is_adx_rising = curr_adx > prev_adx
        curr_atr = atr.iloc[-1]
        
        # Datos de Volumen y Vela
        curr_vol = df['tick_volume'].iloc[-1]
        mean_vol = vol_ma.iloc[-2] # Media de velas CERRADAS
        rel_vol = curr_vol / mean_vol if mean_vol > 0 else 1.0
        
        c_open = df['open'].iloc[-1]
        c_high = df['high'].iloc[-1]
        c_low = df['low'].iloc[-1]
        body_size = abs(c_price - c_open)
        total_range = max(0.00001, c_high - c_low)
        body_ratio = body_size / total_range

        score = 0
        entry = 0
        factors_detailed = []
        mode_label = "ANALIZANDO"

        # --- FILTRO DE SPREAD DINÁMICO ---
        # Si el spread supera el 25% del ATR, la operación no es rentable por costes.
        spread_threshold = curr_atr * 0.25
        is_spread_ok = spread_dist <= spread_threshold        # --- FILTRO DE TENDENCIA SUPERIOR (M5 y M15) ---
        # Validamos que el cierre M5 y M15 estén alineados con la EMA21 respectiva
        df_m5 = mtf_data.get('m5')
        df_m15 = mtf_data.get('m15')
        
        trend_m5 = 0 
        trend_m15 = 0
        
        if df_m5 is not None and len(df_m5) >= 30:
            ema21_m5 = ta.ema(df_m5['close'], length=21)
            if ema21_m5 is not None:
                trend_m5 = 1 if df_m5['close'].iloc[-1] > ema21_m5.iloc[-1] else -1
        
        if df_m15 is not None and len(df_m15) >= 30:
            ema21_m15 = ta.ema(df_m15['close'], length=21)
            if ema21_m15 is not None:
                trend_m15 = 1 if df_m15['close'].iloc[-1] > ema21_m15.iloc[-1] else -1

        # Lógica de Disparo v3.1: M5 + M15 + ADX Obligatorio
        adx_min = 20 # Filtro de fuerza mandatorio
        
        # Buffer de seguridad (10% del ATR) para evitar ruido
        break_threshold = curr_atr * 0.1
        
        # Verificación de Asentamiento (Settlement) - REDUCIDO A 2 VELAS
        # Requerimos que al menos las 2 últimas velas previas estuvieran del lado contrario para mayor agilidad.
        hist_prices = df['close'].iloc[-4:-2]
        hist_ema21 = ema21.iloc[-4:-2]
        
        was_clearly_below = (hist_prices < hist_ema21).all()
        was_clearly_above = (hist_prices > hist_ema21).all()

        # CIERRE DE VELA CONFIRMADO
        p_c_price = df['close'].iloc[-2]
        p_p_price = df['close'].iloc[-3]
        p_ema21 = ema21.iloc[-2]
        p_p_ema21 = ema21.iloc[-3]
        
        # Filtro Institucional EMA50
        c_ema50 = ema50.iloc[-1]

        # --- NEW: FILTRO DE ZONA DE VALOR (PULLBACK RECIENTE) ---
        # No queremos entrar en una rotura si el precio viene de muy lejos (chasing).
        # Exigimos que en las últimas 5 velas haya habido un acercamiento a la EMA21 (< 0.5 ATR)
        dist_ema_hist = abs(df['close'].iloc[-6:-1] - ema21.iloc[-6:-1])
        has_recent_pullback = (dist_ema_hist < (curr_atr * 0.5)).any()

        # DISPARO BASE: Rotura + M5/M15 + Spread OK + RSI Libre + Pullback Reciente + ADX > 20
        is_cross_up = (p_p_price <= p_p_ema21) and (p_c_price > (p_ema21 + break_threshold)) and was_clearly_below and trend_m5 == 1 and trend_m15 == 1 and is_spread_ok and curr_rsi < 70 and has_recent_pullback and curr_adx > adx_min
        is_cross_down = (p_p_price >= p_p_ema21) and (p_c_price < (p_ema21 - break_threshold)) and was_clearly_above and trend_m5 == -1 and trend_m15 == -1 and is_spread_ok and curr_rsi > 30 and has_recent_pullback and curr_adx > adx_min

        if is_cross_up:
            logger.info(f"🔍 [SET-UP UP] Rotura alcista (Pullback OK). Tendencia M5 OK. Spread OK.")
            mode_label = "ROTURA_ALZA"
            score = 50 # Base reducida para exigir confirmaciones extras
            factors_detailed.append({"k": "Disparador", "v": "Cierre > EMA21 ↑", "score": 50})
            factors_detailed.append({"k": "Pullback", "v": "ZONA VALOR OK", "score": 10})
            score += 10
            
            # Filtro 1: Squeeze (Indica explosión de volatilidad inminente)
            if is_squeeze:
                score += 20
                factors_detailed.append({"k": "Squeeze", "v": "VOL COMPRIMIDA OK", "score": 20})
            
            # Filtro 2: Tendencia Superior M5 y M15
            factors_detailed.append({"k": "Trend M5", "v": "ALINEADA ↑", "score": 10})
            factors_detailed.append({"k": "Trend M15", "v": "ESTRUCTURA ↑", "score": 10})
            score += 10
            
            # Filtro 3: Intención
            if body_ratio > 0.5:
                score += 15
                factors_detailed.append({
                    "k": "Cuerpo Vela", 
                    "v": f"Sólido {body_ratio*100:.0f}%", 
                    "score": 15,
                    "desc": "Mide el porcentaje del cuerpo respecto al rango total. > 50% indica intención alcista clara en M1."
                })
            else:
                factors_detailed.append({
                    "k": "Cuerpo Vela", 
                    "v": "Indecisión", 
                    "score": 0,
                    "desc": "Vela con cuerpo pequeño o mechas largas. Se prefiere > 50% para confirmar dirección."
                })
            
            # Filtro 4: Volumen (Sensibilidad M1: 1.2x)
            if rel_vol > 1.2:
                score += 15
                factors_detailed.append({
                    "k": "Volumen", 
                    "v": f"Fuerte {rel_vol:.1f}x", 
                    "score": 15,
                    "desc": "Se requiere > 1.2x volumen relativo respecto a la media de 20 para confirmar la rotura alcista."
                })
            else:
                factors_detailed.append({
                    "k": "Volumen", 
                    "v": "Normal", 
                    "score": 0,
                    "desc": "Volumen bajo/normal. Una rotura con > 1.2x tendría más fiabilidad profesional."
                })

            # Filtro 5: Fuerza (ADX > 15)
            if curr_adx > 15:
                score += 15
                factors_detailed.append({
                    "k": "Fuerza ADX", 
                    "v": f"Potencia {curr_adx:.0f} ↑", 
                    "score": 15,
                    "desc": "ADX > 15 indica que la tendencia alcista tiene inercia operativa suficiente."
                })
            else:
                factors_detailed.append({
                    "k": "Fuerza ADX", 
                    "v": "Iniciando", 
                    "score": 0,
                    "desc": "Fuerza de tendencia baja (ADX < 15). Se monitoriza el inicio del movimiento."
                })
            
            # Filtro 6: Espacio RSI (No Sobrecompra > 75)
            if curr_rsi < 75:
                score += 10
                factors_detailed.append({
                    "k": "Espacio RSI", 
                    "v": "No Extendido", 
                    "score": 10,
                    "desc": "El RSI por debajo de 75 indica que aún hay 'aire' o margen para que el precio suba más."
                })
            else:
                score -= 40 
                factors_detailed.append({
                    "k": "Espacio RSI", 
                    "v": "Saturado", 
                    "desc": "Mercado sobrecomprado (>75). Alto riesgo de retroceso inmediato."
                })

            # Filtro 7: EMA50 Institucional
            if p_c_price > c_ema50:
                score += 10
                factors_detailed.append({
                    "k": "EMA50 Inst.", 
                    "v": "A Favor ↑", 
                    "score": 10,
                    "desc": "Precio por encima de la EMA50, confirmando tendencia estructural intacta."
                })
            else:
                factors_detailed.append({
                    "k": "EMA50 Inst.", 
                    "v": "En Contra", 
                    "score": 0,
                    "desc": "Operando contra la dirección principal de la EMA50 de M1."
                })

            # Evaluación de entrada con umbral dinámico (desde DB o 80 fallback)
            threshold = kwargs.get('score_threshold') or 80
            if score >= threshold:
                entry = 1

        elif is_cross_down:
            logger.info(f"🔍 [SET-UP DOWN] Rotura bajista (Pullback OK). Tendencia M5 OK. Spread OK.")
            mode_label = "ROTURA_BAJA"
            score = 50 # Base reducida para exigir confirmaciones extras
            factors_detailed.append({"k": "Disparador", "v": "Cierre < EMA21 ↓", "score": 50})
            factors_detailed.append({"k": "Pullback", "v": "ZONA VALOR OK", "score": 10})
            score += 10

            # Filtro 1: Squeeze
            if is_squeeze:
                score += 20
                factors_detailed.append({"k": "Squeeze", "v": "VOL COMPRIMIDA OK", "score": 20})
            
            # Filtro 2: Tendencia Superior M5 y M15
            factors_detailed.append({"k": "Trend M5", "v": "ALINEADA ↓", "score": 10})
            factors_detailed.append({"k": "Trend M15", "v": "ESTRUCTURA ↓", "score": 10})
            score += 10
            
            # Filtro 3: Intención
            if body_ratio > 0.5:
                score += 15
                factors_detailed.append({
                    "k": "Cuerpo Vela", 
                    "v": f"Sólido {body_ratio*100:.0f}%", 
                    "score": 15,
                    "desc": "Mide el porcentaje del cuerpo respecto al rango total. > 50% indica intención clara y pocas mechas de indecisión."
                })
            else:
                factors_detailed.append({
                    "k": "Cuerpo Vela", 
                    "v": "Indecisión", 
                    "score": 0,
                    "desc": "Vela con cuerpo pequeño o mechas largas. Se prefiere > 50% para confirmar dirección."
                })
            
            # Filtro 4: Volumen (Sensibilidad M1: 1.2x)
            if rel_vol > 1.2:
                score += 15
                factors_detailed.append({
                    "k": "Volumen", 
                    "v": f"Fuerte {rel_vol:.1f}x", 
                    "score": 15,
                    "desc": "Se requiere > 1.2x volumen relativo respecto a la media para confirmar la rotura bajista."
                })
            else:
                factors_detailed.append({
                    "k": "Volumen", 
                    "v": "Normal", 
                    "score": 0,
                    "desc": "Volumen bajo/normal. Una rotura con > 1.2x tendría más fiabilidad profesional."
                })

            # Filtro 5: Fuerza (ADX > 15)
            if curr_adx > 15:
                score += 15
                factors_detailed.append({
                    "k": "Fuerza ADX", 
                    "v": f"Potencia {curr_adx:.0f} ↓", 
                    "score": 15,
                    "desc": "ADX > 15 indica que la tendencia bajista tiene inercia operativa suficiente."
                })
            else:
                factors_detailed.append({
                    "k": "Fuerza ADX", 
                    "v": "Iniciando", 
                    "score": 0,
                    "desc": "Fuerza de tendencia baja (ADX < 15). Se monitoriza el inicio del movimiento bajista."
                })
            
            # Filtro 6: Espacio RSI (No Sobreventa < 25)
            if curr_rsi > 25:
                score += 10
                factors_detailed.append({
                    "k": "Espacio RSI", 
                    "v": "No Extendido", 
                    "score": 10,
                    "desc": "El RSI por encima de 25 indica que no hay una saturación extrema de ventas aún."
                })
            else:
                score -= 40
                factors_detailed.append({
                    "k": "Espacio RSI", 
                    "v": "Saturado", 
                    "desc": "Mercado en sobreventa extrema (<25). Alto riesgo de rebote inmediato."
                })

            # Filtro 7: EMA50 Institucional
            if p_c_price < c_ema50:
                score += 10
                factors_detailed.append({
                    "k": "EMA50 Inst.", 
                    "v": "A Favor ↓", 
                    "score": 10,
                    "desc": "Precio por debajo de la EMA50, confirmando tendencia estructural bajista."
                })
            else:
                factors_detailed.append({
                    "k": "EMA50 Inst.", 
                    "v": "En Contra", 
                    "score": 0,
                    "desc": "Operando contra la dirección principal de la EMA50 de M1."
                })

            # Evaluación de entrada con umbral dinámico (desde DB o 80 fallback)
            threshold = kwargs.get('score_threshold') or 80
            if score >= threshold:
                entry = -1

        # --- GESTIÓN DE RUIDO Y PUNTUACIÓN PASIVA (User Req: Transparencia Total) ---
        if not is_cross_up and not is_cross_down:
            mode_label = "ACECHANDO"
            passive_score = 0
            
            # 1. Proximidad a EMA21 (Zona de Valor)
            dist_ema21 = abs(c_price - c_ema21)
            dist_ema21_atr = dist_ema21 / curr_atr if curr_atr > 0 else 0
            if dist_ema21_atr < 0.8:
                passive_score += 25
                factors_detailed.append({
                    "k": "Proximidad", 
                    "v": f"ZONA EMA21 ({dist_ema21_atr:.1f} ATR)", 
                    "score": 25,
                    "desc": "El precio está en zona de valor cerca de la media rápida (EMA21). Ideal para buscar roturas."
                })
            else:
                factors_detailed.append({
                    "k": "Proximidad", 
                    "v": f"Lejos ({dist_ema21_atr:.1f} ATR)", 
                    "score": 0,
                    "desc": "El precio está alejado de la EMA21. Se requiere un acercamiento o pullback antes de operar."
                })
            
            # 2. Alineación de tendencia y Sesgo
            if (c_price > c_ema21 and c_ema9 > c_ema21):
                passive_score += 15
                factors_detailed.append({
                    "k": "Sesgo", 
                    "v": "Alineación Alcista", 
                    "score": 15,
                    "desc": "Precio por encima de EMA21 y EMA9 confirmando presión de compra inmediata."
                })
            elif (c_price < c_ema21 and c_ema9 < c_ema21):
                passive_score += 15
                factors_detailed.append({
                    "k": "Sesgo", 
                    "v": "Alineación Bajista", 
                    "score": 15,
                    "desc": "Precio por debajo de EMA21 y EMA9 confirmando presión de venta inmediata."
                })
            else:
                factors_detailed.append({
                    "k": "Sesgo", 
                    "v": "Indefinido/Cruce", 
                    "score": 0,
                    "desc": "Las medias están cruzándose o el precio está en terreno neutral."
                })
            
            # 3. Fuerza de Mercado (ADX) - SIEMPRE VISIBLE
            adx_pts = 15 if curr_adx > 20 else 0
            adx_status = "OK" if curr_adx > 20 else "Bajo"
            factors_detailed.append({
                "k": "Fuerza (ADX)", 
                "v": f"{adx_status} ({curr_adx:.0f})", 
                "score": adx_pts,
                "desc": "El ADX mide la intensidad de la tendencia. Se requiere ADX > 20 para sumar puntos de fuerza."
            })
            passive_score += adx_pts
            
            # 4. Volumen Relativo - SIEMPRE VISIBLE
            vol_pts = 10 if rel_vol > 1.2 else 0
            vol_status = "Fuerte" if rel_vol > 1.2 else "Débil"
            factors_detailed.append({
                "k": "Volumen", 
                "v": f"{vol_status} ({rel_vol:.1f}x)", 
                "score": vol_pts,
                "desc": "Volumen relativo respecto a la media de 20 velas. Se requiere > 1.2x para confirmar interés profesional."
            })
            passive_score += vol_pts
                
            # 5. Squeeze (Preparación) - SIEMPRE VISIBLE
            sq_pts = 10 if is_squeeze else 0
            sq_status = "ACTIVO" if is_squeeze else "No"
            factors_detailed.append({
                "k": "Squeeze", 
                "v": sq_status, 
                "score": sq_pts,
                "desc": "Indica si el mercado está comprimido (Bollinger dentro de Keltner). La salida del squeeze suele ser explosiva."
            })
            passive_score += sq_pts

            # 6. Intención (Cuerpo de vela) - SIEMPRE VISIBLE
            body_pts = 10 if body_ratio > 0.6 else 0
            body_status = f"{body_ratio*100:.0f}%"
            factors_detailed.append({
                "k": "Cuerpo Vela", 
                "v": body_status, 
                "score": body_pts,
                "desc": "Mide el tamaño del cuerpo frente a las mechas. Una vela sólida (> 60%) indica convicción en el movimiento."
            })
            passive_score += body_pts

            # 7. RSI (Momentum/Agotamiento) - SIEMPRE VISIBLE
            rsi_pts = -20 if (curr_rsi > 75 or curr_rsi < 25) else 10
            rsi_status = "Extremo" if rsi_pts < 0 else "Normal"
            factors_detailed.append({
                "k": "RSI (M1)", 
                "v": f"{rsi_status} ({curr_rsi:.0f})", 
                "score": rsi_pts,
                "desc": "Zonas extremas (>75 o <25) indican agotamiento y riesgo de retroceso inminente."
            })
            passive_score += rsi_pts

            # Aplicar Puntuación Pasiva (Máximo 79 para no disparar entrada sin rotura)
            score = min(79, passive_score)
            
            # Mensajes de aviso originales si aplica
            if was_clearly_below and c_price > c_ema21:
                if not has_recent_pullback:
                     factors_detailed.append({"k": "Aviso", "v": "ENTRY EXTENDIDA (Anti-Chase)", "score": 0, "desc": "El precio está demasiado lejos de la EMA21. Esperando pullback."})
                else:
                     factors_detailed.append({"k": "Aviso", "v": "Rotura Débil (Buffer)", "score": 0, "desc": "El precio cruzó la EMA21 pero sin la fuerza o el margen necesario."})
            elif was_clearly_above and c_price < c_ema21:
                factors_detailed.append({"k": "Aviso", "v": "Rotura Débil (Buffer)", "score": 0, "desc": "El precio cruzó la EMA21 pero sin la fuerza o el margen necesario."})

        # 4. Decisión Final
        # Gestión de Riesgo Scalping: SL basado en ATR pero limitado a MAX_SCALPER_SL_POINTS
        sl_dist = curr_atr * SL_ATR_MULTIPLIER
        
        # Extracción de Spread para visibilidad (User Req)
        spread_pts = kwargs.get('spread_points', 0)
        spread_dist = kwargs.get('spread_dist', 0)
        
        symbol_upper = kwargs.get('symbol', '').upper()
        if any(idx in symbol_upper for idx in ['US500', 'NAS100', 'GER40', 'US30']):
            one_point = 1.0
        elif 'XAU' in symbol_upper or 'GOLD' in symbol_upper:
            one_point = 0.1 # 10 cents = 1 point
        elif any(fx in symbol_upper for fx in ['EURUSD', 'GBPUSD', 'AUDUSD', 'USDCAD']):
            one_point = 0.0001
        else:
            # Fallback dinámico basado en spread si está disponible
            one_point = spread_dist / max(0.1, spread_points) if spread_points > 0 else (0.0001 if c_price < 10 else 0.01)

        max_sl_dist = one_point * MAX_SCALPER_SL_POINTS
        
        # Clipping SL stricts
        actual_sl_points = sl_dist / one_point if one_point > 0 else 0
        
        if sl_dist > max_sl_dist:
            sl_dist = max_sl_dist
            actual_sl_points = MAX_SCALPER_SL_POINTS
            factors_detailed.append({"k": "Riesgo Cap", "v": f"Limitado a {MAX_SCALPER_SL_POINTS} pts", "score": 0})
        else:
            factors_detailed.append({"k": "Riesgo Est.", "v": f"{actual_sl_points:.1f} pts", "score": 0})
        
        tp_dist = sl_dist * self.min_rr
        
        # Filtro de Spread (Más tolerante: 25% del TP)
        if spread_dist > (tp_dist * 0.25): 
            score -= 20
            factors_detailed.append({
                "k": "Coste Spread", 
                "v": "ALTO", 
                "score": -20,
                "desc": f"Spread actual: {spread_points} pts ({spread_dist:.5f}). Supera el 25% del TP esperado, reduciendo drásticamente la esperanza matemática."
            })
            mode_label = "SPREAD_ALTO"
            logger.warning(f"🚨 [SCALPER] Spread demasiado alto ({spread_points} pts) para TP esperado.")
        else:
            factors_detailed.append({
                "k": "Coste Spread", 
                "v": "Bajo OK", 
                "score": 0,
                "desc": f"Spread actual: {spread_points} pts ({spread_dist:.5f}). El coste es aceptable para los objetivos de Scalping."
            })

        final_score = min(100, max(0, score))
        
        # Modo Acecho (Stalking): Si la puntuación es cercana al umbral
        threshold = kwargs.get('score_threshold') or 80
        is_stalking = False
        if entry == 0 and final_score >= 65 and (is_cross_up or is_cross_down):
            is_stalking = True

        return {
            "strategy": self.STRATEGY_NAME,
            "score": final_score,
            "signal": "BUY" if entry == 1 else ("SELL" if entry == -1 else "NEUTRAL"),
            "entry": entry,
            "is_stalking": is_stalking,
            "direction": 1 if is_cross_up else (-1 if is_cross_down else 0),
            "target_price": c_ema21, # El objetivo del stalking es el pullback a la media
            "atr": curr_atr,
            "sl_dist": sl_dist,
            "tp_dist": tp_dist,
            "tp_price": c_price + (tp_dist if entry == 1 else -tp_dist),
            "metadata": {
                "mode": mode_label,
                "factors_detailed": factors_detailed,
                "rsi": round(curr_rsi, 1),
                "adx": round(curr_adx, 1),
                "risk_pts": round(actual_sl_points, 1),
                "rr_ratio": self.min_rr,
                "threshold_used": threshold
            }
        }

    def check_exit_signal(self, mtf_data, p_type: str) -> bool:
        """
        Salida dinámica v3.3: Exige CIERRE DE VELA confirmado al otro lado de la EMA21.
        """
        df_m1 = mtf_data.get('m1') if isinstance(mtf_data, dict) else mtf_data
        if df_m1 is None or len(df_m1) < 25: return False
        
        # Calculamos EMA21 de M1 para salida rápida pero filtrada
        ema_exit = ta.ema(df_m1['close'], length=21)
        if ema_exit is None: return False
        
        # Usamos la vela ANTERIOR (cerrada) para evitar ruidos de mechas en tiempo real
        last_close = df_m1['close'].iloc[-2]
        last_ema = ema_exit.iloc[-2]
        
        if p_type == "BUY" and last_close < last_ema:
            logger.info("🚪 [EXIT] Cierre por debajo de EMA21 (M1 Confirmado)")
            return True
        if p_type == "SELL" and last_close > last_ema:
            logger.info("🚪 [EXIT] Cierre por encima de EMA21 (M1 Confirmado)")
            return True
            
        return False
