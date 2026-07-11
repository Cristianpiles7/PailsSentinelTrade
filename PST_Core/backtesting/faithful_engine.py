"""
PST · Motor de backtesting FIEL para PSTPrecisionScalping
──────────────────────────────────────────────────────────
A diferencia de PSTBacktestEngine (SL/TP fijos por ATR, sin salidas), este motor
reproduce el runtime REAL de la estrategia + executor con fidelidad "realista pragmática":

  · SL con la MISMA secuencia que el executor y dimensionado con ATR de M5 (no de M1):
    swing 15 velas M5 (banda 1.5-2.4·ATR_M5) → override target_price_sl estrategia →
    fallback ATR → suelo 0.08% → auto-fix R:R (ciñe SL o estira TP). Fiel a producción.
  · TP por grupo (v2.5.7), espejo del executor: tp_price técnico (VWAP/Donchian) en TODO
    menos ÍNDICES; en índices, ATR-based (atr_unit·tp_m·ajuste_vol M5). Validado A/B: el
    técnico mejora forex/metal/cripto (+0.07..+0.10R) pero corta a los índices. Auto-fix R:R.
  · Cierre parcial a 1R (mueve SL a breakeven con padding de comisión).
  · Breakeven a safe_be_mult·ATR antes del parcial.
  · Salida dinámica check_exit_signal (VWAP+momentum) tras 120s de sostenimiento.
  · Timeout de 30 min por estancamiento.
  · Coste de spread real: medio spread en la ENTRADA + medio en la SALIDA (round-trip
    completo, sin doble conteo). Antes se cobraba entero en la entrada — mismo neto, pero
    ahora el coste de salida queda explícito.
  · Comisión del broker descontada de cada trade en R (config COMMISSION_SPEC, calibrada
    con datos reales). Dos modelos: per_lot (forex/metal/acción, vía tick_value/tick_size) y
    pct_notional (cripto, ~0.065% → ~0.39R/trade, DOMINA el edge). Índices: sin comisión.

Usa ventana acotada de lookback (simulación lineal), igual que Tools/pst_backtest_compare.py.
Resultado en múltiplos de R vía BacktestResult/BacktestTrade (mismas métricas de producción).
"""
import logging
import pandas as pd

from .engine import BacktestTrade, BacktestResult, PSTBacktestEngine

logger = logging.getLogger("PST-FaithfulBT")

try:
    from ..config import (
        SCALPER_PARTIAL_CLOSE_ENABLED, SCALPER_PARTIAL_CLOSE_PCT,
        SCALPER_PARTIAL_BE_COMMISSION_PADDING_PTS, TP_ATR_BY_CLASS,
        COMMISSION_SPEC, CRYPTO_MAX_COMMISSION_R, SCALPER_PARTIAL_CLOSE_DISABLED_CLASSES,
    )
except Exception:  # ejecución fuera del paquete (carga suelta)
    SCALPER_PARTIAL_CLOSE_ENABLED = True
    SCALPER_PARTIAL_CLOSE_PCT = 0.50
    SCALPER_PARTIAL_BE_COMMISSION_PADDING_PTS = 2.0
    TP_ATR_BY_CLASS = {"CRYPTO": 4.0, "INDEX": 5.5, "METAL": 3.5, "FOREX": 6.0}
    COMMISSION_SPEC = {
        "FOREX": {"per_lot": 4.3}, "METAL": {"per_lot": 5.5}, "COMMODITY": {"per_lot": 5.5},
        "CRYPTO": {"pct_notional": 0.00065}, "INDEX": {"per_lot": 0.0}, "EQUITIES": {"per_lot": 0.011},
    }
    CRYPTO_MAX_COMMISSION_R = 0.25
    SCALPER_PARTIAL_CLOSE_DISABLED_CLASSES = {"METAL"}


def _commission_r(spec, entry_price, sl_dist, value_per_price):
    """Comisión round-turn del trade expresada en R (múltiplos de riesgo).

    · per_lot (forex/metal/acción): comm_R = per_lot / (R_price · valor_por_precio_por_lote).
      El riesgo en dinero se cancela (comm_money/M = per_lot/(R·vpp)); necesita tick data.
    · pct_notional (cripto): comm_R = pct · precio / R_price. El nocional y el lote se
      cancelan → NO necesita tick data, solo precio y distancia de SL.
    """
    if not spec or sl_dist <= 0:
        return 0.0
    if "pct_notional" in spec:
        pct = float(spec["pct_notional"])
        return pct * entry_price / sl_dist if pct > 0 else 0.0
    per_lot = float(spec.get("per_lot", 0.0))
    if per_lot > 0 and value_per_price:
        return per_lot / (sl_dist * value_per_price)
    return 0.0


class FaithfulScalpingEngine:
    # Defaults alineados con el seed de PrecisionScalping (symbol_strategies) para que el
    # backtest refleje lo que corre en real. Sobreescribibles por símbolo vía el profile
    # (sl_mult / min_rr / be_mult), igual que en la BBDD.
    SL_MULT = 1.6            # SL scalper por ATR (fallback si no hay Donchian) — seed sl_mult
    MIN_RR = 1.8             # R:R mínimo (auto-fix) — seed min_rr, executor scalping = max(min_rr,1.2)
    MIN_SL_PCT = 0.0008      # suelo de SL (0.08% del precio) — espejo executor
    SAFE_BE_MULT = 3.5       # BE a 3.5·ATR (scalper) — executor safe_be = max(1.5, be_mult=3.5)
    HOLD_SECS = 120          # sostenimiento mínimo antes de salida VWAP
    TIMEOUT_SECS = 30 * 60   # cierre por estancamiento

    # lookback_m1 = 200 para COINCIDIR con lo que el bot ve en vivo: get_mtf_data_async
    # descarga exactamente 200 barras M1 (fetch_rates_async(sym, 1, 200)). El VWAP de sesión
    # se ancla, por tanto, como máximo 200 barras atrás (no desde la apertura real del día).
    # Usar 600 anclaba el VWAP mucho más atrás → señales distintas y peores que en real
    # (validado: GBPUSD +0.011R@600 vs +0.314R@200 con el MISMO perfil). 200 = fiel a producción.
    def __init__(self, threshold: int = 70, lookback_m1: int = 200, lookback_m5: int = 240,
                 warmup: int = 240, model_spread: bool = True, use_technical_tp=None,
                 cooldown_mins: int = 15):
        self.threshold = threshold
        self.lookback_m1 = lookback_m1
        self.lookback_m5 = lookback_m5
        self.warmup = warmup
        self.model_spread = model_spread
        # Cooldown tras pérdida (v2.6.9): el motor NO modelaba el bloqueo de
        # loss_cooldown_minutes (cooldown_manager.py, default 15) que el bot real aplica
        # por símbolo tras cada SL — reevaluaba entrada en la siguiente barra M1 sin
        # esperar. Con WR~45% (más de la mitad de los trades disparan cooldown), esto
        # infla el ritmo de trades/día del juez muy por encima de lo real: medido en vivo
        # vs backtest, XAUUSD 4.4x, GBPUSD 8x, GER40 1.75x, US500 2.7x más rápido el juez.
        self.cooldown_secs = cooldown_mins * 60
        # TP: None (default) = AUTO por grupo, espejo del executor tras v2.5.7 — usa el tp_price
        # técnico (VWAP/Donchian) en TODO menos ÍNDICES (validado A/B: mejora forex/metal/cripto
        # +0.07..+0.10R, pero empeora índices, que prefieren correr → ATR). True/False fuerzan.
        self.use_technical_tp = use_technical_tp
        self._eng = PSTBacktestEngine(score_threshold=threshold)

    def _asset_class(self, symbol):
        return self._eng._get_asset_class(symbol)

    async def run(self, strategy, symbol, data, profile=None, point=None,
                  spread_dist=0.0) -> BacktestResult:
        """
        strategy     : instancia de PSTPrecisionScalping
        data         : {"m1": df, "m5": df} con columna 'time'
        profile      : dict de params por símbolo → kwargs a calculate_signal
        point        : tamaño de punto del símbolo (para padding BE); si None se estima
        spread_dist  : coste de spread en precio (round-trip embebido en la entrada)
        """
        profile = profile or {}
        df_m1, df_m5 = data["m1"], data["m5"]
        t_m5 = df_m5["time"].values
        # ATR(14) M5 vectorizado para toda la serie — v2.6.9: el breakeven standalone
        # (_manage paso 3) usaba t.atr, el ATR M1 CONGELADO al abrir el trade. En vivo,
        # manage_active_trades recalcula el ATR M5 en CADA ciclo de 10s (fetch_rates_async
        # M5 fresco) — con ATR_M5 ≈ 2-3× ATR_M1 (ver sl_backtest_vs_live_m1_m5), el juez
        # disparaba el BE con un movimiento 2-3x menor del necesario en la realidad,
        # "protegiendo" trades mucho antes de lo que ocurre en vivo. Espejo de
        # FaithfulRangeEngine, que ya recalculaba esto correctamente por barra.
        import pandas_ta as _pta
        atr_m5_mgmt_series = _pta.atr(df_m5["high"], df_m5["low"], df_m5["close"], length=14)
        a_class = self._asset_class(symbol)
        tp_mult = TP_ATR_BY_CLASS.get(a_class, 4.0)
        spread = spread_dist if self.model_spread else 0.0
        half_spread = spread / 2.0            # medio en entrada, medio en salida
        if point is None:
            point = 10 ** -(self._infer_digits(df_m1))
        be_pad = SCALPER_PARTIAL_BE_COMMISSION_PADDING_PTS * point

        # Comisión → R (ver _commission_r). El modelo per_lot necesita tick_value/tick_size;
        # el pct_notional (cripto) no. Aviso si hace falta tick data y no está (caché viejo).
        comm_spec = COMMISSION_SPEC.get(a_class, {})
        tick_value = data.get("tick_value")
        tick_size = data.get("tick_size")
        value_per_price = (tick_value / tick_size) if (tick_value and tick_size) else None
        if comm_spec.get("per_lot", 0.0) > 0 and value_per_price is None:
            logger.warning("%s: comisión per_lot configurada pero sin tick_value/tick_size "
                           "(caché viejo) → comisión NO aplicada. Regenerar con --refresh.", symbol)

        period_start = df_m1.iloc[self.warmup]["time"]
        period_end = df_m1.iloc[-1]["time"]
        res = BacktestResult("PST-PrecisionScalping", symbol, period_start, period_end)

        pos = None          # posición abierta
        cooldown_until = None
        n = len(df_m1)
        i = self.warmup
        while i < n:
            bar = df_m1.iloc[i]
            if pos is not None:
                closed = self._manage(pos, bar, df_m1, df_m5, t_m5, i, strategy, symbol, profile,
                                       atr_m5_mgmt_series)
                if closed:
                    if pos.pnl_r < 0 and self.cooldown_secs > 0:
                        cooldown_until = bar["time"] + pd.Timedelta(seconds=self.cooldown_secs)
                    res.trades.append(pos)
                    pos = None
                i += 1
                continue

            # --- flat: evaluar entrada ---
            cur_time = bar["time"]
            if cooldown_until is not None and cur_time < cooldown_until:
                i += 1
                continue
            m1_slice = df_m1.iloc[max(0, i + 1 - self.lookback_m1): i + 1]
            end5 = int((t_m5 <= cur_time.to_datetime64()).sum())
            if end5 < 30:
                i += 1
                continue
            m5_slice = df_m5.iloc[max(0, end5 - self.lookback_m5): end5]

            sig = await strategy.calculate_signal({"m1": m1_slice, "m5": m5_slice},
                                                  symbol=symbol, **profile)
            score, entry, atr = sig.get("score", 0), sig.get("entry", 0), sig.get("atr", 0.0)
            if score < self.threshold or entry == 0 or atr <= 0:
                i += 1
                continue

            # ATR(14) M5 + media 20 + swing 15 velas M5 → el executor dimensiona SL y TP con
            # ESTO (M5), no con el ATR de M1 de la estrategia. Corrige el bug de fidelidad.
            atr_m5, ma_atr_m5, m5_low15, m5_high15 = self._m5_sl_inputs(m5_slice)

            pos = self._open(sig, bar, symbol, atr, entry, tp_mult, half_spread, be_pad,
                             profile, comm_spec, value_per_price, atr_m5, ma_atr_m5, m5_low15, m5_high15)
            i += 1

        if pos is not None:  # abierto al final del periodo
            last_close = float(df_m1.iloc[-1]["close"])
            self._close_remaining(pos, last_close, period_end, "EOD")
            pos.result = "OPEN"
            res.trades.append(pos)

        return res

    # ------------------------------------------------------------------ apertura
    def _open(self, sig, bar, symbol, atr, entry, tp_mult, half_spread, be_pad, profile,
              comm_spec=None, value_per_price=None, atr_m5=0.0, ma_atr_m5=0.0,
              m5_low15=None, m5_high15=None):
        direction = int(entry)
        raw = float(bar["close"])
        fill = raw + direction * half_spread     # medio spread en la entrada (mitad round-trip)
        meta = sig.get("metadata", {}) or {}
        sl_m = float(profile.get("sl_mult") or self.SL_MULT)   # 1.6 (seed)
        # FIDELIDAD: el executor dimensiona SL y TP con ATR de M5, no con el de M1 de la
        # estrategia (M5_ATR ≈ 2-3× M1_ATR). Reproducimos su secuencia y su atr_unit.
        a5 = atr_m5 if (atr_m5 and atr_m5 > 0) else atr
        # atr_unit del executor = max(ATR_M1·orch_sl_mult, 0.15%·precio)/2.5 (el orquestador
        # suelo la distancia a 0.15% antes de pasarla). Alimenta el fallback de SL y el TP.
        orch_sl_mult = 3.5 if (len(symbol) > 3 or "500" in symbol or "30" in symbol) else 2.5
        atr_unit = max(atr * orch_sl_mult, fill * 0.0015) / 2.5

        # --- SL: réplica de execute_trade (mismo orden de prioridad) ---
        sl = 0.0
        # 1) SL estructural: swing de 15 velas M5, banda 1.5 ≤ dist/ATR_M5 ≤ sl_m·1.5
        if a5 > 0:
            if direction == 1 and m5_low15 is not None:
                cand = m5_low15 - 0.2 * a5
                if 1.5 <= (fill - cand) / a5 <= sl_m * 1.5:
                    sl = cand
            elif direction == -1 and m5_high15 is not None:
                cand = m5_high15 + 0.2 * a5
                if 1.5 <= (cand - fill) / a5 <= sl_m * 1.5:
                    sl = cand
        # 2) Override: SL estructural de la estrategia (M1-Donchian) si viene y valida lado
        struct_sl = meta.get("target_price_sl", 0) or 0
        if struct_sl > 0 and ((direction == 1 and struct_sl < fill) or (direction == -1 and struct_sl > fill)):
            sl = struct_sl
        # 3) Fallback: real_sl_atr del executor = atr_unit·sl_m
        if sl == 0:
            sl = fill - direction * atr_unit * sl_m
        # 4) Suelo de seguridad 0.08%
        if abs(fill - sl) < fill * self.MIN_SL_PCT:
            sl = fill - direction * fill * self.MIN_SL_PCT

        # --- TP: por defecto réplica FIEL de execute_trade — ATR-based. El executor IGNORA el
        # tp_price técnico de la estrategia (su metadata no expone target_price_tp/tp_target) y
        # usa real_tp_atr = atr_unit·tp_m·ajuste_vol(M5). tp_m = seed _SCALP_BASE 2.5.
        # use_technical_tp None = AUTO (técnico salvo índices); True/False fuerzan.
        if self.use_technical_tp is None:
            use_tech = self._asset_class(symbol) != "INDEX"
        else:
            use_tech = bool(self.use_technical_tp)
        tp = 0.0
        if use_tech:
            tp_sig = sig.get("tp_price", 0) or 0
            if tp_sig > 0 and ((direction == 1 and tp_sig > fill) or (direction == -1 and tp_sig < fill)):
                tp = tp_sig
        if tp == 0:
            tp_m = float(profile.get("tp_mult") or 2.5)
            vol_ratio = (a5 / ma_atr_m5) if ma_atr_m5 > 0 else 1.0
            if vol_ratio > 1.2:
                tp_adj = min(1.5, vol_ratio)
            elif vol_ratio < 0.8:
                tp_adj = max(0.7, vol_ratio)
            else:
                tp_adj = 1.0
            tp = fill + direction * atr_unit * tp_m * tp_adj

        # auto-fix R:R (igual que el executor): 1º ceñir el SL hasta el suelo
        # max(1.0·ATR_M5, 0.08% del precio) — v2.6.9: el executor añadió el suelo de
        # precio al encogimiento (antes solo ATR M5, y en baja volatilidad generaba
        # SLs dentro del spread); si no cabe, estirar el TP. min_rr por símbolo o seed 1.8.
        min_rr = float(profile.get("min_rr") or self.MIN_RR)
        sl_dist = abs(fill - sl)
        tp_dist = abs(tp - fill)
        if sl_dist > 0 and tp_dist / sl_dist < min_rr:
            ideal_sl_dist = tp_dist / min_rr
            if ideal_sl_dist >= max(1.0 * a5, fill * self.MIN_SL_PCT):
                sl = fill - direction * ideal_sl_dist       # ceñir SL
            else:
                tp = fill + direction * sl_dist * min_rr    # estirar TP
        sl_dist = abs(fill - sl)                            # R final tras el auto-fix

        # COMMISSION GUARD (v2.6.2): espejo del executor — no abrir si la comisión proyectada
        # (cripto, pct_notional) supera CRYPTO_MAX_COMMISSION_R del riesgo del trade.
        if comm_spec and "pct_notional" in comm_spec and sl_dist > 0:
            proj_commission_r = float(comm_spec["pct_notional"]) * fill / sl_dist
            if proj_commission_r > CRYPTO_MAX_COMMISSION_R:
                return None

        t = BacktestTrade(
            entry_time=bar["time"], exit_time=None, symbol=symbol, strategy="PST-PrecisionScalping",
            direction=direction, entry_price=fill, sl_price=sl, tp_price=tp,
            score=abs(sig.get("score", 0)), atr=atr,
        )
        # estado extendido para gestión intrabar
        t._R = sl_dist
        t._rem = 1.0
        t._realized = 0.0
        t._partial = False
        t._be = False
        t._be_pad = be_pad
        t._half_spread = half_spread            # medio spread a cobrar también en la salida
        # Comisión en R (round-turn completa, cobrada una vez al cierre final).
        t._commission_r = _commission_r(comm_spec, fill, sl_dist, value_per_price)
        # BE por símbolo: executor usa max(1.5, be_mult) para scalpers
        t._be_mult = max(1.5, float(profile.get("be_mult") or self.SAFE_BE_MULT))
        return t

    # ------------------------------------------------------------------ gestión
    def _manage(self, t, bar, df_m1, df_m5, t_m5, j, strategy, symbol, profile,
                atr_m5_mgmt_series=None) -> bool:
        """Gestiona la posición en la barra j. Devuelve True si se cerró del todo."""
        h, l, c = float(bar["high"]), float(bar["low"]), float(bar["close"])
        d, fill, R = t.direction, t.entry_price, t._R
        age = (bar["time"] - t.entry_time).total_seconds()

        # 1) SL / TP intrabar (SL antes que TP, conservador)
        if d == 1:
            if l <= t.sl_price:
                self._close_remaining(t, t.sl_price, bar["time"], "SL"); return True
            if h >= t.tp_price:
                self._close_remaining(t, t.tp_price, bar["time"], "TP"); return True
        else:
            if h >= t.sl_price:
                self._close_remaining(t, t.sl_price, bar["time"], "SL"); return True
            if l <= t.tp_price:
                self._close_remaining(t, t.tp_price, bar["time"], "TP"); return True

        # 2) Cierre parcial a 1R (extremo favorable alcanzado) → BE
        partial_ok = SCALPER_PARTIAL_CLOSE_ENABLED and self._asset_class(symbol) not in SCALPER_PARTIAL_CLOSE_DISABLED_CLASSES
        if partial_ok and not t._partial and R > 0:
            reached_1r = (h >= fill + R) if d == 1 else (l <= fill - R)
            if reached_1r:
                pct = SCALPER_PARTIAL_CLOSE_PCT
                # media parte a +1R, menos el medio spread de salida (round-trip)
                hs = getattr(t, "_half_spread", 0.0)
                t._realized += pct * ((R - hs) / R)
                t._rem -= pct
                t._partial = True
                t._be = True
                t.sl_price = fill + d * t._be_pad   # SL a breakeven con padding
                t.exit_reason = "PARTIAL_1R"

        # 3) Breakeven a be_mult·ATR (si aún no hubo parcial/BE) — ATR M5 FRESCO (última
        # vela M5 completada a esta altura), no el ATR M1 congelado de la entrada. Espejo
        # de manage_active_trades, que recalcula el ATR M5 en cada ciclo de 10s.
        atr_fresh = t.atr
        if atr_m5_mgmt_series is not None:
            end5_be = int((t_m5 <= bar["time"].to_datetime64()).sum())
            if end5_be >= 15:
                v = float(atr_m5_mgmt_series.iloc[end5_be - 1])
                if v == v:  # no NaN
                    atr_fresh = v
        if not t._be and atr_fresh > 0:
            be_atr = getattr(t, "_be_mult", self.SAFE_BE_MULT)
            reached_be = (h >= fill + be_atr * atr_fresh) if d == 1 else (l <= fill - be_atr * atr_fresh)
            if reached_be:
                t.sl_price = fill + d * t._be_pad
                t._be = True

        # 4) Salida dinámica VWAP+momentum tras sostenimiento mínimo
        if age >= self.HOLD_SECS:
            m1s = df_m1.iloc[max(0, j + 1 - self.lookback_m1): j + 1]
            end5 = int((t_m5 <= bar["time"].to_datetime64()).sum())
            m5s = df_m5.iloc[max(0, end5 - self.lookback_m5): end5]
            pos_type = "BUY" if d == 1 else "SELL"
            try:
                # symbol + filter_profile: igual que el executor real, para que vwap_exit
                # on/off por símbolo/grupo se respete también en el backtest.
                if strategy.check_exit_signal({"m1": m1s, "m5": m5s}, pos_type,
                                              symbol=symbol, filter_profile=profile.get("filter_profile")):
                    self._close_remaining(t, c, bar["time"], "VWAP_EXIT"); return True
            except Exception:
                pass

        # 5) Timeout por estancamiento
        if age > self.TIMEOUT_SECS:
            self._close_remaining(t, c, bar["time"], "TIMEOUT"); return True

        return False

    def _close_remaining(self, t, price, time, reason):
        """Cierra la fracción remanente a 'price' y finaliza el trade.

        Aplica el medio spread de salida (cruzas el spread al salir) y descuenta la
        comisión round-turn una sola vez, sobre el total del trade.
        """
        d, fill, R = t.direction, t.entry_price, t._R
        hs = getattr(t, "_half_spread", 0.0)
        eff = price - d * hs                          # peor precio al cruzar el spread de salida
        r_leg = (d * (eff - fill) / R) if R > 0 else 0.0
        t._realized += t._rem * r_leg
        t._realized -= getattr(t, "_commission_r", 0.0)   # comisión round-turn (una vez)
        t._rem = 0.0
        t.exit_price = price
        t.exit_time = time
        t.pnl_r = t._realized
        if not t.exit_reason or reason != "EOD":
            t.exit_reason = reason
        t.result = "WIN" if t.pnl_r > 0 else "LOSS"

    @staticmethod
    def _m5_sl_inputs(m5_slice):
        """ATR(14) M5, su media móvil de 20 (para el ajuste de volatilidad del TP) y el
        swing low/high de las últimas 15 velas M5 — lo que el executor usa para SL y TP.
        Devuelve (atr_m5, ma_atr_m5, low15, high15)."""
        atr_m5 = ma_atr_m5 = 0.0
        try:
            import pandas_ta as pta
            s = pta.atr(m5_slice["high"], m5_slice["low"], m5_slice["close"], length=14)
            if s is not None and len(s):
                v = float(s.iloc[-1]); atr_m5 = v if v == v else 0.0
                mv = float(s.rolling(20).mean().iloc[-1]); ma_atr_m5 = mv if mv == mv else atr_m5
        except Exception:
            pass
        low15 = float(m5_slice["low"].tail(15).min()) if len(m5_slice) else None
        high15 = float(m5_slice["high"].tail(15).max()) if len(m5_slice) else None
        return atr_m5, ma_atr_m5, low15, high15

    @staticmethod
    def _infer_digits(df_m1):
        """Estima los decimales del símbolo a partir de los precios (para el padding BE)."""
        try:
            px = float(df_m1["close"].iloc[-1])
            if px >= 1000:
                return 2
            if px >= 100:
                return 2
            if px >= 10:
                return 3
            return 5
        except Exception:
            return 5
