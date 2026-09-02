//+------------------------------------------------------------------+
//|                   PST_GapAndGo_AAPL_H1.mq5                        |
//|                              PailsSentinelMT5TradeV2 — Fase 6+     |
//|                                                                    |
//| Hipotesis H42 (python/notebooks/backtest_engine.py::               |
//| backtest_h42_gap_and_go), segunda idea de "inercia" para acciones  |
//| de esta sesion (2026-09-02), especifica de AAPL. Idea clasica de    |
//| trading de acciones ("gap and go"): compara el Close de la vela de |
//| apertura de HOY (InpSessionOpenHour) contra el Close de la ultima  |
//| vela de la sesion de AYER (InpForceCloseHour del dia anterior) --   |
//| si el hueco de apertura supera InpGapThreshold Y en la vela de      |
//| InpConfirmHour el precio ha seguido moviendose en la misma          |
//| direccion del hueco (no lo ha rellenado), entra a favor -- apuesta  |
//| a que un hueco que no se rellena en la primera hora tiende a        |
//| continuar. Maximo 1 señal al dia (la mas selectiva de las 4 ideas   |
//| de inercia probadas esta sesion). TP DINAMICO segun ADX en el       |
//| momento de la entrada (mismo patron que PST_LiquiditySweepFade_     |
//| EURUSD_M5.mq5 y PST_DonchianVolumeBreakout_Stocks.mq5). SL por ATR. |
//| Cierre FORZADO fin de sesion (nunca overnight) -- comprueba la hora |
//| de la vela que ACABA DE EMPEZAR (shift 0), mismo criterio que       |
//| corrigio el bug de H20.                                             |
//|                                                                    |
//| Validado en Python sobre datos H1 reales (2024.08-2026.08, ~2 años, |
//| spread real en vivo 0.14), sesion 2026-09-02: **9/9 estable en TODO |
//| el rango de InpGapThreshold probado (0.2%-1.5%)** -- meseta muy     |
//| limpia, la                                                           |
//| mejor de las 4 ideas de inercia en cuanto a robustez de umbral.     |
//| Retorno +6% a +32% segun umbral, Sharpe hasta 0.36. SALVEDAD        |
//| IMPORTANTE: InpConfirmHour SI parece un punto mas fragil que el     |
//| umbral -- confirm_hour=17 (1h tras apertura, defecto) es el unico   |
//| que funciono en el barrido; a las 2h (18) y 3h (19) el resultado se |
//| rompe (signo invertido). Tiene sentido de mercado (si un hueco se   |
//| va a rellenar, suele pasar en la primera hora), pero es una         |
//| dependencia mas fuerte de un punto concreto que el resto de la      |
//| hipotesis -- vigilar en la confirmacion real. SOLO AAPL: en el      |
//| barrido inicial MSFT salio inestable y TSLA claramente NEGATIVO     |
//| (-33% a -50% en el mismo rango de umbral) -- NO desplegar en otros  |
//| simbolos sin retrabajar. Informes:                                  |
//| python/reports/h40_h43_stocks_scan.csv y deepening posterior en la  |
//| conversacion.                                                       |
//|                                                                    |
//| CONFIRMADO en Strategy Tester real (2026-09-02): barrido de         |
//| InpGapThreshold sobre AAPL H1, 2024.08.22-2026.08.20 -- todas las    |
//| combinaciones probadas con Profit Factor 1.16-1.75 y Sharpe hasta   |
//| 7.25. Pendiente split in/out-of-sample formal y que el usuario      |
//| adjunte el EA al grafico de AAPL cuando decida darlo por bueno.     |
//|                                                                    |
//| NOTA de fidelidad Python-vs-MT5: el SL y TP se envian como ordenes  |
//| NATIVAS del broker desde la apertura, monitorizadas de forma        |
//| continua -- igual que el resto de EAs del proyecto.                 |
//+------------------------------------------------------------------+
#property copyright "PailsSentinelMT5Trade"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <PST_V2\PST_RiskManager.mqh>
#include <PST_V2\PST_Logger.mqh>
#include <PST_V2\PST_OptExport.mqh>

//--------------------------------------------------------------- inputs
input string   EA_Name             = "PST_GapAndGo_AAPL_H1";
input int      InpMagicNumber      = 101902;

input group    "--- Estrategia H42: gap and go ---"
input double   InpGapThreshold     = 0.005;  // 0.5%, estable en todo el rango 0.2%-1.5%
input int      InpSessionOpenHour  = 16;     // hora de servidor, apertura de NY (verificado)
input int      InpConfirmHour      = 17;     // OJO: unico punto validado, ver cabecera
input int      InpForceCloseHour   = 22;

input group    "--- Gestion de riesgo y TP dinamico por ADX (ver cabecera) ---"
input int      InpAtrPeriod        = 14;
input double   InpAtrStopMult      = 2.0;
input int      InpAdxPeriod        = 14;
input double   InpAdxLow           = 20.0;
input double   InpAdxHigh          = 30.0;
input double   InpTpRLowRegime     = 4.0;
input double   InpTpRMidRegime     = 2.5;
input double   InpTpRHighRegime    = 1.25;
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
int    handleAtr, handleAdx;
datetime lastBarTime = 0;
double dayStartBalance = 0;
datetime currentDay = 0;
string g_correlationGroup[];

datetime g_dayKey = 0;
double   g_prevSessionClose = 0;
double   g_pendingPrevClose = 0;
double   g_todayOpenPrice = 0;
bool     g_dayEnteredOrSkipped = false;

//+------------------------------------------------------------------+
int OnInit()
  {
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);

   handleAtr = iATR(_Symbol, PERIOD_CURRENT, InpAtrPeriod);
   handleAdx = iADX(_Symbol, PERIOD_CURRENT, InpAdxPeriod);
   if(handleAtr == INVALID_HANDLE || handleAdx == INVALID_HANDLE)
     {
      PST_Log(EA_Name, "ERROR: no se pudo crear algun indicador en OnInit.");
      return(INIT_FAILED);
     }

   dayStartBalance = PST_CurrentBalance();
   currentDay = 0;
   PST_ParseCsvGroup(InpCorrelationGroup, g_correlationGroup);

   PST_Log(EA_Name, StringFormat("Inicializado en %s %s. Magic=%d GapThreshold=%.2f%% ConfirmHour=%d Riesgo=%.1f%%",
           _Symbol, EnumToString(PERIOD_CURRENT), InpMagicNumber,
           InpGapThreshold * 100.0, InpConfirmHour, InpRiskPercent));
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   IndicatorRelease(handleAtr);
   IndicatorRelease(handleAdx);
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
double TpRByAdxRegime(double adxVal)
  {
   if(adxVal < InpAdxLow)
      return InpTpRLowRegime;
   if(adxVal < InpAdxHigh)
      return InpTpRMidRegime;
   return InpTpRHighRegime;
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   UpdateDailyBalanceTracker();

   if(!IsNewBar())
      return;

   // Cierre forzado: comprueba la hora de la vela que ACABA DE EMPEZAR (shift 0), no la
   // cerrada -- mismo criterio que corrigio el bug de H20 (ver cabecera).
   MqlDateTime dtNow;
   datetime currentBarTime = iTime(_Symbol, PERIOD_CURRENT, 0);
   TimeToStruct(currentBarTime, dtNow);
   int currentHour = dtNow.hour;

   bool hasPosition = HasOpenPosition();
   if(hasPosition && currentHour >= InpForceCloseHour)
     {
      double price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      if(trade.PositionClose(_Symbol))
         PST_LogTrade(EA_Name, "CLOSE", _Symbol, 0, price, 0, 0, "cierre forzado fin de sesion");
      else
         PST_Log(EA_Name, "ERROR al cerrar posicion (cierre forzado): " + IntegerToString(trade.ResultRetcode()));
      return;
     }

   MqlDateTime dtSignal;
   datetime signalBarTime = iTime(_Symbol, PERIOD_CURRENT, 1);
   TimeToStruct(signalBarTime, dtSignal);
   datetime dayKey = (datetime)(dtSignal.year * 10000 + dtSignal.mon * 100 + dtSignal.day);
   int signalHour = dtSignal.hour;

   if(dayKey != g_dayKey)
     {
      g_dayKey = dayKey;
      // El "prev close" de HOY es el ultimo Close registrado antes de que cambiara el dia.
      g_prevSessionClose = g_pendingPrevClose;
      g_todayOpenPrice = 0;
      g_dayEnteredOrSkipped = false;
     }

   // Guarda continuamente el Close mas reciente de la sesion de HOY -- sera el "prev close"
   // que se use manana. Se actualiza en cada vela de la sesion regular.
   g_pendingPrevClose = iClose(_Symbol, PERIOD_CURRENT, 1);

   if(signalHour == InpSessionOpenHour && g_todayOpenPrice == 0)
      g_todayOpenPrice = iClose(_Symbol, PERIOD_CURRENT, 1);

   if(hasPosition)
      return;
   if(signalHour != InpConfirmHour || g_dayEnteredOrSkipped)
      return;
   if(g_prevSessionClose <= 0 || g_todayOpenPrice <= 0)
      return;

   g_dayEnteredOrSkipped = true;
   double openPrice = g_todayOpenPrice;

   double gap = openPrice / g_prevSessionClose - 1.0;
   double confirmClose = iClose(_Symbol, PERIOD_CURRENT, 1);

   bool gapUpConfirmed   = gap > InpGapThreshold && confirmClose > openPrice;
   bool gapDownConfirmed = gap < -InpGapThreshold && confirmClose < openPrice;
   if(!gapUpConfirmed && !gapDownConfirmed)
      return;

   double atrBuf[];
   ArraySetAsSeries(atrBuf, true);
   double adxBuf[];
   ArraySetAsSeries(adxBuf, true);
   if(CopyBuffer(handleAtr, 0, 0, 2, atrBuf) < 2 || CopyBuffer(handleAdx, 0, 0, 2, adxBuf) < 2)
     {
      PST_Log(EA_Name, "ERROR: fallo al copiar buffers de indicadores.");
      return;
     }
   double atrRef = atrBuf[1];
   double adxRef = adxBuf[1];
   if(atrRef <= 0)
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

   double tpR = TpRByAdxRegime(adxRef);
   double risk = InpAtrStopMult * atrRef;

   if(gapUpConfirmed)
     {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl = NormalizeDouble(ask - risk, digits);
      double tp = NormalizeDouble(ask + risk * tpR, digits);
      double slDistancePoints = risk / point;
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
      if(trade.Buy(lots, _Symbol, ask, sl, tp, EA_Name))
         PST_LogTrade(EA_Name, "OPEN_BUY", _Symbol, lots, ask, sl, tp, StringFormat("Gap alcista %.2f%% confirmado (ADX=%.1f, TPxR=%.2f)", gap * 100.0, adxRef, tpR));
      else
         PST_Log(EA_Name, "ERROR al abrir compra: " + IntegerToString(trade.ResultRetcode()));
     }
   else if(gapDownConfirmed)
     {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl = NormalizeDouble(bid + risk, digits);
      double tp = NormalizeDouble(bid - risk * tpR, digits);
      double slDistancePoints = risk / point;
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
      if(trade.Sell(lots, _Symbol, bid, sl, tp, EA_Name))
         PST_LogTrade(EA_Name, "OPEN_SELL", _Symbol, lots, bid, sl, tp, StringFormat("Gap bajista %.2f%% confirmado (ADX=%.1f, TPxR=%.2f)", gap * 100.0, adxRef, tpR));
      else
         PST_Log(EA_Name, "ERROR al abrir venta: " + IntegerToString(trade.ResultRetcode()));
     }
  }

//+------------------------------------------------------------------+
double OnTester()
  {
   string fileName = StringFormat("PST_V2_OptResults\\%s_%s_%s.csv",
                                   EA_Name, _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period));
   string header = "GapThreshold;ConfirmHour;NetProfit;ProfitFactor;Trades;Sharpe;MaxDDPercent;ExpectedPayoff";
   string line = StringFormat("%.4f;%d;%.2f;%.3f;%d;%.3f;%.2f;%.2f",
                 InpGapThreshold, InpConfirmHour,
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
