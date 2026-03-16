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
    STRATEGY_TYPE = "ALL" # Permite operar en cualquier régimen (Trend/Range/Volatile)
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
        
        # Nombre dinámico si tiene proveedor fijo para coincidir con config.ENABLED_STRATEGIES
        if provider_type:
            p_name = provider_type.capitalize()
            self.STRATEGY_NAME = f"PST-AI-Oracle-{p_name}"

        # Cache compartida entre hilos/instancias (Sincronización)
        if not hasattr(self, '_shared_cooldowns'):
            self.__class__._shared_cooldowns = {} 
        if not hasattr(self, '_shared_last_analysis'):
            self.__class__._shared_last_analysis = {}

    async def _ensure_provider(self, symbol: str, force: bool = False):
        """Inicializa o cambia el proveedor de IA dinámicamente, respetando los interruptores maestros."""
        p_type = self.provider_type
        
        if not p_type or p_type.upper() == "AUTO":
            p_type = await self.db.get_config(f"ai_provider_{symbol}")
            if not p_type or p_type.upper() == "AUTO":
                p_type = await self.db.get_config("ai_provider")
                if not p_type or p_type.upper() == "AUTO":
                    p_type = "gemini" # El último recurso real
        
        p_type = p_type.lower()
        
        # --- NEW: GLOBAL MASTER SWITCH CHECK (Standardized naming) ---
        global_switch_key = f"enabled_PST-AI-Oracle-{p_type.capitalize()}"
        global_active = await self.db.get_config(global_switch_key, default="true")
        
        if global_active.lower() == "false" and not force:
            logger.debug(f"🛑 [IA GLOBAL] {p_type.upper()} está desactivado en el Panel Maestro. Omitiendo {symbol}.")
            return "silenced"
        
        # Validación final contra los proveedores soportados
        if p_type not in self.VALID_PROVIDERS:
            logger.warning(f"⚠️ Proveedor '{p_type}' no reconocido en {symbol}. Usando GEMINI por defecto.")
            p_type = "gemini"
        
        # Solo re-instanciar si el proveedor ha cambiado o no existe
        if not self.provider or getattr(self.provider, '_type', None) != p_type:
            logger.info(f"🧠 [{symbol}] Usando Cerebro IA: {p_type.upper()} (Force: {force})")
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

    async def calculate_signal(self, mtf_data, current_regime, user_levels=None, force=False, **kwargs):
        """Analiza el mercado usando Inteligencia Artificial. force=True ignora filtros técnicos."""
        
        # 2. Control de frecuencia (Anti-Burst)
        symbol = mtf_data.get('symbol', 'UNKNOWN')
        
        # A. Asegurar proveedor dinámico (Pasar flag de force)
        p_name = await self._ensure_provider(symbol, force=force)
        
        if not p_name: 
            return self._build_neutral_result(f"Configurar IA para {symbol}")
            
        if p_name == "silenced":
            return self._build_neutral_result(f"IA Desactivada Globalmente")

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
            # AUMENTO DE CONTEXTO: 60 Velas para patrones chartistas y estructuras (Hombro-Cabeza-Hombro, Dobles, etc.)
            # Fix: reset_index() insures 'time' is a column in the dict
            last_candles = df_m5.tail(60).reset_index().to_dict(orient='records') 
            
            # Cálculo de Volumen Promedio y ATR Local
            try:
                vol_values = [str(c.get('tick_volume', 0)) for c in last_candles] 
                avg_vol = sum(int(c.get('tick_volume', 0)) for c in last_candles) / len(last_candles)
            except: avg_vol = 999999
            
            candles_summary = []
            for c in last_candles:
                v_c = int(c.get('tick_volume', 0))
                t_str = str(c.get('time', ''))[11:16] if 'time' in c else ""
                candles_summary.append({
                    "t": t_str,
                    "o": round(c['open'], 5), "h": round(c['high'], 5),
                    "l": round(c['low'], 5), "c": round(c['close'], 5),
                    "v": f"{v_c}"
                })
            
            # --- CONTEXT REFINEMENT: GROUND TRUTH (Stricter & Fairer) ---
            # 1. Use finished candle for base stability
            v_prev = int(last_candles[-2].get('tick_volume', 0)) if len(last_candles) > 1 else 0
            v_ma_val = avg_vol if avg_vol > 0 else 1
            
            # 2. Check current candle relative to mean
            v_curr = int(last_candles[-1].get('tick_volume', 0))
            v_curr_rel = v_curr / v_ma_val
            
            # 3. Factor: Use the higher of both to avoid "start-of-candle" penalty
            vol_ratio = round(max(v_curr_rel, v_prev / v_ma_val), 2)
            vol_status = "MUY BAJO" if vol_ratio < 0.7 else "NORMAL" if vol_ratio < 1.3 else "ALTO" if vol_ratio < 2.0 else "EXPLOSIVO"

            # A. Métricas Técnicas (M5)
            indicators = {}
            try:
                import pandas_ta as ta
                # RSI y ADX M5 (Robust)
                df_m5_clean = df_m5.tail(50).ffill().fillna(0)
                rsi_s = ta.rsi(df_m5_clean['close'], length=14)
                indicators['rsi_m5'] = round(rsi_s.iloc[-1], 2) if rsi_s is not None and not rsi_s.empty else 50
                
                adx_res = ta.adx(df_m5_clean['high'], df_m5_clean['low'], df_m5_clean['close'], length=14)
                _adx_val = 0
                if adx_res is not None and not adx_res.empty:
                    adx_cols = [c for c in adx_res.columns if 'ADX' in c]
                    if adx_cols:
                        _adx_val = adx_res[adx_cols[0]].iloc[-1]
                indicators['adx_m5'] = round(float(_adx_val), 2) if not pd.isna(_adx_val) else 0
                
                # ATR (Volatilidad)
                atr_s = ta.atr(df_m5['high'], df_m5['low'], df_m5['close'], length=14)
                indicators['atr_m5'] = round(atr_s.iloc[-1], 5) if atr_s is not None else 0
                
                # Distancia y Posición vs EMAs (Explicit Tags)
                ema21 = ta.ema(df_m5['close'], length=21).iloc[-1]
                ema50 = ta.ema(df_m5['close'], length=50).iloc[-1]
                dist_21 = ((current_price - ema21) / ema21) * 100
                dist_50 = ((current_price - ema50) / ema50) * 100
                
                indicators['pos_ema21'] = f"[{'ARRIBA' if current_price > ema21 else 'DEBAJO'}] ({dist_21:+.3f}%)"
                indicators['pos_ema50'] = f"[{'ARRIBA' if current_price > ema50 else 'DEBAJO'}] ({dist_50:+.3f}%)"
                indicators['crossover'] = f"[{'ALCISTA (Oro)' if ema21 > ema50 else 'BAJISTA (Muerte)'}]"
                
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

            # B. Estructura Multi-Timeframe (Detección Basada en EMA21 + Precios Recientes)
            mtf_context = {}
            for tf in ['m5', 'm15', 'h1', 'h4']:
                df_tf = mtf_data.get(tf)
                if df_tf is not None and len(df_tf) > 25:
                    try:
                        ema_tf = ta.ema(df_tf['close'], length=21).iloc[-1]
                        last_3 = df_tf['close'].tail(3).round(5).tolist()
                        c_tf = last_3[-1]
                        trend = f"[{'ALCISTA' if c_tf > ema_tf else 'BAJISTA'}]"
                        rsi_tf = 50
                        r_s = ta.rsi(df_tf['close'], length=14)
                        if r_s is not None: rsi_tf = round(r_s.iloc[-1], 1)
                        mtf_context[tf] = {
                            "estado": f"{trend} (vs EMA21)",
                            "rsi": rsi_tf,
                            "precios_recientes": last_3
                        }
                    except: mtf_context[tf] = "Error de Datos"
                else: mtf_context[tf] = "Sin datos suficientes"

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
            
            # 2. SPREAD AWARENESS (Simulated)
            # Usamos % del precio para ser compatible con Crypto/Indices
            atr_val = indicators.get('atr_m5', 0)
            if current_price > 0:
                spread_pct = (atr_val / current_price) * 100
            else:
                spread_pct = 0
            
            # Penalizar si la volatilidad es demasiado baja (< 0.01%) o absurda (> 5%)
            spread_penalty = -5 if spread_pct < 0.01 else 0
            spread_info = f"~{spread_pct:.4f}% Volatilidad (ATR)"
            
            # 3. VOLATILITY CONTEXT
            atr_current = indicators.get('atr_m5', 0)
            # TODO: Calculate 20-day ATR average from historical data
            # For now, assume current ATR is the baseline
            volatility_info = f"ATR: {atr_current:.5f}"
            
            # ========== END PHASE 1 IMPROVEMENTS ==========


            # ÚLTIMAS 5 VELAS (DATOS REALES) - FIX: Usar las 5 más recientes [-5:] para evitar lag temporal
            recent_candles_slice = candles_summary[-5:]
            server_time = datetime.now().strftime("%H:%M:%S")

            # Construir el Prompt Super Vision V15 (Human Feedback)
            prompt = f"""
Eres un GESTOR DE RIESGOS INSTITUCIONAL. Análisis UX-Friendly.
Hora Actual: {server_time}

IMPORTANTE: Responde SIEMPRE en ESPAÑOL. Usa un lenguaje CLARO y DIRECTO.

=== REGLAS DE UI (NUEVAS) ===
- No uses JSON ni llaves dentro de tus explicaciones.
- Los "key_factors" deben ser frases cortas de máximo 5-7 palabras que un humano entienda al vuelo.
- Si el score >= 75, tu veredicto debe ser "EJECUTANDO OPERACIÓN".
- Si el score < 75, tu veredicto debe ser "OBSERVANDO MERCADO".

=== REGLAS DE COHERENCIA ===
- DIRECCIÓN 1: Solo si Precio > EMAs y quieres comprar.
- DIRECCIÓN -1: Solo si Precio < EMAs y quieres vender.
- DIRECCIÓN 0: Si prefieres esperar.

=== ANÁLISIS REQUERIDO ===
1. GATILLO: ¿Qué ha pasado en las últimas 5 velas? (Ej: "Ruptura limpia de EMA21").
2. PATRONES: ¿Ves algún triángulo, bandera o canal? ¿Hacia dónde proyectan?
3. SINERGIA: ¿ADX y Volumen apoyan el movimiento actual?

=== ESCALA DE PUNTUACIÓN ===
- 85-100 (EJECUCIÓN VIP): Gatillo reciente + Volumen explosivo + Alineación Total.
- 75-84 (EJECUCIÓN): Setup fuerte con gatillo en velas recientes.
- 60-74 (OBSERVACIÓN): Tendencia saludable pero sin gatillo óptimo AHORA.
- 0-59 (ESPERA): Sin señal clara.

=== GROUND TRUTH (DATOS REALES IRREFUTABLES) ===
- PRECIO ACTUAL: {current_price}
- VOLUMEN ACTUAL: {vol_status} (Ratio vs Media: {vol_ratio}x)
- FUERZA (ADX): {indicators.get('adx_m5')} ({'SIN TENDENCIA' if indicators.get('adx_m5',0) < 20 else 'TENDENCIA DÉBIL' if indicators.get('adx_m5',0) < 25 else 'FUERTE'})
- RSI M5: {indicators.get('rsi_m5')}
- POSICIÓN: {indicators.get('pos_ema21')} y {indicators.get('pos_ema50')}
- VELAS RECIENTES (Últimas 5): {json.dumps(recent_candles_slice)}

=== MÓDULO DE ESCEPTICISMO (REGLAS ESTRICTAS) ===
1. Si el VOLUMEN es < 1.0x o ADX < 20, el SCORE NO PUEDE SUPERAR 65 (aunque el precio parezca romper algo).
2. "EXPLOSIVO" requiere Volumen > 2.0x. No mientas.
3. El SCORE 85+ solo existe si hay: VOLUMEN ALTO + ADX > 25 + CONFLUENCIA MTF.
4. Si el precio está "pegado" a la EMA21 sin volumen, di "RANGO INDECISO" y score < 50.

Salida JSON REQUERIDA (SIEMPRE en ESPAÑOL):
{{
  "score": (0-100),
  "direction": (1, -1, 0),
  "veredicto_ia": "[Resumen ejecutivo: Qué gatillo se ha activado, qué patrón ves a futuro y por qué ahora es el momento. Incluye DATOS CLAVE como el ATR relativo o RSI actual en el texto]",
  "key_factors": [
    "LOCAL: [Analiza M5 y la relación con EMAs]",
    "MACRO: [Analiza H1 y M15. ¿Hay alineación?]",
    "GATILLO: [Analiza Volumen y ADX. ¿Son suficientes?]",
    "ZONAS CLAVE: [Soportes/Resistencias EXACTOS]",
    "RIESGO: [Nivel de peligro y STOP sugerido]"
  ]
}}
DO NOT ADD TEXT OUTSIDE THE JSON. NO MARKDOWN BLOCKS. NO REPEATED KEYS. 
SI EL VOLUMEN ES BAJO O EL ADX < 20, SE PESIMISTA. VALORA LA ESPERA SOBRE LA ACCIÓN.
"""

            # 4. Llamar al proveedor (Asyncnativo)
            try:
                response_text = await self.provider.generate_content(prompt)
            except Exception as e:
                # El error ya se loguea en el Provider si es grave.
                self._shared_cooldowns[cache_key] = now_ts 
                fallback_res = self._build_neutral_result(f"Error Provider: {str(e)}")
                self._shared_last_analysis[cache_key] = fallback_res
                return fallback_res
            
            # DEBUG LOG
            # logger.info(f"🔍 [AI-RAW] {symbol} Response: {response_text}...")

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
                reasoning = res_data.get('veredicto_ia') or res_data.get('reasoning') or res_data.get('veredicto') or ""
                factors = res_data.get('key_factors', [])

                # --- NEW: SEARCH FOR ANALYSIS IN FACTORS (PROMOTING BEST TEXT) ---
                if not reasoning or "ANALISIS TEXTUAL" in reasoning.upper() or "VER DETALLE" in reasoning.upper():
                    # Look for a factor that looks like long-form analysis
                    potential_reasoning = ""
                    for f in factors:
                        # If a factor belongs to a "Note", "Analysis" or is just long (> 40 chars)
                        if any(x in f.upper() for x in [': NOTA', 'RAZONAMIENTO:', 'ANALISIS:', 'VEREDICTO:']) or len(f) > 80:
                            parts = f.split(':', 1)
                            potential_reasoning = parts[1].strip() if len(parts) > 1 else f.strip()
                            break
                    if potential_reasoning:
                        reasoning = potential_reasoning

                # Construir factores para el UI (Filtrado Robusto)
                factors_ui = []
                exclude_keys = ['REASONING', 'RAZONAMIENTO', 'DIRECTION', 'DIRECCIÓN', 'STATUS', 'ACTION', 'SCORE', 'PUNTUACIÓN', 'VEREDICTO']
                
                for i, f in enumerate(factors[:8]):
                    parts = f.split(':', 1)
                    if len(parts) == 2:
                        k_final = parts[0].strip().replace('*', '').replace('🔭', '').replace('⚡', '').replace('🎚️', '').replace('📍', '')
                        v_final = parts[1].strip().replace('*', '')
                    else:
                        k_final = f"Dato {i+1}"
                        v_final = f.strip().replace('*', '')
                    
                    # Evitar duplicados técnicos o redundantes
                    k_upper = k_final.upper()
                    if any(x in k_upper for x in exclude_keys):
                        continue
                    if v_final in reasoning: # No repetir si ya está en el veredicto general
                        continue

                    factors_ui.append({"k": k_final, "v": v_final})
                
                # --- AUDIT FACTORS (GROUND TRUTH) ---
                curr_adx = indicators.get('adx_m5', 0)
                bb_pos = indicators.get('bb_position', 50)
                factors_ui.append({"k": "📊 Vol Ratio", "v": f"{vol_ratio}x ({vol_status})"})
                factors_ui.append({"k": "🛡️ Fuerza Trend", "v": f"ADX {curr_adx:.1f}"})
                factors_ui.append({"k": "📏 Ubicación BB", "v": f"{bb_pos}% ({'Extremo' if bb_pos > 90 or bb_pos < 10 else 'Media'})"})
                factors_ui.append({"k": "📉 RSI Context", "v": f"{indicators.get('rsi_m5', 50)}"})
                
                # --- NEW: SKEPTICISM SAFETY FILTER (HARDWARE VALIDATION) ---
                original_score = score
                is_capped = False
                cap_reason = ""

                vol_ratio_val = locals().get('vol_ratio', 1.0)
                
                # Rule 1: Low Volume Cap
                if vol_ratio_val < 0.9 and score > 65:
                    score = 65
                    is_capped = True
                    cap_reason = "Bajo Volumen"
                
                # Rule 2: No Trend (ADX) Cap
                if curr_adx < 20 and score > 60:
                    score = 60
                    is_capped = True
                    cap_reason = "Ausencia de Tendencia (ADX < 20)"

                # Rule 3: EMA Sandwich (No confirmación)
                dist_21_50 = abs(ema21 - ema50)
                if abs(current_price - ema21) < (dist_21_50 * 0.5) and score > 50 and curr_adx < 25:
                    score = 50
                    is_capped = True
                    cap_reason = "Zona de Indecisión (Sandwich EMA21/50)"

                # Inyectar el razonamiento principal (VEREDICTO) con indicador visual
                dir_icon = "🟢 COMPRA" if direction == 1 else "🔴 VENTA" if direction == -1 else "⚪ ESPERA"
                clean_reasoning = reasoning.replace('**', '').strip()
                
                if is_capped:
                    logger.warning(f"🛡️ [{symbol}] Score AI Ajustado ({original_score} -> {score}) por {cap_reason}")
                    clean_reasoning = f"⚠️ [RECHAZO INSTITUCIONAL: {cap_reason}] {clean_reasoning}"

                # --- NEW: CONSULTATION MODE SAFETY (MODO CONSULTA) ---
                # Si force=True pero el interruptor global sigue OFF, capamos la ejecución automática
                global_switch_key = f"global_ai_{p_name.lower()}"
                global_active_cfg = await self.db.get_config(global_switch_key, default="true")
                global_active = global_active_cfg.lower() == "true"
                
                if not global_active and force:
                    is_capped = True
                    cap_reason = "MODO CONSULTA"
                    score = min(score, 60) # Visual/Informativo
                    direction = 0          # Bloqueo total de señal
                    clean_reasoning = f"🛡️ [MODO CONSULTA: MASTER OFF] {clean_reasoning}"
                    logger.info(f"🛡️ [{symbol}] Bloqueo de Trading aplicado: MODO CONSULTA ACTIVO.")

                factors_ui.insert(0, {"k": "🤖 Veredicto IA", "v": f"[{dir_icon}] {clean_reasoning}"})

                # Guardar en cache y actualizar cooldown
                result = {
                    "entry": direction if score >= 85 else 0,
                    "atr": 0,
                    "metadata": {
                        "strategy": self.STRATEGY_NAME,
                        "score": score,
                        "total_score": score,
                        "direction": direction,
                        "score_breakdown": {"IA": reasoning},
                        "factors_detailed": factors_ui,
                        "can_entry": score >= 80 
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
                # Better Reasoning Extraction for Fallback
                reason_val = "Análisis detectado (Ver factores)"
                if reason_match:
                    reason_val = reason_match.group(1).strip()
                elif len(factors) > 0:
                    # If we have factors, maybe the first one is the reasoning
                    for f in factors:
                        if any(x in f.upper() for x in ['NOTA', 'ANALISIS', 'RAZONAMIENTO']):
                             parts = f.split(':', 1)
                             reason_val = parts[1].strip() if len(parts) > 1 else f.strip()
                             break

                return {
                    "score": int(score_match.group(1)),
                    "direction": int(dir_match.group(1)) if dir_match else 0,
                    "veredicto_ia": reason_val,
                    "key_factors": factors
                }
        except Exception as e_scrape:
            logger.debug(f"Scraping fallback failed: {e_scrape}")

        return None
