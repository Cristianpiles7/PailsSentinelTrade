// EA de prueba: NO opera. Solo escribe un latido para demostrar que esta vivo en un grafico.
#property strict
int OnInit(){ EventSetTimer(2); Escribir("init"); return(INIT_SUCCEEDED); }
void OnDeinit(const int r){ EventKillTimer(); Escribir("deinit "+IntegerToString(r)); }
void OnTimer(){ Escribir("latido"); }
void OnTick(){}
void Escribir(string s){
   int h=FileOpen("pst_noop_latido.txt",FILE_WRITE|FILE_READ|FILE_TXT|FILE_ANSI);
   if(h==INVALID_HANDLE) return;
   FileSeek(h,0,SEEK_END);
   FileWriteString(h,TimeToString(TimeLocal(),TIME_SECONDS)+" "+_Symbol+" "+s+"\r\n");
   FileClose(h);
}
