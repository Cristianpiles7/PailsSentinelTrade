"""
Bloqueo de operador: solo un PC opera la flota LLM sobre la cuenta.

Fichero .agents/data/operador.json con el equipo que opera y la hora de su ultimo latido. Cada pasada de
la rotacion y cada arranque lo renuevan. Si otro equipo tiene latido de hace menos de CADUCA_S, este no
activa ni arranca agentes (los que ya corran en el otro PC no se tocan). Si el otro equipo se apaga, el
latido caduca y este puede tomar el relevo.
"""
import json
import socket
import time

import rutas

FICHERO = rutas.AGENTS_DATA_DIR / "operador.json"
CADUCA_S = 15 * 60
EQUIPO = socket.gethostname()


def _leer():
    try:
        return json.loads(FICHERO.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, ValueError):
        return None


def operador_ajeno():
    """Nombre del otro equipo que opera ahora, o None si nadie opera o es este."""
    d = _leer()
    if not d or d.get("equipo") == EQUIPO:
        return None
    if time.time() - d.get("latido", 0) > CADUCA_S:
        return None
    return d["equipo"]


def tomar_o_latir():
    """Este equipo opera: escribe su latido. Devuelve el otro equipo si lo impide, o None si puede operar."""
    ajeno = operador_ajeno()
    if ajeno:
        return ajeno
    FICHERO.parent.mkdir(parents=True, exist_ok=True)
    FICHERO.write_text(json.dumps({"equipo": EQUIPO, "latido": time.time()}, indent=2), encoding="utf-8")
    return None
