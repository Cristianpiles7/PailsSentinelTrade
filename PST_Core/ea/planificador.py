"""
Programador de la flota de agentes DENTRO de la app (alternativa a la tarea de Windows de
Tools/scheduler_install.ps1, que necesita permisos de administrador).

Mientras la app esta abierta (y lo esta siempre, porque opera), una vez al dia a la hora configurada lanza
`.agents/scheduler.py` desde el REPO con el Python del sistema: los agentes necesitan el repo (prompts,
.mcp.json, PST_MCP) y no pueden correr desde el exe. El planificador decide que agentes tocan (puertas SQL y
cuota), asi que lanzarlo no gasta nada si no hay trabajo.

Configuracion en ea_config (pestaña Configuracion de EAs MT5): planificador_activo, planificador_hora.
Estado (ultima ejecucion) en <datos>/.agents/data/planificador_estado.json.
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import rutas, store

logger = logging.getLogger("PST-Planificador")

_proceso = None


def _datos():
    base = Path(os.environ.get("PST_DATA_ROOT") or rutas.REPO)
    d = base / ".agents" / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ruta_estado():
    return _datos() / "planificador_estado.json"


def leer_estado():
    try:
        return json.loads(_ruta_estado().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _guardar_estado(e):
    _ruta_estado().write_text(json.dumps(e, indent=2), encoding="utf-8")


def repo():
    """Repo donde estan .agents/scheduler.py y el MCP. Es el mismo del que salen las fuentes de los EAs."""
    r = Path(rutas.REPO)
    return r if (r / ".agents" / "scheduler.py").exists() else None


def python_sistema():
    """Python del sistema (no el exe congelado), con el que corren el planificador y el MCP."""
    if not getattr(sys, "frozen", False):
        return sys.executable
    for nombre in ("python", "python3", "py"):
        p = shutil.which(nombre)
        if p and "WindowsApps" not in p:
            return p
    return ""


def log_path():
    r = repo()
    return (r / ".agents" / "out" / "scheduler.log") if r else None


def en_curso():
    return _proceso is not None and _proceso.poll() is None


def lanzar(motivo="manual"):
    """Lanza el planificador en segundo plano. Devuelve dict con el resultado."""
    global _proceso
    if en_curso():
        return {"ok": False, "error": "ya hay una pasada del planificador en curso"}
    r, py = repo(), python_sistema()
    if not r:
        return {"ok": False, "error": f"no encuentro .agents/scheduler.py en {rutas.REPO}"}
    if not py:
        return {"ok": False, "error": "no encuentro el Python del sistema en el PATH"}
    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    salida = open(log, "a", encoding="utf-8")
    salida.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M} · lanzado desde la app ({motivo}) =====\n")
    salida.flush()
    entorno = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    _proceso = subprocess.Popen([py, "-u", str(r / ".agents" / "scheduler.py")], cwd=str(r), stdout=salida,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=entorno, creationflags=flags)
    e = leer_estado()
    e.update({"ultima": time.time(), "ultima_fecha": datetime.now().strftime("%Y-%m-%d"), "motivo": motivo,
              "pid": _proceso.pid})
    _guardar_estado(e)
    store.evento(None, "planificador", f"lanzado ({motivo}), pid {_proceso.pid}", "sistema")
    logger.info("🗓️ Planificador de agentes lanzado (%s), pid %s", motivo, _proceso.pid)
    return {"ok": True, "pid": _proceso.pid}


def estado():
    cfg = store.config()
    e = leer_estado()
    log = log_path()
    cola = []
    if log and log.exists():
        try:
            cola = log.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
        except OSError:
            pass
    return {"activo": cfg["planificador_activo"], "hora": cfg["planificador_hora"], "en_curso": en_curso(),
            "ultima": e.get("ultima"), "motivo": e.get("motivo"), "repo": str(repo() or ""),
            "python": python_sistema(), "log": cola}


def toca_ahora(ahora=None):
    """True si hoy aun no se ha lanzado y estamos dentro de la ventana [hora, hora + 4 h).

    La ventana evita que, si la app estuvo cerrada de noche, los agentes arranquen a media manana y se coman la
    cuota mientras el usuario trabaja: esa noche simplemente no hay pasada.
    """
    cfg = store.config()
    if not cfg["planificador_activo"]:
        return False
    ahora = ahora or datetime.now()
    try:
        hh, mm = (int(x) for x in str(cfg["planificador_hora"]).split(":"))
    except ValueError:
        hh, mm = 3, 0
    minutos = ahora.hour * 60 + ahora.minute - (hh * 60 + mm)
    if not 0 <= minutos < 240:
        return False
    return leer_estado().get("ultima_fecha") != ahora.strftime("%Y-%m-%d")


async def bucle():
    """Tarea de fondo de la app: mira cada minuto si toca lanzar."""
    import asyncio
    while True:
        try:
            if toca_ahora() and not en_curso():
                lanzar("programado")
        except Exception:
            logger.exception("Error en el programador de agentes")
        await asyncio.sleep(60)
