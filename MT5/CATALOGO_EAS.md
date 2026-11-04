# Catálogo de estrategias EA

Registro de todas las estrategias EA: qué hay, dónde corre, en qué estado está y qué resultados tiene.
Se actualiza **cada vez** que cambia algo (alta, versión nueva, activación, pausa, retirada).

- **Fuente de verdad del estado vivo:** la BBDD (`ea_despliegues`, `ea_eventos`, `ea_backtests`), que se ve
  en la app → Estrategias → **EAs MT5**. Lo que diga la app manda sobre este documento.
- **Lista de EAs:** [despliegues.json](despliegues.json). La app da de alta desde ahí los que falten.
- Este archivo es el cuaderno para humanos y agentes: por qué existe cada EA, resultados de referencia e
  historial de decisiones. Desarrollo del sistema: [DESARROLLO_EAS.md](DESARROLLO_EAS.md).

**Reglas:** un EA = una estrategia × un símbolo × un timeframe × un magic. Nombre
`PST_<Estrategia>_<SIMBOLO>_<TF>`, sin códigos de hipótesis. Magic 100000–108999 (109xxx es de la flota LLM).

**Estados:** `idea` · `experimental` · `propuesta` · `demo-reducido` · `activa` · `pausada` · `retirada`

---

## 1. EAs

Versión 2.0 = la misma lógica que la v1 del proyecto original + control desde la app (`PST_Control.mqh`)
+ posiciones por magic. Todos entran **pausados y sin instalar**.

| EA | Estrategia | Símbolo | TF | Magic | Versión | Estado |
|---|---|---|---|---|---|---|
| `PST_BollingerRsi_EURGBP_H4` | Bollinger + RSI (reversión a la media) | EURGBP | H4 | 100500 | 2.0 | pausada |
| `PST_BollingerRsi_AUDCAD_H4` | Bollinger + RSI (reversión a la media) | AUDCAD | H4 | 100501 | 2.0 | pausada |
| `PST_BollingerRsi_GBPAUD_H4` | Bollinger + RSI (reversión a la media) | GBPAUD | H4 | 100502 | 2.0 | pausada |
| `PST_BollingerRsi_GBPUSD_H4` | Bollinger + RSI (reversión a la media) | GBPUSD | H4 | 100503 | 2.0 | pausada |
| `PST_GapFill_JP225_D1` | Relleno de gap de apertura | JP225.cash | D1 | 100700 | 2.0 | pausada |
| `PST_RelativeStrength_AAPL_H1` | Fuerza relativa frente a US500.cash | AAPL | H1 | 101000 | 2.0 | pausada |
| `PST_RelativeStrength_MSFT_H1` | Fuerza relativa frente a US500.cash | MSFT | H1 | 101001 | 2.0 | pausada |
| `PST_RelativeStrength_TSLA_H1` | Fuerza relativa frente a US500.cash | TSLA | H1 | 101002 | 2.0 | pausada |
| `PST_VwapAdx_UK100_H1` | Reversión a VWAP de sesión + ADX | UK100.cash | H1 | 101100 | 2.0 | pausada |
| `PST_LiquiditySweep_EURUSD_M5` | Fade de barrido de liquidez | EURUSD | M5 | 101800 | 2.0 | pausada |
| `PST_MacdMomentum_GER40_H1` | Momentum MACD + ADX + EMA200 | GER40.cash | H1 | 101300 | 2.0 | pausada |
| `PST_Ichimoku_GER40_H1` | Tendencia Ichimoku (nube + Tenkan/Kijun) | GER40.cash | H1 | 102001 | 2.0 | pausada |
| `PST_Ichimoku_CADJPY_H4` | Tendencia Ichimoku (nube + Tenkan/Kijun) | CADJPY | H4 | 102003 | 2.0 | pausada |
| `PST_DonchianVolumen_AAPL_H1` | Ruptura Donchian + volumen + tendencia | AAPL | H1 | 101900 | 2.0 | pausada |
| `PST_DonchianVolumen_TSLA_H1` | Ruptura Donchian + volumen + tendencia | TSLA | H1 | 101901 | 2.0 | pausada |
| `PST_GapAndGo_AAPL_H1` | Gap and go (hueco confirmado tras 1h) | AAPL | H1 | 101902 | 2.0 | pausada |

Notas:
- `PST_VwapAdx_UK100_H1` fue el mejor resultado del proyecto original (Sharpe 5,45).
- Los EAs que antes compartían un fichero para varios símbolos (Bollinger, RelativeStrength, Donchian,
  Ichimoku) se separaron el 2026-11-04. Cada copia parte de los **valores por defecto** del original; los
  inputs concretos con que corrían en el terminal antiguo no se conservaron. Revisar con backtest por símbolo.
- `PST_Ichimoku_CADJPY_H4` usa los parámetros por defecto pensados para GER40 H1. Revisar.
- La variante B del LiquiditySweep (magic 101801) no se ha migrado. Si se quiere, será otro EA.
- Riesgo común (v2.0): 1 % por operación × multiplicador de la app, 4 % de cartera, 2,5 % por grupo
  correlacionado, pérdida diaria máx. 500 €, total máx. 1000 € sobre 10 000 €. Ver [docs/politica-de-riesgo.md](docs/politica-de-riesgo.md).

## 2. Backtests de referencia

Los guarda la app (`ea_backtests`, pestaña Backtests). Aquí solo los que sirven de línea base.

| Fecha | EA / versión | Periodo | Modelo | Neto € | PF | Sharpe | Ops | DD equity | Para qué |
|---|---|---|---|---:|---:|---:|---:|---:|---|
| 2026-11-04 | VwapAdx_UK100_H1 v1 | 2026-06-01 → 2026-11-01 | OHLC M1 | +85,14 | 1,18 | 1,62 | 17 | 2,80 % | Prueba de viabilidad del backtest por CLI. Muestra corta: no juzga la estrategia |

**Pendiente:** backtest de regresión v2 contra v1 (mismo periodo: tiene que salir idéntico) y línea base
de 2 años con IS/OOS para los 16. Bloqueado hasta que producción deje de usar el terminal portable
(ver DESARROLLO_EAS.md §7).

## 3. Historial de cambios

| Fecha | EA | Cambio | Quién | Aprobado por |
|---|---|---|---|---|
| 2026-09-02 | Los 9 EAs | Migrados desde `PailsSentinelMT5TradeV2` a `MT5/` | — | — |
| 2026-11-04 | Todos | v2.0: un fichero por despliegue (9 → 16), control desde la app, posiciones por magic, nombres sin códigos de hipótesis. Magics nuevos: 100501–100503, 101001–101002 | Claude | Usuario |

## 4. Ideas y descartadas

De las ~40 hipótesis del proyecto original, solo las de arriba pasaron el ciclo completo.

| Idea | Estado | Motivo / siguiente paso |
|---|---|---|
| `PST_EmaCrossTimingTest_EURUSD_M5` (en `Experts/Experimental/`) | experimental | EA de diagnóstico (magic 109900, rango de la flota LLM). Sin control de la app. Revisar si sigue haciendo falta |
| LiquiditySweep EURUSD M5 variante B (magic 101801) | retirada | No migrada. Si se recupera, EA propio con magic nuevo |
