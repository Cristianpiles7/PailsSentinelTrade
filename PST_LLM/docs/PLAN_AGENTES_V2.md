# Plan v2 — Flota de agentes LLM (demo FTMO)

Estado: **propuesta, nada implementado.** Sustituye a la parte de agentes LLM de
[PLAN_AGENTES.md](PLAN_AGENTES.md) (aquel documento sigue siendo válido para el plano C de
investigación). Fecha: 2026-10-22.

Decisiones ya tomadas por el usuario:

- Se retiran **todas** las estrategias MT5 (hecho hoy, ver [ESTRATEGIAS_ACTIVAS.md](ESTRATEGIAS_ACTIVAS.md)).
- **Cripto fuera** (BTCUSD y ETHUSD dejan de tener agente; las comisiones se comen el R).
- **Un agente por símbolo**, con ciclo de **5 minutos**.
- Hasta **10 símbolos** (la selección propuesta está en §2).
- Un **agente selector** que decide qué símbolos están activos, y escribe en la BBDD para que la app lo muestre.
- La app debe enseñar **qué prompt recibe cada agente y qué devuelve**.
- Los agentes nocturno y semanal quedan en segundo plano. El nocturno (autopsia) ya existe.

---

## 0. Lo que hay que decir antes de empezar

1. **El coste sube linealmente y es lo que más hay que vigilar.** 10 agentes × 12 ciclos/hora =
   120 llamadas/hora. Con los números medidos (≈11–13k tokens por llamada, casi todo caché
   leída) y la nota de `llm_agents_experiment.md` (5 agentes en M5 ≈ 4 pts/h de la ventana de 5 h),
   **10 agentes sin filtro previo probablemente superen el freno del 60 % en pocas horas.**
   Por eso la §3 mete un filtro determinista antes de cada llamada. Sin él, el plan no cabe en la cuota.
2. **El "gasto mínimo" de hoy no es una medida fiable.** Salió de ~8 h de operación con 5 agentes.
   Hay que medirlo al menos durante una semana completa antes de escalar.
3. **Un LLM no tiene edge demostrado.** Las familias TA probadas (memoria: `strategy_exploration_lessons`)
   fallan out-of-sample. El plan es un experimento de medición, no una
   promesa de rentabilidad. Los criterios de corte están en §8.
4. **El juez no sirve para evaluar al agente.** Las cifras de §2 vienen del juez de PrecisionScalping,
   no de un LLM. Son un prior para elegir símbolos, nada más.

---

## 1. Arquitectura

```
                      ┌────────────── BBDD pst_trading.db ──────────────┐
                      │ symbols_config.is_active   (lo que se opera)     │
                      │ llm_decisions              (prompt, entrada,     │
                      │                             salida, validación)  │
                      │ llm_selector_runs          (decisiones del selector) │
                      │ llm_symbol_changes         (auditoría on/off)    │
                      └──────▲──────────────▲─────────────▲──────────────┘
                             │              │             │
  Selector (1/día, pre-sesión) │   Trader ×10 (cada 5 min, con filtro)   │ App PST_Web
  decide qué símbolos activar ─┘   decide por símbolo (entrada/salida) ───┘ muestra todo
                                              │
                                   Revisor (cada 5 min, solo con posición abierta)
                                              │
                               Ejecutor determinista (MT5 order_send)
```

**Principio:** el LLM propone; código determinista valida y ejecuta. Ningún agente dimensiona riesgo
ni crea SL/TP fuera de los límites duros. Los límites duros de `llm_trader.py` se mantienen.

---

## 2. Universo: 10 símbolos propuestos (sin cripto)

Criterio: positivos en la validación de PrecisionScalping (juez fiel) y de costes razonables.
Esto es **un prior**, no evidencia de que un LLM vaya a ganar en ellos.

| # | Símbolo | Clase | Evidencia previa (juez fiel) | Nota |
|---|---|---|---|---|
| 1 | US100.cash | Índice | +0.285 R/PF 1.86 @30d | El mejor del barrido de índices |
| 2 | US30.cash | Índice | +0.201 R/PF 1.73 @30d | Confirmado en 2 ventanas |
| 3 | US500.cash | Índice | positivo (grupo de índices) | Ya tiene agente |
| 4 | GER40.cash | Índice | +0.189 R/PF 1.56 @30d | Swap largo alto: vigilar |
| 5 | EU50.cash | Índice | +0.119 R en holdout | Nunca tuneado |
| 6 | UK100.cash | Índice | +0.070 R, marginal | Experimental |
| 7 | MSFT | Acción | +0.143 R/PF 1.47 @30d | Muestra menor (38 trades) |
| 8 | AVGO | Acción | +0.344 R @20d | Una sola ventana |
| 9 | BRK.B | Acción | +0.259 R @20d | Una sola ventana |
| 10 | XOM | Acción | +0.233 R @20d | Una sola ventana |

**Fuera y por qué:**
- Cripto (BTC, ETH): comisión porcentual que se come el R. Fuera por decisión del usuario.
- EURUSD y el agente de XAUUSD: los cruces de forex murieron por coste en el barrido de universo
  (memoria: `universe_expansion_candidates`). No hay número fiable de XAUUSD en memoria: entra solo si el
  selector lo justifica con datos.
- JP225, HK50, ASML, RTX, FDX, FRA40: negativos, marginales o desactivados por la auditoría de BBDD.

**Nombres:** confirmar en MT5 con `get_marketwatch_symbols` antes de activar (ver el aviso de
`US30` / `NAS100.cash` en la memoria de universo: hay filas con nombres que no existen en FTMO).

---

## 3. Agente trader (uno por símbolo, cada 5 min)

### 3.1 Filtro determinista previo (obligatorio)

El agente **no se llama cada 5 minutos por defecto**. Antes de la llamada, código puro calcula:

- ¿hay posición abierta de este símbolo? → **sí**: se llama solo al revisor (ver §4), no al trader.
- ¿hay señal de contexto nueva? Condiciones simples y fijas: cruce de EMA, ruptura de rango de N velas,
  cambio de régimen de ADX, o precio a menos de X ATR de un nivel clave. **Sin señal, no hay llamada.**
- ¿el coste de la operación es razonable? (spread + comisión frente al stop previsto, ver §3.3).

Esto corta la mayoría de llamadas. Es lo que decide si el plan cabe en la cuota. **Objetivo inicial:
≤ 25 % de los ciclos llaman al modelo.** Se mide y se ajusta.

### 3.2 Contenido del prompt (mejorado)

El prompt se construye en código, en secciones fijas y versionadas (`prompt_version` + hash):

1. **Rol y reglas duras** (fijas): solo puede devolver el esquema JSON; toda operación con SL; no operar
   si el coste supera X % del R.
2. **Datos de mercado**: últimas 40 velas M5 (OHLC), resumen M15/H1/H4/D1 (tendencia, ATR, ADX, RSI, distancia
   a EMA200), máximos/mínimos de sesión y previos, VWAP.
3. **Coste**: spread actual en precio y en R, comisión por lote, swap, distancia del SL propuesto en ATR.
4. **Tiempo**: hora de la sesión, minutos hasta el cierre forzado, si es víspera de evento.
5. **Estado de cuenta del agente**: P&L del día, nº de operaciones, tiempo desde la última salida (enfriamiento).
6. **Historial propio reciente**: últimas 5 operaciones con su R real y motivo de salida. El agente ve
   sus propios resultados (es parte de lo que se quiere medir).
7. **Salida exigida** (esquema estricto, ver §3.4).

Reglas del prompt: nada de "tú eres un experto". Cada dato con su unidad. Sin consejos genéricos.

### 3.3 Coherencia: lo que el código comprueba sobre lo que devuelve el modelo

Antes de ejecutar, el código rechaza la respuesta si:

- no valida contra el esquema JSON;
- el SL está a menos de 0,5 ATR (ruido) o a más de 3 ATR (riesgo fuera de política);
- el TP da un R neto menor que el coste estimado × 2;
- el precio de entrada, SL o TP no cuadra con el último precio del snapshot (± spread);
- `invalidated_by` no es concreto (el agente debe decir qué precio o hecho rompe la tesis).

**Todo rechazo queda registrado** con el motivo. Esa tabla es la que dice si el modelo es consistente.

### 3.4 Esquema de salida (todos los agentes trader)

```json
{
  "action": "hold | buy | sell | close",
  "confidence": 0.0,
  "entry_basis_tf": "M5 | M15 | H1 | H4 | D1",
  "sl_price": null,
  "tp_price": null,
  "invalidated_by": "precio o hecho concreto",
  "rationale": "máx. 280 caracteres, citando cifras del snapshot"
}
```

### 3.5 Cadencia y coste

- 10 traders × 12 ciclos/h = 120 comprobaciones/h, pero **con el filtro de §3.1 solo ~25 % llaman al modelo**.
- Lanzamiento escalonado: los 10 procesos no arrancan a la vez (evita ráfagas sobre la cuota).
- Freno: 60 % ventana 5 h y 70 % semanal, como hoy. Si se alcanza, el scheduler deja de llamar y lo anota.

---

## 4. Revisor (cada 5 min, solo posiciones abiertas)

- Se llama **solo** si hay posición abierta de magic de agente. Sin posición, cero coste.
- Solo puede **cerrar** y solo si la tesis se rompió (confianza ≥ 0,85, invalidación concreta).
- Dato del primer día: los cierres discrecionales costaron ~390 €. Se mantiene en modo muy restrictivo
  y se mide cada semana si sus cierres ayudan (`llm_trader_flota.py --resultados`).
- Si la medición demuestra que sus cierres restan, se apaga. Esa decisión la toma el dato, no la intuición.

---

## 5. Agente selector (decide qué símbolos operan)

**Cadencia:** 1 vez al día, antes de la apertura europea (≈ 07:30 hora local), más una vez el
domingo para revisión semanal. **No es horario de 5 min.**

**Entradas** (todo calculado por código, el modelo no busca datos):

- por símbolo: coste medio (spread + comisión + swap) en R, ATR relativo, volumen/actividad de sesión,
  resultado de los últimos 20–30 días de su agente (R neto, nº de operaciones, PF);
- por símbolo: resultado del juez fiel más reciente (si existe) y su fecha;
- estado de cartera: correlación entre símbolos activos, exposición por clase.

**Salida:** esquema JSON de activaciones con motivo por símbolo:

```json
{
  "date": "2026-10-23",
  "decisions": [
    { "symbol": "XOM", "active": true, "reason": "coste 0,08 R, ATR estable, PF 1,4 en 30 d", "evidence_ref": "llm_selector_runs#..." },
    { "symbol": "UK100.cash", "active": false, "reason": "spread 2× media semanal" }
  ]
}
```

**Límites duros en código (el selector no puede saltárselos):**

- máximo **10** símbolos activos; nunca cripto (lista negra en código, no en el prompt);
- **histéresis:** un símbolo no se desactiva por un solo mal día. Mínimo 5 días de sesión desde la última
  activación, salvo coste anómalo (> 2× media) o fallo técnico;
- **no desactivar con posición abierta:** la desactivación se aplica sin cerrar posiciones; la
  posición se gestiona hasta su cierre por SL/TP o por el revisor;
- cambios **solo** a `symbols_config.is_active` y a `llm_symbol_changes` (auditoría con motivo y `run_id`).
  Nunca toca lotes, SL, TP ni riesgo.

**Visibilidad en la app:** la tabla `symbols_config` ya la lee la app. El selector escribe ahí
con el mismo `is_active` que usa el resto del sistema, así que al entrar en la app se ve qué
símbolos se están operando. Si el orquestador (retirado) se reactivara, leería esos mismos flags:
hay que tenerlo en cuenta antes de volver a arrancarlo.

---

## 6. Trazabilidad: lo que se ve en la app

Hoy las decisiones de los agentes viven en `llm_trader.db`, un SQLite por agente que la app no lee.
Se mueven a **`pst_trading.db`** para que la app las lea por la misma API que el resto.

**Tabla `llm_decisions`** (una fila por llamada al modelo, incluidas las rechazadas):

| Campo | Contenido |
|---|---|
| `id`, `ts`, `agent`, `symbol`, `role` | trader / revisor / selector |
| `prompt_version`, `prompt_hash` | versión del prompt y su hash |
| `snapshot_json` | los datos que se enviaron (la entrada exacta) |
| `prompt_text` | el prompt completo renderizado |
| `raw_response` | la respuesta tal cual |
| `parsed_json` | la respuesta validada, o null |
| `validation` | `ok` o motivo de rechazo (esquema, SL fuera de rango, coste, etc.) |
| `action_taken` | lo que hizo el código (nada, orden enviada, cierre, etc.) |
| `tokens_in`, `tokens_out`, `cache_read` | consumo |
| `outcome_r` | R real de la operación derivada, rellenado al cerrar |

**Vista en PST_Web, pestaña "Agentes":**

1. **Panel de símbolos activos**: lista de `symbols_config.is_active = 1`, con su agente, última decisión
   y estado de cuota.
2. **Detalle por símbolo**: última decisión con el prompt renderizado, el snapshot en tabla (no solo
   JSON), la respuesta cruda y la validación. Se puede ver el prompt de cualquier decisión pasada.
3. **Historial**: decisiones con filtro por validación (ok / rechazada), por acción y por resultado.
4. **Coherencia**: % de respuestas rechazadas por motivo, y % de SL que se movieron contra la tesis.
5. **Selector**: última decisión de activación con su motivo y los datos que la sustentan.
6. **Consumo**: tokens por hora y por agente, y medidor frente al freno de 5 h y semanal.

**Cómo se "ve lo que devuelve":** el detalle de una decisión muestra tres columnas alineadas:
*entrada (snapshot)* → *prompt* → *salida (validada)*. Si el modelo cita un precio, la app marca si
ese precio existe en el snapshot. Eso hace visible la inconsistencia sin tener que leer el JSON.

---

## 7. Presupuesto y medición

- Primera semana: **medir sin ampliar.** Se corre con 5 agentes (los actuales, sin cripto) y el filtro de §3.1.
  Se anota tokens por llamada y % de ciclos que llaman al modelo.
- Con esos datos se decide el nº de agentes que cabe por debajo del 60 % de la ventana de 5 h.
- El nocturno (autopsia) y el semanal no se programan hasta que el consumo diario esté medido.
- El selector solo corre 2 veces por semana al principio (diario después de medir).

---

## 8. Criterios de corte (qué significa que funciona o no)

Se fijan **ahora**, antes de tener datos:

- **Fracaso:** R neto negativo tras 4 semanas, o peor que el brazo base (no hacer nada / reglas simples
  del mismo filtro sin LLM). Entonces se apaga la flota.
- **Coste:** si el coste en tokens supera el 30 % del R bruto ganado en el mismo periodo, se recorta el número de agentes.
- **Consistencia:** si más del 20 % de las respuestas se rechazan por esquema o por SL fuera de rango, el prompt se
  revisa antes de seguir.
- **Éxito provisional (no es prueba):** R neto positivo en 4 semanas con ≥ 60 operaciones y sin depender de un solo símbolo.

---

## 9. Fases

| Fase | Entregable | Criterio para pasar |
|---|---|---|
| **0** | Confirmar nombres de símbolos en MT5, quitar BTC/ETH, decidir los 10 | Lista validada en Market Watch |
| **1** | Filtro determinista (§3.1) + `llm_decisions` en `pst_trading.db` + prompt versionado | El filtro deja pasar ≤ 25 % de ciclos en un día de prueba |
| **2** | Pestaña Agentes (§6) con detalle, prompt y validación | Se puede ver el prompt y la respuesta de cualquier decisión |
| **3** | Agente selector (§5) con escritura en `symbols_config` y auditoría | Un día completo sin cambios fuera de límites |
| **4** | Medición de una semana completa con consumo (§7) | Hay tokens/hora medidos y decisión de nº de agentes |
| **5** | Revisión de criterios (§8) | Continuar, recortar o apagar |

---

## 10. Decisiones tomadas (2026-10-22)

1. **Universo:** los 10 de §2 (sin cripto, sin forex, sin XAUUSD). Se empieza por ellos.
2. **Filtro previo obligatorio** (§3.1).
3. **Horario:** cada símbolo opera solo mientras su mercado está abierto. Además, **calendario de FTMO**:
   no se abre operación con noticias de alto impacto (ver §11).
4. **Modelos:** **Sonnet** para los traders de los símbolos principales y para el selector. Haiku para
   el resto y para el revisor. La elección por símbolo queda en configuración, no en código.
5. **Decisiones en `pst_trading.db`** y visibles en la app (§6).

**Arquitectura de subagentes (decisión del usuario):** un agente de **estrategia** por clase de
mercado, con **subagentes por símbolo** que solo se activan cuando su mercado está abierto. Ejemplo:
acciones de EE. UU. (sesión NY) y de Europa (sesión EU) tienen horarios distintos; los índices, otro.
El selector se ejecuta **antes de que abra cada mercado** (no a una hora fija): para una acción de EE. UU.,
antes de la apertura NY; para un índice europeo, antes de la apertura EU.

---

## 10b. Estado de implementación (2026-10-22)

- [x] **Nombres verificados en MT5** (Market Watch): US100.cash, US30.cash, US500.cash, GER40.cash, EU50.cash,
      UK100.cash, MSFT, AVGO, BRK.B, XOM. Todos disponibles con esos nombres exactos.
- [x] **Agentes fuera del universo parados:** BTC (Agente_LLM, PID retirado), ETH, XAU y EURUSD.
      ETH, XAU y EURUSD se pararon con fichero `llm_trader_<Nombre>.STOP` en `.agents/data/`.
      **Ojo:** esos ficheros hay que borrarlos antes de volver a arrancar esos agentes, o se apagan al nacer.
      BTC no tiene fichero propio (su STOP sería el global, que pararía a todos), así que su proceso se paró directamente.
- [x] **Sigue activo:** Agente_US500 (en el universo).
- [ ] **Pendiente:** crear los 9 agentes restantes. La lista de `llm_trader_flota.py` sigue con los 5 antiguos;
      `--iniciar` volvería a arrancar BTC/ETH/XAU/EURUSD mientras no se borren sus STOP. Hay que cambiar esa lista antes de usarla.
- [ ] **Pendiente:** filtro previo (§3.1), tabla `llm_decisions` en `pst_trading.db`, pestaña Agentes, selector (§5).
- **Posiciones abiertas que quedan:** EURUSD (LLM, sell 0.45 lotes) y BTCUSD (LLM, sell 0.03 lotes). No se tocaron.
  Siguen con su SL/TP en el servidor. El revisor sigue viéndolas porque su magic (109001 y 109004) está dentro de su rango.

## 11. Calendario y horario (nuevo)

- **Horario por símbolo:** cada subagente tiene su ventana de sesión (apertura → cierre del mercado
  del símbolo, menos un margen de cierre forzado). Fuera de esa ventana no se llama al modelo.
- **Calendario FTMO:** antes de cada decisión de entrada, el código consulta el calendario de noticias de alto
  impacto y no abre si hay una a menos de N minutos (N a definir, por defecto 30 antes y 15 después). Las
  posiciones ya abiertas no se cierran por noticia salvo que el revisor lo decida con una invalidación
  concreta. **Pendiente:** elegir la fuente del calendario (la que use FTMO o una pública) y verificar que
  se puede consultar desde el proceso sin depender de la cuota.

**Comparación con el calendario de FTMO, semana 19–25 oct (2026-10-22):**

- FTMO marca como "evento restringido" **13 eventos** esta semana (5 el lunes, 8 el miércoles; ningún otro día).
- El filtro por impacto **alto** del feed público solo coincide con **5 de 13**. Pierde 8, incluidos eventos de
  impacto bajo como "Core CPI m/m" o "Crude Oil Inventories".
- La restricción de FTMO **no sigue el impacto**. Sigue el tipo de dato: todos los CPI, PPI, RPI,
  "Public Sector Net Borrowing", "Crude Oil Inventories" y "Median/Trimmed/Common CPI".
  No están restringidos: encuestas (NAHB, BOC), tipos del CNY, discursos del Buba, "HPI y/y" ni el discurso del BoE.
- Regla provisional: restringir por título con esa lista. Coincide con los 13 de esta semana y no
  deja fuera ninguno. **No está validada** con más semanas.
- Opción más fiable: que el usuario exporte el calendario restringido de FTMO (botón Export) cada semana
  y el bot lo lea de un fichero. Así no hace falta acceder a la cuenta FTMO desde el bot.

**Estado del calendario (2026-10-22):**
- El calendario de FTMO (`trader.ftmo.com/economic-calendar`) **exige iniciar sesión** (redirige a SSO).
  Nuestro proceso no tiene credenciales de ese portal, así que no se usa. No se intenta saltar el login.
- Alternativa pública: `https://nfs.faireconomy.media/ff_calendar_thisweek.json`. Es JSON válido, con campos
  `title`, `country`, `date`, `impact`, `forecast`, `previous`. **No verificado:** la lectura automática
  dio solo 1 evento de impacto alto en USD/EUR/GBP/JPY para la semana, lo que parece pocos. Antes de confiar
  en el filtro hay que comprobarlo contra el calendario de FTMO a mano durante una semana.

---

## 12. Modelos y estructura de agentes (nuevo)

| Rol | Modelo | Ciclo | Cuándo llama |
|---|---|---|---|
| Selector | Sonnet | antes de apertura de cada mercado + revisión dominical | siempre que toque |
| Trader (símbolos principales: US100, US500, US30, GER40) | Sonnet | 5 min dentro de sesión, con filtro §3.1 | solo si hay señal |
| Trader (resto) | Haiku | 5 min dentro de sesión, con filtro §3.1 | solo si hay señal |
| Revisor | Haiku | 5 min | solo con posición abierta |

Los modelos se revisan tras la primera semana de medición: si Sonnet no mejora la calidad (menos
rechazos por coherencia, mejor R) el coste no se justifica y se baja a Haiku.
