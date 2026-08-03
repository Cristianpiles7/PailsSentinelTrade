import pandas as pd
import pandas_ta as ta
import logging

logger = logging.getLogger("PST-PrecisionScalping")


class PSTPrecisionScalping:
    """
    Scalping de precisión M1 con contexto M5.

    Núcleo de la señal (gate obligatorio): cruce fresco de EMA 9/21 en M1.
    Sobre ese gate se apilan filtros de calidad que suben o hunden el score:

      · Contexto de precio ...... VWAP de sesión (reset diario) + bandas σ dinámicas
      · Momentum superior ....... RSI M5 (nivel + pendiente)
      · Calidad de tendencia .... ADX M5 + Choppiness Index M5  (Filtro de Ruido)
      · Microestructura ......... Volumen relativo + Rate-of-Change de tick volume
      · Acción del precio ....... Engulfing / Pin bar en M1 sobre niveles clave
      · Volatilidad ............. ratio ATR M1/M5 (evita mercado dormido)
      · Anti-spike / spread ..... rechaza velas parabólicas y spread caro

    Estructura (para SL/TP): fractales Donchian de corto plazo (10/20) que se
    exponen al executor vía metadata (target_price_sl) y como tp_price técnico.
    """

    STRATEGY_NAME = "PST-PrecisionScalping"
    STRATEGY_TYPE = "SCALPING"

    # ------------------------------------------------------------------ #
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _last(series, default=0.0):
        """Último valor float no-NaN de una Serie/valor; robusto a None."""
        try:
            if series is None:
                return default
            val = float(series.iloc[-1]) if hasattr(series, "iloc") else float(series)
            if val != val:  # NaN
                return default
            return val
        except Exception:
            return default

    @staticmethod
    def _time_values(df):
        """
        Devuelve un array de fechas alineado con df, sea 'time' una columna
        (backtest) o el índice DatetimeIndex (vivo). None si no hay timestamps.
        """
        try:
            if "time" in df.columns:
                return pd.to_datetime(df["time"]).dt.date.values
            if isinstance(df.index, pd.DatetimeIndex):
                return df.index.date
        except Exception:
            pass
        return None

    @classmethod
    def _session_df(cls, df):
        """Filtra las velas de la sesión (día) actual para el VWAP intradía."""
        dates = cls._time_values(df)
        if dates is None:
            return df
        try:
            today = dates[-1]
            mask = dates == today
            if mask.sum() >= 5:
                return df[mask]
        except Exception:
            pass
        return df

    @staticmethod
    def _vwap_bands(session_df, vol_col, atr_fallback):
        """
        VWAP de sesión + bandas de desviación estándar ponderada por volumen.
        Las bandas σ son intrínsecamente DINÁMICAS: se ensanchan/estrechan con la
        volatilidad realizada de la sesión (equivalente a Bollinger sobre el VWAP).
        Devuelve (vwap, u1, l1, u2, l2).
        """
        s_typical = (session_df["high"] + session_df["low"] + session_df["close"]) / 3
        vwap = float(s_typical.iloc[-1])

        if vol_col and session_df[vol_col].sum() > 0:
            s_vol = session_df[vol_col]
            cum_vol = s_vol.cumsum()
            vwap_series = (s_typical * s_vol).cumsum() / cum_vol
            vwap = float(vwap_series.iloc[-1])
            variance = ((s_typical - vwap_series) ** 2 * s_vol).cumsum() / cum_vol
            std = float(variance.iloc[-1] ** 0.5)
        else:
            # Proxy sin volumen: EMA20 del típico y bandas por ATR
            proxy = ta.ema(s_typical, length=20)
            if proxy is not None and len(proxy.dropna()):
                vwap = float(proxy.iloc[-1])
            std = atr_fallback

        return vwap, vwap + std, vwap - std, vwap + 2 * std, vwap - 2 * std

    @staticmethod
    def _is_crypto(symbol: str) -> bool:
        """True si el símbolo es cripto (filtro de ruido relajado)."""
        try:
            from ..utils.tech_utils import get_asset_class
            return get_asset_class(symbol) == "CRYPTO"
        except Exception:
            s = (symbol or "").upper()
            return any(k in s for k in ("BTC", "ETH", "SOL", "ADA", "XRP", "LTC", "DOT", "AVAX", "DOGE"))

    # Perfil de filtros por defecto. Cada símbolo puede sobreescribir cualquier clave vía la
    # columna `filter_profile` (JSON) de symbol_strategies, que el orquestador inyecta como
    # kwarg. Los defaults reproducen el comportamiento calibrado actual (Fase 2).
    FILTER_DEFAULTS = {
        # Timeframe de entrada (gate EMA9/21 + estructura SL/TP) y de validación
        # (RSI/ADX/Chop de contexto). Configurable por símbolo/grupo vía filter_profile:
        # algunos símbolos pueden tener menos whipsaw en M2/M3 que en M1 (ver
        # entry_tf sweep). Valores válidos: "m1", "m2", "m3", "m5" (los que expone
        # get_mtf_data_async). Por defecto reproduce el comportamiento calibrado (M1/M5).
        "entry_tf": "m1",
        "validation_tf": "m5",
        "entry_threshold": 70,        # score mínimo (régimen normal)
        "entry_threshold_volatile": 80,
        "adx_clean": 25.0, "chop_clean": 38.2,   # tendencia limpia → bonus
        "adx_ok": 18.0,    "chop_ok": 61.8,      # aceptable → neutro
        "adx_extreme": 15.0, "chop_extreme": 70.0,  # lateral extrema → veto
        "noise_mode": "on",           # 'on' = penaliza ruido moderado; 'soft'/'off' = no penaliza
        "noise_penalty": 8,
        "rsi_strong": 55.0,           # RSI M5 para momentum fuerte (short usa 100-rsi_strong)
        "rsi_ok": 50.0,
        "m1_eff_mode": "off",         # 'off' = no evalúa (default forex/index/metal); 'on' = activo
        "m1_eff_lookback": 20,        # velas M1 para el ratio de eficiencia (Kaufman ER)
        "m1_eff_clean": 0.25,         # eficiencia alta → micro-tendencia limpia, bonus
        "m1_eff_ok": 0.10,            # por debajo → whipsaw M1, penaliza
        "m1_eff_penalty": 10,
        # Salida dinámica (check_exit_signal): 'on' = activa (cruce VWAP en contra + giro de
        # momentum, o cruce EMA9/21 fresco opuesto). El backtest fiel (A/B por grupo) mostró
        # que esta salida AYUDA en forex (corta pérdidas antes del SL) pero PERJUDICA en
        # cripto e índices (corta rachas ganadoras antes de tiempo) — se ajusta por grupo
        # en el seed de la BBDD, no aquí (este 'on' es el default genérico).
        "vwap_exit": "on",
        # Borde de sesión: 'off' por defecto (sin efecto, retrocompatible). 'on' penaliza
        # entradas muy cerca de la apertura/cierre real del instrumento. Solo tiene
        # fundamento donde existe un GAP diario de verdad (mercado cerrado de un día para
        # otro), verificado empíricamente en MT5 (no por suposición de "horario NYSE/Xetra
        # en CET" — el server del bróker no sigue el DST de EEUU y difiere ~1h):
        #   · EQUITIES (AAPL/MSFT): cierre real 22:59→16:35 del día siguiente.
        #   · EU50.cash: cierre real 22:59→09:05 (única entre los índices — el resto,
        #     US500/US100/US30/GER40/UK100, cotiza casi continuo con solo una pausa de
        #     mantenimiento de ~76min sobre medianoche, SIN gap real de sesión).
        # FOREX/METAL/resto de INDEX cotizan sin gap real (el usuario opera 24h ahí) — no
        # se aplica. CRIPTO 24/7, sin sesión. Ver _SESSION_BOUNDARIES.
        "session_edge_mode": "off",
        "session_edge_buffer_min": 15,
        "session_edge_penalty": 12,
        # Reapertura semanal: 'off' por defecto (sin efecto, retrocompatible). A diferencia
        # del borde de sesión diario (que solo aplica donde hay gap diario real), el gap del
        # FIN DE SEMANA existe en todo el universo salvo cripto: los primeros minutos del
        # lunes (hora bróker) reabren con liquidez fina, spread ancho y el precio digiriendo
        # el gap. Auditoría BBDD 13-14 jul: las 3 entradas del lunes 00:38-00:49 (JP225,
        # GER40, US500) fueron 0/3 (-20.16€), las tres vendiendo el extremo inferior del
        # rango post-gap. Modos: 'penalty' descuenta weekend_reopen_penalty del score;
        # 'block' resta 100 (veto efectivo). El juez fiel lo modela automáticamente al
        # activarlo (el filtro vive en la estrategia, que pst_bt_lab carga tal cual).
        "weekend_reopen_mode": "off",
        "weekend_reopen_buffer_min": 60,
        "weekend_reopen_penalty": 15,
    }

    # Bordes de sesión (minutos desde medianoche, hora bróker/MT5) — horarios REALES
    # verificados en MT5 (varios días consecutivos, sin excepción), no calculados por
    # zona horaria teórica. Solo los símbolos con un gap diario genuino tienen entrada aquí.
    _SESSION_BOUNDARIES = {
        "EQUITIES": [(16 * 60 + 35, 22 * 60 + 59)],
    }
    _SESSION_BOUNDARIES_BY_SYMBOL = {
        "EU50.cash": [(9 * 60 + 5, 22 * 60 + 59)],
    }

    _EQUITIES_KEYWORDS = ("NVDA", "TSLA", "AAPL", "MSFT", "GOOG", "AMZN", "META", "NFLX")

    @classmethod
    def _session_edge_distance(cls, symbol, bar_time):
        """Minutos hasta el borde de sesión (apertura/cierre) más cercano. None si el
        símbolo no tiene un gap diario real (forex, metal, cripto, y la mayoría de índices).

        Fallback SIN import relativo (igual que _is_crypto): el harness de backtest carga
        esta estrategia como módulo suelto (sin paquete padre) para comparar working-tree
        vs baseline de git, y ahí `from ..utils import ...` lanza ImportError silencioso —
        sin el fallback, el filtro nunca se evaluaría en el backtest (bug real detectado
        y corregido: los 30 días de prueba inicial dieron 0 penalizaciones)."""
        bounds = cls._SESSION_BOUNDARIES_BY_SYMBOL.get(symbol)
        if bounds is None:
            try:
                from ..utils.tech_utils import get_asset_class
                a_class = get_asset_class(symbol)
            except Exception:
                s = (symbol or "").upper()
                a_class = "EQUITIES" if any(k in s for k in cls._EQUITIES_KEYWORDS) else None
            bounds = cls._SESSION_BOUNDARIES.get(a_class)
        if not bounds:
            return None
        minute_of_day = bar_time.hour * 60 + bar_time.minute
        best = None
        for start, end in bounds:
            for edge in (start, end):
                d = abs(minute_of_day - edge)
                d = min(d, 1440 - d)  # wrap-around medianoche
                if best is None or d < best:
                    best = d
        return best

    @classmethod
    def _resolve_profile(cls, kwargs, is_crypto: bool) -> dict:
        """
        Construye el perfil de filtros efectivo: defaults → default por clase de activo
        (cripto relaja el ruido) → override explícito de `filter_profile` (dict o JSON).
        """
        prof = dict(cls.FILTER_DEFAULTS)
        if is_crypto:
            prof["noise_mode"] = "soft"   # cripto: impulsos válidos con ADX bajo, no penalizar ruido
            # m1_eff_mode NO se activa por clase de activo: el backtest mostró que el whipsaw M1
            # que castiga a ETHUSD es un patrón propio de ese símbolo, no de "cripto" en general
            # (BTCUSD empeoró con el mismo filtro). Se activa por símbolo vía filter_profile.
        raw = kwargs.get("filter_profile")
        if raw:
            try:
                import json
                override = json.loads(raw) if isinstance(raw, str) else dict(raw)
                prof.update({k: v for k, v in override.items() if v is not None})
            except Exception:
                pass
        return prof

    @staticmethod
    def _candle_pattern(df):
        """
        Reconocimiento rápido de acción del precio en la última vela M1 cerrada.
        Devuelve (+1 alcista / -1 bajista / 0) y un nombre. Sin dependencia de TA-Lib.
        """
        try:
            o1, h1, l1, c1 = (float(df["open"].iloc[-1]), float(df["high"].iloc[-1]),
                              float(df["low"].iloc[-1]), float(df["close"].iloc[-1]))
            o2, c2 = float(df["open"].iloc[-2]), float(df["close"].iloc[-2])
        except Exception:
            return 0, ""

        rng = h1 - l1
        if rng <= 0:
            return 0, ""
        body = abs(c1 - o1)
        upper_wick = h1 - max(c1, o1)
        lower_wick = min(c1, o1) - l1

        # Engulfing (el cuerpo actual envuelve al anterior y cambia el signo)
        if c1 > o1 and c2 < o2 and c1 >= o2 and o1 <= c2:
            return 1, "Engulfing alcista"
        if c1 < o1 and c2 > o2 and c1 <= o2 and o1 >= c2:
            return -1, "Engulfing bajista"

        # Pin bar / martillo: mecha dominante y cierre en el tercio favorable
        if lower_wick > body * 2 and upper_wick < body and c1 >= l1 + rng * 0.6:
            return 1, "Pin bar alcista"
        if upper_wick > body * 2 and lower_wick < body and c1 <= h1 - rng * 0.6:
            return -1, "Pin bar bajista"

        return 0, ""

    @staticmethod
    def _trend_efficiency(close, lookback):
        """
        Kaufman Efficiency Ratio sobre M1: avance neto / recorrido bruto en la ventana.
        1.0 = tendencia pura, 0.0 = ida-y-vuelta puro (whipsaw). A diferencia de ADX/Chop
        M5 (miden calidad de tendencia a 5 min), esta mide ruido a la escala real de
        entrada (M1), donde ocurre el whipsaw que el filtro M5 no detecta.
        """
        try:
            seg = close.iloc[-lookback:] if len(close) >= lookback else close
            if len(seg) < 2:
                return 0.0
            net = abs(float(seg.iloc[-1]) - float(seg.iloc[0]))
            total = float(seg.diff().abs().sum())
            return net / total if total > 0 else 0.0
        except Exception:
            return 0.0

    # ------------------------------------------------------------------ #
    #  Señal principal                                                   #
    # ------------------------------------------------------------------ #
    async def calculate_signal(
        self,
        mtf_data,
        current_regime=None,
        user_levels=None,
        spread_points=0,
        spread_dist=0,
        **kwargs,
    ) -> dict:
        neutral = {
            "score": 0,
            "entry": 0,
            "atr": 0,
            "metadata": {"status": "Sin datos suficientes", "factors_detailed": []},
        }

        # Clase de activo + perfil de filtros ANTES de resolver timeframes: entry_tf/
        # validation_tf viven en el mismo filter_profile (configurable por símbolo).
        symbol = kwargs.get("symbol", "") or ""
        is_crypto = self._is_crypto(symbol)
        prof = self._resolve_profile(kwargs, is_crypto)
        entry_tf = str(prof.get("entry_tf", "m1"))
        validation_tf = str(prof.get("validation_tf", "m5"))

        df_m1 = mtf_data.get(entry_tf)
        df_m5 = mtf_data.get(validation_tf)

        if df_m1 is None or len(df_m1) < 30:
            return {**neutral, "metadata": {"status": f"{entry_tf.upper()} insuficiente (min 30 velas)", "factors_detailed": []}}
        if df_m5 is None or len(df_m5) < 30:
            return {**neutral, "metadata": {"status": f"{validation_tf.upper()} insuficiente (min 30 velas)", "factors_detailed": []}}

        close_m1 = df_m1["close"]
        high_m1 = df_m1["high"]
        low_m1 = df_m1["low"]

        # --- Indicadores M1 ---
        ema9 = ta.ema(close_m1, length=9)
        ema21 = ta.ema(close_m1, length=21)
        atr_m1 = ta.atr(high_m1, low_m1, close_m1, length=14)

        if any(x is None for x in [ema9, ema21, atr_m1]):
            return {**neutral, "metadata": {"status": "Error indicadores M1", "factors_detailed": []}}

        atr = self._last(atr_m1, 0.0)
        price = self._last(close_m1, 0.0)
        if atr <= 0 or price <= 0:
            return {**neutral, "metadata": {"status": "ATR/precio inválido", "factors_detailed": []}}

        # --- VWAP de sesión + bandas dinámicas ---
        vol_col = "tick_volume" if "tick_volume" in df_m1.columns else ("volume" if "volume" in df_m1.columns else None)
        try:
            session_df = self._session_df(df_m1)
            vwap_val, vwap_upper1, vwap_lower1, vwap_upper2, vwap_lower2 = self._vwap_bands(session_df, vol_col, atr)
        except Exception:
            vwap_val = price
            vwap_upper1 = vwap_lower1 = vwap_upper2 = vwap_lower2 = price

        # --- Indicadores M5 ---
        rsi_m5 = ta.rsi(df_m5["close"], length=14)
        atr_m5 = ta.atr(df_m5["high"], df_m5["low"], df_m5["close"], length=14)
        adx_m5 = ta.adx(df_m5["high"], df_m5["low"], df_m5["close"], length=14)
        chop_m5 = ta.chop(df_m5["high"], df_m5["low"], df_m5["close"], length=14)

        e9 = self._last(ema9)
        e9_prev = self._last(ema9.iloc[:-1])
        e21_val = self._last(ema21)
        e21_prev = self._last(ema21.iloc[:-1])
        atr5 = self._last(atr_m5, atr)
        rsi5 = self._last(rsi_m5, 50.0)
        rsi5_prev = self._last(rsi_m5.iloc[:-1], rsi5)

        adx5 = 0.0
        if adx_m5 is not None:
            adx_col = next((c for c in adx_m5.columns if c.startswith("ADX")), None)
            adx5 = self._last(adx_m5[adx_col]) if adx_col else 0.0
        chop5 = self._last(chop_m5, 50.0)

        score = 0
        factors = []
        direction = 0

        # 1. Cruce EMA 9/21 en M1 — GATE obligatorio de entrada.
        cross_bull = e9_prev <= e21_prev and e9 > e21_val
        cross_bear = e9_prev >= e21_prev and e9 < e21_val
        has_fresh_cross = cross_bull or cross_bear

        if cross_bull:
            score += 35
            direction = 1
            factors.append({"k": "EMA 9/21 M1", "v": "Cruce ALCISTA (señal fresca) ✅", "score": 35})
        elif cross_bear:
            score += 35
            direction = -1
            factors.append({"k": "EMA 9/21 M1", "v": "Cruce BAJISTA (señal fresca) ✅", "score": 35})
        elif e9 > e21_val:
            score += 10
            direction = 1
            factors.append({"k": "EMA 9/21 M1", "v": "Posición alcista (sin cruce, solo contexto)", "score": 10})
        elif e9 < e21_val:
            score += 10
            direction = -1
            factors.append({"k": "EMA 9/21 M1", "v": "Posición bajista (sin cruce, solo contexto)", "score": 10})
        else:
            factors.append({"k": "EMA 9/21 M1", "v": "NEUTRAL", "score": 0})

        if direction == 0:
            return {
                "score": 0, "entry": 0, "atr": atr,
                "metadata": {"status": "EMAs M1 sin dirección", "factors_detailed": factors},
            }

        # 2. VWAP de sesión + bandas — premia pullback, penaliza persecución
        near_upper2 = price >= vwap_upper2 * 0.998
        near_lower2 = price <= vwap_lower2 * 1.002
        near_upper1 = price >= vwap_upper1 * 0.999
        near_lower1 = price <= vwap_lower1 * 1.001

        if direction == 1:
            if price > vwap_val:
                if near_upper2:
                    score -= 25
                    factors.append({"k": "VWAP", "v": f"Precio en +2σ ({vwap_upper2:.5f}) — sobre-extendido ❌", "score": -25})
                elif near_upper1:
                    score -= 5
                    factors.append({"k": "VWAP", "v": f"Precio en +1σ ({vwap_upper1:.5f}) — ya extendido ⚠️", "score": -5})
                else:
                    score += 15
                    factors.append({"k": "VWAP", "v": f"Precio > VWAP ({vwap_val:.5f}) — momentum controlado ✅", "score": 15})
            else:
                score += 20
                factors.append({"k": "VWAP", "v": f"BUY cerca/bajo VWAP ({vwap_val:.5f}) — pullback sano ✅", "score": 20})
        else:
            if price < vwap_val:
                if near_lower2:
                    score -= 25
                    factors.append({"k": "VWAP", "v": f"Precio en -2σ ({vwap_lower2:.5f}) — sobre-extendido ❌", "score": -25})
                elif near_lower1:
                    score -= 5
                    factors.append({"k": "VWAP", "v": f"Precio en -1σ ({vwap_lower1:.5f}) — ya extendido ⚠️", "score": -5})
                else:
                    score += 15
                    factors.append({"k": "VWAP", "v": f"Precio < VWAP ({vwap_val:.5f}) — momentum controlado ✅", "score": 15})
            else:
                score += 20
                factors.append({"k": "VWAP", "v": f"SELL cerca/sobre VWAP ({vwap_val:.5f}) — pullback sano ✅", "score": 20})

        # 3. Momentum M5 (RSI nivel + pendiente) — hasta 20 pts
        rsi_strong = float(prof["rsi_strong"])           # long: > rsi_strong ; short: < (100-rsi_strong)
        rsi_strong_short = 100.0 - rsi_strong
        rsi_ok = float(prof["rsi_ok"])
        rsi_slope = rsi5 - rsi5_prev
        if direction == 1 and rsi5 > rsi_strong:
            pts = 20 if rsi_slope > 0 else 14
            score += pts
            factors.append({"k": "RSI M5", "v": f"{rsi5:.1f} (Δ{rsi_slope:+.1f}) momentum alcista ✅", "score": pts})
        elif direction == -1 and rsi5 < rsi_strong_short:
            pts = 20 if rsi_slope < 0 else 14
            score += pts
            factors.append({"k": "RSI M5", "v": f"{rsi5:.1f} (Δ{rsi_slope:+.1f}) momentum bajista ✅", "score": pts})
        elif direction == 1 and rsi5 > rsi_ok:
            score += 10
            factors.append({"k": "RSI M5", "v": f"{rsi5:.1f} (OK)", "score": 10})
        elif direction == -1 and rsi5 < rsi_ok:
            score += 10
            factors.append({"k": "RSI M5", "v": f"{rsi5:.1f} (OK)", "score": 10})
        else:
            score -= 10
            factors.append({"k": "RSI M5", "v": f"{rsi5:.1f} (Contra dirección ❌)", "score": -10})

        # 4. Microestructura: volumen relativo + ROC de tick volume — hasta 15 pts
        if vol_col and len(df_m1) >= 21:
            vol_s = df_m1[vol_col]
            vol_ma = self._last(vol_s.rolling(20).mean(), 0.0)
            vol_cur = self._last(vol_s, 0.0)
            vol_ref = self._last(vol_s.iloc[:-3], vol_cur)  # ~3 velas atrás
            vol_roc = (vol_cur - vol_ref) / vol_ref if vol_ref > 0 else 0.0

            v_pts = 0
            v_msg = f"{vol_cur:.0f}"
            vol_expand = vol_ma > 0 and vol_cur > vol_ma * 1.2
            if vol_expand:
                v_pts += 10
                v_msg += f" > MA20 {vol_ma:.0f} ✅"
                # El ROC solo puntúa si el flujo YA está expandido (confirmación real,
                # no puntos gratis por una simple aceleración desde volumen bajo).
                if vol_roc > 0.25:
                    v_pts += 5
                    v_msg += f" | ROC +{vol_roc*100:.0f}% ⚡"
            else:
                v_msg += " (Normal)"
            score += v_pts
            factors.append({"k": "Microestructura", "v": v_msg, "score": v_pts})
        else:
            factors.append({"k": "Microestructura", "v": "Sin datos de volumen", "score": 0})

        # 5. Volatilidad M1 activa (no mercado dormido) — hasta 10 pts
        if atr5 > 0:
            atr_ratio = atr / atr5
            if atr_ratio >= 0.3:
                score += 10
                factors.append({"k": "Volatilidad", "v": f"ATR M1/M5 ratio {atr_ratio:.2f} ✅", "score": 10})
            elif atr_ratio < 0.2:
                score -= 15
                factors.append({"k": "Volatilidad", "v": f"Mercado dormido (ratio {atr_ratio:.2f}) ❌", "score": -15})
            else:
                factors.append({"k": "Volatilidad", "v": f"ATR ratio {atr_ratio:.2f}", "score": 0})

        # 6. Acción del precio en M1 — confirmación/refutación de la vela — ±10 pts
        pat_dir, pat_name = self._candle_pattern(df_m1)
        at_level = near_upper1 or near_lower1 or abs(price - vwap_val) <= atr * 0.35
        # Solo bonifica el patrón a favor SOBRE un nivel clave (confluencia real);
        # un patrón a favor sin nivel no aporta y uno en contra sí penaliza.
        if pat_dir == direction and at_level:
            score += 8
            factors.append({"k": "Price Action", "v": f"{pat_name} a favor en nivel ✅", "score": 8})
        elif pat_dir == -direction:
            score -= 8
            factors.append({"k": "Price Action", "v": f"{pat_name} EN CONTRA ❌", "score": -8})

        # 7. Filtro de Ruido: calidad de tendencia M5 (ADX + Choppiness) — GRADUADO.
        # El backtest mostró que vetar todo ADX<20 corta rachas ganadoras en activos
        # volátiles (XAU/BTC), donde hay impulsos rentables con ADX M5 bajo. Por eso el
        # filtro premia la tendencia limpia y penaliza el ruido de forma proporcional,
        # reservando el veto duro solo para lateralidad EXTREMA (chop alto + ADX plano).
        extreme_chop = adx5 < float(prof["adx_extreme"]) and chop5 > float(prof["chop_extreme"])
        if adx5 >= float(prof["adx_clean"]) and chop5 <= float(prof["chop_clean"]):
            score += 6
            factors.append({"k": "Trend Quality", "v": f"ADX M5 {adx5:.0f} / Chop {chop5:.0f} — tendencia limpia ✅", "score": 6})
        elif adx5 >= float(prof["adx_ok"]) and chop5 <= float(prof["chop_ok"]):
            factors.append({"k": "Trend Quality", "v": f"ADX M5 {adx5:.0f} / Chop {chop5:.0f} — aceptable", "score": 0})
        elif extreme_chop:
            score -= 18
            factors.append({"k": "Trend Quality", "v": f"ADX M5 {adx5:.0f} / Chop {chop5:.0f} — LATERAL EXTREMA, cruce anulado ❌", "score": -18})
        elif prof["noise_mode"] == "on":
            pen = int(prof["noise_penalty"])
            score -= pen
            factors.append({"k": "Trend Quality", "v": f"ADX M5 {adx5:.0f} / Chop {chop5:.0f} — ruido moderado ⚠️", "score": -pen})
        else:
            # noise_mode 'soft'/'off' (p.ej. cripto): el ruido moderado no penaliza.
            factors.append({"k": "Trend Quality", "v": f"ADX M5 {adx5:.0f} / Chop {chop5:.0f} — ruido tolerado", "score": 0})

        # 7b. Filtro de ruido M1: eficiencia de tendencia (Kaufman ER) — GRADUADO, apagado por
        # defecto (ver m1_eff_mode). Complementa el filtro M5: un cruce fresco con ADX M5
        # "aceptable" puede seguir siendo whipsaw a la escala de 1 minuto en la que entra la
        # estrategia; el filtro M5 no lo detecta porque promedia a 5x esa escala.
        if prof.get("m1_eff_mode", "off") != "off":
            eff_lookback = int(prof.get("m1_eff_lookback", 20))
            m1_eff = self._trend_efficiency(close_m1, eff_lookback)
            eff_clean = float(prof.get("m1_eff_clean", 0.25))
            eff_ok = float(prof.get("m1_eff_ok", 0.10))
            if m1_eff >= eff_clean:
                score += 6
                factors.append({"k": "Eficiencia M1", "v": f"{m1_eff:.2f} — micro-tendencia limpia ✅", "score": 6})
            elif m1_eff < eff_ok:
                pen = int(prof.get("m1_eff_penalty", 10))
                score -= pen
                factors.append({"k": "Eficiencia M1", "v": f"{m1_eff:.2f} — whipsaw M1 (ruido bajo la señal) ⚠️", "score": -pen})
            else:
                factors.append({"k": "Eficiencia M1", "v": f"{m1_eff:.2f} — aceptable", "score": 0})

        # 7c. Borde de sesión — apagado por defecto (ver session_edge_mode). Solo tiene
        # efecto en grupos con boundaries definidos (hoy: EQUITIES).
        if prof.get("session_edge_mode", "off") != "off":
            buf = int(prof.get("session_edge_buffer_min", 15))
            edge_dist = self._session_edge_distance(symbol, df_m1["time"].iloc[-1])
            if edge_dist is not None:
                if edge_dist <= buf:
                    pen = int(prof.get("session_edge_penalty", 12))
                    score -= pen
                    factors.append({"k": "Borde de sesión", "v": f"{edge_dist:.0f} min de apertura/cierre — descontado ⚠️", "score": -pen})
                else:
                    factors.append({"k": "Borde de sesión", "v": "Fuera del margen de apertura/cierre", "score": 0})

        # 7d. Reapertura semanal — apagado por defecto (ver weekend_reopen_mode en
        # FILTER_DEFAULTS). Primeros minutos del lunes (hora bróker) tras el gap del
        # finde. No aplica a cripto (24/7, sin gap).
        if prof.get("weekend_reopen_mode", "off") != "off" and not is_crypto:
            bar_t = df_m1["time"].iloc[-1]
            wbuf = int(prof.get("weekend_reopen_buffer_min", 60))
            if bar_t.weekday() == 0 and (bar_t.hour * 60 + bar_t.minute) < wbuf:
                if prof.get("weekend_reopen_mode") == "block":
                    score -= 100
                    factors.append({"k": "Reapertura semanal", "v": f"Lunes {bar_t.hour:02d}:{bar_t.minute:02d}, dentro de los {wbuf} min post-gap del finde — VETADO ❌", "score": -100})
                else:
                    pen = int(prof.get("weekend_reopen_penalty", 15))
                    score -= pen
                    factors.append({"k": "Reapertura semanal", "v": f"Lunes {bar_t.hour:02d}:{bar_t.minute:02d}, dentro de los {wbuf} min post-gap del finde — descontado ⚠️", "score": -pen})

        # 8. Anti-spike: vela parabólica o impulso agotado
        last_candle_range = self._last(high_m1) - self._last(low_m1)
        move_5candles = abs(self._last(close_m1) - self._last(close_m1.iloc[:-5])) if len(close_m1) >= 6 else 0.0
        if last_candle_range > atr * 3.0:
            score -= 40
            factors.append({"k": "Anti-Spike", "v": f"Rango vela {last_candle_range:.4f} > 3x ATR — spike ❌", "score": -40})
        elif move_5candles > atr * 4.0:
            score -= 30
            factors.append({"k": "Anti-Spike (acum.)", "v": f"Movimiento 5 velas {move_5candles:.4f} > 4x ATR — impulso agotado ❌", "score": -30})

        # 9. Spread estricto
        if spread_dist > atr * 1.5:
            score -= 20
            factors.append({"k": "Spread", "v": f"CRÍTICO: {spread_dist:.5f} (>{atr*1.5:.5f}) ❌", "score": -20})
        elif spread_dist > atr * 0.8:
            score -= 10
            factors.append({"k": "Spread", "v": f"Alto: {spread_dist:.5f}", "score": -10})
        else:
            factors.append({"k": "Spread", "v": f"OK: {spread_dist:.5f}", "score": 0})

        # --- Consolidación de score + gates duros ---
        score = max(0, min(100, int(score)))
        # Gate 1: sin cruce fresco no supera el umbral (solo contexto de HUD)
        if not has_fresh_cross:
            score = min(score, 65)
        # Gate 2: solo lateralidad EXTREMA anula la entrada (el ruido moderado ya
        # penaliza vía score, sin vetar rachas ganadoras en activos volátiles).
        if extreme_chop:
            score = min(score, 65)

        entry_threshold = int(prof["entry_threshold_volatile"] if current_regime == "VOLATILE" else prof["entry_threshold"])
        entry = direction if score >= entry_threshold else 0

        # --- Estructura para SL/TP: fractales Donchian de corto plazo ---
        target_price_sl = 0.0
        tp_price = 0.0
        if entry != 0:
            n_sl, n_tp = 10, 20
            don_low = self._last(low_m1.rolling(n_sl).min(), self._last(low_m1))
            don_high = self._last(high_m1.rolling(n_sl).max(), self._last(high_m1))
            don_low20 = self._last(low_m1.rolling(n_tp).min(), don_low)
            don_high20 = self._last(high_m1.rolling(n_tp).max(), don_high)
            buf = atr * 0.20

            if entry == 1:
                # SL bajo el fractal reciente; validamos que la distancia sea razonable (0.8–3.0 ATR)
                cand_sl = don_low - buf
                sl_dist = price - cand_sl
                if atr * 0.8 <= sl_dist <= atr * 3.0:
                    target_price_sl = cand_sl
                # TP: banda VWAP superior o extremo Donchian(20), lo que dé recorrido con R:R sano
                tp_target = max(vwap_upper1, don_high20)
                min_tp = price + max(atr * 1.0, (price - (target_price_sl or cand_sl)) * 1.2)
                tp_price = max(tp_target, min_tp)
            else:
                cand_sl = don_high + buf
                sl_dist = cand_sl - price
                if atr * 0.8 <= sl_dist <= atr * 3.0:
                    target_price_sl = cand_sl
                tp_target = min(vwap_lower1, don_low20)
                min_tp = price - max(atr * 1.0, ((target_price_sl or cand_sl) - price) * 1.2)
                tp_price = min(tp_target, min_tp)

        metadata = {
            "status": (
                f"PrecisionScalping | Score {score} | "
                f"{'BUY' if direction == 1 else 'SELL'} | "
                f"ADX {adx5:.0f}/Chop {chop5:.0f} | "
                f"VWAP {vwap_val:.5f} [+1σ {vwap_upper1:.5f} / -1σ {vwap_lower1:.5f}]"
            ),
            "factors_detailed": factors,
            "vwap": vwap_val,
            "vwap_upper1": vwap_upper1,
            "vwap_lower1": vwap_lower1,
            "vwap_upper2": vwap_upper2,
            "vwap_lower2": vwap_lower2,
            "adx_m5": adx5,
            "chop_m5": chop5,
            # Umbral REAL de entrada (viene de filter_profile.entry_threshold). El orquestador
            # prioriza este valor sobre score_threshold → fuente única de verdad del gate.
            "threshold_used": entry_threshold,
            # Vela M1 (entry_tf) realmente usada en esta decisión + si hubo cruce fresco.
            # Persistidos en signal_logs para poder reproducir offline la decisión exacta:
            # sin esto, un replay histórico solo puede acercarse por reloj de pared y no
            # sabe si el bróker le sirvió al vivo la misma vela que ve el histórico.
            "bar_time": str(df_m1["time"].iloc[-1]),
            "fresh_cross": bool(has_fresh_cross),
        }
        # Exponer SL estructural al executor (valida el lado antes de aplicarlo)
        if target_price_sl > 0:
            metadata["target_price_sl"] = target_price_sl

        return {
            "score": score,
            "entry": entry,
            "atr": atr,
            "tp_price": tp_price,
            "direction": direction,
            "metadata": metadata,
        }

    # ------------------------------------------------------------------ #
    #  Salida dinámica                                                   #
    # ------------------------------------------------------------------ #
    def check_exit_signal(self, mtf_data: dict, position_type: str, symbol: str = "", **kwargs) -> bool:
        """
        Salida dinámica en dos vías (desactivable por símbolo/grupo vía filter_profile.vwap_exit):
          A) El precio cruza el VWAP de sesión en contra (con colchón ATR) Y el
             momentum EMA9/21 se ha girado en contra.
          B) Aparece un cruce fresco de EMA9/21 opuesto a la posición (giro claro).
        """
        try:
            prof = self._resolve_profile(kwargs, self._is_crypto(symbol))
            if str(prof.get("vwap_exit", "on")).lower() == "off":
                return False

            entry_tf = str(prof.get("entry_tf", "m1"))
            df_m1 = mtf_data.get(entry_tf)
            if df_m1 is None or len(df_m1) < 21:
                return False

            high_m1 = df_m1["high"]
            low_m1 = df_m1["low"]
            close_m1 = df_m1["close"]

            vol_col = "tick_volume" if "tick_volume" in df_m1.columns else ("volume" if "volume" in df_m1.columns else None)
            session_df = self._session_df(df_m1)
            s_typical = (session_df["high"] + session_df["low"] + session_df["close"]) / 3

            vwap_val = float(s_typical.iloc[-1])
            if vol_col and session_df[vol_col].sum() > 0:
                s_vol = session_df[vol_col]
                vwap_val = float((s_typical * s_vol).cumsum().iloc[-1] / s_vol.cumsum().iloc[-1])
            else:
                proxy = ta.ema(s_typical, length=20)
                if proxy is not None:
                    vwap_val = self._last(proxy, vwap_val)

            price = self._last(close_m1)
            atr_s = ta.atr(high_m1, low_m1, close_m1, length=14)
            buf = self._last(atr_s) * 0.25

            ema9 = ta.ema(close_m1, length=9)
            ema21 = ta.ema(close_m1, length=21)
            e9 = self._last(ema9)
            e9_prev = self._last(ema9.iloc[:-1])
            e21 = self._last(ema21)
            e21_prev = self._last(ema21.iloc[:-1])

            mom_down = e9 < e21
            mom_up = e9 > e21
            fresh_bear = e9_prev >= e21_prev and e9 < e21
            fresh_bull = e9_prev <= e21_prev and e9 > e21

            if position_type == "BUY":
                if price < (vwap_val - buf) and mom_down:
                    return True
                if fresh_bear:
                    return True
            if position_type == "SELL":
                if price > (vwap_val + buf) and mom_up:
                    return True
                if fresh_bull:
                    return True

            return False
        except Exception:
            return False
