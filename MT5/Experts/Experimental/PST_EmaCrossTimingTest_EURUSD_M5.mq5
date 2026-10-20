//+------------------------------------------------------------------+
//|          PST_EmaCrossTimingTest_EURUSD_M5.mq5                     |
//|                              PailsSentinelMT5TradeV2 — Experimental|
//|                                                                    |
//| EA DE DIAGNOSTICO, no una hipotesis de trading nueva. Objetivo:    |
//| aislar UNA sola variable -- ¿el gatillo de entrada se evalua al    |
//| CIERRE de vela (como el resto de EAs del proyecto, IsNewBar() +    |
//| shift 1) o INTRABAR, tick a tick, en cuanto ocurre? -- para        |
//| responder la pregunta del usuario de si las estrategias de ruptura |
//| de EMA que se probaron y descartaron (ver proyecto Python original,|
//| familias de indicadores en general) fallaron por falta de edge o   |
//| por llegar tarde al movimiento.                                    |
//|                                                                    |
//| Diseño: MISMA logica de señal (cruce EMA rapida/lenta), MISMO      |
//| dimensionamiento de SL/TP (ATR de la ultima vela cerrada, igual en |
//| los dos modos), MISMO gestor de riesgo -- lo UNICO que cambia con  |
//| InpIntrabarEntry es EL MOMENTO en que se dispara la entrada:       |
//|   false (baseline) -> igual que todos los EAs del proyecto: al     |
//|                        cierre de la vela donde se confirmo el      |
//|                        cruce (shift 1 vs shift 2).                 |
//|   true  (test)      -> en cuanto el cruce ocurre DENTRO de la vela |
//|                        en formacion (shift 0 recalculado en cada   |
//|                        tick), comparado contra el estado           |
//|                        confirmado al cierre de la vela anterior.   |
//|                                                                    |
//| Con esto controlado, cualquier diferencia de resultado entre las   |
//| dos pasadas del Strategy Tester (misma fecha, mismo simbolo, solo  |
//| cambia InpIntrabarEntry) es atribuible A LA VELOCIDAD DE REACCION, |
//| no a diferencias de logica ni de motor (a diferencia de los labs   |
//| Python del proyecto original, que reimplementan el mercado aparte; |
//| aqui las dos pasadas corren en el MISMO Strategy Tester nativo).   |
//|                                                                    |
//| COMO EJECUTAR EL EXPERIMENTO (obligatorio para que sea valido):    |
//| 1. Strategy Tester -> modo "Cada tick basado en ticks reales"      |
//|    (Every tick based on real ticks). Cualquier otro modo interpola |
//|    ticks sinteticos entre el Open/High/Low/Close de cada vela y el |
//|    modo intrabar dejaria de ser una prueba real de velocidad.      |
//| 2. Simbolo EURUSD, periodo M5, mismo rango de fechas para las dos  |
//|    pasadas (usar el mismo periodo ya confirmado del EA H21,        |
//|    PST_LiquiditySweepFade_EURUSD_M5.mq5, para que haya tick data   |
//|    real disponible en el broker).                                 |
//| 3. Pasada 1: InpIntrabarEntry=false. Pasada 2: InpIntrabarEntry=   |
//|    true. Todo lo demas identico. Comparar NetProfit/ProfitFactor/  |
//|    Sharpe/MaxDD/Trades de las dos.                                 |
//| 4. Alternativa comoda: lanzar como OPTIMIZACION variando SOLO      |
//|    InpIntrabarEntry (2 combinaciones) -- OnTester() vuelca cada    |
//|    pasada a Common\Files\PST_V2_OptResults\<name>.csv (igual que   |
//|    el resto de EAs del proyecto) para comparar las dos filas       |
//|    directamente sin depender del report grafico.                  |
//|                                                                    |
//| Lectura de resultados: si el modo intrabar NO mejora sustancialmen-|
//| te el resultado (o incluso empeora, p.ej. por mas señales falsas   |
//| que se revierten dentro de la misma vela), la conclusion es que el |
//| fallo de las estrategias de EMA/ruptura no era de latencia sino de |
//| ausencia de edge -- coherente con el patron ya visto en el         |
//| proyecto (ORB y ORBFADE, la ruptura Y su inversa exacta, perdiendo |
//| las dos a la vez: firma de mercado eficiente a esta escala, no de  |
//| mecanica mal cronometrada).                                        |
//|                                                                    |
//| NO desplegar en real/demo sin el ciclo de validacion completo del  |
//| proyecto (README.md: split in/out-of-sample positivo en las dos    |
//| mitades). Este EA existe solo para responder la pregunta de        |
//| timing, no es un candidato de estrategia.                          |
//+------------------------------------------------------------------+
#property copyright "PailsSentinelMT5Trade"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <PST_V2\PST_RiskManager.mqh>
#include <PST_V2\PST_Logger.mqh>
#include <PST_V2\PST_OptExport.mqh>

//--------------------------------------------------------------- inputs
input string   EA_Name             = "PST_EmaCrossTimingTest_EURUSD_M5";
input int      InpMagicNumber      = 109900;   // rango reservado para EAs de diagnostico, no colisiona con los 9 activos

input group    "--- Variable bajo prueba ---"
input bool     InpIntrabarEntry    = false;    // false = entrada al cierre de vela (baseline, igual que el resto de EAs). true = entrada intrabar tick a tick

input group    "--- Señal: cruce EMA rapida/lenta ---"
input int      InpFastEmaPeriod    = 20;
input int      InpSlowEmaPeriod    = 50;

input group    "--- SL/TP (identico en ambos modos, dimensionado con la ultima vela CERRADA) ---"
input int      InpAtrPeriod        = 14;
input double   InpAtrStopMult      = 1.5;
input double   InpTpRMultiple      = 2.0;      // 0 = sin TP

input group    "--- Sesion (evita overnight, politica FTMO) ---"
input int      InpForceCloseHour   = 21;       // hora de servidor, cierre forzado

input group    "--- Gestion de riesgo ---"
input double   InpRiskPercent          = 1.0;
input double   InpMaxDailyDrawdownPct  = 4.0;

input group    "--- Limites de cuenta FTMO (docs/politica-de-riesgo.md) ---"
input double   InpAccountInitialBalance = 10000.0;
input double   InpMaxDailyLossAmount    = 500.0;
input double   InpMaxTotalLossAmount    = 1000.0;

//--------------------------------------------------------------- state
CTrade trade;
int    handleFastEma;
int    handleSlowEma;
int    handleAtr;
datetime lastBarTime = 0;
double dayStartBalance = 0;
datetime currentDay = 0;

int  g_confirmedFastAboveSlow = -1;   // -1 = sin inicializar, 0 = lenta encima, 1 = rapida encima (estado AL CIERRE de la ultima vela)
bool g_signaledThisBar        = false; // evita multiples entradas intrabar dentro de la misma vela en formacion

//+------------------------------------------------------------------+
int OnInit()
  {
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);

   handleFastEma = iMA(_Symbol, PERIOD_CURRENT, InpFastEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   handleSlowEma = iMA(_Symbol, PERIOD_CURRENT, InpSlowEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   handleAtr     = iATR(_Symbol, PERIOD_CURRENT, InpAtrPeriod);
   if(handleFastEma == INVALID_HANDLE || handleSlowEma == INVALID_HANDLE || handleAtr == INVALID_HANDLE)
     {
      PST_Log(EA_Name, "ERROR: no se pudo crear algun indicador en OnInit.");
      return(INIT_FAILED);
     }

   dayStartBalance = PST_CurrentBalance();
   currentDay = 0;

   PST_Log(EA_Name, StringFormat("Inicializado en %s %s. Magic=%d IntrabarEntry=%s Fast=%d Slow=%d ATRxStop=%.2f TPxR=%.2f Riesgo=%.1f%%",
           _Symbol, EnumToString(PERIOD_CURRENT), InpMagicNumber, InpIntrabarEntry ? "true" : "false",
           InpFastEmaPeriod, InpSlowEmaPeriod, InpAtrStopMult, InpTpRMultiple, InpRiskPercent));
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   IndicatorRelease(handleFastEma);
   IndicatorRelease(handleSlowEma);
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
     }
  }

//+------------------------------------------------------------------+
bool HasOpenPosition()
  {
   return PositionSelect(_Symbol) && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber;
  }

//+------------------------------------------------------------------+
//| Abre la operacion en la direccion dada, con SL/TP dimensionados   |
//| a partir del ATR de la ULTIMA VELA CERRADA -- identico en los dos |
//| modos, para que solo cambie el momento del gatillo, no el riesgo. |
//+------------------------------------------------------------------+
void OpenTrade(bool buyDirection, string reasonTag)
  {
   bool ddExceeded = PST_DailyDrawdownExceeded(InpMaxDailyDrawdownPct, dayStartBalance);
   bool dailyLossExceeded = PST_DailyLossExceeded(InpMaxDailyLossAmount, dayStartBalance);
   bool totalLossExceeded = PST_TotalLossExceeded(InpAccountInitialBalance, InpMaxTotalLossAmount);
   if(dailyLossExceeded || totalLossExceeded || ddExceeded)
     {
      if(dailyLossExceeded)
         PST_Log(EA_Name, "Circuit breaker: perdida diaria excedida. No se abre operacion.");
      if(totalLossExceeded)
         PST_Log(EA_Name, "Circuit breaker: perdida TOTAL de cuenta excedida. No se abre operacion.");
      return;
     }

   double atrBuf[];
   ArraySetAsSeries(atrBuf, true);
   if(CopyBuffer(handleAtr, 0, 1, 1, atrBuf) < 1)
     {
      PST_Log(EA_Name, "ERROR: fallo al copiar buffer de ATR.");
      return;
     }
   double atrRef = atrBuf[0];
   if(atrRef <= 0)
      return;

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(point <= 0)
      return;

   if(buyDirection)
     {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl = NormalizeDouble(ask - InpAtrStopMult * atrRef, digits);
      double risk = ask - sl;
      double tp = (InpTpRMultiple > 0) ? NormalizeDouble(ask + risk * InpTpRMultiple, digits) : 0.0;
      double slDistancePoints = risk / point;
      if(slDistancePoints <= 0)
         return;
      double lots = PST_CalcLotByRisk(_Symbol, InpRiskPercent, slDistancePoints);
      if(lots <= 0)
        {
         PST_Log(EA_Name, "Lotaje calculado invalido (0). No se abre compra.");
         return;
        }
      if(!PST_CheckMarginOk(_Symbol, ORDER_TYPE_BUY, lots))
        {
         PST_Log(EA_Name, "Margen insuficiente para compra.");
         return;
        }
      if(trade.Buy(lots, _Symbol, ask, sl, tp, EA_Name))
         PST_LogTrade(EA_Name, "OPEN_BUY", _Symbol, lots, ask, sl, tp, reasonTag);
      else
         PST_Log(EA_Name, "ERROR al abrir compra: " + IntegerToString(trade.ResultRetcode()));
     }
   else
     {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl = NormalizeDouble(bid + InpAtrStopMult * atrRef, digits);
      double risk = sl - bid;
      double tp = (InpTpRMultiple > 0) ? NormalizeDouble(bid - risk * InpTpRMultiple, digits) : 0.0;
      double slDistancePoints = risk / point;
      if(slDistancePoints <= 0)
         return;
      double lots = PST_CalcLotByRisk(_Symbol, InpRiskPercent, slDistancePoints);
      if(lots <= 0)
        {
         PST_Log(EA_Name, "Lotaje calculado invalido (0). No se abre venta.");
         return;
        }
      if(!PST_CheckMarginOk(_Symbol, ORDER_TYPE_SELL, lots))
        {
         PST_Log(EA_Name, "Margen insuficiente para venta.");
         return;
        }
      if(trade.Sell(lots, _Symbol, bid, sl, tp, EA_Name))
         PST_LogTrade(EA_Name, "OPEN_SELL", _Symbol, lots, bid, sl, tp, reasonTag);
      else
         PST_Log(EA_Name, "ERROR al abrir venta: " + IntegerToString(trade.ResultRetcode()));
     }
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   UpdateDailyBalanceTracker();

   bool isNewBar = IsNewBar();
   if(isNewBar)
      g_signaledThisBar = false;

   // --- Cierre forzado: hora de la vela que ACABA DE EMPEZAR (shift 0), mismo criterio que el resto del proyecto.
   MqlDateTime dtNow;
   datetime currentBarTime = iTime(_Symbol, PERIOD_CURRENT, 0);
   TimeToStruct(currentBarTime, dtNow);
   if(HasOpenPosition() && dtNow.hour >= InpForceCloseHour)
     {
      double price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      if(trade.PositionClose(_Symbol))
         PST_LogTrade(EA_Name, "CLOSE", _Symbol, 0, price, 0, 0, "cierre forzado fin de sesion");
      else
         PST_Log(EA_Name, "ERROR al cerrar posicion (cierre forzado): " + IntegerToString(trade.ResultRetcode()));
      return;
     }

   // --- Al cierre de cada vela: recalcula el estado CONFIRMADO (shift 1) y, si InpIntrabarEntry=false,
   // dispara la entrada aqui comparando contra el estado confirmado de la vela anterior.
   if(isNewBar)
     {
      double fastBuf[], slowBuf[];
      ArraySetAsSeries(fastBuf, true);
      ArraySetAsSeries(slowBuf, true);
      if(CopyBuffer(handleFastEma, 0, 1, 1, fastBuf) < 1 || CopyBuffer(handleSlowEma, 0, 1, 1, slowBuf) < 1)
        {
         PST_Log(EA_Name, "ERROR: fallo al copiar buffers de EMA (cierre de vela).");
         return;
        }
      int newConfirmed = (fastBuf[0] > slowBuf[0]) ? 1 : 0;
      int prevConfirmed = g_confirmedFastAboveSlow;

      if(!InpIntrabarEntry && prevConfirmed != -1 && !HasOpenPosition())
        {
         if(prevConfirmed == 0 && newConfirmed == 1)
           {
            OpenTrade(true, StringFormat("Cruce EMA%d>EMA%d al cierre de vela", InpFastEmaPeriod, InpSlowEmaPeriod));
            g_signaledThisBar = true;
           }
         else if(prevConfirmed == 1 && newConfirmed == 0)
           {
            OpenTrade(false, StringFormat("Cruce EMA%d<EMA%d al cierre de vela", InpFastEmaPeriod, InpSlowEmaPeriod));
            g_signaledThisBar = true;
           }
        }

      g_confirmedFastAboveSlow = newConfirmed;
     }

   // --- En cada tick: si InpIntrabarEntry=true, compara el estado EN VIVO (shift 0, vela en formacion,
   // recalculado por MT5 en cada tick) contra el ultimo estado CONFIRMADO al cierre -- dispara en cuanto
   // el cruce ocurre, sin esperar al cierre de la vela.
   if(InpIntrabarEntry && !g_signaledThisBar && !HasOpenPosition() && g_confirmedFastAboveSlow != -1)
     {
      double fastLiveBuf[], slowLiveBuf[];
      ArraySetAsSeries(fastLiveBuf, true);
      ArraySetAsSeries(slowLiveBuf, true);
      if(CopyBuffer(handleFastEma, 0, 0, 1, fastLiveBuf) < 1 || CopyBuffer(handleSlowEma, 0, 0, 1, slowLiveBuf) < 1)
         return;

      int liveState = (fastLiveBuf[0] > slowLiveBuf[0]) ? 1 : 0;
      if(liveState != g_confirmedFastAboveSlow)
        {
         OpenTrade(liveState == 1, StringFormat("Cruce EMA%d/EMA%d intrabar (tick)", InpFastEmaPeriod, InpSlowEmaPeriod));
         g_signaledThisBar = true;
        }
     }
  }

//+------------------------------------------------------------------+
double OnTester()
  {
   string fileName = StringFormat("PST_V2_OptResults\\%s_%s_%s.csv",
                                   EA_Name, _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period));
   string header = "IntrabarEntry;FastEma;SlowEma;AtrStopMult;TpRMultiple;NetProfit;ProfitFactor;Trades;Sharpe;MaxDDPercent;ExpectedPayoff";
   string line = StringFormat("%s;%d;%d;%.2f;%.2f;%.2f;%.3f;%d;%.3f;%.2f;%.2f",
                 InpIntrabarEntry ? "true" : "false", InpFastEmaPeriod, InpSlowEmaPeriod,
                 InpAtrStopMult, InpTpRMultiple,
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
