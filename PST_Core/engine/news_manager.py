import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
import logging
import threading
import time

logger = logging.getLogger("PST_News_Manager")

class NewsManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(NewsManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.events = []
        self.last_update = None
        self._initialized = True
        self._shutdown = False
        self._thread = None
        self.start_auto_update()

    def start_auto_update(self):
        def update_loop():
            # Delay inicial para dejar que la red se estabilice si arranca con el sistema
            time.sleep(2) 
            while not self._shutdown:
                try:
                    self.fetch_news()
                except Exception as e:
                    logger.error(f"❌ Error fetching news: {e}")
                
                # Esperar 1 hora antes de la próxima actualización
                for _ in range(3600):
                    if self._shutdown: break
                    time.sleep(1)

        self._thread = threading.Thread(target=update_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._shutdown = True
        if self._thread:
            self._thread.join(timeout=2)

    def fetch_news(self):
        # Usamos el feed de faireconomy que es más amigable para scripts
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
        try:
            headers = {"User-Agent": "PailsSentinelTrade/1.0"}
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                new_events = []
                for event in root.findall('event'):
                    try:
                        title = ""
                        title_node = event.find('title')
                        if title_node is not None: title = title_node.text or ""
                        
                        country = ""
                        country_node = event.find('country')
                        if country_node is not None: country = country_node.text or ""
                        
                        date_str = ""
                        date_node = event.find('date')
                        if date_node is not None: date_str = date_node.text or ""
                        
                        time_str = ""
                        time_node = event.find('time')
                        if time_node is not None: time_str = time_node.text or ""
                        
                        impact = ""
                        impact_node = event.find('impact')
                        if impact_node is not None: impact = impact_node.text or ""

                        if not date_str: continue
                        
                        event_time_utc = None
                        if time_str and ":" in time_str:
                            dt_str = f"{date_str} {time_str}"
                            # FairEconomy XML suele venir en hora de servidor que mapea bien a UTC en este feed 
                            # o requiere ajuste. Para ff_calendar_thisweek.xml se suele considerar UTC.
                            try:
                                dt_raw = datetime.strptime(dt_str, "%m-%d-%Y %I:%M%p")
                                event_time_utc = dt_raw.replace(tzinfo=timezone.utc)
                            except:
                                dt_raw = datetime.strptime(dt_str, "%m-%d-%Y %H:%M%p")
                                event_time_utc = dt_raw.replace(tzinfo=timezone.utc)
                        elif time_str and "All Day" in time_str:
                            dt_raw = datetime.strptime(date_str, "%m-%d-%Y")
                            event_time_utc = dt_raw.replace(tzinfo=timezone.utc)
                        
                        if event_time_utc:
                            new_events.append({
                                "title": title,
                                "country": country,
                                "time_utc": event_time_utc,
                                "impact": impact
                            })
                    except Exception as ex:
                        continue
                
                self.events = new_events
                self.last_update = datetime.now()
                logger.info(f"✅ NewsManager: {len(self.events)} eventos cargados.")
            else:
                logger.error(f"❌ NewsManager HTTP Error: {response.status_code}")
        except Exception as e:
            logger.error(f"❌ Error en fetch_news: {e}")

    def get_upcoming_high_impact(self, limit=5):
        now = datetime.now(timezone.utc)
        high = [e for e in self.events if e['impact'] == 'High' and e['time_utc'] > now]
        high.sort(key=lambda x: x['time_utc'])
        return high[:limit]

    def get_active_blocks(self):
        """Retorna eventos de alto impacto que están bloqueando el trading ahora."""
        now = datetime.now(timezone.utc)
        blocks = []
        for event in self.events:
            if event['impact'] == 'High':
                event_time = event['time_utc']
                # Bloqueo: 30 min antes y 30 min después
                if (event_time - timedelta(minutes=30)) <= now <= (event_time + timedelta(minutes=30)):
                    blocks.append(event)
        return blocks

    def is_news_blocked(self, symbol: str) -> tuple[bool, dict | None]:
        """
        Determina si un símbolo específico está bloqueado por noticias inminentes.
        """
        active = self.get_active_blocks()
        if not active:
            return False, None
            
        relevant = ["ALL"]
        sym = symbol.upper()
        
        if len(sym) == 6:
            relevant.extend([sym[:3], sym[3:]])
        elif "JPY" in sym: relevant.append("JPY")
        elif "GBP" in sym: relevant.append("GBP")
        elif "EUR" in sym: relevant.append("EUR")
        elif any(x in sym for x in ["USD", "US30", "NAS100", "SP500", "BTC", "ETH", "SOL"]):
            relevant.append("USD")
        elif "AUD" in sym: relevant.append("AUD")
        elif "NZD" in sym: relevant.append("NZD")
        elif "CAD" in sym: relevant.append("CAD")
        elif "CHF" in sym: relevant.append("CHF")
            
        for event in active:
            if event['country'] in relevant:
                return True, event
                
        return False, None

# Instancia única para el sistema
news_guard = NewsManager()
