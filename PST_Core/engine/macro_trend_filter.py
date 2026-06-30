"""
PST Macro Trend Filter
Analiza H1 y H4 para determinar la tendencia macro del mercado.
Bloquea entradas contrarias a la tendencia dominante — el filtro más simple y efectivo.

Lógica:
  - BULL  → solo permite señales BUY   (o neutraliza SELL con penalización)
  - BEAR  → solo permite señales SELL  (o neutraliza BUY con penalización)
  - NEUTRAL → permite ambas direcciones sin penalización
"""
import logging
import pandas as pd
import pandas_ta as ta

logger = logging.getLogger("PST-MacroFilter")

# Ajuste por tipo de estrategia: las de scalping reciben un filtro más suave
_FILTER_STRENGTH = {
    "TREND":   1.0,   # AlphaTrend — filtro completo (veto si contra tendencia)
    "RANGE":   0.7,   # RangeBreaker — filtro moderado (penalización, no veto)
    "SCALPING": 0.5,  # PrecisionScalping — filtro suave (H1 solo, sin H4)
}


class MacroTrendResult:
    def __init__(self, direction: int, strength: str, reason: str, h1_bias: int, h4_bias: int):
        self.direction = direction    # 1=BULL, -1=BEAR, 0=NEUTRAL
        self.strength  = strength    # "STRONG", "MODERATE", "WEAK", "NEUTRAL"
        self.reason    = reason
        self.h1_bias   = h1_bias    # 1, -1 o 0
        self.h4_bias   = h4_bias    # 1, -1 o 0

    def allows_direction(self, signal_dir: int, strategy_type: str = "TREND") -> tuple[bool, int]:
        """
        Comprueba si la dirección de la señal es compatible con la tendencia macro.

        Returns:
            (allowed: bool, score_adjustment: int)
            score_adjustment es negativo si hay conflicto
        """
        if self.direction == 0:
            return True, 0  # NEUTRAL: no filtrar

        strength = _FILTER_STRENGTH.get(strategy_type, 1.0)

        if self.direction == signal_dir:
            # A favor de la tendencia macro — bonus
            bonus = int(15 * strength)
            return True, bonus
        else:
            # Contra tendencia macro
            if strategy_type == "TREND":
                # Veto total para estrategias de tendencia
                return False, -100
            elif strategy_type == "RANGE":
                # Penalización fuerte pero no veto (puede haber reversión en rango)
                if self.strength == "STRONG":
                    return False, -100
                return True, int(-30 * strength)
            else:
                # SCALPING: solo penalización moderada
                return True, int(-20 * strength)


class MacroTrendFilter:
    """
    Evalúa la tendencia macro usando EMAs en H1 y H4.

    Criterios:
      H1: EMA21 vs EMA50 vs EMA200 + Supertrend + ADX
      H4: EMA21 vs EMA50 (confirmación macro)

    El resultado final requiere acuerdo entre H1 y H4 para marcar BULL/BEAR STRONG.
    Si solo H1 tiene señal clara, es MODERATE. Si discrepan, es NEUTRAL.
    """

    def analyze(self, mtf_data: dict) -> MacroTrendResult:
        """
        Analiza MTF y devuelve el estado de la tendencia macro.
        Nunca lanza excepción — devuelve NEUTRAL si faltan datos.
        """
        try:
            h1_bias  = self._analyze_h1(mtf_data.get("h1"))
            h4_bias  = self._analyze_h4(mtf_data.get("h4"))
            return self._combine(h1_bias, h4_bias)
        except Exception as e:
            logger.debug(f"[MacroFilter] Error en análisis: {e}")
            return MacroTrendResult(0, "NEUTRAL", f"Error: {e}", 0, 0)

    def _analyze_h1(self, df: pd.DataFrame) -> int:
        """Retorna 1 (BULL), -1 (BEAR) o 0 (NEUTRAL) para H1."""
        if df is None or len(df) < 55:
            return 0

        close = df["close"]
        high  = df["high"]
        low   = df["low"]

        ema21 = ta.ema(close, length=21)
        ema50 = ta.ema(close, length=50)
        adx_df = ta.adx(high, low, close, length=14)

        if ema21 is None or ema50 is None or adx_df is None:
            return 0

        e21 = float(ema21.iloc[-1])
        e50 = float(ema50.iloc[-1])
        adx = float(adx_df["ADX_14"].iloc[-1])
        dmp = float(adx_df["DMP_14"].iloc[-1])
        dmn = float(adx_df["DMN_14"].iloc[-1])
        price = float(close.iloc[-1])

        # Supertrend para confirmación adicional
        st_bias = 0
        try:
            st_df = ta.supertrend(high, low, close, length=10, multiplier=3.0)
            if st_df is not None:
                st_dir_col = [c for c in st_df.columns if "SUPERTd" in c]
                if st_dir_col:
                    st_bias = int(st_df[st_dir_col[0]].iloc[-1])
        except Exception:
            pass

        votes_bull = 0
        votes_bear = 0

        # Voto 1: EMA 21 vs 50
        if e21 > e50:
            votes_bull += 1
        elif e21 < e50:
            votes_bear += 1

        # Voto 2: Precio vs EMA21
        if price > e21:
            votes_bull += 1
        elif price < e21:
            votes_bear += 1

        # Voto 3: ADX + DI± (solo si hay tendencia activa ADX > 20)
        if adx > 20:
            if dmp > dmn:
                votes_bull += 1
            elif dmn > dmp:
                votes_bear += 1

        # Voto 4: Supertrend
        if st_bias == 1:
            votes_bull += 1
        elif st_bias == -1:
            votes_bear += 1

        # Necesita mayoría de 3+ votos para definir dirección
        if votes_bull >= 3:
            return 1
        if votes_bear >= 3:
            return -1
        return 0

    def _analyze_h4(self, df: pd.DataFrame) -> int:
        """Retorna 1 (BULL), -1 (BEAR) o 0 (NEUTRAL) para H4."""
        if df is None or len(df) < 55:
            return 0

        close = df["close"]
        ema21 = ta.ema(close, length=21)
        ema50 = ta.ema(close, length=50)

        if ema21 is None or ema50 is None:
            return 0

        e21 = float(ema21.iloc[-1])
        e50 = float(ema50.iloc[-1])
        price = float(close.iloc[-1])

        bull_signals = (e21 > e50) + (price > e21)
        bear_signals = (e21 < e50) + (price < e21)

        if bull_signals == 2:
            return 1
        if bear_signals == 2:
            return -1
        return 0

    def _combine(self, h1_bias: int, h4_bias: int) -> MacroTrendResult:
        """Combina los sesgos H1 y H4 en un resultado unificado."""
        if h1_bias == 0 and h4_bias == 0:
            return MacroTrendResult(0, "NEUTRAL", "H1 y H4 sin dirección clara", h1_bias, h4_bias)

        if h1_bias == h4_bias and h1_bias != 0:
            label = "BULL" if h1_bias == 1 else "BEAR"
            return MacroTrendResult(
                h1_bias, "STRONG",
                f"H1 {label} + H4 {label} — Tendencia macro confirmada",
                h1_bias, h4_bias,
            )

        if h1_bias != 0 and h4_bias == 0:
            label = "BULL" if h1_bias == 1 else "BEAR"
            return MacroTrendResult(
                h1_bias, "MODERATE",
                f"H1 {label} — H4 neutral (tendencia moderada)",
                h1_bias, h4_bias,
            )

        if h4_bias != 0 and h1_bias == 0:
            label = "BULL" if h4_bias == 1 else "BEAR"
            return MacroTrendResult(
                h4_bias, "WEAK",
                f"H4 {label} — H1 neutral (solo macro, sin confirmación H1)",
                h1_bias, h4_bias,
            )

        # H1 y H4 discrepan → NEUTRAL (no operar contra viento y marea)
        return MacroTrendResult(
            0, "NEUTRAL",
            f"H1 {'BULL' if h1_bias == 1 else 'BEAR'} vs H4 {'BULL' if h4_bias == 1 else 'BEAR'} — Conflicto macro",
            h1_bias, h4_bias,
        )
