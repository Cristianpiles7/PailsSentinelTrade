# MT5 — Estrategias en producción (PailsSentinelMT5TradeV2)

Migrado desde `PailsSentinelMT5TradeV2` (proyecto separado, EAs en MQL5 puro para MetaTrader 5,
cuenta FTMO-Demo). Contiene solo lo que está **actualmente activo en demo**, no el histórico de
hipótesis descartadas ni las herramientas de cribado en Python — ese proceso de investigación
vive en el proyecto original si hace falta consultarlo.

## Estructura

```
MT5/
  Experts/
    TrendFollowing/   PST_MacdMomentum_GER40_H1.mq5, PST_IchimokuTrend_GER40_H1.mq5
    Breakout/         PST_DonchianVolumeBreakout_Stocks.mq5, PST_GapAndGo_AAPL_H1.mq5
    MeanReversion/    PST_LiquiditySweepFade_EURUSD_M5.mq5, PST_MeanReversion_EURGBP_BB.mq5,
                      PST_MeanReversion_GapFill_D1.mq5, PST_RelativeStrength_H1.mq5,
                      PST_VwapReversion_ADX.mq5
  Include/            PST_RiskManager.mqh, PST_Logger.mqh, PST_OptExport.mqh
                      (los 3 son obligatorios para compilar cualquiera de los EAs anteriores)
  docs/               broker-y-datos.md, politica-de-riesgo.md
```

Para instalar en un terminal MT5: copiar `Include/*.mqh` a `MQL5/Include/PST_V2/` de la carpeta de
datos del terminal, y cada `.mq5` a su subcarpeta correspondiente dentro de `MQL5/Experts/PST_V2/`
(los `#include` de los EAs los referencian como `<PST_V2\PST_RiskManager.mqh>`, etc.). Compilar
con F7 en MetaEditor.

## Estrategias activas (9 despliegues, 9 ficheros)

| Hipótesis | EA | Símbolo(s) | Timeframe | Magic | Idea |
|---|---|---|---|---|---|
| H5 | `PST_MeanReversion_EURGBP_BB.mq5` | EURGBP, AUDCAD, GBPAUD, GBPUSD | H4 | 100500 | Reversión a la media con Bandas de Bollinger + RSI |
| H12 | `PST_MeanReversion_GapFill_D1.mq5` | JP225.cash | D1 | 100700 | Relleno de gap de apertura en índices |
| H20 | `PST_RelativeStrength_H1.mq5` | AAPL, MSFT, TSLA | H1 | 101000 | Fuerza relativa de una acción frente a US500.cash |
| H25 | `PST_VwapReversion_ADX.mq5` | UK100.cash | H1 | 101100 | Reversión a VWAP de sesión filtrada por ADX (rango) — mejor resultado confirmado del proyecto (Sharpe 5.45) |
| H28b | `PST_MacdMomentum_GER40_H1.mq5` | GER40.cash | H1 | 101300 | Momentum de histograma MACD + ADX + EMA200 |
| H21 | `PST_LiquiditySweepFade_EURUSD_M5.mq5` | EURUSD | M5 | 101800 | Fade de barrido de liquidez Londres-NY, TP dinámico por ADX |
| H41 | `PST_DonchianVolumeBreakout_Stocks.mq5` | AAPL (magic 101900), TSLA (magic 101901) | H1 | 101900/101901 | Ruptura de rango Donchian + volumen + filtro de tendencia |
| H42 | `PST_GapAndGo_AAPL_H1.mq5` | AAPL | H1 | 101902 | Gap and go: hueco de apertura confirmado tras 1h |
| H45 | `PST_IchimokuTrend_GER40_H1.mq5` | GER40.cash (magic 102001), CADJPY (magic 102003) | H1 / H4 | 102001/102003 | Tendencia con nube de Ichimoku + cruce Tenkan/Kijun |

Todas comparten `PST_RiskManager.mqh` (1% riesgo/operación, máx. 4% riesgo simultáneo de cartera,
máx. 2.5% por grupo correlacionado, límites de importe absoluto de cuenta FTMO — pérdida diaria
máx. 500€, pérdida total máx. 1000€ sobre balance inicial 10 000€, ver `docs/politica-de-riesgo.md`)
y registran sus operaciones vía `PST_Logger.mqh`.

## Proceso de validación (por si se retoma la investigación)

Cada hipótesis siguió el mismo ciclo antes de llegar aquí: prototipo vectorizado en Python (sin
fricción) → cribado con coste de spread real → si pasa, EA en MQL5 → confirmación individual en
Strategy Tester real (tick data) → split in/out-of-sample (ambas mitades del periodo deben ser
positivas) → solo entonces demo. La mayoría de hipótesis probadas NO llegan a esta carpeta: de
~40 hipótesis exploradas en el proyecto original, estas 9 son las que sobrevivieron el ciclo
completo.

*(Última actualización de esta migración: 2026-09-02)*
