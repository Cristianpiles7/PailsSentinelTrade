# 📘 Sistema de estrategias nuevo — PST-MomentumPulse / PST-DivergenceReversion

Documentación de las dos estrategias construidas desde cero (2026-08-11) para
reemplazar a PrecisionScalping/RangeBreaker, que se pausaron por no lograr
rentabilidad sostenida en demo pese a meses de auditorías. Código: mira
`PST_Core/strategies/pst_momentum_pulse.py` y `pst_divergence_reversion.py`.

---

## Motor compartido (`PST_Core/ml/`)

Ambas estrategias usan las mismas dos piezas de infraestructura, para que nunca
diverjan entre lo que ven en vivo, en backtest y al entrenar el modelo:

- **`FeatureBuilder`** (`feature_builder.py`): calcula 27 features técnicos
  (RSI, ATR percentil, EMAs normalizadas por ATR, ADX/Choppiness, volumen
  relativo, patrones de vela, distancia a swing high/low, contexto H1, clase de
  activo y sesión horaria) siempre con el mismo código, tanto para el score en
  vivo como para entrenar el modelo.
- **`AdaptiveTFSelector`** (`tf_selector.py`): en vez de operar siempre en el
  mismo timeframe o esperar a que un timeframe superior confirme (lo que llega
  tarde a las reversiones), mide la volatilidad relativa actual (percentil de
  ATR) y elige un timeframe más rápido (M2/M3) cuando el mercado está más
  agitado de lo normal, o más lento (M15/M30) cuando está tranquilo. Ese
  timeframe elegido (`active_tf`) es el que usa la estrategia para su gate
  principal, sus indicadores y su estructura de SL/TP.
- **Modelo entrenable (`PST_Core/ml/blend.py` + `model_registry.py`)**: cada
  estrategia, tras calcular su score por reglas, intenta cargar un modelo
  LightGBM entrenado para (estrategia, clase de activo). Si existe y supera un
  umbral mínimo de fiabilidad (AUC ≥ 0.52 sobre ≥ 20 ejemplos de test — si no,
  se descarta aunque el archivo exista), combina 70% modelo / 30% reglas. Si no
  hay modelo o no pasa el umbral, el score es 100% reglas — nunca falla por
  falta de modelo. Reentrenamiento: manual, vía
  `python Tools/pst_train_ml.py --strategy <nombre> --symbols ... --days ...`.

---

## PST-MomentumPulse

**Tipo:** TREND · **Objetivo:** entrar a favor del impulso, no perseguir el
precio — busca el pullback dentro de una tendencia ya establecida.

**Gate obligatorio:** alineación de EMA 9/21/50 en el timeframe activo (9>21>50
= sesgo alcista, 9<21<50 = sesgo bajista). Sin alineación clara, no hay señal.

**Factores de score (sobre ese gate):**
| Factor | Qué mide | Puntos |
|---|---|---|
| Alineación EMA | Gate (ver arriba) | 30 |
| Pullback | Precio cerca de la EMA9 (no extendido) vs. persiguiendo el movimiento | ±20 |
| Momentum RSI | A favor de la dirección pero sin estar ya agotado (>75/<25) | ±15 |
| Calidad de tendencia | ADX/Choppiness del timeframe activo | ±15 |
| Volumen | Confirmación de participación (volumen > 1.1x media) | +10 |
| Anti-spike | Penaliza velas parabólicas (cuerpo > 3 ATR) | −35 |
| Spread | Penaliza spread caro relativo al ATR | −15 |

El filtro macro H1/H4 (tendencia mayor) ya se aplica a nivel de orquestador
para todas las estrategias — no se duplica aquí.

**SL/TP:** SL en el fractal de swing del timeframe activo (estructural, no un
múltiplo ATR fijo). TP en el borde de canal H1 opuesto, o un múltiplo ATR
amplio si no hay canal resoluble — pensado para dejar correr las ganadoras
(por eso tiene `use_trailing=1`/`use_breakeven=1` en su configuración, y una
excepción específica en el ejecutor para que el cierre automático a 30 min no
le aplique).

**Umbral de entrada actual:** 40 (score 0-100). Es una primera calibración
sobre muestra pequeña (ver comentario en el propio código) — al integrar el
modelo, el score pasó a reflejar una probabilidad real de acierto, y los
setups que superan el filtro de reglas solo ganan ~15-30% de las veces frente
al SL/TP con el que se entrenó. Pendiente de una calibración más sólida con
más historial.

---

## PST-DivergenceReversion

**Tipo:** RANGE · **Objetivo:** encontrar suelos/techos comparando el precio
contra el RSI (divergencias) — la idea de "adivinar" el giro que pediste.

**Gate obligatorio:** divergencia RSI/precio detectada en el timeframe activo
(`detect_divergence` en `tech_utils.py`): el precio hace un mínimo más bajo
mientras el RSI hace un mínimo más alto (divergencia alcista), o al revés
(divergencia bajista).

**Factores de score (sobre ese gate):**
| Factor | Qué mide | Puntos |
|---|---|---|
| Divergencia RSI | Gate (ver arriba) | 40 |
| Veto de momentum | Si la última vela cerrada va EN CONTRA de la reversión, anula la señal entera | veto (score 0) |
| Espacio RSI | Cuánto recorrido le queda al RSI antes de la zona extrema opuesta | ±15 |
| Confluencia de canal | Si el precio está además cerca del borde de un canal (soporte/resistencia dinámico), usando el motor de scoring `calculate_manual_score` | hasta ±40 (ponderado 40%) |
| Spread | Igual que MomentumPulse pero más permisivo | −10 |

**SL/TP:** SL justo detrás del pivote exacto que originó la divergencia
(estructural). TP en el borde opuesto del canal — si no hay canal resoluble,
un múltiplo ATR conservador (2.5x, más corto que MomentumPulse porque aquí el
objetivo es un nivel concreto, no dejar correr la operación).

**Umbral de entrada actual:** 50. Igual que MomentumPulse, primera
calibración — las divergencias genuinas son un setup menos frecuente que el
momentum (más selectivo por naturaleza), así que esta estrategia opera con
menos frecuencia y eso es esperado, no un fallo.

---

## Qué NO hacen (todavía)

- No tienen `filter_profile` configurable por símbolo desde el Matrix Editor
  (a diferencia de PrecisionScalping) — los parámetros de reglas están fijos
  en el código. Se puede añadir más adelante si hace falta ajustar por símbolo.
- El modelo entrenado hoy solo cubre 4 símbolos piloto (`US100.cash`,
  `US30.cash`, `XAUUSD`, `EURUSD`) — en el resto de símbolos ambas estrategias
  funcionan solo con reglas (el gate de calidad del modelo las protege de usar
  un modelo sin datos suficientes).
- `PST-DivergenceReversion` no tiene salida dinámica (`check_exit_signal`) —
  depende solo de SL/TP y de la gestión genérica del ejecutor.
