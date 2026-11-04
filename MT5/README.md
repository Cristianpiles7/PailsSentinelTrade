# MT5 — EAs de PST

EAs en MQL5 puro para MetaTrader 5 (cuenta FTMO-Demo), gestionados desde la app (Estrategias → **EAs MT5**)
y por agentes (tools `ea_*` del MCP `pst`). Migrados desde `PailsSentinelMT5TradeV2` el 2026-09-02.

- **Qué EAs hay, estado y resultados:** [CATALOGO_EAS.md](CATALOGO_EAS.md)
- **Cómo funciona el sistema, decisiones y pendientes:** [DESARROLLO_EAS.md](DESARROLLO_EAS.md)
- **Lista canónica de EAs:** [despliegues.json](despliegues.json)

## Reglas

- **Un EA = una estrategia × un símbolo × un timeframe × un magic.** Un fichero por combinación; el EA se
  niega a arrancar en otro símbolo o TF.
- Nombre `PST_<Estrategia>_<SIMBOLO>_<TF>`, sin códigos de hipótesis.
- Magic único entre 100000 y 108999 (109xxx es de la flota LLM).
- Todos incluyen `PST_RiskManager.mqh`, `PST_Logger.mqh`, `PST_OptExport.mqh` y `PST_Control.mqh`.
- Las posiciones se buscan por magic, nunca solo por símbolo.

## Estructura

```
MT5/
  despliegues.json          lista de EAs (la app da de alta desde aqui)
  Experts/
    MeanReversion/          PST_BollingerRsi_* (4), PST_GapFill_JP225_D1, PST_RelativeStrength_* (3),
                            PST_VwapAdx_UK100_H1, PST_LiquiditySweep_EURUSD_M5
    TrendFollowing/         PST_MacdMomentum_GER40_H1, PST_Ichimoku_GER40_H1, PST_Ichimoku_CADJPY_H4
    Breakout/               PST_DonchianVolumen_AAPL_H1, PST_DonchianVolumen_TSLA_H1, PST_GapAndGo_AAPL_H1
    Experimental/           EAs en investigacion (solo tester)
    Sistema/                PST_Gestor.mq5 (pone/quita EAs de los graficos segun la app)
  Include/                  PST_RiskManager.mqh, PST_Logger.mqh, PST_OptExport.mqh, PST_Control.mqh
  docs/                     broker-y-datos.md, politica-de-riesgo.md
  pruebas/                  ficheros de las pruebas de viabilidad
```

## Instalación

No se hace a mano: la app (botón **Instalar en el terminal**) o un agente (`ea_accion instalar`) copia los
includes a `MQL5\Include\PST_V2\`, el `.mq5` a `MQL5\Experts\PST_V2\`, compila con MetaEditor, escribe la
plantilla del gráfico y pide al gestor que lo cargue. Entra **pausado**. Una sola vez hay que cargar
`PST_Gestor` en un gráfico cualquiera del terminal vivo (ver DESARROLLO_EAS.md §6).

## Proceso de validación

Prototipo → cribado con coste de spread real → EA en MQL5 → backtest en el Strategy Tester → split
dentro/fuera de muestra (ambas mitades positivas) → demo con riesgo reducido → activa. De ~40 hipótesis del
proyecto original, solo las del catálogo pasaron el ciclo completo.
