# Entorno de pruebas (sin release)

Para probar cambios del código **sin** hacer commit, tag, push ni release. Arranca la app directamente
desde `repo\` con Python, al lado del `PST_MASTER.exe` de producción, sin interferir con él.

## Arrancar

Desde `repo\`:

```
python Tools/dev/arrancar_dev.py --web
```

o doble clic en `Tools\dev\PRUEBAS.cmd` (hace lo mismo).

| URL | Qué es |
|---|---|
| http://127.0.0.1:5174 | Panel con **recarga en caliente** (Vite): cambias un `.jsx` y se ve al momento |
| http://127.0.0.1:8100 | API de pruebas (y el panel compilado en `PST_Web/dist`, que puede estar desfasado) |
| http://127.0.0.1:8000 | **Producción** (el exe). No se toca |

En el panel de pruebas aparece una franja ámbar arriba ("Entorno de pruebas") y la etiqueta
**PRUEBAS** junto al estado de MT5. Si no la ves, estás en producción.

Los cambios en Python (`PST_API`, `PST_Core`, `PST_LLM`) necesitan parar (Ctrl+C) y volver a arrancar.

## Opciones

| Opción | Efecto |
|---|---|
| `--web` | Lanza también Vite (5174) |
| `--refrescar-bd` | Vuelve a copiar la BBDD y las configs de agentes de producción (si no, reutiliza la copia anterior) |
| `--mt5-tester` | Conecta al terminal portable de pruebas (`C:\Users\crist\PST_MT5_Tester`) en vez de al vivo |
| `--permitir-ordenes` | Deja pasar `order_send`. Solo para probar algo concreto en la cuenta demo, con cuidado |
| `--puerto N` | Puerto de la API (por defecto 8100; el 8000 está prohibido) |

## Qué está aislado

| | Producción (exe) | Pruebas |
|---|---|---|
| Código | El del último release | El del repo, tal cual está (rama actual, sin commit) |
| BBDD | `PailsSentinelTrade\PST_Core\data\pst_trading.db` | Copia en `C:\Users\crist\PST_DEV\PST_Core\data\` |
| Datos de agentes | `PailsSentinelTrade\.agents\data\` | Copia de los `.json` en `C:\Users\crist\PST_DEV\.agents\data\` |
| Órdenes a MT5 | Sí | **Bloqueadas** (`order_send` devuelve `None` y lo apunta en consola) |
| Agentes LLM / scalping / puntuador | Arrancan | **No se lanzan** (cualquier proceso de `PST_LLM` o `.agents` queda bloqueado) |
| Matar procesos | — | Solo los suyos. Parar un agente desde el panel de pruebas **no** mata el de producción |
| Telegram, monitor, alertas de calendario | Sí | No |
| Protección de cierre por horario | Sí (cierra posiciones) | No |
| habblaud | Según `.env` | No |
| Actualizador | Sí | Bloqueado |

Lo que **sí** comparten: el terminal MT5 vivo (en lectura, salvo `--mt5-tester`) y los secretos de
`.env` (contraseña del panel, Gemini). La contraseña del panel es la misma que en producción.

Cómo funciona: `Tools/dev/arrancar_dev.py` fija `PST_ENTORNO=dev` y las rutas antes de importar la
app. Con esa variable, `PST_Core/config.py` no deja que el `.env` de producción pise las rutas, y
`PST_API/main.py` (`ES_DEV`) no arranca las tareas que actúan sobre la cuenta. `/api/health`
devuelve `"entorno": "dev"`, que es lo que pinta la franja del panel.

## Flujo de trabajo

1. Trabajar en una rama (p. ej. `feature/eas-mt5`).
2. Probar con el entorno de pruebas hasta que funcione.
3. Solo entonces: merge a `main` y release (ver `CLAUDE.md` §2).

Claude puede arrancarlo solo para comprobar sus cambios (`python Tools/dev/arrancar_dev.py` en
segundo plano) y pararlo al terminar.
