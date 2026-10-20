//+------------------------------------------------------------------+
//|                       PST_MeanReversion_EURGBP_BB.mq5             |
//|                              PailsSentinelMT5TradeV2 — Fase 6     |
//|                                                                   |
//| Hipotesis H5 (docs/hipotesis-estrategias.md): reversion a la      |
//| media con bandas de Bollinger + confirmacion RSI, en pares de     |
//| forex menores. Diseñada para EURGBP, H4.                          |
//|                                                                   |
//| Prototipo Python (python/reports/resumen_h5_bollinger.md): unica  |
//| hipotesis de toda la ronda de Fase 5/6 donde las 27 combinaciones |
//| del barrido fueron rentables (no un pico aislado). Mejor combo:   |
//| BB(20,2.0), stop 3xATR -> +22.0% en 7.6 años, MaxDD -4.8%, Sharpe |
//| 0.34, 381 operaciones, win rate 66.3%. Racional: a diferencia de  |
//| US500 (donde la reversion RSI+ADX fallo, ver                     |
//| python/reports/resumen_h4_mean_reversion.md), un cruce sin USD    |
//| como EURGBP tiende a rangear mas que a tender.                    |
//|                                                                   |
//| Logica:                                                           |
//|  - Bandas de Bollinger (InpBbPeriod, InpBbStd) sobre Close.       |
//|  - Entra LARGO si Close cierra <= banda inferior Y RSI < umbral   |
//|    de sobreventa; CORTO si Close >= banda superior Y RSI > umbral |
//|    de sobrecompra.                                                |
//|  - Filtro de invalidacion (H5 original): si el ancho de banda se  |
//|    ha expandido bruscamente respecto a su propia media reciente   |
//|    (posible ruptura de rango en curso), no entra.                 |
//|  - Sale cuando el precio vuelve a la banda central (media movil). |
//|  - Stop nativo nada mas entrar a InpAtrStopMult x ATR de la       |
//|    entrada -- red de seguridad no explicita en la hipotesis       |
//|    original pero necesaria para no dejar una reversion fallida    |
//|    sin salida (mismo criterio que en las demas EAs del proyecto). |
//|  - Lotaje por % de riesgo, circuit breaker de drawdown diario.    |
//+------------------------------------------------------------------+
#property copyright "PailsSentinelMT5Trade"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <PST_V2\PST_RiskManager.mqh>
#include <PST_V2\PST_Logger.mqh>
#include <PST_V2\PST_OptExport.mqh>

//--------------------------------------------------------------- inputs
input string   EA_Name             = "PST_MeanReversion_EURGBP_BB";
input int      InpMagicNumber      = 100500;

input group    "--- Estrategia H5: Bollinger + RSI (reversion a la media) ---"
input int      InpBbPeriod         = 20;
input double   InpBbStd            = 2.0;
input int      InpRsiPeriod        = 14;
input double   InpRsiOversold      = 30.0;
input double   InpRsiOverbought    = 70.0;
input int      InpAtrPeriod        = 14;
input double   InpAtrStopMult      = 3.0;   // stop de seguridad, en x ATR
input double   InpMaxBandWidthRatio = 0.0;  // 0 = filtro desactivado; ej. 1.5 = descarta si el ancho de banda supera 1.5x su media reciente

input group    "--- Gestion de riesgo ---"
input double   InpRiskPercent          = 1.0;   // % del balance arriesgado por operacion
input double   InpMaxDailyDrawdownPct  = 4.0;   // circuit breaker diario (%)

input group    "--- Gestion de riesgo de cartera (docs/politica-de-riesgo.md) ---"
input double   InpMaxPortfolioRiskPercent = 4.0;   // riesgo abierto maximo, TODA la cartera (%)
input string   InpCorrelationGroup   = "AUDCAD,GBPAUD,GBPUSD"; // grupo con correlacion positiva medida; EURGBP no incluido (correlacion negativa con los otros 3, ver docs/fase6-h5-bollinger-resultados.md)
input double   InpMaxGroupRiskPercent  = 2.5;   // riesgo abierto maximo dentro del grupo (%)

input group    "--- Limites de cuenta FTMO (importe absoluto, docs/politica-de-riesgo.md) ---"
input double   InpAccountInitialBalance = 10000.0;  // balance INICIAL del reto FTMO, fijo -- no el balance de hoy
input double   InpMaxDailyLossAmount    = 500.0;    // perdida diaria maxima permitida (divisa de la cuenta)
input double   InpMaxTotalLossAmount    = 1000.0;   // perdida total maxima permitida desde InpAccountInitialBalance

//--------------------------------------------------------------- state
CTrade trade;
int    handleBands, handleRsi, handleAtr;
datetime lastBarTime = 0;
double dayStartBalance = 0;
datetime currentDay = 0;
string g_correlationGroup[];

//+------------------------------------------------------------------+
int OnInit()
  {
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);

   handleBands = iBands(_Symbol, PERIOD_CURRENT, InpBbPeriod, 0, InpBbStd, PRICE_CLOSE);
   handleRsi   = iRSI(_Symbol, PERIOD_CURRENT, InpRsiPeriod, PRICE_CLOSE);
   handleAtr   = iATR(_Symbol, PERIOD_CURRENT, InpAtrPeriod);

   if(handleBands == INVALID_HANDLE || handleRsi == INVALID_HANDLE || handleAtr == INVALID_HANDLE)
     {
      PST_Log(EA_Name, "ERROR: no se pudo crear algun indicador en OnInit.");
      return(INIT_FAILED);
     }

   dayStartBalance = PST_CurrentBalance();
   currentDay = 0;

   PST_ParseCsvGroup(InpCorrelationGroup, g_correlationGroup);

   PST_Log(EA_Name, StringFormat("Inicializado en %s %s. Magic=%d BB(%d,%.1f) RSI(%d) %.0f/%.0f Riesgo=%.1f%% Cartera<=%.1f%% Grupo<=%.1f%%",
           _Symbol, EnumToString(PERIOD_CURRENT), InpMagicNumber,
           InpBbPeriod, InpBbStd, InpRsiPeriod, InpRsiOversold, InpRsiOverbought, InpRiskPercent,
           InpMaxPortfolioRiskPercent, InpMaxGroupRiskPercent));
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   IndicatorRelease(handleBands);
   IndicatorRelease(handleRsi);
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
void OnTick()
  {
   UpdateDailyBalanceTracker();

   if(!IsNewBar())
      return;

   double mid[], upper[], lower[], rsiBuf[], atrBuf[];
   ArraySetAsSeries(mid, true);
   ArraySetAsSeries(upper, true);
   ArraySetAsSeries(lower, true);
   ArraySetAsSeries(rsiBuf, true);
   ArraySetAsSeries(atrBuf, true);

   // Necesitamos InpBbPeriod velas extra del ancho de banda para el filtro de expansion,
   // asi que pedimos InpBbPeriod+2 valores de cada buffer de bandas.
   int bandsNeeded = InpBbPeriod + 2;
   if(CopyBuffer(handleBands, 0, 0, bandsNeeded, mid) < bandsNeeded ||
      CopyBuffer(handleBands, 1, 0, bandsNeeded, upper) < bandsNeeded ||
      CopyBuffer(handleBands, 2, 0, bandsNeeded, lower) < bandsNeeded ||
      CopyBuffer(handleRsi, 0, 0, 2, rsiBuf) < 2 ||
      CopyBuffer(handleAtr, 0, 0, 2, atrBuf) < 2)
     {
      PST_Log(EA_Name, "ERROR: fallo al copiar buffers de indicadores.");
      return;
     }

   double closeSignal = iClose(_Symbol, PERIOD_CURRENT, 1);
   double midRef = mid[1];
   double upperRef = upper[1];
   double lowerRef = lower[1];
   double rsiRef = rsiBuf[1];
   double atrRef = atrBuf[1];

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(point <= 0)
      return;

   bool hasPosition = HasOpenPosition();

   if(hasPosition)
     {
      long posType = PositionGetInteger(POSITION_TYPE);

      // TP dinamico: la banda media se recalcula cada vela, asi que el TP
      // "sigue" a la media movil en vez de quedar fijo en el precio de entrada.
      double newTp = NormalizeDouble(midRef, digits);
      double curTp = PositionGetDouble(POSITION_TP);
      double curSl = PositionGetDouble(POSITION_SL);
      if(MathAbs(newTp - curTp) >= point)
        {
         if(trade.PositionModify(_Symbol, curSl, newTp))
            PST_Log(EA_Name, StringFormat("TP dinamico actualizado a %s (banda media).", DoubleToString(newTp, digits)));
         else
            PST_Log(EA_Name, "ERROR al actualizar TP dinamico: " + IntegerToString(trade.ResultRetcode()));
        }

      // Red de seguridad: si el broker no llego a ejecutar el TP (gap, spread)
      // pero el cierre de la vela ya volvio a tocar la banda, cerramos igualmente.
      bool exitSignal = (posType == POSITION_TYPE_BUY  && closeSignal >= midRef) ||
                         (posType == POSITION_TYPE_SELL && closeSignal <= midRef);
      if(exitSignal)
        {
         double price = (posType == POSITION_TYPE_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                                                          : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         if(trade.PositionClose(_Symbol))
            PST_LogTrade(EA_Name, "CLOSE", _Symbol, 0, price, 0, 0, "retorno a banda central");
         else
            PST_Log(EA_Name, "ERROR al cerrar posicion: " + IntegerToString(trade.ResultRetcode()));
        }
      return;
     }

   bool ddExceeded = PST_DailyDrawdownExceeded(InpMaxDailyDrawdownPct, dayStartBalance);
   bool dailyLossExceeded = PST_DailyLossExceeded(InpMaxDailyLossAmount, dayStartBalance);
   bool totalLossExceeded = PST_TotalLossExceeded(InpAccountInitialBalance, InpMaxTotalLossAmount);
   if(dailyLossExceeded)
      PST_Log(EA_Name, StringFormat("Circuit breaker: perdida diaria >= %.2f. No se abren operaciones hoy.", InpMaxDailyLossAmount));
   if(totalLossExceeded)
      PST_Log(EA_Name, StringFormat("Circuit breaker: perdida TOTAL de cuenta >= %.2f. No se abren mas operaciones.", InpMaxTotalLossAmount));
   if(ddExceeded || dailyLossExceeded || totalLossExceeded || atrRef <= 0)
      return;

   // Filtro de invalidacion: ancho de banda actual vs media de los InpBbPeriod anchos previos.
   bool widthOk = true;
   if(InpMaxBandWidthRatio > 0)
     {
      double widthSum = 0;
      for(int i = 1; i <= InpBbPeriod; i++)
        {
         double w = (upper[i] - lower[i]) / mid[i];
         widthSum += w;
        }
      double widthAvg = widthSum / InpBbPeriod;
      double widthNow = (upperRef - lowerRef) / midRef;
      if(widthAvg > 0)
         widthOk = (widthNow / widthAvg) <= InpMaxBandWidthRatio;
     }

   bool wantLong  = closeSignal <= lowerRef && rsiRef < InpRsiOversold && widthOk;
   bool wantShort = closeSignal >= upperRef && rsiRef > InpRsiOverbought && widthOk;

   if(wantLong || wantShort)
     {
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
     }

   if(wantLong)
     {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl = NormalizeDouble(ask - atrRef * InpAtrStopMult, digits);
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
         PST_LogTrade(EA_Name, "OPEN_BUY", _Symbol, lots, ask, sl, 0, "toque banda inferior + RSI sobreventa");
      else
         PST_Log(EA_Name, "ERROR al abrir compra: " + IntegerToString(trade.ResultRetcode()));
     }
   else if(wantShort)
     {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl = NormalizeDouble(bid + atrRef * InpAtrStopMult, digits);
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
         PST_LogTrade(EA_Name, "OPEN_SELL", _Symbol, lots, bid, sl, 0, "toque banda superior + RSI sobrecompra");
      else
         PST_Log(EA_Name, "ERROR al abrir venta: " + IntegerToString(trade.ResultRetcode()));
     }
  }

//+------------------------------------------------------------------+
//| Se ejecuta al final de cada pasada del Strategy Tester (test      |
//| individual u optimizacion). Anota los parametros de esta pasada  |
//| y sus metricas en un CSV compartido, porque el Report= del .ini  |
//| solo exporta para Optimization=0 (ver docs/fase4-resultados.md). |
//+------------------------------------------------------------------+
double OnTester()
  {
   string fileName = StringFormat("PST_V2_OptResults\\%s_%s_%s.csv",
                                   EA_Name, _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period));
   string header = "BbPeriod;BbStd;AtrStopMult;NetProfit;ProfitFactor;Trades;Sharpe;MaxDDPercent;ExpectedPayoff";
   string line = StringFormat("%d;%.2f;%.2f;%.2f;%.3f;%d;%.3f;%.2f;%.2f",
                 InpBbPeriod, InpBbStd, InpAtrStopMult,
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
