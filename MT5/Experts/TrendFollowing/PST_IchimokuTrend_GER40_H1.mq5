//+------------------------------------------------------------------+
//|                  PST_IchimokuTrend_GER40_H1.mq5                   |
//|                              PailsSentinelMT5TradeV2 — Fase 6+     |
//|                                                                    |
//| Hipotesis H45 (python/notebooks/backtest_engine.py::               |
//| backtest_h45_ichimoku_trend, run_h44_h45_new_indicators.py),       |
//| primera estrategia del proyecto con Ichimoku Kinko Hyo. Sistema    |
//| de tendencia clasico: precio ya por encima/debajo de la nube       |
//| (Senkou A/B, filtro de tendencia de fondo) + cruce Tenkan/Kijun a  |
//| favor de esa direccion como gatillo de entrada -- no hace falta    |
//| EMA de tendencia aparte, la nube ya cumple ese papel. Sin ancla    |
//| horaria (igual que H44/CCI) -- respeta la preferencia del usuario  |
//| de no atar la estrategia a una franja horaria fija. TP DINAMICO    |
//| segun ADX en el momento de la entrada (mismo patron que el resto   |
//| de la familia H41/H42/H44), SL por ATR, sin limite de tiempo en    |
//| la posicion (sale solo por TP o SL).                               |
//|                                                                    |
//| Cribado Python (cesta de indices/metales H1 + 4 pares forex        |
//| mayores H4, split de la muestra en dos mitades, sesion             |
//| 2026-09-02): GER40.cash H1 forma meseta real (tenkan/kijun/        |
//| senkou_b = 9/26/52 estandar Y 10/30/60 ambos estables), pero con   |
//| ASIMETRIA de split (casi todo el retorno en la 1a mitad, 23.9% y   |
//| 14.5%, la 2a practicamente plana, 0.4-0.5%) -- mismo patron ya     |
//| visto en H28b/GER40-MACD (que si paso la confirmacion real pese a  |
//| una asimetria parecida, 4.9%/4.3%). No descalificante por si solo, |
//| pero vigilar de cerca el split en la confirmacion real, no solo    |
//| el signo total. Informe:                                           |
//| python/reports/resumen_h44_h45_new_indicators.md.                  |
//|                                                                    |
//| CONFIRMADO en Strategy Tester real (2026-09-02), test individual   |
//| con parametros por defecto (Tenkan=9/Kijun=26/SenkouB=52/          |
//| AtrStopMult=2.0), sin limites de riesgo de cuenta (para split      |
//| limpio, ver leccion metodologica H21 en la memoria del proyecto):  |
//| periodo completo 2024.08.22-2026.08.20 -> +2322.96€ (23.2%), PF    |
//| 1.181, 166 operaciones, Sharpe 1.21. Split in/out-of-sample: 1a    |
//| mitad (2024.08.22-2025.08.21) +1848.80€ PF 1.342 80 ops Sharpe     |
//| 2.10, 2a mitad (2025.08.21-2026.08.20) +525.23€ PF 1.084 86 ops    |
//| Sharpe 0.57 -- AMBAS POSITIVAS, pasa el criterio de validacion del |
//| proyecto (aunque con la misma asimetria ya prevista en el cribado  |
//| Python, mas retorno en la 1a mitad). Fue el UNICO de los 4         |
//| candidatos nuevos de esta sesion (GBPUSD/H44-CCI, AMD/H41,         |
//| GOOG/H42, GER40/H45-Ichimoku) que paso la confirmacion real limpia |
//| -- los otros 3 fallaron en real pese a pasar el cribado Python.    |
//| Pendiente que el usuario adjunte el EA al grafico de GER40.cash H1 |
//| cuando decida darlo por bueno.                                     |
//|                                                                    |
//| Nota de implementacion: el iIchimoku nativo de MT5 ya devuelve los |
//| buffers Senkou Span A/B DESPLAZADOS hacia adelante (kijun_sen      |
//| periodos, convenio estandar de la plataforma para dibujar la nube  |
//| tal como se ve en el grafico) -- exactamente el mismo desplazamiento |
//| causal que python/notebooks/backtest_engine.py::ichimoku() aplica  |
//| con shift(displacement). Por eso CopyBuffer(handle, 2/3, 1, 1, buf) |
//| en la vela shift=1 ya da directamente el valor de la nube "tal     |
//| como estaba dibujada" en ese momento, sin necesidad de recalcular  |
//| nada a mano (a diferencia del iMACD nativo, que si diverge).        |
//+------------------------------------------------------------------+
#property copyright "PailsSentinelMT5Trade"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <PST_V2\PST_RiskManager.mqh>
#include <PST_V2\PST_Logger.mqh>
#include <PST_V2\PST_OptExport.mqh>

//--------------------------------------------------------------- inputs
input string   EA_Name             = "PST_IchimokuTrend_GER40_H1";
input int      InpMagicNumber      = 102001;

input group    "--- Estrategia H45: tendencia con nube de Ichimoku ---"
input int      InpTenkanPeriod     = 9;
input int      InpKijunPeriod      = 26;
input int      InpSenkouBPeriod    = 52;

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
int    handleIchimoku, handleAdx, handleAtr;
datetime lastBarTime = 0;
double dayStartBalance = 0;
datetime currentDay = 0;
string g_correlationGroup[];

// Buffers de iIchimoku: 0=Tenkan, 1=Kijun, 2=Senkou A (ya desplazado), 3=Senkou B (ya desplazado), 4=Chikou
#define ICHIMOKU_TENKAN   0
#define ICHIMOKU_KIJUN    1
#define ICHIMOKU_SENKOU_A 2
#define ICHIMOKU_SENKOU_B 3

//+------------------------------------------------------------------+
int OnInit()
  {
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);

   handleIchimoku = iIchimoku(_Symbol, PERIOD_CURRENT, InpTenkanPeriod, InpKijunPeriod, InpSenkouBPeriod);
   handleAdx      = iADX(_Symbol, PERIOD_CURRENT, InpAdxPeriod);
   handleAtr      = iATR(_Symbol, PERIOD_CURRENT, InpAtrPeriod);
   if(handleIchimoku == INVALID_HANDLE || handleAdx == INVALID_HANDLE || handleAtr == INVALID_HANDLE)
     {
      PST_Log(EA_Name, "ERROR: no se pudo crear algun indicador en OnInit.");
      return(INIT_FAILED);
     }

   dayStartBalance = PST_CurrentBalance();
   currentDay = 0;
   PST_ParseCsvGroup(InpCorrelationGroup, g_correlationGroup);

   PST_Log(EA_Name, StringFormat("Inicializado en %s %s. Magic=%d Tenkan=%d Kijun=%d SenkouB=%d Riesgo=%.1f%%",
           _Symbol, EnumToString(PERIOD_CURRENT), InpMagicNumber,
           InpTenkanPeriod, InpKijunPeriod, InpSenkouBPeriod, InpRiskPercent));
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   IndicatorRelease(handleIchimoku);
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

   bool hasPosition = HasOpenPosition();
   if(hasPosition)
      return; // sale solo por TP/SL nativos, sin limite de tiempo (fiel al prototipo Python)

   double tenkanBuf[], kijunBuf[], senkouABuf[], senkouBBuf[];
   ArraySetAsSeries(tenkanBuf, true);
   ArraySetAsSeries(kijunBuf, true);
   ArraySetAsSeries(senkouABuf, true);
   ArraySetAsSeries(senkouBBuf, true);
   if(CopyBuffer(handleIchimoku, ICHIMOKU_TENKAN, 1, 2, tenkanBuf) < 2 ||
      CopyBuffer(handleIchimoku, ICHIMOKU_KIJUN, 1, 2, kijunBuf) < 2 ||
      CopyBuffer(handleIchimoku, ICHIMOKU_SENKOU_A, 1, 1, senkouABuf) < 1 ||
      CopyBuffer(handleIchimoku, ICHIMOKU_SENKOU_B, 1, 1, senkouBBuf) < 1)
     {
      PST_Log(EA_Name, "ERROR: fallo al copiar buffer de Ichimoku.");
      return;
     }
   double tenkanNow  = tenkanBuf[0]; // shift 1: vela recien cerrada
   double tenkanPrev = tenkanBuf[1]; // shift 2
   double kijunNow   = kijunBuf[0];
   double kijunPrev  = kijunBuf[1];
   double cloudTop    = MathMax(senkouABuf[0], senkouBBuf[0]);
   double cloudBottom = MathMin(senkouABuf[0], senkouBBuf[0]);

   double closeSignal = iClose(_Symbol, PERIOD_CURRENT, 1);
   bool aboveCloud = closeSignal > cloudTop;
   bool belowCloud = closeSignal < cloudBottom;
   bool bullishCross = tenkanNow > kijunNow && tenkanPrev <= kijunPrev;
   bool bearishCross = tenkanNow < kijunNow && tenkanPrev >= kijunPrev;

   bool wantLong  = aboveCloud && bullishCross;
   bool wantShort = belowCloud && bearishCross;
   if(!wantLong && !wantShort)
      return;

   double adxBuf[], atrBuf[];
   ArraySetAsSeries(adxBuf, true);
   ArraySetAsSeries(atrBuf, true);
   if(CopyBuffer(handleAdx, 0, 1, 1, adxBuf) < 1 || CopyBuffer(handleAtr, 0, 1, 1, atrBuf) < 1)
     {
      PST_Log(EA_Name, "ERROR: fallo al copiar buffer de ADX/ATR.");
      return;
     }
   double adxVal = adxBuf[0];
   double atrVal = atrBuf[0];
   if(atrVal <= 0)
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
   double tpR = TpRByAdxRegime(adxVal);

   if(wantLong)
     {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl = NormalizeDouble(ask - risk, digits);
      double tp = NormalizeDouble(ask + risk * tpR, digits);
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
         PST_LogTrade(EA_Name, "OPEN_BUY", _Symbol, lots, ask, sl, tp, StringFormat("Precio sobre la nube + cruce alcista Tenkan/Kijun (ADX=%.1f, TPxR=%.2f)", adxVal, tpR));
      else
         PST_Log(EA_Name, "ERROR al abrir compra: " + IntegerToString(trade.ResultRetcode()));
     }
   else if(wantShort)
     {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl = NormalizeDouble(bid + risk, digits);
      double tp = NormalizeDouble(bid - risk * tpR, digits);
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
         PST_LogTrade(EA_Name, "OPEN_SELL", _Symbol, lots, bid, sl, tp, StringFormat("Precio bajo la nube + cruce bajista Tenkan/Kijun (ADX=%.1f, TPxR=%.2f)", adxVal, tpR));
      else
         PST_Log(EA_Name, "ERROR al abrir venta: " + IntegerToString(trade.ResultRetcode()));
     }
  }

//+------------------------------------------------------------------+
double OnTester()
  {
   string fileName = StringFormat("PST_V2_OptResults\\%s_%s_%s.csv",
                                   EA_Name, _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period));
   string header = "TenkanPeriod;KijunPeriod;SenkouBPeriod;NetProfit;ProfitFactor;Trades;Sharpe;MaxDDPercent;ExpectedPayoff";
   string line = StringFormat("%d;%d;%d;%.2f;%.3f;%d;%.3f;%.2f;%.2f",
                 InpTenkanPeriod, InpKijunPeriod, InpSenkouBPeriod,
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
