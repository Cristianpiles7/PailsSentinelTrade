import pandas as pd
import numpy as np
import logging
import json
import asyncio
from datetime import datetime
from ..models.classifier import RegimeMode
from ..models.database import PSTDatabase

from .ai_providers import AIProviderFactory

logger = logging.getLogger("PST-AI-Oracle")

class PSTAIOracle:
    STRATEGY_NAME = "PST-AI-Oracle 🌌"
    VALID_PROVIDERS = ["gemini", "groq", "ollama"]

    def __init__(self, db: PSTDatabase = None, provider_type: str = None):
        """
        Inicializa la estrategia. 
        Si provider_type se pasa, esta instancia queda BLOQUEADA a ese proveedor.
        Si es None, se determinará dinámicamente.
        """
        self.db = db or PSTDatabase()
        self.provider_type = provider_type
        self.provider = None
        
        # ID Único para configuración (Dashboard toggles)
        p_id = provider_type.lower() if provider_type else "dynamic"
        self.STRAT_ID = f"PSTAIOracle_{p_id}"
        
        # Nombre dinámico si tiene proveedor fijo
        if provider_type:
            p_name = provider_type.upper()
            self.STRATEGY_NAME = f"PST-AI-Oracle [{p_name}] 🌌"

        # Cache compartida entre hilos/instancias (Sincronización)
        if not hasattr(self, '_shared_cooldowns'):
            self.__class__._shared_cooldowns = {} 
        if not hasattr(self, '_shared_last_analysis'):
            self.__class__._shared_last_analysis = {}

    async def _ensure_provider(self, symbol: str):
        """Inicializa o cambia el proveedor de IA dinámicamente."""
        p_type = self.provider_type
        
        if not p_type or p_type.upper() == "AUTO":
            p_type = await self.db.get_config(f"ai_provider_{symbol}")
            if not p_type or p_type.upper() == "AUTO":
                p_type = await self.db.get_config("ai_provider")
                if not p_type or p_type.upper() == "AUTO":
                    p_type = "gemini" # El último recurso real
        
        p_type = p_type.lower()
        
        # Validación final contra los proveedores soportados
        if p_type not in self.VALID_PROVIDERS:
            logger.warning(f"⚠️ Proveedor '{p_type}' no reconocido en {symbol}. Usando GEMINI por defecto.")
            p_type = "gemini"
        
        # Solo re-instanciar si el proveedor ha cambiado o no existe
        if not self.provider or getattr(self.provider, '_type', None) != p_type:
            logger.info(f"🧠 [{symbol}] Usando Cerebro IA: {p_type.upper()}")
            from .ai_providers import AIProviderFactory
            # Cargar API Key si es necesario
            api_key = None
            if p_type == "gemini":
                api_key = await self.db.get_config("gemini_api_key")
            elif p_type == "groq":
                api_key = await self.db.get_config("groq_api_key")
            
            if p_type != "ollama" and not api_key:
                logger.warning(f"⚠️ API Key para {p_type} no configurada.")
                # No se puede inicializar, retornar None para indicar fallo
                return None

            self.provider = AIProviderFactory.get_provider(p_type, api_key)
            self.provider._type = p_type # Mark it
        
        return p_type

    async def _get_dynamic_cooldown(self, symbol, p_type):
        """Calcula el cooldown óptimo basado en cuotas y símbolos activos."""
        p_type = p_type.lower()
        
        # 1. Ollama: Local y Gratis -> Frecuencia Máxima
        if p_type == "ollama":
            return 45 # 45 segundos por seguridad (evitar sobrecarga local si hay muchos símbolos)
        
        # 2. Cloud Providers (Groq/Gemini): Basado en RPD (Requests Per Day)
        # Obtenemos cuántos símbolos tienen activada esta IA específica
        active_count = await self.db.count_active_strategy_symbols(self.STRAT_ID)
        active_count = max(1, active_count) # Evitar división por cero
        
        daily_limit = 12000 # Nuevo Límite: Llama 3.1 8b Instant (~14.4K RPD) -> Usamos 12K (Margen)
        if p_type == "gemini":
            daily_limit = 1500 # Límite aproximado de Gemini Free
            
        # Fórmula: (Segundos en un día / Límite) * Símbolos * Margen Seguridad
        # 86400 / 12000 = 7.2 segundos por llamada global.
        safety_margin = 1.1 # 10% margen extra
        cooldown = (86400 / daily_limit) * active_count * safety_margin
        
        # Límites razonables (No menos de 45s para cloud (anti-spam), no más de 30 min)
        return max(45, min(1800, int(cooldown)))

    async def calculate_signal(self, mtf_data, current_regime, user_levels=None, force=False):
        """Analiza el mercado usando Inteligencia Artificial. force=True ignora filtros técnicos."""
        
        # 2. Control de frecuencia (Anti-Burst)
        symbol = mtf_data.get('symbol', 'UNKNOWN')
        
        # A. Asegurar proveedor dinámico
        p_name = await self._ensure_provider(symbol)
        
        if not p_name: 
            return self._build_neutral_result(f"Configurar IA para {symbol}")

        p_name_upper = p_name.upper()
        cache_key = f"{symbol}_{p_name.lower()}"
        
        now_ts = datetime.now().timestamp()

        # A. Inicialización del Cooldown y Sincronización pro DB
        if cache_key not in self._shared_cooldowns:
            # 1. Recuperar o Inicializar Estado del Símbolo
            # Intentar recuperar estado de la Base de Datos (Sincronización Bot vs Dashboard)
            db_state = await self.db.get_ai_oracle_state(f"{symbol}_{p_name}")
            if not db_state and p_name == "dynamic":
                db_state = await self.db.get_ai_oracle_state(symbol)

            if db_state:
                self._shared_last_analysis[cache_key] = db_state['analysis']
                self._shared_cooldowns[cache_key] = db_state['ts']
                logger.info(f"🔄 [{symbol}] Sincronizado estado de IA ({p_name}) desde Base de Datos.")
            else:
                # 2. Si no hay en DB, inicializar con desfase de seguridad
                stagger_delay = sum(ord(c) for c in symbol) % 60
                self._shared_cooldowns[cache_key] = now_ts - 120 + stagger_delay
                logger.debug(f"⏳ [{symbol}] IA {p_name} activada.")

        # B. Comprobación de Cooldown DINÁMICO
        dynamic_cooldown = await self._get_dynamic_cooldown(symbol, p_name)
        elapsed = now_ts - self._shared_cooldowns[cache_key]
        
        if elapsed < dynamic_cooldown and not force:
            wait_info = f"IA en espera estratégica... ({int(dynamic_cooldown - elapsed)}s)"
            return self._shared_last_analysis.get(cache_key, self._build_neutral_result(wait_info))

        # 3. Preparar el contexto técnico para la IA
        try:
            df_m5 = mtf_data.get('m5') if isinstance(mtf_data, dict) else mtf_data
            df_h1 = mtf_data.get('h1') if isinstance(mtf_data, dict) else None
            
            if df_m5 is None or len(df_m5) < 30:
                return self._build_neutral_result("Esperando datos suficientes...")

            # Métricas base
            current_price = float(df_m5['close'].iloc[-1])
            # AUMENTO DE CONTEXTO: 25 Velas para patrones más claros (Ollama Friendly)
            # Fix: reset_index() insures 'time' is a column in the dict
            last_candles = df_m5.tail(25).reset_index().to_dict(orient='records') 
            
            # Cálculo de Volumen Promedio y ATR Local
            try:
                vol_values = [str(c.get('tick_volume', 0)) for c in last_candles] 
                avg_vol = sum(int(c.get('tick_volume', 0)) for c in last_candles) / len(last_candles)
            except: avg_vol = 999999
            
            candles_summary = []
            for c in last_candles:
                v_c = int(c.get('tick_volume', 0))
                # Mark high volume candles
                v_tag = " (ALTO VOL)" if v_c > avg_vol * 1.5 else ""
                
                # Check if time exists (it should now)
                t_str = str(c.get('time', ''))[11:16] if 'time' in c else ""
                
                candles_summary.append({
                    "t": t_str, # Solo hora HH:MM
                    "o": round(c['open'], 5), "h": round(c['high'], 5),
                    "l": round(c['low'], 5), "c": round(c['close'], 5),
                    "v": f"{v_c}{v_tag}"
                })

            # A. Métricas Técnicas (M5)
            indicators = {}
            try:
                import pandas_ta as ta
                # RSI y ADX M5
                rsi_s = ta.rsi(df_m5['close'], length=14)
                indicators['rsi_m5'] = round(rsi_s.iloc[-1], 2) if rsi_s is not None else 50
                
                adx_df = ta.adx(df_m5['high'], df_m5['low'], df_m5['close'], length=14)
                indicators['adx_m5'] = round(adx_df['ADX_14'].iloc[-1], 2) if adx_df is not None else 0
                
                # ATR (Volatilidad)
                atr_s = ta.atr(df_m5['high'], df_m5['low'], df_m5['close'], length=14)
                indicators['atr_m5'] = round(atr_s.iloc[-1], 5) if atr_s is not None else 0
                
                # Distancia y Posición vs EMAs (Explicit Tags)
                ema21 = ta.ema(df_m5['close'], length=21).iloc[-1]
                ema50 = ta.ema(df_m5['close'], length=50).iloc[-1]
                dist_21 = ((current_price - ema21) / ema21) * 100
                dist_50 = ((current_price - ema50) / ema50) * 100
                
                indicators['pos_ema21'] = f"{'ARRIBA' if current_price > ema21 else 'DEBAJO'} ({dist_21:+.3f}%)"
                indicators['pos_ema50'] = f"{'ARRIBA' if current_price > ema50 else 'DEBAJO'} ({dist_50:+.3f}%)"
                indicators['crossover'] = "ALCISTA (Oro)" if ema21 > ema50 else "BAJISTA (Muerte)"
                
                # Bollinger Bands
                bbands = ta.bbands(df_m5['close'], length=20, std=2)
                if bbands is not None:
                    bb_upper = bbands[f'BBU_20_2.0'].iloc[-1]
                    bb_lower = bbands[f'BBL_20_2.0'].iloc[-1]
                    bb_position = ((current_price - bb_lower) / (bb_upper - bb_lower)) * 100 if bb_upper != bb_lower else 50
                    indicators['bb_position'] = round(bb_position, 1)
                else:
                    indicators['bb_position'] = 50
                
                # MACD
                macd = ta.macd(df_m5['close'], fast=12, slow=26, signal=9)
                if macd is not None:
                    macd_line = macd['MACD_12_26_9'].iloc[-1]
                    signal_line = macd['MACDs_12_26_9'].iloc[-1]
                    macd_hist = macd['MACDh_12_26_9'].iloc[-1]
                    indicators['macd_signal'] = 'ALCISTA' if macd_line > signal_line else 'BAJISTA'
                    indicators['macd_strength'] = abs(round(macd_hist, 5))
                else:
                    indicators['macd_signal'] = 'NEUTRAL'
                    indicators['macd_strength'] = 0
                
                # Volume Trend
                recent_vol = df_m5['tick_volume'].tail(5).mean()
                prev_vol = df_m5['tick_volume'].tail(25).head(20).mean()
                vol_trend = ((recent_vol - prev_vol) / prev_vol * 100) if prev_vol > 0 else 0
                indicators['volume_trend'] = round(vol_trend, 1)
            except: pass
            
            # Candlestick Pattern Detection
            patterns_detected = []
            try:
                last_3 = df_m5.tail(3)
                if len(last_3) >= 3:
                    c1, c2, c3 = last_3.iloc[-3], last_3.iloc[-2], last_3.iloc[-1]
                    
                    # Engulfing
                    if c3['close'] > c3['open'] and c2['close'] < c2['open']:
                        if c3['open'] <= c2['close'] and c3['close'] >= c2['open']:
                            patterns_detected.append("Engulfing Alcista")
                    elif c3['close'] < c3['open'] and c2['close'] > c2['open']:
                        if c3['open'] >= c2['close'] and c3['close'] <= c2['open']:
                            patterns_detected.append("Engulfing Bajista")
                    
                    # Pinbar
                    body = abs(c3['close'] - c3['open'])
                    upper_wick = c3['high'] - max(c3['open'], c3['close'])
                    lower_wick = min(c3['open'], c3['close']) - c3['low']
                    total_range = c3['high'] - c3['low']
                    
                    if total_range > 0:
                        if lower_wick > body * 2 and lower_wick > upper_wick * 2:
                            patterns_detected.append("Pinbar Alcista")
                        elif upper_wick > body * 2 and upper_wick > lower_wick * 2:
                            patterns_detected.append("Pinbar Bajista")
                        if body < total_range * 0.1:
                            patterns_detected.append("Doji")
            except: pass
            
            patterns_str = ", ".join(patterns_detected) if patterns_detected else "Ninguno"
            
            # Support/Resistance Levels
            support_resistance = []
            try:
                last_50 = df_m5.tail(50)
                highs = last_50['high'].values
                lows = last_50['low'].values
                
                for i in range(2, len(highs)-2):
                    if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                        support_resistance.append(('R', round(highs[i], 5)))
                    if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                        support_resistance.append(('S', round(lows[i], 5)))
                
                supports = sorted([lvl for typ, lvl in support_resistance if typ == 'S' and lvl < current_price], reverse=True)[:2]
                resistances = sorted([lvl for typ, lvl in support_resistance if typ == 'R' and lvl > current_price])[:2]
                sr_str = f"S: {supports} | R: {resistances}"
            except:
                sr_str = "N/A"

            # B. Estructura Multi-Timeframe (Detección Basada en EMA21)
            mtf_context = {}
            for tf in ['m1', 'm3', 'm15', 'm30', 'h1', 'h4']:
                df_tf = mtf_data.get(tf)
                if df_tf is not None and len(df_tf) > 25:
                    try:
                        ema_tf = ta.ema(df_tf['close'], length=21).iloc[-1]
                        c_tf = df_tf['close'].iloc[-1]
                        trend = "ALCISTA (Encima EMA21)" if c_tf > ema_tf else "BAJISTA (Debajo EMA21)"
                        rsi_tf = 50
                        r_s = ta.rsi(df_tf['close'], length=14)
                        if r_s is not None: rsi_tf = round(r_s.iloc[-1], 1)
                        mtf_context[tf] = f"{trend} | RSI: {rsi_tf}"
                    except: mtf_context[tf] = "N/A"

            # C. Niveles Manuales (Liquidez)
            levels_str = "Ninguno definido"
            if user_levels and isinstance(user_levels, list):
                levels_str = ", ".join([f"{L.get('type','NIVEL')}: {L.get('price')}" for L in user_levels[:5]])

            # ========== PHASE 1 IMPROVEMENTS ==========
            
            # 1. TRADING SESSION DETECTION
            hour_utc = datetime.utcnow().hour
            if 0 <= hour_utc < 8:
                session_name = "Asiática"
                session_desc = "Baja volatilidad, ser MUY conservador"
                session_penalty = -5
            elif 8 <= hour_utc < 16:
                session_name = "Europea"
                session_desc = "Alta volatilidad, oportunidades claras"
                session_penalty = 0
            else:
                session_name = "Americana"
                session_desc = "Muy alta volatilidad, máxima precaución"
                session_penalty = -3
            
            session_info = f"{session_name} ({session_desc})"
            
            # 2. SPREAD AWARENESS (Simulated - TODO: Get from broker)
            # For now, use ATR as proxy for spread
            spread_current = indicators.get('atr_m5', 0) * 10000  # Convert to pips
            spread_avg = spread_current * 0.7  # Assume current is 1.4x average
            spread_penalty = -10 if spread_current > spread_avg * 1.5 else 0
            spread_info = f"{spread_current:.1f} pips (Promedio: {spread_avg:.1f})"
            if spread_penalty < 0:
                spread_info += f" ⚠️ ALTO ({spread_penalty} pts)"
            
            # 3. VOLATILITY CONTEXT
            atr_current = indicators.get('atr_m5', 0)
            # TODO: Calculate 20-day ATR average from historical data
            # For now, assume current ATR is the baseline
            volatility_info = f"ATR: {atr_current:.5f}"
            
            # ========== END PHASE 1 IMPROVEMENTS ==========


            # Construir el Prompt Super Vision V6 (Phase 1 Improvements)
            prompt = f"""
Eres un Analista Institucional de Mercado y Experto en Price Action.
Analiza el siguiente contexto para {symbol} y determina la PROBABILIDAD REAL de éxito.

IMPORTANTE: Responde SIEMPRE en ESPAÑOL. Todos los factores, razonamientos y explicaciones deben estar en español.

=== CONTEXTO DE MERCADO ===
📍 Sesión: {session_info}
💰 Spread: {spread_info}
📊 Volatilidad: {volatility_info}

1. ESTRUCTURA Y TENDENCIA:
- Régimen M5: {current_regime}
- ADX: {indicators.get('adx_m5', 0)} (>25 = tendencia fuerte)
- MTF: M1={mtf_context.get('m1', 'N/A')}, M3={mtf_context.get('m3', 'N/A')}, H1={mtf_context.get('h1', 'N/A')}, H4={mtf_context.get('h4', 'N/A')}

2. INDICADORES TÉCNICOS:
- RSI: {indicators.get('rsi_m5', 50)} (<30 sobreventa, >70 sobrecompra)
- Bollinger: Posición {indicators.get('bb_position', 50)}% (0=inferior, 100=superior)
- MACD: {indicators.get('macd_signal', 'NEUTRAL')}, Fuerza={indicators.get('macd_strength', 0)}
- Precio vs EMA21: {indicators.get('pos_ema21', 'N/A')}
- Precio vs EMA50: {indicators.get('pos_ema50', 'N/A')}
- Cruce Medias (EMA21/50): {indicators.get('crossover', 'N/A')}

3. VOLUMEN Y PATRONES:
- Tendencia Volumen: {indicators.get('volume_trend', 0)}% (últimas 5 vs 20 velas)
- Patrones: {patterns_str}
- S/R: {sr_str}

4. NIVELES USUARIO: {levels_str}
5. PRECIO: {current_price}

6. ACCIÓN DEL PRECIO (Últimas 25 velas M5):
{json.dumps(candles_summary)}

=== SISTEMA DE SCORING ESTRUCTURADO ===

Calcula el score sumando puntos por categoría (máximo 100):

IMPORTANTE: Si el precio está por ENCIMA de las EMAs 21 y 50 de forma robusta, el sesgo institucional es alcista (BUY). Si está por DEBAJO, el sesgo es bajista (SELL).
No ignores la acción del precio actual vs medias móviles.

1. 🔭 ESTRUCTURA (0-25 pts):
   - Alineación MTF perfecta (todos TF coinciden): 25 pts
   - Alineación parcial (2-3 TF coinciden): 15 pts
   - Alineación débil o contradictoria: 5 pts
   - ADX >25 suma +5 pts adicionales

2. 📊 PATRONES (0-20 pts):
   - Patrón claro (Engulfing/Pinbar) en zona clave: 20 pts
   - Patrón presente pero no en zona óptima: 12 pts
   - Patrón débil o Doji: 8 pts
   - Sin patrón claro: 0 pts

3. ⚡ VOLUMEN (0-15 pts):
   - Volumen alto confirma movimiento: 15 pts
   - Volumen neutral: 8 pts
   - Volumen contradice: 0 pts

4. 📈 INDICADORES (0-20 pts):
   - Todos alineados (RSI, MACD, Bollinger): 20 pts
   - Mayoría alineados: 12 pts
   - Mixtos o contradictorios: 5 pts

5. 🎚️ NIVELES (0-15 pts):
   - Cerca de S/R clave con espacio para movimiento: 15 pts
   - Cerca de EMA con confirmación: 10 pts
   - Zona intermedia: 5 pts

6. ⚠️ RIESGOS (0-5 pts):
   - Sin riesgos evidentes: 5 pts
   - Riesgos moderados: 2 pts
   - Riesgos altos: 0 pts

=== PENALIZACIONES OBLIGATORIAS ===
Resta estos puntos del score final:
- Sesión Asiática: {session_penalty} pts (baja volatilidad)
- Spread Alto: {spread_penalty} pts (reduce rentabilidad)
- Señales contradictorias entre TF: -8 pts
- Volumen contradice dirección: -10 pts

=== INTERPRETACIÓN FINAL ===
- 0-20: Probabilidad muy baja, NO operar
- 21-40: Probabilidad baja, esperar mejor setup
- 41-60: Neutral/incierto, monitorear
- 61-75: Probabilidad moderada, considerar con cautela
- 76-85: Probabilidad alta, buen setup
- 86-100: Probabilidad muy alta, setup óptimo

Salida JSON REQUERIDA:
{{
  "score": (0-100, suma de categorías - penalizaciones),
  "direction": (1 BUY, -1 SELL, 0 WAIT),
  "reasoning": "Resumen ejecutivo. Máximo 2 frases.",
  "key_factors": [
    "🔭 ESTRUCTURA: [Análisis MTF y ADX] (+X pts)",
    "📊 PATRONES: [Patrones detectados] (+X pts)",
    "⚡ VOLUMEN: [Confirmación volumen] (+X pts)",
    "📈 INDICADORES: [Confluencia] (+X pts)",
    "🎚️ NIVELES: [Proximidad S/R] (+X pts)",
    "⚠️ RIESGOS: [Advertencias y penalizaciones] (-X pts)"
  ]
}}
IMPORTANT: RETURN ONLY THE JSON. DO NOT USE MARKDOWN BLOCK QUOTES. DO NOT ADD CONVERSATIONAL TEXT.
FORMATO: JSON PURO.
"""

            # 4. Llamar al proveedor (Asyncnativo)
            try:
                response_text = await self.provider.generate_content(prompt)
            except Exception as e:
                # El error ya se loguea en el Provider si es grave.
                self._shared_cooldowns[cache_key] = now_ts # CRITICAL: Prevent Infinite Loop
                # CRITICAL: Update Last Analysis to prevent server from thinking it's empty and retrying
                fallback_res = self._build_neutral_result(f"IA {self.provider_type.upper()} no disponible (Cupo/Error).")
                self._shared_last_analysis[cache_key] = fallback_res
                return self._shared_last_analysis.get(symbol, fallback_res)
            
            # DEBUG LOG
            logger.info(f"🔍 [AI-RAW] {symbol} Response: {response_text}...")

            # 5. Parsear respuesta con extracción robusta
            res_data = self._extract_json(response_text)
            
            if not res_data:
                p_label = self.provider_type.upper() if self.provider_type else "IA"
                logger.error(f"❌ [{symbol}] Error de Parseo. Respuesta RAW de {p_label}:\n{response_text}")
                self._shared_cooldowns[cache_key] = now_ts # CRITICAL: Prevent Infinite Loop
                
                fallback_res = self._build_neutral_result(f"Error de formato en {p_label}")
                self._shared_last_analysis[cache_key] = fallback_res
                return self._shared_last_analysis.get(cache_key, fallback_res)
            
            try:
                score = res_data.get('score', 0)
                direction = res_data.get('direction', 0)
                reasoning = res_data.get('reasoning', "Analizado")
                factors = res_data.get('key_factors', [])

                # Construir factores para el UI
                factors_ui = []
                for i, f in enumerate(factors[:6]):  # Changed from 3 to 6 for new comprehensive analysis
                    # Parsear "CATEGORIA: Explicación (+Pts)"
                    parts = f.split(':', 1)
                    if len(parts) == 2:
                        k_txt = parts[0].strip().replace("🔭 ", "").replace("⚡ ", "").replace("🎚️ ", "") # Clean icons for Key if desired, or keep them
                        v_txt = parts[1].strip()
                        # Keep icons in Key for flavor? User likes emojis.
                        k_final = parts[0].strip()
                        v_final = v_txt
                    else:
                        k_final = f"Factor {i+1}"
                        v_final = f.strip()
                    
                    factors_ui.append({"k": k_final, "v": v_final})
                
                # Inyectar el razonamiento principal (VEREDICTO)
                factors_ui.insert(0, {"k": "🤖 Veredicto IA", "v": reasoning})

                # Guardar en cache y actualizar cooldown
                result = {
                    "entry": direction if score >= 86 else 0, # Threshold matches prompt: 86-100 = very high probability
                    "atr": 0,
                    "metadata": {
                        "strategy": self.STRATEGY_NAME,
                        "score": score,
                        "total_score": score,
                        "direction": direction,
                        "score_breakdown": {"IA": reasoning},
                        "factors_detailed": factors_ui,
                        "can_entry": score >= 86  # Consistent with entry threshold
                    },
                    "score": score
                }
                # 6. Guardar en memoria de clase y en Base de Datos (Para sincronización)
                self._shared_last_analysis[cache_key] = result
                self._shared_cooldowns[cache_key] = now_ts
                
                # Persistencia en DB (Async)
                # Guardamos con el nombre del proveedor para que no choquen
                asyncio.create_task(self.db.save_ai_oracle_state(f"{symbol}_{p_name}", json.dumps(result), now_ts))
                
                return result

            except Exception as parse_error:
                logger.error(f"❌ Error parseando respuesta de IA: {parse_error}")
                self._shared_cooldowns[cache_key] = now_ts # CRITICAL: Prevent Infinite Loop
                
                fallback_res = self._build_neutral_result("Error en razonamiento de IA")
                self._shared_last_analysis[cache_key] = fallback_res
                return self._shared_last_analysis.get(symbol, fallback_res)

        except Exception as e:
            logger.error(f"❌ Error en estrategia AI Oracle: {e}")
            if 'cache_key' in locals(): 
                 self._shared_cooldowns[cache_key] = now_ts # CRITICAL
                 # Update Last Analysis
                 fallback_res = self._build_neutral_result("IA Offline/Error")
                 self._shared_last_analysis[cache_key] = fallback_res
            
            return self._shared_last_analysis.get(symbol, self._build_neutral_result("IA Temporalmente Offline"))

    def _build_neutral_result(self, msg):
        return {
            "entry": 0,
            "atr": 0,
            "metadata": {
                "strategy": self.STRATEGY_NAME,
                "score": 0,
                "total_score": 0,
                "score_breakdown": {"Estado": msg},
                "factors_detailed": [{"k": "Estado", "v": msg}]
            },
            "score": 0
        }

    def _extract_json(self, text: str) -> dict:
        """Extrae el primer bloque JSON válido de una cadena usando múltiples estrategias."""
        if not text:
            return None
            
        import re
        
        # Pre-Limpieza: Eliminar alucinaciones comunes como "-1 (SELL)" -> "-1"
        # Esto elimina cualquier texto entre paréntesis que venga después de un número
        text = re.sub(r'(\d+)\s*\([a-zA-Z\s]+\)', r'\1', text)
        
        # Estrategia 1: Bloques de código Markdown (```json ... ```)
        code_blocks = re.findall(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        for block in code_blocks:
            try: return json.loads(block)
            except: continue
            
        # Estrategia 2: Regex Greedy (Del primer '{' al último '}')
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            json_str = match.group()
            try: return json.loads(json_str)
            except: 
                # Segundo intento: limpieza agresiva de claves sin comillas o comas finales
                pass
            
            
        # Estrategia 3: Fallback Scraping para formato Markdown Stylized (Groq/Llama)
        # Soporta: "**Score:** 82", "**Score**: 85", "Score: 85", y keys en inglés/español
        # Estrategia 3: Fallback Scraping para formato Markdown Stylized (Groq/Llama)
        try:
            # Score: Permite "**Score:**", "**Score**:", "Score:", etc.
            score_match = re.search(r'(?:Score|Puntuación)[^:]*:\s*(?:\*)*\s*(\d+)', text, re.IGNORECASE)
            
            # Dirección
            dir_match = re.search(r'(?:Direction|Dirección)[^:]*:\s*(?:\*)*\s*(-?1|0)', text, re.IGNORECASE)
            
            # Razonamiento
            # Busca comillas primero, si no, coge resto de línea
            reason_match = re.search(r'(?:Reasoning|Razonamiento)[^:]*:\s*(?:\*)*\s*"([^"]+)"', text, re.IGNORECASE)
            if not reason_match:
                 reason_match = re.search(r'(?:Reasoning|Razonamiento)[^:]*:\s*(?:\*)*\s*(.+)', text, re.IGNORECASE)

            factors = []
            # Buscar lineas de lista (numerada o bullets)
            # Regex: (numero/bullet) (espacio) (**Clave**) (:) (Valor)
            factor_matches = re.findall(r'(?:\d+\.|\*|-)\s*(?:\*\*)?(.*?)(?:\*\*)?:\s*(.*)', text)
            
            clean_factors = []
            for k, v in factor_matches:
                k_clean = k.strip()
                # Filtrar si la "Clave" capturada es en realidad "Score" o similar
                if k_clean.lower() not in ['score', 'direction', 'reasoning', 'puntuación', 'dirección', 'razonamiento']:
                    clean_factors.append(f"{k_clean}: {v.strip()}")
            
            factors = clean_factors

            if score_match:
                return {
                    "score": int(score_match.group(1)),
                    "direction": int(dir_match.group(1)) if dir_match else 0,
                    "reasoning": reason_match.group(1).strip() if reason_match else "Análisis textual (Ver detalle)",
                    "key_factors": factors
                }
        except Exception as e_scrape:
            logger.debug(f"Scraping fallback failed: {e_scrape}")

        return None
