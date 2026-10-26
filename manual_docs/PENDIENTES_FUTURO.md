# Pendientes a futuro

Lista viva de cosas por hacer, poco a poco. Cada punto dice en qué estado está. Lo que se cierre se marca y no se borra, para tener el historial.

Estados: **pendiente** (no empezado), **decidir** (falta una decisión tuya), **en curso**, **hecho**.

---

## 1. Agente que diagnostica y propone arreglos de código

**Estado:** decidir

Dos capas, en este orden:

**Capa 1: detección determinista (sin LLM).** Un script que comprueba invariantes cada pocos minutos.

| Chequeo | Señal |
|---|---|
| Agente activo sin proceso | `activo=1` en `llm_agentes` sin proceso vivo |
| Agente activo sin decisiones | Última fila de `llm_decisions` antigua con mercado abierto |
| Símbolo mal configurado | Nombre en BBDD o config distinto del universo (p. ej. filas US30 / NAS100.cash) |
| Propuesta caducada sin resolver | `state='propuesta'` con `expira` ya pasado |
| Hora mezclada | `created_at` en UTC y el resto de la app en local |
| Error de entorno | `Claude CLI no encontrado`, `retcode 10027` |

Cada hallazgo se clasifica en **código**, **entorno** o **configuración**. Solo los de código llegan al agente. Los de entorno van a ti con la acción concreta.

**Capa 2: diagnóstico y PR.** Solo para hallazgos de código: leer código y logs, reproducir en una copia, rama `fix/...`, PR con causa raíz y evidencia. El merge y la release los hace una persona.

**Reglas:**
- Prohibido "arreglar" quitando la comprobación que falla.
- Cambios en ejecución, riesgo o cartera: siempre con revisión humana.
- Tope de dos intentos por hallazgo y tope de PRs al día.
- Nunca escribe en `.agents/data` ni en la BBDD de producción.
- Corre en este PC (tiene el repo) y lee logs y BBDD del portátil desde G: en solo lectura.

**Decisiones pendientes:**
- ¿Qué hallazgos pueden llegar a PR automáticamente? (propuesta: solo código fuera de la ruta de ejecución)
- ¿Canal de aviso? (propuesta: bandeja de propuestas)

---

## 2. Aviso por Telegram cuando una orden se rechaza por trading automático desactivado

**Estado:** pendiente

Hoy el error `retcode 10027` solo queda en el log y en la tabla de decisiones. Debería avisar una vez, no en cada intento.

---

## 3. Hora de `created_at` en las propuestas

**Estado:** pendiente (decidido dejarlo como está de momento)

`proposals.created_at` usa `CURRENT_TIMESTAMP` de SQLite, que es UTC. La bandeja la muestra sin convertir, así que va dos horas por detrás en horario de verano. Arreglo: guardar la hora local en `_insertar` o convertirla al mostrarla.

---

## 4. Menú de exclusión de Telegram con muchos agentes

**Estado:** decidir

Con 25 agentes activos, la cuadrícula de botones sigue siendo larga. Opciones:
- **Paginar:** 6 u 8 botones por página con ◀ y ▶.
- **Menú de dos pasos:** primero el grupo (índices, acciones, cripto, forex) y luego el símbolo.

---

## 5. Cuota de Claude con 25 agentes en M1 sin filtro

**Estado:** pendiente de observar

Con el filtro de setup apagado en todos los agentes, la cuota (ventana 5 h y semanal) será lo que frene primero. Hay que vigilar cuántas veces salta el freno y, si es constante, decidir si volver a encender el filtro en algún símbolo o cambiar el reloj a M5.

---

## 6. Bot de Telegram: `historico sembrado` repetido

**Estado:** pendiente de investigar

El mensaje `[bot] historico sembrado` sale cada vez que el bot arranca con la tabla de avisos vacía. Sale varias veces en los logs del portátil, así que o el bot se reinicia a menudo o su base se reinicia. No afecta a las operaciones, pero conviene aclararlo.

---

## 7. Claude CLI en el portátil tras el cambio de equipo

**Estado:** resuelto (incidente puntual)

El 23 de octubre, entre las 17:32 y las 18:14, los agentes no pudieron llamar al modelo (`Claude CLI no encontrado`; 123 decisiones en error). Desde entonces funcionan y el CLI está instalado e iniciado en el portátil. No se requiere acción salvo que vuelva a aparecer ese error tras una reinstalación.

---

## 8. Vigía de logs (`PST_UTIL/vigia_logs.py`)

**Estado:** hecho en local, sin commit

Pinta en la terminal errores, avisos y operaciones, sin Telegram. Pendiente:
- Decidir si se lanza en continuo en el portátil (terminal dedicada) o solo cuando haga falta.
- Subirlo con la próxima release.

---

## 9. Cierre de fin de semana para los agentes LLM

**Estado:** pendiente, prioritario

Los agentes LLM no cierran sus posiciones antes del fin de semana. El barrido de viernes solo existe en el sistema de los EAs (`PST_Core/engine/executor.py`). El SL y el TP están en el broker, pero el gap del lunes y el swap de fin de semana no los controla nadie.

Propuesta: antes del cierre de cada clase de activo un viernes, cerrar a mercado las posiciones de los agentes LLM, sin depender de la decisión del modelo. Cripto queda fuera. Toca la ruta de ejecución, así que requiere revisión tuya antes de la release.

**Avance:** el horario sale del propio broker, sin escribirlo a mano.
- `MT5/Scripts/PST_ExportaSesiones.mq5`: exporta `pst_sesiones.json` a la carpeta Common de MT5 (compila sin errores).
- `PST_LLM/sesiones.py`: calcula las paradas de más de 2 h y `cierre_inminente`, que avisa 15 min antes. Probado con un horario de ejemplo (5/5 casos).
- **Falta:** ejecutar el exportador en el portátil (el terminal de este PC no tiene sesión iniciada) e integrar la llamada en `llm_trader.py` (ruta de ejecución, con tu revisión).
- **Pendiente de decidir:** los festivos del mercado pueden no venir en el horario del broker; hay que comprobarlo con un viernes de festivo.

**Estado (v2.6.44):** implementado y subido. Las paradas se sacan del historial de velas de MT5. Un corte que se repite cada día y dura menos de 8 h no cierra nada. Una parada de más de 2 h que no es diaria (fin de semana) cierra 30 min antes.
- **Decisión tomada:** las acciones se cierran cada noche (su corte nocturno de ~17 h cuenta como parada larga). Si se quiere mantenerlas de un día para otro, subir `DIARIA_MAX_S` a unas 20 h.
- **Sin cubrir:** festivos sueltos; símbolos con menos de dos semanas de historial; posiciones de los EAs (su barrido de viernes es otro sistema).
- **Por verificar en el portátil:** que la detección coincide con el horario del broker para cada símbolo (se verá en el contador de la home).

**Caso de hoy (viernes 23-oct):** MSFT (venta 19:40) y GER40 (venta 19:55) probablemente quedan abiertas. Confirmar en MT5.

---

## 10. Informe de la confianza de los agentes

**Estado:** pendiente

El campo `confidence` que el modelo devuelve en cada decisión no tiene una definición en el prompt, y no está calibrado. Primeros datos (17 operaciones ejecutadas, 15 cerradas): el tramo 0.35–0.40 gana 7 de 10 y el 0.45–0.50, 3 de 5, con resultados que dependen de pocos trades. Con tan pocos datos no sirve para decidir.

Hacer un informe que agrupe las operaciones por tramo de confianza (n, aciertos, resultado neto) y se actualice solo. Cuando haya muestra suficiente, decidir si mantener el campo en la notificación, o definirlo en el prompt como probabilidad de llegar al objetivo antes que al stop.

---

## 11. Lo que ya está hecho y conviene no perder de vista

- **Filtro de setup y tope de llamadas** configurables desde la app (v2.6.40). El tope global es 0 = sin límite.
- **Prompt de los agentes:** criterio de entrada explícito y sin muestra fija (v2.6.40).
- **Botones de Telegram en cuadrícula** de tres por fila (v2.6.41).
- **Propuestas de rotación:** los botones se quitan al decidir desde la app o al caducar (v2.6.41).
- **Pendiente operativo:** tras cada release, reiniciar la app en el portátil y borrar la caché de la webview (`PST_Core/data/webview_storage`).
