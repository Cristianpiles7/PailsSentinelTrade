# Plan v3 — Mejorar las operaciones por símbolo

Fecha: 2026-10-30. Estado: **Fases 0, 1 y 2 implementadas el 2026-10-30** (decisiones 1-3 aprobadas: toda la cripto a simulación,
M1 y M5 prohibidos de golpe para el stop, tope 3 por lado y clase / 5 por lado). Fase 3: simulador ejecutado el 2026-10-30 (`simulacion_salidas.py`; sobre 271 operaciones, A −0,10 R, A0 +0,03, B +0,10, C +0,10; sesgo: las velas no llevan spread en A0/B/C). **Regla C implementada en vivo el 2026-10-30** (`gestion_salidas.py`, la ejecuta el revisor; flag `salidas_reglas` en `llm_revisor.json`; el agente solo puede cerrar con invalidación en H1/H4/D1).
Fase 4: brazo base montado (`brazo_base.py`) y backtest de 90 días ejecutado: R medio −0,07 en 6.659 operaciones; resultado abajo en §3 fase 4. **Fases 5 y 6 implementadas el 2026-10-30** (`llm_mejora_prompt.py`, `niveles.py`; ver «Estado de la implementación» al final de cada fase).
Copia de las configs anteriores en `.agents/backup_plan_v3/`. El historial de deals de MT5 solo llega a ~29-oct 23:00: la comisión, la salida y el MAE/MFE solo existen para 36 de las 271 operaciones.
Complementa a [PLAN_AGENTES_V2.md](PLAN_AGENTES_V2.md): aquel definía la flota; este se centra en **cómo opera cada símbolo**
(cuándo entra, con qué stop, cómo sale, qué símbolos operan y cómo se mejora el criterio).

---

## Resumen en 6 líneas

1. Primera semana real (23–30 oct): **269 operaciones, 31 % de aciertos, factor de beneficio 0,82, −986 €**. De media se pierden **−0,09 R por operación**.
2. Esa pérdida media es pequeña: está a la altura de los costes. **El problema principal es la estructura de la operación, no la dirección.**
3. Las operaciones basadas en **M15 ganan (+0,20 R de media)**. Las de **M1 (−0,66 R) y M5 (−0,09 R) pierden**, y son el 85 % del total.
4. Los agentes corren con reloj de 1 minuto y sin filtro: **56.500 llamadas al modelo en una semana para 544 señales** (el 98,6 % son `hold`).
5. El bucle de mejora del criterio aprueba cambios con **2 operaciones** de muestra. Eso es ajustarse al ruido.
6. El plan: medir bien → arreglar coste y tamaño del stop → filtrar llamadas → salidas por reglas → comparar contra un brazo sin LLM → mejorar criterios con disciplina.

---

## 1. Dónde estamos (datos de la semana)

Fuentes: tabla `trades` (las 269 operaciones `LLM_TRADER`), las BBDD de cada agente (`.agents/data/llm_trader_Agente_*.db`)
y `llm_decisions` de `pst_trading.db`. Cada operación se ha emparejado con su decisión por ticket (269 de 269).

> **Aviso:** MT5 no estaba accesible desde Python al preparar esto (va dentro del .exe). No he podido confirmar si el
> campo `profit` de `trades` incluye comisión y swap, ni el motivo real de cada salida. La Fase 0 lo resuelve.

### 1.1 Por clase de activo

| Clase | Símbolos | Neto | Lectura |
|---|---|---|---|
| Índices | US100 +352, US500 +58, UK100 +49, EU50 +17, US30 −40, GER40 −239 | **+197 €** | La única clase en positivo. GER40 subió +3,5 % en la semana y el agente vendió 8 de 13 veces. |
| Acciones EE. UU. | BA +30, MSFT +5, META +2, NVDA −9, INTC −31 | −4 € | Muy pocas operaciones (10). Sin conclusión. |
| Forex | GBPUSD +190, EURUSD +101, AUDUSD −104, USDCAD −116, USDJPY −305 | −299 € | Mixto. Los pares de USD contra divisa de materias primas y JPY pierden. |
| Materias | XAGUSD −146 (0 de 8) | −146 € | 8 ventas, 8 pérdidas. |
| Cripto | XLM +100, SOL +97, BTC −1, LTC −7, XRP −23, DOT −36, ADA −58, UNI −110, ETH −127, LNK −571 | **−735 €** | La que más pierde. LNKUSD sola es el 58 % de la pérdida total. |

### 1.2 Qué características tienen las operaciones que ganan

| Corte | Grupo | Operaciones | Neto | R medio |
|---|---|---|---|---|
| **Marco del stop** | M1 | 16 | −424 € | **−0,66** |
| | M5 | 213 | −709 € | −0,09 |
| | M15 | 34 | +231 € | **+0,20** |
| | H1 | 6 | −84 € | −0,29 |
| **Objetivo (tp_r)** | ≤ 1,5 R | 109 | −822 € | **−0,19** |
| | 1,5–2,5 R | 160 | −164 € | −0,02 |
| | > 2,5 R | 0 | — | el agente nunca lo elige |
| **Confianza** | < 0,6 | 269 | −986 € | todas las ejecutadas |

Las muestras son pequeñas (34 en M15). Son **hipótesis a confirmar**, no conclusiones. Pero van en la dirección que predice
el coste: con stops de M1/M5 la distancia es corta, el lote sale grande y el spread y la comisión pesan más sobre el R.

### 1.3 Cómo terminan las operaciones

- Pérdidas: 185. Llegan a −0,9 R o peor: 83. **Se cortan antes: 102** (49 entre −0,9 y −0,3 R; 53 por encima de −0,3 R).
- Ganadoras: 83. Llegan a 1,5 R o más: 35. **Se cortan antes: 48.**
- El agente ejecutó **98 cierres a mano** en la semana. El dato del 22-oct ya decía que los cierres a mano costaban dinero.
  Con la mitad de las salidas fuera del SL/TP, el sistema real no es "entrada + SL + TP", es "entrada + lo que decida el agente en cada minuto".

### 1.4 Coste y uso del modelo

- Los 36 agentes tienen `timeframe: M1`, `sin_filtro: true`, Haiku con esfuerzo `low`.
- **56.557 llamadas** en la semana. 55.786 `hold`, 504 `sell`, 40 `buy`, 104 `close`, 123 errores.
- 858 M de tokens de caché leídos. Coste de referencia del CLI: 316 USD (en suscripción cuenta la cuota, no el dólar).
- Señales que no se ejecutaron: 113 por enfriamiento, **~110 por margen** (demasiadas posiciones a la vez o lotes muy grandes),
  42 rechazadas por el broker (`order_check` / retcode), 7 por spread.

### 1.5 Sesgo vendedor

240 de 269 operaciones son ventas (89 %). La semana fue bajista en la mayoría de símbolos (US100, cripto y EURUSD
terminaron por debajo de sus EMA de H4), así que **el sesgo sigue en parte al mercado y no es un fallo de cálculo.**
Dos riesgos:

- El sistema **no se ha probado nunca en una semana alcista**. Las reglas que el bucle de mejora ha añadido a los criterios
  (GOOG, AVGO, INTC, XAUUSD, FRA40) son reglas de "vender en régimen bajista". Si el mercado gira, esos símbolos se quedan sin compras.
- GER40 subió y siguió vendiendo: el caso que hay que vigilar.

---

## 2. Qué falla, por orden de impacto

| # | Problema | Evidencia | Impacto |
|---|---|---|---|
| 1 | **Stops demasiado cortos** (M1/M5) para el coste de cada operación | M1 −0,66 R, M5 −0,09 R, M15 +0,20 R | Alto: es la diferencia entre perder y quedar en cero |
| 2 | **Salidas sin regla**: el agente cierra a mano cada minuto | 98 cierres del agente; 102 de 185 pérdidas y 48 de 83 ganadoras cortadas antes | Alto: corta ganadoras y no deja medir la entrada |
| 3 | **Reloj M1 sin filtro**: el modelo se llama 60 veces por hora para decir `hold` | 98,6 % de `hold`; 56.500 llamadas | Alto en cuota; medio en resultado (más ruido, más cierres) |
| 4 | **Objetivos cortos** (tp_r ≤ 1,5) | −0,19 R frente a −0,02 R | Medio |
| 5 | **Mejora del criterio con muestras de 2 operaciones** | `mejora_prompt_config.json`: `min_operaciones: 2` | Medio: los criterios se ajustan al ruido de una semana |
| 6 | **Exposición concentrada** | ~110 rechazos por margen; 89 % ventas a la vez | Medio: una vuelta del mercado pega a toda la flota |
| 7 | **La confianza del modelo no informa** | Todas las operaciones ejecutadas tienen confianza < 0,6 | Bajo hoy; impide filtrar por confianza |
| 8 | **Datos incompletos** | Sin motivo de salida, sin R real, sin MAE/MFE, `time_out` con desfase de reloj | Bloquea todo lo demás: sin esto no se puede comparar |

---

## 3. El plan, por fases

Cada fase tiene un criterio de paso. **No se pasa a la siguiente sin cumplirlo.** Los cambios de configuración (Fase 1)
son reversibles desde la app; los de código van con revisión.

### Fase 0 — Medir bien (1–2 días, sin coste de cuota)

Objetivo: que cada operación tenga una fila completa y comparable.

- Nueva tabla **`llm_operaciones`** en `pst_trading.db`, una fila por posición cerrada, rellenada desde MT5 (`history_deals_get`):
  `ticket, symbol, clase, agente, lado, marco_stop, sl_atr_mult, tp_r, riesgo_eur, bruto, comision, swap, neto, r_real,
  motivo_salida (SL / TP / agente / revisor / sesión), minutos, mae_r, mfe_r, criterio_version, prompt_version, regimen_h1_h4`.
- **MAE/MFE** (cuánto fue en contra y a favor como máximo, en R) con velas M1 entre la entrada y la salida. Esto dice si los
  stops están mal puestos (MAE) y si los objetivos se quedan cortos (MFE).
- Rellenar hacia atrás las 269 operaciones de esta semana.
- Un informe `python PST_LLM/informe_operaciones.py` con los cortes de la §1.2 (marco, tp_r, salida, clase, hora, régimen).

**Paso a la Fase 1:** las 269 operaciones tienen motivo de salida, comisión y R real. Confirmar o corregir las cifras de la §1.

### Fase 1 — Arreglos de configuración (1 día, reversibles)

Todo esto se cambia en ficheros de configuración o constantes, sin tocar la lógica.

| Cambio | Dónde | De → a | Por qué |
|---|---|---|---|
| Reloj del agente | `llm_trader_Agente_*.json` → `timeframe` | M1 → **M5** | Una vela de M1 no aporta información nueva para un stop de M15. Divide las llamadas entre 5. |
| Marcos para el stop | `DECISION_SCHEMA.timeframe` en `llm_trader.py` | M1…D1 → **M15, H1, H4** | M1 y M5 pierden; M15 gana. M5 sigue en el snapshot para afinar la entrada. |
| Objetivo mínimo | `TP_R` | (1,0, 4,0) → **(1,8, 4,0)** | tp_r ≤ 1,5 es el peor grupo. Con 31 % de aciertos hace falta ≥ 2,2 R de media para no perder. |
| Mínimo antes de cerrar a mano | `min_cierre_min` | 30 → **90** min (y con stop en M15 ya no hay prisa) | Reduce los cierres impulsivos mientras llega la Fase 3. |
| Muestra mínima del bucle de mejora | `mejora_prompt_config.json` → `min_operaciones` | 2 → **20** | Con 2 operaciones cualquier patrón es casualidad. |
| Cripto | rotación | activa → **simulación** (sigue decidiendo, no envía órdenes) | −735 € en la semana. Se sigue midiendo gratis en dinero; vuelve a demo si pasa el criterio de la §4. |

**Paso a la Fase 2:** 3 días de mercado con los cambios. Llamadas por día bajan ≥ 70 %. Ningún error nuevo en el log.

### Fase 2 — Coste y tamaño del stop (código, 1–2 días)

Hoy la única regla de coste es el spread frente al stop (`MAX_SPREAD_SOBRE_STOP = 0,15`). **Falta la comisión.**

- En `calcular_orden`: calcular el **coste total en R** = (spread + comisión ida y vuelta por lote, pasada a precio) / distancia del stop.
  La comisión por lote ya la mide `rotacion._comision_r`. Rechazar si el coste total supera **10 % del R**.
- **Stop mínimo por clase** en ATR de M15 (no del marco que elija el agente): evita que vuelva a stops de ruido con otro nombre.
- **Tope de exposición** (antes de abrir): como mucho **3 posiciones en el mismo lado por clase** y **5 en el mismo lado en total**.
  Sustituye a los rechazos por margen (que hoy llegan tarde y al azar) por una regla clara. El agente ya ve `cartera`; esto lo hace obligatorio.
- Registrar en `llm_operaciones` el coste en R de cada orden, para seguir el dato semana a semana.

**Paso a la Fase 3:** ningún rechazo por margen en 3 días; coste medio por operación ≤ 10 % del R.

### Fase 3 — Salidas por reglas (código, 2–3 días)

Hoy la mitad de las salidas las decide el agente en cada vela. Se sustituye por reglas fijas, iguales para todos, y se mide:

- **A** (actual): SL + TP + cierre del agente/revisor.
- **B** (reglas): SL + TP, y a **+1 R se mueve el stop a entrada** (break-even). Sin cierre a mano salvo invalidación concreta en H1.
- **C** (reglas): como B, más **cierre del 50 % a +1,5 R** y el resto con stop a +0,5 R.

Cómo comparar sin esperar semanas: con las velas M1 de MT5 y las entradas reales de la Fase 0, **simular B y C sobre las mismas operaciones**
(misma entrada, distinta salida). Es barato y no gasta cuota. Lo que gane en esa simulación pasa a demo; A queda como referencia.

Si B o C gana: el agente deja de poder cerrar salvo invalidación en H1 o superior, y el revisor solo actúa en esos casos.

**Paso a la Fase 4:** la regla elegida mejora el R medio frente a A en la simulación sobre ≥ 150 operaciones.

### Fase 4 — Brazo base sin LLM (el control que nunca se montó)

El plan v2 (§8) ya pedía comparar contra "reglas simples sin LLM". No se hizo. Ahora es más fácil: **la mayoría de criterios
aprobados ya son reglas mecánicas** (por ejemplo TSLA: "compra solo si H1 y H4 por encima de EMA200 y EMA20 > EMA50, en M15 con
px_vs_ema20_atr entre −0,5 y +0,7 y RSI 40–60"). Una regla así se puede programar y probar en el histórico gratis.

- Programar la regla común a los criterios (**retroceso a la EMA20 de M15 a favor del régimen H1+H4**) como estrategia determinista.
- Pasarla por el backtest fiel (`PST_Core/backtesting/faithful_engine.py`) con el coste real por símbolo, en 60–90 días.
- Correrla en **simulación en paralelo** al agente, en los mismos símbolos.

Tres resultados posibles, y qué hacer con cada uno:

| Resultado | Decisión |
|---|---|
| La regla sola iguala o supera al agente | El LLM no aporta en la entrada. Se usa como **veto** (solo puede decir "no" a una señal de la regla), no como generador. Ahorra el 90 % de la cuota. |
| El agente supera a la regla de forma clara | El LLM aporta. Se mantiene y se invierte en él (Sonnet en los mejores símbolos, mejor snapshot). |
| Ninguno gana | Volver a la §4: apagar o recortar. |

**Resultado del backtest (2026-10-30, `python PST_LLM/brazo_base.py`, 36 símbolos, 90 días, stop 1,5 ATR M15, objetivo 2 R, netos de spread y comisión):**
la regla sola da **−0,07 R** (salida B) en 6.659 operaciones; ninguna clase es claramente positiva (acciones ≈ 0, materias ≈ 0, índices −0,07, forex −0,12, cripto −0,13).
En la última semana: regla −0,10 R (371 ops) frente al agente −0,10 R (271 ops). Lectura: ni la regla ni el agente (versión anterior) generan edge; no hay evidencia de que el LLM aporte en la entrada.
Es el caso «ninguno gana» de la tabla: seguir con la §4, y repetir la comparación con el agente ya reconfigurado (fases 1-2) antes de decidir veto o apagado.
**Sombra en vivo:** `brazo_sombra.py` (lo actualiza el revisor por lotes) guarda las operaciones hipotéticas de la regla en `llm_brazo_base`; `python PST_LLM/brazo_sombra.py --comparar` la enfrenta al agente por clase.

**Paso a la Fase 5:** 4 semanas de comparación, o ≥ 100 operaciones por brazo.

### Fase 5 — Mejora del criterio con disciplina

El bucle (`llm_mejora_prompt.py` + `validacion_propuesta.py`) es buena idea, pero hoy aprueba demasiado rápido. Reglas nuevas:

- **Muestra mínima 20 operaciones** cerradas con el criterio activo (Fase 1).
- **Validación fuera de muestra obligatoria** (`--fuera-de-muestra`) antes de que la propuesta llegue a la bandeja. Sin veredicto "mejora", no se avisa.
- **Simétrica**: si la propuesta añade una regla de venta, debe decir qué hace el agente en régimen alcista. El vigía de sesgo (`sesgo.py`)
  se pasa a la propuesta y una propuesta que deja al símbolo sin uno de los dos lados se marca.
- **Periodo de prueba de 2 semanas** tras aprobar. Si el R medio cae frente al criterio anterior, se **revierte solo** (y avisa).
- Un **cambio a la vez por símbolo**: hoy TSLA tiene 3 versiones en 3 días; así no se sabe qué funcionó.

**Implementado (2026-10-30):** muestra mínima 20 (suelo duro en código y en el panel); hold-out de 48 h y validación fuera de muestra
obligatoria (sin veredicto «mejora» la propuesta se descarta); simetría (descarta si deja sin un lado que cubría o que el vigía de sesgo
echa en falta, y el revisor debe explicar `lado_contrario`); periodo de prueba de 14 días (28 si hay < 5 operaciones) con reversión
automática si el R medio cae más de 0,05 frente al anterior y aviso por Telegram; un cambio a la vez por símbolo; el bucle se detiene si se
revierte más de la mitad (≥ 4 evaluados). Limitación: los holds contrafactuales y el sesgo que ve el revisor no respetan el hold-out.

### Fase 6 — Qué símbolos operan

La rotación (`rotacion.py`) ya puntúa coste, tendencia, volatilidad e historial. Se cambian tres cosas:

- **Tres niveles por símbolo**: *operativo* (demo), *en prueba* (simulación) y *banquillo* (sin agente). Los símbolos bajan y suben
  de nivel con reglas, no se quitan y ponen cada 15 minutos.
- **Evidencia mínima para subir a operativo**: ≥ 20 operaciones en simulación con R medio > 0 y coste ≤ 10 % del R.
- **Bajar a simulación**: R medio < −0,15 tras ≥ 20 operaciones, o 3 semanas seguidas en negativo. Sigue midiendo, sin arriesgar.

La clase cuenta más que el símbolo: con 10–25 operaciones por símbolo, el ruido manda. Las decisiones de subir o bajar se toman
**primero por clase** (índices, acciones, forex, cripto, materias) y solo después por símbolo.

---

**Implementado (2026-10-30):** `niveles.py` (tablas `llm_niveles` y `llm_niveles_log`). Reglas y umbrales en las constantes del módulo;
clase primero, símbolo después; evidencia contada desde el inicio de cada nivel. Sembrado: los 26 símbolos no cripto en `operativo`,
cripto (clase y símbolos) en `prueba`; todo símbolo nuevo entra en `prueba`. `registro.activar` fuerza simulación si clase o símbolo no están
en `operativo`; la rotación evalúa los niveles en cada pasada de ranking y no vuelve a poner el banquillo. `python PST_LLM/niveles.py --estado`.

## 4. Criterios de corte (se fijan ahora, antes de ver más datos)

| Medida | Umbral | Qué se hace |
|---|---|---|
| R medio de la flota tras Fases 1–3 | < 0 en 4 semanas y ≥ 150 operaciones | Recortar a índices, la única clase en positivo |
| R medio de una clase | < −0,15 tras ≥ 40 operaciones | La clase entera a simulación |
| Agente frente a brazo base (Fase 4) | No lo supera en 4 semanas | LLM pasa a veto, no a generador |
| Coste por operación | > 15 % del R de media | Subir el stop mínimo de esa clase |
| Llamadas al modelo por operación | > 50 | Endurecer el filtro previo |
| Propuestas de criterio revertidas | > 50 % | Parar el bucle de mejora y revisarlo |

Lo que **no** cuenta como éxito: una semana buena, un símbolo que gana mucho solo, o un porcentaje de aciertos alto sin R positivo.

---

## 5. Qué no hacer

- **No subir el riesgo por operación** (0,5 %) hasta tener R positivo en ≥ 150 operaciones.
- **No pasar a Sonnet** para "mejorar" las entradas antes de la Fase 4. Si la regla sola gana, el modelo más caro no arregla nada.
- **No añadir símbolos** mientras haya clases enteras en negativo.
- **No sacar conclusiones por símbolo** con menos de 20 operaciones (LNKUSD −571 € son 25 operaciones: es mucho dinero y poca muestra).
- **No cambiar varias cosas en el mismo símbolo a la vez**: si cambian marco, objetivo y criterio el mismo día, no se sabe qué funcionó.

---

## 6. Decisiones que necesito de ti

1. **Cripto a simulación** (Fase 1): ¿de acuerdo, o prefieres solo LNKUSD, UNIUSD y ETHUSD?
2. **Prohibir stops en M1/M5** (Fase 1): ¿de golpe, o primero M1 y en una semana M5?
3. **Tope de exposición** (Fase 2): ¿3 por lado y clase, 5 por lado en total, te parece bien?
4. **Salidas por reglas** (Fase 3): si la simulación da ganador, ¿se quita al agente la capacidad de cerrar salvo invalidación en H1?
5. **Orden**: propongo Fase 0 → 1 → 2 esta semana y Fase 3 la siguiente. La Fase 4 puede empezar en paralelo (es backtest, no gasta cuota).

---

## Anexo — Resultados por símbolo (23–30 oct)

| Símbolo | Ops | Aciertos | Neto € | Compras / ventas |
|---|---|---|---|---|
| US100.cash | 23 | 43 % | +352,0 | 2 / 21 |
| GBPUSD | 22 | 36 % | +189,8 | 4 / 18 |
| EURUSD | 21 | 43 % | +100,9 | 0 / 21 |
| XLMUSD | 11 | 36 % | +100,2 | 0 / 11 |
| SOLUSD | 13 | 46 % | +96,8 | 0 / 13 |
| US500.cash | 19 | 32 % | +58,3 | 4 / 15 |
| UK100.cash | 2 | 50 % | +49,2 | 0 / 2 |
| BA | 2 | 50 % | +29,9 | 0 / 2 |
| EU50.cash | 4 | 25 % | +16,9 | 0 / 4 |
| MSFT | 1 | 100 % | +4,8 | 0 / 1 |
| META | 3 | 67 % | +1,5 | 0 / 3 |
| BTCUSD | 13 | 38 % | −0,6 | 0 / 13 |
| LTCUSD | 2 | 50 % | −6,8 | 0 / 2 |
| NVDA | 2 | 50 % | −9,2 | 0 / 2 |
| XRPUSD | 8 | 12 % | −23,1 | 0 / 8 |
| INTC | 2 | 0 % | −31,1 | 0 / 2 |
| DOTUSD | 1 | 0 % | −36,3 | 0 / 1 |
| US30.cash | 12 | 42 % | −39,8 | 2 / 10 |
| ADAUSD | 11 | 27 % | −57,5 | 0 / 11 |
| USDCHF | 3 | 0 % | −65,1 | 1 / 2 |
| AUDUSD | 4 | 25 % | −103,6 | 1 / 3 |
| UNIUSD | 16 | 38 % | −110,1 | 0 / 16 |
| USDCAD | 4 | 25 % | −115,5 | 2 / 2 |
| ETHUSD | 15 | 27 % | −127,4 | 0 / 15 |
| XAGUSD | 8 | 0 % | −146,2 | 0 / 8 |
| GER40.cash | 13 | 8 % | −238,5 | 5 / 8 |
| USDJPY | 9 | 11 % | −304,5 | 4 / 5 |
| LNKUSD | 25 | 16 % | −570,9 | 1 / 24 |
| **Total** | **269** | **31 %** | **−985,7** | **29 / 240** |

Ganadora media +52,8 €, perdedora media −28,9 €, riesgo medio por operación 43 € (0,5 % de ~9.800 € de equity).
Por día: 23-oct +57, 24 +41, 25 −335, 26 −133, 27 −18, 28 −417, 29 −22, 30 (hasta las 11:30) −160.
