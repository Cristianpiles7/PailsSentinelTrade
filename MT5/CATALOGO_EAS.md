# Catálogo de estrategias EA

Registro de todas las estrategias EA: qué hay, dónde corre, en qué estado está y qué resultados
tiene. Se actualiza **cada vez** que cambia algo (alta, versión nueva, activación, pausa, retirada).

Cuando exista la sección EAs de la app, la fuente de verdad será la BBDD (`ea_despliegues`,
`ea_versiones`, `ea_backtests`) y este archivo se regenerará desde ella. Hasta entonces se mantiene
a mano. Desarrollo del sistema: [DESARROLLO_EAS.md](DESARROLLO_EAS.md).

**Estados:** `idea` · `experimental` · `propuesta` · `demo-reducido` · `activa` · `pausada` · `retirada`

---

## 1. Despliegues

Un despliegue = EA × símbolo × timeframe × magic.

| Hipótesis | EA | Símbolo | TF | Magic | Versión | Estado | Instalado en terminal vivo | Notas |
|---|---|---|---|---|---|---|---|---|
| H5 | `PST_MeanReversion_EURGBP_BB` | EURGBP | H4 | 100500 | v1 | pausada | No | Bollinger + RSI |
| H5 | `PST_MeanReversion_EURGBP_BB` | AUDCAD | H4 | 100500 | v1 | pausada | No | Comparte magic con los otros H5 |
| H5 | `PST_MeanReversion_EURGBP_BB` | GBPAUD | H4 | 100500 | v1 | pausada | No | |
| H5 | `PST_MeanReversion_EURGBP_BB` | GBPUSD | H4 | 100500 | v1 | pausada | No | |
| H12 | `PST_MeanReversion_GapFill_D1` | JP225.cash | D1 | 100700 | v1 | pausada | No | Relleno de gap |
| H20 | `PST_RelativeStrength_H1` | AAPL | H1 | 101000 | v1 | pausada | No | Fuerza relativa frente a US500.cash |
| H20 | `PST_RelativeStrength_H1` | MSFT | H1 | 101000 | v1 | pausada | No | |
| H20 | `PST_RelativeStrength_H1` | TSLA | H1 | 101000 | v1 | pausada | No | |
| H25 | `PST_VwapReversion_ADX` | UK100.cash | H1 | 101100 | v1 | pausada | No | Mejor resultado histórico (Sharpe 5,45) |
| H28b | `PST_MacdMomentum_GER40_H1` | GER40.cash | H1 | 101300 | v1 | pausada | No | MACD + ADX + EMA200 |
| H21 | `PST_LiquiditySweepFade_EURUSD_M5` | EURUSD | M5 | 101800 / 101801 | v1 | pausada | No | Ventanas A y B |
| H41 | `PST_DonchianVolumeBreakout_Stocks` | AAPL | H1 | 101900 | v1 | pausada | No | Donchian + volumen |
| H41 | `PST_DonchianVolumeBreakout_Stocks` | TSLA | H1 | 101901 | v1 | pausada | No | |
| H42 | `PST_GapAndGo_AAPL_H1` | AAPL | H1 | 101902 | v1 | pausada | No | Gap and go |
| H45 | `PST_IchimokuTrend_GER40_H1` | GER40.cash | H1 | 102001 | v1 | pausada | No | Ichimoku |
| H45 | `PST_IchimokuTrend_GER40_H1` | CADJPY | H4 | 102003 | v1 | pausada | No | |

Estado a 2026-11-04: los 9 EAs están en el repo pero **no instalados** en el terminal vivo
(no existe `MQL5\Experts\PST_V2\`). Por eso figuran como `pausada`. Ninguno tiene operaciones en la
cuenta actual (ver [ESTRATEGIAS_ACTIVAS.md](../manual_docs/ESTRATEGIAS_ACTIVAS.md)).

Riesgo común a todos (v1): 1 % por operación, 4 % de cartera, 2,5 % por grupo correlacionado,
pérdida diaria máx. 500 €, total máx. 1000 € sobre 10 000 €. Ver [docs/politica-de-riesgo.md](docs/politica-de-riesgo.md).

## 2. Backtests registrados

| Fecha | EA / versión | Símbolo TF | Periodo | Modelo | Neto € | PF | Sharpe | Ops | DD equity | Para qué |
|---|---|---|---|---|---:|---:|---:|---:|---:|---|
| 2026-11-04 | H25 v1 | UK100.cash H1 | 2026-06-01 → 2026-11-01 | OHLC M1 | +85,14 | 1,18 | 1,62 | 17 | 2,80 % | Prueba de viabilidad del backtest por CLI. Muestra corta: no sirve para juzgar la estrategia |

Los backtests originales de validación (proyecto `PailsSentinelMT5TradeV2`) no se migraron. Cuando
la fase 2 esté lista, se relanzarán todos con el mismo criterio y se apuntarán aquí como línea base.

## 3. Historial de cambios

| Fecha | Despliegue | Cambio | Quién | Aprobado por |
|---|---|---|---|---|
| 2026-09-02 | Los 9 EAs | Migrados desde `PailsSentinelMT5TradeV2` a `MT5/` | — | — |
| 2026-11-04 | — | Catálogo creado | Claude | — |

## 4. Ideas y descartadas

Hipótesis que no están desplegadas pero conviene recordar. De las ~40 exploradas en el proyecto
original, solo las 9 de arriba pasaron el ciclo completo.

| Hipótesis | Idea | Estado | Motivo / siguiente paso |
|---|---|---|---|
| — | `PST_EmaCrossTimingTest_EURUSD_M5` (en `Experts/Experimental/`) | experimental | Revisar qué era y si merece seguir |
