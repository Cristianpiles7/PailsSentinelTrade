//+------------------------------------------------------------------+
//|                                                  PST_Gestor.mq5  |
//| Gestor de despliegues EA (rama feature/eas-mt5).                 |
//|                                                                  |
//| Se carga UNA vez en un grafico cualquiera del terminal. Cada     |
//| InpIntervaloSeg lee Common\Files\<InpPstControlDir>\despliegue.csv|
//| (lo escribe la app) y reconcilia los graficos:                   |
//|   instalar=1 y no hay grafico con ese EA -> abre grafico         |
//|                 (simbolo, TF) y aplica su plantilla              |
//|                 MQL5\Profiles\Templates\PST_EA\<id>.tpl          |
//|   instalar=0 y hay grafico con ese EA   -> cierra ese grafico    |
//| Los graficos con EAs que no estan en el fichero NO se tocan.     |
//| Escribe gestor_estado.csv con lo que ve. No opera nunca.         |
//|                                                                  |
//| Formato de despliegue.csv (';', con cabecera):                   |
//|    id;simbolo;tf;instalar                                        |
//|    PST_H25_VwapAdx_UK100_H1;UK100.cash;H1;1                      |
//+------------------------------------------------------------------+
#property copyright "PailsSentinelTrade"
#property version   "1.00"
#property strict

input string InpPstControlDir = "PST_EA";
input int    InpIntervaloSeg  = 10;

#define MAX_DESP 128

string   g_id[MAX_DESP];
string   g_sym[MAX_DESP];
string   g_tf[MAX_DESP];
int      g_instalar[MAX_DESP];
int      g_n = 0;

// Graficos abiertos por el gestor para un id (para reintentar la plantilla en el mismo grafico
// en vez de abrir uno nuevo en cada ciclo si falla).
string   g_abiertoId[MAX_DESP];
long     g_abiertoChart[MAX_DESP];
int      g_abiertoIntentos[MAX_DESP];
int      g_nAbiertos = 0;

string   g_error[MAX_DESP];

//+------------------------------------------------------------------+
ENUM_TIMEFRAMES TfDeTexto(const string tf)
  {
   if(tf == "M1")  return PERIOD_M1;
   if(tf == "M5")  return PERIOD_M5;
   if(tf == "M15") return PERIOD_M15;
   if(tf == "M30") return PERIOD_M30;
   if(tf == "H1")  return PERIOD_H1;
   if(tf == "H4")  return PERIOD_H4;
   if(tf == "D1")  return PERIOD_D1;
   if(tf == "W1")  return PERIOD_W1;
   return PERIOD_CURRENT;
  }

//+------------------------------------------------------------------+
bool LeerDespliegues()
  {
   int fh = FileOpen(InpPstControlDir + "\\despliegue.csv",
                     FILE_READ|FILE_CSV|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ|FILE_SHARE_WRITE, ';');
   if(fh == INVALID_HANDLE)
      return false;
   g_n = 0;
   bool cabecera = true;
   while(!FileIsEnding(fh) && g_n < MAX_DESP)
     {
      string id = FileReadString(fh);
      if(FileIsEnding(fh) && id == "")
         break;
      string sym = FileReadString(fh);
      string tf  = FileReadString(fh);
      string ins = FileReadString(fh);
      while(!FileIsLineEnding(fh) && !FileIsEnding(fh))
         FileReadString(fh);
      if(cabecera) { cabecera = false; continue; }
      if(id == "")
         continue;
      g_id[g_n] = id;
      g_sym[g_n] = sym;
      g_tf[g_n] = tf;
      g_instalar[g_n] = (int)StringToInteger(ins);
      g_error[g_n] = "";
      g_n++;
     }
   FileClose(fh);
   return true;
  }

//+------------------------------------------------------------------+
// Grafico que tiene cargado el EA `id`, o 0.
long ChartConEa(const string id)
  {
   long c = ChartFirst();
   while(c >= 0)
     {
      if(ChartGetString(c, CHART_EXPERT_NAME) == id)
         return c;
      c = ChartNext(c);
     }
   return 0;
  }

bool ChartExiste(const long cid)
  {
   long c = ChartFirst();
   while(c >= 0)
     {
      if(c == cid)
         return true;
      c = ChartNext(c);
     }
   return false;
  }

int IndiceAbierto(const string id)
  {
   for(int i = 0; i < g_nAbiertos; i++)
      if(g_abiertoId[i] == id)
         return i;
   return -1;
  }

//+------------------------------------------------------------------+
void Instalar(const int k)
  {
   string id = g_id[k];
   ENUM_TIMEFRAMES tf = TfDeTexto(g_tf[k]);
   if(tf == PERIOD_CURRENT)
     {
      g_error[k] = "timeframe desconocido: " + g_tf[k];
      return;
     }
   if(!SymbolSelect(g_sym[k], true))
     {
      g_error[k] = "simbolo no disponible: " + g_sym[k];
      return;
     }
   string plantilla = "\\Profiles\\Templates\\PST_EA\\" + id + ".tpl";

   int a = IndiceAbierto(id);
   long cid = 0;
   if(a >= 0 && ChartExiste(g_abiertoChart[a]))
     {
      cid = g_abiertoChart[a];                       // ya lo abrimos: reintentar ahi
      if(g_abiertoIntentos[a] >= 5)
        {
         g_error[k] = "plantilla rechazada 5 veces; revisar " + plantilla + " y el log del terminal";
         return;
        }
     }
   else
     {
      ResetLastError();
      cid = ChartOpen(g_sym[k], tf);
      if(cid == 0)
        {
         g_error[k] = "ChartOpen fallo, err=" + IntegerToString(GetLastError());
         return;
        }
      if(a < 0 && g_nAbiertos < MAX_DESP)
        {
         a = g_nAbiertos++;
         g_abiertoId[a] = id;
        }
      if(a >= 0)
        {
         g_abiertoChart[a] = cid;
         g_abiertoIntentos[a] = 0;
        }
     }
   ResetLastError();
   if(!ChartApplyTemplate(cid, plantilla))
      g_error[k] = "ChartApplyTemplate fallo, err=" + IntegerToString(GetLastError());
   if(a >= 0)
      g_abiertoIntentos[a]++;
   PrintFormat("[PST_Gestor] instalar %s en %s %s (grafico %I64d) %s", id, g_sym[k], g_tf[k], cid,
               g_error[k] == "" ? "ok" : g_error[k]);
  }

//+------------------------------------------------------------------+
void EscribirEstado()
  {
   int fh = FileOpen(InpPstControlDir + "\\gestor_estado.csv",
                     FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ);
   if(fh == INVALID_HANDLE)
      return;
   FileWriteString(fh, "id;instalar;grafico;cargado;error\r\n");
   for(int k = 0; k < g_n; k++)
     {
      long c = ChartConEa(g_id[k]);
      FileWriteString(fh, StringFormat("%s;%d;%I64d;%d;%s\r\n", g_id[k], g_instalar[k], c, c != 0 ? 1 : 0, g_error[k]));
     }
   FileWriteString(fh, StringFormat("#gestor;%s;%I64d;%s;%s\r\n",
                   TimeToString(TimeLocal(), TIME_DATE|TIME_SECONDS),
                   AccountInfoInteger(ACCOUNT_LOGIN),
                   TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) ? "algo_on" : "algo_off",
                   TerminalInfoString(TERMINAL_DATA_PATH)));
   FileClose(fh);
  }

//+------------------------------------------------------------------+
void Ciclo()
  {
   if(!LeerDespliegues())
     {
      g_n = 0;
      EscribirEstado();
      return;
     }
   for(int k = 0; k < g_n; k++)
     {
      long c = ChartConEa(g_id[k]);
      if(g_instalar[k] == 1 && c == 0)
         Instalar(k);
      else if(g_instalar[k] == 0 && c != 0 && c != ChartID())
        {
         bool ok = ChartClose(c);
         PrintFormat("[PST_Gestor] retirar %s (grafico %I64d): %s", g_id[k], c, ok ? "ok" : "fallo");
         int a = IndiceAbierto(g_id[k]);
         if(a >= 0)
            g_abiertoIntentos[a] = 0;
        }
      else if(c != 0)
        {
         int a = IndiceAbierto(g_id[k]);
         if(a >= 0)
            g_abiertoIntentos[a] = 0;              // cargado: se reinicia el contador
        }
     }
   EscribirEstado();
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   if(MQLInfoInteger(MQL_TESTER))
      return(INIT_FAILED);                         // el gestor no tiene sentido en el tester
   FolderCreate(InpPstControlDir, FILE_COMMON);
   EventSetTimer(MathMax(InpIntervaloSeg, 3));
   PrintFormat("[PST_Gestor] arrancado. Carpeta de control: Common\\Files\\%s", InpPstControlDir);
   Ciclo();
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

void OnTimer()
  {
   Ciclo();
  }

void OnTick() {}
//+------------------------------------------------------------------+
