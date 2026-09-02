"""
PST · Motor de backtesting FIEL para PST-MomentumPulse / PST-DivergenceReversion
──────────────────────────────────────────────────────────────────────────────
Estas dos estrategias se activaron en real el 2026-08-11 con un comentario de código
("validadas offline (Fases 0-5)") que resultó ser falso: solo existía el motor
GENÉRICO (PSTBacktestEngine, engine.py) con SL/TP por múltiplo de ATR fijo y costes
opt-in — no modela breakeven, trailing, cierre parcial, la excepción de timeout de
TREND, cooldown tras pérdida, ni el filtro de régimen/macro que aplica el
orquestador antes de ejecutar. Este archivo es ESE motor que faltaba, siguiendo el
mismo patrón que faithful_engine.py (PrecisionScalping) y faithful_range_engine.py
(RangeBreaker): réplica de la lógica EXACTA del pipeline orquestador→executor, no
un proxy simplificado.

A diferencia de PrecisionScalping/RangeBreaker (que sí tenían diferencias reales
entre sí — VWAP exit, TP técnico por grupo de activo — que justificaban dos clases
separadas), MomentumPulse y DivergenceReversion comparten EXACTAMENTE la misma
gestión genérica del executor (manage_active_trades) porque ninguna implementa
check_exit_signal ni tiene "Scalping"/"Scalper" en su comentario de orden — así que
is_scalper_pos=False SIEMPRE para ambas. Una única clase parametrizada por el nombre
de la estrategia basta.

Verificado releyendo el código real (líneas citadas pueden haberse movido unas pocas
líneas desde que se escribió este comentario, pero la lógica fue confirmada):

ENTRADA — DOBLE capa orquestador→executor:

  Capa 1, orchestrator.py ~704-761 (genérico, TODAS las estrategias): antes de
  llamar a execute_trade, recalcula una distancia de SL preliminar:
      atr_current = s_result["atr"]           (ATR de la estrategia en su active_tf)
      sl_mult = SL_ATR_MULTIPLIER (2.5); tp_mult = TP_ATR_MULTIPLIER (6.0)
      if (len(symbol) > 3 or "500" in symbol or "30" in symbol) and "Scalping" not in s_name:
          sl_mult = 3.5; tp_mult = 5.0        (¡SIEMPRE cierto para nuestro universo:
                                                todo símbolo real tiene >3 caracteres!)
      if mode == VOLATILE: sl_mult *= 1.5
      sl_dist = max(atr_current * sl_mult, price * 0.0015)
  Este sl_dist es lo único que sobrevive al viaje: se pasa a execute_trade como
  `stop_loss_atr`, que allí se convierte en `atr_unit = stop_loss_atr / 2.5` — la
  base de los fallbacks de SL/TP del executor. El parámetro `take_profit_atr` que
  también recibe execute_trade está MUERTO (se declara pero nunca se lee dentro de
  la función — confirmado por grep) — el tp_dist calculado aquí en el orquestador NO
  tiene ningún efecto real.

  HALLAZGO CRÍTICO #1 (orchestrator.py ~750-757, replicado tal cual — es
  comportamiento real de producción, no un bug a "arreglar"): el tp_price técnico que
  calcula la propia estrategia (borde de canal H1 para MomentumPulse, borde opuesto
  para DivergenceReversion) SOLO se inyecta en metadata["target_price_tp"] (que es lo
  que execute_trade usa después) SI get_asset_class(symbol) != "INDEX" O la estrategia
  es "PST-RangeBreaker". Ninguna de nuestras dos estrategias es RangeBreaker, así que
  en símbolos INDEX (US100.cash, US30.cash — 2 de los 4 símbolos piloto) el TP técnico
  se DESCARTA SIEMPRE y el trade usa el fallback ATR del executor. Parametrizado aquí
  como `index_technical_tp` (default False = fiel a producción; True = inyecta igual,
  para A/B).

  Capa 2, executor.py::execute_trade (~líneas 25-423):
    · SL estructural M5 (~148-172): swing low/high de 15 velas M5 (SIEMPRE M5, sin
      importar el active_tf de la señal), banda válida 1.5 ≤ dist/ATR_M5 ≤ sl_m·1.5.
    · Override (~174-181): SI metadata["target_price_sl"] > 0 Y lado válido → GANA
      sobre el structural M5, SIN validación de banda. Ambas estrategias siempre
      devuelven target_price_sl cuando entry != 0 Y la banda propia de la estrategia
      (0.8-3.5 ATR MomentumPulse / 0.5-4.0 ATR DivergenceReversion) valida — así que
      este es el SL real casi siempre, pero NO el 100% de las veces (si esa banda no
      valida, metadata ni siquiera trae la clave y se cae al structural M5 o al ATR).
    · Fallback ATR (~184-186): real_sl_atr = atr_unit · sl_m (sl_m de symbol_strategies).
    · Suelo 0.08% del precio (~191-200).
    · TP (~220-234): target_tp = metadata.get("target_price_tp", 0) (0 para INDEX,
      ver hallazgo #1) con fallback de seguridad si queda del lado incorrecto; si no,
      real_tp_atr_adj = atr_unit · tp_m · ajuste_vol(M5 current_atr/ma_atr).
    · Auto-fix R:R (~236-264): ciñe el SL hasta max(1.0·ATR_M5, 0.08%·precio); si no
      cabe, estira el TP. min_rr de symbol_strategies.
    · Guards finales (~266-288): spread > 55% del SL final → rechazar; comisión
      proyectada cripto > CRYPTO_MAX_COMMISSION_R → rechazar.

  Seeds reales de symbol_strategies (database.py ~544-561, usados como default,
  parametrizables igual que los otros dos motores):
    MomentumPulse:        use_breakeven=1, use_trailing=1, be_mult=2.0, ts_mult=2.5,
                           min_rr=1.8, sl_mult=1.8, tp_mult=3.5
    DivergenceReversion:  use_breakeven=1, use_trailing=0, be_mult=2.0, ts_mult=2.5,
                           min_rr=1.5, sl_mult=2.0, tp_mult=3.0

GESTIÓN — executor.py::manage_active_trades (~líneas 425-660), rama NO-scalper
(is_scalper_pos=False siempre: ni "Scalping" ni "Scalper" aparecen en el comentario
de orden "MomentumPulse_TF"/"DivergenceReversion_TF"):
    · ATR/ADX M5 FRESCOS, recalculados cada ciclo desde 200 velas M5 (no 50, y no el
      ATR congelado de la entrada). Aquí: series vectorizadas sobre TODO el M5 del
      periodo (el valor en la posición i de un rolling(14)/rolling(20) local no
      depende de si se calculó sobre una ventana de 50 o de 200 barras, mientras haya
      suficiente historia antes de i — así que una única serie vectorizada sirve
      tanto para el dimensionado al abrir como para la gestión, sin recomputar nada).
    · Breakeven: use_be = strat_cfg.use_breakeven==1 (1 para ambas). safe_be_mult =
      max(2.5, be_mult=2.0) = 2.5. Dispara cuando el extremo favorable de la vela
      (h/l, no el close) superó profit > ATR_M5_fresco·2.5. Padding 1 punto
      (no-scalper). Solo se re-arma si el SL actual no está YA en breakeven o mejor
      (is_sl_at_be), igual que en vivo.
    · Trailing: use_ts = strat_cfg.use_trailing==1 (MomentumPulse sí,
      DivergenceReversion no). safe_ts_mult = max(2.0, ts_mult=2.5) = 2.5. Arma con
      el CLOSE de la vela (no el extremo — así lo hace también faithful_engine.py:
      el trailing usa el precio "actual", no el mejor punto tocado). Correa
      ts_mult·ATR (1.5 si ADX_M5(14) > 40). Solo mejora el SL, nunca lo empeora.
    · Sin salida dinámica VWAP (esa rama solo aplica si "PrecisionScalping" in
      p.comment — ninguna de las dos).
    · Cierre parcial (~580-620): aplica a AMBAS ("Scalper" not in p.comment es
      SIEMPRE True para estas dos estrategias — el guard SCALPER_PARTIAL_CLOSE_*
      nunca entra en juego). 50% al alcanzar +1.0R, calculado contra el SL ORIGINAL
      guardado al abrir (t._R, fijo — nunca el SL actual movido por BE/trailing).
      Tras el parcial: fuerza SL a breakeven con padding de 2 puntos (no-scalper),
      SOBRESCRIBIENDO lo que BE/trailing hubieran puesto ese mismo ciclo — así lo
      hace también el executor real (el bloque D no lee lo que puso el bloque A/B,
      simplemente asigna su propio new_sl).
    · Timeout 30 min nominal (~622-651): SOLO SI STRATEGY_CATEGORIES[strat] !=
      "TREND" (aquí: strategy.STRATEGY_TYPE, valor idéntico). MomentumPulse (TREND)
      queda EXENTO — nunca se cierra por timeout, coherente con su diseño de "dejar
      correr ganadoras". DivergenceReversion (RANGE) SÍ se cierra a los 30 min
      nominales. Antecedente documentado (faithful_range_engine.py, RangeBreaker):
      en vivo ese timeout se midió con un offset de reloj del broker de +3h en julio
      de 2026 (30 min nominales → ~3h30 reales) — NO sabemos si ese offset sigue
      vigente hoy ni si aplica igual a DivergenceReversion. `timeout_mins` expone el
      valor NOMINAL (30) por defecto; recalibrar con trades reales de
      DivergenceReversion es tarea aparte, no resuelta aquí.

FILTRO DE ENTRADA PREVIO — orchestrator.py:
    · Macro filter (~520-544): MacroTrendFilter.analyze(H1/H4) +
      allows_direction(dirección, STRATEGY_TYPE). TREND (MomentumPulse): veto total
      si contra tendencia STRONG/MODERATE/WEAK. RANGE (DivergenceReversion): veto
      solo si STRONG, si no penalización de score (no bloquea).
    · Gate de régimen H1 (~592-619, RegimeClassifier):

      HALLAZGO CRÍTICO #2 (verificado línea a línea, no es lo que dice el
      comentario del código real): la variable is_strat_in_regime empieza en False y
      SOLO puede pasar a True si `strat_type == mode` (coincidencia EXACTA) o
      `strat_type == "ALL"`. Los bloques posteriores que mencionan VOLATILE
      ("TREND: tendencia Y volátil" / "RANGE: rango Y volátil") tienen la forma
      `if strat_type == X and mode not in [X, VOLATILE]: is_strat_in_regime = False`
      — es decir, SOLO pueden apagar el flag (pasarlo de True a False), NUNCA
      encenderlo. Para TREND en régimen VOLATILE: la primera asignación no dispara
      (mode "VOLATILE" != strat_type "TREND"), y la rama de abajo tampoco lo enciende
      (solo lo apagaría si mode NO estuviera en la lista permitida, pero SÍ está). El
      flag queda en False. Mismo resultado para RANGE en VOLATILE. CONCLUSIÓN: en
      producción, MomentumPulse SOLO opera en régimen TREND puro y DivergenceReversion
      SOLO en régimen RANGE puro — VOLATILE bloquea a ambas pese a que el comentario
      del código ("Crisis WR Fix") dice lo contrario. Esto es MÁS estricto que lo que
      predecía inicialmente el plan de este motor. `regime_volatile_bug_faithful=True`
      (default) reproduce el comportamiento REAL; False simula el diseño pretendido
      (mode in [TYPE, VOLATILE]) para poder comparar cuánta señal se pierde por este bug.

COOLDOWN: 15 min tras pérdida (loss_cooldown_minutes, bot_config default), igual
patrón que los otros dos motores.

COSTES: cost_model.py (commission_r/swap_r/nights_held) — reutilizado tal cual, es
más completo que el _commission_r de faithful_engine.py (incluye swap/overnight,
relevante aquí porque MomentumPulse puede mantener posiciones horas/días sin
timeout). Medio spread en la entrada (ajuste de precio de fill) y medio en la salida
(ajuste de precio de cierre) — algebraicamente idéntico a cost_model.exit_spread_r,
IGUAL patrón que faithful_engine.py/faithful_range_engine.py (se deja así por
consistencia con los motores existentes en vez de mezclar los dos estilos).

Infidelidades conocidas (deliberadas, documentadas):
  · Granularidad M5 (ANCHOR_TF de ambas estrategias, y el TF que usa el executor
    para su propia gestión) vs el ciclo real de ~10s del bot. AdaptiveTFSelector
    puede elegir un active_tf MÁS RÁPIDO que M5 (m2/m3) para el score de entrada —
    en ese caso, la vela más rápida que ve este motor es la última M2/M3
    COMPLETADA al cierre del M5, no la vela en formación que vería el bot en vivo
    (a diferencia de faithful_range_engine.py, que sí sintetiza la M15 en
    formación porque RangeBreaker depende del toque exacto de banda de Bollinger —
    replicar eso para 6 timeframes distintos aquí no se ha hecho).
  · ATR/ADX M5 de gestión usan la última vela M5 COMPLETADA, no la que estaría en
    formación a mitad de un ciclo de 10s.
  · Cierre por hueco de sesión (>SESSION_GAP_MINS sin barra siguiente, con posición
    abierta) — NO pedido explícitamente en el plan, pero necesario aquí porque
    MomentumPulse no tiene timeout y puede sobrevivir a un fin de semana: sin esto,
    el SL/TP de una vela de lunes se evaluaría contra el rango high/low de un gap de
    62h, produciendo fills irreales. Mismo patrón que faithful_range_engine.py
    (SESSION_GAP_SECS=2h): en la última vela antes del hueco, cierra al close SIN
    comprobar su propio SL/TP (igual simplificación que ya acepta ese motor).
  · Filtro de noticias, límites de portfolio/correlación multi-símbolo y el
    News/SAR/Stalking del orquestador no se modelan (motor de UN símbolo aislado,
    igual que los otros dos).
"""
import logging

import numpy as np
import pandas as pd

from .engine import BacktestTrade, BacktestResult, PSTBacktestEngine
from .cost_model import commission_r, swap_r, nights_held, commission_guard_ok

logger = logging.getLogger("PST-FaithfulMLEngine")

try:
    from ..config import COMMISSION_SPEC
except Exception:  # ejecución fuera del paquete (carga suelta)
    COMMISSION_SPEC = {
        "FOREX": {"per_lot": 4.3}, "METAL": {"per_lot": 5.5}, "COMMODITY": {"per_lot": 5.5},
        "CRYPTO": {"pct_notional": 0.00065}, "INDEX": {"per_lot": 0.0}, "EQUITIES": {"per_lot": 0.011},
    }

# Seeds reales de symbol_strategies (database.py ~544-561) — defaults del motor,
# sobreescribibles vía constructor (igual que los otros dos motores fieles).
_SEEDS = {
    "PST-MomentumPulse": dict(
        use_breakeven=1, use_trailing=1, be_mult=2.0, ts_mult=2.5,
        min_rr=1.8, sl_mult=1.8, tp_mult=3.5,
    ),
    "PST-DivergenceReversion": dict(
        use_breakeven=1, use_trailing=0, be_mult=2.0, ts_mult=2.5,
        min_rr=1.5, sl_mult=2.0, tp_mult=3.0,
    ),
}
_DEFAULT_SEED = dict(use_breakeven=1, use_trailing=0, be_mult=2.0, ts_mult=2.5,
                      min_rr=1.5, sl_mult=2.0, tp_mult=3.0)

_TF_KEYS = ("m2", "m3", "m5", "m15", "m30", "h1", "h4")


class FaithfulMLEngine:
    """Motor fiel compartido por PST-MomentumPulse y PST-DivergenceReversion.

    Uso:
        eng = FaithfulMLEngine()
        res = await eng.run(PSTMomentumPulse(), "US100.cash", data)
        res.print_report()

    `data` sigue el shape que produce Tools/ml_data.py::get_full_mtf_data: dict con
    clave por timeframe (m2/m3/m5/m15/m30/h1/h4, DataFrames con columna 'time') +
    "point"/"spread_dist"/"tick_value"/"tick_size"/"swap_long"/"swap_short".
    """

    MIN_SL_PCT = 0.0008          # suelo de SL (0.08% del precio) — espejo executor
    MTF_SLICE_LOOKBACK = PSTBacktestEngine.MTF_SLICE_LOOKBACK  # 400 velas de contexto

    def __init__(self, min_rr=None, sl_mult=None, tp_mult=None, be_mult=None, ts_mult=None,
                 use_breakeven=None, use_trailing=None,
                 macro_mode: str = "on", regime_gate: str = "on",
                 regime_volatile_policy: str = "fixed",
                 index_technical_tp: bool = False,
                 timeout_mins: int = 30, cooldown_mins: int = 15,
                 partial_enabled: bool = True, partial_pct: float = 0.50, partial_r: float = 1.0,
                 model_spread: bool = True, session_gap_mins: int = 120,
                 warmup_bars: int = 200, extra_score_floor: int = 0):
        """
        min_rr/sl_mult/tp_mult/be_mult/ts_mult/use_breakeven/use_trailing: None =
            usa el seed real de symbol_strategies para la estrategia que se pase a
            run() (_SEEDS); un valor explícito lo sobreescribe para AMBAS estrategias
            (para overrides por símbolo, instanciar un motor por símbolo).
        macro_mode/regime_gate: "on"/"off" para A/B del filtro macro y el gate de
            régimen H1 respectivamente.
        regime_volatile_policy: "fixed" (default) = espejo EXACTO de la producción
            ACTUAL tras el fix del 2026-08-13 (ver HALLAZGO CRÍTICO #2 en el
            docstring del módulo + PST_Core/engine/orchestrator.py ~604-635):
            TREND opera en TREND y VOLATILE; RANGE SOLO en RANGE puro (VOLATILE
            excluido a propósito — medido con este mismo motor vía
            Tools/pst_ml_regime_ab.py: incluir VOLATILE le resta -0.107R/trade a
            DivergenceReversion, consistente en los 4 símbolos piloto). "pre_fix_bug"
            = comportamiento ANTES del fix (bug real hasta esa fecha: ni TREND ni
            RANGE operaban nunca en VOLATILE) — solo para comparar baselines
            guardados antes del 2026-08-13. "symmetric" = diseño originalmente
            pretendido para AMBAS categorías (mode in [TYPE, VOLATILE]) — descartado
            para RANGE por los datos, disponible solo para referencia/depuración.
        index_technical_tp: False (default) = fiel a producción, el TP técnico de la
            estrategia se descarta en símbolos INDEX (ver HALLAZGO CRÍTICO #1). True =
            lo inyecta igualmente, para A/B.
        timeout_mins: minutos nominales del timeout de estancamiento (solo aplica a
            estrategias RANGE, p.ej. DivergenceReversion — MomentumPulse/TREND está
            exenta siempre). Ver nota de incertidumbre de offset de reloj en el
            docstring del módulo antes de recalibrar este valor con datos reales.
        cooldown_mins: bloqueo de reentrada tras una pérdida (loss_cooldown_minutes).
        partial_enabled/partial_pct/partial_r: cierre parcial genérico (no-scalper):
            producción usa 1.0R / 50% fijos (hardcodeados en el executor, no leídos
            de configuración) — expuestos aquí solo para poder desactivarlos en A/B.
        model_spread: aplica medio spread en entrada + medio en salida.
        session_gap_mins: cierra una posición abierta al close de la última vela
            antes de un hueco de datos > este umbral (ver "Infidelidades conocidas").
            0 = desactivado.
        warmup_bars: velas M5 de calentamiento antes de empezar a evaluar entradas.
        extra_score_floor: filtro de score ADICIONAL sobre el ENTRY_THRESHOLD propio
            de la estrategia (que ya se aplica dentro de calculate_signal — entry=0
            si no lo supera). 0 = no-op, inerte por defecto; existe para que un futuro
            script de sweep pueda barrer el umbral sin tocar la clase de estrategia.
        """
        self._ovr = dict(min_rr=min_rr, sl_mult=sl_mult, tp_mult=tp_mult, be_mult=be_mult,
                          ts_mult=ts_mult, use_breakeven=use_breakeven, use_trailing=use_trailing)
        self.macro_mode = macro_mode
        self.regime_gate = regime_gate
        self.regime_volatile_policy = regime_volatile_policy
        self.index_technical_tp = index_technical_tp
        self.timeout_secs = timeout_mins * 60
        self.cooldown_secs = cooldown_mins * 60
        self.partial_enabled = partial_enabled
        self.partial_pct = partial_pct
        self.partial_r = partial_r
        self.model_spread = model_spread
        self.session_gap_secs = session_gap_mins * 60 if session_gap_mins else 0
        self.warmup_bars = warmup_bars
        self.extra_score_floor = extra_score_floor
        self._eng = PSTBacktestEngine(score_threshold=1)

    def _asset_class(self, symbol):
        return self._eng._get_asset_class(symbol)

    def _regime_allowed(self, strat_type: str, mode: str) -> bool:
        """Réplica del gate de régimen H1 (orchestrator.py ~592-635, arreglado el
        2026-08-13 — ver HALLAZGO CRÍTICO #2 en el docstring del módulo y
        Tools/pst_ml_regime_ab.py para la medición que motivó el fix asimétrico."""
        if self.regime_volatile_policy == "pre_fix_bug":
            return strat_type == mode
        if self.regime_volatile_policy == "symmetric":
            if strat_type == "TREND":
                return mode in ("TREND", "VOLATILE")
            if strat_type == "RANGE":
                return mode in ("RANGE", "VOLATILE")
            return strat_type == mode
        # "fixed" (default) — espejo exacto de la producción actual.
        if strat_type == "TREND":
            return mode in ("TREND", "VOLATILE")
        if strat_type == "RANGE":
            return mode == "RANGE"
        return strat_type == mode

    # ------------------------------------------------------------------ run
    async def run(self, strategy, symbol, data, point=None, spread_dist=None) -> BacktestResult:
        """
        strategy : instancia de PSTMomentumPulse o PSTDivergenceReversion
        symbol   : símbolo (ej. "US100.cash")
        data     : dict {"m2","m3","m5","m15","m30","h1","h4": DataFrame, "point",
                   "spread_dist", "tick_value", "tick_size", "swap_long", "swap_short"}
                   — shape de Tools/ml_data.py::get_full_mtf_data.
        """
        from ..models.classifier import RegimeClassifier
        from ..engine.macro_trend_filter import MacroTrendFilter
        import pandas_ta as pta

        strat_name = getattr(strategy, "STRATEGY_NAME", "PST-Unknown")
        strat_type = getattr(strategy, "STRATEGY_TYPE", "TREND")

        seed = dict(_SEEDS.get(strat_name, _DEFAULT_SEED))
        if strat_name not in _SEEDS:
            logger.warning("%s: sin seed conocido en FaithfulMLEngine, usando default genérico.", strat_name)
        for k, v in self._ovr.items():
            if v is not None:
                seed[k] = v
        profile = seed

        if "m5" not in data:
            raise ValueError("FaithfulMLEngine requiere al menos la clave 'm5' en `data`.")
        df_m5 = data["m5"].reset_index(drop=True)
        tf_dfs = {tf: data[tf].reset_index(drop=True) for tf in _TF_KEYS if tf in data}
        m5_times = df_m5["time"].values
        n = len(df_m5)

        if n <= self.warmup_bars + 20:
            logger.warning("%s/%s: datos M5 insuficientes (%d velas) para el motor fiel ML.", strat_name, symbol, n)
            return BacktestResult(strat_name, symbol, None, None)

        spread = spread_dist if spread_dist is not None else data.get("spread_dist")
        spread = (spread or 0.0) if self.model_spread else 0.0
        half_spread = spread / 2.0
        pt = point if point is not None else data.get("point")
        if not pt:
            pt = 10 ** -self._infer_digits(df_m5)

        a_class = self._asset_class(symbol)
        comm_spec = COMMISSION_SPEC.get(a_class, {})
        tick_value, tick_size = data.get("tick_value"), data.get("tick_size")
        value_per_price = (tick_value / tick_size) if (tick_value and tick_size) else None
        swap_long = data.get("swap_long") or 0.0
        swap_short = data.get("swap_short") or 0.0
        if comm_spec.get("per_lot", 0.0) > 0 and value_per_price is None:
            logger.warning("%s: comisión per_lot configurada pero sin tick_value/tick_size "
                            "(caché viejo) → comisión NO aplicada. Regenerar con --refresh.", symbol)

        # --- Precómputo vectorizado M5 (una sola vez): ATR14/ADX14/MA20(ATR)/swing15.
        # El valor en la posición i de estas series NO depende del tamaño de la
        # ventana usada para calcularlas (son rolling LOCALES), así que sirve tanto
        # para el dimensionado al abrir (executor: fetch 50 velas) como para la
        # gestión (executor: fetch 200 velas) sin recomputar nada por barra.
        atr_m5_series = pta.atr(df_m5["high"], df_m5["low"], df_m5["close"], length=14)
        ma_atr_m5_series = atr_m5_series.rolling(20).mean()
        adx_m5_df = pta.adx(df_m5["high"], df_m5["low"], df_m5["close"], length=14)
        adx_m5_series = adx_m5_df["ADX_14"] if (adx_m5_df is not None and "ADX_14" in adx_m5_df) else None
        low15_series = df_m5["low"].rolling(15).min()
        high15_series = df_m5["high"].rolling(15).max()

        # --- Índices "as-of" vectorizados por timeframe (nº de velas de tf <= tiempo
        # de cada barra M5) — mismo principio que la ventana deslizante acotada de
        # PSTBacktestEngine.run() (engine.py ~308-332), calculado de una vez para
        # toda la serie en vez de recomputar un booleano completo barra a barra.
        end_idx = {}
        for tf, tdf in tf_dfs.items():
            if tf == "m5":
                continue
            end_idx[tf] = np.searchsorted(tdf["time"].values, m5_times, side="right")

        classifier = RegimeClassifier()
        macro_filter = MacroTrendFilter()

        period_start = df_m5.iloc[self.warmup_bars]["time"]
        period_end = df_m5.iloc[-1]["time"]
        res = BacktestResult(strat_name, symbol, period_start, period_end)

        pos = None
        cooldown_until = None

        for i in range(self.warmup_bars, n):
            bar = df_m5.iloc[i]
            t = bar["time"]

            # Cierre por hueco de datos (fin de semana/EOD) con posición abierta —
            # ver "Infidelidades conocidas". Igual patrón que faithful_range_engine.py:
            # cierra al close de ESTA barra sin comprobar su propio SL/TP.
            if pos is not None and self.session_gap_secs > 0 and i + 1 < n:
                gap = (df_m5.iloc[i + 1]["time"] - t).total_seconds()
                if gap > self.session_gap_secs:
                    self._close_remaining(pos, float(bar["close"]), t, "SESSION_END", pt, swap_long, swap_short)
                    res.trades.append(pos)
                    pos = None
                    continue

            atr5 = float(atr_m5_series.iloc[i]) if i < len(atr_m5_series) else 0.0
            atr5 = atr5 if atr5 == atr5 else 0.0
            adx5 = 0.0
            if adx_m5_series is not None and i < len(adx_m5_series):
                v = float(adx_m5_series.iloc[i])
                adx5 = v if v == v else 0.0

            if pos is not None:
                closed = self._manage(pos, bar, atr5, adx5, pt, profile, strat_type, swap_long, swap_short)
                if closed:
                    if pos.pnl_r < 0 and self.cooldown_secs > 0:
                        cooldown_until = t + pd.Timedelta(seconds=self.cooldown_secs)
                    res.trades.append(pos)
                    pos = None
                continue

            # --- flat: evaluar entrada ---
            if cooldown_until is not None and t < cooldown_until:
                continue

            mtf_slice = {}
            for tf, tdf in tf_dfs.items():
                end = (i + 1) if tf == "m5" else int(end_idx[tf][i])
                if end <= 0:
                    continue
                start = max(0, end - self.MTF_SLICE_LOOKBACK)
                mtf_slice[tf] = tdf.iloc[start:end]

            mode, _adx_h1 = classifier.classify(mtf_slice.get("h1"))

            sig = await strategy.calculate_signal(mtf_slice, mode, symbol=symbol, spread_dist=spread)
            score = sig.get("score", 0)
            entry = sig.get("entry", 0)
            atr_sig = sig.get("atr", 0.0)
            if entry == 0 or atr_sig <= 0 or score < self.extra_score_floor:
                continue

            # --- filtro macro (orchestrator.py ~520-544) ---
            if self.macro_mode == "on":
                macro = macro_filter.analyze(mtf_slice)
                ok, adj = macro.allows_direction(int(entry), strat_type)
                if not ok:
                    continue
                score = max(0, min(100, score + adj))

            # --- gate de régimen H1 (orchestrator.py ~592-619, ver HALLAZGO #2) ---
            if self.regime_gate == "on" and not self._regime_allowed(strat_type, mode):
                continue

            pos = self._open(sig, bar, symbol, strat_name, mode, a_class,
                              half_spread, pt, value_per_price, i,
                              atr_m5_series, ma_atr_m5_series, low15_series, high15_series,
                              profile)
            # pos None = bloqueado por spread o comisión (guards finales del executor)

        if pos is not None:  # abierto al final del periodo
            last_close = float(df_m5.iloc[-1]["close"])
            self._close_remaining(pos, last_close, period_end, "EOD", pt, swap_long, swap_short)
            pos.result = "OPEN"
            res.trades.append(pos)

        return res

    # ------------------------------------------------------------------ apertura
    def _open(self, sig, bar, symbol, strat_name, mode, a_class, half_spread, point,
              value_per_price, i, atr_m5_series, ma_atr_m5_series, low15_series, high15_series,
              profile):
        direction = int(sig["entry"])
        raw = float(bar["close"])
        fill = raw + direction * half_spread     # medio spread en la entrada
        meta = sig.get("metadata", {}) or {}
        atr_sig = float(sig.get("atr", 0.0))      # ATR de la estrategia en su active_tf

        # --- Capa 1 (orquestador): sl_dist preliminar → atr_unit para el executor.
        # (len(symbol) > 3 or "500" in symbol or "30" in symbol) es prácticamente
        # SIEMPRE cierto para símbolos reales (EURUSD=6 chars, XAUUSD=6, US100.cash=10,
        # BTCUSD=6...) — se implementa la condición completa igualmente por fidelidad.
        sl_mult_o = 3.5 if (len(symbol) > 3 or "500" in symbol or "30" in symbol) else 2.5
        if mode == "VOLATILE":
            sl_mult_o *= 1.5
        sl_dist_orch = max(atr_sig * sl_mult_o, fill * 0.0015)
        atr_unit = sl_dist_orch / 2.5

        # HALLAZGO CRÍTICO #1: el TP técnico de la estrategia se descarta en símbolos
        # INDEX salvo index_technical_tp=True (A/B, no es lo que corre en real).
        tp_price_strat = float(sig.get("tp_price", 0) or 0)
        inject_tp = tp_price_strat > 0 and (a_class != "INDEX" or self.index_technical_tp)
        target_tp = tp_price_strat if inject_tp else 0.0

        sl_m = float(profile["sl_mult"])
        tp_m = float(profile["tp_mult"])
        min_rr = float(profile["min_rr"])

        # --- Capa 2 (executor.execute_trade) ---
        current_atr = self._at(atr_m5_series, i)
        ma_atr = self._at(ma_atr_m5_series, i)
        if current_atr <= 0:
            current_atr = atr_unit           # fallback razonable (espejo executor línea ~144)
            ma_atr = current_atr
        low15 = self._at(low15_series, i, nan_ok=True)
        high15 = self._at(high15_series, i, nan_ok=True)

        # 1) SL estructural: swing 15 velas M5, banda 1.5 ≤ dist/ATR_M5 ≤ sl_m·1.5
        sl = 0.0
        if current_atr > 0:
            if direction == 1 and low15 == low15:
                cand = low15 - 0.2 * current_atr
                dist_atr = (fill - cand) / current_atr
                if 1.5 <= dist_atr <= sl_m * 1.5:
                    sl = cand
            elif direction == -1 and high15 == high15:
                cand = high15 + 0.2 * current_atr
                dist_atr = (cand - fill) / current_atr
                if 1.5 <= dist_atr <= sl_m * 1.5:
                    sl = cand

        # 2) Override: SL estructural de la propia estrategia — GANA sin validar banda
        struct_sl = float(meta.get("target_price_sl", 0) or 0)
        if struct_sl > 0 and ((direction == 1 and struct_sl < fill) or (direction == -1 and struct_sl > fill)):
            sl = struct_sl

        # 3) Fallback: atr_unit · sl_m
        if sl == 0:
            sl = fill - direction * atr_unit * sl_m

        # 4) Suelo de seguridad 0.08%
        if abs(fill - sl) < fill * self.MIN_SL_PCT:
            sl = fill - direction * fill * self.MIN_SL_PCT

        # --- TP ---
        tp = 0.0
        if target_tp > 0 and ((direction == 1 and target_tp > fill) or (direction == -1 and target_tp < fill)):
            tp = target_tp
        if tp == 0:
            vol_ratio = (current_atr / ma_atr) if ma_atr > 0 else 1.0
            if vol_ratio > 1.2:
                tp_adj = min(1.5, vol_ratio)
            elif vol_ratio < 0.8:
                tp_adj = max(0.7, vol_ratio)
            else:
                tp_adj = 1.0
            tp = fill + direction * atr_unit * tp_m * tp_adj

        # --- auto-fix R:R (idéntico patrón a los otros dos motores) ---
        sl_dist = abs(fill - sl)
        tp_dist = abs(tp - fill)
        if sl_dist > 0 and tp_dist / sl_dist < min_rr:
            min_sl_allowed = max(current_atr * 1.0, fill * self.MIN_SL_PCT)
            ideal_sl_dist = tp_dist / min_rr
            if ideal_sl_dist >= min_sl_allowed:
                sl = fill - direction * ideal_sl_dist        # ceñir SL
            else:
                tp_dist = sl_dist * min_rr
                tp = fill + direction * tp_dist               # estirar TP
        sl_dist = abs(fill - sl)

        # --- guards finales ---
        if sl_dist <= 0 or (half_spread * 2.0) > sl_dist * 0.55:
            return None
        if not commission_guard_ok(a_class, fill, sl_dist):
            return None

        t = BacktestTrade(
            entry_time=bar["time"], exit_time=None, symbol=symbol, strategy=strat_name,
            direction=direction, entry_price=fill, sl_price=sl, tp_price=tp,
            score=abs(sig.get("score", 0)), atr=atr_sig,
        )
        t._R = sl_dist                # SL ORIGINAL fijo — la base del parcial a 1R
        t._rem = 1.0
        t._realized = 0.0
        t._partial = False
        t._half_spread = half_spread
        t._commission_r = commission_r(a_class, fill, sl_dist, value_per_price)
        return t

    # ------------------------------------------------------------------ gestión
    def _manage(self, t, bar, atr5, adx5, point, profile, strat_type, swap_long, swap_short) -> bool:
        """Gestiona la posición en la barra. Devuelve True si se cerró del todo.
        Orden idéntico a manage_active_trades: A.Breakeven -> B.Trailing ->
        (C.salida dinámica N/A) -> D.Parcial -> E.Timeout."""
        h, l, c = float(bar["high"]), float(bar["low"]), float(bar["close"])
        d, fill, R = t.direction, t.entry_price, t._R

        # 1) SL / TP intrabar (SL antes que TP, conservador) — corta el resto.
        if d == 1:
            if l <= t.sl_price:
                self._close_remaining(t, t.sl_price, bar["time"], "SL", point, swap_long, swap_short); return True
            if h >= t.tp_price:
                self._close_remaining(t, t.tp_price, bar["time"], "TP", point, swap_long, swap_short); return True
        else:
            if h >= t.sl_price:
                self._close_remaining(t, t.sl_price, bar["time"], "SL", point, swap_long, swap_short); return True
            if l <= t.tp_price:
                self._close_remaining(t, t.tp_price, bar["time"], "TP", point, swap_long, swap_short); return True

        new_sl = t.sl_price

        # A. Breakeven — extremo favorable de la vela (h/l), como faithful_engine.py.
        if int(profile.get("use_breakeven", 0)) == 1 and atr5 > 0:
            is_sl_at_be = (d == 1 and new_sl >= fill) or (d == -1 and new_sl <= fill and new_sl > 0)
            safe_be_mult = max(2.5, float(profile.get("be_mult", 2.0)))
            reached_be = (h >= fill + atr5 * safe_be_mult) if d == 1 else (l <= fill - atr5 * safe_be_mult)
            if reached_be and not is_sl_at_be:
                new_sl = fill + d * 1 * point       # padding 1 pt (no-scalper)

        # B. Trailing — usa el CLOSE como "precio actual" (igual que faithful_engine.py:
        # el umbral de armado y la distancia se calculan sobre c, no sobre el extremo).
        if int(profile.get("use_trailing", 0)) == 1 and atr5 > 0:
            safe_ts_mult = max(2.0, float(profile.get("ts_mult", 2.5)))
            profit_c = d * (c - fill)
            if profit_c > atr5 * safe_ts_mult:
                mult = 1.5 if adx5 > 40 else float(profile.get("ts_mult", 2.5))
                trail_sl = c - d * mult * atr5
                if d == 1 and trail_sl > new_sl:
                    new_sl = trail_sl
                elif d == -1 and (trail_sl < new_sl or new_sl == 0):
                    new_sl = trail_sl

        # D. Cierre parcial 50% a +1R contra el SL ORIGINAL (t._R fijo) — extremo
        # favorable de la vela. SOBRESCRIBE new_sl sin condición (igual que el bloque
        # D real, que no lee lo que puso A/B este mismo ciclo).
        if self.partial_enabled and not t._partial and R > 0:
            reached = (h >= fill + self.partial_r * R) if d == 1 else (l <= fill - self.partial_r * R)
            if reached:
                hs = t._half_spread
                t._realized += self.partial_pct * ((self.partial_r * R - hs) / R)
                t._rem -= self.partial_pct
                t._partial = True
                new_sl = fill + d * 2 * point       # padding 2 pt (no-scalper)
                t.exit_reason = "PARTIAL_1R"

        # E. Timeout de estancamiento — MomentumPulse (TREND) exento siempre.
        if self.timeout_secs > 0 and strat_type != "TREND":
            age = (bar["time"] - t.entry_time).total_seconds()
            if age > self.timeout_secs:
                self._close_remaining(t, c, bar["time"], "TIMEOUT", point, swap_long, swap_short)
                return True

        if abs(new_sl - t.sl_price) > point:
            t.sl_price = new_sl

        return False

    def _close_remaining(self, t, price, time, reason, point, swap_long, swap_short):
        """Cierra la fracción remanente. Medio spread de salida (ajuste de precio,
        algebraicamente igual a cost_model.exit_spread_r) + comisión round-turn
        (cost_model.commission_r, cobrada una vez) + swap/overnight
        (cost_model.swap_r, NUEVO respecto a los motores viejos)."""
        d, fill, R = t.direction, t.entry_price, t._R
        hs = t._half_spread
        eff = price - d * hs
        r_leg = (d * (eff - fill) / R) if R > 0 else 0.0
        t._realized += t._rem * r_leg
        t._realized -= getattr(t, "_commission_r", 0.0)
        if point and R > 0:
            nights = nights_held(t.entry_time, time)
            if nights > 0:
                t._realized -= swap_r(d, nights, swap_long, swap_short, point, R)
        t._rem = 0.0
        t.exit_price = price
        t.exit_time = time
        t.pnl_r = t._realized
        if not t.exit_reason or reason != "EOD":
            t.exit_reason = reason
        t.result = "WIN" if t.pnl_r > 0 else "LOSS"

    @staticmethod
    def _at(series, i, nan_ok=False):
        """Valor de una serie precomputada en la posición i, NaN-safe."""
        if series is None or i >= len(series):
            return float("nan") if nan_ok else 0.0
        v = float(series.iloc[i])
        if v != v:  # NaN
            return v if nan_ok else 0.0
        return v

    @staticmethod
    def _infer_digits(df_m5):
        try:
            px = float(df_m5["close"].iloc[-1])
            if px >= 100:
                return 2
            if px >= 10:
                return 3
            return 5
        except Exception:
            return 5
