# 🤖 Plan de agentes e IA — PST

Plan de incorporación de agentes Claude al proyecto (bot Python + EAs MQL5 + web).
Consolidado el **2026-10-20**. Punto de partida: el repo `marmottajr/habblaud` y la
idea de "una IA con subagentes que operen".

**Estado: propuesta, nada implementado.** Las secciones marcadas ⚠️ son suposiciones
sin verificar.

---

## 0. Las tres conclusiones que condicionan todo el plan

1. **habblaud no es un framework de agentes.** Es una *oficina pixel-art* que
   **visualiza** sesiones de Claude Code: lee los transcripts de `~/.claude/projects/`,
   dibuja cada proyecto como sala y cada agente/subagente como personaje. No define
   agentes, no los lanza, no sabe de MT5 y no opera. Sirve como sala de control y como
   cantera de patrones, no como motor.

2. **Sin API key, la única vía es el CLI de Claude Code como subproceso.** Con cuenta de
   suscripción quedan descartados el SDK de Anthropic, el Tool Runner y Managed Agents
   (todos exigen `ANTHROPIC_API_KEY` y facturan por token). La doc oficial del Agent SDK
   además prohíbe usar login/límites de claude.ai en productos construidos sobre él.
   La vía correcta —y que la propia doc señala— es `claude -p`.

3. **La cuota, no el dinero, es la restricción de diseño.** Ventana móvil de 5 h + tope
   semanal, **compartidos con las sesiones interactivas propias**. Esto mata cualquier
   LLM en el hot path: no se puede hacer depender la ejecución de una cuota que el propio
   usuario puede agotar chateando a las 16:00.

---

## 1. Lo que ya existe en el repo

Más infraestructura de la que parece. Nada de esto hay que construir desde cero:

| Pieza | Qué es | Reutilización |
|---|---|---|
| `.agents/workflows/` (5 md) | Workflows estilo Windsurf (`// turbo`) | Migrar a `.claude/agents/` + `.claude/commands/` |
| `PST_MCP/pst_server.py` | MCP stdio: `query_db` + cuenta MT5 viva | **Superficie de herramientas de la flota.** Hay que expandirla |
| `PST_Core/strategies/ai_providers.py` | Gemini / Groq / Ollama async | Ya no encaja (ver §0.2): se mantiene sólo si se quiere un LLM local de runtime |
| `PST_Core/engine/monitor_agent.py` | Sentinel determinista 24/7 (DD, rachas, MT5) | Ya es un agente. **Que siga determinista** |
| `Tools/` (12 labs, `pst_bt_lab`, `pst_range_lab`) | Jueces fieles | Las tools reales de los agentes de investigación |
| `signal_logs`, `trade_context`, `ai_oracle_state` | Tablas ya creadas | Telemetría y precedente de estado LLM |
| FastAPI + `/ws` + `LiveConsole.jsx` | Streaming al front | Base del panel de agentes |
| `metatrader5-mcp` | **Caído** (`ConnectionRefused`) | Bloquea al agente MQL5 y la Opción B de §5. **Arreglar primero** |

---

## 2. Arquitectura: tres planos, y el LLM no toca el de ejecución

```
PLANO A — EJECUCIÓN (0% LLM, intocable)
  orchestrator -> executor -> MT5.  Determinista, auditable, sin latencia de red.
  Los EAs MQL5 viven aquí también.

PLANO B — SUPERVISIÓN (programada y batcheada, NO 24/7)
  Briefing pre-sesión + reporte diario narrado.
  Falla -> se salta en silencio. Nunca bloquea.

PLANO C — INVESTIGACIÓN (subagentes Claude Code, offline, de madrugada)
  Juez · Reconciliador · Cazador · Ingeniero MQL5 · Revisor
  Escriben PROPUESTAS en BBDD. Nada se aplica solo.
  <- AQUÍ se enchufa habblaud
```

El plano B y el C son runtimes distintos: C son sesiones de Claude Code (habblaud las ve),
B son tareas programadas que escriben en BBDD (habblaud no las ve; necesitan panel propio).

---

## 3. Plano C — la flota de investigación

Cinco definiciones en `.claude/agents/*.md`, con tools acotadas:

| Agente | Entrada | Tools | Salida | Modelo / effort |
|---|---|---|---|---|
| **reconciliador** | BBDD live vs juez | `pst_reconcile.py`, MCP `query_db` | desglose de la brecha € | `sonnet` / `low` |
| **juez** | trades de ayer | `pst_bt_lab.py`, `pst_range_lab.py`, `audit_precisionscalping.py` | ¿exigente o laxo? por símbolo | `sonnet` / `low` |
| **revisor** | diff de la noche | `/code-review`, git | bugs antes de que lleguen al exe | `sonnet` / `medium` |
| **cazador** | `signal_logs` + OHLC | `pst_new_strategy_lab.py`, MT5 MCP | hipótesis + veredicto en Tester nativo | `opus` / `high` |
| **ingeniero-mql5** | hipótesis del cazador | `metaeditor64.exe` CLI, MT5 MCP | EA en `MT5/Experts/Experimental/` + XML | `opus` / `high` |

**El orden importa: `reconciliador` antes que `cazador`.** Ver §7.

El tiering de modelo no es por coste en euros, es por consumo de ventana: los mecánicos
(corren scripts y resumen números) van en `sonnet`; `opus` sólo donde hace falta criterio.

### Invocación concreta

Flags verificados en el CLI local (v2.1.273):

```bash
claude -p "$(cat .agents/prompts/reconciliador.md)" \
  --agent reconciliador \
  --model sonnet --effort low \
  --output-format json \
  --json-schema "$(cat .agents/schemas/proposal.json)" \
  --mcp-config .agents/mcp/pst.json --strict-mcp-config \
  --allowedTools "mcp__pst__*,Read,Bash(python Tools/*)" \
  --permission-prompts none \
  --add-dir . \
  > .agents/out/reconciliador_$(date +%F).json
```

Tres piezas hacen el trabajo:

- **`--json-schema`** — salida validada contra esquema. El agente **no devuelve prosa**:
  devuelve una propuesta JSON que entra con un `INSERT` directo en `proposals`. Es lo que
  convierte la flota en algo usable sin leer informes.
- **`--permission-prompts none`** — en desatendido, lo que pediría permiso se deniega solo.
  Nunca se queda colgado a las 3 de la mañana.
- **`--allowedTools` + `--strict-mcp-config`** — cada agente ve exactamente las tools de
  `PST_MCP` que necesita y nada más. Para analistas de sólo lectura, añadir `--restricted`
  (quita Bash y los tools que ejecutan código).

Otros flags útiles: `--forward-subagent-text` con `--output-format stream-json` saca el texto
de los subagentes al stream (lo que el panel consume); `claude --bg` + `claude agents / logs /
attach / stop` ya es un runner de sesiones en background, no hay que construirlo.

### Autenticación y cron

- `claude setup-token` genera un token de larga duración (*requiere suscripción*): es lo
  que necesita el cron desatendido.
- **Nunca usar `--bare`**: ignora OAuth a propósito y exige `ANTHROPIC_API_KEY`.
- Windows 11 -> `schtasks`, no cron.

### Presupuesto de cuota

1. **Secuencial, nunca en paralelo.** Cinco subagentes a la vez se comen la ventana de 5 h.
2. **Fuera de horas de trabajo propias.** De madrugada; por la mañana la ventana está limpia.
3. **Empezar con dos agentes/noche** (reconciliador + juez), medir el consumo semanal real,
   y crecer desde el dato.
4. **`agent_runs` es contabilidad, no telemetría.** `--output-format json` devuelve el uso.
   Si la semanal va justa, el scheduler salta al `cazador` (el caro) y deja los baratos.
5. ⚠️ `--max-budget-usd` existe pero está pensado para llamadas facturadas por API; no
   contar con él como tope en suscripción. El tope real es cuántas ejecuciones se programan.

---

## 4. Plano B — briefing pre-sesión (sustituye al veto en caliente)

La idea original de "un LLM que vete señales en caliente" queda **descartada** por §0.3.
En su lugar:

> Una ejecución programada de Claude **antes de la apertura** escribe una configuración
> determinista en BBDD (símbolos vetados hoy, regímenes, sesgo macro). El orchestrator lee
> esa fila: cero latencia, cero dependencia de cuota, degradación limpia — fila vieja o
> ausente = defaults neutros.

La IA decide la configuración del día **por adelantado**; el motor determinista ejecuta.
Más auditable que un veto en caliente, y deja a `monitor_agent.py` 100% determinista,
que es donde debe estar.

Segundo uso de plano B: narrar el reporte diario de las 22:00 que ya genera el Sentinel.

---

## 5. Reestructuración de estrategias y "versión IA"

### El hallazgo: ya hay dos filosofías, y la buena es la de MT5

**PST_Core (Python)** — 1 motor, 6 estrategias, N símbolos, matriz de config por símbolo,
`entry_tf` configurable, buckets de capital. La unidad es *"estrategia"* y el despliegue
sale de combinar config. Superficie combinatoria enorme.

**MT5 (MQL5)** — 9 ficheros, 9 despliegues. La unidad es *"hipótesis × símbolo × timeframe
× magic"*, autocontenida, compartiendo sólo tres includes. Con el pipeline documentado en
`MT5/README.md`: prototipo vectorizado -> cribado con spread real -> EA MQL5 -> Tester con
tick data -> split IS/OOS **con ambas mitades positivas** -> demo. De ~40 hipótesis,
sobrevivieron 9.

El lado MT5 tiene la disciplina que al lado Python le falta, y el lado Python es donde está
el dinero perdido (WR 34% real vs 48% esperado). La matriz de config genera cientos de
combinaciones que nadie puede validar con ese rigor — y los barridos lo confirman:
`entry_tf` M1/M2/M3 sin ganador robusto, expansión de universo con filas mal nombradas,
bloqueo por correlación mal calibrado.

### Propuesta: cambiar la unidad de despliegue, no las estrategias

Un manifiesto declarativo único que describa los ~15 despliegues de **ambos mundos**:

```yaml
- id: H25
  engine: mql5            # python | mql5
  symbols: [UK100.cash]
  timeframe: H1
  magic: 101100
  state: demo             # candidate | shadow | demo | live | retired
  risk_bucket: RANGE
  evidence:
    tester_xml: Tester/H25_UK100_H1.xml
    is_oos: {is: 2.1, oos: 5.45}
    judge_run: null
```

**Pros**
- El orchestrator lee esto en vez de `ENABLED_STRATEGIES` + matriz de config.
- Hoy no se puede responder *"¿qué evidencia justifica que US100 esté vivo?"* sin excavar.
  Con esto, el estado de validación es un campo.
- Es **la superficie legible por máquina contra la que los agentes proponen**. Sin algo así,
  la flota no tiene dónde escribir y sólo produce informes que nadie lee.

**Contras**
- Es un refactor del bot que mueve dinero real, con riesgo de regresión.
- El exe necesita `/release` para recoger cambios: el radio de impacto no es teórico.
- Mitigación: construir el manifiesto **en paralelo** primero (generado desde la config
  actual y comparado), no sustituyendo de golpe.

### Las cuatro formas de "versión IA" de las estrategias

⚠️ Dato que decide el debate y que conviene confirmar con el MCP de MetaTrader cuando
vuelva: **`WebRequest()` no funciona en el Strategy Tester de MT5.** Un EA que llame a un
LLM **no se puede backtestear**; y si no se puede backtestear, por las reglas del propio
proyecto (IS/OOS, ambas mitades positivas) **nunca puede llegar a demo**.

| | Diseño | Pros | Contras |
|---|---|---|---|
| **A** | **IA fuera, EA dentro.** Claude corre pre-sesión y escribe régimen/vetos del día en fichero o global variables; los EAs lo leen en `OnInit`/nueva vela | Latencia cero, sin WebRequest, respeta el pipeline. **Única forma en que la IA "opera"** de verdad | La capa IA es invisible al Tester. Validarla exige un archivo histórico de decisiones IA fechadas -> meses de forward, y propenso a lookahead si se reconstruye a posteriori |
| **B** | **IA como fábrica, no como componente.** Claude escribe hipótesis MQL5, compila con `metaeditor64.exe`, corre el Tester, lee el XML, y mata o promueve | 100% compatible con el pipeline. El producto sigue siendo un EA determinista y auditable. Ataca el cuello de botella real: 9 de 40 es un problema de **throughput de cribado riguroso**, que es justo lo que una flota multiplica | **Comparaciones múltiples, y es grave.** Con 500 hipótesis, ~25 pasan IS/OOS por azar puro. Sin mitigación, industrializa el sobreajuste |
| **C** | **IA como juez de régimen semanal.** Claude lee la semana y decide cuáles de los 9 EAs siguen activos | Horizonte de decisión encaja con la cadencia de cuota. Espacio de acción mínimo y restrictivo | 9 EAs × decisión semanal = poquísimos datos. Harían falta años para distinguir "la IA aporta" de "déjalo todo encendido" |
| **D** | **LLM dentro del EA vía WebRequest** | — | Imposible de backtestear, 1-5 s de latencia en `OnTick`, no determinista (misma vela -> decisión distinta), cuota en el hot path, rompe la auditabilidad tipo FTMO |

**Recomendación: B como eje, A como única forma de "operar", C como experimento barato,
D nunca.**

Las tres mitigaciones de B no son opcionales, son la condición para que B valga algo:

1. **Hipótesis pre-registrada.** El agente escribe qué espera y por qué *antes* de correr
   el Tester. Si la justificación es "la encontré probando", se descarta.
2. **Penalización por número de pruebas.** Si la flota probó N hipótesis esa semana, el
   umbral de Sharpe sube con N. Sin esto, B es una máquina de falsos positivos.
3. **Un hold-out que el agente nunca ve.** Ni en el prompt ni en los XML que lee. Se corre
   a mano al final.

Sin esos tres, B es más peligroso que no hacer nada: produce EAs con pedigrí aparente.

---

## 6. Infraestructura compartida (el 80% del trabajo real)

### a) Expandir `PST_MCP/pst_server.py`

De 2 tools a ~12: `read_signal_logs`, `read_trades`, `run_judge(symbol, days)`,
`run_lab(name, args)`, `get_matrix_config`, `propose_change(...)`, `list_proposals`,
`get_reconciliation_delta`. Sin esto los agentes improvisan rutas y comandos, que es el
fallo clásico.

### b) Dos tablas nuevas en `PST_Core/models/database.py`

```sql
agent_runs   -- id, agent, started, finished, cost_tokens, status, summary, artifacts_json
proposals    -- id, agent_run_id, kind, target, current_json, proposed_json,
             --   evidence_json, judge_delta_eur, state, decided_by, decided_at
             -- state: propuesta -> shadow -> aprobada -> viva -> revertida
```

Append-only. Toda acción de agente deja rastro con evidencia adjunta. Es lo que convierte
"la IA sugirió X" en auditable.

### c) Modo shadow

Una propuesta en `shadow` no cambia nada; el orchestrator registra en paralelo qué habría
pasado. Cuatro semanas de shadow es el único dato que justifica dar autonomía.

### d) Esquema y runner

`.agents/schemas/proposal.json` + `.agents/run.ps1`, que invoca el CLI, valida la salida
contra el esquema e inserta en `proposals`.

---

## 7. Que "operen": las puertas de graduación

- **G0 — Shadow (4 semanas).** Los agentes sólo proponen. Métrica: Δ€ real vs Δ€ predicho.
  Si no aciertan aquí, no pasan.
- **G1 — Autonomía restrictiva.** Pueden *apagar* (símbolo, estrategia, par correlacionado).
  Nunca encender. Auto-revierte a las 72 h sin reconfirmación. Riesgo máximo de un fallo:
  dejar de operar algo.
- **G2 — Autonomía de parámetros.** Ajustan dentro de whitelist y bandas ±X%,
  obligatoriamente con una corrida del juez fiel adjunta a la propuesta.
- **G3 — Briefing pre-sesión** (§4). Sustituye al veto en caliente del diseño original.

**Espacio de acción prohibido, para siempre:** ningún agente crea órdenes, dimensiona
riesgo, mueve SL ni toca el tope de pérdida diaria. Eso sigue en el plano A.

### Prerrequisito, no una fase

Hay una brecha conocida entre el juez y el live (comisión/swap, spread de salida, offset de
reloj de 210 min) y un WR real del 34% contra un 48% esperado. **Una flota que optimiza
contra un juez que no cuadra con el live industrializa el error.** Por eso el
`reconciliador` va antes que el `cazador`, y toda propuesta debe llevar su delta juez-live
declarado.

---

## 8. Sala de Control en PST_Web

Pestaña nueva junto a `StrategyLab.jsx`:

- **Flota** — plano B/C en vivo.
- **Bandeja de propuestas** — tarjetas con evidencia, Δ€ del juez, y tres botones:
  Aprobar / Shadow / Rechazar. Este componente es el que da control real.
- **Carrete de agentes** — últimas 20 `agent_runs` con consumo.

### Embeber habblaud

Licencia **MIT**, v1.0.3, Node ≥22.12. Su servidor escucha en `127.0.0.1:4747`
(`HABBLAUD_PORT`), valida el header `Host` estrictamente (localhost / `*.localhost` /
loopback / `HABBLAUD_ALLOWED_HOSTS`, resto 403), **no implementa CORS**, manda
`Cross-Origin-Resource-Policy: same-origin`, y en POST exige `Content-Type:
application/json` + `Origin` same-origin **o local**.

**Vía A — iframe.** ⚠️ Debería funcionar: el CORP gobierna subrecursos, no la navegación de
un iframe, y no documentan `X-Frame-Options` ni `CSP frame-ancestors`. La app es pywebview
(Edge WebView2), ventana en `PST_API/main.py:1463` desde `start_app()` (línea 1429), React
servido por uvicorn en otro puerto loopback -> el `Origin` cuenta como "local" y hasta los
POST deberían pasar. **Verificar con `curl -I http://127.0.0.1:4747/`**; si aparece
`X-Frame-Options`, el arreglo es un reverse-proxy de 20 líneas en FastAPI (patrón de
montaje ya en `main.py:1292`).

**Vía B — consumir su API (recomendada).** Renderizar su JSON con el lenguaje visual de PST,
reutilizando el patrón de `/ws` + `LiveConsole.jsx`:

| Endpoint | Qué da |
|---|---|
| `GET /api/mod/summary` | contadores + **qué agentes te esperan**. Widget de 3 líneas |
| `GET /api/stream` (SSE) | `snapshot`, `feed`, `notice`, ping cada 15 s |
| `GET /api/snapshot` | estado completo de la oficina |
| `GET /api/stats` | estadísticas del día |

**Hacer las dos**: iframe como pestaña "Oficina" aparte (vista divertida + aprobar
permisos), API para los widgets integrados.

Su barra de uso por cuenta (5 h y semanal) deja de ser decoración y se convierte en **el
instrumento por el que se gobierna la flota** (§3, presupuesto de cuota).

Nota de diseño suyo: **terminal, permisos y mensajes sólo funcionan con bind local.** Si se
expone el puerto, habblaud los desactiva él mismo. Embeber y acceso remoto son mutuamente
excluyentes.

---

## 9. Roadmap

| Fase | Duración | Entregable |
|---|---|---|
| **0** | 1 tarde | habblaud + mod funcionando; **MCP MT5 arreglado**; `.agents/workflows` -> `.claude/agents`; `claude setup-token` |
| **1** | 1-2 sem | MCP expandido + tablas `agent_runs`/`proposals` + `proposal.json` + `run.ps1` end-to-end |
| **2** | 2-4 sem | reconciliador + juez en cron nocturno. Primer informe automático por la mañana |
| **3** | mes 2 | Sala de Control + Sentinel narrado. Manifiesto de despliegues en paralelo (§5) |
| **4** | mes 2-3 | cazador + ingeniero-mql5 con las 3 mitigaciones de B. Shadow del briefing pre-sesión |
| **5** | mes 3+ | Puertas G1 -> G3 según resultados de G0 |

---

## 10. Riesgos

| Riesgo | Mitigación |
|---|---|
| Optimizar contra un juez que no cuadra con el live | `reconciliador` primero (§7) |
| Sobreajuste industrializado por la flota | Las 3 mitigaciones de B (§5) |
| Agotar la cuota semanal con la flota | 2 agentes/noche, secuencial, medir antes de crecer (§3) |
| Agente colgado esperando permiso de madrugada | `--permission-prompts none` |
| Exponer habblaud a internet | Es RCE con dinero detrás: terminal en la máquina que opera la cuenta y `.env` con credenciales de broker. Sólo localhost, sin login/HTTPS móvil, sin abrir puerto |
| Refactor del manifiesto rompe el bot vivo | Construirlo en paralelo y comparar antes de sustituir |

---

## 11. Pendiente de verificar

- [x] Headers de habblaud — **RESUELTO y mi suposición era FALSA**: sirve la página con
      `X-Frame-Options: DENY` (está en `server/http/static.ts` y lo fija su propio test),
      así que el iframe directo es imposible. De ahí el espejo de §8.
- [x] `metatrader5-mcp` levantado — **OK el 2026-10-20**, ver §12
- [x] Entorno del terminal — **FTMO-Demo reconectado**, los 13 símbolos disponibles, §12
- [ ] `WebRequest()` en Strategy Tester — test empírico pendiente (EA mínimo + backtest +
      journal). Decide si la Opción D de §5 está muerta del todo; la doy por muerta pero
      sin haberlo ejecutado.
- [ ] ⚠️ Comportamiento real de `--max-budget-usd` bajo suscripción
- [ ] Rotar `WEB_PASSWORD` en `.env`: el código ya no publica la credencial, pero el valor
      en uso sigue siendo el que estuvo en el repo público

---

## 12. Estado del MCP de MetaTrader (verificado 2026-10-20)

**El MCP funciona entero.** Comprobados: `get_workspace_info`, `get_trading_account_info`,
`get_time_information`, `list_available_expert_advisors`, `get_marketwatch_symbols`.
Compilador MQL5 presente (build 6182, target avx2), `can_compile_file`/`can_run_backtest`/
`can_run_optimization` = true, write roots sobre `MQL5\` y `Terminal\Common`.

Confirmado que **sigue sin haber tool de compilar expuesta** (dos búsquedas): la capability
`can_compile_file` indica que el compilador existe, no que haya una tool. Compilar sigue
siendo vía `metaeditor64.exe` por CLI.

### ✅ RESUELTO el bloqueante del entorno (verificado 2026-10-20, 20:05)

Durante unas horas este apartado describía un bloqueante real: el terminal era el de FTMO
pero estaba logueado en `MetaQuotes-Demo` y **desconectado desde el 2026-09-15**, con
equity 0.00, cotizaciones congeladas y **sin los `.cash`** donde operan 6 de los 9
despliegues. El usuario reconectó la cuenta y queda resuelto.

| | Antes (MetaQuotes-Demo) | Ahora (verificado) |
|---|---|---|
| Servidor | `MetaQuotes-Demo` | **`FTMO-Demo`** |
| Cuenta | 5055699033 | **1515029407** — "€10k FTMO Free Trial 2-Step" |
| Broker | MetaQuotes Ltd. | **FTMO Global Markets Ltd** |
| `server_connected` | false | **true** |
| Cotizaciones | congeladas en 2026-09-15 | **en vivo** |
| `experts_trade_allowed` | — | **true** |

**Los 13 símbolos de las 9 hipótesis están disponibles.** Ninguno falta:

| Hipótesis | Símbolo(s) | Estado |
|---|---|---|
| H25 (el mejor, Sharpe 5.45) | `UK100.cash` | ✅ 10741.40 / 10743.45 (spread 2.05) |
| H28b, H45 | `GER40.cash` | ✅ 25370.44 / 25372.77 (spread 2.33) |
| H12 | `JP225.cash` | ✅ 64834.50 / 64844.50 (spread 10.00) |
| H20 (referencia) | `US500.cash` | ✅ 7711.05 / 7711.65 (spread 0.60) |
| H5 | EURGBP, AUDCAD, GBPAUD, GBPUSD | ✅ |
| H21 | EURUSD | ✅ |
| H41, H42 | AAPL, TSLA | ✅ |
| H20 | MSFT | ✅ |
| H45 (parcial) | CADJPY | ✅ |

Market Watch: 66 símbolos, con 10 índices `.cash` (AUS200, EU50, FRA40, GER40, HK50,
JP225, UK100, US100, US30, US500).

**Por qué importaba:** el pipeline de `MT5/README.md` se apoya en *"cribado con coste de
spread real"*. Con spreads y swaps de MetaQuotes el cribado usaba el modelo de costes
equivocado — el mismo modo de fallo que advierte §7 (optimizar contra un juez que no cuadra
con el live). Ahora los costes son los de FTMO, incluidos los swaps reales, que no son
menores: `GER40.cash` tiene swap largo de −496.04 y `JP225.cash` de −993.18. Cualquier
hipótesis que mantenga posición varios días tiene que digerir eso.

**Comprobación de regresión:** antes de dar por bueno cualquier cribado futuro, confirmar
`server_connected: true` y que `get_marketwatch_symbols` devuelve los `.cash` con
`update_time` reciente. Es un estado que se pierde con un login equivocado, y el síntoma
—resultados plausibles calculados con costes erróneos— no salta a la vista.

### Hallazgo lateral: el terminal tiene 28 EAs, el repo tiene 9

En `MQL5\Experts\PST_V2\` viven las hipótesis descartadas que `MT5/README.md` decía que
quedaban "en el proyecto original": `PST_KeltnerFade_UK100_H1`, `PST_RsiDivergence_H1`,
`PST_MeanReversion_BTCUSD_BB_ADX`, `PST_PairsSpread_MSFT_US500`, `PST_PairsSpread_US500_US30`,
`PST_SqueezeBreakout`, `PST_CciMomentum_GBPUSD_H4`, `PST_PsarFlip_XAUUSD_H1`,
`PST_TrendFollowing_US500(_VolGate)`, `PST_Breakout_XAU_*`, `PST_Breakout_US500_ORB`, etc.

Es material de entrenamiento para el `cazador`: hipótesis ya probadas y su veredicto. Vale la
pena que el agente las lea antes de proponer, para no repetir trabajo muerto.

---

## 13. Estado del servidor MCP de PST (verificado 2026-10-20)

La Fase 1 decía "expandir el MCP de 2 a 12 tools". La realidad al ejecutarlo: **nunca había
funcionado**. Cinco fallos, todos comprobados corriéndolo, no leyéndolo:

| # | Fallo | Efecto |
|---|---|---|
| 1 | No estaba registrado como servidor MCP en ningún sitio | Nunca invocable |
| 2 | `Tool(input_schema=)` en vez de `inputSchema` | `ValidationError` en la primera llamada a `tools/list` |
| 3 | `get_drawdown_report` y `get_symbol_matrix` declaradas sin implementar | Caían en silencio en el `return` final |
| 4 | **`PST_Core/config.py` hacía `print()` a stdout** | En MCP stdout es el transporte JSON-RPC: el handshake moría con `Expecting value: line 1 column 1` |
| 5 | Sincronización MT5 completa en *cada* llamada a *cualquier* tool | 20 consultas de un agente = 20 escrituras en la BBDD viva |

El cuarto es el instructivo: es invisible leyendo el código del servidor, porque el `print`
está en otro fichero que el servidor importa.

**Estado actual:** 13 tools funcionando, registradas en `.mcp.json` (ámbito proyecto y
versionable, mejor que `~/.claude.json`). Verificado con handshake MCP real por stdio:
`initialize` → `PST-Intelligence-Hub`, `tools/list` → 13, `tools/call` → datos reales.

Sólo **dos** tools escriben, y lo declaran en su descripción: `propose_change` (inserta en
estado `propuesta`; no aplica nada) y `sync_mt5_today`. Las lecturas abren la BBDD en
`?mode=ro` para que una consulta no pueda alterar datos ni por error.

Guardas verificados: `query_select` rechaza `DELETE`/`UPDATE`/multi-sentencia; `run_lab`
tiene lista blanca cerrada de labs — una ruta libre sería ejecución de código arbitrario vía
MCP — con timeout de 900 s y salida recortada para no agotar el contexto del agente.

### Lo que queda para que la flota produzca algo

La Fase 1 está completa (tablas, MCP, esquema, runner, prompt del reconciliador), pero
**falta el dato**: la BBDD se reseteó de cero el 2026-10-20 y tiene 1 operación cerrada.
Lanzar el reconciliador ahora gasta cuota para que conteste, con razón, que no hay datos.
Esperar a 20-30 cierres antes de la primera pasada.
