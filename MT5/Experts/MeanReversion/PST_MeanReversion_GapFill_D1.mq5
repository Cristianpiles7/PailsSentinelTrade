//+------------------------------------------------------------------+
//|                        PST_MeanReversion_GapFill_D1.mq5          |
//|                              PailsSentinelMT5TradeV2 — Fase 6     |
//|                                                                   |
//| Hipotesis H12 (python/notebooks/run_h12_gapfill.py,               |
//| python/reports/resumen_h12_gapfill.md): los indices CFD suelen    |
//| abrir con un salto (gap) respecto al cierre del dia anterior,     |
//| que tiende a rellenarse parcialmente durante la sesion. Idea      |
//| nueva, no una variante de tendencia/breakout (H7/H11, ya          |
//| descartadas en MT5 real, docs/fase6-h11-donchian-atr-             |
//| resultados.md). Diseñada para D1, cualquier indice (_Symbol no    |
//| hardcodeado). Validada en Python con out-of-sample estable en     |
//| JP225.cash (gap_atr_mult=0.5, stop_atr_mult=1.5): ambos tramos    |
//| 2019-2022/2023-2026 positivos, Sharpe 0.14->0.91, MaxDD 11.2%->   |
//| 3.9%, 66 operaciones en 7.5 años.                                  |
//|                                                                   |
//| Logica:                                                            |
//|  - En cada vela D1 nueva: si sigue abierta una posicion del dia   |
//|    anterior (no toco ni objetivo ni stop), se cierra ahora --     |
//|    aproximacion de "cerrar al final de la sesion" con datos       |
//|    diarios (mismo criterio que el prototipo Python).              |
//|  - Gap = Open de hoy - Close de ayer. ATR de referencia = ATR     |
//|    calculado hasta ayer (shift 1, sin fuga de informacion).       |
//|  - Si |gap| >= InpGapAtrMult x ATR: entra a favor de la reversion |
//|    (CORTO si gap al alza, LARGO si gap a la baja). Objetivo       |
//|    (TP nativo) = cierre de ayer. Stop (SL nativo) = apertura de   |
//|    hoy +/- InpStopAtrMult x ATR. Al usar SL/TP nativos, MT5       |
//|    simula la ejecucion intravela real (mas fiel que el prototipo  |
//|    Python, que solo mira High/Low diario y asume el peor caso si  |
//|    ambos niveles caben en el rango del dia).                      |
//|  - Lotaje por % de riesgo, circuit breaker de drawdown diario,    |
//|    gestion de riesgo de cartera (docs/politica-de-riesgo.md).     |
//+------------------------------------------------------------------+
#property copyright "PailsSentinelMT5Trade"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <PST_V2\PST_RiskManager.mqh>
#include <PST_V2\PST_Logger.mqh>
#include <PST_V2\PST_OptExport.mqh>

//--------------------------------------------------------------- inputs
input string   EA_Name             = "PST_MeanReversion_GapFill_D1";
input int      InpMagicNumber      = 100700;

input group    "--- Estrategia H12: reversion del gap de apertura ---"
input double   InpGapAtrMult       = 0.5;
input int      InpAtrPeriod        = 14;
input double   InpStopAtrMult      = 1.5;

input group    "--- Gestion de riesgo ---"
input double   InpRiskPercent          = 1.0;
input double   InpMaxDailyDrawdownPct  = 4.0;

input group    "--- Gestion de riesgo de cartera (docs/politica-de-riesgo.md) ---"
input double   InpMaxPortfolioRiskPercent = 4.0;
input string   InpCorrelationGroup   = "";
input double   InpMaxGroupRiskPercent  = 2.5;

input group    "--- Limites de cuenta FTMO (importe absoluto, docs/politica-de-riesgo.md) ---"
input double   InpAccountInitialBalance = 10000.0;  // balance INICIAL del reto FTMO, fijo -- no el balance de hoy
input double   InpMaxDailyLossAmount    = 500.0;    // perdida diaria maxima permitida (divisa de la cuenta)
input double   InpMaxTotalLossAmount    = 1000.0;   // perdida total maxima permitida desde InpAccountInitialBalance

//--------------------------------------------------------------- state
CTrade trade;
int    handleAtr;
datetime lastBarTime = 0;
double dayStartBalance = 0;
datetime currentDay = 0;
string g_correlationGroup[];

//+------------------------------------------------------------------+
int OnInit()
  {
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);

   handleAtr = iATR(_Symbol, PERIOD_CURRENT, InpAtrPeriod);
   if(handleAtr == INVALID_HANDLE)
     {
      PST_Log(EA_Name, "ERROR: no se pudo crear el indicador ATR.");
      return(INIT_FAILED);
     }

   dayStartBalance = PST_CurrentBalance();
   currentDay = 0;

   PST_ParseCsvGroup(InpCorrelationGroup, g_correlationGroup);

   PST_Log(EA_Name, StringFormat("Inicializado en %s %s. Magic=%d GapAtrMult=%.2f StopAtrMult=%.2f Riesgo=%.1f%%",
           _Symbol, EnumToString(PERIOD_CURRENT), InpMagicNumber,
           InpGapAtrMult, InpStopAtrMult, InpRiskPercent));
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
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

   // Nueva vela D1: si sigue abierta una posicion de ayer (no toco objetivo ni stop),
   // se cierra ahora -- aproximacion de "cerrar al final de la sesion" (ver cabecera).
   if(HasOpenPosition())
     {
      if(trade.PositionClose(_Symbol))
         PST_LogTrade(EA_Name, "CLOSE", _Symbol, 0, SymbolInfoDouble(_Symbol, SYMBOL_BID), 0, 0, "fin de sesion sin tocar objetivo/stop");
      else
         PST_Log(EA_Name, "ERROR al cerrar posicion de fin de sesion: " + IntegerToString(trade.ResultRetcode()));
     }

   double atrBuf[];
   ArraySetAsSeries(atrBuf, true);
   if(CopyBuffer(handleAtr, 0, 0, 2, atrBuf) < 2)
     {
      PST_Log(EA_Name, "ERROR: fallo al copiar buffer de ATR.");
      return;
     }
   double atrRef = atrBuf[1];
   if(atrRef <= 0)
      return;

   double todayOpen = iOpen(_Symbol, PERIOD_CURRENT, 0);
   double prevClose = iClose(_Symbol, PERIOD_CURRENT, 1);
   double gap = todayOpen - prevClose;

   if(MathAbs(gap) < InpGapAtrMult * atrRef)
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

   if(gap > 0)
     {
      // gap al alza: corto, objetivo = cierre de ayer, stop por encima de la apertura de hoy
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl = NormalizeDouble(todayOpen + InpStopAtrMult * atrRef, digits);
      double tp = NormalizeDouble(prevClose, digits);
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
      if(trade.Sell(lots, _Symbol, bid, sl, tp, EA_Name))
         PST_LogTrade(EA_Name, "OPEN_SELL", _Symbol, lots, bid, sl, tp, "gap al alza, reversion hacia cierre de ayer");
      else
         PST_Log(EA_Name, "ERROR al abrir venta: " + IntegerToString(trade.ResultRetcode()));
     }
   else
     {
      // gap a la baja: largo, objetivo = cierre de ayer, stop por debajo de la apertura de hoy
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl = NormalizeDouble(todayOpen - InpStopAtrMult * atrRef, digits);
      double tp = NormalizeDouble(prevClose, digits);
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
      if(trade.Buy(lots, _Symbol, ask, sl, tp, EA_Name))
         PST_LogTrade(EA_Name, "OPEN_BUY", _Symbol, lots, ask, sl, tp, "gap a la baja, reversion hacia cierre de ayer");
      else
         PST_Log(EA_Name, "ERROR al abrir compra: " + IntegerToString(trade.ResultRetcode()));
     }
  }

//+------------------------------------------------------------------+
//| Se ejecuta al final de cada pasada del Strategy Tester (test      |
//| individual u optimizacion). Anota los parametros de esta pasada  |
//| y sus metricas en un CSV compartido (ver PST_OptExport.mqh).     |
//+------------------------------------------------------------------+
double OnTester()
  {
   string fileName = StringFormat("PST_V2_OptResults\\%s_%s_%s.csv",
                                   EA_Name, _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period));
   string header = "GapAtrMult;AtrPeriod;StopAtrMult;NetProfit;ProfitFactor;Trades;Sharpe;MaxDDPercent;ExpectedPayoff";
   string line = StringFormat("%.2f;%d;%.2f;%.2f;%.3f;%d;%.3f;%.2f;%.2f",
                 InpGapAtrMult, InpAtrPeriod, InpStopAtrMult,
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
