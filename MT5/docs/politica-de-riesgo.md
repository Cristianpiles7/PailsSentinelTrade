# Política de riesgo de cartera

Entregable de la Fase 5 del plan (`docs/PLAN-Estrategias-MT5.md`): reglas de riesgo a nivel de
cartera, no solo por operación o por EA — necesarias antes de correr H5
(`docs/fase6-h5-bollinger-resultados.md`) en varios símbolos a la vez en la misma cuenta.

## Reglas

### 1. Riesgo por operación

**1% del balance por operación** (`InpRiskPercent`), calculado por `PST_CalcLotByRisk()` a
partir de la distancia real al Stop Loss de cada operación. Sin cambios respecto a como ya
funcionaba cada EA individualmente desde Fase 3.

### 2. Riesgo máximo simultáneo de cartera

**4% del balance** (`InpMaxPortfolioRiskPercent`) en riesgo abierto a la vez, sumando **todas**
las posiciones abiertas en la cuenta, de cualquier EA o símbolo — no solo las del EA que evalúa
la regla. Con el 1% por operación por defecto, esto permite un máximo de 4 posiciones
simultáneas en toda la cuenta antes de bloquear nuevas entradas, independientemente de en qué
símbolo se abran.

### 3. Riesgo máximo por grupo correlacionado

**2.5% del balance** (`InpMaxGroupRiskPercent`) para el grupo de símbolos con correlación
positiva medida entre los confirmados de H5: **AUDCAD, GBPAUD, GBPUSD**
(`InpCorrelationGroup`). Basado en la correlación real de retornos H4 2019-2026
(`python/reports/correlacion_h5_simbolos.csv`, ver también
`docs/fase6-h5-bollinger-resultados.md`):

| | EURGBP | AUDCAD | GBPAUD | GBPUSD |
|---|---|---|---|---|
| EURGBP | 1.00 | -0.14 | -0.38 | -0.55 |
| AUDCAD | -0.14 | 1.00 | -0.53 | 0.42 |
| GBPAUD | -0.38 | -0.53 | 1.00 | 0.24 |
| GBPUSD | -0.55 | 0.42 | 0.24 | 1.00 |

**EURGBP queda fuera del grupo** deliberadamente: su correlación con GBPAUD (-0.38) y GBPUSD
(-0.55) es negativa (EURGBP lleva la libra en el denominador, los otros dos en el numerador —
una libra fuerte los mueve en direcciones opuestas), así que actúa como diversificador, no como
amplificador de riesgo. AUDCAD-GBPUSD (+0.42) y GBPAUD-GBPUSD (+0.24) sí son correlaciones
positivas reales — ese trío es el que se limita en conjunto por debajo de lo que sumarían sus
riesgos individuales (3% si las 3 estuvieran abiertas a la vez sin este límite).

Si se añaden más símbolos a H5 en el futuro, recalcular la matriz de correlación
(`python/notebooks/run_correlation_h5_symbols.py`) antes de asumir que un símbolo nuevo
pertenece o no a un grupo existente — no asumir por "misma divisa" sin medir, como ya demostró
ser engañoso aquí.

### 4. Circuit breaker de drawdown diario

**4% del balance de inicio del día** (`InpMaxDailyDrawdownPct`), ya implementado desde Fase 3
(`PST_DailyDrawdownExceeded`) y de alcance de cuenta completa (usa `ACCOUNT_EQUITY`, no una
métrica por EA) — si se supera, ningún EA que comparta esta función abre operaciones nuevas
hasta el día siguiente. Las posiciones ya abiertas no se cierran automáticamente (no hay una
liquidación forzosa); el circuit breaker solo bloquea nuevas entradas.

## Implementación

`MQL5/Include/PST_V2/PST_RiskManager.mqh`, funciones nuevas (además de las ya existentes desde
Fase 3: `PST_CalcLotByRisk`, `PST_CheckMarginOk`, `PST_DailyDrawdownExceeded`):

- `PST_SumOpenRiskPercent(group[])` — suma el riesgo (dinero hasta el SL nativo) de todas las
  posiciones abiertas en la cuenta, opcionalmente filtrado a un grupo de símbolos.
- `PST_PortfolioRiskOk(newTradeRiskPercent, maxPortfolioRiskPercent)` — regla 2.
- `PST_GroupRiskOk(group[], newTradeRiskPercent, maxGroupRiskPercent)` — regla 3.
- `PST_ParseCsvGroup(csv, group[])` / `PST_SymbolInArray(symbol, group[])` — helpers para
  declarar el grupo correlacionado como input de texto en cada EA, sin hardcodear ni recompilar
  por símbolo.

Cada EA llama a `PST_PortfolioRiskOk` y, si su símbolo pertenece a un grupo declarado, también a
`PST_GroupRiskOk`, **antes** de calcular el lotaje y abrir cualquier posición nueva — si
cualquiera de las dos falla, la operación se descarta y se registra en el log
(`PST_Log`), no se fuerza ni se reduce el tamaño, simplemente no se abre esa operación en ese
momento.

**Desplegado en** `MQL5/Experts/MeanReversion/PST_MeanReversion_EURGBP_BB.mq5` (el único EA que
se ejecutará en varios símbolos a la vez en la misma cuenta — EURGBP, AUDCAD, GBPAUD, GBPUSD).
Si se añaden más EAs a producción en el futuro, deben adoptar las mismas funciones compartidas
de `PST_RiskManager.mqh` en vez de reimplementar su propia lógica de riesgo de cartera.

## Pendiente / fuera de alcance de esta fase

- Sensibilidad a costes (spread/comisión) por símbolo antes de demo — mencionado en
  `docs/fase6-h5-bollinger-resultados.md`, no cuantificado todavía símbolo a símbolo (sí se
  cuantificó en general al descartar H11 y el scalping M15, `docs/fase6-h11-donchian-atr-resultados.md`).
- Circuit breaker semanal (solo existe el diario). No se ha considerado necesario todavía dado
  el drawdown moderado observado en los backtests de H5 (9-13%).
- Liquidación forzosa de posiciones abiertas al superar el circuit breaker diario (hoy solo
  bloquea nuevas entradas, no cierra lo ya abierto).
