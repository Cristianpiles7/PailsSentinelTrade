import requests
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("PST-News")

class NewsManager:
    def __init__(self):
        self.news_data = []
        self.last_fetch = None
        # URL de calendario económico público (ForexFactory JSON format or similar)
        self.news_url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        import os
        self.cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news_cache.json")

    def fetch_news(self):
        """Descarga el calendario de la semana con caché en archivo para evitar 429."""
        import os
        import json
        try:
            # 1. Intentar cargar desde caché en archivo primero
            if os.path.exists(self.cache_path):
                mtime = os.path.getmtime(self.cache_path)
                # Si el archivo tiene menos de 1 hora, lo usamos
                if (datetime.now().timestamp() - mtime) < 3600:
                    with open(self.cache_path, "r") as f:
                        self.news_data = json.load(f)
                    self.last_fetch = datetime.fromtimestamp(mtime)
                    return

            # 2. Si no hay caché o es vieja, descargar
            response = requests.get(self.news_url, timeout=10)
            if response.status_code == 200:
                self.news_data = response.json()
                self.last_fetch = datetime.now()
                # Guardar en caché para otros procesos
                with open(self.cache_path, "w") as f:
                    json.dump(self.news_data, f)
                logger.info(f"📥 Calendario económico actualizado ({len(self.news_data)} eventos).")
            elif response.status_code == 429:
                logger.warning("⚠️ Rate limit (429) alcanzado en noticias. Usando caché vieja si existe.")
                if os.path.exists(self.cache_path):
                    with open(self.cache_path, "r") as f:
                        self.news_data = json.load(f)
            else:
                logger.error(f"❌ Error al descargar noticias: {response.status_code}")
        except Exception as e:
            logger.error(f"❌ Excepción consultando noticias: {e}")

    def is_news_near(self, symbol: str, window_minutes: int = 30):
        """
        Comprueba si hay una noticia de ALTO impacto cerca para la divisa del símbolo.
        """
        if not self.news_data:
            self.fetch_news()
            if not self.news_data: return False

        now = datetime.utcnow() # ForexFactory usa UTC
        
        # Mapeo de Símbolo a Divisa
        currency = self._get_currency_from_symbol(symbol)
        
        for event in self.news_data:
            # Solo nos importan noticias de ALTO impacto (High)
            if event.get('impact') != 'High':
                continue
                
            # La noticia debe ser de la divisa del símbolo (o USD que afecta a todo)
            event_curr = event.get('country')
            if event_curr != currency and event_curr != 'USD':
                continue
            
            try:
                # El formato suele ser "MM-DD-YYYY HH:MM:SS"
                event_time_str = event.get('date')
                # Algunos JSON vienen con formato ISO8601
                if 'T' in event_time_str:
                    event_time = datetime.fromisoformat(event_time_str.replace('Z', '+00:00')).replace(tzinfo=None)
                else:
                    # Ajustar según el formato real del feed
                    # FF suele ser: "2026-01-30T13:30:00-05:00"
                    event_time = datetime.fromisoformat(event_time_str).replace(tzinfo=None)

                diff = abs((now - event_time).total_seconds() / 60)
                
                if diff <= window_minutes:
                    logger.warning(f"⚠️ [NEWS] Bloqueo: {event.get('title')} ({event_curr}) en {diff:.1f} min.")
                    return True
            except:
                continue
                
        return False

    def _get_currency_from_symbol(self, symbol: str):
        """Extrae la divisa base de un símbolo."""
        if "EUR" in symbol: return "EUR"
        if "GBP" in symbol: return "GBP"
        if "JPY" in symbol: return "JPY"
        if "BTC" in symbol: return "BTC"
        if "ETH" in symbol: return "ETH"
        if "US500" in symbol or "NAS100" in symbol or "XAU" in symbol: return "USD"
        return "USD"

# Singleton
news_mgr = NewsManager()
