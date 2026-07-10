"""
PST · Motor de backtesting FIEL para PST-RangeBreaker
──────────────────────────────────────────────────────
Réplica del pipeline REAL orquestador→executor→gestión para la estrategia de
reversión a la media (Bollinger M15). Verificado contra el código v2.6.1 y los
logs/BBDD de producción (2026-07-06..08):

  ENTRADA (por barra M1, con velas EN FORMACIÓN como ve el bot cada 10s):
  · Señal PSTRangeBreaker sobre M15+H1 sintetizados as-of (forming bar incluida).
  · Filtro macro (MacroTrendFilter real): contra STRONG → veto; contra
    MODERATE/WEAK → −21 pts; a favor → +10 pts (strength RANGE=0.7).
  · Gate de régimen H1 (RegimeClassifier real): solo RANGE o VOLATILE.
  · Umbral de ejecución 70 (hardcodeado en la estrategia; score_threshold BBDD es NULL).
  · Bloqueo de spread del executor: spread > 0.55·real_sl_atr.
  · SL: swing 15 velas M5 ±0.2·ATR_M5 (banda 1.5..sl_m·1.5) → fallback
    atr_unit·sl_m, con atr_unit = max(ATR_M15·3.5·(1.5 si VOLATILE), 0.15%·precio)/2.5.
    Suelo 0.08%. (orch_sl_mult=3.5: todos los símbolos del universo tienen >3 chars
    y "Scalper" no está en el nombre.)
  · TP: técnico (media de Bollinger) en TODOS los grupos, incl. ÍNDICES — validado
    con pst_range_lab.py (2026-07-09, 20d/5 símbolos: Δexpectancy +0.023R,
    ΔSharpe +0.30 vs. dejar ATR en índices) y shippeado como excepción de
    RangeBreaker al gate INDEX del orquestador (que sí sigue aplicando a Scalping).
  · Auto-fix R:R min_rr=1.8 (seed symbol_strategies): 1º ciñe SL a tp_dist/min_rr
    si ≥1.0·ATR_M5; si no, ESTIRA el TP a sl_dist·min_rr (¡más allá de la media!).

  GESTIÓN (manage_active_trades):
  · Parcial 50% a +1R → SL a BE+2pt (padding). BE a max(2.5, be_mult=2.0)·ATR_M5.
  · Trailing: si profit > max(2.0, ts_mult=2.5)·ATR_M5 → SL a precio∓ts_mult·ATR_M5
    (1.5 si ADX_M5 > 40). Solo si mejora.
  · TIMEOUT: nominal 30 min, pero en vivo datetime.fromtimestamp(p.time) lleva el
    reloj del broker (+3h medido el 2026-07-09) → dispara a los ~3h30m reales.
    Default fiel: 210 min. (El trade US100 07-06 duró 2h11 y murió por SL, coherente.)
  · Salida VWAP: NO aplica (solo PrecisionScalping).
  · Cooldown 15 min tras pérdida (loss_cooldown_minutes BBDD, v2.5.9+).
  · Cierre por fin de sesión: hueco M1 > 2h con posición abierta → cierre al último close.

  COSTES: medio spread en entrada + medio en salida; comisión COMMISSION_SPEC
  (INDEX 0€ — en índices el coste real es el spread; CRYPTO % nocional; forex per-lot).

Infidelidades conocidas (deliberadas, documentadas):
  · Granularidad M1 vs ciclo de 10s del bot (toques intraminuto pueden diferir).
  · ATR/ADX M5 de gestión usan la última vela M5 COMPLETADA (no la en formación).
  · Filtro de noticias y límites de portfolio multi-símbolo no modelados.
"""
import logging
import numpy as np
import pandas as pd

from .engine import BacktestTrade, BacktestResult, PSTBacktestEngine
from .faithful_engine import _commission_r

logger = logging.getLogger("PST-FaithfulRangeBT")

try:
    from ..config import COMMISSION_SPEC
except Exception:
    COMMISSION_SPEC = {
        "FOREX": {"per_lot": 4.3}, "METAL": {"per_lot": 5.5}, "COMMODITY": {"per_lot": 5.5},
        "CRYPTO": {"pct_notional": 0.00065}, "INDEX": {"per_lot": 0.0}, "EQUITIES": {"per_lot": 0.011},
    }

_TF_MINUTES = {"m5": 5, "m15": 15, "h1": 60, "h4": 240}


def _synth_forming(df_m1, tf_minutes):
    """Para cada barra M1: OHL de la vela `tf` EN FORMACIÓN a la que pertenece
    (open del bucket, cummax/cummin hasta esa M1) + open time del bucket."""
    bucket = df_m1["time"].dt.floor(f"{tf_minutes}min")
    g = df_m1.groupby(bucket, sort=False)
    return pd.DataFrame({
        "bucket": bucket.values,
        "open": g["open"].transform("first").values,
        "high": g["high"].cummax().values,
        "low": g["low"].cummin().values,
        "close": df_m1["close"].values,
    })


class FaithfulRangeEngine:
    # Seeds reales de symbol_strategies para PST-RangeBreaker (BBDD, todos los símbolos)
    SL_MULT = 2.5
    TP_MULT = 3.5
    MIN_RR = 1.8
    BE_MULT = 2.0          # → safe_be = max(2.5, be_mult) = 2.5 (no-scalper)
    TS_MULT = 2.5          # → safe_ts = max(2.0, ts_mult) = 2.5
    THRESHOLD = 70         # hardcodeado en la estrategia
    MIN_SL_PCT = 0.0008
    BE_PAD_PTS = 2         # padding no-scalper del executor (2·point tras parcial; 1·point BE)
    PARTIAL_PCT = 0.50
    COOLDOWN_SECS = 15 * 60
    TIMEOUT_SECS_LIVE = 210 * 60   # 30 min nominal + 3h de offset del reloj (fiel a live)
    SPREAD_BLOCK_RATIO = 0.55
    SESSION_GAP_SECS = 2 * 3600

    LOOKBACK = {"m15": 200, "h1": 500, "h4": 500, "m5": 50}

    def __init__(self, threshold: int = THRESHOLD, min_rr: float = MIN_RR,
                 macro_mode: str = "on", regime_gate: str = "on",
                 tp_mode: str = "live", timeout_mins: int = 210,
                 sl_mult: float = SL_MULT, tp_mult: float = TP_MULT,
                 be_mult: float = BE_MULT, ts_mult: float = TS_MULT,
                 partial_enabled: bool = True, trailing_enabled: bool = True,
                 cooldown_mins: int = 15, model_spread: bool = True):
        """
        tp_mode: 'live' = técnico salvo INDEX (fiel) · 'technical' = BB-media siempre
                 · 'atr' = ATR siempre.
        timeout_mins: 210 = comportamiento live (bug +3h) · 30 = intención del código
                      · 0 = sin timeout.
        macro_mode/regime_gate: 'on'/'off' para A/B del gate.
        """
        self.threshold = threshold
        self.min_rr = min_rr
        self.macro_mode = macro_mode
        self.regime_gate = regime_gate
        self.tp_mode = tp_mode
        self.timeout_secs = timeout_mins * 60
        self.sl_mult = sl_mult
        self.tp_mult = tp_mult
        self.be_mult = be_mult
        self.ts_mult = ts_mult
        self.partial_enabled = partial_enabled
        self.trailing_enabled = trailing_enabled
        self.cooldown_secs = cooldown_mins * 60
        self.model_spread = model_spread
        self._eng = PSTBacktestEngine(score_threshold=threshold)

    def _asset_class(self, symbol):
        return self._eng._get_asset_class(symbol)

    # ------------------------------------------------------------------ run
    async def run(self, strategy, symbol, data, point=None, spread_dist=0.0) -> BacktestResult:
        """
        strategy : instancia de PSTRangeBreaker
        data     : {"m1","m5","m15","h1","h4"} DataFrames con columna 'time'
                   (+ point/spread_dist/tick_value/tick_size)
        """
        from ..models.classifier import RegimeClassifier, RegimeMode
        from ..engine.macro_trend_filter import MacroTrendFilter

        df_m1 = data["m1"].reset_index(drop=True)
        tf_dfs = {tf: data[tf].reset_index(drop=True) for tf in ("m5", "m15", "h1", "h4")}
        spread = (spread_dist if self.model_spread else 0.0) or 0.0
        half_spread = spread / 2.0
        if point is None:
            point = 10 ** -self._infer_digits(df_m1)

        a_class = self._asset_class(symbol)
        comm_spec = COMMISSION_SPEC.get(a_class, {})
        tick_value, tick_size = data.get("tick_value"), data.get("tick_size")
        value_per_price = (tick_value / tick_size) if (tick_value and tick_size) else None
        if comm_spec.get("per_lot", 0.0) > 0 and value_per_price is None:
            logger.warning("%s: comisión per_lot sin tick_value/tick_size → no aplicada. "
                           "Regenerar caché con --refresh.", symbol)

        classifier = RegimeClassifier()
        macro_filter = MacroTrendFilter()

        # --- precomputados vectorizados ---
        m1_times = df_m1["time"]
        m1_t64 = m1_times.values
        forming = {tf: _synth_forming(df_m1, mins) for tf, mins in _TF_MINUTES.items()}
        # nº de velas tf COMPLETADAS antes de cada M1 (open < bucket_open de esa M1)
        completed_idx = {}
        for tf in _TF_MINUTES:
            opens = tf_dfs[tf]["time"].values
            completed_idx[tf] = np.searchsorted(opens, forming[tf]["bucket"].values, side="left")

        # ATR14/ADX14 sobre M5 completadas (gestión: trailing/BE); valor at k-1
        import pandas_ta as pta
        m5 = tf_dfs["m5"]
        atr_m5_series = pta.atr(m5["high"], m5["low"], m5["close"], length=14)
        adx_m5_df = pta.adx(m5["high"], m5["low"], m5["close"], length=14)
        adx_m5_series = adx_m5_df["ADX_14"] if adx_m5_df is not None else None

        # Prefiltro de toque BB (exacto + margen conservador): media/σ de 19 closes M15
        # completadas + close M1 en formación → solo llamamos a la estrategia si la vela
        # en formación está cerca de una banda exterior. Nunca descarta un toque real.
        m15 = tf_dfs["m15"]
        c15 = m15["close"].values.astype(float)
        s19 = pd.Series(c15).rolling(19).sum().values
        q19 = pd.Series(c15 ** 2).rolling(19).sum().values
        k15 = completed_idx["m15"]
        valid = k15 >= 50                       # estrategia exige ≥50 velas M15
        cform = forming["m15"]["close"].values.astype(float)
        lform = forming["m15"]["low"].values.astype(float)
        hform = forming["m15"]["high"].values.astype(float)
        with np.errstate(invalid="ignore"):
            S = np.where(k15 >= 19, s19[np.clip(k15 - 1, 0, None)], np.nan)
            Q = np.where(k15 >= 19, q19[np.clip(k15 - 1, 0, None)], np.nan)
            mean20 = (S + cform) / 20.0
            var1 = ((Q + cform ** 2) - 20.0 * mean20 ** 2) / 19.0   # ddof=1 (pandas)
            sigma = np.sqrt(np.clip(var1, 0, None))
            buf = 0.15 * 2.0 * sigma            # margen 15% del semiancho (conservador)
            near_touch = (lform <= mean20 - 2.0 * sigma + buf) | (hform >= mean20 + 2.0 * sigma - buf)
        candidate = valid & np.nan_to_num(near_touch, nan=False)

        period_start = m1_times.iloc[0]
        period_end = m1_times.iloc[-1]
        res = BacktestResult("PST-RangeBreaker", symbol, period_start, period_end)

        pos = None
        cooldown_until = None
        n = len(df_m1)
        for i in range(n):
            bar = df_m1.iloc[i]
            t = bar["time"]

            # cierre por hueco de sesión (finde/EOD) con posición abierta
            if pos is not None and i + 1 < n:
                gap = (m1_times.iloc[i + 1] - t).total_seconds()
                if gap > self.SESSION_GAP_SECS:
                    self._close_remaining(pos, float(bar["close"]), t, "SESSION_END")
                    res.trades.append(pos)
                    pos = None
                    continue

            if pos is not None:
                k5 = completed_idx["m5"][i]
                atr5 = float(atr_m5_series.iloc[k5 - 1]) if (atr_m5_series is not None and k5 >= 15) else 0.0
                adx5 = float(adx_m5_series.iloc[k5 - 1]) if (adx_m5_series is not None and k5 >= 15) else 0.0
                closed = self._manage(pos, bar, atr5, adx5)
                if closed:
                    if pos.pnl_r < 0:
                        cooldown_until = t + pd.Timedelta(seconds=self.cooldown_secs)
                    res.trades.append(pos)
                    pos = None
                continue

            # --- flat: evaluar entrada ---
            if not candidate[i]:
                continue
            if cooldown_until is not None and t < cooldown_until:
                continue

            m15_slice = self._slice(tf_dfs, forming, completed_idx, "m15", i)
            h1_slice = self._slice(tf_dfs, forming, completed_idx, "h1", i)
            sig = await strategy.calculate_signal({"m15": m15_slice, "h1": h1_slice},
                                                  spread_dist=spread)
            score, entry, atr = sig.get("score", 0), sig.get("entry", 0), sig.get("atr", 0.0)
            if entry == 0 or atr <= 0:
                continue

            # filtro macro (idéntico al orquestador: ajusta score, veto si STRONG contra)
            if self.macro_mode == "on":
                h4_slice = self._slice(tf_dfs, forming, completed_idx, "h4", i)
                macro = macro_filter.analyze({"h1": h1_slice, "h4": h4_slice})
                ok, adj = macro.allows_direction(entry, "RANGE")
                if not ok:
                    continue
                score = max(0, min(100, score + adj))
            if score < self.threshold:
                continue

            # gate de régimen H1: RANGE o VOLATILE
            mode, _ = classifier.classify(h1_slice)
            if self.regime_gate == "on" and mode not in (RegimeMode.RANGE, RegimeMode.VOLATILE):
                continue

            pos = self._open(sig, bar, symbol, a_class, mode, half_spread, point,
                             comm_spec, value_per_price,
                             self._slice(tf_dfs, forming, completed_idx, "m5", i))
            # pos None = bloqueado por spread u orden inválida

        if pos is not None:
            self._close_remaining(pos, float(df_m1.iloc[-1]["close"]), period_end, "EOD")
            pos.result = "OPEN"
            res.trades.append(pos)
        return res

    # ------------------------------------------------------------------ slices as-of
    def _slice(self, tf_dfs, forming, completed_idx, tf, i):
        """DataFrame de `tf` como lo vería el bot en la barra M1 i: hasta N-1 velas
        completadas + la vela EN FORMACIÓN sintetizada desde M1."""
        n_keep = self.LOOKBACK[tf] - 1
        k = int(completed_idx[tf][i])
        comp = tf_dfs[tf].iloc[max(0, k - n_keep):k]
        f = forming[tf]
        row = pd.DataFrame({
            "time": [f["bucket"].iloc[i]],
            "open": [f["open"].iloc[i]], "high": [f["high"].iloc[i]],
            "low": [f["low"].iloc[i]], "close": [f["close"].iloc[i]],
        })
        cols = ["time", "open", "high", "low", "close"]
        return pd.concat([comp[cols], row], ignore_index=True)

    # ------------------------------------------------------------------ apertura
    def _open(self, sig, bar, symbol, a_class, mode, half_spread, point,
              comm_spec, value_per_price, m5_slice):
        import pandas_ta as pta
        direction = int(sig["entry"])
        raw = float(bar["close"])
        fill = raw + direction * half_spread
        atr_m15 = float(sig.get("atr", 0.0))

        # atr_unit del executor: el orquestador pasa sl_dist = ATR_M15·3.5 (símbolos >3
        # chars, "Scalper" no en el nombre) ·1.5 si VOLATILE, con suelo 0.15%·precio.
        vol_mult = 1.5 if mode == "VOLATILE" else 1.0
        atr_unit = max(atr_m15 * 3.5 * vol_mult, raw * 0.0015) / 2.5
        real_sl_atr = atr_unit * self.sl_mult

        # Bloqueo de spread del executor
        if half_spread * 2.0 > real_sl_atr * self.SPREAD_BLOCK_RATIO:
            return None

        # ATR M5 + swing 15 (slice de 50 velas con forming, como el fetch del executor)
        atr_m5 = 0.0
        ma_atr_m5 = 0.0
        try:
            s = pta.atr(m5_slice["high"], m5_slice["low"], m5_slice["close"], length=14)
            if s is not None and len(s):
                v = float(s.iloc[-1]); atr_m5 = v if v == v else 0.0
                mv = float(s.rolling(20).mean().iloc[-1]); ma_atr_m5 = mv if mv == mv else atr_m5
        except Exception:
            pass
        low15 = float(m5_slice["low"].tail(15).min())
        high15 = float(m5_slice["high"].tail(15).max())

        # --- SL: réplica execute_trade ---
        sl = 0.0
        if atr_m5 > 0:
            if direction == 1:
                cand = low15 - 0.2 * atr_m5
                if 1.5 <= (fill - cand) / atr_m5 <= self.sl_mult * 1.5:
                    sl = cand
            else:
                cand = high15 + 0.2 * atr_m5
                if 1.5 <= (cand - fill) / atr_m5 <= self.sl_mult * 1.5:
                    sl = cand
        if sl == 0:
            sl = fill - direction * real_sl_atr
        if abs(fill - sl) < fill * self.MIN_SL_PCT:
            sl = fill - direction * fill * self.MIN_SL_PCT

        # --- TP: técnico (BB media) salvo índices, según tp_mode ---
        if self.tp_mode == "atr":
            use_tech = False
        else:  # live / technical: TP técnico (BB-media) en TODOS los grupos, incl.
            # ÍNDICES — confirmado por pst_range_lab.py (2026-07-09, 20d/5 símbolos,
            # Δexpectancy +0.023R/ΔSharpe +0.30) y shippeado en orchestrator.py
            # (excepción de RangeBreaker al gate INDEX que sí aplica a Scalping).
            use_tech = True
        tp = 0.0
        if use_tech:
            tp_sig = sig.get("tp_price", 0) or 0
            if tp_sig > 0 and ((direction == 1 and tp_sig > fill) or (direction == -1 and tp_sig < fill)):
                tp = tp_sig
        if tp == 0:
            vol_ratio = (atr_m5 / ma_atr_m5) if ma_atr_m5 > 0 else 1.0
            if vol_ratio > 1.2:
                tp_adj = min(1.5, vol_ratio)
            elif vol_ratio < 0.8:
                tp_adj = max(0.7, vol_ratio)
            else:
                tp_adj = 1.0
            tp = fill + direction * atr_unit * self.tp_mult * tp_adj

        # --- auto-fix R:R del executor (v2.6.9: el encogimiento del SL respeta el
        # suelo max(1.0·ATR_M5, 0.08% del precio), como en execute_trade) ---
        sl_dist = abs(fill - sl)
        tp_dist = abs(tp - fill)
        if self.min_rr > 0 and sl_dist > 0 and tp_dist / sl_dist < self.min_rr:
            ideal_sl_dist = tp_dist / self.min_rr
            if ideal_sl_dist >= max(1.0 * atr_m5, fill * self.MIN_SL_PCT):
                sl = fill - direction * ideal_sl_dist
            else:
                tp = fill + direction * sl_dist * self.min_rr
        sl_dist = abs(fill - sl)

        t = BacktestTrade(
            entry_time=bar["time"], exit_time=None, symbol=symbol, strategy="PST-RangeBreaker",
            direction=direction, entry_price=fill, sl_price=sl, tp_price=tp,
            score=abs(sig.get("score", 0)), atr=atr_m15,
        )
        t._R = sl_dist
        t._rem = 1.0
        t._realized = 0.0
        t._partial = False
        t._be = False
        t._half_spread = half_spread
        t._point = point
        t._commission_r = _commission_r(comm_spec, fill, sl_dist, value_per_price)
        return t

    # ------------------------------------------------------------------ gestión
    def _manage(self, t, bar, atr_m5, adx_m5) -> bool:
        h, l, c = float(bar["high"]), float(bar["low"]), float(bar["close"])
        d, fill, R = t.direction, t.entry_price, t._R
        age = (bar["time"] - t.entry_time).total_seconds()

        # 1) SL / TP intrabar (SL primero, conservador)
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

        # 2) Parcial 50% a +1R → BE con padding (no-scalper: 2·point)
        if self.partial_enabled and not t._partial and R > 0:
            reached_1r = (h >= fill + R) if d == 1 else (l <= fill - R)
            if reached_1r:
                hs = t._half_spread
                t._realized += self.PARTIAL_PCT * ((R - hs) / R)
                t._rem -= self.PARTIAL_PCT
                t._partial = True
                t._be = True
                t.sl_price = fill + d * self.BE_PAD_PTS * t._point
                t.exit_reason = "PARTIAL_1R"

        # 3) BE a safe_be_mult·ATR_M5 (no-scalper: max(2.5, be_mult))
        if not t._be and atr_m5 > 0:
            be_atr = max(2.5, self.be_mult)
            reached_be = (h >= fill + be_atr * atr_m5) if d == 1 else (l <= fill - be_atr * atr_m5)
            if reached_be:
                t.sl_price = fill + d * 1 * t._point   # padding no-scalper: 1 pt
                t._be = True

        # 4) Trailing (use_trailing=1 en BBDD): profit > max(2.0, ts_mult)·ATR_M5
        if self.trailing_enabled and atr_m5 > 0:
            profit = (c - fill) if d == 1 else (fill - c)
            if profit > max(2.0, self.ts_mult) * atr_m5:
                mult = 1.5 if adx_m5 > 40 else self.ts_mult
                trail = c - d * mult * atr_m5
                if (d == 1 and trail > t.sl_price) or (d == -1 and trail < t.sl_price):
                    t.sl_price = trail

        # 5) Timeout (live: 30 min nominal + 3h de offset del reloj → 210 min)
        if self.timeout_secs > 0 and age > self.timeout_secs:
            self._close_remaining(t, c, bar["time"], "TIMEOUT"); return True

        return False

    def _close_remaining(self, t, price, time, reason):
        d, fill, R = t.direction, t.entry_price, t._R
        hs = t._half_spread
        eff = price - d * hs
        r_leg = (d * (eff - fill) / R) if R > 0 else 0.0
        t._realized += t._rem * r_leg
        t._realized -= t._commission_r
        t._rem = 0.0
        t.exit_price = price
        t.exit_time = time
        t.pnl_r = t._realized
        if not t.exit_reason or reason != "EOD":
            t.exit_reason = reason
        t.result = "WIN" if t.pnl_r > 0 else "LOSS"

    @staticmethod
    def _infer_digits(df_m1):
        try:
            px = float(df_m1["close"].iloc[-1])
            if px >= 100:
                return 2
            if px >= 10:
                return 3
            return 5
        except Exception:
            return 5
