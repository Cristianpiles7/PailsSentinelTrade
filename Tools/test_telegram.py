"""
Test de envío de Telegram para PST.
Verifica que el bot puede enviar mensajes antes de arrancar el sistema.

Uso: python Tools/test_telegram.py
"""
import os
import sys
import asyncio
from datetime import datetime

# Añadir raíz al path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


async def run_test():
    if not TOKEN or not CHAT_ID:
        print("❌ Credenciales no encontradas en .env")
        print()
        print("  Ejecuta primero:  python Tools/get_telegram_chat_id.py")
        print("  o añade manualmente al .env:")
        print("    TELEGRAM_BOT_TOKEN=xxxx")
        print("    TELEGRAM_CHAT_ID=xxxx")
        sys.exit(1)

    print(f"✅ TOKEN encontrado: ...{TOKEN[-8:]}")
    print(f"✅ CHAT_ID encontrado: {CHAT_ID}")
    print("\nEnviando mensaje de prueba...")

    from telegram import Bot
    from telegram.constants import ParseMode

    bot = Bot(token=TOKEN)
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    msg = (
        f"🟢 <b>PST — Test de Conectividad</b>\n\n"
        f"✅ El Agente Monitor está correctamente configurado.\n\n"
        f"Comandos disponibles:\n"
        f"  /status  — Ver posiciones abiertas\n"
        f"  /metrics — Métricas en tiempo real\n"
        f"  /report  — Resumen del día\n"
        f"  /closeall — Cierre de emergencia\n\n"
        f"<i>Enviado: {now}</i>"
    )

    try:
        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode=ParseMode.HTML)
        print("\n✅ ¡Mensaje enviado con éxito!")
        print("   Revisa tu Telegram — deberías ver el mensaje del bot.")
    except Exception as e:
        print(f"\n❌ Error al enviar: {e}")
        print("\n  Posibles causas:")
        print("  - El CHAT_ID es incorrecto")
        print("  - El TOKEN ha expirado o es inválido")
        print("  - Nunca escribiste al bot (búscalo en Telegram y envíale 'hola')")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_test())
