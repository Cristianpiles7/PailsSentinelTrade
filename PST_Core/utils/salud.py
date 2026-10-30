"""
Latido del proceso: la app escribe su estado cada minuto en un fichero junto a la BBDD. Asi un vigia externo
(PST_UTIL/vigia_salud.py) puede ver si la app sigue viva aunque su bucle este bloqueado.
"""
import json
import os
import time
from pathlib import Path


def ruta():
    from PST_Core.config import DB_PATH
    return Path(DB_PATH).parent / "heartbeat.json"


def escribir(**campos):
    """Escribe el latido. Nunca lanza: un fallo de disco no debe tumbar la app."""
    campos["t"] = time.time()
    f = ruta()
    tmp = f.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(campos), encoding="utf-8")
        os.replace(tmp, f)
    except OSError:
        pass


def leer():
    """Ultimo latido como dict, o None si no existe o no se puede leer."""
    try:
        return json.loads(ruta().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
