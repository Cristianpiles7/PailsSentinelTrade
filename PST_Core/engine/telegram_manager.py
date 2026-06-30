import os
import asyncio
import logging
from telegram import Bot, Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("PST-Telegram")

class TelegramManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TelegramManager, cls).__new__(cls)
            cls._instance._initialized = False
            cls._instance.application = None
            cls._instance.engine = None
            cls._instance.monitor_agent = None
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.bot = Bot(token=self.token) if self.token else None
        self._initialized = True
        
        if not self.token or not self.chat_id:
            logger.warning("⚠️ TelegramManager: TOKEN o CHAT_ID no configurados en .env")

    async def start_listener(self, engine, monitor_agent=None):
        """Inicializa la aplicación y escucha comandos 24/7."""
        if not self.token:
            return

        self.engine = engine
        self.monitor_agent = monitor_agent
        logger.info("🤖 [TELEGRAM] Inicializando Listener de Comandos...")

        try:
            self.application = Application.builder().token(self.token).build()

            # Registrar comandos
            self.application.add_handler(CommandHandler("status", self.cmd_status))
            self.application.add_handler(CommandHandler("closeall", self.cmd_closeall))
            self.application.add_handler(CommandHandler("report", self.cmd_report))
            self.application.add_handler(CommandHandler("metrics", self.cmd_metrics))
            self.application.add_handler(CallbackQueryHandler(self.handle_callback))
            
            # Iniciar aplicación puramente asíncrona dentro del event loop actual de PST
            await self.application.initialize()
            await self.application.start()
            
            # Use lower-level start_polling avoiding Updater blocking
            await self.application.updater.start_polling(drop_pending_updates=True)
            logger.info("✅ [TELEGRAM] Listener activo. Esperando comandos.")
            
        except Exception as e:
            logger.error(f"❌ Error al iniciar Telegram Listener: {e}")

    async def get_authorized_chat(self, update: Update) -> bool:
        """Verifica que el usuario solicitante es el dueño (el CHAT_ID del .env)."""
        incoming_id = str(update.effective_chat.id)
        if incoming_id != str(self.chat_id):
            logger.warning(f"⚠️ Intento de acceso a Telegram de ID no autorizado: {incoming_id}")
            return False
        return True

    # ------------------
    # COMANDOS
    # ------------------
    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.get_authorized_chat(update): return
        
        if not self.engine:
            await update.message.reply_text("❌ Motor no está enlazado todavía.")
            return

        try:
            # Obtener datos usando el Engine (Orchestrator tiene db y positions)
            positions = await self.engine.db.get_active_trades()
            active_count = len(positions)
            
            summary_text = "<b>📊 PST STATUS RESUMEN</b>\n\n"
            summary_text += f"🔌 Motor Activo | Operaciones Abiertas: {active_count}\n\n"
            
            for p in positions:
                summary_text += f"- {p['symbol']} | {p['type']} {p['volume']} lotes | In: {p['price_in']}\n"
            
            if active_count == 0:
                summary_text += "<i>Sin operaciones flotantes.</i>"
                
            keyboard = [
                [InlineKeyboardButton("🔄 Refresh Status", callback_data='cmd_status')],
            ]
            
            if active_count > 0:
                keyboard.append([InlineKeyboardButton("🛑 EMERGENCY CLOSE ALL", callback_data='cmd_closeall')])

            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Si venimos de un CallbackQuery (botón), editamos el mensaje en lugar de enviar uno nuevo
            if update.callback_query:
                try:
                    await update.callback_query.edit_message_text(text=summary_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
                except Exception as e:
                    # Ignorar el error si el texto no ha cambiado
                    if "Message is not modified" not in str(e):
                        raise e
            else:
                await update.message.reply_text(summary_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)

        except Exception as e:
            if update.callback_query:
                await update.callback_query.message.reply_text(f"❌ Error obteniendo status: {e}")
            else:    
                await update.message.reply_text(f"❌ Error obteniendo status: {e}")

    async def cmd_closeall(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self.get_authorized_chat(update): return
        
        if not self.engine:
            msg = "❌ Motor no está enlazado todavía."
            if update.callback_query: await update.callback_query.message.reply_text(msg)
            else: await update.message.reply_text(msg)
            return
            
        alert_msg = "⚠️ <b>EMERGENCY PROTOCOL ACTIVATED</b>\nIntentando cerrar todas las posiciones..."
        if update.callback_query:
            await update.callback_query.message.reply_text(alert_msg, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(alert_msg, parse_mode=ParseMode.HTML)
        
        try:
            positions = await self.engine.db.get_active_trades()
            symbols_to_close = set(p['symbol'] for p in positions)
            
            for symbol in symbols_to_close:
                # LLamar al executor para cerrar (o al metatrader directo)
                # PSTExecutor tiene close_all_for_symbol
                await self.engine.close_all_for_symbol(symbol, "TELEGRAM_EMERGENCY_CLOSE")
            
            confirm_msg = f"✅ Protocolo ejecutado para {len(symbols_to_close)} símbolos."
            if update.callback_query: await update.callback_query.message.reply_text(confirm_msg)
            else: await update.message.reply_text(confirm_msg)
            
        except Exception as e:
            err_msg = f"❌ Error durante el cierre de emergencia: {e}"
            if update.callback_query: await update.callback_query.message.reply_text(err_msg)
            else: await update.message.reply_text(err_msg)

    async def cmd_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Genera y envía el resumen diario de operaciones."""
        if not await self.get_authorized_chat(update):
            return
        if not self.monitor_agent:
            await update.message.reply_text("⚠️ Monitor agent no está activo.")
            return
        report = await self.monitor_agent.build_daily_report()
        await update.message.reply_text(report, parse_mode=ParseMode.HTML)

    async def cmd_metrics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Muestra métricas de cuenta en tiempo real."""
        if not await self.get_authorized_chat(update):
            return
        if not self.monitor_agent:
            await update.message.reply_text("⚠️ Monitor agent no está activo.")
            return
        snapshot = await self.monitor_agent.build_metrics_snapshot()
        keyboard = [[InlineKeyboardButton("🔄 Actualizar", callback_data='cmd_metrics')]]
        await update.message.reply_text(
            snapshot,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Maneja las pulsaciones de los botones integrados (InlineKeyboard)."""
        query = update.callback_query
        
        # Opcional: Acknowledge callback immediately to remove loading state on button
        await query.answer()

        if query.data == 'cmd_status':
            await self.cmd_status(update, context)
        elif query.data == 'cmd_closeall':
            await self.cmd_closeall(update, context)
        elif query.data == 'cmd_metrics':
            await self.cmd_metrics(update, context)

    async def send_message(self, text: str):
        """Envía un mensaje asíncrono a Telegram."""
        if not self.bot or not self.chat_id:
            return
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"❌ Error enviando mensaje a Telegram: {e}")

    async def send_signal_alert(self, symbol, stype, score, price, strategy):
        """Notificación de nueva señal detectada."""
        emoji = "🚀" if stype == "BUY" else "🔻"
        text = (
            f"<b>{emoji} NUEVA SEÑAL DETECTADA</b>\n\n"
            f"<b>Símbolo:</b> {symbol}\n"
            f"<b>Tipo:</b> {stype}\n"
            f"<b>Score:</b> {score}\n"
            f"<b>Precio:</b> {price}\n"
            f"<b>Estrategia:</b> {strategy}\n\n"
            f"<i>Pails Sentinel Trade Intelligence</i>"
        )
        await self.send_message(text)

    async def send_trade_notification(self, ticket, stype, symbol, price, profit, is_closing=False):
        """Notificación de apertura o cierre de trade."""
        if is_closing:
            emoji = "💰" if profit >= 0 else "📉"
            status = "CERRADA"
            profit_str = f"<b>Beneficio:</b> {profit:.2f}€\n"
        else:
            emoji = "⚡"
            status = "ABIERTA"
            profit_str = ""

        text = (
            f"<b>{emoji} OPERACIÓN {status}</b>\n\n"
            f"<b>Ticket:</b> #{ticket}\n"
            f"<b>Símbolo:</b> {symbol}\n"
            f"<b>Tipo:</b> {stype}\n"
            f"<b>Precio:</b> {price}\n"
            f"{profit_str}\n"
            f"<i>Sentinel execution node active.</i>"
        )
        await self.send_message(text)

# Instancia única
telegram_bot = TelegramManager()
