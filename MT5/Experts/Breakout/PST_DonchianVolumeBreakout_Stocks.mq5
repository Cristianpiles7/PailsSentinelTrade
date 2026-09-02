//+------------------------------------------------------------------+
//|          PST_DonchianVolumeBreakout_Stocks.mq5                    |
//|                              PailsSentinelMT5TradeV2 — Fase 6+     |
//|                                                                    |
//| Hipotesis H41 (python/notebooks/backtest_engine.py::               |
//| backtest_h41_donchian_volume_breakout), primera estrategia de       |
//| "inercia" para acciones de esta rama que pasa el cribado con        |
//| generalizacion real entre simbolos -- distinta de las dos familias  |
//| de reversion/fade en acciones ya descartadas (H23 momentum          |
//| transversal, H21-acciones), que mueren a 2 puntos basicos de        |
//| spread. Aqui se opera A FAVOR de la ruptura (al contrario que H21), |
//| motivado por que en acciones el momentum SI tiene edge real (H20   |
//| es de las mejores estrategias del proyecto, Sharpe 4.58 en MSFT).   |
//|                                                                    |
//| Idea: ruptura del maximo/minimo de las ultimas InpDonchianPeriod    |
//| velas H1 (causal, no incluye la vela en curso) CON volumen de la    |
//| vela de ruptura por encima de InpVolumeMult x su propia media       |
//| movil de InpVolumePeriod velas -- exige participacion real, no      |
//| ruido de baja liquidez. Filtro de tendencia de fondo: Close por     |
//| encima/debajo de su EMA(InpEmaTrend) propia (no ir a contra-        |
//| tendencia). TP DINAMICO segun ADX en el momento de la entrada       |
//| (mismo patron que PST_LiquiditySweepFade_EURUSD_M5.mq5): ADX bajo   |
//| -> TP amplio (tendencia con mas recorrido), ADX alto ya al entrar   |
//| -> TP corto (movimiento maduro, mas riesgo de agotamiento). SL por  |
//| ATR. Cierre FORZADO fin de sesion (nunca overnight, acciones sin    |
//| cotizacion continua) -- comprueba la hora de la vela que ACABA DE   |
//| EMPEZAR (shift 0), no la cerrada, mismo criterio que corrigio el    |
//| bug de H20 (ver PST_RelativeStrength_H1.mq5). Puede reabrir mas de  |
//| una vez el mismo dia si vuelve a haber ruptura tras cerrar la       |
//| anterior (fiel al prototipo Python, que no limita a una operacion   |
//| diaria en esta hipotesis).                                          |
//|                                                                    |
//| Validado en Python sobre datos H1 reales (2024.08-2026.08, ~2 años, |
//| coste de spread real en vivo: AAPL 0.14, TSLA 0.47), sesion         |
//| 2026-09-02.                                                          |
//| Con InpDonchianPeriod=40/InpVolumeMult=1.5 (defecto de este EA):    |
//| AAPL estable en 6/6 periodos de Donchian probados (25-50) con este  |
//| mismo volume_mult -- meseta muy limpia, no depende de calibrar el   |
//| periodo exacto. TSLA estable en 4/6 con volume_mult=1.5 y en 6/6    |
//| (mas limpio, menor magnitud) con volume_mult=1.75. MSFT NO          |
//| funciona con este mecanismo (negativo/inestable en todo el          |
//| barrido) -- no desplegar en MSFT. volume_mult=2.0 es demasiado      |
//| restrictivo en los tres simbolos (muy pocas operaciones). Ver       |
//| tambien PST_GapAndGo_AAPL_H1.mq5 (H42, la otra idea de esta         |
//| sesion, solo AAPL). Informes: python/reports/h40_h43_stocks_scan.csv|
//| (barrido inicial) y deepening posterior en la conversacion.         |
//|                                                                    |
//| CONFIRMADO en Strategy Tester real (2026-09-02), tras corregir el   |
//| bug de iHighest/iLowest (ver mas abajo): donchian=40/vol=1.5, H1,   |
//| 2024.08.22-2026.08.20. AAPL: PF 1.18-1.34 consistente en el         |
//| barrido, split in/out-of-sample AMBAS mitades positivas (+113€/    |
//| +78€). TSLA: PF 2.5-2.8, Sharpe 13-15 en el barrido, split ambas    |
//| mitades positivas y la 2a MEJOR que la 1a (+363€/+604€, PF hasta    |
//| 5.85). Pendiente que el usuario adjunte el EA a los graficos de    |
//| AAPL y TSLA cuando decida darlo por bueno.                          |
//|                                                                    |
//| NOTA de fidelidad Python-vs-MT5: el SL y TP se envian como ordenes  |
//| NATIVAS del broker desde la apertura (igual que el resto de EAs del |
//| proyecto) -- monitorizados de forma continua por el broker, no solo |
//| en la vela de entrada como una simulacion vectorizada simple.       |
//+------------------------------------------------------------------+
#property copyright "PailsSentinelMT5Trade"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <PST_V2\PST_RiskManager.mqh>
#include <PST_V2\PST_Logger.mqh>
#include <PST_V2\PST_OptExport.mqh>

//--------------------------------------------------------------- inputs
input string   EA_Name             = "PST_DonchianVolumeBreakout_Stocks";
input int      InpMagicNumber      = 101900;

input group    "--- Estrategia H41: ruptura de rango + volumen + tendencia ---"
input int      InpDonchianPeriod   = 40;
input int      InpVolumePeriod     = 20;
input double   InpVolumeMult       = 1.5;   // 1.5 valido en AAPL/TSLA; 1.75 alternativa mas limpia en TSLA
input int      InpEmaTrend         = 200;
input int      InpSessionOpenHour  = 16;    // hora de servidor, apertura de NY (verificado)
input int      InpSessionLastEntryHour = 21;
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
int    handleAtr, handleAdx, handleEmaTrend;
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
   if(hasPosition)
      return;

   MqlDateTime dtSignal;
   datetime signalBarTime = iTime(_Symbol, PERIOD_CURRENT, 1);
   TimeToStruct(signalBarTime, dtSignal);
   int signalHour = dtSignal.hour;

   if(signalHour < InpSessionOpenHour || signalHour > InpSessionLastEntryHour)
      return;

   // start=2 (no 1): el rango de referencia debe excluir la propia vela de señal (shift 1),
   // igual que RollHigh=High.rolling(N).max().shift(1) en Python -- con start=1 la vela de
   // señal queda incluida en su propio rango y la ruptura nunca puede darse (bug encontrado
   // en la confirmacion MT5 real, 2026-09-02: 0 operaciones en TODAS las combinaciones).
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

   double highSignal  = iHigh(_Symbol, PERIOD_CURRENT, 1);
   double lowSignal    = iLow(_Symbol, PERIOD_CURRENT, 1);
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

   bool breakoutUp   = highSignal > rollHigh && volOk && closeSignal > emaRef;
   bool breakoutDown = lowSignal  < rollLow  && volOk && closeSignal < emaRef;
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
         PST_LogTrade(EA_Name, "OPEN_BUY", _Symbol, lots, ask, sl, tp, StringFormat("Ruptura alcista Donchian(%d) + volumen (ADX=%.1f, TPxR=%.2f)", InpDonchianPeriod, adxRef, tpR));
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
         PST_LogTrade(EA_Name, "OPEN_SELL", _Symbol, lots, bid, sl, tp, StringFormat("Ruptura bajista Donchian(%d) + volumen (ADX=%.1f, TPxR=%.2f)", InpDonchianPeriod, adxRef, tpR));
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
