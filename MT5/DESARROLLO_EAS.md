# Desarrollo: EAs de MT5 gestionados desde la app y por agentes

Rama: `feature/eas-mt5` · Iniciado: 2026-11-04

Documento de trabajo del proyecto: objetivo, decisiones, cómo funciona cada pieza, qué está probado y qué
falta. El catálogo de EAs (qué hay, en qué estado y con qué resultados) está en [CATALOGO_EAS.md](CATALOGO_EAS.md).
Todo se prueba en el entorno de pruebas (`manual_docs/ENTORNO_PRUEBAS.md`) antes de cualquier release.

---

## 1. Objetivo

Una familia de estrategias más en la app, junto a la flota LLM y el scalping: **EAs de MQL5**. Son estrategias
escritas en código (deterministas, backtesteables), no agentes. Los agentes trabajan **alrededor** de ellas:
crean o modifican EAs, los compilan, les hacen backtest, proponen añadir una estrategia a un símbolo o mejorar
una existente, y pausan si algo va mal.

El usuario no entra en MT5 para el día a día. Todo se ve y se configura en la app → Estrategias → **EAs MT5**.

## 2. Decisiones tomadas

| # | Decisión | Motivo |
|---|---|---|
| D1 | Proyecto en rama propia `feature/eas-mt5` | Es casi un proyecto nuevo |
| D2 | Herramientas propias (`PST_Core/ea` + tools `ea_*` del MCP `pst`), no el `metatrader5-mcp` externo | El externo ya no está, no compilaba y perdía el `run_id` del tester. La línea de comandos de MT5 funciona (§4) |
| D3 | Dos terminales: **vivo** (opera) y **portable** de pruebas (compila y backtestea) | El vivo no puede lanzar el tester por CLI y solo detecta EAs nuevos al reiniciarse |
| D4 | Un **gestor** (`PST_Gestor.mq5`) dentro del terminal vivo pone y quita EAs de los gráficos | Desde Python no se puede adjuntar un EA a un gráfico; desde MQL5 sí (`ChartOpen` + `ChartApplyTemplate`) |
| D5 | Cada EA lee un **fichero de control** (activo / riesgo / cerrar) cada 5 s | Pausar es instantáneo y no depende del gestor |
| D6 | Un agente solo hace sin aprobación lo que diga `agentes_pueden` (por defecto pausar, compilar, backtest) | Pausar reduce riesgo; activar lo aumenta |
| D7 | Desactivar = no abrir nuevas; lo abierto sigue con su SL/TP. Cerrar posiciones es otra acción | Evita cierres a mercado por un clic |
| D8 | Toda la configuración vive en la app (tabla `ea_config`) | Requisito: claro y configurable desde la app |
| D9 | **Un EA = una estrategia × un símbolo × un timeframe × un magic** (un fichero cada uno) | Requisito del usuario: cada combinación evoluciona por su cuenta |
| D10 | **Nombres sin códigos de hipótesis**: `PST_<Estrategia>_<SIMBOLO>_<TF>` | Requisito del usuario. El código antiguo queda solo como `referencia_historica` en `despliegues.json` |
| D11 | Falla cerrado: si un EA no encuentra `control.csv` o su fila, no abre operaciones | Un EA recién instalado no opera hasta que la app lo active |
| D12 | El entorno de pruebas usa la carpeta de control `PST_EA_DEV` y el terminal portable para instalar | `Common\Files` es compartido por todos los terminales: pruebas nunca puede tocar EAs reales |
| D13 | Toda conexión a MT5 pasa la ruta del terminal (`PST_Core/utils/terminal_mt5.py`) | Incidente del 2026-11-04 (§7) |

## 3. Arquitectura

```
APP (PST_API/eas_api.py + PST_Web EasPanel)        AGENTES (MCP pst: ea_listar, ea_historial,
  Estrategias > EAs MT5                              ea_accion, ea_compilar, ea_backtest, ea_alta)
          │                                                  │
          └──────────────► PST_Core/ea/servicio.py ◄─────────┘   (mismas funciones y permisos)
                              │          │           │
                 BBDD ea_*  ◄─┘          │           └─► mt5cli: MetaEditor /compile, terminal64 /portable /config
                                         ▼                          (terminal portable de pruebas)
                 Common\Files\PST_EA\control.csv      ──► cada EA (PST_Control.mqh), cada 5 s
                 Common\Files\PST_EA\despliegue.csv   ──► PST_Gestor (abre/cierra gráficos)
                 Common\Files\PST_EA\estado_<magic>.csv ◄── latido de cada EA
                 Common\Files\PST_EA\gestor_estado.csv  ◄── lo que ve el gestor
                 <terminal>\MQL5\Profiles\Templates\PST_EA\<id>.tpl   plantilla por EA
```

| Pieza | Fichero |
|---|---|
| Lista de EAs (semilla) | `MT5/despliegues.json` |
| EAs | `MT5/Experts/<categoria>/<id>.mq5` (16) |
| Control desde la app | `MT5/Include/PST_Control.mqh` |
| Gestor | `MT5/Experts/Sistema/PST_Gestor.mq5` |
| Lógica Python | `PST_Core/ea/` (`rutas`, `store`, `control`, `mt5cli`, `servicio`) |
| API | `PST_API/eas_api.py` (`/api/eas/...`) |
| Interfaz | `PST_Web/src/components/EasPanel.jsx` (dentro de `EstrategiaView`) |
| Tools de agentes | `PST_MCP/pst_server.py` (`ea_*`), agentes `ingeniero-mql5` y `cazador` |
| Migración v1→v2 | `Tools/ea/separar_eas.py` (lee los originales del commit `329ff20`) |

### 3.1 Formatos

- `control.csv`: `magic;activo;mult_riesgo;cerrar_token`. Fila `0` = interruptor general. `cerrar_token` nuevo
  (≠ 0 y distinto del último atendido, guardado en una variable global del terminal) = el EA cierra sus posiciones.
- `despliegue.csv`: `id;simbolo;tf;instalar`.
- `estado_<magic>.csv`: `clave;valor` (estado, versión, cuenta, puede_abrir, posiciones, flotante, error…).
- Multiplicador de riesgo: el EA lo limita a [0, 2]; la app a [0, `mult_riesgo_max`] (1,5 por defecto).

### 3.2 Ciclo de vida

`idea` → `experimental` (solo tester) → `propuesta` (IS/OOS superado) → `demo-reducido` → `activa` →
`pausada` / `retirada`. El estado lo cambia solo el usuario. Cada cambio de código sube `PST_EA_VERSION` y
se backtestea con el mismo periodo que la versión anterior.

## 4. Pruebas de viabilidad (2026-11-04) ✅

Ficheros en [pruebas/](pruebas/).

| Prueba | Resultado |
|---|---|
| Compilar por CLI | ✅ `MetaEditor64.exe /compile:<mq5> /inc:<carpeta MQL5> /log:<log>`. Log en UTF-16 |
| Backtest sin ventanas | ✅ `terminal64.exe /portable /config:<ini>` en la copia portable, 30 s, informe `.htm` completo |
| Poner y quitar un EA de un gráfico desde MQL5 | ✅ `ChartOpen` → `ChartApplyTemplate` → `CHART_EXPERT_NAME` correcto → latidos → `ChartClose` |
| Los 16 EAs v2 compilan | ✅ 0 errores, los mismos 2 avisos que la v1 |
| API `/api/eas` en el entorno de pruebas | ✅ activar, pausar, riesgo con tope, interruptor general, cerrar, config, historial; todo en `PST_EA_DEV` |
| Tools MCP `ea_*` | ✅ permisos (pausar sí, activar bloqueado), validación de alta (rechaza códigos H y magics 109xxx) |

## 5. Configuración en la app (pestaña Configuración de EAs MT5)

- Interruptor general (cabecera).
- Terminales: carpeta de datos e instalación del vivo (vacío = autodetectar), terminal portable, cuenta del tester.
- Backtest por defecto: modelo, depósito, divisa, apalancamiento, tiempo máximo.
- Multiplicador de riesgo máximo por EA.
- Criterios para aprobar: PF, Sharpe, operaciones mínimas, DD máximo, IS y OOS positivos.
- Qué pueden hacer los agentes sin aprobación.

Por EA (fila desplegable): estado, activo, multiplicador de riesgo, instalar/quitar del gráfico, cerrar
posiciones, compilar, backtest (con IS/OOS), latido y estado del gestor.

## 6. Fases

### Fase 0 — Viabilidad ✅

### Fase 1 — Control desde la app (código hecho, falta probar en MT5)
- [x] `PST_Control.mqh` (interruptor, riesgo, cierre por token, latido, posiciones por magic)
- [x] 16 EAs v2, uno por despliegue, nombres sin códigos de hipótesis
- [x] `PST_Gestor.mq5`
- [x] Tablas `ea_*`, `PST_Core/ea/`, `PST_API/eas_api.py`, sección EAs MT5 en la web
- [x] Nombres por magic desde `despliegues.json` (historial, Telegram, ESTRATEGIAS_ACTIVAS)
- [x] Probado en MT5 (portable, `PST_EA_DEV`): el gestor instala el EA en 6 s; activar, pausar, riesgo, cierre
      por token y retirada responden en < 6 s
- [x] Backtest de regresión v2 = v1: idéntico (+85,14 €, PF 1,18, 17 ops)
- [x] Bloqueo de aperturas en pruebas (`InpPstBloquearAperturas`): ni `AllowLiveTrading=0` ni `expertmode` quitan el permiso
- [ ] Instalar el gestor en el terminal vivo (una vez) y comprobar si detecta un `.ex5` nuevo sin reiniciar
- [ ] Tope de riesgo común EAs + flota LLM (hoy cada uno vigila el suyo)

### Fase 2 — Compilar y backtest automáticos (código hecho)
- [x] `mt5cli.compilar` / `mt5cli.backtest` + parseo del informe
- [x] IS/OOS y veredicto con los criterios de la app
- [x] Botones y trabajos en segundo plano en la app; tools MCP
- [x] Línea base de 2 años para los 16 EAs (CATALOGO_EAS.md §2.1): 2 aprobados
- [x] Backtests sin el freno de pérdida total (`tester_sin_freno_total`)
- [ ] Repetir los cercanos con ticks reales (modelo 4) y con más historial (GapFill D1)

### Fase 3 — Agentes
- [x] `ingeniero-mql5` y `cazador` con las tools `ea_*` (sin `metatrader5-mcp`)
- [ ] Bandeja de propuestas de EAs en la app (hoy van por `propose_change`, sin pantalla propia)
- [ ] Revisión periódica: comparar vivo contra backtest y proponer pausar o mejorar
- [ ] Optimización de parámetros (el tester lo soporta; falta la tool y el control de sobreajuste)

## 7. Gotchas conocidos

- ⚠️ **INCIDENTE 2026-11-04: `mt5.initialize()` sin ruta se engancha al último terminal arrancado.**
  Al arrancar el portable para las pruebas, producción y sus agentes pasaron a conectarse a él: a las 20:01:51
  `PST_MASTER.exe` (PID 8728) relanzó `C:\Users\crist\PST_MT5_Tester\terminal64.exe` y la flota operó a través
  de él (misma cuenta demo: las órdenes son válidas). El portable queda ocupado y no se puede backtestear.
  **Arreglado en código** (31 llamadas → `PST_Core/utils/terminal_mt5.py`, ruta del terminal FTMO instalado o
  `MT5_TERMINAL_PATH` en `.env`). **Llega a producción con la siguiente release.** Hasta entonces: no arrancar el
  portable con producción abierta. Para recuperar: cerrar la app de producción → cerrar el portable → abrir la app.
- `Common\Files` es compartido por todos los terminales del usuario: por eso el tester escribe sus operaciones en
  `PST_trade_log_tester.csv` y el entorno de pruebas usa `PST_EA_DEV`.
- El terminal solo escanea `MQL5\Experts\` al arrancar: un `.ex5` nuevo no aparece en el tester hasta reiniciar.
  En el portable da igual (arranca de cero en cada backtest). En el vivo está por comprobar con el gestor.
- `WebRequest()` no funciona en el Strategy Tester.
- Logs de MetaEditor e informes del tester en UTF-16. Las etiquetas del informe dependen del idioma del terminal
  (el parser lee español e inglés).
- Includes `<PST_V2\...>`: en cada terminal tienen que estar en `MQL5\Include\PST_V2\` (`mt5cli` los copia).
- La cuenta ha cambiado varias veces (1514332119 → … → 1515130055). Nada la tiene fijada: se lee del terminal.
- El servidor MCP `pst` necesita `pip install -r requirements-dev.txt` (`mcp` 1.x). Sin `PST_ENTORNO=dev` trabaja
  sobre la BBDD y la carpeta de control de **producción**.

## 8. Bitácora

- **2026-11-04** — Arranque. Rama, pruebas de viabilidad, `DESARROLLO_EAS.md` y `CATALOGO_EAS.md`. Portable en
  `C:\Users\crist\PST_MT5_Tester`. Entorno de pruebas sin release.
- **2026-11-04** — Fase 1 y 2 en código: 16 EAs v2 (un fichero por despliegue, nombres sin códigos de hipótesis,
  posiciones por magic: antes un EA podía cerrar la posición de otro EA o de la flota en el mismo símbolo),
  `PST_Control.mqh`, `PST_Gestor.mq5`, `PST_Core/ea`, API, sección EAs MT5, tools MCP `ea_*`, agentes actualizados.
  Incidente de conexión MT5 detectado y arreglado en código (pendiente de release).
- **2026-11-04** — Release v2.8.0 publicada y en producción (conexión a MT5 ya con ruta: producción no volvió a engancharse al portable). Probado en MT5 el control y el gestor; regresión v2 = v1; línea base de 2 años: 2 de 16 aprobados. Backtests de evaluación sin el freno de pérdida total. Bloqueo de aperturas en pruebas.
