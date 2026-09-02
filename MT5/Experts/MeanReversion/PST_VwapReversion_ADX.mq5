//+------------------------------------------------------------------+
//|                          PST_VwapReversion_ADX.mq5                |
//|                              PailsSentinelMT5TradeV2 — Fase 6     |
//|                                                                   |
//| Hipotesis H25 (python/notebooks/backtest_engine.py::               |
//| backtest_h25_vwap_reversion): reversion al VWAP de sesion,        |
//| filtrada por regimen (ADX). A diferencia de las Bandas de         |
//| Bollinger de H5/H4 (SMA simple que arrastra informacion de dias   |
//| anteriores), el VWAP se reinicia cada dia y pondera por volumen.  |
//| Solo se opera reversion si ADX < InpAdxThreshold (mercado en      |
//| rango, sin tendencia en marcha) -- sin este filtro la reversion   |
//| falla en dias de tendencia (precio sigue extendiendose).          |
//|                                                                   |
//| Validado en Python (docs/fase6-h11-donchian-atr-resultados.md,    |
//| seccion H25) en UK100.cash, 2024.08-2026.08 (unico dato           |
//| disponible: 2 años), con coste de spread real: k=2.0/ADX<15,      |
//| ambos tramos OOS positivos, Sharpe mejorando 0.17->0.35. US500,   |
//| GER40 y XAUUSD descartados (negativos o inconsistentes en el      |
//| mismo barrido) -- esta EA esta pensada para UK100.cash            |
//| especificamente, no para toda la cesta.                           |
//|                                                                   |
//| Logica:                                                            |
//|  - Cada dia (deteccion por cambio de fecha en la vela recien      |
//|    cerrada): se reinician los acumuladores de VWAP.               |
//|  - En cada vela cerrada: VWAP = suma(precio tipico x volumen) /   |
//|    suma(volumen), acumulado desde el inicio del dia. Desviacion   |
//|    tipica acumulada con la misma ponderacion. Bandas = VWAP +/-   |
//|    InpKEntry x desviacion.                                        |
//|  - Tras InpMinBarsIntoSession velas, si Close cierra fuera de la  |
//|    banda Y ADX(InpAdxPeriod) < InpAdxThreshold, entra apostando a |
//|    la reversion hacia el VWAP.                                    |
//|  - Sale en la primera vela que cruza de vuelta al VWAP, o         |
//|    FORZADO en InpForceCloseHour (nunca overnight, cuenta FTMO de  |
//|    limites ajustados, docs/politica-de-riesgo.md). El cierre      |
//|    forzado comprueba la hora de la vela que ACABA DE EMPEZAR      |
//|    (shift 0), no la que acaba de cerrar -- mismo criterio que     |
//|    corrigio el bug de H20 (docs/fase6-h11-donchian-atr-           |
//|    resultados.md).                                                |
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
input string   EA_Name             = "PST_VwapReversion_ADX";
input int      InpMagicNumber      = 101100;

input group    "--- Estrategia H25: reversion a VWAP de sesion + filtro ADX ---"
input double   InpKEntry             = 2.0;
input double   InpAdxThreshold       = 15.0;
input int      InpAdxPeriod          = 14;
input int      InpMinBarsIntoSession = 3;
input int      InpForceCloseHour     = 23;   // hora de servidor, ultima vela fiable de sesion

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
int    handleAdx, handleAtr;
datetime lastBarTime = 0;
double dayStartBalance = 0;
datetime currentDay = 0;
string g_correlationGroup[];

datetime g_sessionDay = 0;
double   g_cumPV = 0;
double   g_cumV  = 0;
double   g_cumPV2 = 0;
int      g_barsIntoSession = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);

   handleAdx = iADX(_Symbol, PERIOD_CURRENT, InpAdxPeriod);
   handleAtr = iATR(_Symbol, PERIOD_CURRENT, InpAtrPeriod);
   if(handleAdx == INVALID_HANDLE || handleAtr == INVALID_HANDLE)
     {
      PST_Log(EA_Name, "ERROR: no se pudo crear algun indicador en OnInit.");
      return(INIT_FAILED);
     }

   dayStartBalance = PST_CurrentBalance();
   currentDay = 0;
   PST_ParseCsvGroup(InpCorrelationGroup, g_correlationGroup);

   PST_Log(EA_Name, StringFormat("Inicializado en %s %s. Magic=%d K=%.1f ADX<%.0f Riesgo=%.1f%%",
           _Symbol, EnumToString(PERIOD_CURRENT), InpMagicNumber,
           InpKEntry, InpAdxThreshold, InpRiskPercent));
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   IndicatorRelease(handleAdx);
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

   // --- Cierre forzado: comprueba la hora de la vela que ACABA DE EMPEZAR (shift 0), no la
   // que acaba de cerrar -- mismo criterio que corrigio el bug de H20 (ver cabecera).
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

   // --- Vela recien cerrada (shift 1): actualiza acumuladores de VWAP y evalua senal.
   datetime signalBarTime = iTime(_Symbol, PERIOD_CURRENT, 1);
   MqlDateTime dtSignal;
   TimeToStruct(signalBarTime, dtSignal);
   datetime dayKey = (datetime)(dtSignal.year * 10000 + dtSignal.mon * 100 + dtSignal.day);

   if(dayKey != g_sessionDay)
     {
      g_sessionDay = dayKey;
      g_cumPV = 0;
      g_cumV = 0;
      g_cumPV2 = 0;
      g_barsIntoSession = 0;
     }

   double closeSignal = iClose(_Symbol, PERIOD_CURRENT, 1);
   double highSignal   = iHigh(_Symbol, PERIOD_CURRENT, 1);
   double lowSignal    = iLow(_Symbol, PERIOD_CURRENT, 1);
   long   volSignal     = iTickVolume(_Symbol, PERIOD_CURRENT, 1);
   double vol = (volSignal > 0) ? (double)volSignal : 1.0;
   double typicalPrice = (highSignal + lowSignal + closeSignal) / 3.0;

   g_cumPV  += typicalPrice * vol;
   g_cumV   += vol;
   g_cumPV2 += typicalPrice * typicalPrice * vol;
   g_barsIntoSession++;

   if(g_cumV <= 0)
      return;

   double vwap = g_cumPV / g_cumV;
   double variance = g_cumPV2 / g_cumV - vwap * vwap;
   if(variance < 0)
      variance = 0;
   double stdDev = MathSqrt(variance);

   double adxBuf[];
   ArraySetAsSeries(adxBuf, true);
   if(CopyBuffer(handleAdx, 0, 0, 2, adxBuf) < 2)
     {
      PST_Log(EA_Name, "ERROR: fallo al copiar buffer de ADX.");
      return;
     }
   double adxVal = adxBuf[1];

   if(hasPosition)
     {
      long posType = PositionGetInteger(POSITION_TYPE);
      bool exitSignal = (posType == POSITION_TYPE_BUY  && closeSignal >= vwap) ||
                         (posType == POSITION_TYPE_SELL && closeSignal <= vwap);
      if(exitSignal)
        {
         double price = (posType == POSITION_TYPE_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                                                          : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         if(trade.PositionClose(_Symbol))
            PST_LogTrade(EA_Name, "CLOSE", _Symbol, 0, price, 0, 0, "retorno a VWAP de sesion");
         else
            PST_Log(EA_Name, "ERROR al cerrar posicion: " + IntegerToString(trade.ResultRetcode()));
        }
      return;
     }

   if(g_barsIntoSession < InpMinBarsIntoSession || stdDev <= 0 || adxVal >= InpAdxThreshold)
      return;

   double upper = vwap + InpKEntry * stdDev;
   double lower = vwap - InpKEntry * stdDev;

   bool wantShort = closeSignal > upper;
   bool wantLong  = closeSignal < lower;
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
         PST_LogTrade(EA_Name, "OPEN_BUY", _Symbol, lots, ask, sl, 0, StringFormat("Close<VWAP-%.1fsd, ADX=%.1f<%.1f", InpKEntry, adxVal, InpAdxThreshold));
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
         PST_LogTrade(EA_Name, "OPEN_SELL", _Symbol, lots, bid, sl, 0, StringFormat("Close>VWAP+%.1fsd, ADX=%.1f<%.1f", InpKEntry, adxVal, InpAdxThreshold));
      else
         PST_Log(EA_Name, "ERROR al abrir venta: " + IntegerToString(trade.ResultRetcode()));
     }
  }

//+------------------------------------------------------------------+
double OnTester()
  {
   string fileName = StringFormat("PST_V2_OptResults\\%s_%s_%s.csv",
                                   EA_Name, _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period));
   string header = "KEntry;AdxThreshold;NetProfit;ProfitFactor;Trades;Sharpe;MaxDDPercent;ExpectedPayoff";
   string line = StringFormat("%.2f;%.1f;%.2f;%.3f;%d;%.3f;%.2f;%.2f",
                 InpKEntry, InpAdxThreshold,
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
