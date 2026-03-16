import pandas as pd
import numpy as np
import pandas_ta as ta
import logging
from datetime import datetime, timezone
from ..models.classifier import RegimeMode
from ..utils.tech_utils import get_asset_class, get_market_session, detect_absorption, detect_order_blocks, detect_fvg

def get_safe(series, default=0.0):
    try:
        if series is None or len(series) == 0: return default
        val = series.iloc[-1]
        return float(val) if not pd.isna(val) else default
    except: return default

logger = logging.getLogger("PST-Liquidity-Hunter")

class PSTLiquidityHunter:
    STRATEGY_NAME = "PST-Liquidity-Hunter"
    STRATEGY_TYPE = RegimeMode.VOLATILE # Especialista en cacerías y volatilidad
    WEIGHT = 1.0 
    
    # Tolerancia: Qué tan lejos puede romper el nivel para que siga siendo un 'Sweep' y no una rotura real.
    MAX_SWEEP_ATR = 1.5 
    
    async def calculate_signal(self, data_input, current_regime, user_levels=None, **kwargs):
        """
        Calcula señales de "cacería de liquidez" (Stop Hunts / Sweeps).
        Busca que el precio rompa el alto/bajo del día anterior o sesión asiática, pero que el cierre
        de la vela se regrese por dentro del rango.
        """
        if not isinstance(data_input, dict) or 'm15' not in data_input or 'd1' not in data_input:
            return self._build_neutral_result("Faltan datos requeridos (M15 / D1)")
            
        df = data_input.get('m15') # La lectura principal de Sweeps se hace en M15
        df_d1 = data_input.get('d1')
        symbol = data_input.get('symbol', "UNKNOWN")
        asset_class = get_asset_class(symbol)
        
        if df is None or len(df) < 50 or df_d1 is None or len(df_d1) < 2:
            return self._build_neutral_result("Velas insuficientes (<50)")

        # 1. IDENTIFICAR NIVELES CLAVE DE LIQUIDEZ (PDH / PDL - Previous Daily High/Low)
        # Tomamos el alto y bajo de la vela Diaria anterior
        pd_high = df_d1['high'].iloc[-2]
        pd_low = df_d1['low'].iloc[-2]
        
        # 2. CALCULAR MÉTRICAS ACTUALES
        current_atr = get_safe(ta.atr(df['high'], df['low'], df['close'], length=14))
        
        latest_c = df['close'].iloc[-1]
        latest_h = df['high'].iloc[-1]
        latest_l = df['low'].iloc[-1]
        prev_c = df['close'].iloc[-2]
        prev_h = df['high'].iloc[-2]
        prev_l = df['low'].iloc[-2]
        
        # Entorno Operativo VSA & Sesión
        session_name = "UNKNOWN"
        if 'time' in df.columns:
            last_dt = pd.to_datetime(df['time'].iloc[-1], unit='s', utc=True)
            session_name = get_market_session(last_dt)
            
        abs_type, is_climax = detect_absorption(df)
        
        score = 0
        factor_groups = {
            "ESTADO": {"k": "Estado", "v": "Vigilando Liquidez", "score": 0},
            "SWEEP": None,
            "VSA": None,
            "SMC": None,
            "MOMENTO": None,
            "FILTROS": []
        }
        
        signal_type = "NEUTRAL"
        target_tp = 0
        structural_sl = 0

        # 3. LÓGICA DE DETECCIÓN DE SWEEP (BARRIDO DE STOPS)
        
        # BEAR TRAP (Cacería en el PDL -> Compramos)
        # Condición: La vela actual (o la anterior) bajó del PDL (latest_l < pd_low),
        # pero el cierre de la vela actual ya está por encima (latest_c > pd_low).
        pdl_sweep_detected = (latest_l < pd_low or prev_l < pd_low) and latest_c > pd_low
        
        # BULL TRAP (Cacería en el PDH -> Vendemos)
        # Condición: La vela actual/anterior subió del PDH (latest_h > pd_high),
        # pero el cierre de la actual está por debajo (latest_c < pd_high).
        pdh_sweep_detected = (latest_h > pd_high or prev_h > pd_high) and latest_c < pd_high
        
        gate_failed = False
        
        if pdl_sweep_detected:
            signal_type = "BUY"
            
            # Qué tan profundo fue el barrido (Riesgo de rotura genuina)
            sweep_dist = pd_low - min(latest_l, prev_l)
            if sweep_dist > (current_atr * self.MAX_SWEEP_ATR):
                gate_failed = True
                factor_groups["FILTROS"].append({"k": "Sweep Size", "v": "Excesivo (Posible Rotura Real)", "score": -50})
            
            score += 40
            factor_groups["SWEEP"] = {"k": "Barrido PDL", "v": f"Daily Low Recuperado ({pd_low:.5f})", "score": 40}
            structural_sl = min(latest_l, prev_l) - (current_atr * 0.1) # Debajo del barrido
            
        elif pdh_sweep_detected:
            signal_type = "SELL"
            
            sweep_dist = max(latest_h, prev_h) - pd_high
            if sweep_dist > (current_atr * self.MAX_SWEEP_ATR):
                gate_failed = True
                factor_groups["FILTROS"].append({"k": "Sweep Size", "v": "Excesivo (Posible Rotura Real)", "score": -50})
                
            score += 40
            factor_groups["SWEEP"] = {"k": "Barrido PDH", "v": f"Daily High Recuperado ({pd_high:.5f})", "score": 40}
            structural_sl = max(latest_h, prev_h) + (current_atr * 0.1) # Encima del barrido
            
        else:
            # Distancia a los niveles
            dist_h = abs(latest_c - pd_high) / current_atr
            dist_l = abs(latest_c - pd_low) / current_atr
            
            if dist_h < 1.0:
                factor_groups["ESTADO"] = {"k": "Estado", "v": "Rondando PDH (Alto del Día)", "score": 0}
            elif dist_l < 1.0:
                factor_groups["ESTADO"] = {"k": "Estado", "v": "Rondando PDL (Bajo del Día)", "score": 0}
                
            return self._build_result(0, list(filter(None, factor_groups.values())), factor_groups["ESTADO"]["v"], direction=0)

        # 4. CONFIDENTES DE VOLUMEN (VSA)
        vol_ma = ta.sma(df['tick_volume'], length=20).iloc[-1] if 'tick_volume' in df.columns else 1
        curr_vol = df['tick_volume'].iloc[-1]
        vol_rel = curr_vol / vol_ma if vol_ma > 0 else 0
        
        if vol_rel >= 1.5:
            score += 20
            factor_groups["VSA"] = {"k": "Volumen Relativo", "v": f"Confirmado ({vol_rel:.1f}x)", "score": 20}
        else:
            factor_groups["VSA"] = {"k": "Volumen Relativo", "v": f"Bajo ({vol_rel:.1f}x - No Institucional)", "score": -10}
            score -= 10
            
        # Absorción (Confluencia Perfecta)
        if signal_type == "BUY" and abs_type == "BUY_ABS":
            pts = 30 if is_climax else 15
            score += pts
            factor_groups["FILTROS"].append({"k": "Pinbar/Absorción", "v": "Rechazo Claro", "score": pts})
        elif signal_type == "SELL" and abs_type == "SELL_ABS":
            pts = 30 if is_climax else 15
            score += pts
            factor_groups["FILTROS"].append({"k": "Pinbar/Absorción", "v": "Rechazo Claro", "score": pts})

        # --- NEW: SMC CONFIRMATIONS (FASE SMC PRO) ---
        obs = detect_order_blocks(df)
        fvgs = detect_fvg(df)
        smc_pts = 0
        smc_desc = []
        
        # A. Order Blocks (OB)
        # Buscamos si el nivel que estamos barriendo coincide con un OB institucional
        active_ob = None
        for ob in obs:
            if not ob['mitigated']:
                # Si es un BUY, buscamos un Bullish OB cerca del PDL
                if signal_type == "BUY" and ob['type'] == 'BULLISH':
                    if (ob['bottom'] - (current_atr*0.5)) <= latest_l <= (ob['top'] + (current_atr*0.5)):
                        active_ob = ob
                        break
                # Si es un SELL, buscamos un Bearish OB cerca del PDH
                elif signal_type == "SELL" and ob['type'] == 'BEARISH':
                    if (ob['top'] + (current_atr*0.5)) >= latest_h >= (ob['bottom'] - (current_atr*0.5)):
                        active_ob = ob
                        break
        
        if active_ob:
            smc_pts += 35
            smc_desc.append(f"Order Block {active_ob['type']}")
            
        # B. Fair Value Gaps (FVG)
        # Buscamos si hay un FVG reciente que el mercado esté cubriendo durante el sweep
        active_fvg = None
        for fvg in fvgs:
            if signal_type == "BUY" and fvg['type'] == 'BULLISH':
                # Si el precio acaba de entrar en un FVG alcista
                if latest_l <= fvg['top'] and latest_c > fvg['bottom']:
                    active_fvg = fvg
                    break
            elif signal_type == "SELL" and fvg['type'] == 'BEARISH':
                if latest_h >= fvg['bottom'] and latest_c < fvg['top']:
                    active_fvg = fvg
                    break
        
        if active_fvg:
            smc_pts += 15
            smc_desc.append(f"FVG {active_fvg['type']}")
            
        if smc_pts > 0:
            score += smc_pts
            factor_groups["SMC"] = {"k": "Smart Money", "v": " + ".join(smc_desc), "score": smc_pts}

        # 5. CONTEXTO SESIONAL
        # Los verdaderos Sweeps de liquidez pasan en London Open y NY Open
        if session_name in ["LONDON", "NY", "OVERLAP"]:
            score += 15
            factor_groups["MOMENTO"] = {"k": "Sesión (Liquidez)", "v": f"Óptima ({session_name})", "score": 15}
        else:
            # Sweeps asiáticos son menos fiables, suelen ser solo consolidación aburrida
            gate_failed = True
            factor_groups["FILTROS"].append({"k": "Bloqueo Sedentario", "v": f"Baja Liquidez ({session_name})", "score": -30})
            
        # --- CÁLCULO DE TARGET (TP) ---
        # Si sweep es éxito, solemos buscar la reversión a la media (EMA50)
        ema50_val = get_safe(ta.ema(df['close'], length=50))
        if ema50_val > 0:
            target_tp = ema50_val
        else:
            # Fallback a 2 ATRs
            target_tp = latest_c + (current_atr*2) if signal_type == "BUY" else latest_c - (current_atr*2)

        final_score = min(100, max(0, score))

        factors_list = []
        for k in ["ESTADO", "SWEEP", "SMC", "VSA", "MOMENTO"]:
            if factor_groups[k]: factors_list.append(factor_groups[k])
        for f in factor_groups["FILTROS"]:
            factors_list.append(f)

        if final_score >= 75 and not gate_failed:
             factor_groups["ESTADO"]["v"] = f"CACERÍA CONFIRMADA ({signal_type})"
             return self._build_result(final_score, factors_list, factor_groups["ESTADO"]["v"], gate_failed, signal_type, 1 if signal_type == "BUY" else -1, target_tp)
        elif final_score >= 40:
             factor_groups["ESTADO"]["v"] = "Posible Falsa Rotura"
             return self._build_result(final_score, factors_list, factor_groups["ESTADO"]["v"], gate_failed, "NEUTRAL", 0)
        else:
             return self._build_result(final_score, factors_list, "Setup Débil", gate_failed, direction=0)

    def _build_neutral_result(self, reason):
        return {
            "entry": 0, "atr": 0, "tp_price": 0,
            "metadata": {
                "strategy": self.STRATEGY_NAME, "score": 0, "total_score": 0,
                "score_breakdown": {"Estado": reason}, "factors_detailed": [],
                "direction": 0, "gate_failed": False, "status": reason
            },
            "score": 0
        }

    def _build_result(self, score, factors, status_msg, gate_failed=False, entry_signal="NEUTRAL", direction=0, tp_price=0):
        entry = 1 if entry_signal == "BUY" else (-1 if entry_signal == "SELL" else 0)
        
        breakdown = {}
        for f in factors:
            if 'k' in f and 'v' in f and 'score' in f:
                breakdown[f['k']] = f"{f['v']} ({'+' if f['score'] >= 0 else ''}{f['score']})"
                
        return {
            "entry": entry,
            "atr": 0, 
            "tp_price": tp_price, 
            "metadata": {
                "strategy": self.STRATEGY_NAME,
                "score": score,
                "total_score": score,
                "score_breakdown": breakdown,
                "factors_detailed": factors,
                "can_entry": (entry != 0) and not gate_failed,
                "gate_failed": gate_failed,
                "status": status_msg,
                "direction": direction,
                "tp_target": tp_price 
            },
            "score": score
        }
