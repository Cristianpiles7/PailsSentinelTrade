"""
Aviso de version nueva y auto-actualizacion desde la app.

Cada release (tag vX.Y.Z) la compila la GitHub Action y sube PST_MASTER.exe a GitHub Releases
(.github/workflows/build-release.yml). Aqui se consulta la ultima release, se compara con la
version de la app y, si el usuario acepta:

  1. se descarga PST_MASTER.exe nuevo junto al actual (.new) y se comprueba que esta completo;
  2. se renombra el exe en marcha a .old (Windows permite renombrar un exe abierto, no borrarlo)
     y el nuevo ocupa su sitio;
  3. se lanza un .bat que espera a que este proceso termine y abre el exe nuevo;
  4. se cierra la ventana: start_app() para los agentes LLM como en un cierre normal, y al
     abrir la version nueva se relanzan los que estaban activos.

Solo funciona en el exe compilado. En modo desarrollo se avisa igual, pero se actualiza con git pull.
"""
import asyncio
import logging
import os
import subprocess
import sys
import threading
import time

import requests
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["Actualizacion"])
logger = logging.getLogger("PST-Updater")

REPO = "Cristianpiles7/PailsSentinelTrade"
ASSET = "PST_MASTER.exe"
CACHE_SEG = 30 * 60          # GitHub sin token permite 60 consultas/hora: con 30 min sobra

_cache = {"ts": 0.0, "release": None, "error": None}
_estado = {"fase": "inactivo", "descargado": 0, "total": 0, "error": None}   # inactivo|descargando|reiniciando|error
_lock = threading.Lock()


def _es_exe():
    return getattr(sys, "frozen", False)


def _version_tupla(v):
    try:
        return tuple(int(p) for p in str(v).strip().lstrip("vV").split("."))
    except ValueError:
        return ()


def _consultar_github():
    """Ultima release + mensaje del commit del tag (la descripcion que se puso en /release)."""
    r = requests.get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=10)
    r.raise_for_status()
    data = r.json()
    asset = next((a for a in data.get("assets", []) if a.get("name") == ASSET), None)
    notas = (data.get("body") or "").strip()
    if not notas:
        try:
            c = requests.get(f"https://api.github.com/repos/{REPO}/commits/{data['tag_name']}", timeout=10)
            if c.ok:
                notas = c.json()["commit"]["message"].strip()
        except Exception:
            pass
    return {
        "tag": data.get("tag_name", ""),
        "publicada": data.get("published_at"),
        "notas": notas,
        "url_exe": asset["browser_download_url"] if asset else None,
        "tamano": asset["size"] if asset else 0,
    }


def _release(forzar=False):
    if forzar or time.time() - _cache["ts"] > CACHE_SEG:
        try:
            _cache["release"] = _consultar_github()
            _cache["error"] = None
        except Exception as e:
            _cache["error"] = str(e)
            logger.warning(f"⚠️ No se pudo consultar la ultima release en GitHub: {e}")
        _cache["ts"] = time.time()
    return _cache["release"]


def _posiciones_abiertas():
    try:
        import MetaTrader5 as mt5
        n = mt5.positions_total()
        return n if n is not None and n >= 0 else None
    except Exception:
        return None


def limpiar_restos():
    """Al arrancar: borra el exe viejo y descargas a medias que dejo una actualizacion anterior."""
    if not _es_exe():
        return
    for sufijo in (".old", ".new"):
        try:
            os.remove(sys.executable + sufijo)
        except OSError:
            pass


@router.get("/api/update/status")
async def update_status(request: Request, forzar: bool = False):
    release = await asyncio.to_thread(_release, forzar)
    actual = request.app.version
    disponible = bool(
        release and release["url_exe"]
        and _version_tupla(release["tag"]) > _version_tupla(actual)
    )
    return {
        "actual": actual,
        "ultima": release["tag"] if release else None,
        "disponible": disponible,
        "notas": release["notas"] if release else "",
        "publicada": release["publicada"] if release else None,
        "puede_instalar": _es_exe(),
        "posiciones_abiertas": _posiciones_abiertas() if disponible else None,
        "error_consulta": _cache["error"],
        "estado": dict(_estado),
    }


@router.post("/api/update/apply")
async def update_apply(request: Request):
    if not _es_exe():
        raise HTTPException(400, "Modo desarrollo: actualiza con git pull, no desde la app.")
    release = await asyncio.to_thread(_release, True)
    if not release or not release["url_exe"]:
        raise HTTPException(502, "No se encontro el ejecutable en la ultima release de GitHub.")
    if _version_tupla(release["tag"]) <= _version_tupla(request.app.version):
        raise HTTPException(400, "Ya tienes la ultima version.")
    with _lock:
        if _estado["fase"] in ("descargando", "reiniciando"):
            raise HTTPException(409, "Ya hay una actualizacion en curso.")
        _estado.update(fase="descargando", descargado=0, total=release["tamano"], error=None)
    threading.Thread(target=_instalar, args=(release,), daemon=True).start()
    return {"ok": True, "version": release["tag"]}


def _instalar(release):
    exe = sys.executable
    nuevo, viejo = exe + ".new", exe + ".old"
    try:
        logger.info(f"🔽 Descargando {release['tag']} desde GitHub...")
        with requests.get(release["url_exe"], stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(nuevo, "wb") as f:
                for trozo in r.iter_content(1024 * 1024):
                    f.write(trozo)
                    _estado["descargado"] += len(trozo)

        tamano = os.path.getsize(nuevo)
        with open(nuevo, "rb") as f:
            cabecera = f.read(2)
        if cabecera != b"MZ" or (release["tamano"] and tamano != release["tamano"]):
            raise RuntimeError(f"Descarga incompleta o corrupta ({tamano} de {release['tamano']} bytes)")

        # Cambio de exe: si falla a medias, se deja el original en su sitio
        try:
            os.remove(viejo)
        except OSError:
            pass
        os.replace(exe, viejo)
        try:
            os.replace(nuevo, exe)
        except OSError:
            os.replace(viejo, exe)
            raise

        # version.txt junto al exe: lo lee PST_LAUNCHER para saber que version hay instalada
        try:
            with open(os.path.join(os.path.dirname(exe), "version.txt"), "w") as f:
                f.write(release["tag"])
        except OSError:
            pass

        _estado["fase"] = "reiniciando"
        logger.info(f"✅ {release['tag']} instalada. Reiniciando la app...")
        _lanzar_reinicio(exe)
        time.sleep(1.5)          # deja que el frontend lea la fase "reiniciando"
        _cerrar_app()
    except Exception as e:
        logger.exception("❌ Fallo al actualizar")
        _estado.update(fase="error", error=str(e))
        try:
            os.remove(nuevo)
        except OSError:
            pass


def _lanzar_reinicio(exe):
    """Bat que espera a que muera este proceso (y el bootloader de PyInstaller, su padre) y abre el exe.
    Sin consola 'timeout' no funciona: la pausa de 1 s se hace con ping."""
    # Rutas absolutas: no depender del PATH (con Git en el PATH, 'find' seria el de Unix)
    sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    tasklist, find, taskkill, ping = (os.path.join(sys32, n) for n in ("tasklist.exe", "find.exe", "taskkill.exe", "PING.EXE"))
    exe = os.path.abspath(exe)
    pids = sorted({os.getpid(), os.getppid()})
    comprobar = "\n".join(
        f'"{tasklist}" /FI "PID eq {p}" /NH 2>nul | "{find}" "{p}" >nul && set vivo=1' for p in pids
    )
    matar = " ".join(f"/PID {p}" for p in pids)
    pausa = f'"{ping}" -n 2 127.0.0.1 >nul'
    bat = os.path.join(os.path.dirname(exe), "_pst_reinicio.bat")
    lineas = [
        "@echo off",
        "set n=0",
        ":esperar",
        "set vivo=0",
        comprobar,
        "if %vivo%==0 goto abrir",
        "set /a n+=1",
        "if %n% geq 90 goto forzar",
        pausa,
        "goto esperar",
        ":forzar",
        f'"{taskkill}" /F {matar} >nul 2>&1',
        ":abrir",
        pausa,
        pausa,
        f'start "" "{exe}"',
        '(goto) 2>nul & del "%~f0"',
    ]
    with open(bat, "w", encoding="mbcs") as f:
        f.write("\n".join(lineas) + "\n")
    # Consola oculta (CREATE_NO_WINDOW), no DETACHED_PROCESS: sin consola tasklist/ping se cuelgan.
    # BREAKAWAY_FROM_JOB: que el bat sobreviva aunque el proceso padre este en un job que mata a sus hijos.
    base = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    for flags in (base | subprocess.CREATE_BREAKAWAY_FROM_JOB, base):
        try:
            subprocess.Popen(
                [os.path.join(sys32, "cmd.exe"), "/c", bat],
                cwd=os.path.dirname(exe),
                creationflags=flags,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            return
        except OSError:
            continue
    raise RuntimeError("No se pudo lanzar el reinicio")


def _cerrar_app():
    """Cierra la ventana: start_app() sigue con su limpieza normal (agentes, habblaud) y el proceso sale.
    Si en 30 s no ha salido, se fuerza para que el bat pueda abrir la version nueva."""
    try:
        import webview
        for w in list(webview.windows):
            w.destroy()
    except Exception:
        logger.exception("No se pudo cerrar la ventana")
    t = threading.Timer(30, lambda: os._exit(0))
    t.daemon = True
    t.start()
