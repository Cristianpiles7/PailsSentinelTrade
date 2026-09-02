# Broker y Datos — Fase 0

Este documento fija las condiciones reales del bróker/cuenta que se usará para backtesting y demo, ya que el spread, la comisión, el slippage y la calidad de los datos históricos determinan qué estrategias son viables en la práctica. Complétalo antes de dar por cerrada la Fase 0 del plan.

## 1. Bróker y cuenta

Datos leídos directamente de la API de MT5 (`AccountInfoString`/`AccountInfoInteger`) mediante el script `PST_ExportBrokerData.mq5`, ejecutado el 20/08/2026:

Nombre del bróker: **FTMO Global Markets Ltd**
Servidor MT5: **FTMO-Demo**
Login: **1514332119**
Tipo de cuenta: **€10k FTMO Free Trial 2-Step**
Modo de contabilidad: **Hedging** (`ACCOUNT_MARGIN_MODE_RETAIL_HEDGING` — permite posiciones largas y cortas simultáneas en el mismo símbolo)
Tipo de trading: **Demo** (`ACCOUNT_TRADE_MODE_DEMO`)
Moneda de la cuenta: **EUR**
Apalancamiento: **1:100**
Balance en el momento de la lectura: 10.001,13 EUR (referencia, cambia con cada operación)

## 2. Condiciones por instrumento

Datos leídos directamente de la API de MT5 (`SymbolInfoDouble`/`SymbolInfoInteger`) el 20/08/2026. El spread es el valor vivo en el momento de la lectura (es flotante y varía con la liquidez); el resto de campos (contrato, volumen, swap, sesión) son fijos por símbolo.

| Instrumento | Spread (puntos) | Swap largo / corto (puntos) | Lote mín. / máx. / paso | Tamaño contrato | Horario sesión (hora del servidor MT5) | Notas |
|---|---|---|---|---|---|---|
| EURUSD | 1 pt (0.1 pip) | -8.81 / +0.39 | 0.01 / 50 / 0.01 | 100,000 | Lun-Vie 00:05-23:55 | 5 dígitos |
| GBPUSD | 3 pts (0.3 pip) | -5.26 / -5.49 | 0.01 / 50 / 0.01 | 100,000 | Lun-Vie 00:05-23:55 | |
| AUDUSD | 2 pts (0.2 pip) | -3.06 / -6.05 | 0.01 / 50 / 0.01 | 100,000 | Lun-Vie 00:05-23:55 | |
| EURAUD | 7 pts (0.7 pip) | -13.08 / +2.23 | 0.01 / 50 / 0.01 | 100,000 | Lun-Vie 00:05-23:55 | |
| EURGBP | 7 pts (0.7 pip) | -7.00 / +0.17 | 0.01 / 50 / 0.01 | 100,000 | Lun-Vie 00:05-23:55 | |
| AUDNZD | 10 pts (1.0 pip) | +1.38 / -8.03 | 0.01 / 50 / 0.01 | 100,000 | Lun-Vie 00:05-23:55 | |
| USDJPY | 3 pts (0.3 pip) | +2.56 / -17.40 | 0.01 / 50 / 0.01 | 100,000 | Lun-Vie 00:05-23:55 | 3 dígitos |
| XAUUSD (oro) | 40 pts (0.40 en precio) | -93.35 / -15.15 | 0.01 / 100 / 0.01 | 100 oz | Lun-Vie 01:05-23:50 | Spot CFD |
| XAGUSD (plata) | 41 pts (0.041 en precio) | -15.13 / +0.44 | 0.01 / 100 / 0.01 | 5,000 oz | Lun-Vie 01:05-23:50 | Spot CFD |
| US500.cash | 60 pts (0.60 en índice) | -127.64 / -33.97 | 0.01 / 1000 / 0.01 | 1 | Lun-Vie 01:05-23:50 | Spot CFD, spread fijo |
| US30.cash | 210 pts (2.10 en índice) | +949.14 / -2069.84 | 0.01 / 1000 / 0.01 | 1 | Lun-Vie 01:05-23:50 | Spot CFD |
| USOIL.cash | 68 pts (0.068 en precio) | +5.92 / -34.73 | 0.01 / 1000 / 0.01 | 100 | Lun-Vie 01:05-23:50 | Spot CFD |
| DE40.cash (DAX) | — | — | — | — | — | No disponible con ese nombre en este bróker; si se quiere DAX, buscar el símbolo exacto en el Market Watch (puede llamarse distinto, ej. "GER40.cash") |

Notas importantes sobre esta tabla:
- El swap está en **puntos**, no en divisa (`SwapMode = SYMBOL_SWAP_MODE_POINTS` para todos los símbolos anteriores) — para pasarlo a coste en EUR/USD por lote y día hay que multiplicarlo por el valor del punto de cada símbolo, no compararlo directamente entre instrumentos.
- El horario de sesión está en **hora del servidor MT5** (servidor "FTMO-Demo"), no en hora de Madrid. Antes de fijar filtros de sesión en las estrategias, confirma el offset del servidor respecto a Madrid en Herramientas → Opciones → pestaña Servidor (los servidores MetaQuotes/FTMO suelen ir en GMT+2/+3 con DST propio, parecido pero no siempre igual al horario de Madrid).
- **Comisión**: no es una propiedad de símbolo consultable por la API estándar de MQL5 (no aparece en `SymbolInfo*`), así que no se pudo capturar automáticamente. Compruébalo en tu área de cliente de FTMO o en las condiciones de la cuenta — muchas cuentas FTMO tipo "Free Trial" no cobran comisión explícita (todo el coste va en el spread), pero hay que confirmarlo, no asumirlo.
- Todos los símbolos anteriores tienen `TradeMode = SYMBOL_TRADE_MODE_FULL` (trading permitido sin restricciones).

## 3. Calidad de los datos históricos

Fuente de los datos para backtesting: _(datos del propio terminal MT5 del bróker, o proveedor externo — especificar)_
Profundidad histórica disponible (años): _(pendiente)_
¿Los datos son "tick real" o generados/sintéticos por el bróker?: _(pendiente — esto condiciona la fiabilidad del modo "Every tick based on real ticks" del Strategy Tester)_
Huecos o periodos conocidos con datos de mala calidad: _(pendiente)_

## 4. Validación

Antes de avanzar a la Fase 1, confirmar:

- [x] El spread está documentado para cada instrumento objetivo (vía API MT5). — [ ] Falta confirmar la comisión (no capturable por API, revisar condiciones de la cuenta FTMO).
- [ ] Se ha verificado la calidad de los datos históricos (sin huecos grandes, con ticks reales si es posible) — pendiente, ver sección 3.
- [x] El apalancamiento (1:100) y el tamaño mínimo de lote (0.01 en todos los símbolos evaluados) permiten aplicar la política de riesgo por % de capital definida en la Fase 5 del plan.
- [x] Se conoce el horario de sesión de cada instrumento (hora del servidor MT5) — [ ] falta confirmar el offset exacto del servidor respecto a Madrid.

---

*Este documento se referencia desde `PLAN-Estrategias-MT5.md`, sección "Fase 0 — Preparación".*
