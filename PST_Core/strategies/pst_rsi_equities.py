import pandas_ta as ta
import numpy as np
import logging
from ..models.classifier import RegimeMode

logger = logging.getLogger("RSI-Equities")

class PSTRSIEquities:
    STRATEGY_NAME = "PST-RSI-Equities"
    STRATEGY_TYPE = RegimeMode.RANGE  # Se asocia a Rango/Reversión
    WEIGHT = 1.0

    def _get_asset_params(self, symbol):
        """Detecta el tipo de activo y devuelve params ajustados."""
        # Heurística simple
        is_crypto = "BTC" in symbol or "ETH" in symbol or "XRP" in symbol or "SOL" in symbol
        is_forex = len(symbol) == 6 and symbol.isupper() and ("USD" in symbol or "EUR" in symbol or "JPY" in symbol)
        
        # Default: EQUITIES / STOCKS (RSI 30/70)
        params = {
            "RSI_BUY": 30,
            "RSI_SELL": 70,
            "MODE": "STOCK"
        }
        
        if is_crypto:
             # Crypto es MUY volátil, necesita extremos más fuertes para no quemarse
             params["RSI_BUY"] = 20
             params["RSI_SELL"] = 80
             params["MODE"] = "CRYPTO"
             
        elif is_forex:
             # Forex en rango suele respetar bien los niveles medios
             params["RSI_BUY"] = 30
             params["RSI_SELL"] = 70
             params["MODE"] = "FOREX"
             
        return params

    def _detect_divergence(self, df, rsi_series, lookback=30):
        """
        Detecta divergencias Regulares (Reversión).
        """
        try:
            current_price = df['close'].iloc[-1]
            current_rsi = rsi_series.iloc[-1]
            
            # Buscamos PIVOTS recientes en Precio y RSI
            # Usar ventana pequeña de 5 para pivots locales
            # Simplificación: Buscar min/max en la ventana lookback
            
            lowest_price = df['low'].iloc[-lookback:-1].min()
            highest_price = df['high'].iloc[-lookback:-1].max()
            
            lowest_rsi = rsi_series.iloc[-lookback:-1].min()
            highest_rsi = rsi_series.iloc[-lookback:-1].max()
            
            # BULLISH DIVERGENCE (Precio Lower Low, RSI Higher Low)
            is_bull_div = False
            if current_price < lowest_price: # Nuevo bajo en precio (o muy cerca)
                # Pero el RSI NO ha hecho un nuevo bajo
                if current_rsi > lowest_rsi + 2: # Margen de 2 pts
                     # Estamos haciendo suelo más profundo pero RSI aguanta
                     is_bull_div = True
                     
            # BEARISH DIVERGENCE (Precio Higher High, RSI Lower High)
            is_bear_div = False
            if current_price > highest_price: # Nuevo alto en precio
                # Pero el RSI NO ha hecho nuevo alto
                if current_rsi < highest_rsi - 2:
                    is_bear_div = True
                    
            return is_bull_div, is_bear_div
        except:
            return False, False

    async def calculate_signal(self, data_input, current_regime, user_levels=None):
        # 1. Adaptador de Datos (Soporte M1 y M5)
        df_m1 = None
        df_m5 = None
        
        if isinstance(data_input, dict):
            df_m1 = data_input.get('m1') 
            df_m5 = data_input.get('m5')
        else:
            df_m5 = data_input

        # Seleccionar Timeframe Principal
        active_df = df_m5 # Default
        tf_label = "M5"
        
        # --- DETECT ASSET CLASS & PARAMS ---
        symbol = user_levels.get('symbol', 'UNKNOWN') if isinstance(user_levels, dict) else 'UNKNOWN'
        asset_params = self._get_asset_params(symbol)
        RSI_BUY = asset_params["RSI_BUY"]
        RSI_SELL = asset_params["RSI_SELL"]
        
        if active_df is not None:
             # Detección simple de timeframe
             if len(active_df) > 2:
                 t1 = active_df.index[-1]
                 t2 = active_df.index[-2]
                 try:
                     diff_sec = (t1 - t2).total_seconds()
                     if 50 < diff_sec < 70: 
                         tf_label = "M1"
                         # En M1 apretamos aún más (5 pts extra)
                         RSI_BUY -= 5
                         RSI_SELL += 5
                 except: pass

        if active_df is None or len(active_df) < 50:
            return {"entry": 0, "atr": 0, "metadata": {}, "score": 0}

        # 2. Indicadores Estándar
        # RSI
        rsi_df = ta.rsi(active_df['close'], length=14)
        rsi = rsi_df.iloc[-1] if rsi_df is not None else 50
        prev_rsi = rsi_df.iloc[-2] if rsi_df is not None else 50
        
        # Volumen y su Media (SMA 20)
        # Check volume existance
        if 'tick_volume' in active_df:
            vol = active_df['tick_volume'].iloc[-1]
            vol_ma_df = ta.sma(active_df['tick_volume'], length=20)
            vol_ma = vol_ma_df.iloc[-1] if vol_ma_df is not None else 1
        else:
            vol = 0
            vol_ma = 1
        
        # EMA 200 (Filtro de Tendencia Global)
        ema200_df = ta.ema(active_df['close'], length=200)
        ema200 = ema200_df.iloc[-1] if ema200_df is not None else 0
        
        close = active_df['close'].iloc[-1]

        # 3. Detectar Divergencias
        bull_div, bear_div = self._detect_divergence(active_df, rsi_df)

        # 4. Motor de Puntuación (Scoring) - Max 100
        score = 0
        breakdown = {}
        breakdown["Asset"] = f"{asset_params['MODE']} ({tf_label})"
        
        # A. Condición RSI (Base 40 pts)
        is_oversold = rsi < RSI_BUY
        is_overbought = rsi > RSI_SELL
        
        rsi_event = 0 # 0, 1 (Buy Cross), -1 (Sell Cross)
        
        if is_oversold:
            score += 40
            breakdown[f"RSI < {RSI_BUY}"] = "Oversold (+40)"
            
            if prev_rsi >= RSI_BUY and rsi < RSI_BUY:
                breakdown["Trigger"] = "Fresh Cross Down (+10)"
                score += 10
                rsi_event = 1
            elif prev_rsi < RSI_BUY and rsi > prev_rsi:
                breakdown["Trigger"] = "Turning Up (+10)"
                score += 10
                rsi_event = 1
                
        elif is_overbought:
            score += 40
            breakdown[f"RSI > {RSI_SELL}"] = "Overbought (+40)"
            
            if prev_rsi <= RSI_SELL and rsi > RSI_SELL:
                breakdown["Trigger"] = "Fresh Cross Up (+10)"
                score += 10
                rsi_event = -1
            elif prev_rsi > RSI_SELL and rsi < prev_rsi:
                breakdown["Trigger"] = "Turning Down (+10)"
                score += 10
                rsi_event = -1
        else:
            breakdown[f"RSI {rsi:.1f}"] = "Neutral (0)"

        # B. Validación de Volumen (Max 20 pts)
        if vol > vol_ma:
            score += 20
            breakdown["Volume"] = "High (>Avg) (+20)"
        elif vol > vol_ma * 0.8:
            score += 10
            breakdown["Volume"] = "Normal (+10)"
        else:
            breakdown["Volume"] = "Low (0)"
            
        # C. Divergencias (Bono Potente +30)
        # Esto puede disparar la entrada incluso si el RSI no está extremo extremo, 
        # pero para seguridad, exigimos que al menos esté cerca de zonas.
        if bull_div and rsi < 50: # Divergencia alcista válida en zona baja
             score += 30
             breakdown["Divergence"] = "BULLISH DIV (+30)"
             # Forzamos evento de compra si hay div
             rsi_event = 1
             
        if bear_div and rsi > 50: # Divergencia bajista válida en zona alta
             score += 30
             breakdown["Divergence"] = "BEARISH DIV (+30)"
             # Forzamos evento de venta
             rsi_event = -1

        # D. Filtro de Tendencia (EMA 200) (Max 10 pts)
        dist_ema = (close - ema200) / (ema200 if ema200!=0 else 1) * 100 
        
        if rsi_event == 1: # Buscamos Compra
            if close > ema200: 
                score += 10
                breakdown["Trend"] = "Bullish Pullback (+10)"
            elif dist_ema < -2.0: 
                score += 10
                breakdown["Trend"] = "Oversold Ext (+10)"

        elif rsi_event == -1: # Buscamos Venta
            if close < ema200: 
                score += 10
                breakdown["Trend"] = "Bearish Pullback (+10)"
            elif dist_ema > 2.0: 
                score += 10
                breakdown["Trend"] = "Overbought Ext (+10)"
        
        # --- DECISIÓN FINAL ---
        entry = 0
        atr = ta.atr(active_df['high'], active_df['low'], active_df['close'], length=14).iloc[-1]
        
        # Umbral: 80 puntos
        # Con Div (30) + RSI (40) + Vol (10) = 80 -> Entrada
        if score >= 80:
             if rsi_event == 1: entry = 1
             elif rsi_event == -1: entry = -1
        
        metadata = {
            "strategy": "PST-RSI-Equities",
            "score": score,
            "total_score": score,
            "score_breakdown": breakdown,
            "rsi": round(rsi, 2),
            "vol_ratio": round(vol/vol_ma, 2) if vol_ma > 0 else 0,
            "timeframe": tf_label
        }
        
        return {
            "entry": entry,
            "atr": atr,
            "metadata": metadata,
            "score": score
        }
