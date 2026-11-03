"""
Ciclo de vida del agente de scalping (PST_LLM/scalping.py): arrancar, parar y saber si corre.

Igual que la flota: el agente es un proceso aparte. La intencion del usuario va en la config
(`enabled`, en BBDD y espejo JSON). Al abrir la app se relanza si esta encendido; al cerrarla se para
con el fichero STOP, que el agente comprueba en cada vuelta, y se espera a que salga.

Nunca mata el proceso: si no sale en el plazo se devuelve `parando` y el estado lo muestra.
"""
import os
import subprocess
import time

import psutil

import config_store
import rutas

SCRIPT = rutas.PST_LLM_DIR / "scalping.py"
DATA = rutas.AGENTS_DATA_DIR
STOP = DATA / "llm_scalping.STOP"
LOG = DATA / "llm_scalping.log"
ERR = DATA / "llm_scalping.err"
PLAZO_PARADA_S = 90


def _es_scalping(cmdline):
    return bool(cmdline) and any(a.endswith("scalping.py") for a in cmdline)


def pid_corriendo():
    """PID del agente de scalping si esta vivo, si no None. Lee la linea de comandos de cada proceso."""
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            if _es_scalping(p.info["cmdline"]):
                return p.info["pid"]
        except (psutil.Error, OSError):
            continue
    return None


def intencion_encendida():
    """Lo que quiere el usuario (config `enabled`). Defecto: encendido, como estaba el agente."""
    return bool(config_store.valor_o("enabled", True, "scalping"))


def marcar_visibles():
    """Los simbolos del scalping salen en la home (symbols_config.is_active) mientras la estrategia esta activa."""
    import sqlite3
    import registro
    simbolos = registro.simbolos_con("scalping")
    con = sqlite3.connect(registro._db_central_path(), timeout=15)
    try:
        with con:
            con.executemany("UPDATE symbols_config SET is_active=1 WHERE symbol=?", [(s,) for s in simbolos])
    finally:
        con.close()
    return simbolos


def arrancar():
    """Lanza el agente si no corre. Devuelve el PID (nuevo o existente)."""
    try:
        marcar_visibles()
    except Exception as e:
        print(f"[scalping] no pude marcar los simbolos en la home: {e}", flush=True)
    vivo = pid_corriendo()
    if vivo:
        return vivo
    STOP.unlink(missing_ok=True)
    salida = open(LOG, "a", encoding="utf-8")
    error = open(ERR, "a", encoding="utf-8")
    # Proceso independiente: sobrevive a que se cierre la terminal que lo lanzo (igual que la flota).
    flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    p = subprocess.Popen(rutas.comando_python(SCRIPT), cwd=str(rutas.DATOS), stdout=salida, stderr=error,
                         stdin=subprocess.DEVNULL, creationflags=flags)
    return p.pid


def parar(plazo_s=PLAZO_PARADA_S):
    """Pide la parada con el fichero STOP y espera a que el agente salga.

    Devuelve {"corriendo": bool}. Si sigue vivo al cabo del plazo, no se mata: se informa.
    """
    if not pid_corriendo():
        STOP.unlink(missing_ok=True)
        return {"corriendo": False}
    STOP.write_text("stop", encoding="ascii")
    limite = time.time() + plazo_s
    while time.time() < limite:
        if not pid_corriendo():
            break
        time.sleep(2)
    corriendo = pid_corriendo() is not None
    if not corriendo:
        STOP.unlink(missing_ok=True)
    return {"corriendo": corriendo}


def arrancar_si_encendido():
    """Al abrir la app: relanza el scalping si el usuario lo dejo encendido."""
    if not intencion_encendida():
        return None
    return arrancar()


def estado():
    pid = pid_corriendo()
    return {"corriendo": pid is not None, "pid": pid, "encendido": intencion_encendida()}
