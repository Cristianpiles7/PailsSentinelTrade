"""
PST · Motor de backtesting FIEL para PSTPrecisionScalping
──────────────────────────────────────────────────────────
A diferencia de PSTBacktestEngine (SL/TP fijos por ATR, sin salidas), este motor
reproduce el runtime REAL de la estrategia + executor con fidelidad "realista pragmática":

  · SL estructural (metadata.target_price_sl / Donchian) con suelo de seguridad; fallback ATR.
  · TP técnico (tp_price / banda VWAP) con auto-fix de R:R mínimo; fallback ATR por clase.
  · Cierre parcial a 1R (mueve SL a breakeven con padding de comisión).
  · Breakeven a safe_be_mult·ATR antes del parcial.
  · Salida dinámica check_exit_signal (VWAP+momentum) tras 120s de sostenimiento.
  · Timeout de 30 min por estancamiento.
  · Coste de spread real embebido en el precio de entrada.

Usa ventana acotada de lookback (simulación lineal), igual que Tools/pst_backtest_compare.py.
Resultado en múltiplos de R vía BacktestResult/BacktestTrade (mismas métricas de producción).
"""
import logging

from .engine import BacktestTrade, BacktestResult, PSTBacktestEngine

logger = logging.getLogger("PST-FaithfulBT")

try:
    from ..config import (
        SCALPER_PARTIAL_CLOSE_ENABLED, SCALPER_PARTIAL_CLOSE_PCT,
        SCALPER_PARTIAL_BE_COMMISSION_PADDING_PTS, TP_ATR_BY_CLASS,
    )
except Exception:  # ejecución fuera del paquete (carga suelta)
    SCALPER_PARTIAL_CLOSE_ENABLED = True
    SCALPER_PARTIAL_CLOSE_PCT = 0.50
    SCALPER_PARTIAL_BE_COMMISSION_PADDING_PTS = 2.0
    TP_ATR_BY_CLASS = {"CRYPTO": 4.0, "INDEX": 5.5, "METAL": 3.5, "FOREX": 6.0}


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

    def __init__(self, threshold: int = 70, lookback_m1: int = 600, lookback_m5: int = 240,
                 warmup: int = 240, model_spread: bool = True):
        self.threshold = threshold
        self.lookback_m1 = lookback_m1
        self.lookback_m5 = lookback_m5
        self.warmup = warmup
        self.model_spread = model_spread
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
        a_class = self._asset_class(symbol)
        tp_mult = TP_ATR_BY_CLASS.get(a_class, 4.0)
        spread = spread_dist if self.model_spread else 0.0
        if point is None:
            point = 10 ** -(self._infer_digits(df_m1))
        be_pad = SCALPER_PARTIAL_BE_COMMISSION_PADDING_PTS * point

        period_start = df_m1.iloc[self.warmup]["time"]
        period_end = df_m1.iloc[-1]["time"]
        res = BacktestResult("PST-PrecisionScalping", symbol, period_start, period_end)

        pos = None          # posición abierta
        n = len(df_m1)
        i = self.warmup
        while i < n:
            bar = df_m1.iloc[i]
            if pos is not None:
                closed = self._manage(pos, bar, df_m1, df_m5, t_m5, i, strategy, symbol, profile)
                if closed:
                    res.trades.append(pos)
                    pos = None
                i += 1
                continue

            # --- flat: evaluar entrada ---
            m1_slice = df_m1.iloc[max(0, i + 1 - self.lookback_m1): i + 1]
            cur_time = bar["time"]
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

            pos = self._open(sig, bar, symbol, atr, entry, tp_mult, spread, be_pad, profile)
            i += 1

        if pos is not None:  # abierto al final del periodo
            last_close = float(df_m1.iloc[-1]["close"])
            self._close_remaining(pos, last_close, period_end, "EOD")
            pos.result = "OPEN"
            res.trades.append(pos)

        return res

    # ------------------------------------------------------------------ apertura
    def _open(self, sig, bar, symbol, atr, entry, tp_mult, spread, be_pad, profile):
        direction = int(entry)
        raw = float(bar["close"])
        fill = raw + direction * spread          # pagar spread en la entrada
        meta = sig.get("metadata", {}) or {}

        # SL: estructural si viene y valida lado; si no, ATR
        sl = 0.0
        struct_sl = meta.get("target_price_sl", 0) or 0
        if struct_sl > 0 and ((direction == 1 and struct_sl < fill) or (direction == -1 and struct_sl > fill)):
            sl = struct_sl
        if sl == 0:
            sl_mult = float(profile.get("sl_mult") or self.SL_MULT)
            sl = fill - direction * sl_mult * atr
        # suelo de seguridad
        if abs(fill - sl) < fill * self.MIN_SL_PCT:
            sl = fill - direction * fill * self.MIN_SL_PCT

        # TP: técnico si viene y valida lado; si no, ATR por clase
        tp = 0.0
        tp_sig = sig.get("tp_price", 0) or 0
        if tp_sig > 0 and ((direction == 1 and tp_sig > fill) or (direction == -1 and tp_sig < fill)):
            tp = tp_sig
        if tp == 0:
            tp = fill + direction * tp_mult * atr

        # auto-fix R:R mínimo (estirar TP) — min_rr por símbolo o default (seed 1.8)
        min_rr = float(profile.get("min_rr") or self.MIN_RR)
        sl_dist = abs(fill - sl)
        if sl_dist > 0 and abs(tp - fill) / sl_dist < min_rr:
            tp = fill + direction * sl_dist * min_rr

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
        # BE por símbolo: executor usa max(1.5, be_mult) para scalpers
        t._be_mult = max(1.5, float(profile.get("be_mult") or self.SAFE_BE_MULT))
        return t

    # ------------------------------------------------------------------ gestión
    def _manage(self, t, bar, df_m1, df_m5, t_m5, j, strategy, symbol, profile) -> bool:
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
        if SCALPER_PARTIAL_CLOSE_ENABLED and not t._partial and R > 0:
            reached_1r = (h >= fill + R) if d == 1 else (l <= fill - R)
            if reached_1r:
                pct = SCALPER_PARTIAL_CLOSE_PCT
                t._realized += pct * 1.0            # media parte a +1R
                t._rem -= pct
                t._partial = True
                t._be = True
                t.sl_price = fill + d * t._be_pad   # SL a breakeven con padding
                t.exit_reason = "PARTIAL_1R"

        # 3) Breakeven a be_mult·ATR (si aún no hubo parcial/BE)
        if not t._be and t.atr > 0:
            be_atr = getattr(t, "_be_mult", self.SAFE_BE_MULT)
            reached_be = (h >= fill + be_atr * t.atr) if d == 1 else (l <= fill - be_atr * t.atr)
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
        """Cierra la fracción remanente a 'price' y finaliza el trade."""
        d, fill, R = t.direction, t.entry_price, t._R
        r_leg = (d * (price - fill) / R) if R > 0 else 0.0
        t._realized += t._rem * r_leg
        t._rem = 0.0
        t.exit_price = price
        t.exit_time = time
        t.pnl_r = t._realized
        if not t.exit_reason or reason != "EOD":
            t.exit_reason = reason
        t.result = "WIN" if t.pnl_r > 0 else "LOSS"

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
