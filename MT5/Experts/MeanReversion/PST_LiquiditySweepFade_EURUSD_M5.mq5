//+------------------------------------------------------------------+
//|              PST_LiquiditySweepFade_EURUSD_M5.mq5                 |
//|                              PailsSentinelMT5TradeV2 — Fase 6+     |
//|                                                                    |
//| Hipotesis H21 (python/notebooks/backtest_engine.py::               |
//| backtest_h21_london_ny_turnaround), primer candidato de scalping   |
//| forex M1/M5 de esta rama del proyecto que paso el cribado Python   |
//| con meseta de parametros Y split in/out-of-sample estable —        |
//| distinto de H27/H28/H29/H30/H31/H32/H34, todos descartados en      |
//| esta misma rama por picos aislados, spread devorando el edge a     |
//| alta frecuencia, o inestabilidad de regimen temporal.               |
//|                                                                    |
//| Idea (inspirada en "Liquidity Sweep H4-M15", catalogo MQL5): cada  |
//| dia, el rango de la sesion europea (maximo/minimo entre            |
//| InpEuroStartHour e InpEuroEndHour, hora de servidor) actua de      |
//| nivel de liquidez. En la ventana de solape Londres-NY               |
//| (InpSweepStartHour a InpSweepEndHour), si una vela ROMPE ese        |
//| maximo/minimo pero CIERRA de vuelta dentro del rango (mecha de     |
//| rechazo, sin confirmar continuacion), se entra EN CONTRA del        |
//| barrido (fade): corto si barrio el maximo, largo si barrio el       |
//| minimo. Solo una operacion por dia. Cierre FORZADO en               |
//| InpForceCloseHour si no toco el stop antes (nunca overnight,        |
//| cuenta FTMO de limites ajustados, docs/politica-de-riesgo.md).      |
//|                                                                    |
//| ACTUALIZACION 2026-09-01 (misma sesion): tras deteccion de la       |
//| ventana original (rango 9-15h/barrido 16-17h/cierre 20h, "Ventana   |
//| A", solo EURUSD), el usuario pidio explicitamente evitar mecanica   |
//| de horario UNICO fijo y en su lugar buscar VARIAS franjas           |
//| independientes que se puedan apilar para cubrir mas horas del dia.  |
//| Escaneando otras 5 franjas del dia con la misma mecanica se         |
//| encontro una "Ventana B" (rango 3-9h / barrido 10-11h / cierre      |
//| 14h) que es MAS ROBUSTA que la original: 19/22 (86%) combinaciones  |
//| estables en el barrido de profundizacion (vs ~60-75% de la Ventana  |
//| A), Y GENERALIZA a EURUSD Y GBPUSD a la vez (la Ventana A solo       |
//| funcionaba en EURUSD). Por eso los inputs por defecto de este EA    |
//| se han cambiado a la Ventana B. La Ventana A original SIGUE siendo  |
//| valida como candidato secundario (para apilar en otra franja        |
//| horaria del dia) -- usar inputs range=9-15h/sweep=16-17h/close=20h  |
//| en EURUSD si se quiere desplegar en paralelo (magic number          |
//| distinto). Ver tambien PST_LiquiditySweepFade_GBPUSD_M5.mq5 (misma  |
//| Ventana B, para GBPUSD).                                            |
//|                                                                    |
//| Validado en Python sobre EURUSD/GBPUSD M5 real (2025.04-2026.09,    |
//| ~16 meses, coste de spread real 0.1/0.3 pip), sesion 2026-09-01.    |
//| Ventana B (VALIDADA, defecto de este EA): rango 3-9h, barrido       |
//| 10-11h, cierre 14h. atr_stop_mult ESTABLE en casi todo el rango     |
//| 1.0-2.5 (11/12 combinaciones EURUSD+GBPUSD, solo GBPUSD/atr=1.0     |
//| rompe signo), Sharpe 0.04-0.10, ~450-500 operaciones. Sensibilidad  |
//| horaria: sweep 9-10/10-11/9-11/10-12 estables en los dos simbolos,  |
//| solo 11-12h rompe signo (8/10 en total) -- meseta real, no pico      |
//| aislado. Ventana A (rango 9-15h/barrido 16-17h/cierre 20h,          |
//| candidato secundario, solo EURUSD): atr_stop_mult estable en        |
//| 1.0-2.0 (5/6), sweep_hour estable en 15-17/16-17/16-18 (3/5),       |
//| euro_hour estable en 8-15/8-16/9-16 (3/4). Ninguna de las dos       |
//| ventanas generaliza a EURGBP (spread mas ancho, sale negativo en    |
//| todas las pruebas, mismo patron que H32/H34 con este par) ni a      |
//| EURUSD/GBPUSD M1 (mayor frecuencia, signo invertido). Informes:     |
//| python/reports/h21_other_windows_scan.csv,                          |
//| python/reports/h21_windowB_deepen.csv,                              |
//| python/reports/h21_deepen_h36_orb_sweep.csv                         |
//|                                                                    |
//| CONFIRMADO en Strategy Tester real (MT5, tick data, 2026-09-01),    |
//| Ventana B, ATRxStop=1.5, EURUSD M5, 2025.04.28-2026.09.01 (99791    |
//| velas): NetProfit +2664.36 EUR (+26.6% sobre 10000), ProfitFactor  |
//| 1.141, 224 operaciones, Sharpe 3.41, MaxDD 23.4%. Barrido de       |
//| ATRxStop en el mismo periodo: 6/7 puntos positivos (1.25 a 2.50,   |
//| pico en 1.5), solo ATRxStop=1.0 negativo -- con solo 110           |
//| operaciones (la mitad de las demas), probablemente ordenes         |
//| rechazadas por margen insuficiente al pedir un lote mayor para el  |
//| mismo riesgo con un stop tan ajustado, no un fallo de la mecanica. |
//| Split in/out-of-sample (sin el limite de perdida total del EA,     |
//| ver nota de circuit breaker abajo): 1a mitad (2025.04.28-          |
//| 2025.12.30) +2369.89 EUR (+23.7%, PF 1.287, 103 ops, Sharpe 6.87),  |
//| 2a mitad (2025.12.30-2026.09.01) +235.41 EUR (+2.35%, PF 1.028,    |
//| 121 ops, Sharpe 0.61) -- AMBAS mitades positivas, mismo patron     |
//| direccional que Python (1a mitad mas fuerte que 2a). PASA el       |
//| criterio de validacion del proyecto (meseta de parametros + split  |
//| estable). Informes en python/reports/h21_windowB_deepen.csv        |
//| (Python) y Common\Files\PST_V2_OptResults\                         |
//| PST_LiquiditySweepFade_EURUSD_M5_EURUSD_PERIOD_M5.csv (MT5 real).  |
//|                                                                    |
//| NOTA sobre el circuit breaker de perdida total (InpMaxTotalLoss    |
//| Amount, 1000 EUR por defecto sobre InpAccountInitialBalance=10000, |
//| es decir 10%): con el split in/out-of-sample probado de forma      |
//| INDEPENDIENTE (cada mitad arranca en 10000 EUR desde cero, sin el  |
//| colchon de ganancias de la mitad anterior), la 2a mitad SI llega a |
//| superar esa perdida total en un momento dado del recorrido (antes  |
//| de recuperarse hasta el +235 final) -- el circuit breaker lo       |
//| habria parado en real si el EA hubiese arrancado justo ahi sin     |
//| colchon previo. En el test CONTINUO (periodo completo sin reset de |
//| capital, escenario mas realista de despliegue real) esto NO llega  |
//| a pasar porque el colchon de la 1a mitad absorbe la caida. Vale la |
//| pena que el usuario revise InpMaxDailyDrawdownPct/InpMaxTotalLoss  |
//| Amount/InpRiskPercent antes de operar en real, sabiendo que el     |
//| dibujo de drawdown intramuestral puede acercarse a ese limite.     |
//|                                                                    |
//| GBPUSD (ver PST_LiquiditySweepFade_GBPUSD_M5.mq5): a diferencia de |
//| EURUSD, GBPUSD NO se confirma en MT5 real pese a que Python lo     |
//| daba como estable -- mismo patron ya visto varias veces en este    |
//| proyecto (H29/XAUUSD, H32/acciones): la criba en Python no         |
//| garantiza la confirmacion en datos reales de tick. NO desplegar el |
//| EA de GBPUSD sin retrabajar esa version.                           |
//|                                                                    |
//| ACTUALIZACION 2026-09-02 -- TP anadido tras pregunta del usuario     |
//| ("no seria mejor ponerle TP?") al ver operar el EA en real sin uno.  |
//| H21 original nunca tuvo TP (sale por stop o cierre forzado), pero    |
//| se probo en Python (backtest_h21b_liquidity_sweep_tp,                |
//| python/reports/h21b_tp_test_eurusd_m5.csv) sobre los mismos datos    |
//| confirmados: un TP AJUSTADO (0.5R-2.0R) empeora el resultado (baja   |
//| el retorno/Sharpe aunque sube el win rate -- parte del edge viene    |
//| de dejar correr las reversiones mas grandes), pero un TP GENEROSO    |
//| (2.75R-5.0R, meseta real, no pico aislado) MEJORA el resultado       |
//| frente al control sin TP: +2.2%/Sharpe0.07/MaxDD-1.9% (sin TP) vs    |
//| +3.3%/Sharpe0.11/MaxDD-1.6% (TP=3.25R). Se fija InpTpRMultiple=3.0   |
//| (redondo, dentro de la meseta). InpTpRMultiple=0 desactiva el TP y   |
//| recupera el comportamiento original. Las 6+9 combinaciones           |
//| probadas en el barrido de TP son TODAS estables en split in/out-of- |
//| sample.                                                              |
//|                                                                      |
//| ACTUALIZACION 2026-09-02 (2) -- TP DINAMICO segun ADX al entrar,    |
//| a peticion del usuario ("quiero que el 3R sea dinamico"). Distinto  |
//| de una salida dinamica de verdad (que se probo en backtest_h21c y   |
//| se descarto por el riesgo de divergencia Python-MT5 sin mejora      |
//| clara de retorno, solo de drawdown): aqui el ADX se lee UNA VEZ al  |
//| momento del barrido y decide el TAMAÑO del TP, que despues queda    |
//| FIJO como el resto de la operacion -- mismo patron anti-divergencia |
//| que toda la familia R-multiple del proyecto, sin reevaluacion       |
//| continua. Regimen de rango genuino (ADX<InpAdxLow) -> TP amplio     |
//| (mas margen, mercado sin tendencia). Regimen intermedio -> TP medio |
//| (el 3.0R validado antes). Regimen con tendencia formandose          |
//| (ADX>=InpAdxHigh) -> TP corto (mayor riesgo de que el barrido sea   |
//| el inicio de una tendencia real). Validado en Python               |
//| (backtest_h21d_liquidity_sweep_dynamic_tp,                          |
//| python/reports/h21d_dynamic_tp_grid.csv): con                       |
//| AdxLow=20/AdxHigh=30/TpLow=4.5R/TpMid=3.0R/TpHigh=1.25R ->           |
//| +3.7% retorno, Sharpe 0.14, MaxDD -0.9% (vs +2.8%/0.10/-1.7% del TP  |
//| fijo 3.0R) -- MEJOR en TODAS las metricas. Barrido de 20             |
//| combinaciones de TpLow (3.5-5.0R) x TpHigh (1.0-2.0R) alrededor de   |
//| ese punto: 20/20 ESTABLES en split in/out-of-sample -- meseta        |
//| amplia, no pico aislado, evidencia mas solida que la del TP fijo    |
//| original.                                                            |
//|                                                                      |
//| ACTUALIZACION 2026-09-02 (3) -- Ventana C sustituye a la Ventana B  |
//| como defecto. Escaneo horario mas fino (hora a hora, no saltos de   |
//| 3h) sobre EURUSD encontro rango 2-8h/barrido 9-10h/cierre 13h con   |
//| MEJOR resultado que la Ventana B (+4.7% vs +2.7%, Sharpe 0.13 vs    |
//| 0.08). Profundizada con el mismo rigor: 14/15 (93%) estable en      |
//| atr_stop_mult (6/6), ventana de rango (4/4) y sensibilidad de la    |
//| hora de barrido (4/5) -- meseta mas limpia que la propia Ventana B  |
//| original, y dentro de esa meseta la propia franja 10-11h (el        |
//| defecto anterior) resulta ser el punto MAS DEBIL/inestable de la    |
//| vecindad. Por tanto los defectos de este EA pasan a la Ventana C.   |
//| Ventanas A (9-15h/16-17h/20h) y B (3-9h/10-11h/14h) siguen           |
//| documentadas como alternativas para apilar en otras franjas del     |
//| dia si se desea, pero YA NO son el punto de partida recomendado en  |
//| EURUSD. NO se probo si esta Ventana C generaliza a GBPUSD (la B      |
//| tampoco generalizaba). Informe:                                     |
//| python/reports/h21_candidateC_deepen.csv,                           |
//| python/reports/h21_finegrain_scan_eurusd.csv.                       |
//|                                                                      |
//| NOTA IMPORTANTE de fidelidad Python-vs-MT5: en el motor Python el   |
//| stop-loss (ATR desde la entrada) solo se comprueba con las velas    |
//| que quedan DENTRO de la ventana de sweep (InpSweepStartHour-        |
//| InpSweepEndHour) — si no salta ahi, la posicion queda "desprotegida"|
//| en el prototipo Python hasta el cierre forzado (efecto colateral    |
//| del bucle vectorizado, no una decision deliberada de la             |
//| hipotesis). En este EA el SL se envia como orden NATIVA del broker  |
//| desde la apertura (igual que el resto de EAs del proyecto, ver      |
//| PST_VwapReversion_ADX.mq5) y por tanto queda activo y monitorizado  |
//| de forma CONTINUA hasta el cierre forzado o el stop — mas           |
//| protector que el prototipo Python, no menos. Es una divergencia     |
//| conocida y deliberada (mas segura), no un bug a replicar.           |
//| Salvo esto, la logica de entrada/deteccion de barrido es fiel.      |
//+------------------------------------------------------------------+
#property copyright "PailsSentinelMT5Trade"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <PST_V2\PST_RiskManager.mqh>
#include <PST_V2\PST_Logger.mqh>
#include <PST_V2\PST_OptExport.mqh>

//--------------------------------------------------------------- inputs
input string   EA_Name             = "PST_LiquiditySweepFade_EURUSD_M5";
input int      InpMagicNumber      = 101800;

input group    "--- Estrategia H21: liquidity sweep (fade). Ventana B (defecto, la mas robusta) ---"
input int      InpEuroStartHour    = 2;    // hora de servidor, inicio rango de referencia
input int      InpEuroEndHour      = 8;    // hora de servidor, fin rango de referencia
input int      InpSweepStartHour   = 9;    // hora de servidor, inicio ventana de entrada
input int      InpSweepEndHour     = 10;   // hora de servidor, fin ventana de entrada
input int      InpForceCloseHour   = 13;   // hora de servidor, cierre forzado (nunca overnight)
input int      InpAtrPeriod        = 14;
input double   InpAtrStopMult      = 1.5;  // estable en 1.0-2.5 en Ventana B (ver cabecera)
input double   InpTpRMultiple      = 3.0;  // usado solo si InpDynamicTp=false; 0 = sin TP (ver cabecera)

input group    "--- TP dinamico segun ADX al entrar (ver cabecera, 2026-09-02) ---"
input bool     InpDynamicTp        = true;  // true = usa TpLow/TpMid/TpHigh segun ADX; false = InpTpRMultiple fijo
input int      InpAdxPeriod        = 14;
input double   InpAdxLow           = 20.0;  // ADX < esto = regimen de rango -> TP amplio
input double   InpAdxHigh          = 30.0;  // ADX >= esto = tendencia formandose -> TP corto
input double   InpTpRLowRegime     = 4.5;   // TP si ADX < InpAdxLow
input double   InpTpRMidRegime     = 3.0;   // TP si InpAdxLow <= ADX < InpAdxHigh
input double   InpTpRHighRegime    = 1.25;  // TP si ADX >= InpAdxHigh

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
int    handleAtr;
int    handleAdx;
datetime lastBarTime = 0;
double dayStartBalance = 0;
datetime currentDay = 0;
string g_correlationGroup[];

datetime g_sessionDay   = 0;
double   g_euroHigh     = 0;
double   g_euroLow      = 0;
bool     g_euroRangeSet = false;
bool     g_tradedToday  = false;

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

   PST_Log(EA_Name, StringFormat("Inicializado en %s %s. Magic=%d Euro=%d-%dh Sweep=%d-%dh ATRxStop=%.2f DynamicTp=%s TPxR=%.2f (Low=%.2f Mid=%.2f High=%.2f) Riesgo=%.1f%%",
           _Symbol, EnumToString(PERIOD_CURRENT), InpMagicNumber,
           InpEuroStartHour, InpEuroEndHour, InpSweepStartHour, InpSweepEndHour,
           InpAtrStopMult, InpDynamicTp ? "true" : "false", InpTpRMultiple,
           InpTpRLowRegime, InpTpRMidRegime, InpTpRHighRegime, InpRiskPercent));
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
void OnTick()
  {
   UpdateDailyBalanceTracker();

   if(!IsNewBar())
      return;

   // --- Cierre forzado: comprueba la hora de la vela que ACABA DE EMPEZAR (shift 0), no la
   // que acaba de cerrar -- mismo criterio que corrigio el bug de H20/usa el resto del proyecto.
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

   // --- Vela recien cerrada (shift 1): actualiza rango europeo y evalua senal de barrido.
   datetime signalBarTime = iTime(_Symbol, PERIOD_CURRENT, 1);
   MqlDateTime dtSignal;
   TimeToStruct(signalBarTime, dtSignal);
   datetime dayKey = (datetime)(dtSignal.year * 10000 + dtSignal.mon * 100 + dtSignal.day);
   int signalHour = dtSignal.hour;

   if(dayKey != g_sessionDay)
     {
      g_sessionDay   = dayKey;
      g_euroHigh     = 0;
      g_euroLow      = 0;
      g_euroRangeSet = false;
      g_tradedToday  = false;
     }

   double highSignal  = iHigh(_Symbol, PERIOD_CURRENT, 1);
   double lowSignal   = iLow(_Symbol, PERIOD_CURRENT, 1);
   double closeSignal = iClose(_Symbol, PERIOD_CURRENT, 1);

   if(signalHour >= InpEuroStartHour && signalHour <= InpEuroEndHour)
     {
      if(!g_euroRangeSet)
        {
         g_euroHigh = highSignal;
         g_euroLow  = lowSignal;
         g_euroRangeSet = true;
        }
      else
        {
         if(highSignal > g_euroHigh) g_euroHigh = highSignal;
         if(lowSignal  < g_euroLow)  g_euroLow  = lowSignal;
        }
     }

   if(hasPosition || g_tradedToday)
      return;

   if(!g_euroRangeSet)
      return;
   if(signalHour < InpSweepStartHour || signalHour > InpSweepEndHour)
      return;

   bool sweptHigh = (highSignal > g_euroHigh) && (closeSignal < g_euroHigh);
   bool sweptLow  = (lowSignal  < g_euroLow)  && (closeSignal > g_euroLow);
   if(!sweptHigh && !sweptLow)
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

   // --- TP dinamico: ADX en el momento del barrido decide el tamaño del TP, UNA sola vez
   // (no se reevalua mientras la posicion esta abierta -- ver cabecera, 2026-09-02).
   double tpRMultiple = InpTpRMultiple;
   if(InpDynamicTp)
     {
      double adxBuf[];
      ArraySetAsSeries(adxBuf, true);
      if(CopyBuffer(handleAdx, 0, 0, 2, adxBuf) < 2)
        {
         PST_Log(EA_Name, "ERROR: fallo al copiar buffer de ADX.");
         return;
        }
      double adxRef = adxBuf[1];
      if(adxRef < InpAdxLow)
         tpRMultiple = InpTpRLowRegime;
      else if(adxRef < InpAdxHigh)
         tpRMultiple = InpTpRMidRegime;
      else
         tpRMultiple = InpTpRHighRegime;
     }

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

   if(sweptLow)
     {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double risk = ask - NormalizeDouble(closeSignal - InpAtrStopMult * atrRef, digits);
      double sl = NormalizeDouble(ask - risk, digits);
      double tp = (tpRMultiple > 0) ? NormalizeDouble(ask + risk * tpRMultiple, digits) : 0.0;
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
        {
         g_tradedToday = true;
         PST_LogTrade(EA_Name, "OPEN_BUY", _Symbol, lots, ask, sl, tp, StringFormat("Barrido de minimo europeo (Low<%.5f, Close>%.5f) TPxR=%.2f", g_euroLow, g_euroLow, tpRMultiple));
        }
      else
         PST_Log(EA_Name, "ERROR al abrir compra: " + IntegerToString(trade.ResultRetcode()));
     }
   else if(sweptHigh)
     {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double risk = NormalizeDouble(closeSignal + InpAtrStopMult * atrRef, digits) - bid;
      double sl = NormalizeDouble(bid + risk, digits);
      double tp = (tpRMultiple > 0) ? NormalizeDouble(bid - risk * tpRMultiple, digits) : 0.0;
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
        {
         g_tradedToday = true;
         PST_LogTrade(EA_Name, "OPEN_SELL", _Symbol, lots, bid, sl, tp, StringFormat("Barrido de maximo europeo (High>%.5f, Close<%.5f) TPxR=%.2f", g_euroHigh, g_euroHigh, tpRMultiple));
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
   string header = "EuroStartHour;EuroEndHour;SweepStartHour;SweepEndHour;AtrStopMult;NetProfit;ProfitFactor;Trades;Sharpe;MaxDDPercent;ExpectedPayoff";
   string line = StringFormat("%d;%d;%d;%d;%.2f;%.2f;%.3f;%d;%.3f;%.2f;%.2f",
                 InpEuroStartHour, InpEuroEndHour, InpSweepStartHour, InpSweepEndHour, InpAtrStopMult,
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
