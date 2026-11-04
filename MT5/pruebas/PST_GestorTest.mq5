// Prueba del gestor: abre un grafico, le aplica una plantilla con un EA, comprueba y lo quita.
#property strict
void Log(string s){
   int h=FileOpen("pst_gestor_resultado.txt",FILE_WRITE|FILE_READ|FILE_TXT|FILE_ANSI);
   if(h==INVALID_HANDLE) return;
   FileSeek(h,0,SEEK_END); FileWriteString(h,TimeToString(TimeLocal(),TIME_SECONDS)+" "+s+"\r\n"); FileClose(h);
   Print(s);
}
int fase=0; long cid=0;
int OnInit(){ EventSetTimer(5); Log("gestor arrancado en "+_Symbol); return(INIT_SUCCEEDED); }
void OnTimer(){
   fase++;
   if(fase==1){
      cid=ChartOpen("EURUSD",PERIOD_H1);
      Log("ChartOpen -> "+IntegerToString(cid)+" err="+IntegerToString(GetLastError()));
      ResetLastError();
      bool ok=ChartApplyTemplate(cid,"\Profiles\Templates\pst_noop.tpl");
      Log("ChartApplyTemplate -> "+(ok?"true":"false")+" err="+IntegerToString(GetLastError()));
   }
   if(fase==3){
      Log("EA en el grafico: '"+ChartGetString(cid,CHART_EXPERT_NAME)+"'");
   }
   if(fase==4){
      bool c=ChartClose(cid);
      Log("ChartClose -> "+(c?"true":"false"));
   }
   if(fase==5){ Log("fin de la prueba"); TerminalClose(0); }
}
void OnDeinit(const int r){ EventKillTimer(); }
void OnTick(){}
