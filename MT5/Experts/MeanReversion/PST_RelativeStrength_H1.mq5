//+------------------------------------------------------------------+
//|                          PST_RelativeStrength_H1.mq5              |
//|                              PailsSentinelMT5TradeV2 — Fase 6     |
//|                                                                   |
//| Hipotesis H20 (python/notebooks/backtest_engine.py::               |
//| backtest_h20_relative_strength): idea nueva, distinta de H16      |
//| (que operaba el spread entre dos INDICES) -- aqui es una ACCION   |
//| individual frente al indice de referencia (US500.cash). Cada dia, |
//| ~1h tras la apertura de NY (hora de servidor 16, verificada       |
//| empiricamente), se mide el alpha acumulado desde la apertura:     |
//| Alpha = Retorno(Accion) - Retorno(Indice). Si el alpha es grande  |
//| y el precio esta del lado correcto de su propia EMA20, entra en  |
//| la direccion del alpha. Cierre FORZADO antes del cierre de        |
//| sesion -- nunca overnight (cuenta FTMO de limites ajustados,      |
//| docs/politica-de-riesgo.md).                                      |
//|                                                                   |
//| Validado en Python en AAPL (2024.08-2026.08, unico dato           |
//| disponible: 2 años, no el rango completo 2019-2026 de otras       |
//| hipotesis): ambos años positivos, Sharpe 0.18-0.32, MaxDD 5-8%,   |
//| con coste de spread real. MSFT tambien positivo pero con muestra  |
//| escasa en el primer año (misma reserva que JP225 gap-fill) -- no  |
//| desplegado todavia, solo AAPL.                                    |
//|                                                                   |
//| Logica:                                                            |
//|  - InpSessionOpenHour (hora de servidor, 16 por defecto): al      |
//|    cerrar esa vela H1, se registra su Close como referencia de    |
//|    apertura de la accion y del indice (misma marca de tiempo).    |
//|  - InpCheckHour (17 por defecto): al cerrar esa vela, se calcula  |
//|    el alpha. Si |alpha| >= InpAlphaThreshold y el precio esta del |
//|    lado correcto de la EMA(InpEmaPeriod) propia, entra en la      |
//|    siguiente vela (apertura de mercado, mismo criterio de retraso |
//|    de una vela que el resto de EAs del proyecto).                 |
//|  - InpForceCloseHour (22 por defecto, ultima vela de la sesion    |
//|    regular en este broker): cierre forzado si sigue abierta.      |
//|  - SL nativo de seguridad (ATR), no parte de la hipotesis         |
//|    original. Lotaje por % de riesgo, gestion de riesgo de         |
//|    cartera, limites de cuenta FTMO en importe absoluto.           |
//+------------------------------------------------------------------+
#property copyright "PailsSentinelMT5Trade"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <PST_V2\PST_RiskManager.mqh>
#include <PST_V2\PST_Logger.mqh>
#include <PST_V2\PST_OptExport.mqh>

//--------------------------------------------------------------- inputs
input string   EA_Name             = "PST_RelativeStrength_H1";
input int      InpMagicNumber      = 101000;

input group    "--- Estrategia H20: fortaleza relativa vs indice ---"
input string   InpIndexSymbol      = "US500.cash";
input int      InpEmaPeriod        = 20;
input double   InpAlphaThreshold   = 0.01;  // 1%
input int      InpSessionOpenHour  = 16;    // hora de servidor, apertura de NY (verificado)
input int      InpCheckHour        = 17;
input int      InpForceCloseHour   = 22;

input group    "--- Gestion de riesgo ---"
input int      InpAtrPeriod            = 14;
input double   InpAtrStopMult          = 2.0;   // SL nativo de seguridad, no parte de la hipotesis
input double   InpRiskPercent          = 1.0;
input double   InpMaxDailyDrawdownPct  = 4.0;

input group    "--- Gestion de riesgo de cartera (docs/politica-de-riesgo.md) ---"
input double   InpMaxPortfolioRiskPercent = 4.0;
input string   InpCorrelationGroup   = "";
input double   InpMaxGroupRiskPercent  = 2.5;

input group    "--- Limites de cuenta FTMO (importe absoluto, docs/politica-de-riesgo.md) ---"
input double   InpAccountInitialBalance = 10000.0;
input double   InpMaxDailyLossAmount    = 500.0;
input double   InpMaxTotalLossAmount    = 1000.0;

//--------------------------------------------------------------- state
CTrade trade;
int    handleEma, handleAtr;
datetime lastBarTime = 0;
double dayStartBalance = 0;
datetime currentDay = 0;
string g_correlationGroup[];

datetime g_dayKey = 0;
double   g_dayOpenStockPrice = 0;
double   g_dayOpenIndexPrice = 0;
bool     g_dayEnteredOrSkipped = false;

//+------------------------------------------------------------------+
int OnInit()
  {
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);

   handleEma = iMA(_Symbol, PERIOD_CURRENT, InpEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   handleAtr = iATR(_Symbol, PERIOD_CURRENT, InpAtrPeriod);
   if(handleEma == INVALID_HANDLE || handleAtr == INVALID_HANDLE)
     {
      PST_Log(EA_Name, "ERROR: no se pudo crear algun indicador en OnInit.");
      return(INIT_FAILED);
     }

   if(!SymbolSelect(InpIndexSymbol, true))
     {
      PST_Log(EA_Name, "ERROR: no se pudo seleccionar InpIndexSymbol en Market Watch.");
      return(INIT_FAILED);
     }

   dayStartBalance = PST_CurrentBalance();
   currentDay = 0;
   PST_ParseCsvGroup(InpCorrelationGroup, g_correlationGroup);

   PST_Log(EA_Name, StringFormat("Inicializado en %s %s vs %s. Magic=%d AlphaThreshold=%.2f%% Riesgo=%.1f%%",
           _Symbol, EnumToString(PERIOD_CURRENT), InpIndexSymbol, InpMagicNumber,
           InpAlphaThreshold * 100.0, InpRiskPercent));
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   IndicatorRelease(handleEma);
   IndicatorRelease(handleAtr);
  }

//+------------------------------------------------------------------+
bool IsNewBar()
  {
   datetime t = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(t != lastBarTime)
     {
      lastBarTime = t;
      return true;
     }
   return false;
  }

//+------------------------------------------------------------------+
void UpdateDailyBalanceTracker()
  {
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   datetime dayKey = (datetime)(dt.year * 10000 + dt.mon * 100 + dt.day);
   if(dayKey != currentDay)
     {
      currentDay = dayKey;
      dayStartBalance = PST_CurrentBalance();
      PST_Log(EA_Name, StringFormat("Nuevo dia. dayStartBalance=%.2f", dayStartBalance));
     }
  }

//+------------------------------------------------------------------+
bool HasOpenPosition()
  {
   return PositionSelect(_Symbol) && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber;
  }

//+------------------------------------------------------------------+
//| Precio de cierre de InpIndexSymbol en la vela cuya apertura mas  |
//| se aproxima (sin pasarse) a targetTime -- sincroniza el indice   |
//| (que cotiza casi 24h) con la vela de la accion (sesion regular   |
//| mas corta), igual criterio que idx.asof() en el prototipo Python.|
//+------------------------------------------------------------------+
double IndexCloseAt(datetime targetTime)
  {
   int shift = iBarShift(InpIndexSymbol, PERIOD_CURRENT, targetTime, false);
   if(shift < 0)
      return 0;
   return iClose(InpIndexSymbol, PERIOD_CURRENT, shift);
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   UpdateDailyBalanceTracker();

   if(!IsNewBar())
      return;

   MqlDateTime dtSignal;
   datetime signalBarTime = iTime(_Symbol, PERIOD_CURRENT, 1);
   TimeToStruct(signalBarTime, dtSignal);
   datetime dayKey = (datetime)(dtSignal.year * 10000 + dtSignal.mon * 100 + dtSignal.day);
   int signalHour = dtSignal.hour;

   if(dayKey != g_dayKey)
     {
      g_dayKey = dayKey;
      g_dayOpenStockPrice = 0;
      g_dayOpenIndexPrice = 0;
      g_dayEnteredOrSkipped = false;
     }

   bool hasPosition = HasOpenPosition();

   // Cierre forzado: nunca overnight. Debe comprobar la hora de la vela que ACABA DE
   // EMPEZAR (shift 0), no la que acaba de cerrar (shift 1) -- si se comprueba la cerrada,
   // en simbolos sin cotizacion continua (acciones, sesion regular) el hueco entre el cierre
   // de sesion y la apertura del dia siguiente hace que el cierre forzado no se evalue hasta
   // DESPUES del gap nocturno completo, dejando la posicion expuesta durante toda la noche
   // pese a la intencion de cerrarla siempre el mismo dia.
   MqlDateTime dtNow;
   datetime currentBarTime = iTime(_Symbol, PERIOD_CURRENT, 0);
   TimeToStruct(currentBarTime, dtNow);
   int currentHour = dtNow.hour;

   if(hasPosition && currentHour >= InpForceCloseHour)
     {
      double price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      if(trade.PositionClose(_Symbol))
         PST_LogTrade(EA_Name, "CLOSE", _Symbol, 0, price, 0, 0, "cierre forzado fin de sesion");
      else
         PST_Log(EA_Name, "ERROR al cerrar posicion (cierre forzado): " + IntegerToString(trade.ResultRetcode()));
      return;
     }

   if(hasPosition)
      return;

   // Registrar apertura del dia.
   if(signalHour == InpSessionOpenHour && g_dayOpenStockPrice == 0)
     {
      g_dayOpenStockPrice = iClose(_Symbol, PERIOD_CURRENT, 1);
      g_dayOpenIndexPrice = IndexCloseAt(signalBarTime);
      return;
     }

   if(signalHour != InpCheckHour || g_dayEnteredOrSkipped)
      return;
   if(g_dayOpenStockPrice <= 0 || g_dayOpenIndexPrice <= 0)
      return;

   g_dayEnteredOrSkipped = true;

   double checkPriceStock = iClose(_Symbol, PERIOD_CURRENT, 1);
   double checkPriceIndex = IndexCloseAt(signalBarTime);
   if(checkPriceIndex <= 0)
      return;

   double retStock = checkPriceStock / g_dayOpenStockPrice - 1.0;
   double retIndex = checkPriceIndex / g_dayOpenIndexPrice - 1.0;
   double alpha = retStock - retIndex;

   double emaBuf[];
   ArraySetAsSeries(emaBuf, true);
   double atrBuf[];
   ArraySetAsSeries(atrBuf, true);
   if(CopyBuffer(handleEma, 0, 0, 2, emaBuf) < 2 || CopyBuffer(handleAtr, 0, 0, 2, atrBuf) < 2)
     {
      PST_Log(EA_Name, "ERROR: fallo al copiar buffers de indicadores.");
      return;
     }
   double emaRef = emaBuf[1];
   double atrRef = atrBuf[1];
   if(atrRef <= 0)
      return;

   bool wantLong  = alpha > InpAlphaThreshold && checkPriceStock > emaRef;
   bool wantShort = alpha < -InpAlphaThreshold && checkPriceStock < emaRef;
   if(!wantLong && !wantShort)
      return;

   bool ddExceeded = PST_DailyDrawdownExceeded(InpMaxDailyDrawdownPct, dayStartBalance);
   bool dailyLossExceeded = PST_DailyLossExceeded(InpMaxDailyLossAmount, dayStartBalance);
   bool totalLossExceeded = PST_TotalLossExceeded(InpAccountInitialBalance, InpMaxTotalLossAmount);
   if(dailyLossExceeded)
      PST_Log(EA_Name, StringFormat("Circuit breaker: perdida diaria >= %.2f. No se abren operaciones hoy.", InpMaxDailyLossAmount));
   if(totalLossExceeded)
      PST_Log(EA_Name, StringFormat("Circuit breaker: perdida TOTAL de cuenta >= %.2f. No se abren mas operaciones.", InpMaxTotalLossAmount));
   if(ddExceeded || dailyLossExceeded || totalLossExceeded)
      return;

   if(!PST_PortfolioRiskOk(InpRiskPercent, InpMaxPortfolioRiskPercent))
     {
      PST_Log(EA_Name, StringFormat("Operacion bloqueada: riesgo de cartera ya al limite (max %.1f%%).", InpMaxPortfolioRiskPercent));
      return;
     }
   if(PST_SymbolInArray(_Symbol, g_correlationGroup) &&
      !PST_GroupRiskOk(g_correlationGroup, InpRiskPercent, InpMaxGroupRiskPercent))
     {
      PST_Log(EA_Name, StringFormat("Operacion bloqueada: riesgo del grupo correlacionado ya al limite (max %.1f%%).", InpMaxGroupRiskPercent));
      return;
     }

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(point <= 0)
      return;

   if(wantLong)
     {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl = NormalizeDouble(ask - InpAtrStopMult * atrRef, digits);
      double slDistancePoints = (ask - sl) / point;
      if(slDistancePoints <= 0)
         return;
      double lots = PST_CalcLotByRisk(_Symbol, InpRiskPercent, slDistancePoints);
      if(lots <= 0)
        {
         PST_Log(EA_Name, "Lotaje calculado invalido (0). No se abre operacion larga.");
         return;
        }
      if(!PST_CheckMarginOk(_Symbol, ORDER_TYPE_BUY, lots))
        {
         PST_Log(EA_Name, "Margen insuficiente para compra de " + DoubleToString(lots, 2) + " lotes.");
         return;
        }
      if(trade.Buy(lots, _Symbol, ask, sl, 0, EA_Name))
         PST_LogTrade(EA_Name, "OPEN_BUY", _Symbol, lots, ask, sl, 0, StringFormat("alpha=%.2f%% > umbral, Close>EMA%d", alpha * 100.0, InpEmaPeriod));
      else
         PST_Log(EA_Name, "ERROR al abrir compra: " + IntegerToString(trade.ResultRetcode()));
     }
   else if(wantShort)
     {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl = NormalizeDouble(bid + InpAtrStopMult * atrRef, digits);
      double slDistancePoints = (sl - bid) / point;
      if(slDistancePoints <= 0)
         return;
      double lots = PST_CalcLotByRisk(_Symbol, InpRiskPercent, slDistancePoints);
      if(lots <= 0)
        {
         PST_Log(EA_Name, "Lotaje calculado invalido (0). No se abre operacion corta.");
         return;
        }
      if(!PST_CheckMarginOk(_Symbol, ORDER_TYPE_SELL, lots))
        {
         PST_Log(EA_Name, "Margen insuficiente para venta de " + DoubleToString(lots, 2) + " lotes.");
         return;
        }
      if(trade.Sell(lots, _Symbol, bid, sl, 0, EA_Name))
         PST_LogTrade(EA_Name, "OPEN_SELL", _Symbol, lots, bid, sl, 0, StringFormat("alpha=%.2f%% < -umbral, Close<EMA%d", alpha * 100.0, InpEmaPeriod));
      else
         PST_Log(EA_Name, "ERROR al abrir venta: " + IntegerToString(trade.ResultRetcode()));
     }
  }

//+------------------------------------------------------------------+
double OnTester()
  {
   string fileName = StringFormat("PST_V2_OptResults\\%s_%s_%s.csv",
                                   EA_Name, _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period));
   string header = "AlphaThreshold;EmaPeriod;NetProfit;ProfitFactor;Trades;Sharpe;MaxDDPercent;ExpectedPayoff";
   string line = StringFormat("%.4f;%d;%.2f;%.3f;%d;%.3f;%.2f;%.2f",
                 InpAlphaThreshold, InpEmaPeriod,
                 TesterStatistics(STAT_PROFIT),
                 TesterStatistics(STAT_PROFIT_FACTOR),
                 (int)TesterStatistics(STAT_TRADES),
                 TesterStatistics(STAT_SHARPE_RATIO),
                 TesterStatistics(STAT_EQUITY_DDREL_PERCENT),
                 TesterStatistics(STAT_EXPECTED_PAYOFF));
   PST_AppendOptCsv(fileName, header, line);
   return TesterStatistics(STAT_PROFIT_FACTOR);
  }
//+------------------------------------------------------------------+
