//+------------------------------------------------------------------+
//|                                                PST_Control.mqh   |
//|                     PST — control de EAs desde la app (rama      |
//|                     feature/eas-mt5, ver MT5/DESARROLLO_EAS.md)  |
//|                                                                  |
//| Cada EA incluye este fichero para:                               |
//|  1. Saber si puede ABRIR operaciones (interruptor de la app) y   |
//|     con que multiplicador de riesgo. Lo lee de                   |
//|     Common\Files\<InpPstControlDir>\control.csv cada 5 s.         |
//|  2. Cerrar sus posiciones cuando la app lo pide (token).         |
//|  3. Escribir su latido en Common\Files\PST_EA\estado_<magic>.csv |
//|  4. Buscar/cerrar/modificar SOLO su posicion (por magic). En     |
//|     cuenta hedging, PositionSelect(_Symbol) devuelve la primera  |
//|     posicion del simbolo, que puede ser de otro EA o de la flota.|
//|                                                                  |
//| Formato de control.csv (separador ';', con cabecera):            |
//|    magic;activo;mult_riesgo;cerrar_token                         |
//|    0;1;1.0;0          <- fila global: activo=0 para TODOS los EAs |
//|    101100;1;1.0;0                                                |
//|                                                                  |
//| Reglas:                                                          |
//|  - En el Strategy Tester no se lee nada: siempre activo, mult=1. |
//|  - En vivo, si no hay control.csv o el EA no tiene fila: NO abre |
//|    (falla cerrado). Las posiciones abiertas siguen gestionandose.|
//|  - Desactivar = no abrir nuevas. Cerrar es una orden aparte.     |
//+------------------------------------------------------------------+
#property strict

#include <Trade\Trade.mqh>

#define PST_CONTROL_SEG     5

// Carpeta dentro de Common\Files. Common es compartido por TODOS los terminales del usuario (vivo y
// tester portable): el entorno de pruebas de la app usa "PST_EA_DEV" para no tocar los EAs reales.
input group    "--- Control desde la app (PST_Control.mqh) ---"
input string   InpPstControlDir = "PST_EA";

string          g_ctlEa        = "";
long            g_ctlMagic     = 0;
string          g_ctlVersion   = "";
bool            g_ctlActivo    = false;
bool            g_ctlGlobal    = false;
double          g_ctlMult      = 1.0;
long            g_ctlToken     = 0;
bool            g_ctlLeido     = false;     // hay control.csv con fila para este magic
datetime        g_ctlUltLect   = 0;
bool            g_ctlUltEstado = false;     // para loguear solo los cambios
string          g_ctlUltError  = "";

bool PST_Control_EnTester()
  {
   return (bool)MQLInfoInteger(MQL_TESTER) || (bool)MQLInfoInteger(MQL_OPTIMIZATION);
  }

//+------------------------------------------------------------------+
//| Posiciones propias (por simbolo + magic)                          |
//+------------------------------------------------------------------+
// Deja seleccionada la posicion propia (PositionGet* funciona despues). false si no hay.
bool PST_SelectMyPosition(const string symbol, const long magic)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(PositionGetString(POSITION_SYMBOL) == symbol && PositionGetInteger(POSITION_MAGIC) == magic)
         return true;
     }
   return false;
  }

int PST_CountMyPositions(const string symbol, const long magic)
  {
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket != 0 && PositionGetString(POSITION_SYMBOL) == symbol && PositionGetInteger(POSITION_MAGIC) == magic)
         n++;
     }
   return n;
  }

bool PST_CloseMyPosition(CTrade &t, const string symbol, const long magic)
  {
   if(!PST_SelectMyPosition(symbol, magic))
      return false;
   return t.PositionClose((ulong)PositionGetInteger(POSITION_TICKET));
  }

bool PST_ModifyMyPosition(CTrade &t, const string symbol, const long magic, const double sl, const double tp)
  {
   if(!PST_SelectMyPosition(symbol, magic))
      return false;
   return t.PositionModify((ulong)PositionGetInteger(POSITION_TICKET), sl, tp);
  }

//+------------------------------------------------------------------+
//| Lectura de control.csv                                            |
//+------------------------------------------------------------------+
void PST_Control_Leer()
  {
   g_ctlUltLect = TimeLocal();
   int fh = FileOpen(InpPstControlDir + "\\control.csv", FILE_READ|FILE_CSV|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE, ';');
   if(fh == INVALID_HANDLE)
     {
      g_ctlLeido = false;
      g_ctlUltError = "sin control.csv";
      return;
     }
   bool encontrado = false;
   bool global = true;
   bool cabecera = true;
   while(!FileIsEnding(fh))
     {
      string sMagic = FileReadString(fh);
      if(FileIsEnding(fh) && sMagic == "")
         break;
      string sActivo = FileReadString(fh);
      string sMult   = FileReadString(fh);
      string sToken  = FileReadString(fh);
      while(!FileIsLineEnding(fh) && !FileIsEnding(fh))
         FileReadString(fh);                       // columnas extra: se ignoran
      if(cabecera) { cabecera = false; continue; }
      long m = StringToInteger(sMagic);
      if(m == 0)
         global = (StringToInteger(sActivo) == 1);
      else if(m == g_ctlMagic)
        {
         encontrado = true;
         g_ctlActivo = (StringToInteger(sActivo) == 1);
         double mult = StringToDouble(sMult);
         g_ctlMult = (mult >= 0 && mult <= 2.0) ? mult : 1.0;   // tope de seguridad: nunca mas del doble
         long tok = StringToInteger(sToken);
         if(tok != 0 && tok != g_ctlToken)
            g_ctlToken = tok;
        }
     }
   FileClose(fh);
   g_ctlGlobal = global;
   g_ctlLeido = encontrado;
   g_ctlUltError = encontrado ? "" : "magic sin fila en control.csv";
  }

//+------------------------------------------------------------------+
//| Cierre pedido por la app: se ejecuta una vez por token            |
//+------------------------------------------------------------------+
void PST_Control_AtenderCierre(CTrade &t)
  {
   if(g_ctlToken == 0)
      return;
   string gv = StringFormat("PST_EA_cerrar_%I64d", g_ctlMagic);
   if(GlobalVariableCheck(gv) && (long)GlobalVariableGet(gv) == g_ctlToken)
      return;                                      // ya atendido
   int cerradas = 0;
   while(PST_SelectMyPosition(_Symbol, g_ctlMagic))
     {
      if(!t.PositionClose((ulong)PositionGetInteger(POSITION_TICKET)))
        {
         g_ctlUltError = "cierre pedido por la app fallido: " + IntegerToString(t.ResultRetcode());
         return;                                   // se reintenta en el siguiente temporizador
        }
      cerradas++;
     }
   GlobalVariableSet(gv, (double)g_ctlToken);
   PrintFormat("[PST_Control] %s magic=%I64d: cierre pedido por la app, %d posiciones cerradas.", g_ctlEa, g_ctlMagic, cerradas);
  }

//+------------------------------------------------------------------+
//| Latido                                                            |
//+------------------------------------------------------------------+
void PST_Control_Latido(const string estado)
  {
   string nombre = StringFormat("%s\\estado_%I64d.csv", InpPstControlDir, g_ctlMagic);
   int fh = FileOpen(nombre, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ);
   if(fh == INVALID_HANDLE)
      return;
   double flotante = 0;
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket != 0 && PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == g_ctlMagic)
        {
         n++;
         flotante += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
        }
     }
   FileWriteString(fh, "clave;valor\r\n");
   FileWriteString(fh, "estado;" + estado + "\r\n");
   FileWriteString(fh, "ea;" + g_ctlEa + "\r\n");
   FileWriteString(fh, "version;" + g_ctlVersion + "\r\n");
   FileWriteString(fh, StringFormat("magic;%I64d\r\n", g_ctlMagic));
   FileWriteString(fh, "simbolo;" + _Symbol + "\r\n");
   FileWriteString(fh, "tf;" + EnumToString((ENUM_TIMEFRAMES)_Period) + "\r\n");
   FileWriteString(fh, StringFormat("cuenta;%I64d\r\n", AccountInfoInteger(ACCOUNT_LOGIN)));
   FileWriteString(fh, "puede_abrir;" + (PST_Control_PuedeAbrirSinLog() ? "1" : "0") + "\r\n");
   FileWriteString(fh, "activo_app;" + (g_ctlActivo ? "1" : "0") + "\r\n");
   FileWriteString(fh, "activo_global;" + (g_ctlGlobal ? "1" : "0") + "\r\n");
   FileWriteString(fh, StringFormat("mult_riesgo;%.2f\r\n", g_ctlMult));
   FileWriteString(fh, "algo_trading;" + (TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) && MQLInfoInteger(MQL_TRADE_ALLOWED) ? "1" : "0") + "\r\n");
   FileWriteString(fh, StringFormat("posiciones;%d\r\n", n));
   FileWriteString(fh, StringFormat("flotante;%.2f\r\n", flotante));
   FileWriteString(fh, StringFormat("token_cerrar;%I64d\r\n", g_ctlToken));
   FileWriteString(fh, "hora_local;" + TimeToString(TimeLocal(), TIME_DATE|TIME_SECONDS) + "\r\n");
   FileWriteString(fh, "hora_servidor;" + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "\r\n");
   FileWriteString(fh, "error;" + g_ctlUltError + "\r\n");
   FileClose(fh);
  }

//+------------------------------------------------------------------+
//| API para los EAs                                                  |
//+------------------------------------------------------------------+
bool PST_Control_PuedeAbrirSinLog()
  {
   if(PST_Control_EnTester())
      return true;
   return g_ctlLeido && g_ctlGlobal && g_ctlActivo && g_ctlMult > 0;
  }

// Llamar justo antes de abrir. Loguea solo cuando cambia el estado.
bool PST_Control_PuedeAbrir()
  {
   if(PST_Control_EnTester())
      return true;
   if(TimeLocal() - g_ctlUltLect >= PST_CONTROL_SEG)
      PST_Control_Leer();
   bool puede = PST_Control_PuedeAbrirSinLog();
   if(puede != g_ctlUltEstado)
     {
      g_ctlUltEstado = puede;
      PrintFormat("[PST_Control] %s magic=%I64d: %s (%s)", g_ctlEa, g_ctlMagic,
                  puede ? "ACTIVO, puede abrir" : "PAUSADO, no abre operaciones nuevas",
                  g_ctlUltError != "" ? g_ctlUltError : (!g_ctlGlobal ? "interruptor global apagado" : (!g_ctlActivo ? "desactivado en la app" : "ok")));
     }
   return puede;
  }

// Riesgo por operacion con el multiplicador de la app (en tester, el base).
double PST_RiesgoEfectivo(const double riesgoBase)
  {
   if(PST_Control_EnTester())
      return riesgoBase;
   return riesgoBase * g_ctlMult;
  }

// En OnInit. Comprueba que el EA esta en SU simbolo y SU timeframe (un fichero = un despliegue).
int PST_Control_Init(const string eaName, const long magic, const string version,
                     const string simboloEsperado, const ENUM_TIMEFRAMES tfEsperado)
  {
   g_ctlEa = eaName;
   g_ctlMagic = magic;
   g_ctlVersion = version;
   if(_Symbol != simboloEsperado || (ENUM_TIMEFRAMES)_Period != tfEsperado)
     {
      PrintFormat("[PST_Control] %s es para %s %s y esta cargado en %s %s. No arranca.",
                  eaName, simboloEsperado, EnumToString(tfEsperado), _Symbol, EnumToString((ENUM_TIMEFRAMES)_Period));
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(PST_Control_EnTester())
      return(INIT_SUCCEEDED);
   FolderCreate(InpPstControlDir, FILE_COMMON);
   PST_Control_Leer();
   g_ctlUltEstado = !PST_Control_PuedeAbrirSinLog();   // fuerza el primer log
   PST_Control_PuedeAbrir();
   EventSetTimer(PST_CONTROL_SEG);
   PST_Control_Latido("iniciado");
   return(INIT_SUCCEEDED);
  }

// En OnTimer.
void PST_Control_OnTimer(CTrade &t)
  {
   if(PST_Control_EnTester())
      return;
   PST_Control_Leer();
   PST_Control_PuedeAbrir();
   PST_Control_AtenderCierre(t);
   PST_Control_Latido("vivo");
  }

// En OnDeinit.
void PST_Control_Deinit(const int reason)
  {
   if(PST_Control_EnTester())
      return;
   EventKillTimer();
   PST_Control_Latido("parado:" + IntegerToString(reason));
  }
//+------------------------------------------------------------------+
