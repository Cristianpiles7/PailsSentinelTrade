import pandas_ta as ta
import pandas as pd
import numpy as np
import logging
from datetime import datetime, timezone
from ..models.classifier import RegimeMode
from ..utils.tech_utils import get_asset_class

def get_safe(series, default=0.0):
    """Auxiliar para extraer valores de forma segura de una Serie de pandas."""
    try:
        if series is None or len(series) == 0: return default
        val = series.iloc[-1]
        return float(val) if not pd.isna(val) else default
    except: return default

def get_mtr_data(df_in, tf_minutes=5):
    if df_in is None or len(df_in) < 20: return None
    try:
        _rsi = ta.rsi(df_in['close'], length=14).iloc[-1]
        _adx = ta.adx(df_in['high'], df_in['low'], df_in['close'], length=14)['ADX_14'].iloc[-1]
        _v = df_in['tick_volume'].iloc[-1] if 'tick_volume' in df_in else 0
        _v_ma = ta.sma(df_in['tick_volume'], length=20).iloc[-1] if 'tick_volume' in df_in else 1
        return {"rsi": _rsi, "adx": _adx, "vol_rel": _v / _v_ma if _v_ma > 0 else 0}
    except: return None

logger = logging.getLogger("PST-Mean-Reversion")

class PSTMeanReversion:
    STRATEGY_NAME = "PST-Mean-Reversion"
    STRATEGY_TYPE = RegimeMode.RANGING # Especialista en rangos
    WEIGHT = 1.0 # Peso estándar

    async def calculate_signal(self, data_input, current_regime, user_levels=None):
        """
        Calcula señales de reversión a la media basadas en Bollinger Bands (2.5 dev) + RSI.
        Implementa un TP AGRESIVO que se cierra ligeramente antes de la media para asegurar el beneficio.
        """
        # 1. Adaptador de Datos
        df = None
        if isinstance(data_input, dict):
            df = data_input.get('m5') # Usamos M5 como base
        else:
            df = data_input
        
        # --- ASSET PROFILE LOADER ---
        symbol = "UNKNOWN"
        if isinstance(data_input, dict) and 'symbol' in data_input:
             symbol = data_input['symbol']
        asset_class = get_asset_class(symbol)

        # PARAMETROS BASE
        P_BB_LEN = 20
        P_BB_DEV = 2.5      # Exigente: Solo extremos reales
        P_RSI_LEN = 14
        P_RSI_OB = 70       # Sobrecompra
        P_RSI_OS = 30       # Sobreventa
        P_ADX_MAX = 50      # Filtro anti-tren: Si ADX > 50, no operar contra tendencia
        
        if asset_class == "CRYPTO":
            P_BB_DEV = 2.8   # Cripto es más volátil, exigimos más desviación
            P_ADX_MAX = 40   # Cripto en tendencia te mata rápido
        elif asset_class == "INDEX":
            P_RSI_OB = 75    # Indices suelen sobre-extenderse
            P_RSI_OS = 25

        if df is None or len(df) < 50:
             return self._build_neutral_result("Datos insuficientes (<50 velas)")

        # 2. CALCULO DE INDICADORES
        # RSI
        df['rsi'] = ta.rsi(df['close'], length=P_RSI_LEN)
        
        # Bollinger Bands
        bbands = ta.bbands(df['close'], length=P_BB_LEN, std=P_BB_DEV)
        if bbands is None:
             return self._build_neutral_result("Error calculando Bollinger Bands")
        
        # Nombres de columnas BB (pandas_ta suele usar BBL_20_2.5, BBM_20_2.5, BBU_20_2.5)
        # Buscamos dinámicamente
        lower_col = f"BBL_{P_BB_LEN}_{P_BB_DEV}"
        upper_col = f"BBU_{P_BB_LEN}_{P_BB_DEV}"
        mid_col = f"BBM_{P_BB_LEN}_{P_BB_DEV}"
        
        # Fallback si pandas_ta usa nombres genéricos
        if lower_col not in bbands.columns:
            # Intentar inferir
            cols = list(bbands.columns)
            lower_col = cols[0]
            mid_col = cols[1]
            upper_col = cols[2]

        df['bb_lower'] = bbands[lower_col]
        df['bb_upper'] = bbands[upper_col]
        df['bb_mid'] = bbands[mid_col]
        
        # ADX (Filtro de Fuerza)
        adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
        adx_val = 0
        if adx_df is not None and not adx_df.empty:
             adx_val = adx_df.iloc[-1, 0] # ADX_14 suele ser la primera columna

        # 3. ANALISIS DE LA VELA ACTUAL
        close = df['close'].iloc[-1]
        high = df['high'].iloc[-1]
        low = df['low'].iloc[-1]
        rsi = df['rsi'].iloc[-1]
        
        bb_upper = df['bb_upper'].iloc[-1]
        bb_lower = df['bb_lower'].iloc[-1]
        bb_mid = df['bb_mid'].iloc[-1]
        
        # Estado Anterior (Para detectar cruces/reingresos)
        prev_close = df['close'].iloc[-2]
        prev_rfc = df['close'].iloc[-2] # Reference for crossover
        prev_rsi = df['rsi'].iloc[-2]
        prev_bb_lower = df['bb_lower'].iloc[-2]
        prev_bb_upper = df['bb_upper'].iloc[-2]

        # 4. LOGICA DE ENTRADA Y FACTORES
        score = 0
        factor_groups = {
            "ESTADO": {"k": "Estado", "v": "Analizando Rango", "score": 0},
            "ESTRUCTURA": None,
            "RSI": None,
            "GATILLO": None,
            "ENTORNO": None,
            "VOLUMEN": None,
            "DIVERGENCIA": None,
            "ABSORCION": None
        }
        signal_type = "NEUTRAL"
        
        # --- 4.1 FILTRO MAESTRO: ADX (BLOQUEO) ---
        # Si ADX > 50 -> Bloqueo total (Tendencia imparable).
        is_adx_extreme = adx_val > P_ADX_MAX
        
        gate_failed = False
        block_reasons = []

        if is_adx_extreme:
             reason = f"Tendencia Fuerte (ADX:{adx_val:.1f} > {P_ADX_MAX})"
             factor_groups["ENTORNO"] = {"k": "Filtro ADX", "v": reason, "score": -50}
             block_reasons.append(reason)
             gate_failed = True # NO EARLY RETURN. Computamos el score normal pero bloqueamos la entrada.

        # --- 4.2 Métricas de Entorno (Siempre visibles) ---
        from ..utils.tech_utils import detect_divergence, detect_absorption
        div_type = detect_divergence(df)
        abs_type, is_climax = detect_absorption(df)
        
        # Divergencia Info
        if div_type:
            score_div = 20
            factor_groups["DIVERGENCIA"] = {"k": "Divergencia", "v": f"Detectada ({div_type})", "score": score_div}
        else:
            factor_groups["DIVERGENCIA"] = {"k": "Divergencia", "v": "No detectada", "score": 0}
            
        # Absorción Info (VSA)
        if abs_type:
            score_abs = 25 if is_climax else 15
            climax_txt = " (CLÍMAX VSA)" if is_climax else ""
            factor_groups["ABSORCION"] = {"k": "Absorción", "v": f"Presión Institucional{climax_txt}", "score": score_abs}
        else:
            factor_groups["ABSORCION"] = {"k": "Absorción", "v": "Neutro", "score": 0}

        mtr = get_mtr_data(df, tf_minutes=5)
        vol_rel = mtr['vol_rel'] if mtr else 0
        adx_now = mtr['adx'] if mtr else adx_val

        # ADX Logic
        if adx_now < 25:
            factor_groups["ENTORNO"] = {"k": "Fuerza ADX", "v": f"Ideal Lateral ({adx_now:.1f})", "score": 10}
        else:
            factor_groups["ENTORNO"] = {"k": "Fuerza ADX", "v": f"Moderado ({adx_now:.1f})", "score": 0}
        
        # Volume Logic
        if vol_rel > 1.2:
            factor_groups["VOLUMEN"] = {"k": "Volumen MTF", "v": f"Explosivo ({vol_rel:.1f}x)", "score": 10}
        elif vol_rel < 0.8:
            factor_groups["VOLUMEN"] = {"k": "Volumen MTF", "v": f"Insuficiente ({vol_rel:.1f}x)", "score": 0}
        else:
            factor_groups["VOLUMEN"] = {"k": "Volumen MTF", "v": f"Neutro ({vol_rel:.1f}x)", "score": 0}

        # Determinar Sesgo Potencial
        potential_buy = (low <= bb_lower) or (prev_close <= prev_bb_lower)
        potential_sell = (high >= bb_upper) or (prev_close >= prev_bb_upper)

        if potential_buy:
             signal_type = "BUY"
             # Estructura: Siempre +40 si toca banda (Base operativa)
             score += 40
             factor_groups["ESTRUCTURA"] = {"k": "Estructura", "v": f"Extrema Inf. ({bb_lower:.5f})", "score": 40}
             
             # RSI OS
             if rsi <= P_RSI_OS:
                 score += 15
                 factor_groups["RSI"] = {"k": "RSI", "v": f"Sobreventa ({rsi:.1f})", "score": 15}
             else:
                 factor_groups["RSI"] = {"k": "RSI", "v": f"Neutral ({rsi:.1f})", "score": 0}

             # GATILLOS
             trigger_buy_reentry = (prev_close < prev_bb_lower) and (close > bb_lower)
             trigger_buy_rsi = (prev_rsi < P_RSI_OS) and (rsi > P_RSI_OS)
             
             if trigger_buy_reentry:
                 score += 15
                 factor_groups["GATILLO"] = {"k": "Gatillo", "v": "Reingreso a Banda", "score": 15}
             elif trigger_buy_rsi:
                 score += 10
                 factor_groups["GATILLO"] = {"k": "Gatillo", "v": "Escape de Sobreventa", "score": 10}
             else:
                 factor_groups["GATILLO"] = {"k": "Gatillo", "v": "Sin disparador", "score": 0}
             
             if factor_groups["ENTORNO"]["score"] > 0: score += 10
             if factor_groups["VOLUMEN"]["score"] > 0: score += 10
             if div_type == "BULLISH": score += factor_groups["DIVERGENCIA"]["score"]
             if abs_type == "BUY_ABS": score += factor_groups["ABSORCION"]["score"]

        elif potential_sell:
             signal_type = "SELL"
             # Estructura: Siempre +40 si toca banda
             score += 40
             factor_groups["ESTRUCTURA"] = {"k": "Estructura", "v": f"Extrema Sup. ({bb_upper:.5f})", "score": 40}
             
             # RSI OB
             if rsi >= P_RSI_OB:
                 score += 15
                 factor_groups["RSI"] = {"k": "RSI", "v": f"Sobrecompra ({rsi:.1f})", "score": 15}
             else:
                 factor_groups["RSI"] = {"k": "RSI", "v": f"Neutral ({rsi:.1f})", "score": 0}

             # GATILLOS
             trigger_sell_reentry = (prev_close > prev_bb_upper) and (close < bb_upper)
             trigger_sell_rsi = (prev_rsi > P_RSI_OB) and (rsi < P_RSI_OB)
             
             if trigger_sell_reentry:
                 score += 15
                 factor_groups["GATILLO"] = {"k": "Gatillo", "v": "Reingreso a Banda", "score": 15}
             elif trigger_sell_rsi:
                 score += 10
                 factor_groups["GATILLO"] = {"k": "Gatillo", "v": "Escape de Sobrecompra", "score": 10}
             else:
                 factor_groups["GATILLO"] = {"k": "Gatillo", "v": "Sin disparador", "score": 0}

             if factor_groups["ENTORNO"]["score"] > 0: score += 10
             if factor_groups["VOLUMEN"]["score"] > 0: score += 10
             if div_type == "BEARISH": score += factor_groups["DIVERGENCIA"]["score"]
             if abs_type == "SELL_ABS": score += factor_groups["ABSORCION"]["score"]

        # Finalización de factores
        factors_final = []
        for k in ["ESTADO", "ESTRUCTURA", "RSI", "GATILLO", "ENTORNO", "VOLUMEN", "DIVERGENCIA", "ABSORCION"]:
             if factor_groups[k]:
                  factors_final.append(factor_groups[k])

        # --- VALIDACION FINAL ---
        final_score = min(100, score)

        # Cálculo de TP Agresivo (Busca el objetivo más cercano que esté ADELANTE del precio)
        # Para BUY: objetivos > close. Para SELL: objetivos < close.
        ema21_val = get_safe(ta.ema(df['close'], length=21))
        ema50_val = get_safe(ta.ema(df['close'], length=50))
        
        potential_targets = []
        if signal_type == "BUY":
            if bb_mid > close: potential_targets.append(bb_mid)
            if ema21_val > close: potential_targets.append(ema21_val)
            if ema50_val > close: potential_targets.append(ema50_val)
            
            if not potential_targets:
                # Si no hay objetivos técnicos claros arriba, usamos un objetivo conservador de 1.5 ATR
                atr_val = get_safe(ta.atr(df['high'], df['low'], df['close'], length=14))
                best_init_target = close + (atr_val * 1.5)
            else:
                best_init_target = min(potential_targets)
                
            # TP Agresivos: 10% antes del objetivo
            dist = abs(best_init_target - close)
            target_tp = best_init_target - (dist * 0.10)
            
        elif signal_type == "SELL":
            if bb_mid < close: potential_targets.append(bb_mid)
            if ema21_val < close: potential_targets.append(ema21_val)
            if ema50_val < close: potential_targets.append(ema50_val)
            
            if not potential_targets:
                atr_val = get_safe(ta.atr(df['high'], df['low'], df['close'], length=14))
                best_init_target = close - (atr_val * 1.5)
            else:
                best_init_target = max(potential_targets)
                
            # TP Agresivos: 10% antes del objetivo (Ej: 30.0 + 0.1 = 30.1)
            dist = abs(best_init_target - close)
            target_tp = best_init_target + (dist * 0.10)
        else:
            target_tp = 0

        if final_score >= 80 and not gate_failed:
             factor_groups["ESTADO"]["v"] = "Oportunidad Confirmada"
             return self._build_result(final_score, factors_final, f"Reversión {signal_type}", gate_failed, entry_signal=signal_type, direction=1 if signal_type == "BUY" else -1, tp_price=target_tp)
        
        # SIN BLOQUEO VISUAL (Cap removido para transparencia total)
        capped_score = final_score
        
        # Inyectar motivo de bloqueo si el score era prometedor
        if final_score >= 50 or gate_failed:
            txt_reason = ", ".join(block_reasons) if block_reasons else "Falta Gatillo Claro (Ej: Reingreso)"
            factors_final.insert(0, {"k": "REGLA MAESTRA", "v": txt_reason, "score": -50 if gate_failed else 0})

        if final_score >= 50 and not gate_failed:
             factor_groups["ESTADO"]["v"] = "Vigilando Extremo"
             return self._build_result(capped_score, factors_final, "Posible Reversión", gate_failed, entry_signal="NEUTRAL", direction=0, tp_price=target_tp)
        else:
             stat_msg = "Rango Neutral" if not gate_failed else "Bloqueo por Tendencia (Seguridad)"
             return self._build_result(capped_score, factors_final, stat_msg, gate_failed, direction=0)

    def get_dynamic_targets(self, df, direction):
        """
        Calcula el objetivo dinámico (BBM o EMAs) para una posición abierta.
        Busca el objetivo más cercano (agresivo) entre BBM, EMA21 y EMA50.
        Aplica un margen de seguridad del 10%.
        """
        if df is None or len(df) < 50: return None
        
        current_price = df['close'].iloc[-1]
        
        # 1. BBM (Media de Bollinger)
        bbands = ta.bbands(df['close'], length=20, std=2.5)
        bb_mid = bbands.iloc[-1, 1] if bbands is not None else None
        
        # 2. EMAs
        ema21 = get_safe(ta.ema(df['close'], length=21), default=None)
        ema50 = get_safe(ta.ema(df['close'], length=50), default=None)
        
        # Filtrar objetivos válidos según la dirección (siempre ADELANTE del precio)
        targets = []
        if direction == 1: # BUY (Objetivos por encima del precio)
            if bb_mid and bb_mid > current_price: targets.append(bb_mid)
            if ema21 and ema21 > current_price: targets.append(ema21)
            if ema50 and ema50 > current_price: targets.append(ema50)
        else: # SELL (Objetivos por debajo del precio)
            if bb_mid and bb_mid < current_price: targets.append(bb_mid)
            if ema21 and ema21 < current_price: targets.append(ema21)
            if ema50 and ema50 < current_price: targets.append(ema50)
            
        if not targets:
            # Si ya cruzamos todos los objetivos, devolvemos el precio actual 
            # (el executor decidirá si cierra con el margen de 2 puntos)
            return None
            
        # Elegimos el objetivo más cercano (el más agresivo)
        if direction == 1:
            best_target = min(targets) # El más bajo de los que están arriba
        else:
            best_target = max(targets) # El más alto de los que están abajo
            
        # Ajuste agresivo: Salir un 10% antes del objetivo
        dist = abs(best_target - current_price)
        margin = dist * 0.10
        
        return (best_target - margin) if direction == 1 else (best_target + margin)


    def _build_neutral_result(self, reason):
        return {
            "entry": 0,
            "atr": 0,
            "metadata": {
                "strategy": self.STRATEGY_NAME,
                "score": 0,
                "total_score": 0,
                "score_breakdown": {"Estado": reason},
                "factors_detailed": [],
                "direction": 0,
                "gate_failed": False
            },
            "score": 0
        }

    def _build_result(self, score, factors, status_msg, gate_failed=False, entry_signal="NEUTRAL", direction=0, tp_price=0):
        entry = 1 if entry_signal == "BUY" else (-1 if entry_signal == "SELL" else 0)
        return {
            "entry": entry,
            "atr": 0, # No recalculamos ATR aqui, lo hace el orchestrator
            "tp_price": tp_price, # NEW: Objetivo técnico específico
            "metadata": {
                "strategy": self.STRATEGY_NAME,
                "score": score,
                "total_score": score,
                "score_breakdown": {"Estado": status_msg},
                "factors_detailed": factors,
                "can_entry": (entry != 0) and not gate_failed,
                "gate_failed": gate_failed,
                "status": status_msg,
                "direction": direction,
                "tp_target": tp_price # Para mostrar en dashboard
            },
            "score": score
        }
