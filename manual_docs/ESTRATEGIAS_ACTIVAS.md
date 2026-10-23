# Estrategias activas (MT5 · FTMO-Demo)

Cuaderno de a bordo de lo que está desplegado. **Solo se reescribe a mano la parte de arriba y
la de abajo; el bloque de estadísticas lo regenera cada noche `Tools/update_active_strategies.py`
(lanzado por el planificador de la flota) desde el historial de MT5.** No edites entre los
marcadores `AUTO`, se pierde.

Parámetros de riesgo comunes a todos los EAs: 1 % por operación, 4 % de riesgo simultáneo de
cartera, 2,5 % por grupo correlacionado, pérdida diaria máx. 500 €, pérdida total máx. 1000 €
(balance inicial 10 000 €). Detalle de cada EA: [MT5/README.md](../MT5/README.md).

## Estadísticas (automático)

<!-- AUTO:START -->
_Actualizado: **2026-10-23 03:00** · cuenta `1515057736` (FTMO-Demo) · balance 9956.00 EUR · equity 9906.68_  
_Historial disponible desde 2026-10-22. Con pocas operaciones el WR no significa nada: mirar la columna N antes de fiarse._

| Estrategia | Magic | Símbolos | TF | N cerradas | WR % | Neto € | PF | Medio €/op | N 7d | Neto 7d € | Abiertas | Flotante € | Última op |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| H5 Bollinger+RSI | 100500 | EURGBP/AUDCAD/GBPUSD/GBPAUD | H4 | 0 | — | +0.00 | — | — | 0 | +0.00 | 0 | +0.00 | — |
| H12 GapFill | 100700 | JP225.cash | D1 | 0 | — | +0.00 | — | — | 0 | +0.00 | 0 | +0.00 | — |
| H20 RelativeStrength | 101000 | AAPL/MSFT/TSLA | H1 | 0 | — | +0.00 | — | — | 0 | +0.00 | 0 | +0.00 | — |
| H25 VWAP+ADX | 101100 | UK100.cash | H1 | 0 | — | +0.00 | — | — | 0 | +0.00 | 0 | +0.00 | — |
| H28b MACD Momentum | 101300 | GER40.cash | H1 | 0 | — | +0.00 | — | — | 0 | +0.00 | 0 | +0.00 | — |
| H21 LiquiditySweep (ventana A) | 101800 | EURUSD | M5 | 0 | — | +0.00 | — | — | 0 | +0.00 | 0 | +0.00 | — |
| H21 LiquiditySweep (ventana B) | 101801 | EURUSD | M5 | 0 | — | +0.00 | — | — | 0 | +0.00 | 0 | +0.00 | — |
| H41 Donchian+Volumen (AAPL) | 101900 | AAPL | H1 | 0 | — | +0.00 | — | — | 0 | +0.00 | 0 | +0.00 | — |
| H41 Donchian+Volumen (TSLA) | 101901 | TSLA | H1 | 0 | — | +0.00 | — | — | 0 | +0.00 | 0 | +0.00 | — |
| H42 GapAndGo | 101902 | AAPL | H1 | 0 | — | +0.00 | — | — | 0 | +0.00 | 0 | +0.00 | — |
| H45 Ichimoku (GER40) | 102001 | GER40.cash | H1 | 0 | — | +0.00 | — | — | 0 | +0.00 | 0 | +0.00 | — |
| H45 Ichimoku (CADJPY) | 102003 | CADJPY | H4 | 0 | — | +0.00 | — | — | 0 | +0.00 | 0 | +0.00 | — |
| Experimento: agente LLM BTCUSD (PST_LLM/llm_trader.py) | 109001 | BTCUSD | M5 | 0 | — | +0.00 | — | — | 0 | +0.00 | 0 | +0.00 | — |
| Experimento: agente LLM ETHUSD (PST_LLM/llm_trader.py) | 109002 | ETHUSD | M5 | 0 | — | +0.00 | — | — | 0 | +0.00 | 0 | +0.00 | — |
| Experimento: agente LLM XAUUSD (PST_LLM/llm_trader.py) | 109003 | XAUUSD | M15 | 0 | — | +0.00 | — | — | 0 | +0.00 | 0 | +0.00 | — |
| Experimento: agente LLM EURUSD (PST_LLM/llm_trader.py) | 109004 | EURUSD | M15 | 0 | — | +0.00 | — | — | 0 | +0.00 | 0 | +0.00 | — |
| Experimento: agente LLM US500.cash (PST_LLM/llm_trader.py) | 109005 | US500.cash | M15 | 0 | — | +0.00 | — | — | 0 | +0.00 | 0 | +0.00 | — |
| ⚠ magic desconocido | 109019 | — | — | 1 | 100.0 | +53.86 | — | — | — | — | — | — | — |
| ⚠ magic desconocido | 109006 | — | — | 2 | 50.0 | -18.80 | — | — | — | — | — | — | — |
| ⚠ magic desconocido | 109013 | — | — | 1 | 0.0 | -12.60 | — | — | — | — | — | — | — |
| ⚠ magic desconocido | 109009 | — | — | 1 | 0.0 | -62.03 | — | — | — | — | — | — | — |
| **TOTAL** | | | | **5** | | **-39.57** | | | | | 4 | -46.68 | |

**Desglose por símbolo**

| Estrategia | Símbolo | N | WR % | Neto € |
|---|---|---:|---:|---:|
<!-- AUTO:END -->

## Estado de los gráficos en MT5

Qué EA hay realmente adjunto a cada gráfico. Revisado a mano el **2026-10-21** con `list_open_charts`.
Un EA que no está en un gráfico no opera, aunque aparezca en el catálogo.

| Estrategia | Magic | Símbolo / TF | EA en el gráfico |
|---|---|---|---|
| H5 Bollinger+RSI | 100500 | EURGBP, AUDCAD, GBPUSD, GBPAUD · H4 | ✅ |
| H21 LiquiditySweep | 101800 / 101801 | EURUSD · M5 (2 gráficos, ventanas horarias distintas) | ✅ |
| H28b MACD Momentum | 101300 | GER40.cash · H1 | ✅ (re-adjuntado 2026-10-21) |
| H45 Ichimoku GER40 | 102001 | GER40.cash · H1 | ✅ (re-adjuntado 2026-10-21) |
| H41 Donchian+Volumen AAPL | 101900 | AAPL · H1 | ✅ |
| H42 GapAndGo | 101902 | AAPL · H1 | ✅ |
| H41 Donchian+Volumen TSLA | 101901 | TSLA · H1 | ⚠ el gráfico lleva magic **101900** (el de AAPL): comparten identidad y las estadísticas se mezclan |
| H12 GapFill | 100700 | JP225.cash · D1 | ❌ gráfico abierto sin EA |
| H20 RelativeStrength | 101000 | AAPL, MSFT, TSLA · H1 | ❌ gráficos abiertos sin EA |
| H25 VWAP+ADX | 101100 | UK100.cash · H1 | ❌ gráfico abierto sin EA |
| H45 Ichimoku CADJPY | 102003 | CADJPY · H4 | ❌ no desplegado (el EA con magic de GER40 estaba mal puesto en CADJPY H1; retirado 2026-10-21) |

## Experimento: agentes LLM (demo)

Agentes Claude que deciden al cierre de cada vela M5 viendo varios marcos (M5, M15, H1, H4, D1) y
eligen en cuál se apoya cada operación. **No son estrategias validadas**: es una prueba de coste y de
razonamiento. Desde la noche del 2026-10-21 **operan de verdad, solo en cuenta demo**.

| Agente | Símbolo | Magic | Filtro previo |
|---|---|---|---|
| Agente_LLM | BTCUSD | 109001 | no |
| Agente_ETH | ETHUSD | 109002 | no |
| Agente_XAU | XAUUSD | 109003 | sí |
| Agente_EURUSD | EURUSD | 109004 | sí |
| Agente_US500 | US500.cash | 109005 | sí |

Un **revisor** (`PST_LLM/llm_revisor.py`) revisa cada 5 min solo las posiciones de estos magic, nunca las
de los EAs ni las manuales, y las cierra si la tesis se rompió (confianza ≥ 0.7). Todo avisa por Telegram.

- Gestión: `python PST_LLM/llm_trader_flota.py --estado | --iniciar | --parar`.
- Consumo y decisiones: `python PST_LLM/llm_trader.py --report [--nombre Agente_ETH]`, `--ultimas 10 --solo-operaciones`.
- Límites duros: 0,5 % de riesgo por operación, stop siempre, una posición por agente, pérdida diaria de 50 €
  por agente y 150 € entre todos, equity mínimo 9.400 €, margen máximo 30 % por orden.
- Apagado de emergencia: crear `.agents/data/llm_trader.STOP` (para a todos los agentes).
- Resultados reales y qué habría pasado sin los cierres a mano: `python PST_LLM/llm_trader_flota.py --resultados`.
- **Panel de Telegram** (`PST_LLM/llm_telegram_bot.py`, solo lectura): botones Abiertas, Cerradas hoy, Ganancias hoy, General,
  Agentes y Cuenta, y un aviso por cada posición de la cuenta que se cierra (con sus datos y el motivo).

## Cambios y notas

- **2026-10-22** — **Retiradas todas las estrategias MT5.** Se cerraron los 11 gráficos con EA adjunto
  (H5 EURGBP/AUDCAD/GBPUSD/GBPAUD, H28b GER40, H21 EURUSD ventanas A y B, H41 AAPL y TSLA, H42 AAPL,
  H45 GER40). No se cerró ninguna posición: en ese momento no había abierta ninguna de esos magic.
  Las únicas posiciones abiertas eran de los agentes LLM. Decisión del usuario: quedarse solo con los
  agentes Claude como experimento.
- **2026-10-21** — Añadido el experimento del agente LLM (BTCUSD, simulación por defecto).
- **2026-10-21** — GER40 se había quedado sin ningún EA: el gráfico donde estaba Ichimoku se había
  cambiado a CADJPY. Re-adjuntados MACD (gráfico H1 grande) e Ichimoku (segundo gráfico H1) mediante
  las plantillas `PST_GER40_Macd.tpl` y `PST_GER40_Ichimoku.tpl` en `MQL5\Profiles\Templates`.
- El historial de la cuenta actual (prueba FTMO) empieza el 2026-10-20, así que todavía hay muy pocas
  operaciones por estrategia. Los WR de la tabla no son concluyentes hasta tener decenas de cierres.
