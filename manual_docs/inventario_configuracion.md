# Inventario de configuración (Fase 0)

Punto de partida para la reorganización por estrategias. Lista dónde vive hoy cada parámetro, qué valor tiene en disco y qué está duplicado o muerto. No describe el destino: eso está en el plan (Fases 1-5).

Fecha de inventario: 2026-11-03. Rutas de datos: `.agents/data/` (raíz del proyecto) y `PST_Core/data/pst_trading.db`.

## 1. Ficheros JSON de configuración

| Fichero | Lo lee | Claves y valor actual | Notas |
|---|---|---|---|
| `llm_global.json` | `registro.py` | `max_llamadas_dia: 0` | Tope diario global de la flota. Lo escribe Ajustes. |
| `cuota_topes.json` | `registro.py`, `llm_trader.py` | `max_5h: 60`, `max_semanal: 90` | Topes de cuota de la flota. Defecto en código: 60 / 70. |
| `llm_flota_modo.json` | (ver `llm_trader_flota.py`) | `modo: worker` | Modo de ejecución de la flota. |
| `llm_trader_flota.json` | `llm_trader_flota.py`, Telegram | lista de 41 agentes | Registro de agentes de la flota (no es un conjunto de parámetros). |
| `llm_trader_Agente_<sym>.json` (153 ficheros) | `llm_trader.py` (`CONFIG_FILE`) | Claves: `enabled`, `symbol`, `timeframe`, `model`, `effort`, `live`, `sin_filtro`, `max_llamadas_dia`, `max_perdida_dia`, `max_perdida_flota_dia`, `equity_minima`, `sesion_continua`, `reiniciar_cada`, `telegram`, `aviso_cooldown_min`, `timeframes_analisis`, `enfriamiento_min`, `min_cierre_min`, `margen_sesion_min`, `riesgo_pct` | Un fichero por agente. Mismo esquema (`CONFIG_CLAVES`, `llm_trader.py:240`). Esquema duplicado en 153 ficheros. |
| `llm_scalping.json` | `scalping_config.py`, `scalping.py`, `llm_api.py` | `enabled: true`, `model: haiku`, `effort: low`, **`live: true`**, `cadencia_s: 0`, `pausa_minima_s: 1`, `riesgo_pct: 0.25`, `riesgo_por_simbolo: {}`, `max_hold_min: 20`, `max_abiertas: 2`, `max_perdida_dia_r: 3.0`, `sl_atr: [1.0, 2.0]`, `tp_r: [2.0, 3.0]`, `cooldown_tras_stop_min: 10`, `max_spread_sobre_stop: 0.10`, `max_coste_r: 0.10`, `max_llamadas_dia: 1500` | Defectos en `scalping_config.py:DEFECTO`. El fichero real tiene `live: true` (el defecto es `false`): conviene confirmar que es intencionado. |
| `llm_revisor.json` | `llm_revisor.py` | `enabled`, `intervalo_min: 5`, `model: haiku`, `effort: low`, `cerrar: true`, `confianza_minima: 0.85`, `min_cierre_min: 90`, `telegram: true`, `reiniciar_cada: 6`, `tick_espera_s: 15` | Agente revisor. |
| `mejora_prompt_config.json` | `prompts.py`, `llm_mejora_prompt.py` | `activo`, `min_operaciones: 20`, `modelo: haiku`, `esfuerzo: medium`, `cadencia_min: 60`, `max_caracteres: 3000`, `max_operaciones_contexto: 60` | Agente de mejora de prompts. |
| `puntuador_config.json` | `puntuador.py`, `puntuador_api.py` | `activo`, `modelo: haiku`, `cadencia_min: 1`, `solo_mercado_abierto: true`, **`cuota_max_5h: 60`**, **`cuota_max_semanal: 70`** | **Duplicado** con `cuota_topes.json` (ver §4). |
| `rotacion_config.json` | `rotacion.py` | `max_activos: 15`, `max_por_clase: 5`, `ciclo_min: 5`, `cadencia_ranking_min: 15`, `ventana_aprobacion_min: 25`, `margen_cambio: 0.1`, `max_sustituciones_dia: 2`, `penalizacion_cripto: 0.15`, `penalizacion_comision: 0.3`, `ajuste_rendimiento: 0.15`, `coste_max_r: 0.5`, `penalizacion_quitar: 0.2`, `penalizacion_dias: 3`, `inactividad_min: 180`, `accion_entrada: aprobar`, `accion_salida: aprobar` | Se relee en cada pasada. |
| `rotacion_estado.json`, `rotacion_penalizaciones.json` | `rotacion.py` | estado, no configuración | Estado de runtime. |
| `operador.json` | agentes | `equipo`, `latido` | Latido de runtime, no configuración. |
| `calendario_ff.json`, `puntuador.json`, `llm_*.log/err` | varios | datos y logs | Fuera de alcance. |

## 2. Tablas SQLite (`PST_Core/data/pst_trading.db`)

**Configuración viva:**

| Tabla | Filas | Uso |
|---|---|---|
| `bot_config` | 5 | Clave/valor global (`get_config` / `update_config`). Claves: `loss_cooldown_minutes=15`, `hysteresis_minutes=15`, `daily_drawdown_locked=0`, y dos marcas de migración. Las leen `executor.py` y `orchestrator.py` (motor retirado). |
| `llm_agentes` | 38 (15 activos) | Agentes de la flota: `symbol`, `nombre`, `clase`, `magic`, `modelo`, `activo`, `live`, `origen`, `bloqueado`. Parámetros del agente que también viven en su JSON (`modelo`, `live`). |
| `llm_estrategia` | 4 | Estrategia por símbolo: `flota` (XLMUSD), `scalping` (US500, US100, US30). Sin fila = `flota`. Hay un segundo `llm_scalping.db` con tablas propias (ver §3). |
| `llm_prompts`, `llm_criterios` | 28 / 15 | Versiones de prompts y criterio por símbolo. Configuración editable desde la app. |
| `llm_niveles`, `llm_niveles_log` | 44 / 44 | Niveles operativo/prueba/banquillo por clave. |

**Tablas del motor retirado (candidatas a eliminar en Fase 4, pendiente de verificar):**

| Tabla | Filas | Motivo |
|---|---|---|
| `symbols_config` | 61 (15 activos) | Matriz del motor antiguo: `lot_size`, `sl_mult`, `tp_mult`, `score_threshold`, `risk_mode`, `risk_value`, `min_rr`. Alimenta `/api/symbols/{s}/params` y `/api/matrix`, que la app ya no llama. |
| `symbol_strategies` | 244 | Parámetros por símbolo y estrategia legacy (`Scalper Pro`, `PrecisionScalping`...). |
| `channel_config`, `ai_oracle_state`, `symbol_radar`, `regime_history`, `signal_logs`, `trade_context`, `user_levels` | 0 o pocas | Motor retirado (2026-09). |

Tablas de operación (no son configuración, no se tocan): `trades`, `llm_decisions`, `llm_operaciones`, `llm_rotaciones`, `llm_brazo_base`, `llm_prompt_revisiones`, `proposals`, `agent_runs`, `system_logs`.

## 3. Otra base de datos: `llm_scalping.db`

Tablas propias del agente de scalping, en `.agents/data/`. Se lee desde `registro.py` (`SCALPING_DB`). No he inventariado su esquema en detalle; se hará en Fase 1 junto a la migración.

## 4. Duplicados y contradicciones

1. **Topes de cuota en dos sitios.** `cuota_topes.json` (flota) y `puntuador_config.json` (puntuador) tienen los mismos conceptos (`max_5h`, `max_semanal` / `cuota_max_5h`, `cuota_max_semanal`) con valores distintos: `90` frente a `70` en la semanal. Además, el defecto en código de la flota es `70` (`registro.py:608`), y el del puntuador es `70` (`puntuador.py:58`). Hoy el valor que manda depende de qué agente mire qué fichero.
2. **Rangos de scalping duplicados.** `RANGOS` en `scalping_config.py` y `CAMPOS_SCALPING` en `LlmAgentsPanel.jsx:616` definen los mismos min/max a mano.
3. **Esquema de `llm_trader_Agente_<sym>.json` repetido** en 153 ficheros. Cambiar un valor por defecto exige tocar todos.
4. **`live` en dos lugares:** `llm_scalping.json` (`true`) y `llm_agentes.live` en BBDD. Conviene que sea una sola fuente.
5. **Estrategia en dos sitios:** `llm_estrategia` (BBDD) y el campo `strategies` que viene de los EAs de MT5 en `/api/matrix`. Son cosas distintas, pero la UI las mezcla en la misma tarjeta.

## 5. Código muerto o inalcanzable (retirado en Fase 0)

| Dónde | Qué | Por qué era muerto |
|---|---|---|
| `PST_API/main.py` (antes ~1129) | Segundo `GET /api/news/upcoming` | FastAPI sirve la primera ruta registrada (`main.py:655`). |
| `PST_API/main.py` (antes ~1296-1352) | `POST /api/config/symbol` con `SymbolStatusUpdate` y `POST /api/config/strategy` con `StrategyConfigUpdate` | Mismas rutas ya registradas antes (líneas 780 y 784), que ganan. |
| `PST_Web/src/App.jsx` | `riskProfiles`, `fetchProfiles` (llamaba a `/config/risk_profiles`) | Ruta inexistente en la API y nunca invocada. |

## 6. Rutas de API sin uso desde la app (candidatas, no tocadas)

Sin uso en `PST_Web/src` ni en el resto del repositorio: `GET/PUT /api/symbols/{symbol}/params`, `GET /api/matrix`, `POST /api/config/toggle`, `GET/POST /api/config/bot`, `GET/POST/DELETE /api/profiles`, `GET/POST /api/user_levels/{symbol}`. Se eliminan en Fase 4, cuando la tabla `symbols_config` tenga su sustituto.

`PST_Core/dashboard/server.py` es un servidor antiguo con sus propias rutas (`/api/config/symbol`, `/params`). No forma parte de la app actual; no se ha tocado.

## 7. Constantes fijas en código

- `PST_LLM/registro.py:76`: `SIMBOLOS_SCALPING = ("US100.cash", "US30.cash", "US500.cash")`. Debería salir de la BBDD.
- `PST_LLM/llm_trader.py:249`: `TF_ANALISIS_DEFECTO = ["M5", "M15", "H1", "H4", "D1"]`.
- `PST_LLM/scalping.py:37`: `MAGIC = 110101`. Identificador de EA, se queda en código.
- `PST_Web/src/components/LlmAgentsPanel.jsx:397-400`: `RIESGOS` y `ESTRATEGIAS` escritos a mano en el frontend. `ESTRATEGIAS` debería venir de la API.
- `PST_Web/src/App.jsx:80-97`: `TITULOS_VISTA` escrito a mano; se sustituirá por el registro de estrategias.

## 8. Cambios aplicados en Fase 0

- Eliminadas las dos rutas duplicadas y los modelos asociados de `PST_API/main.py`.
- Eliminados `riskProfiles` y `fetchProfiles` de `PST_Web/src/App.jsx`.
- No hay cambios de comportamiento. `PST_API/main.py` compila; el frontend compila con `vite build`.

## 9. Estado de las fases (2026-11-03)

| Fase | Estado | Qué hay |
|---|---|---|
| 0. Inventario y limpieza | Hecha | Este documento. Rutas y código muertos eliminados. |
| 1. Almacén y API | Hecha | `PST_LLM/config_store.py` (tablas `estrategias` y `config_parametros`, esquema único, espejo JSON, migración con copia previa). `PST_API/estrategias_api.py` (rutas `/api/estrategias`, `/api/config/*`). Migración aplicada a la BBDD de producción: 170 claves, 0 distintas, copia en `PST_Core/data/backups/pst_trading_antes_config_store_20261103_195207.db`. |
| 2. Lectores | Hecha, sin ejecutar en vivo | Scalping (`scalping_config.py`), topes de cuota y tope diario (`registro.py`), agentes de la flota (`llm_trader.py:aplicar_config`), puntuador (`puntuador.py`). La BBDD manda; el JSON queda como espejo. |
| 3. Frontend | Hecha, sin prueba visual | Pestaña **Estrategias** (`EstrategiaView.jsx`: Resumen, Símbolos, Configuración) y formulario genérico desde el esquema (`ConfigForm.jsx`). Ajustes conserva solo lo global. |
| 4. Símbolos | Hecha | La Matrix se llama **Símbolos** y tiene filtro por estrategia. Eliminados `/api/matrix` y `/api/symbols/{s}/params` (legacy, sin uso). `symbols_config` se conserva: lo usa `/api/symbols`. |
| 5. Eliminar JSON | **No hecha a propósito** | Los JSON siguen como espejo: `.agents/scheduler.py` lee `cuota_topes.json` y el exe anterior lee el resto. Se retiran tras desplegar y verificar. |

### Despliegue (pendiente)

El `PST_MASTER.exe` que está en marcha usa el código antiguo y sigue leyendo los JSON. Para activar el código nuevo:

1. Reconstruir el exe con el código actual.
2. Antes de arrancarlo, sincronizar el JSON hacia la BBDD, porque la BBDD va a mandar desde ese momento:
   `python PST_LLM/config_store.py --aplicar --sobrescribir` (con `PST_DATA_ROOT` apuntando a la raíz del proyecto).
3. Comprobar `GET /api/config/divergencias`: debe devolver una lista vacía.

Después de ese paso, editar un JSON a mano no tiene efecto: los cambios se hacen desde la app.

### Sin probar en vivo

- `llm_trader.aplicar_config` y `puntuador.py` solo se han compilado: sus dependencias (numpy, pandas, MT5) no están en el entorno de prueba.
- La API no se ha arrancado completa; las rutas nuevas se han probado con `TestClient` contra la BBDD real en solo lectura.
- La interfaz solo se ha compilado (`vite build`); no se ha revisado visualmente.

### Decisiones pendientes

- **Topes de cuota del puntuador vs. flota:** se mantienen separados a propósito (puntuador 60/70, flota 60/90). Unificarlos cambia el comportamiento del puntuador.
- **`live: true` en `llm_scalping.json`:** el defecto en código es `false`. Confirmar que es intencionado.
- **Cambios ajenos en el repositorio:** `PST_LLM/scalping.py` (reescritura en curso a decisiones por lote) y la eliminación de `max_llamadas_dia` en `LlmAgentsPanel.jsx` no son de esta tarea y no se han tocado. `LlmScalpingPanel` quedó sin uso en la app.
