//+------------------------------------------------------------------+
//|                          PST_MacdMomentum_GER40_H1.mq5            |
//|                              PailsSentinelMT5TradeV2 — Fase 6     |
//|                                                                   |
//| Hipotesis H28b (python/notebooks/backtest_engine.py::             |
//| backtest_h28_macd_momentum, corrida en H1 por                    |
//| run_h28b_macd_momentum_h1.py): primera estrategia con MACD en el  |
//| proyecto. En vez del cruce de LINEAS del MACD (equivalente lento  |
//| a un cruce de medias, familia ya fallida en H1/D1: 0/160          |
//| rentable), usa el cambio de signo del HISTOGRAMA (linea MACD -    |
//| señal) como gatillo de momentum de corto plazo. Filtrado por      |
//| ADX>InpAdxThreshold (evita mercado sin tendencia) y por el lado   |
//| de una EMA200 de tendencia de fondo (Close vs EMA200).            |
//|                                                                   |
//| Nacio como H28 en M15 (frecuencia muy alta, ~1500-2300            |
//| trades/7.5 años): el coste de spread devoraba casi todo el edge   |
//| bruto, mismo patron que H23 ya demostro matematicamente           |
//| (resumen_h28_macd_momentum.md). H28b sube a H1 (~4x menos         |
//| frecuencia) para sobrevivir al spread sin caer en el problema     |
//| opuesto de H20 (1 señal/dia a hora fija).                         |
//|                                                                   |
//| Resultado del barrido Python (resumen_h28b_macd_momentum_h1.md,   |
//| con coste de spread real y split de la muestra en dos mitades):   |
//| GER40.cash es el UNICO candidato limpio de la cesta (US500.cash,  |
//| UK100.cash, XAUUSD) -- Sharpe 0.14-0.17 estable en ambas mitades, |
//| ~50 operaciones/año (~1/semana). UK100.cash y XAUUSD invierten de |
//| signo entre mitades (edge de regimen, no estructural, mismo       |
//| patron que H11-D1/H14/H22/H23). US500.cash negativo en ambas.     |
//| Esta EA esta pensada para GER40.cash H1 especificamente.          |
//|                                                                   |
//| PENDIENTE DE CONFIRMAR en el Strategy Tester real de MT5 antes de |
//| considerar despliegue a demo -- el proyecto tiene divergencias    |
//| grandes documentadas entre operaciones Python vs MT5 real (ver    |
//| JP225 gapfill, H26/XAUUSD). Este EA es esa confirmacion.          |
//|                                                                   |
//| Nota de implementacion importante: el indicador iMACD nativo de   |
//| MT5 calcula la linea de señal como SMA de la linea MACD, NO como  |
//| EMA (a diferencia del MACD "de libro" de Appel, que Python usa    |
//| via pandas .ewm(adjust=False) para linea Y señal). Usar iMACD tal |
//| cual habria introducido exactamente el tipo de divergencia        |
//| Python-vs-MT5 que este proyecto ya ha sufrido antes -- por eso    |
//| aqui el MACD (linea rapida, lenta y señal) se recalcula a mano    |
//| con la misma recursion EMA que pandas ewm(adjust=False)           |
//| (UpdateMacdState), sembrada con historial previo real del simbolo |
//| en OnInit (BootstrapMacdState) en vez de con velas "planas".      |
//| EMA200/ADX/ATR si usan los indicadores nativos (MODE_EMA/iADX/    |
//| iATR ya coinciden con el calculo de Python en el resto del        |
//| proyecto, suavizado de Wilder = pandas ewm alpha=1/period).       |
//+------------------------------------------------------------------+
#property copyright "PailsSentinelMT5Trade"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <PST_V2\PST_RiskManager.mqh>
#include <PST_V2\PST_Logger.mqh>
#include <PST_V2\PST_OptExport.mqh>

//--------------------------------------------------------------- inputs
input string   EA_Name             = "PST_MacdMomentum_GER40_H1";
input int      InpMagicNumber      = 101300;

input group    "--- Estrategia H28b: histograma MACD + ADX + EMA200 ---"
input int      InpMacdFast         = 12;
input int      InpMacdSlow         = 26;
input int      InpMacdSignal       = 9;
input int      InpEmaTrend         = 200;
input int      InpAdxPeriod        = 14;
input double   InpAdxThreshold     = 20.0;
input int      InpAtrPeriod        = 14;
input double   InpAtrStopMult      = 1.2;   // distancia del stop, en x ATR (riesgo = stop)
input double   InpTpRMultiple      = 1.5;   // TP = riesgo x este multiplo
input int      InpMaxBarsHold      = 32;    // salida por tiempo si no toca SL/TP antes

input group    "--- Gestion de riesgo ---"
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
int    handleEmaTrend, handleAdx, handleAtr;
datetime lastBarTime = 0;
double dayStartBalance = 0;
datetime currentDay = 0;
string g_correlationGroup[];

// Recursion EMA manual del MACD (ver nota de cabecera). Alpha estilo pandas
// ewm(span=N, adjust=False): alpha = 2/(N+1).
double g_alphaFast, g_alphaSlow, g_alphaSig;
double g_emaFast = 0, g_emaSlow = 0, g_signal = 0, g_lastHist = 0;
bool   g_macdReady = false;

int    g_barsHeld = 0;

//+------------------------------------------------------------------+
void UpdateMacdState(double closePrice)
  {
   g_emaFast = g_alphaFast * closePrice + (1.0 - g_alphaFast) * g_emaFast;
   g_emaSlow = g_alphaSlow * closePrice + (1.0 - g_alphaSlow) * g_emaSlow;
   double macdLine = g_emaFast - g_emaSlow;
   g_signal = g_alphaSig * macdLine + (1.0 - g_alphaSig) * g_signal;
   g_lastHist = macdLine - g_signal;
  }

//+------------------------------------------------------------------+
//| Siembra la recursion EMA del MACD con historial real previo del  |
//| simbolo (tantas velas cerradas como haya disponibles, hasta un   |
//| tope), replicando pandas ewm(adjust=False): primer valor de cada |
//| EMA = primer precio de la ventana, luego recursion hacia         |
//| adelante. Sin esto, arrancar "en frio" (EMA=precio actual) daria |
//| una fase de calentamiento sesgada distinta a la de Python.        |
//+------------------------------------------------------------------+
bool BootstrapMacdState()
  {
   int minNeeded = InpMacdSlow + InpMacdSignal + 50;
   int available = Bars(_Symbol, PERIOD_CURRENT) - 1; // -1: deja fuera la vela en curso (shift 0)
   int n = MathMin(3000, available);
   if(n < minNeeded)
     {
      PST_Log(EA_Name, StringFormat("Aviso: historial insuficiente para sembrar MACD (%d velas, se necesitan %d). Se sembrara en frio.", n, minNeeded));
      return false;
     }

   double closeArr[];
   ArraySetAsSeries(closeArr, false); // false: closeArr[0]=mas antigua, closeArr[n-1]=mas reciente (shift1)
   if(CopyClose(_Symbol, PERIOD_CURRENT, 1, n, closeArr) < n)
      return false;

   g_emaFast = closeArr[0];
   g_emaSlow = closeArr[0];
   g_signal  = 0.0; // macdLine[0] = emaFast[0]-emaSlow[0] = 0
   g_lastHist = 0.0;
   for(int i = 1; i < n; i++)
      UpdateMacdState(closeArr[i]);

   return true;
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);

   handleEmaTrend = iMA(_Symbol, PERIOD_CURRENT, InpEmaTrend, 0, MODE_EMA, PRICE_CLOSE);
   handleAdx      = iADX(_Symbol, PERIOD_CURRENT, InpAdxPeriod);
   handleAtr      = iATR(_Symbol, PERIOD_CURRENT, InpAtrPeriod);
   if(handleEmaTrend == INVALID_HANDLE || handleAdx == INVALID_HANDLE || handleAtr == INVALID_HANDLE)
     {
      PST_Log(EA_Name, "ERROR: no se pudo crear algun indicador en OnInit.");
      return(INIT_FAILED);
     }

   g_alphaFast = 2.0 / (InpMacdFast + 1.0);
   g_alphaSlow = 2.0 / (InpMacdSlow + 1.0);
   g_alphaSig  = 2.0 / (InpMacdSignal + 1.0);
   g_macdReady = BootstrapMacdState();

   dayStartBalance = PST_CurrentBalance();
   currentDay = 0;
   g_barsHeld = 0;
   PST_ParseCsvGroup(InpCorrelationGroup, g_correlationGroup);

   PST_Log(EA_Name, StringFormat("Inicializado en %s %s. Magic=%d ADX>%.0f StopMult=%.2f TP_R=%.2f MaxBarsHold=%d Riesgo=%.1f%% MacdReady=%s",
           _Symbol, EnumToString(PERIOD_CURRENT), InpMagicNumber,
           InpAdxThreshold, InpAtrStopMult, InpTpRMultiple, InpMaxBarsHold, InpRiskPercent,
           g_macdReady ? "SI" : "EN_FRIO"));
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   IndicatorRelease(handleEmaTrend);
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

   // --- Actualiza SIEMPRE el estado del MACD con la vela recien cerrada (shift 1),
   // haya o no posicion abierta -- igual que Python calcula el histograma para toda
   // la serie independientemente de si hay posicion.
   double histPrev = g_lastHist;
   UpdateMacdState(iClose(_Symbol, PERIOD_CURRENT, 1));
   double histNow = g_lastHist;

   bool hasPosition = HasOpenPosition();

   // --- Salida por tiempo (max_bars_hold): SL/TP nativos ya gestiona el broker;
   // esto solo cubre el caso de que ninguno de los dos se haya tocado todavia.
   if(hasPosition)
     {
      g_barsHeld++;
      if(g_barsHeld >= InpMaxBarsHold)
        {
         double price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         if(trade.PositionClose(_Symbol))
            PST_LogTrade(EA_Name, "CLOSE", _Symbol, 0, price, 0, 0, StringFormat("max_bars_hold alcanzado (%d)", InpMaxBarsHold));
         else
            PST_Log(EA_Name, "ERROR al cerrar posicion (max_bars_hold): " + IntegerToString(trade.ResultRetcode()));
        }
      return;
     }

   if(!g_macdReady)
      return;

   double emaBuf[], adxBuf[], atrBuf[];
   ArraySetAsSeries(emaBuf, true);
   ArraySetAsSeries(adxBuf, true);
   ArraySetAsSeries(atrBuf, true);
   if(CopyBuffer(handleEmaTrend, 0, 1, 1, emaBuf) < 1 ||
      CopyBuffer(handleAdx, 0, 1, 1, adxBuf) < 1 ||
      CopyBuffer(handleAtr, 0, 1, 1, atrBuf) < 1)
     {
      PST_Log(EA_Name, "ERROR: fallo al copiar buffer de algun indicador.");
      return;
     }
   double emaTrendVal = emaBuf[0];
   double adxVal = adxBuf[0];
   double atrVal = atrBuf[0];
   if(atrVal <= 0)
      return;

   double closeSignal = iClose(_Symbol, PERIOD_CURRENT, 1);

   bool histCrossUp   = histPrev <= 0 && histNow > 0;
   bool histCrossDown = histPrev >= 0 && histNow < 0;
   bool trending = adxVal > InpAdxThreshold;

   bool wantLong  = histCrossUp   && trending && closeSignal > emaTrendVal;
   bool wantShort = histCrossDown && trending && closeSignal < emaTrendVal;
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

   double risk = InpAtrStopMult * atrVal;
   if(risk <= 0)
      return;

   if(wantLong)
     {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl = NormalizeDouble(ask - risk, digits);
      double tp = NormalizeDouble(ask + risk * InpTpRMultiple, digits);
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
        {
         g_barsHeld = 0;
         PST_LogTrade(EA_Name, "OPEN_BUY", _Symbol, lots, ask, sl, tp, StringFormat("hist cross up, ADX=%.1f>%.1f, Close>EMA%d", adxVal, InpAdxThreshold, InpEmaTrend));
        }
      else
         PST_Log(EA_Name, "ERROR al abrir compra: " + IntegerToString(trade.ResultRetcode()));
     }
   else if(wantShort)
     {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl = NormalizeDouble(bid + risk, digits);
      double tp = NormalizeDouble(bid - risk * InpTpRMultiple, digits);
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
        {
         g_barsHeld = 0;
         PST_LogTrade(EA_Name, "OPEN_SELL", _Symbol, lots, bid, sl, tp, StringFormat("hist cross down, ADX=%.1f>%.1f, Close<EMA%d", adxVal, InpAdxThreshold, InpEmaTrend));
        }
      else
         PST_Log(EA_Name, "ERROR al abrir venta: " + IntegerToString(trade.ResultRetcode()));
     }
  }

//+------------------------------------------------------------------+
double OnTester()
  {
   string fileName = StringFormat("PST_V2_OptResults\\%s_%s_%s.csv",
                                   EA_Name, _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period));
   string header = "AdxThreshold;AtrStopMult;TpRMultiple;NetProfit;ProfitFactor;Trades;Sharpe;MaxDDPercent;ExpectedPayoff";
   string line = StringFormat("%.1f;%.2f;%.2f;%.2f;%.3f;%d;%.3f;%.2f;%.2f",
                 InpAdxThreshold, InpAtrStopMult, InpTpRMultiple,
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
