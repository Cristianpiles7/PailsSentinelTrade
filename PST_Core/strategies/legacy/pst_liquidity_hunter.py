import pandas as pd
import numpy as np
import pandas_ta as ta
import logging
from ..models.classifier import RegimeMode
from ..utils.tech_utils import get_asset_class, get_market_session, detect_order_blocks, detect_fvg, detect_absorption

def get_safe(series, default=0.0):
    try:
        if series is None or len(series) == 0: return default
        val = series.iloc[-1]
        return float(val) if not pd.isna(val) else default
    except: return default

logger = logging.getLogger("PST-Liquidity-Hunter")

class PSTLiquidityHunter:
    STRATEGY_NAME = "PST-Liquidity-Hunter"
    STRATEGY_TYPE = RegimeMode.VOLATILE # Especializada en rebotes en zonas de alta liquidez
    WEIGHT = 1.0 
    
    async def calculate_signal(self, data_input, current_regime, user_levels=None, **kwargs):
        """
        Estrategia SMC Pura: Busca Order Blocks (OBs) en M15.
        Agrega RSI, ADX, Distancias ATR y Descripciones al Payload UI.
        """
        if not isinstance(data_input, dict) or 'm15' not in data_input or 'm5' not in data_input:
            return self._build_neutral_result("Faltan datos requeridos (M15 / M5)")
            
        df_m15 = data_input.get('m15')
        df_m5 = data_input.get('m5')
        symbol = data_input.get('symbol', "UNKNOWN")
        asset_class = get_asset_class(symbol)
        
        if df_m15 is None or len(df_m15) < 30 or df_m5 is None or len(df_m5) < 30:
            return self._build_neutral_result("Velas insuficientes (<30)")

        obs = detect_order_blocks(df_m15, window=30)
        fresh_obs = [ob for ob in obs if not ob['mitigated']]
        
        if not fresh_obs:
            return self._build_neutral_result("Sin Order Blocks Frescos")

        current_atr = get_safe(ta.atr(df_m5['high'], df_m5['low'], df_m5['close'], length=14))
        latest_c = df_m5['close'].iloc[-1]
        latest_h = df_m5['high'].iloc[-1]
        latest_l = df_m5['low'].iloc[-1]
        
        # --- NUEVO CONTEXTO PARA UI (Matrix) ---
        rsi_m5 = get_safe(ta.rsi(df_m5['close'], length=14), default=50)
        adx_df = ta.adx(df_m5['high'], df_m5['low'], df_m5['close'], length=14)
        adx_m5 = 0
        if adx_df is not None and not adx_df.empty:
             adx_cols = [c for c in adx_df.columns if 'ADX' in c]
             if adx_cols: adx_m5 = adx_df[adx_cols[0]].iloc[-1]
             
        session_name = "UNKNOWN"
        if 'time' in df_m5.columns:
            last_dt = pd.to_datetime(df_m5['time'].iloc[-1], unit='s', utc=True)
            session_name = get_market_session(last_dt)
            
        abs_type, is_climax = detect_absorption(df_m5)
        
        score = 0
        factor_groups = {
            "ESTADO": {"k": "Estado", "v": "Vigilando Zonas", "score": 0, "desc": "Buscando mitigaciones de Order Blocks en M15."},
            "SMC": None,
            "GATILLO": None,
            "FILTROS": [],
            "CONTEXTO": [] # Para métricas visuales
        }
        
        signal_type = "NEUTRAL"
        target_tp = 0
        structural_sl = 0

        active_ob = None
        for ob in fresh_obs:
            buffer = current_atr * 0.2
            if ob['type'] == 'BULLISH':
                if latest_l <= (ob['top'] + buffer) and latest_c >= (ob['bottom'] - buffer):
                    active_ob = ob
                    signal_type = "BUY"
                    break
            elif ob['type'] == 'BEARISH':
                if latest_h >= (ob['bottom'] - buffer) and latest_c <= (ob['top'] + buffer):
                    active_ob = ob
                    signal_type = "SELL"
                    break

        gate_failed = False

        if active_ob:
            score += 45
            txt_tipo = "Demanda (Buy)" if signal_type == "BUY" else "Oferta (Sell)"
            factor_groups["SMC"] = {
                "k": f"Order Block {txt_tipo}", 
                "v": f"Tap Confirmado", 
                "score": 45,
                "desc": f"El precio ha retornado a un Order Block sin mitigar fijado en {active_ob['bottom']:.5f} - {active_ob['top']:.5f}."
            }
            
            if signal_type == "BUY":
                structural_sl = active_ob['bottom'] - (current_atr * 0.2)
                swing_high = df_m15['high'].tail(15).max()
                target_tp = max(swing_high, latest_c + (current_atr * 2.5))
            else:
                structural_sl = active_ob['top'] + (current_atr * 0.2)
                swing_low = df_m15['low'].tail(15).min()
                target_tp = min(swing_low, latest_c - (current_atr * 2.5))
                
            sl_dist = abs(latest_c - structural_sl)
            if sl_dist > (current_atr * 3.0):
                 gate_failed = True
                 factor_groups["FILTROS"].append({"k": "Riesgo", "v": "Stop Loss Inasumible (>3 ATR)", "score": -50})
        else:
            try:
                closest_ob = min(fresh_obs, key=lambda x: min(abs(latest_c - x['top']), abs(latest_c - x['bottom'])))
                dist_atr = min(abs(latest_c - closest_ob['top']), abs(latest_c - closest_ob['bottom'])) / current_atr
                tipo = "Soporte (Demanda)" if closest_ob['type'] == 'BULLISH' else "Resistencia (Oferta)"
                
                if dist_atr < 5.0:
                    pts_prox = round((5.0 - dist_atr) / 5.0 * 30)
                    score += pts_prox
                
                factor_groups["ESTADO"] = {
                    "k": "Estado", 
                    "v": f"Esperando mitigación {tipo} a {dist_atr:.1f} ATR", 
                    "score": score,
                    "desc": f"El precio está acercándose a un bloque institucional en M15. Distancia actual: {dist_atr:.1f} ATRs."
                }
                
                factor_groups["CONTEXTO"].append({"k": "OB Más Cercano", "v": f"{closest_ob['bottom']:.5f} - {closest_ob['top']:.5f}", "score": 0, "desc": "Nivel exacto del bloque institucional esperado."})
            except:
                pass
                
            factor_groups["CONTEXTO"].append({"k": "RSI (M5)", "v": f"{rsi_m5:.1f}", "score": 0, "desc": "Oscilador estándar."})
            if adx_m5 > 0:
                factor_groups["CONTEXTO"].append({"k": "ADX (M5)", "v": f"{adx_m5:.1f}", "score": 0, "desc": "Fuerza tendencial de la sesión actual."})
                
            return self._build_result(min(79, score), list(filter(None, [factor_groups.get("ESTADO")] + factor_groups["CONTEXTO"])), factor_groups["ESTADO"]["v"], direction=0)

        if signal_type == "BUY":
             if abs_type == "BUY_ABS":
                 score += 35
                 factor_groups["GATILLO"] = {"k": "Rechazo (VSA)", "v": "Absorción Alcista Confirmada", "score": 35, "desc": "Detección de vela martillo con volumen extremo rebotando en el bloque."}
             elif latest_c > df_m5['open'].iloc[-1]:
                 score += 15
                 factor_groups["GATILLO"] = {"k": "Vela", "v": "Cierre a favor", "score": 15, "desc": "Cierre de vela M5 protegiendo el Order Block."}
             else:
                 factor_groups["FILTROS"].append({"k": "Paciencia", "v": "Esperando rechazo alcista", "score": -20, "desc": "El precio tocó el bloque, pero la vela de M5 aún no muestra rechazo."})
                 gate_failed = True
                 
        elif signal_type == "SELL":
             if abs_type == "SELL_ABS":
                 score += 35
                 factor_groups["GATILLO"] = {"k": "Rechazo (VSA)", "v": "Distribución Bajista Confirmada", "score": 35, "desc": "Detección de vela bajista con volumen extremo rebotando en el bloque."}
             elif latest_c < df_m5['open'].iloc[-1]:
                 score += 15
                 factor_groups["GATILLO"] = {"k": "Vela", "v": "Cierre a favor", "score": 15, "desc": "Cierre de vela M5 protegiendo el Order Block."}
             else:
                 factor_groups["FILTROS"].append({"k": "Paciencia", "v": "Esperando rechazo bajista", "score": -20, "desc": "El precio tocó el bloque, pero la vela aún no muestra rechazo."})
                 gate_failed = True

        if session_name in ["LONDON", "NY", "OVERLAP"]:
            score += 10
            factor_groups["FILTROS"].append({"k": "Sesión", "v": f"Alta Liquidez ({session_name})", "score": 10, "desc": "Sesión ideal para cacerías con Momentum genuino."})
        else:
            score -= 10
            factor_groups["FILTROS"].append({"k": "Sesión", "v": f"Baja Liquidez ({session_name})", "score": -10, "desc": "Mayor riesgo de falsas roturas por falta de soporte."})

        factor_groups["CONTEXTO"].append({"k": "RSI (M5)", "v": f"{rsi_m5:.1f}", "score": 0})
        factor_groups["CONTEXTO"].append({"k": "Distancia ATR", "v": f"Mitigando OB", "score": 0})

        final_score = min(100, max(0, score))

        factors_list = []
        for k in ["ESTADO", "SMC", "GATILLO"]:
            if factor_groups[k]: factors_list.append(factor_groups[k])
            
        for f in factor_groups["CONTEXTO"]:
            factors_list.append(f)
            
        for f in factor_groups["FILTROS"]:
            factors_list.append(f)

        if final_score >= 75 and not gate_failed:
             factor_groups["ESTADO"]["v"] = f"MITIGACIÓN Confirmada ({signal_type})"
             return self._build_result(final_score, factors_list, factor_groups["ESTADO"]["v"], gate_failed, signal_type, 1 if signal_type == "BUY" else -1, target_tp, structural_sl)
        elif final_score >= 40:
             factor_groups["ESTADO"]["v"] = "Rozando OB (Esperando Confirmación)"
             return self._build_result(final_score, factors_list, factor_groups["ESTADO"]["v"], True, "NEUTRAL", 0)
        else:
             return self._build_result(final_score, factors_list, "Setup Débil", True, direction=0)

    def _build_neutral_result(self, reason):
        return {
            "entry": 0, "atr": 0, "tp_price": 0, "sl_price": 0,
            "metadata": {
                "strategy": self.STRATEGY_NAME, "score": 0, "total_score": 0,
                "score_breakdown": {"Estado": reason}, "factors_detailed": [],
                "direction": 0, "gate_failed": False, "status": reason
            },
            "score": 0
        }

    def _build_result(self, score, factors, status_msg, gate_failed=False, entry_signal="NEUTRAL", direction=0, tp_price=0, sl_price=0):
        entry = 1 if entry_signal == "BUY" and not gate_failed else (-1 if entry_signal == "SELL" and not gate_failed else 0)
        
        breakdown = {}
        for f in factors:
            if 'k' in f and 'v' in f and 'score' in f:
                breakdown[f['k']] = f"{f['v']} ({'+' if f['score'] >= 0 else ''}{f['score']})"
                
        return {
            "entry": entry,
            "atr": 0, 
            "signal": entry_signal,
            "target_price": tp_price if entry != 0 else 0,
            "metadata": {
                "strategy": self.STRATEGY_NAME,
                "score": score,
                "total_score": score,
                "score_breakdown": breakdown,
                "factors_detailed": factors,
                "can_entry": (entry != 0),
                "gate_failed": gate_failed,
                "status": status_msg,
                "direction": direction,
                "target_price_tp": tp_price if entry != 0 else 0,
                "target_price_sl": sl_price if entry != 0 else 0,
                "mode": "SMC_MITIGATION"
            },
            "score": score
        }
