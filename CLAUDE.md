# PailsSentinelTrade (PST) — guía operativa

Este archivo lo carga Claude Code automáticamente. Describe dónde está cada cosa, cómo se publica una versión y cómo llega a este PC. Si algo de aquí ya no es cierto, corrígelo.

## 1. Mapa de carpetas

Hay dos mundos separados. No los mezcles.

### Raíz `C:\Users\crist\Desktop\PailsSentinelTrade\` — despliegue local (NO es git)
| Ruta | Qué es |
|---|---|
| `PST_LAUNCHER.exe` | Lanzador. Mira la última Release de GitHub, descarga `PST_MASTER.exe` si es más nueva y lo arranca. |
| `PST_MASTER.exe` | La app (bot + dashboard). Lo reemplaza el launcher en cada actualización. |
| `version.txt` | Versión instalada, con **v** delante (ej. `v2.6.66`). La escribe el launcher. |
| `.env` | Secretos y configuración local (Telegram, Gemini, `WEB_PASSWORD`, `PST_DATA_ROOT`, etc.). Nunca al repo. |
| `PST_Core\data\pst_trading.db` | **BBDD principal** (SQLite, ~450 MB). Es la única fuente de verdad de operaciones y configuración. |
| `PST_Core\data\backups\` | Copias automáticas de la BBDD (`pst_trading_antes_*.db`). |
| `PST_Core\data\pst_trading.db.antes-fechas-*` | Copia antigua de la BBDD. No tocar sin preguntar. |
| `.agents\data\` | BBDD, logs, `.json` y `.err` de cada agente LLM (`llm_trader_<SIMBOLO>.db`, `llm_revisor.db`, `llm_scalping.db`, etc.). Es estado en runtime. |
| `.agents\backup_plan_v3\` | Copia de seguridad de configs de agentes y de un `.db` antiguo. |
| `habblaud\` | Servicio hermano (espejo en `127.0.0.1:4748`). Lo arranca la app si `HABBLAUD_AUTOSTART=1`. Tiene su propio repo y CLAUDE.md. |
| `PST_Startup.log` | Log de arranque de la app. |
| `.tmp.driveupload\`, `.tmp.drivedownload\` | Temporales de sincronización con Drive. No tocar. |

`PST_DATA_ROOT` en `.env` apunta a esta raíz. Por eso la BBDD vive aquí y no dentro del exe.

### `repo\` — código fuente (git)
- Repositorio: `origin` = `https://github.com/Cristianpiles7/PailsSentinelTrade.git`, rama `main`.
- **Aquí se edita, se hace commit, tag y push.** Los comandos git van con `cd` a `repo\`.
- Es público (el launcher consulta la API sin token), así que nada de secretos en el repo.

### Copia que NO se usa: `G:\Mi unidad\PST_DATA_ROOT\`
- Hay un `PST_MASTER.exe` en esa carpeta de Google Drive. **No se usa. Nunca abrirlo.** La app real es `C:\Users\crist\Desktop\PailsSentinelTrade\PST_MASTER.exe`.
- Si aparece un proceso `PST_MASTER.exe` desde `G:\`, es un resto que no hay que arrancar ni tomar como referencia.

## 1b. Entorno de pruebas (sin release)

Sirve para probar cualquier cambio del repo **sin commit, tag, push ni release**. Arranca la app con
Python directamente desde `repo\`, al lado del `PST_MASTER.exe` de producción, sin interferir con él.
Guía completa: `manual_docs/ENTORNO_PRUEBAS.md`.

### Cómo se arranca
- Doble clic en `Tools\dev\PRUEBAS.cmd`, o desde `repo\`: `python Tools/dev/arrancar_dev.py --web`
- Panel con recarga en caliente: **http://127.0.0.1:5174** (franja ámbar "Entorno de pruebas" arriba).
- API de pruebas: http://127.0.0.1:8100. Producción sigue en el **8000** (el 8000 está prohibido en pruebas).
- Cambios en `.jsx`: se ven al momento. Cambios en Python (`PST_API`, `PST_Core`, `PST_LLM`): Ctrl+C y volver a arrancar.
- Contraseña del panel: la misma que en producción (`WEB_PASSWORD` del `.env`).

### Opciones
| Opción | Para qué |
|---|---|
| `--web` | Lanza también Vite (5174). Sin ella, solo API + panel compilado de `PST_Web/dist` (puede estar desfasado) |
| `--refrescar-bd` | Copia de nuevo la BBDD y los `.json` de agentes de producción. Sin ella, reutiliza la copia anterior |
| `--mt5-tester` | Conecta al terminal portable `C:\Users\crist\PST_MT5_Tester` en vez del vivo (para probar EAs) |
| `--permitir-ordenes` | Deja pasar `order_send` (cuenta demo). Solo si el usuario lo pide expresamente |
| `--puerto N` | Otro puerto para la API (por defecto 8100) |

### Qué queda aislado
- BBDD: copia en `C:\Users\crist\PST_DEV\PST_Core\data\pst_trading.db`. La de producción solo se lee para copiarla.
- Órdenes MT5 bloqueadas, agentes LLM/scalping/puntuador no se lanzan, el actualizador tampoco.
- No puede matar procesos que no haya lanzado él (el botón "parar agente" no toca los de producción).
- Sin Telegram, monitor, alertas de calendario, protección de cierre por horario ni habblaud.
- Se comparte: el terminal MT5 vivo en lectura (salvo `--mt5-tester`) y los secretos del `.env`.
- Mecanismo: `PST_ENTORNO=dev` → `PST_Core/config.py` no deja que el `.env` pise rutas y
  `PST_API/main.py` (`ES_DEV`) no arranca las tareas que actúan sobre la cuenta. `/api/health` devuelve `"entorno": "dev"`.

### Flujo de trabajo (obligatorio)
1. Trabajar en una rama, nunca en `main` directamente.
2. Probar en el entorno de pruebas hasta que funcione.
3. Solo entonces: merge a `main` y release (sección 2), pidiendo confirmación al usuario.

### Reglas para Claude
- Comprobar los cambios en el entorno de pruebas **antes** de proponer commit o release. Arrancarlo en
  segundo plano (`python -u Tools/dev/arrancar_dev.py`), probar por la API del 8100 y **pararlo al terminar**
  (matar solo los PID que escuchan en 8100/5174; nunca procesos `PST_MASTER.exe`).
- No usar `--permitir-ordenes` sin que el usuario lo pida.
- Si un cambio añade una tarea de fondo que opera, envía avisos o lanza procesos, debe respetar `ES_DEV`
  (o quedar cubierta por los bloqueos de `Tools/dev/arrancar_dev.py`). Añadirlo a esta lista si hace falta.
- Si se añaden componentes web con la URL de la API fija, usar `import.meta.env.VITE_API_URL` como en `App.jsx`.

## 1c. EAs de MT5 (rama `feature/eas-mt5`)

- Plan, decisiones y pendientes: `MT5/DESARROLLO_EAS.md`. Catálogo (mantenerlo al día en cada cambio): `MT5/CATALOGO_EAS.md`.
- **Un EA = una estrategia × un símbolo × un timeframe × un magic** (un `.mq5` cada uno). Nombres
  `PST_<Estrategia>_<SIMBOLO>_<TF>`, **sin códigos de hipótesis** (H5, H25…). Magic 100000–108999.
- Toda acción sobre EAs pasa por `PST_Core/ea/servicio.py` (la usan la API `/api/eas` y las tools MCP `ea_*`).
- Compilar y backtest solo en el terminal **portable** `C:\Users\crist\PST_MT5_Tester`, nunca en el vivo.
- **Conexión a MT5:** usar siempre `_pst_conectar_mt5(mt5)` / `PST_Core/utils/terminal_mt5.conectar`, nunca
  `mt5.initialize()` sin ruta (se engancha al último terminal arrancado: incidente del 2026-11-04).
- MCP `pst`: requiere `pip install -r requirements-dev.txt`. Sin `PST_ENTORNO=dev` actúa sobre producción.

## 2. Cómo se publica una versión (tag → Release)

Idioma del historial: `vX.Y.Z - descripción en una línea`. Ver también `repo\.claude\commands\release.md` y `repo\.agents\workflows\release.md`.

1. **Revisar cambios sin commit** en `repo\`: `git status`. Ahora mismo hay `PST_LLM/scalping.py` y `PST_LLM/scalping_config.py` modificados sin commit. `git add -A` los mete todos en la release: decide antes si deben ir.
2. **Subir la versión** (solo número, sin "v"):
   - `repo\version.txt` (ej. `2.7.0`). Es el que empaqueta la Action.
   - `repo\PST_UTIL\version.txt`.
   - `repo\PST_API\main.py`: `version="X.Y.Z"` (está en la clase FastAPI, buscar `version=`).
   - `repo\PST_Web\src\App.jsx`: la cadena `PST-CORE: VX.Y.Z` **ya no existe** en ese archivo; si el release.md la menciona, omitir ese paso y buscar otra ubicación si hace falta.
3. **Commit**: `git commit -m "vX.Y.Z - descripción"`. **Sin** línea `Co-Authored-By` en el commit de release (regla de `release.md`).
4. **Tag**: `git tag vX.Y.Z`. Si ya existía: `git tag -d vX.Y.Z` y `git push origin --delete vX.Y.Z`. Si ya había una Release con ese tag, bórrala también en GitHub antes.
5. **Push** — pedir confirmación al usuario antes de ejecutar:
   - `git push origin main`
   - `git push origin vX.Y.Z`
6. **Action** `.github/workflows/build-release.yml` se dispara con el tag `v*`. En ~10 min:
   - compila `PST_MASTER.exe` (PyInstaller, incluye `PST_Web/dist`) y `PST_LAUNCHER.exe`;
   - inyecta `DIST_TOKEN` (secreto de GitHub) en `Tools/_dist_token.py` solo en el build;
   - crea la Release con 3 assets: `PST_MASTER.exe`, `PST_LAUNCHER.exe`, `version.txt`.
   - Ver el resultado en GitHub → Actions. `gh` no está instalado en este PC; usar la web o instalar `gh`.

## 3. Cómo llega la actualización a este PC

- Abres `PST_LAUNCHER.exe` → consulta `api.github.com/repos/Cristianpiles7/PailsSentinelTrade/releases/latest` → compara el `tag_name` con `version.txt` local → si son distintos, descarga `PST_MASTER.exe`, lo sustituye, escribe el tag en `version.txt` y arranca la app.
- La app también avisa de versión nueva (`PST_API/updater_api.py`) y puede instalarla desde el dashboard. Comprueba la versión contra `app.version`.
- **El launcher no se actualiza solo.** Si cambia `Tools/updater_launcher.py`, copiar a mano `PST_LAUNCHER.exe` del asset de la Release a la raíz.
- **Gotcha de formato:** `version.txt` local debe ser `vX.Y.Z` (con v), igual que el tag. Si escribes `2.7.0` a mano en la raíz, el launcher ve que es distinto y re-descarga en cada arranque.
- Estado actual: la raíz tiene `v2.6.66`; el último tag es `v2.7.0`. La próxima vez que se abra el launcher debería actualizar.

## 4. Base de datos

- Ruta: `C:\Users\crist\Desktop\PailsSentinelTrade\PST_Core\data\pst_trading.db`.
- Es SQLite. Los `.db` por agente están en `.agents\data\`.
- Está en `.gitignore` (`*.db`, `data/`). Nunca commitear.
- Una release **no toca** la BBDD: el exe nuevo se pone encima y la base se queda. Las migraciones van en código, no a mano.
- Antes de cualquier operación de riesgo (migración, limpieza, `Tools/RESET_DATABASE.py`), hacer copia en `PST_Core\data\backups\` y **preguntar antes**.
- Para inspeccionar: abrir en modo solo lectura. No escribir en la BBDD mientras la app está corriendo.

## 5. Secretos

- `.env` (raíz) contiene tokens de Telegram y Gemini y `WEB_PASSWORD`. Nunca copiar sus valores en chats, docs ni commits.
- `Tools/_dist_token.py` está versionado en git con `DIST_TOKEN = ""`. La Action lo rellena solo en el build. No poner un token real ahí.
- **Pendiente:** `PST_Startup.log` contiene en claro la URL con el token del bot de Telegram (línea `HTTP Request: POST https://api.telegram.org/bot.../sendMessage`). Conviene rotar ese token en BotFather y no loguear URLs con el token.

## 6. Reglas para Claude en este proyecto

- Idioma de trabajo y de comentarios: español.
- Commits que crea Claude: terminar con `Co-Authored-By: Claude Haiku 5.5 <noreply@anthropic.com>`. Excepción: el commit de release (sección 2, paso 3).
- **Pedir confirmación** antes de `git push`, de crear o borrar tags/releases, y de tocar la BBDD.
- No ejecutar `Tools/RESET_DATABASE.py` ni borrar `PST_Core\data\` sin confirmación explícita.
- No hacer commit de `.env`, `*.db`, `*.log` ni de la raíz de despliegue (no es git).
- Los `.exe` y `PST_Core\data` de la raíz son runtime: no borrar ni renombrar sin preguntar.
- Para dudas de estrategias o de la app, leer primero `repo\README.md` y `repo\manual_docs\`.
