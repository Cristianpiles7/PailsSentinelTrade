//+------------------------------------------------------------------+
//| PST_RelativeStrengthV2_TSLA_H1.mq5
//| Estrategia: Fuerza relativa frente a US500.cash — alpha ajustado por beta (60 dias D1)
//| Simbolo TSLA | TF H1 | magic 105001 | version 1.0
//| Un fichero = un despliegue: este EA solo arranca en ese simbolo y TF.
//| Control desde la app: MT5/Include/PST_Control.mqh. Catalogo: MT5/CATALOGO_EAS.md
//| Variante del taller (mejora-eas, 2026-11-05) de MeanReversion/PST_RelativeStrength_TSLA_H1.mq5.
//| Unico cambio: alpha = Ret(accion) - beta * Ret(indice), con beta movil de InpBetaLookbackDays
//| retornos diarios (acotado a [InpBetaMin, InpBetaMax]). Motivo: TSLA tiene beta ~2 frente al
//| S&P; con el alpha bruto (beta = 1) gran parte de las senales son solo el movimiento del
//| mercado amplificado, no fuerza propia de la accion. El resto de la logica es identica.
//+------------------------------------------------------------------+
#property copyright "PailsSentinelMT5Trade"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <PST_V2\PST_RiskManager.mqh>
#include <PST_V2\PST_Logger.mqh>
#include <PST_V2\PST_OptExport.mqh>
#include <PST_V2\PST_Control.mqh>

#define PST_DESPLIEGUE_SIMBOLO "TSLA"
#define PST_DESPLIEGUE_TF      PERIOD_H1
#define PST_EA_VERSION         "1.0"

//--------------------------------------------------------------- inputs
input string   EA_Name             = "PST_RelativeStrengthV2_TSLA_H1";
input int      InpMagicNumber      = 105001;

input group    "--- Estrategia: fortaleza relativa vs indice (ajustada por beta) ---"
input string   InpIndexSymbol      = "US500.cash";
input int      InpEmaPeriod        = 20;
input double   InpAlphaThreshold   = 0.01;  // 1%
input int      InpSessionOpenHour  = 16;    // hora de servidor, apertura de NY (verificado)
input int      InpCheckHour        = 17;
input int      InpForceCloseHour   = 22;
input int      InpBetaLookbackDays = 60;    // retornos diarios para estimar la beta
input double   InpBetaMin          = 0.5;
input double   InpBetaMax          = 3.0;

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
   int ctl = PST_Control_Init(EA_Name, InpMagicNumber, PST_EA_VERSION,
                              PST_DESPLIEGUE_SIMBOLO, PST_DESPLIEGUE_TF);
   if(ctl != INIT_SUCCEEDED)
      return(ctl);

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

   PST_Log(EA_Name, StringFormat("Inicializado en %s %s vs %s. Magic=%d AlphaThreshold=%.2f%% Riesgo=%.1f%% BetaDias=%d",
           _Symbol, EnumToString(PERIOD_CURRENT), InpIndexSymbol, InpMagicNumber,
           InpAlphaThreshold * 100.0, InpRiskPercent, InpBetaLookbackDays));
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   PST_Control_Deinit(reason);
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
   return PST_SelectMyPosition(_Symbol, InpMagicNumber);
  }

//+------------------------------------------------------------------+
//| Precio de cierre de InpIndexSymbol en la vela cuya apertura mas  |
//| se aproxima (sin pasarse) a targetTime.                          |
//+------------------------------------------------------------------+
double IndexCloseAt(datetime targetTime)
  {
   int shift = iBarShift(InpIndexSymbol, PERIOD_CURRENT, targetTime, false);
   if(shift < 0)
      return 0;
   return iClose(InpIndexSymbol, PERIOD_CURRENT, shift);
  }

//+------------------------------------------------------------------+
//| Beta de la accion frente al indice con retornos diarios cerrados |
//| (velas D1 desde shift 1, sin mirar el dia en curso). Cada dia de |
//| la accion se empareja con la vela D1 del indice de esa fecha.    |
//| Devuelve -1 si no hay datos suficientes.                         |
//+------------------------------------------------------------------+
double CalcBeta()
  {
   int n = 0;
   double sx = 0, sy = 0, sxx = 0, sxy = 0;
   for(int i = 1; i <= InpBetaLookbackDays; i++)
     {
      datetime t0 = iTime(_Symbol, PERIOD_D1, i);
      datetime t1 = iTime(_Symbol, PERIOD_D1, i + 1);
      double s0 = iClose(_Symbol, PERIOD_D1, i);
      double s1 = iClose(_Symbol, PERIOD_D1, i + 1);
      if(t0 == 0 || t1 == 0 || s0 <= 0 || s1 <= 0)
         continue;
      int k0 = iBarShift(InpIndexSymbol, PERIOD_D1, t0, true);
      int k1 = iBarShift(InpIndexSymbol, PERIOD_D1, t1, true);
      if(k0 < 0 || k1 < 0)
         continue;
      double x0 = iClose(InpIndexSymbol, PERIOD_D1, k0);
      double x1 = iClose(InpIndexSymbol, PERIOD_D1, k1);
      if(x0 <= 0 || x1 <= 0)
         continue;
      double rx = x0 / x1 - 1.0;
      double ry = s0 / s1 - 1.0;
      sx += rx; sy += ry; sxx += rx * rx; sxy += rx * ry;
      n++;
     }
   if(n < InpBetaLookbackDays / 2)
      return -1;
   double varX = sxx / n - (sx / n) * (sx / n);
   if(varX <= 0)
      return -1;
   double cov = sxy / n - (sx / n) * (sy / n);
   double beta = cov / varX;
   return MathMax(InpBetaMin, MathMin(InpBetaMax, beta));
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

   // Cierre forzado: nunca overnight (hora de la vela que acaba de empezar, ver original).
   MqlDateTime dtNow;
   datetime currentBarTime = iTime(_Symbol, PERIOD_CURRENT, 0);
   TimeToStruct(currentBarTime, dtNow);
   int currentHour = dtNow.hour;

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

   double beta = CalcBeta();
   if(beta <= 0)
     {
      PST_Log(EA_Name, "Beta no disponible (historial D1 insuficiente). Sin senal hoy.");
      return;
     }

   double retStock = checkPriceStock / g_dayOpenStockPrice - 1.0;
   double retIndex = checkPriceIndex / g_dayOpenIndexPrice - 1.0;
   double alpha = retStock - beta * retIndex;

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

   if(wantLong)
     {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl = NormalizeDouble(ask - InpAtrStopMult * atrRef, digits);
      double slDistancePoints = (ask - sl) / point;
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
      if(trade.Buy(lots, _Symbol, ask, sl, 0, EA_Name))
         PST_LogTrade(EA_Name, "OPEN_BUY", _Symbol, lots, ask, sl, 0, StringFormat("alpha_beta=%.2f%% (beta %.2f) > umbral, Close>EMA%d", alpha * 100.0, beta, InpEmaPeriod));
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
      if(trade.Sell(lots, _Symbol, bid, sl, 0, EA_Name))
         PST_LogTrade(EA_Name, "OPEN_SELL", _Symbol, lots, bid, sl, 0, StringFormat("alpha_beta=%.2f%% (beta %.2f) < -umbral, Close<EMA%d", alpha * 100.0, beta, InpEmaPeriod));
      else
         PST_Log(EA_Name, "ERROR al abrir venta: " + IntegerToString(trade.ResultRetcode()));
     }
  }

//+------------------------------------------------------------------+
double OnTester()
  {
   string fileName = StringFormat("PST_V2_OptResults\\%s_%s_%s.csv",
                                   EA_Name, _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period));
   string header = "AlphaThreshold;EmaPeriod;BetaDays;NetProfit;ProfitFactor;Trades;Sharpe;MaxDDPercent;ExpectedPayoff";
   string line = StringFormat("%.4f;%d;%d;%.2f;%.3f;%d;%.3f;%.2f;%.2f",
                 InpAlphaThreshold, InpEmaPeriod, InpBetaLookbackDays,
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

//+------------------------------------------------------------------+
void OnTimer()
  {
   PST_Control_OnTimer(trade);
  }
//+------------------------------------------------------------------+
