# Desarrollo: EAs de MT5 gestionados desde la app y por agentes

Rama: `feature/eas-mt5` · Iniciado: 2026-11-04

Documento de trabajo del proyecto. Aquí van el objetivo, las decisiones, cómo funciona cada pieza,
lo que ya se ha probado y lo que falta. El catálogo de estrategias (qué EA corre en qué símbolo,
con qué versión y con qué resultados) está aparte, en [CATALOGO_EAS.md](CATALOGO_EAS.md).

---

## 1. Objetivo

Una tercera familia de estrategias en la app, además de la flota LLM y el scalping: **EAs de MQL5**.
Son estrategias escritas en código (deterministas, se pueden backtestear), no agentes. Los agentes
trabajan **alrededor** de ellas:

- crean o modifican EAs, los compilan y les hacen backtest;
- proponen añadir una estrategia a un símbolo o mejorar una existente;
- activan y desactivan despliegues.

El usuario no tiene que entrar en MT5 para nada del día a día. Todo se ve y se configura en la app.

## 2. Decisiones tomadas

| # | Decisión | Motivo |
|---|---|---|
| D1 | Proyecto en rama propia `feature/eas-mt5` | Es casi un proyecto nuevo; no mezclar con releases de la flota |
| D2 | Herramientas propias en `PST_MCP/pst_server.py`, no el `metatrader5-mcp` externo | El externo ya no está instalado, no tenía tool de compilar y perdía el `run_id` del tester. La línea de comandos de MT5 funciona (probado, §4) |
| D3 | Dos terminales: **vivo** y **tester** (copia portable) | El terminal vivo no puede lanzar el tester por CLI y solo detecta EAs nuevos al reiniciarse. El de tester se reinicia sin afectar a la operativa |
| D4 | Un **gestor** dentro del terminal vivo pone y quita EAs de los gráficos | Desde Python no se puede adjuntar un EA a un gráfico. Desde MQL5 sí (`ChartOpen` + `ChartApplyTemplate`), probado en §4 |
| D5 | Además, cada EA lee un **fichero de control** (activo / riesgo / cerrar) | Pausar es instantáneo, sin tocar gráficos, y funciona aunque el gestor falle |
| D6 | El agente puede **pausar** solo. **Activar o desplegar una versión nueva** requiere aprobación en la app | Pausar reduce riesgo; activar lo aumenta |
| D7 | "Desactivar" = no abrir operaciones nuevas; lo abierto sigue con su SL/TP. "Cerrar posiciones" es un botón aparte | Evita cierres a mercado por un clic |
| D8 | Toda la configuración vive en la app (BBDD), no en `.ini` ni en inputs a mano | Requisito del usuario: claro y configurable desde la app |

## 3. Arquitectura

```
APP (PST_API + PST_Web)                   AGENTES (Claude Code + MCP pst)
  sección Estrategias > EAs                 ingeniero-mql5, cazador, juez
  BBDD: ea_despliegues, ea_versiones,         │  ea_listar / ea_estado
        ea_backtests, ea_config               │  ea_compilar / ea_backtest / ea_comparar
        │                                     │  ea_proponer / ea_pausar
        │ escribe                             │
        ▼                                     ▼
  Common\Files\PST_EA\control.csv  ◄──── mismas funciones Python (PST_Core/ea/...)
  Common\Files\PST_EA\despliegue.csv          │
        │ lee cada N s                        │ CLI
        ▼                                     ▼
  TERMINAL VIVO (FTMO)                    TERMINAL TESTER (portable)
    PST_Gestor (servicio/EA)                C:\Users\crist\PST_MT5_Tester
      abre/cierra gráficos,                 metaeditor64 /compile
      aplica plantillas con EA              terminal64 /portable /config:tester.ini
    EAs con PST_Control.mqh                 informe .htm + CSV de PST_OptExport
      escriben latido estado_<magic>.csv
```

Regla: **la app y los agentes usan el mismo código Python** para cada acción (`PST_Core/ea/`).
El botón de la app y la tool MCP llaman a la misma función, así nunca se comportan distinto.

### 3.1 Ficheros de intercambio (en `Terminal\Common\Files\PST_EA\`)

| Fichero | Lo escribe | Lo lee | Contenido |
|---|---|---|---|
| `despliegue.csv` | app | gestor | Qué EA va en qué símbolo/TF/magic y con qué plantilla. El gestor lo reconcilia con los gráficos abiertos |
| `control.csv` | app | cada EA | Por magic: `activo`, `mult_riesgo`, `cerrar_posiciones`, `version_esperada` |
| `estado_<magic>.csv` | cada EA | app | Latido: hora, versión, posiciones, último error |
| `gestor_estado.csv` | gestor | app | Gráficos abiertos, EAs cargados, errores al aplicar plantillas |

### 3.2 Ciclo de vida de una estrategia

`idea` → `experimental` (en `Experts/Experimental/`, solo tester) → `propuesta` (backtest IS/OOS
superado, pendiente de aprobación) → `demo riesgo reducido` → `activa` → `pausada` / `retirada`.

Cada cambio de código es una **versión nueva** (`ea_versiones`), con su backtest guardado.
Nunca se sobrescribe la versión que está operando.

## 4. Pruebas de viabilidad (2026-11-04) ✅

Las tres piezas que podían bloquear el proyecto, probadas sin tocar el terminal vivo.
Los ficheros usados están en [pruebas/](pruebas/).

| Prueba | Resultado |
|---|---|
| Compilar por CLI | ✅ `MetaEditor64.exe /compile:<mq5> /inc:<carpeta MQL5> /log:<log>` → `PST_VwapReversion_ADX.ex5`, 0 errores, 2 avisos. El log sale en UTF-16 |
| Backtest sin ventanas | ✅ `terminal64.exe /portable /config:tester_prueba.ini` en la copia portable. 30 s. Informe `.htm` completo. H25 en UK100.cash H1, 2026-06-01 → 2026-11-01, modelo OHLC M1: neto +85,14 €, PF 1,18, Sharpe 1,62, 17 operaciones, DD equity 2,80 %, calidad del historial 100 % |
| Poner y quitar un EA de un gráfico desde MQL5 | ✅ `PST_GestorTest` arrancado con `[StartUp]` del `.ini`: `ChartOpen(EURUSD,H1)` → `ChartApplyTemplate(pst_noop.tpl)` = true → `CHART_EXPERT_NAME` = `PST_TestNoop` → el EA escribió latidos cada 2 s → `ChartClose` = true → `deinit` |

Notas de las pruebas:
- La copia portable es `C:\Users\crist\PST_MT5_Tester` (copia de `C:\Program Files\FTMO Global
  Markets MT5 Terminal` + `accounts.dat`, `servers.dat`, `common.ini` y `MQL5\` del terminal vivo).
  Inicia sesión sola con la cuenta guardada.
- Plantilla mínima que funciona: ver [pruebas/pst_noop.tpl](pruebas/pst_noop.tpl). Va en
  `MQL5\Profiles\Templates\`.
- En la prueba del gestor, `AllowLiveTrading=0`: el EA de prueba no opera.

Pendiente de probar: el gestor **en el terminal vivo**, con un EA recién compilado y sin reiniciar
(¿aparece en la plantilla sin reiniciar el terminal?). Si no, el gestor tendrá que pedir un reinicio
controlado del terminal vivo fuera de horario.

## 5. Configuración en la app (todo editable desde la UI)

**Global (sección EAs > Configuración)**
- Interruptor general de EAs (para todos a la vez).
- Rutas: terminal vivo, terminal tester, MetaEditor. Comprobación con botón "probar".
- Cuenta del tester (login y servidor; la contraseña no se guarda en la BBDD).
- Límites de riesgo comunes con la flota LLM: pérdida diaria €, pérdida total €, riesgo de cartera %,
  riesgo por grupo correlacionado %. Los leen los EAs (vía `control.csv`) y la flota.
- Permisos de los agentes: qué pueden hacer solos (por defecto solo pausar).
- Criterios para aceptar una propuesta: PF mínimo, Sharpe mínimo, nº mínimo de operaciones,
  DD máximo, ambas mitades IS/OOS positivas.
- Backtest por defecto: modelo (OHLC M1 / ticks reales), años de historial, división IS/OOS.

**Por despliegue (EA × símbolo × TF × magic)**
- Activo / pausado, multiplicador de riesgo, horario permitido.
- Parámetros del EA (inputs) con valores por defecto y rango para optimizar.
- Versión en uso e historial de versiones con su backtest.
- Botones: pausar, activar, cerrar posiciones, lanzar backtest, ver informe.

**Vista**
- Estado vivo (latido, posiciones, P&L) frente a lo esperado por el backtest.
- Propuestas de los agentes pendientes de aprobar, con el diff del código y la comparación.

## 6. Fases

### Fase 0 — Viabilidad ✅
- [x] Rama `feature/eas-mt5`
- [x] Compilar por CLI
- [x] Backtest por CLI en terminal portable
- [x] Gestor: poner/quitar EA en un gráfico
- [ ] Gestor en el terminal vivo con EA recién compilado (sin reinicio)

### Fase 1 — Control desde la app
- [ ] `MT5/Include/PST_Control.mqh`: lectura de `control.csv` + latido. Añadirlo a los 9 EAs (versión nueva de cada uno)
- [ ] `PST_Gestor` (servicio o EA) que reconcilia `despliegue.csv` con los gráficos
- [ ] Tablas `ea_despliegues`, `ea_versiones`, `ea_backtests`, `ea_config` (migración en código, con copia previa de la BBDD)
- [ ] `PST_Core/ea/`: funciones de control, lectura de latidos, instalación de `.ex5` y plantillas
- [ ] `PST_API/eas_api.py` + sección "EAs" en `PST_Web` (lista, interruptores, configuración, estado)
- [ ] Stats reales por magic (reutilizar `mt5_sync` y `Tools/update_active_strategies.py`)
- [ ] Tope de riesgo común EAs + flota LLM

### Fase 2 — Compilar y backtest automáticos
- [ ] `PST_Core/ea/tester.py`: genera el `.ini`, lanza el terminal portable, espera, parsea el informe
- [ ] Split IS/OOS y comparación de versiones
- [ ] Tools MCP: `ea_listar`, `ea_estado`, `ea_compilar`, `ea_backtest`, `ea_comparar`, `ea_pausar`, `ea_proponer`
- [ ] Botón "lanzar backtest" en la app y cola de backtests (uno a la vez en el portable)

### Fase 3 — Agentes
- [ ] Actualizar `ingeniero-mql5` y `cazador` a las tools nuevas (quitar `metatrader5-mcp`)
- [ ] Flujo de propuestas EA en la app (aprobar / rechazar, diff, comparación)
- [ ] Revisión periódica: el agente compara vivo vs backtest y propone pausar/mejorar
- [ ] Mitigaciones de sobreajuste (nº de pruebas por hipótesis, OOS obligatorio, ya descritas en `cazador.md`)

## 7. Gotchas conocidos

- El terminal solo escanea `MQL5\Experts\` al arrancar: un `.ex5` nuevo no aparece en el tester
  hasta reiniciar. En el portable no importa (se lanza de cero en cada backtest).
- `WebRequest()` no funciona en el Strategy Tester: un EA que llame a un LLM no se puede backtestear.
- Los logs de MetaEditor y los informes del tester salen en UTF-16.
- Los includes de los EAs son `<PST_V2\...>`: en cada terminal tienen que estar en
  `MQL5\Include\PST_V2\`.
- La cuenta ha cambiado varias veces (1514332119 → 1515029407 → 1515057736 → 1515130055 en
  `common.ini` el 2026-11-04). El tester debe leer la cuenta de la configuración de la app, no estar fijada.
- Magics: EAs en 100xxx–102xxx, flota LLM en 109xxx. `gestion_salidas.py` filtra por magic: no se pisan.
  Reservar rangos en `ea_config` para que un EA nuevo no choque.

## 8. Bitácora

- **2026-11-04** — Arranque. Rama creada. Pruebas de viabilidad de §4 superadas. Documentos
  `DESARROLLO_EAS.md` y `CATALOGO_EAS.md` creados. Copia portable del tester en
  `C:\Users\crist\PST_MT5_Tester`.
- **2026-11-04** — Entorno de pruebas sin release (`Tools/dev/arrancar_dev.py`, ver
  `manual_docs/ENTORNO_PRUEBAS.md`). Todo el desarrollo de EAs se prueba ahí antes de cualquier release.
