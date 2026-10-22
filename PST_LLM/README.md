# PST_LLM — agentes Claude del bot

Todo lo que usa Claude para decidir operaciones vive aquí. Los datos (BBDD de decisiones,
configuración, ficheros STOP) siguen en `.agents/data/` (en `.gitignore`).

## Ficheros

| Fichero | Qué hace |
|---|---|
| `llm_trader.py` | Agente de un símbolo. Decide al cierre de cada vela, llama a `claude -p` y ejecuta la orden con los límites duros. Tiene `--report`, `--ultimas`, `--once`. |
| `llm_trader_flota.py` | Arranca, para y lista los agentes (`--estado`, `--iniciar`, `--parar`, `--resultados`). La lista de agentes está en el propio fichero. |
| `llm_revisor.py` | Revisa cada 5 min solo las posiciones de los magic 109001-109019 y puede cerrarlas si la tesis se rompió. |
| `llm_telegram_bot.py` | Panel de Telegram de solo lectura: posiciones, cierres, resultados y avisos. |
| `universo.py` | Los 10 símbolos del universo LLM con su clase, magic y modelo. Fuente única. |
| `calendario.py` | Puerta de calendario FTMO: no abre posiciones alrededor de CPI/PPI/RPI/PSNB/inventarios. |
| `registro.py` | Registro compartido: tablas `llm_agentes`, `llm_decisions` y `llm_prompts` en `pst_trading.db`; activar y parar agentes desde la app. |
| `docs/PLAN_AGENTES_V2.md` | Plan vigente de la flota (universo, subagentes, filtro, app, selector). |

## Desde la app

Pestaña Símbolos → "Agentes LLM". Cada símbolo del universo tiene **Simular** (sin órdenes) o **Demo**
(opera en la cuenta demo), y **Parar**. La flecha abre sus decisiones: entrada, respuesta, validación,
coste y el prompt exacto de esa versión.

Un agente activado desde la app arranca siempre con magic y modelo del `universo.py`.
Los cambios de código en `llm_trader.py` solo aplican a los agentes que se reinician.

## Puertas antes de cada llamada al modelo

1. Mercado sin cotizaciones nuevas durante 20 min → no se llama (noche, fin de semana).
2. Sin posición: calendario FTMO (bloquea 30 min antes y 15 min después de un evento restringido).
3. Filtro de setup (solo agentes con `sin_filtro: false`).
4. Cuota: freno al 60 % de la ventana de 5 h y al 70 % semanal.

## Uso

```
python PST_LLM/llm_trader_flota.py --estado
python PST_LLM/llm_trader_flota.py --iniciar   # ojo: arranca TODO el registro; BTC/ETH/XAU/EURUSD siguen ahi (ver abajo)
python PST_LLM/llm_trader_flota.py --parar
python PST_LLM/llm_trader.py --report --nombre Agente_US500
```

## Aviso sobre --iniciar

`--iniciar` arranca todos los agentes del registro. BTC (`Agente_LLM`) no tiene fichero STOP propio y lo
arrancaría, y ETH/XAU/EURUSD lo harían porque `--iniciar` borra sus STOP. Para arrancar agentes de
el universo, usa **Simular/Demo** en la app, no `--iniciar`.

## Parar un agente concreto

Crear `.agents/data/llm_trader_<Nombre>.STOP` (por ejemplo `llm_trader_Agente_ETH.STOP`).
Hay que borrar ese fichero antes de volver a arrancar el agente.

El agente BTC (`Agente_LLM`) usa el fichero sin sufijo, `llm_trader.STOP`, que para a **todos**.
Para parar solo BTC hay que parar su proceso.

## Parada de emergencia

Crear `.agents/data/llm_trader.STOP`: para a todos los agentes.

## Estado

- Activos: `Agente_US500`.
- Parados: BTC, ETH, XAU, EURUSD (cripto y forex fuera del universo, ver el plan).
- Pendiente: los 9 agentes del universo nuevo, el filtro previo, la tabla de decisiones en `pst_trading.db`,
  la pestaña en la app y el agente selector.
