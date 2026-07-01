"""
Ejecuta este script UNA VEZ para obtener tu Chat ID de Telegram.

Pasos:
  1. Abre tu bot en Telegram y escríbele cualquier mensaje (ej: "hola")
  2. Ejecuta: python Tools/get_telegram_chat_id.py
  3. Copia el chat_id que aparezca y ponlo en tu .env
"""
import sys
import os
import requests

def main():
    token = input("Pega tu TELEGRAM_BOT_TOKEN aquí: ").strip()
    if not token:
        print("❌ Token vacío. Abortando.")
        sys.exit(1)

    print("\nConsultando actualizaciones del bot...")
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
    except Exception as e:
        print(f"❌ Error de conexión: {e}")
        sys.exit(1)

    if not data.get("ok"):
        print(f"❌ Error de Telegram: {data.get('description', 'desconocido')}")
        print("   Verifica que el token es correcto.")
        sys.exit(1)

    results = data.get("result", [])
    if not results:
        print("\n⚠️  No se encontraron mensajes.")
        print("   → Abre tu bot en Telegram y envíale un mensaje ('hola'), luego vuelve a ejecutar este script.")
        sys.exit(1)

    # Extraer chats únicos
    seen = set()
    print("\n✅ Chats encontrados:\n")
    for update in results:
        msg = update.get("message") or update.get("channel_post")
        if not msg:
            continue
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        chat_name = chat.get("first_name") or chat.get("title") or chat.get("username") or "—"
        chat_type = chat.get("type", "—")
        if chat_id and chat_id not in seen:
            seen.add(chat_id)
            print(f"  ID: {chat_id}  |  Nombre: {chat_name}  |  Tipo: {chat_type}")

    if not seen:
        print("⚠️  No se encontraron mensajes de usuario.")
        print("   → Abre el bot y escríbele algo, luego vuelve a ejecutar.")
        return

    print("\n─────────────────────────────────────────────────────")
    print("Copia el ID que corresponda a tu chat personal y")
    print("añádelo al archivo .env de PST:")
    print()
    print("  TELEGRAM_BOT_TOKEN=<tu_token>")
    print("  TELEGRAM_CHAT_ID=<el_id_de_arriba>")
    print()

    # Ofrecer escribir el .env directamente
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    chat_id_str = input("¿Quieres que lo escriba en .env automáticamente? Pega el chat_id (o Enter para saltar): ").strip()
    if chat_id_str:
        lines = []
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

        # Actualizar o añadir
        updated_token = updated_chat = False
        for i, line in enumerate(lines):
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                lines[i] = f"TELEGRAM_BOT_TOKEN={token}\n"
                updated_token = True
            if line.startswith("TELEGRAM_CHAT_ID="):
                lines[i] = f"TELEGRAM_CHAT_ID={chat_id_str}\n"
                updated_chat = True

        if not updated_token:
            lines.append(f"\nTELEGRAM_BOT_TOKEN={token}\n")
        if not updated_chat:
            lines.append(f"TELEGRAM_CHAT_ID={chat_id_str}\n")

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        print(f"\n✅ .env actualizado en: {env_path}")
        print("\nAhora ejecuta el test de envío:")
        print("   python Tools/test_telegram.py")

if __name__ == "__main__":
    main()
