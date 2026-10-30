"""
Avisos criticos del sistema por Telegram (fallos de infraestructura, no operaciones).

Un aviso por clave y por ventana de tiempo: si MT5 se cae y vuelve a caer cada pocos segundos, llega un
mensaje por hora, no uno por intento. El envio va en un hilo aparte para no frenar el bucle asincrono.
Credenciales: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (entorno o .env de la raiz). El token nunca se registra.
Sin credenciales solo queda en el log.
"""
import json
import logging
import os
import threading
import time
import urllib.request
from pathlib import Path

logger = logging.getLogger("PST_Alertas")

RAIZ = Path(__file__).resolve().parents[2]
_ultimo = {}                      # clave -> instante del ultimo aviso enviado
_cerrojo = threading.Lock()


def _credenciales():
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if token and chat:
        return token, chat
    cred = {}
    try:
        for linea in (RAIZ / ".env").read_text(encoding="utf-8-sig").splitlines():
            k, _, v = linea.partition("=")
            cred[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return cred.get("TELEGRAM_BOT_TOKEN"), cred.get("TELEGRAM_CHAT_ID")


def _enviar(texto):
    token, chat = _credenciales()
    if not token or not chat:
        return
    body = json.dumps({"chat_id": chat, "text": f"🚨 {texto}"[:4000]}).encode("utf-8")
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        # Solo el tipo de error: la URL lleva el token y no debe acabar en el log.
        logger.error("No se pudo enviar el aviso por Telegram: %s", type(e).__name__)


def avisar(clave, texto, cada_s=3600):
    """Registra el aviso y, si la clave no se envio hace cada_s segundos, lo manda a Telegram."""
    ahora = time.time()
    with _cerrojo:
        if ahora - _ultimo.get(clave, 0.0) < cada_s:
            return False
        _ultimo[clave] = ahora
    logger.warning("[ALERTA] %s", texto)
    threading.Thread(target=_enviar, args=(texto,), daemon=True).start()
    return True
