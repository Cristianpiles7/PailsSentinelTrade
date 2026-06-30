import pandas_ta as ta
import numpy as np
from ..models.classifier import RegimeMode

class PSTNewsFade:
    STRATEGY_NAME = "PST-News-Fade"
    STRATEGY_TYPE = RegimeMode.VOLATILE # Only active during high impact moves
    WEIGHT = 2.5 # High risk/reward, high priority when conditions met

    async def calculate_signal(self, data_input, current_regime, user_levels=None):
        # Adaptador MTF: M5 es ideal para noticias
        if isinstance(data_input, dict):
            df = data_input.get('m5')
        else:
            df = data_input

        # Puede funcionar en VOLATILE o incluso TREND si hay spike absurdo
        # Filtro basico: Solo procesar si hay data minima
        if df is None or len(df) < 50:
            return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        # 1. Indicadores
        c = df['close']
        o = df['open']
        h = df['high']
        l = df['low']
        atr_series = ta.atr(h, l, c, length=14)
        atr = atr_series.iloc[-1] if atr_series is not None else 0
        
        if atr == 0: return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        # 2. Detectar "Spike" (Vela gigante insostenible)
        # Una vela de noticia suele ser > 3x o 4x el ATR normal
        last_body = abs(c.iloc[-1] - o.iloc[-1])
        prev_body = abs(c.iloc[-2] - o.iloc[-2])
        
        # Promedio del cuerpo de las ultimas 20 velas (excluyendo la actual)
        avg_body = abs(c - o).rolling(20).mean().iloc[-2]
        
        is_spike = last_body > (avg_body * 3.0) and last_body > (atr * 2.5)
        
        # --- SCORING ENGINE (Max 100) ---
        score = 0
        breakdown = {}
        
        # A. Spike Magnitude (Max 40)
        direction = 0 # 1=Bull Spike (Sell Fade), -1=Bear Spike (Buy Fade)
        
        if is_spike:
            if c.iloc[-1] > o.iloc[-1]: # Bull Spike
                direction = -1 # Prepared to Sell
                score += 40
                breakdown["Spike"] = "Massive Bull (+40)"
            else: # Bear Spike
                direction = 1 # Prepared to Buy
                score += 40
                breakdown["Spike"] = "Massive Bear (+40)"
        else:
            breakdown["Spike"] = "Normal Vol (0)"
            
        # B. Extension / Exhaustion (Max 30)
        # Distancia a EMA21 (Reversión a la media)
        ema21 = ta.ema(c, length=21).iloc[-1]
        dist_emak = abs(c.iloc[-1] - ema21) / ema21 * 100
        
        # Si se alejó más de un 0.5% (indices/fx) en 5 min, es mucho
        if dist_emak > 0.4: 
            score += 30
            breakdown["Extension"] = f"Overextended {dist_emak:.2f}% (+30)"
        elif dist_emak > 0.2:
            score += 15
            breakdown["Extension"] = "Extended (+15)"
        else:
            breakdown["Extension"] = "Normal (0)"
            
        # C. Stall / Wicks (Max 30) (Opcional: Esperar a la siguiente vela?)
        # Para "Fade", queremos ver rechazo (mecha en contra del movimiento)
        # Si es Bull Spike, queremos Upper Wick grande
        upper_wick = h.iloc[-1] - max(c.iloc[-1], o.iloc[-1])
        lower_wick = min(c.iloc[-1], o.iloc[-1]) - l.iloc[-1]
        
        if direction == -1: # Selling the bull spike
            if upper_wick > last_body * 0.3:
                score += 30
                breakdown["Rejection"] = "Wick Resistance (+30)"
            else:
                 breakdown["Rejection"] = "Full Body (Dangerous) (0)"
        elif direction == 1: # Buying the bear spike
            if lower_wick > last_body * 0.3:
                score += 30
                breakdown["Rejection"] = "Wick Support (+30)"
            else:
                 breakdown["Rejection"] = "Full Body (Dangerous) (0)"

        # --- SIGNAL GENERATION ---
        entry = 0
        # High score needed because trading against momentum is dangerous
        if score >= 70:
            entry = direction

        metadata = {
            "regime": "VOLATILE",
            "total_score": score,
            "score_breakdown": breakdown,
            "spike_size": round(last_body, 2)
        }

        return {
            "entry": entry,
            "atr": atr,
            "metadata": metadata,
            "score": score
        }
