import aiohttp
import asyncio
import logging

logger = logging.getLogger("PST-Notifications")

class TelegramManager:
    """Gestiona alertas a Telegram para señales de alta probabilidad."""
    def __init__(self, db=None):
        self.db = db
        self.token = None
        self.chat_id = None
        self.api_url = None

    async def _load_config(self):
        """Carga token y chat_id desde la DB."""
        if not self.db: return
        self.token = await self.db.get_config("tg_token", None)
        self.chat_id = await self.db.get_config("tg_chat_id", None)
        if self.token:
            self.api_url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        else:
            self.api_url = None

    async def send_signal_alert(self, symbol, strategy, score, direction, sl, tp, factors):
        """Envía una alerta formateada a Telegram."""
        await self._load_config()
        if not self.api_url or not self.chat_id:
            return

        icon = "🚀" if direction == "BUY" else "short"
        if direction == "SELL": icon = "🔻"
        
        msg = (
            f"{icon} *ALERTA PST: {symbol}*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🎯 *Estrategia:* {strategy}\n"
            f"📊 *Score:* {score}/100\n"
            f"↕️ *Dirección:* {direction}\n\n"
            f"🛡️ *Gestión Sugerida (ATR):*\n"
            f"├ SL: {sl}\n"
            f"└ TP: {tp}\n\n"
            f"🔍 *Factores Clave:*\n"
        )
        
        # Añadir los 3 factores con más score
        for f in factors[:3]:
            msg += f"• {f['k']}: {f['v']}\n"

        payload = {
            "chat_id": self.chat_id,
            "text": msg,
            "parse_mode": "Markdown"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.api_url, json=payload) as resp:
                    if resp.status == 200:
                        logger.info(f"📲 Alerta Telegram enviada para {symbol}")
                    else:
                        logger.error(f"❌ Error Telegram ({resp.status}): {await resp.text()}")
        except Exception as e:
            logger.error(f"❌ Excepción en Telegram (Signal): {e}")

    async def send_simple_alert(self, text: str):
        """Envía un mensaje de texto simple a Telegram."""
        await self._load_config()
        if not self.api_url or not self.chat_id:
            return

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.api_url, json=payload) as resp:
                    if resp.status == 200:
                        logger.info(f"📲 Alerta Simple enviada")
                    else:
                        logger.error(f"❌ Error Telegram Simple ({resp.status}): {await resp.text()}")
        except Exception as e:
            logger.error(f"❌ Excepción en Telegram (Simple): {e}")

# Instancia global (Se configurará desde DB/Config)
notif_mgr = TelegramManager()
