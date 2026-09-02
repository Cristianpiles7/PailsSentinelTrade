//+------------------------------------------------------------------+
//|                                                  PST_Logger.mqh  |
//|                              PailsSentinelMT5TradeV2 — Fase 3    |
//|                                                                  |
//| Logging estructurado compartido por todos los EAs del proyecto:  |
//| mensajes generales con marca de tiempo/EA y registro de eventos  |
//| de trading (apertura/cierre) en un CSV en Common\Files para      |
//| poder auditar despues fuera del terminal.                        |
//+------------------------------------------------------------------+
#property copyright "PailsSentinelMT5Trade"
#property strict

//+------------------------------------------------------------------+
//| Log de texto simple a la pestaña "Expertos" con prefijo del EA.  |
//+------------------------------------------------------------------+
void PST_Log(string eaName, string message)
  {
   PrintFormat("[%s] %s", eaName, message);
  }

//+------------------------------------------------------------------+
//| Registra un evento de trading (apertura, cierre, ajuste de SL,   |
//| rechazo por riesgo/margen, etc.) en un CSV compartido en         |
//| Common\Files\PST_trade_log.csv, para poder revisarlo fuera de    |
//| MT5. Crea el archivo con cabecera si no existe todavia.          |
//+------------------------------------------------------------------+
void PST_LogTrade(string eaName, string action, string symbol,
                   double lots, double price, double sl, double tp,
                   string comment = "")
  {
   string fname = "PST_trade_log.csv";
   bool exists = FileIsExist(fname, FILE_COMMON);

   int fh = FileOpen(fname, FILE_READ|FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON, ';');
   if(fh == INVALID_HANDLE)
     {
      PST_Log(eaName, "ERROR: no se pudo abrir " + fname + " para el log de trading.");
      return;
     }

   FileSeek(fh, 0, SEEK_END);

   if(!exists)
      FileWrite(fh, "DateTime", "EA", "Accion", "Symbol", "Lots", "Price", "SL", "TP", "Comentario");

   int dig = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   FileWrite(fh,
             TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS),
             eaName,
             action,
             symbol,
             DoubleToString(lots, 2),
             DoubleToString(price, dig),
             DoubleToString(sl, dig),
             DoubleToString(tp, dig),
             comment);

   FileClose(fh);

   PST_Log(eaName, StringFormat("%s %s %.2f lotes @ %s (SL=%s TP=%s) %s",
           action, symbol, lots, DoubleToString(price, dig),
           DoubleToString(sl, dig), DoubleToString(tp, dig), comment));
  }
