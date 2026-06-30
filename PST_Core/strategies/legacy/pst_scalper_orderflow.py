import pandas_ta as ta
import pandas as pd
import numpy as np
import logging
from datetime import datetime
from ..config import (
    SL_ATR_MULTIPLIER, 
    MAX_SCALPER_SL_POINTS, 
    MIN_RR_RATIO,
    LONDRES_SESSION_START,
    LONDRES_SESSION_END,
    NY_SESSION_START,
    NY_SESSION_END,
    SESSION_SESSIONS_ONLY
)
from ..utils.tech_utils import get_asset_class

logger = logging.getLogger("PST-Scalper-OrderFlow")

class PSTScalperOrderFlow:
    STRATEGY_NAME = "PST-Scalper-OrderFlow"
    STRATEGY_TYPE = "ALL"

    def __init__(self):
        self.ema_trend = 50
        self.rsi_length = 14
        self.vol_ma_length = 20
        self.min_rr = 2.0  # R:R profesional alto por precisión de Order Block
        
        # Perfiles de Activos Optimizados para Order Flow
        self.ASSET_PROFILES = {
            "CRYPTO": {
                "vol_requisite": 1.4,      
                "min_rr": 2.0,             
                "score_threshold": 75,
                "ob_lookback": 15,
                "max_extension_atr": 0.8  # No fomo
            },
            "METAL": {
                "vol_requisite": 1.35,      
                "min_rr": 2.2,            
                "score_threshold": 78,
                "ob_lookback": 12,
                "max_extension_atr": 0.75
            },
            "INDEX": {
                "vol_requisite": 1.4,
                "min_rr": 2.2,
                "score_threshold": 80,
                "ob_lookback": 12,
                "max_extension_atr": 0.7
            },
            "FOREX": {
                "vol_requisite": 1.25,
                "min_rr": 2.0,
                "score_threshold": 72,
                "ob_lookback": 10,
                "max_extension_atr": 0.9
            }
        }
        self.DEFAULT_PROFILE = self.ASSET_PROFILES["FOREX"]

    def _is_market_liquid(self, symbol: str) -> bool:
        """Verifica si estamos dentro de las sesiones horarias líquidas (Londres o NY)."""
        asset_class = get_asset_class(symbol)
        if asset_class == "CRYPTO":
            return True  # Cripto opera 24/7 sin restricciones de liquidez
            
        if not SESSION_SESSIONS_ONLY:
            return True
            
        now_time = datetime.now().strftime("%H:%M")
        
        # Validación de rangos
        in_london = LONDRES_SESSION_START <= now_time <= LONDRES_SESSION_END
        in_ny = NY_SESSION_START <= now_time <= NY_SESSION_END
        
        return in_london or in_ny

    def _calculate_vwap(self, df: pd.DataFrame) -> pd.Series:
        """Calcula el VWAP diario que se resetea a las 00:00."""
        try:
            df_copy = df.copy()
            # Asegurar columna de tiempo
            if 'time' in df_copy.columns:
                df_copy['datetime'] = pd.to_datetime(df_copy['time'])
            else:
                df_copy['datetime'] = pd.to_datetime(df_copy.index)
                
            df_copy['date_only'] = df_copy['datetime'].dt.date
            
            typical_price = (df_copy['high'] + df_copy['low'] + df_copy['close']) / 3
            df_copy['tp_vol'] = typical_price * df_copy['tick_volume']
            
            # Agrupar por fecha y acumular
            cum_tp_vol = df_copy.groupby('date_only')['tp_vol'].cumsum()
            cum_vol = df_copy.groupby('date_only')['tick_volume'].cumsum()
            
            vwap = cum_tp_vol / cum_vol
            return vwap
        except Exception as e:
            logger.error(f"❌ Error calculando VWAP: {e}")
            # Fallback a EMA 50 si falla
            return ta.ema(df['close'], length=self.ema_trend)

    def _detect_order_blocks(self, df: pd.DataFrame, ob_lookback: int, curr_atr: float):
        """
        Escanea las últimas velas buscando zonas de Order Blocks institucionales.
        Devuelve el bloque alcista más reciente y el bloque bajista más reciente.
        """
        bullish_ob = None  # {'high': x, 'low': y, 'index': idx, 'timestamp': t}
        bearish_ob = None
        
        vol_ma = ta.sma(df['tick_volume'], length=self.vol_ma_length)
        if vol_ma is None:
            return None, None
            
        for i in range(len(df) - 5, len(df) - 1): # Dejar la última vela cerrada activa
            c_close = df['close'].iloc[i]
            c_open = df['open'].iloc[i]
            c_high = df['high'].iloc[i]
            c_low = df['low'].iloc[i]
            c_vol = df['tick_volume'].iloc[i]
            mean_vol = vol_ma.iloc[i]
            
            body_size = abs(c_close - c_open)
            vol_rel = c_vol / mean_vol if mean_vol > 0 else 1.0
            
            # 1. Detectar velas de fuerte impulso (Ignición)
            # Debe tener un volumen relativo de al menos 1.3x y un tamaño mínimo de cuerpo
            if vol_rel > 1.3 and body_size > (curr_atr * 0.4):
                if c_close > c_open:
                    # Impulso Alcista: El Order Block es la vela bajista anterior más cercana
                    for j in range(i - 1, max(0, i - 4), -1):
                        prev_close = df['close'].iloc[j]
                        prev_open = df['open'].iloc[j]
                        if prev_close < prev_open: # Es bajista
                            bullish_ob = {
                                "high": df['high'].iloc[j],
                                "low": df['low'].iloc[j],
                                "median": (df['high'].iloc[j] + df['low'].iloc[j]) / 2,
                                "index": j,
                                "type": "BULLISH_DEMAND"
                            }
                            break
                elif c_close < c_open:
                    # Impulso Bajista: El Order Block es la vela alcista anterior más cercana
                    for j in range(i - 1, max(0, i - 4), -1):
                        prev_close = df['close'].iloc[j]
                        prev_open = df['open'].iloc[j]
                        if prev_close > prev_open: # Es alcista
                            bearish_ob = {
                                "high": df['high'].iloc[j],
                                "low": df['low'].iloc[j],
                                "median": (df['high'].iloc[j] + df['low'].iloc[j]) / 2,
                                "index": j,
                                "type": "BEARISH_SUPPLY"
                            }
                            break
                            
        return bullish_ob, bearish_ob

    async def calculate_signal(self, mtf_data, current_regime=None, user_levels=None, spread_points=0, spread_dist=0, **kwargs):
        """
        Estrategia de Scalping Profesional OrderFlow: VWAP Diario + Order Blocks + CHoCH/BOS + Gestión de Sesión.
        """
        symbol = kwargs.get("symbol", "").upper()
        
        # 1. Filtro de Sesión Líquida
        if not self._is_market_liquid(symbol):
            return {
                "score": 0,
                "signal": "NEUTRAL",
                "metadata": {
                    "mode": "FUERA_DE_SESION",
                    "factors_detailed": [{"k": "Estado", "v": "Fuera de Sesión Líquida", "score": 0}]
                }
            }
            
        df_m1 = mtf_data.get('m1') if isinstance(mtf_data, dict) else mtf_data
        df_m3 = mtf_data.get('m3')
        df_m5 = mtf_data.get('m5')
        df_m15 = mtf_data.get('m15')
        
        signals_found = []
        
        # Evaluar temporalidades prioritarias (M5 y M3 son óptimas para Order Blocks en intradía)
        if df_m5 is not None and len(df_m5) >= 50:
            sig_m5 = self._evaluate_tf(df_m5, df_m15, spread_dist, "M5", current_regime=current_regime, **kwargs)
            if sig_m5['entry'] != 0: return sig_m5
            signals_found.append(sig_m5)
            
        if df_m3 is not None and len(df_m3) >= 50:
            sig_m3 = self._evaluate_tf(df_m3, df_m15, spread_dist, "M3", current_regime=current_regime, **kwargs)
            if sig_m3['entry'] != 0: return sig_m3
            signals_found.append(sig_m3)
            
        if df_m1 is not None and len(df_m1) >= 50:
            sig_m1 = self._evaluate_tf(df_m1, df_m5, spread_dist, "M1", current_regime=current_regime, **kwargs)
            if sig_m1['entry'] != 0: return sig_m1
            signals_found.append(sig_m1)
            
        if signals_found:
            best_sig = max(signals_found, key=lambda x: x['score'])
            return best_sig
            
        return {
            "score": 0, 
            "signal": "NEUTRAL", 
            "metadata": {
                "mode": "ESPERANDO_ESTRUCTURA",
                "factors_detailed": [{"k": "Estado", "v": "Esperando Order Block", "score": 0}]
            }
        }

    def _evaluate_tf(self, df_base, df_trend, spread_dist, tf_label, **kwargs):
        symbol = kwargs.get("symbol", "").upper()
        asset_class = get_asset_class(symbol)
        profile = self.ASSET_PROFILES.get(asset_class, self.DEFAULT_PROFILE)
        
        signal_idx = -2 # Última vela cerrada para evitar repintado
        
        c_price = df_base['close'].iloc[signal_idx]
        c_open = df_base['open'].iloc[signal_idx]
        c_high = df_base['high'].iloc[signal_idx]
        c_low = df_base['low'].iloc[signal_idx]
        c_vol = df_base['tick_volume'].iloc[signal_idx]
        
        # Indicadores Básicos
        atr = ta.atr(df_base['high'], df_base['low'], df_base['close'], length=14)
        rsi = ta.rsi(df_base['close'], length=self.rsi_length)
        vol_ma = ta.sma(df_base['tick_volume'], length=self.vol_ma_length)
        
        if atr is None or rsi is None or vol_ma is None:
            return {"score": 0, "signal": "NEUTRAL", "metadata": {"mode": f"CALC_IND_{tf_label}", "factors_detailed": []}}
            
        curr_atr = atr.iloc[signal_idx]
        curr_rsi = rsi.iloc[signal_idx]
        mean_vol = vol_ma.iloc[signal_idx]
        rel_vol = c_vol / mean_vol if mean_vol > 0 else 1.0
        
        # Calcular el VWAP Diario Dinámico
        vwap_series = self._calculate_vwap(df_base)
        curr_vwap = vwap_series.iloc[signal_idx]
        
        # 1. Filtro Tendencial Maestro por VWAP
        price_above_vwap = c_price > curr_vwap
        price_below_vwap = c_price < curr_vwap
        
        # 2. Detección de Bloques de Órdenes
        bull_ob, bear_ob = self._detect_order_blocks(df_base, profile["ob_lookback"], curr_atr)
        
        # 3. Estructura de Mercado y Cambio de Carácter (CHoCH)
        # Comparamos con el máximo y mínimo de las últimas 15 velas para ver si rompió estructura
        recent_window = df_base.iloc[-17:-2]
        recent_max = recent_window['high'].max()
        recent_min = recent_window['low'].min()
        
        has_broken_structure_up = c_price > recent_max and rel_vol > 1.3
        has_broken_structure_down = c_price < recent_min and rel_vol > 1.3
        
        # 4. Lógica de Mitigación (Acecho y Entrada)
        # Entramos en compra si el precio actual está testeando el Order Block Alcista (Demand) por arriba
        # y estamos en tendencia alcista por encima de VWAP.
        # Entramos en venta si el precio actual está testeando el Order Block Bajista (Supply) por abajo
        # y estamos en tendencia bajista por debajo de VWAP.
        
        is_mitigating_demand = False
        is_mitigating_supply = False
        
        # Umbrales y puntuaciones
        score = 0
        entry = 0
        factors_detailed = []
        mode_label = f"ACECHO_{tf_label}"
        target_price_tp = 0.0
        target_price_sl = 0.0
        
        # Entrada en retroceso al OB (el precio toca la zona del OB en la vela actual o anterior)
        if bull_ob:
            ob_high = bull_ob["high"]
            ob_low = bull_ob["low"]
            # El precio entra en la zona del OB (mitigación de demanda) sin romper el mínimo del OB
            if price_above_vwap and (df_base['low'].iloc[-2:] <= ob_high).any() and (df_base['close'].iloc[-2:] >= ob_low).all():
                is_mitigating_demand = True
                
        if bear_ob:
            ob_high = bear_ob["high"]
            ob_low = bear_ob["low"]
            # El precio entra en la zona del OB (mitigación de oferta) sin romper el máximo del OB
            if price_below_vwap and (df_base['high'].iloc[-2:] >= ob_low).any() and (df_base['close'].iloc[-2:] <= ob_high).all():
                is_mitigating_supply = True

        entry_threshold = profile["score_threshold"]
        
        if is_mitigating_demand and not has_broken_structure_down:
            mode_label = f"OB_REBOUND_UP_{tf_label}"
            score = 80
            entry = 1
            
            factors_detailed.append({"k": "TF", "v": tf_label, "score": 0})
            factors_detailed.append({"k": "Estrategia", "v": f"OB ALCISTA ({tf_label})", "score": score})
            factors_detailed.append({"k": "Filtro VWAP", "v": "A favor (Precio > VWAP)", "score": 10})
            score += 10
            
            if has_broken_structure_up:
                factors_detailed.append({"k": "Estructura", "v": "BOS Confirmado (+5)", "score": 5})
                score += 5
                
            if curr_rsi < 45: # RSI en zona de descuento o neutral
                factors_detailed.append({"k": "RSI Descuento", "v": f"{curr_rsi:.1f} (+5)", "score": 5})
                score += 5
                
            # Stop Loss ceñido: justo debajo del mínimo de la vela del Order Block
            # Añadimos un 15% del ATR como buffer para evitar barridos
            sl_buffer = curr_atr * 0.15
            target_price_sl = bull_ob["low"] - sl_buffer
            
            sl_dist = c_price - target_price_sl
            # R:R institucional de 2.0x mínimo
            target_price_tp = c_price + (sl_dist * self.min_rr)
            
        elif is_mitigating_supply and not has_broken_structure_up:
            mode_label = f"OB_REBOUND_DN_{tf_label}"
            score = 80
            entry = -1
            
            factors_detailed.append({"k": "TF", "v": tf_label, "score": 0})
            factors_detailed.append({"k": "Estrategia", "v": f"OB BAJISTA ({tf_label})", "score": score})
            factors_detailed.append({"k": "Filtro VWAP", "v": "A favor (Precio < VWAP)", "score": 10})
            score += 10
            
            if has_broken_structure_down:
                factors_detailed.append({"k": "Estructura", "v": "BOS Confirmado (+5)", "score": 5})
                score += 5
                
            if curr_rsi > 55: # RSI en zona de prima
                factors_detailed.append({"k": "RSI Prima", "v": f"{curr_rsi:.1f} (+5)", "score": 5})
                score += 5
                
            # Stop Loss ceñido: justo por encima del máximo del Order Block
            sl_buffer = curr_atr * 0.15
            target_price_sl = bear_ob["high"] + sl_buffer
            
            sl_dist = target_price_sl - c_price
            target_price_tp = c_price - (sl_dist * self.min_rr)

        # Si no hay señal de entrada
        if entry == 0:
            passive_score = 0
            status_msg = ""
            
            if not self._is_market_liquid(symbol):
                status_msg = "Esperando Horario Líquido"
            elif not bull_ob and not bear_ob:
                status_msg = "Esperando Formación de Order Block"
            elif price_above_vwap and bull_ob and not is_mitigating_demand:
                status_msg = f"Esperando Pullback al OB Alcista ({bull_ob['high']:.5f})"
            elif price_below_vwap and bear_ob and not is_mitigating_supply:
                status_msg = f"Esperando Pullback al OB Bajista ({bear_ob['low']:.5f})"
            else:
                status_msg = "Monitoreando Ordenes"
                
            factors_detailed.append({"k": f"ESTADO ({tf_label})", "v": status_msg, "score": 0})
            
            # Puntuaciones informativas del estado actual
            vwap_dist = abs(c_price - curr_vwap) / curr_atr if curr_atr > 0 else 1.0
            pts_vwap = max(0, (1.0 - min(vwap_dist, 1.0)) * 25)
            passive_score += pts_vwap
            factors_detailed.append({"k": "Proximidad VWAP", "v": f"{vwap_dist:.1f} ATR", "score": round(pts_vwap, 0)})
            
            vol_pts = round(min(rel_vol, 2.0) / 2.0 * 15, 0)
            passive_score += vol_pts
            factors_detailed.append({"k": "Volumen Rel.", "v": f"{rel_vol:.1f}x", "score": vol_pts})
            
            # Límite de score pasivo por debajo del umbral
            score = min(entry_threshold - 1, passive_score)
            
        final_score = min(100, max(0, score))
        
        # Filtro de Spread
        if entry != 0:
            target_sl_dist = abs(c_price - target_price_sl)
            if spread_dist > (target_sl_dist * 0.40):
                final_score -= 25
                entry = 0
                factors_detailed.append({
                    "k": "Coste Spread", "v": "Rechazo por spread alto", "score": -25
                })
                mode_label = f"SPREAD_ALTO_{tf_label}"
                
        return {
            "strategy": self.STRATEGY_NAME,
            "score": final_score,
            "signal": "BUY" if entry == 1 else ("SELL" if entry == -1 else "NEUTRAL"),
            "entry": entry,
            "is_stalking": False,
            "direction": entry,
            "target_price": target_price_tp if target_price_tp > 0 else curr_vwap,
            "atr": curr_atr,
            "metadata": {
                "mode": mode_label,
                "factors_detailed": factors_detailed,
                "rsi": round(curr_rsi, 1),
                "vwap": round(curr_vwap, 5),
                "target_price_tp": round(target_price_tp, 5) if entry != 0 else 0,
                "target_price_sl": round(target_price_sl, 5) if entry != 0 else 0,
                "rr_ratio": round(self.min_rr, 2),
                "score_threshold": round(entry_threshold, 2),
                "use_breakeven": True, # Forzar breakeven dinámico para proteger el trade
                "threshold_used": entry_threshold
            }
        }

    def check_exit_signal(self, mtf_data, p_type: str) -> bool:
        """
        Salida dinámica rápida de OrderFlow:
        Salimos si hay una vela cerrada en contra de la tendencia del VWAP diario.
        """
        df_m1 = mtf_data.get('m1') if isinstance(mtf_data, dict) else mtf_data
        if df_m1 is None or len(df_m1) < 25: return False
        
        signal_idx = -2
        c_price = df_m1['close'].iloc[signal_idx]
        
        vwap_series = self._calculate_vwap(df_m1)
        curr_vwap = vwap_series.iloc[signal_idx]
        
        if p_type == "BUY" and c_price < curr_vwap:
            return True # Pérdida de valor justo
        if p_type == "SELL" and c_price > curr_vwap:
            return True
            
        return False
