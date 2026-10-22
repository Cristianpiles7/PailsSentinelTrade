"""
Rutas de la flota LLM, validas tanto desde el repo como desde el ejecutable (PyInstaller).

Dos cosas distintas:
  * RECURSOS: codigo y scripts que se ejecutan (PST_LLM/*.py, .agents/*.py). En el exe viven en la
    carpeta temporal de PyInstaller (sys._MEIPASS), que se borra al cerrar.
  * DATOS: lo que escribe la flota (.agents/data: configs, BBDD por agente, logs, STOP). Nunca puede
    ir a _MEIPASS porque se pierde en cada arranque. En el exe es PST_DATA_ROOT (.env) o, si no
    esta, la carpeta del propio exe. En el repo es la raiz del repo, como hasta ahora.
"""
import os
import shutil
import sys
from pathlib import Path

CONGELADO = bool(getattr(sys, "frozen", False))

if CONGELADO:
    _EXE_DIR = Path(sys.executable).resolve().parent
    # El .env del exe vive junto a el (la app lo busca ahi); se carga antes de leer PST_DATA_ROOT.
    try:
        from dotenv import load_dotenv
        load_dotenv(_EXE_DIR / ".env", override=False)
    except Exception:
        pass
    RECURSOS = Path(sys._MEIPASS)
    DATOS = Path(os.environ.get("PST_DATA_ROOT") or _EXE_DIR)
    # Los agentes se lanzan como procesos Python normales (python llm_trader.py): que hereden la
    # misma raiz de datos y encuentren PST_Core (empaquetado como fuente, ver build-release.yml).
    os.environ["PST_DATA_ROOT"] = str(DATOS)
    os.environ["PYTHONPATH"] = os.pathsep.join(p for p in (str(RECURSOS), os.environ.get("PYTHONPATH", "")) if p)
else:
    RECURSOS = Path(__file__).resolve().parents[1]
    DATOS = Path(os.environ["PST_DATA_ROOT"]) if os.environ.get("PST_DATA_ROOT") else RECURSOS

PST_LLM_DIR = RECURSOS / "PST_LLM"
AGENTS_CODE_DIR = RECURSOS / ".agents"
AGENTS_DATA_DIR = DATOS / ".agents" / "data"


def python_exe():
    """Interprete para lanzar los agentes. En el exe sys.executable es el propio exe, asi que no sirve."""
    if not CONGELADO:
        return sys.executable
    return shutil.which("python") or shutil.which("py") or "python"
