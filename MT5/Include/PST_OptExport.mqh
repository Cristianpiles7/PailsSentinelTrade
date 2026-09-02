//+------------------------------------------------------------------+
//|                                              PST_OptExport.mqh   |
//|                              PailsSentinelMT5TradeV2 — Fase 5    |
//|                                                                  |
//| MT5 no exporta un listado de todas las pasadas de una            |
//| optimizacion via el Report= del .ini (esa limitacion solo        |
//| exporta para tests individuales, Optimization=0). Este include   |
//| se usa desde OnTester() de cada EA para que CADA pasada (la corra|
//| el agente local que la corra) anote su propia fila de resultado  |
//| en un CSV compartido en Common\Files, que sí es visible fuera    |
//| del sandbox de cada agente de test.                              |
//+------------------------------------------------------------------+
#property copyright "PailsSentinelMT5Trade"
#property strict

//+------------------------------------------------------------------+
//| Añade una fila a un CSV en Common\Files\PST_V2_OptResults\<name>.|
//| Crea el archivo y escribe la cabecera si todavia no existe.      |
//| Reintenta unas pocas veces si el archivo esta bloqueado por otro |
//| agente local escribiendo al mismo tiempo (varios agentes en      |
//| paralelo comparten el mismo archivo de resultados).              |
//+------------------------------------------------------------------+
void PST_AppendOptCsv(const string fileName, const string header, const string line)
  {
   int fh = INVALID_HANDLE;
   for(int attempt = 0; attempt < 20 && fh == INVALID_HANDLE; attempt++)
     {
      if(attempt > 0)
         Sleep(50);
      fh = FileOpen(fileName, FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE);
     }
   if(fh == INVALID_HANDLE)
     {
      PrintFormat("PST_OptExport: no se pudo abrir %s tras varios intentos (err=%d).", fileName, GetLastError());
      return;
     }

   ulong size = FileSize(fh);
   FileSeek(fh, 0, SEEK_END);
   if(size == 0)
      FileWriteString(fh, header + "\r\n");
   FileWriteString(fh, line + "\r\n");
   FileFlush(fh);
   FileClose(fh);
  }
//+------------------------------------------------------------------+
