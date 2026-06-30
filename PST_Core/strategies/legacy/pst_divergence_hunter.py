import pandas_ta as ta
import numpy as np
from ..models.classifier import RegimeMode

class PSTDivergenceHunter:
    STRATEGY_NAME = "PST-Divergence"
    STRATEGY_TYPE = RegimeMode.RANGE # Fits Range/Reversal best, but applies generally
    WEIGHT = 1.8 

    async def calculate_signal(self, data_input, current_regime, user_levels=None):
        # Adaptador MTF: Preferiblemente M15 para divergencias fiables
        if isinstance(data_input, dict):
            df = data_input.get('m15')
        else:
            df = data_input

        # Puede funcionar en cualquier regimen, pero mejor en Range o final de Trend
        # No filtramos por regimen estricto, dejamos que la señal hable.

        if df is None or len(df) < 50:
            return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        # 2. Indicadores
        rsi = ta.rsi(df['close'], length=14)
        c = df['close']
        l = df['low']
        h = df['high']
        atr = ta.atr(h, l, c, length=14).iloc[-1]
        
        # Necesitamos arrays numpy para agilidad
        rsi_np = rsi.to_numpy()
        high_np = h.to_numpy()
        low_np = l.to_numpy()
        
        # --- LOGICA DE DIVERGENCIAS (Pivots) ---
        # Buscamos pivotes en ventana de 5 velas (Izquierda 2, Derecha 2)
        # Esto es lookahead bias en backtest si no se tiene cuidado, pero en live, 
        # detectamos que la vela -2 FUE un pivote ahora que cerramos la 0.
        
        # Pivot High: H[i] > H[i-1] y H[i] > H[i+1] ... 
        # En tiempo real, miramos pivotes confirmados en velas anteriores.
        
        def find_pivots(src, order=2):
            pivots = [] # (index, value)
            for i in range(order, len(src) - order):
                window = src[i-order:i+order+1]
                if src[i] == max(window):
                    pivots.append(('high', i, src[i]))
                elif src[i] == min(window):
                    pivots.append(('low', i, src[i]))
            return pivots

        # Buscamos pivotes recientes en precio y rsi
        # Limitamos a últimas 30 velas para relevancia
        lookback = 30
        subset_slice = slice(-lookback, None)
        
        # Ajustar indices relativos al DF original
        offset = len(df) - lookback
        if offset < 0: offset = 0
        
        price_pivots = find_pivots(low_np[offset:], 2) + find_pivots(high_np[offset:], 2)
        rsi_pivots = find_pivots(rsi_np[offset:], 2)
        
        # Clasificar ultimos 2 de cada tipo
        lows_p = [p for p in price_pivots if p[0] == 'low']
        highs_p = [p for p in price_pivots if p[0] == 'high']
        lows_r = [p for p in rsi_pivots if p[0] == 'low']
        highs_r = [p for p in rsi_pivots if p[0] == 'high']
        
        score = 0
        breakdown = {}
        div_type = "None"
        
        # --- A. BULLISH DIVERGENCE CHECK (Regular) ---
        # Price Lower Low (LL) + RSI Higher Low (HL) -> Reversal UP
        if len(lows_p) >= 2 and len(lows_r) >= 2:
            # Ultimo pivote precio
            p2 = lows_p[-1] # Más reciente
            p1 = lows_p[-2] # Anterior
            
            # Ultimo pivote rsi (deben estar cerca en tiempo)
            r2 = lows_r[-1]
            r1 = lows_r[-2]
            
            # Sincronia temporal (max 3 velas de desfase entre pivote precio y rsi)
            if abs(p2[1] - r2[1]) <= 3 and abs(p1[1] - r1[1]) <= 3:
                # Chequeo Logica
                price_LL = p2[2] < p1[2] # Precio baja
                rsi_HL = r2[2] > r1[2]   # RSI sube
                
                if price_LL and rsi_HL:
                    score += 60
                    div_type = "Reg Bullish"
                    breakdown["Pattern"] = "Price LL + RSI HL (+60)"
        
        # --- B. BEARISH DIVERGENCE CHECK (Regular) ---
        # Price Higher High (HH) + RSI Lower High (LH) -> Reversal DOWN
        if len(highs_p) >= 2 and len(highs_r) >= 2:
            p2 = highs_p[-1]
            p1 = highs_p[-2]
            r2 = highs_r[-1]
            r1 = highs_r[-2]
            
            if abs(p2[1] - r2[1]) <= 3 and abs(p1[1] - r1[1]) <= 3:
                price_HH = p2[2] > p1[2]
                rsi_LH = r2[2] < r1[2]
                
                if price_HH and rsi_LH:
                    score += 60
                    div_type = "Reg Bearish"
                    breakdown["Pattern"] = "Price HH + RSI LH (+60)"
        
        # --- C. HIDDEN DIVERGENCE (Trend Continuation) ---
        # Bullish: Price HL + RSI LL -> Buy dip
        # Bearish: Price LH + RSI HH -> Sell rally
        # (Simplificado: Solo buscamos Regular por ahora para scoring alto)

        # Filters (RSI Levels)
        current_rsi = rsi.iloc[-1]
        
        if "Bull" in div_type:
            # Validar que RSI no esté ya en techo (>60)
            if current_rsi < 50: 
                score += 20
                breakdown["RSI Room"] = "Plenty Space (+20)"
            elif current_rsi < 65:
                score += 10
                breakdown["RSI Room"] = "Some Space (+10)"
            else:
                breakdown["RSI Room"] = "Capped (0)"
                
        elif "Bear" in div_type:
            if current_rsi > 50:
                score += 20
                breakdown["RSI Room"] = "Plenty Space (+20)"
            elif current_rsi > 35:
                score += 10
                breakdown["RSI Room"] = "Some Space (+10)"

        # --- SIGNAL GENERATION ---
        entry = 0
        if score >= 70:
            if "Bull" in div_type: entry = 1
            elif "Bear" in div_type: entry = -1

        metadata = {
            "regime": "REVERSAL",
            "total_score": score,
            "score_breakdown": breakdown,
            "div_type": div_type
        }

        return {
            "entry": entry,
            "atr": atr,
            "metadata": metadata,
            "score": score
        }
