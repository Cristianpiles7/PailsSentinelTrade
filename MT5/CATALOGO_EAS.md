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
| `PST_BollingerRsi_GBPUSD_H4` | Bollinger + RSI (reversión a la media) | GBPUSD | H4 | 100503 | 2.0 | **activa ×0,5** |
| `PST_GapFill_JP225_D1` | Relleno de gap de apertura | JP225.cash | D1 | 100700 | 2.0 | pausada |
| `PST_RelativeStrength_AAPL_H1` | Fuerza relativa frente a US500.cash | AAPL | H1 | 101000 | 2.0 | pausada |
| `PST_RelativeStrength_MSFT_H1` | Fuerza relativa frente a US500.cash | MSFT | H1 | 101001 | 2.0 | pausada |
| `PST_RelativeStrength_TSLA_H1` | Fuerza relativa frente a US500.cash | TSLA | H1 | 101002 | 2.0 | pausada |
| `PST_VwapAdx_UK100_H1` | Reversión a VWAP de sesión + ADX | UK100.cash | H1 | 101100 | 2.0 | pausada |
| `PST_LiquiditySweep_EURUSD_M5` | Fade de barrido de liquidez | EURUSD | M5 | 101800 | 2.0 | pausada |
| `PST_MacdMomentum_GER40_H1` | Momentum MACD + ADX + EMA200 | GER40.cash | H1 | 101300 | 2.0 | pausada |
| `PST_Ichimoku_GER40_H1` | Tendencia Ichimoku (nube + Tenkan/Kijun) | GER40.cash | H1 | 102001 | 2.0 | **activa ×0,5** |
| `PST_Ichimoku_CADJPY_H4` | Tendencia Ichimoku (nube + Tenkan/Kijun) | CADJPY | H4 | 102003 | 2.0 | pausada |
| `PST_DonchianVolumen_AAPL_H1` | Ruptura Donchian + volumen + tendencia | AAPL | H1 | 101900 | 2.0 | pausada |
| `PST_DonchianVolumen_TSLA_H1` | Ruptura Donchian + volumen + tendencia | TSLA | H1 | 101901 | 2.0 | **activa ×0,5** |
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

| 2026-11-04 | VwapAdx_UK100_H1 **v2.0** | 2026-06-01 → 2026-11-01 | OHLC M1 | +85,14 | 1,18 | 1,62 | 17 | 2,80 % | **Regresión v2 = v1: idéntico** |

### 2.1 Línea base v2.0 — 2 años (2024-11-01 → 2026-11-01), OHLC M1, 10 000 €, criterios por defecto

Completo + IS (1.ª mitad) + OOS (2.ª mitad). Criterios: PF ≥ 1,2 · Sharpe ≥ 1 · ≥ 30 ops · DD ≤ 10 % · IS y OOS > 0.
Lanzada desde la app de producción (guardada en su BBDD) salvo las 3 marcadas con *, repetidas sin el freno de
pérdida total (ver nota).

| EA | Neto € | PF | Ops | DD % | IS € | OOS € | Veredicto | Lectura |
|---|---:|---:|---:|---:|---:|---:|---|---|
| DonchianVolumen_TSLA_H1 | +833,50 | 2,61 | 41 | — | +361,90 | +448,08 | ✅ **APROBADO** | El mejor: sólido en ambas mitades |
| BollingerRsi_GBPUSD_H4 | +381,73 | 1,28 | 58 | — | +39,02 | +337,25 | ✅ **APROBADO** | IS justo; vigilar |
| RelativeStrength_TSLA_H1 | +333,74 | 1,16 | 108 | — | +62,54 | +276,29 | ❌ PF < 1,2 | A las puertas; candidato a ajuste |
| GapFill_JP225_D1 | +204,34 | 1,72 | 16 | — | +10,79 | +193,53 | ❌ pocas ops | Diario: necesita más historial, no descartar |
| RelativeStrength_MSFT_H1 | +147,28 | 1,68 | 22 | — | +0,95 | +146,33 | ❌ pocas ops | 1 sola op en IS: muestra insuficiente |
| Ichimoku_GER40_H1 * | +2304,96 | 1,18 | 168 | 14,65 | +1625,39 | +578,06 | ❌ PF, DD | Mucho neto pero DD alto; candidato a ajuste de riesgo |
| VwapAdx_UK100_H1 | +182,48 | 1,10 | 68 | — | +239,12 | −55,35 | ❌ PF, OOS | Era el mejor del proyecto original (Sharpe 5,45). Repetir con ticks reales |
| GapAndGo_AAPL_H1 | +204,41 | 1,08 | 132 | — | +621,97 | −383,84 | ❌ PF, OOS | Pierde en la 2.ª mitad |
| DonchianVolumen_AAPL_H1 | +123,35 | 1,15 | 41 | — | +355,50 | −227,57 | ❌ PF, OOS | Pierde en la 2.ª mitad |
| BollingerRsi_EURGBP_H4 | +149,15 | 1,11 | 55 | — | −0,27 | +149,42 | ❌ PF, Sharpe | Flojo |
| BollingerRsi_AUDCAD_H4 | +65,14 | 1,07 | 38 | — | +253,45 | −191,63 | ❌ PF, OOS | Flojo |
| LiquiditySweep_EURUSD_M5 * | +279,95 | 1,01 | 357 | 20,25 | +152,66 | +123,13 | ❌ PF, DD 20 % | Sin ventaja real y DD alto |
| MacdMomentum_GER40_H1 | −153,84 | 0,99 | 299 | 21,94 | +191,28 | −338,48 | ❌ PF, DD 22 % | Sin ventaja y DD alto |
| RelativeStrength_AAPL_H1 | −400,99 | 0,54 | 36 | — | +45,18 | −444,28 | ❌ | Negativo |
| BollingerRsi_GBPAUD_H4 | −528,31 | 0,73 | 57 | — | −337,86 | −182,21 | ❌ | Negativo en ambas mitades: candidato a retirar |
| Ichimoku_CADJPY_H4 * | −731,35 | 0,70 | 38 | 14,43 | −1202,47 | +701,26 | ❌ | Parámetros de GER40; ajustar para CADJPY o retirar |

Notas:
- "—" en DD: no se guardó en el resumen de la pasada; está en la pestaña Backtests de la app.
- \* **Freno de pérdida total:** con el freno FTMO de 1000 € activo, estos 3 EAs se paraban para siempre en la primera
  mala racha (LiquiditySweep −1020 € en 10 ops, Ichimoku GER40 −1050 € en 17, CADJPY −1004 € en 14) y el backtest no
  medía la estrategia. Desde ahora los backtests de evaluación van sin ese freno (`tester_sin_freno_total`,
  configurable en la app; el freno diario se mantiene). En la BBDD de producción quedan las pasadas con freno; las
  de esta tabla son las repetidas sin freno (entorno de pruebas).
- Resumen: **2 aprobados** de 16 (DonchianVolumen TSLA, BollingerRsi GBPUSD). 4 cerca o con poca muestra
  (RelativeStrength TSLA/MSFT, GapFill JP225, Ichimoku GER40). Ninguno se activa sin decisión del usuario.

## 3. Historial de cambios

| Fecha | EA | Cambio | Quién | Aprobado por |
|---|---|---|---|---|
| 2026-09-02 | Los 9 EAs | Migrados desde `PailsSentinelMT5TradeV2` a `MT5/` | — | — |
| 2026-11-04 | DonchianVolumen_TSLA_H1, BollingerRsi_GBPUSD_H4, Ichimoku_GER40_H1 | Instalados en el terminal vivo y **activados con riesgo ×0,5** (interruptor general encendido). Aprobados en la línea base (Ichimoku con ×0,5) | Claude | Usuario |
| 2026-11-04 | Todos | v2.0: un fichero por despliegue (9 → 16), control desde la app, posiciones por magic, nombres sin códigos de hipótesis. Magics nuevos: 100501–100503, 101001–101002 | Claude | Usuario |

## 4. Ideas y descartadas

De las ~40 hipótesis del proyecto original, solo las de arriba pasaron el ciclo completo.

| Idea | Estado | Motivo / siguiente paso |
|---|---|---|
| `PST_EmaCrossTimingTest_EURUSD_M5` (en `Experts/Experimental/`) | experimental | EA de diagnóstico (magic 109900, rango de la flota LLM). Sin control de la app. Revisar si sigue haciendo falta |
| LiquiditySweep EURUSD M5 variante B (magic 101801) | retirada | No migrada. Si se recupera, EA propio con magic nuevo |

## 5. Taller de EAs

Lo escriben los agentes automáticos `mejora-eas` (cada noche, un EA no aprobado, máx. 3 variantes) y
`nuevas-eas` (domingos, una hipótesis nueva). Variantes en `MT5/Experts/Experimental/` (magics 105000–106999
para mejoras, 107000–108999 para nuevas). Nada se activa sin el usuario. Ver DESARROLLO_EAS.md §9.

| Fecha | Agente | EA original / hipótesis | Variante | Qué cambia | Neto € | PF | Ops | DD % | IS € | OOS € | Veredicto |
|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 2026-11-04 | (manual) | Ichimoku_GER40_H1 | parámetros | `InpRiskPercent` 1,0 → 0,5 | +1168,12 | 1,20 | 168 | 7,42 | +824,92 | +324,18 | ✅ APROBADO (= multiplicador ×0,5 en la app) |
| 2026-11-04 | mejora-eas | GapFill_JP225_D1 | original, más historial | Sin cambios; 5 años (2021-11-01 → 2026-11-01). Historial completo (1293 barras D1, 99 %), pero ninguna op entre 2022-03 y 2024-10 | +227,84 | 1,65 | 20 | 1,77 | −22,97 (3 ops) | +250,79 (17 ops) | ❌ 20 ops < 30, IS < 0 |
| 2026-11-04 | mejora-eas | GapFill_JP225_D1 | parámetros | `InpGapAtrMult` 0,5 → 0,3 (el CFD cotiza casi 24 h y el gap rara vez llega a 0,5×ATR). Periodo 2024-11-01 → 2026-11-01 | −26,06 | 0,96 | 32 | 3,17 | −224,64 | +203,81 | ❌ PF, Sharpe, IS < 0: los gaps pequeños no se rellenan |
