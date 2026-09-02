//+------------------------------------------------------------------+
//|                                            PST_RiskManager.mqh   |
//|                              PailsSentinelMT5TradeV2 — Fase 3    |
//|                                                                  |
//| Funciones compartidas de gestion de riesgo para todos los EAs    |
//| del proyecto: calculo de lotaje por % de riesgo, comprobacion    |
//| de margen, y circuit breaker de drawdown diario.                 |
//+------------------------------------------------------------------+
#property copyright "PailsSentinelMT5Trade"
#property strict

//+------------------------------------------------------------------+
//| Calcula el lotaje para arriesgar exactamente riskPercent % del   |
//| balance de la cuenta, dada una distancia de Stop Loss en puntos. |
//+------------------------------------------------------------------+
double PST_CalcLotByRisk(string symbol, double riskPercent, double slDistancePoints)
  {
   if(slDistancePoints <= 0)
      return 0.0;

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskMoney = balance * riskPercent / 100.0;

   double tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double point     = SymbolInfoDouble(symbol, SYMBOL_POINT);

   if(tickValue <= 0 || tickSize <= 0 || point <= 0)
      return 0.0;

   double moneyPerPoint = tickValue / tickSize * point;
   double lossPerLot = slDistancePoints * moneyPerPoint;

   if(lossPerLot <= 0)
      return 0.0;

   double lots = riskMoney / lossPerLot;

   double lotMin  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double lotMax  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);

   if(lotStep <= 0)
      lotStep = 0.01;

   lots = MathFloor(lots / lotStep) * lotStep;
   lots = MathMax(lotMin, MathMin(lotMax, lots));

   return NormalizeDouble(lots, 2);
  }

//+------------------------------------------------------------------+
//| Comprueba que hay margen libre suficiente para abrir la orden.   |
//+------------------------------------------------------------------+
bool PST_CheckMarginOk(string symbol, ENUM_ORDER_TYPE orderType, double lots)
  {
   double price = (orderType == ORDER_TYPE_BUY)
                  ? SymbolInfoDouble(symbol, SYMBOL_ASK)
                  : SymbolInfoDouble(symbol, SYMBOL_BID);

   double marginRequired = 0.0;
   if(!OrderCalcMargin(orderType, symbol, lots, price, marginRequired))
      return false;

   double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   return marginRequired <= freeMargin;
  }

//+------------------------------------------------------------------+
//| Circuit breaker: true si se ha superado el drawdown diario       |
//| maximo permitido, respecto al balance con el que empezo el dia.  |
//+------------------------------------------------------------------+
bool PST_DailyDrawdownExceeded(double maxDailyDrawdownPercent, double dayStartBalance)
  {
   if(dayStartBalance <= 0)
      return false;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double ddPercent = (dayStartBalance - equity) / dayStartBalance * 100.0;
   return ddPercent >= maxDailyDrawdownPercent;
  }

//+------------------------------------------------------------------+
//| Circuit breakers estilo FTMO (limite duro en importe absoluto de |
//| la divisa de la cuenta, no en %) -- a diferencia de                |
//| PST_DailyDrawdownExceeded (relativo al balance del dia), estos    |
//| reflejan directamente las reglas de una cuenta FTMO: perdida      |
//| diaria maxima e importe de perdida total maxima desde el balance  |
//| inicial del reto (fijo, no se mueve con las ganancias -- una      |
//| cuenta FTMO no se vuelve mas permisiva porque ya se haya ganado   |
//| dinero). Se añaden en Fase 6 tras revisar que solo existia el     |
//| circuit breaker diario, no uno de perdida TOTAL de la cuenta      |
//| (docs/politica-de-riesgo.md).                                     |
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| true si la perdida del dia (respecto al balance con el que        |
//| empezo el dia) supera maxDailyLossAmount, en la divisa de la      |
//| cuenta. Equivalente en importe absoluto a                         |
//| PST_DailyDrawdownExceeded (en %) -- usar el que encaje con como   |
//| esta expresado el limite real de la cuenta (FTMO suele dar un     |
//| importe fijo, p.ej. 500 en la divisa de la cuenta, no un %).      |
//+------------------------------------------------------------------+
bool PST_DailyLossExceeded(double maxDailyLossAmount, double dayStartBalance)
  {
   if(dayStartBalance <= 0)
      return false;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double loss = dayStartBalance - equity;
   return loss >= maxDailyLossAmount;
  }

//+------------------------------------------------------------------+
//| true si la perdida TOTAL de la cuenta (equity actual frente al    |
//| balance INICIAL del reto, no el balance de hoy) supera            |
//| maxTotalLossAmount. initialBalance debe fijarse explicitamente    |
//| como input de cada EA (p.ej. el balance con el que arranco el     |
//| reto FTMO) -- NO inferirse del balance actual al arrancar cada    |
//| EA, porque si un EA arranca despues de que la cuenta ya tenga     |
//| beneficio o perdida acumulados de otros EAs, mediria mal el       |
//| limite real de la cuenta.                                         |
//+------------------------------------------------------------------+
bool PST_TotalLossExceeded(double initialBalance, double maxTotalLossAmount)
  {
   if(initialBalance <= 0)
      return false;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double loss = initialBalance - equity;
   return loss >= maxTotalLossAmount;
  }

//+------------------------------------------------------------------+
//| Devuelve el balance de cierre del dia anterior (aproximado como  |
//| el balance actual al detectar el primer tick de un nuevo dia).   |
//| Los EAs deben llamar a esto una vez al detectar cambio de dia y  |
//| guardar el resultado en una variable propia (dayStartBalance).   |
//+------------------------------------------------------------------+
double PST_CurrentBalance()
  {
   return AccountInfoDouble(ACCOUNT_BALANCE);
  }

//+------------------------------------------------------------------+
//| Gestion de riesgo de CARTERA (Fase 5 del plan, docs/politica-de- |
//| riesgo.md) -- a diferencia de las funciones anteriores (por      |
//| operacion, un solo EA/simbolo), estas suman el riesgo de TODAS   |
//| las posiciones abiertas en la cuenta (de cualquier EA/simbolo),  |
//| para no exceder un limite simultaneo aunque cada EA por separado |
//| respete su propio % de riesgo por operacion.                     |
//+------------------------------------------------------------------+

//+------------------------------------------------------------------+
//| true si 'symbol' aparece en el array 'group' (comparacion exacta,|
//| no distingue mayus/minus -- los nombres de simbolo de MT5 ya son |
//| consistentes en mayusculas).                                     |
//+------------------------------------------------------------------+
bool PST_SymbolInArray(const string symbol, const string &group[])
  {
   for(int i = 0; i < ArraySize(group); i++)
      if(group[i] == symbol)
         return true;
   return false;
  }

//+------------------------------------------------------------------+
//| Parsea un input tipo "AUDCAD,GBPAUD,GBPUSD" (lista separada por  |
//| comas, sin espacios) a un array de strings. Permite declarar el  |
//| grupo de correlacion como input de texto en el EA, sin recompilar|
//| ni hardcodear el grupo en el codigo.                              |
//+------------------------------------------------------------------+
void PST_ParseCsvGroup(const string csv, string &group[])
  {
   if(StringLen(csv) == 0)
     {
      ArrayResize(group, 0);
      return;
     }
   int n = StringSplit(csv, ',', group);
   for(int i = 0; i < n; i++)
     {
      StringTrimLeft(group[i]);
      StringTrimRight(group[i]);
     }
  }

//+------------------------------------------------------------------+
//| Suma el riesgo (dinero en juego hasta el Stop Loss nativo de     |
//| cada posicion) de todas las posiciones abiertas en la cuenta,    |
//| como % del balance. Si 'group' no esta vacio, solo cuenta        |
//| posiciones cuyo simbolo este en 'group' (para el riesgo de un    |
//| grupo correlacionado); si esta vacio, cuenta TODAS las posiciones|
//| (riesgo total de cartera, de cualquier EA o simbolo).            |
//| Posiciones sin SL (no deberia ocurrir, todos los EAs del         |
//| proyecto ponen SL nativo siempre) se ignoran -- no se puede      |
//| cuantificar su riesgo y se prefiere infravalorar el total que    |
//| bloquear todas las operaciones por un caso que no deberia darse. |
//+------------------------------------------------------------------+
double PST_SumOpenRiskPercent(const string &group[])
  {
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(balance <= 0)
      return 0.0;

   double totalRiskMoney = 0.0;
   int total = PositionsTotal();
   for(int i = 0; i < total; i++)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(!PositionSelectByTicket(ticket))
         continue;

      string posSymbol = PositionGetString(POSITION_SYMBOL);
      if(ArraySize(group) > 0 && !PST_SymbolInArray(posSymbol, group))
         continue;

      double sl = PositionGetDouble(POSITION_SL);
      if(sl <= 0)
         continue;

      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double volume = PositionGetDouble(POSITION_VOLUME);
      double tickValue = SymbolInfoDouble(posSymbol, SYMBOL_TRADE_TICK_VALUE);
      double tickSize  = SymbolInfoDouble(posSymbol, SYMBOL_TRADE_TICK_SIZE);
      if(tickValue <= 0 || tickSize <= 0)
         continue;

      double priceDistance = MathAbs(openPrice - sl);
      double riskMoney = priceDistance / tickSize * tickValue * volume;
      totalRiskMoney += riskMoney;
     }

   return totalRiskMoney / balance * 100.0;
  }

//+------------------------------------------------------------------+
//| true si (riesgo ya abierto de TODA la cartera + el riesgo de la  |
//| nueva operacion propuesta) no supera maxPortfolioRiskPercent.    |
//| Llamar ANTES de abrir cualquier posicion, con newTradeRiskPercent|
//| = el mismo % usado para calcular el lotaje de esa operacion      |
//| (PST_CalcLotByRisk). Cubre "riesgo maximo simultaneo si varios   |
//| EAs operan a la vez" (docs/PLAN-Estrategias-MT5.md, Fase 5).     |
//+------------------------------------------------------------------+
bool PST_PortfolioRiskOk(double newTradeRiskPercent, double maxPortfolioRiskPercent)
  {
   string emptyGroup[];
   ArrayResize(emptyGroup, 0);
   return (PST_SumOpenRiskPercent(emptyGroup) + newTradeRiskPercent) <= maxPortfolioRiskPercent;
  }

//+------------------------------------------------------------------+
//| Como PST_PortfolioRiskOk, pero limitado a un grupo de simbolos   |
//| con correlacion positiva medida (p.ej. AUDCAD/GBPAUD/GBPUSD,     |
//| ver docs/politica-de-riesgo.md) -- evita que varias posiciones   |
//| que tienden a moverse juntas concentren mas riesgo real del que  |
//| sugiere cada una por separado. No es necesario llamarla si       |
//| _Symbol no pertenece a ningun grupo definido.                    |
//+------------------------------------------------------------------+
bool PST_GroupRiskOk(const string &group[], double newTradeRiskPercent, double maxGroupRiskPercent)
  {
   return (PST_SumOpenRiskPercent(group) + newTradeRiskPercent) <= maxGroupRiskPercent;
  }
