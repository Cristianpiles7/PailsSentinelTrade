//+------------------------------------------------------------------+
//| PST_DonchianVolumenV2_AAPL_H1.mq5
//| Estrategia: Ruptura Donchian + volumen + tendencia — ruptura confirmada por CIERRE (no por mecha)
//| Simbolo AAPL | TF H1 | magic 105002 | version 1.0
//| Un fichero = un despliegue: este EA solo arranca en ese simbolo y TF.
//| Control desde la app: MT5/Include/PST_Control.mqh. Catalogo: MT5/CATALOGO_EAS.md
//| Variante del taller (mejora-eas, 2026-11-05) de Breakout/PST_DonchianVolumen_AAPL_H1.mq5 v2.0.
//| Unico cambio: la vela de senal debe CERRAR por encima del maximo (o por debajo del minimo)
//| Donchian, en vez de bastar con que la mecha lo toque.
//| Motivo: en el OOS 2025-11 -> 2026-11 casi todas las operaciones salen por el cierre forzado
//| y los largos (13 ops, 23 % ganadoras) compran rupturas que se deshacen esa misma tarde. Una
//| ruptura de mecha con cierre dentro del canal es justo una ruptura rechazada.
//| Resto (volumen, EMA, ATR, TP por ADX, sesion, riesgo) identico al original.
//+------------------------------------------------------------------+
#property copyright "PailsSentinelMT5Trade"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <PST_V2\PST_RiskManager.mqh>
#include <PST_V2\PST_Logger.mqh>
#include <PST_V2\PST_OptExport.mqh>
#include <PST_V2\PST_Control.mqh>

#define PST_DESPLIEGUE_SIMBOLO "AAPL"
#define PST_DESPLIEGUE_TF      PERIOD_H1
#define PST_EA_VERSION         "1.0"

//--------------------------------------------------------------- inputs
input string   EA_Name             = "PST_DonchianVolumenV2_AAPL_H1";
input int      InpMagicNumber      = 105002;

input group    "--- Estrategia: ruptura de rango (confirmada por cierre) + volumen + tendencia ---"
input int      InpDonchianPeriod   = 40;
input int      InpVolumePeriod     = 20;
input double   InpVolumeMult       = 1.5;
input int      InpEmaTrend         = 200;
input int      InpSessionOpenHour  = 16;    // hora de servidor, apertura de NY (verificado)
input int      InpSessionLastEntryHour = 21;
input int      InpForceCloseHour   = 22;

input group    "--- Gestion de riesgo y TP dinamico por ADX ---"
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
int    handleAtr, handleAdx, handleEmaTrend;
datetime lastBarTime = 0;
double dayStartBalance = 0;
datetime currentDay = 0;
string g_correlationGroup[];

//+------------------------------------------------------------------+
int OnInit()
  {
   int ctl = PST_Control_Init(EA_Name, InpMagicNumber, PST_EA_VERSION,
                              PST_DESPLIEGUE_SIMBOLO, PST_DESPLIEGUE_TF);
   if(ctl != INIT_SUCCEEDED)
      return(ctl);

   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);

   handleAtr = iATR(_Symbol, PERIOD_CURRENT, InpAtrPeriod);
   handleAdx = iADX(_Symbol, PERIOD_CURRENT, InpAdxPeriod);
   handleEmaTrend = iMA(_Symbol, PERIOD_CURRENT, InpEmaTrend, 0, MODE_EMA, PRICE_CLOSE);
   if(handleAtr == INVALID_HANDLE || handleAdx == INVALID_HANDLE || handleEmaTrend == INVALID_HANDLE)
     {
      PST_Log(EA_Name, "ERROR: no se pudo crear algun indicador en OnInit.");
      return(INIT_FAILED);
     }

   dayStartBalance = PST_CurrentBalance();
   currentDay = 0;
   PST_ParseCsvGroup(InpCorrelationGroup, g_correlationGroup);

   PST_Log(EA_Name, StringFormat("Inicializado en %s %s. Magic=%d Donchian=%d VolMult=%.2f Riesgo=%.1f%%",
           _Symbol, EnumToString(PERIOD_CURRENT), InpMagicNumber,
           InpDonchianPeriod, InpVolumeMult, InpRiskPercent));
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   PST_Control_Deinit(reason);
   IndicatorRelease(handleAtr);
   IndicatorRelease(handleAdx);
   IndicatorRelease(handleEmaTrend);
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
   return PST_SelectMyPosition(_Symbol, InpMagicNumber);
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

   // Cierre forzado: comprueba la hora de la vela que ACABA DE EMPEZAR (shift 0).
   MqlDateTime dtNow;
   datetime currentBarTime = iTime(_Symbol, PERIOD_CURRENT, 0);
   TimeToStruct(currentBarTime, dtNow);
   int currentHour = dtNow.hour;

   bool hasPosition = HasOpenPosition();
   if(hasPosition && currentHour >= InpForceCloseHour)
     {
      double price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      if(PST_CloseMyPosition(trade, _Symbol, InpMagicNumber))
         PST_LogTrade(EA_Name, "CLOSE", _Symbol, 0, price, 0, 0, "cierre forzado fin de sesion");
      else
         PST_Log(EA_Name, "ERROR al cerrar posicion (cierre forzado): " + IntegerToString(trade.ResultRetcode()));
      return;
     }
   if(hasPosition)
      return;

   MqlDateTime dtSignal;
   datetime signalBarTime = iTime(_Symbol, PERIOD_CURRENT, 1);
   TimeToStruct(signalBarTime, dtSignal);
   int signalHour = dtSignal.hour;

   if(signalHour < InpSessionOpenHour || signalHour > InpSessionLastEntryHour)
      return;

   // start=2: el rango de referencia excluye la propia vela de senal (shift 1).
   int highestShift = iHighest(_Symbol, PERIOD_CURRENT, MODE_HIGH, InpDonchianPeriod, 2);
   int lowestShift   = iLowest(_Symbol, PERIOD_CURRENT, MODE_LOW, InpDonchianPeriod, 2);
   if(highestShift < 0 || lowestShift < 0)
      return;
   double rollHigh = iHigh(_Symbol, PERIOD_CURRENT, highestShift);
   double rollLow  = iLow(_Symbol, PERIOD_CURRENT, lowestShift);

   double volAvg = 0;
   for(int i = 1; i <= InpVolumePeriod; i++)
      volAvg += (double)iVolume(_Symbol, PERIOD_CURRENT, i);
   volAvg /= InpVolumePeriod;
   if(volAvg <= 0)
      return;

   double closeSignal = iClose(_Symbol, PERIOD_CURRENT, 1);
   double volSignal    = (double)iVolume(_Symbol, PERIOD_CURRENT, 1);
   bool volOk = volSignal > InpVolumeMult * volAvg;

   double emaBuf[];
   ArraySetAsSeries(emaBuf, true);
   double atrBuf[];
   ArraySetAsSeries(atrBuf, true);
   double adxBuf[];
   ArraySetAsSeries(adxBuf, true);
   if(CopyBuffer(handleEmaTrend, 0, 0, 2, emaBuf) < 2 ||
      CopyBuffer(handleAtr, 0, 0, 2, atrBuf) < 2 ||
      CopyBuffer(handleAdx, 0, 0, 2, adxBuf) < 2)
     {
      PST_Log(EA_Name, "ERROR: fallo al copiar buffers de indicadores.");
      return;
     }
   double emaRef = emaBuf[1];
   double atrRef = atrBuf[1];
   double adxRef = adxBuf[1];
   if(atrRef <= 0)
      return;

   // CAMBIO de esta variante: la ruptura se confirma con el CIERRE de la vela de senal.
   bool breakoutUp   = closeSignal > rollHigh && volOk && closeSignal > emaRef;
   bool breakoutDown = closeSignal < rollLow  && volOk && closeSignal < emaRef;
   if(!breakoutUp && !breakoutDown)
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

   // Interruptor de la app (PST_Control): si esta pausado no se abre nada nuevo.
   if(!PST_Control_PuedeAbrir())
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

   if(breakoutUp)
     {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double risk = InpAtrStopMult * atrRef;
      double sl = NormalizeDouble(ask - risk, digits);
      double tp = NormalizeDouble(ask + risk * tpR, digits);
      double slDistancePoints = risk / point;
      if(slDistancePoints <= 0)
         return;
      double lots = PST_CalcLotByRisk(_Symbol, PST_RiesgoEfectivo(InpRiskPercent), slDistancePoints);
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
         PST_LogTrade(EA_Name, "OPEN_BUY", _Symbol, lots, ask, sl, tp, StringFormat("Ruptura alcista por cierre Donchian(%d) + volumen (ADX=%.1f, TPxR=%.2f)", InpDonchianPeriod, adxRef, tpR));
      else
         PST_Log(EA_Name, "ERROR al abrir compra: " + IntegerToString(trade.ResultRetcode()));
     }
   else if(breakoutDown)
     {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double risk = InpAtrStopMult * atrRef;
      double sl = NormalizeDouble(bid + risk, digits);
      double tp = NormalizeDouble(bid - risk * tpR, digits);
      double slDistancePoints = risk / point;
      if(slDistancePoints <= 0)
         return;
      double lots = PST_CalcLotByRisk(_Symbol, PST_RiesgoEfectivo(InpRiskPercent), slDistancePoints);
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
         PST_LogTrade(EA_Name, "OPEN_SELL", _Symbol, lots, bid, sl, tp, StringFormat("Ruptura bajista por cierre Donchian(%d) + volumen (ADX=%.1f, TPxR=%.2f)", InpDonchianPeriod, adxRef, tpR));
      else
         PST_Log(EA_Name, "ERROR al abrir venta: " + IntegerToString(trade.ResultRetcode()));
     }
  }

//+------------------------------------------------------------------+
double OnTester()
  {
   string fileName = StringFormat("PST_V2_OptResults\\%s_%s_%s.csv",
                                   EA_Name, _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period));
   string header = "DonchianPeriod;VolumeMult;NetProfit;ProfitFactor;Trades;Sharpe;MaxDDPercent;ExpectedPayoff";
   string line = StringFormat("%d;%.2f;%.2f;%.3f;%d;%.3f;%.2f;%.2f",
                 InpDonchianPeriod, InpVolumeMult,
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
void OnTimer()
  {
   PST_Control_OnTimer(trade);
  }
//+------------------------------------------------------------------+
