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
    # Sin python-dotenv (puede no ir empaquetado en el exe): KEY=VALUE, sin pisar lo ya definido.
    try:
        for _linea in (_EXE_DIR / ".env").read_text(encoding="utf-8-sig").splitlines():
            _linea = _linea.strip()
            if _linea and not _linea.startswith("#") and "=" in _linea:
                _k, _v = _linea.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
    except OSError:
        pass
    RECURSOS = Path(sys._MEIPASS)
    DATOS = Path(os.environ.get("PST_DATA_ROOT") or _EXE_DIR)
    # Los agentes heredan la misma raiz de datos. NO se pone PYTHONPATH con la raiz del bundle: contiene
    # _sqlite3.pyd (compilado para python312) y, delante de la libreria estandar, el hijo (python 3.13 del
    # sistema) lo cargaba y fallaba en sqlite3. Los hijos reciben sus rutas por comando_python().
    os.environ["PST_DATA_ROOT"] = str(DATOS)
    # PyInstaller antepone _MEIPASS al PATH: el python312.dll del exe choca con el Python del sistema
    # que usan los hijos (ImportError en sqlite3). Se quita de su entorno, junto con las variables _PYI_.
    _mei = os.path.normcase(os.path.abspath(sys._MEIPASS))
    os.environ["PATH"] = os.pathsep.join(
        p for p in os.environ.get("PATH", "").split(os.pathsep)
        if p and not os.path.normcase(os.path.abspath(p)).startswith(_mei))
    for _k in [k for k in os.environ if k.startswith("_PYI_") or k == "_MEIPASS2"]:
        del os.environ[_k]
else:
    RECURSOS = Path(__file__).resolve().parents[1]
    DATOS = Path(os.environ["PST_DATA_ROOT"]) if os.environ.get("PST_DATA_ROOT") else RECURSOS

PST_LLM_DIR = RECURSOS / "PST_LLM"
AGENTS_CODE_DIR = RECURSOS / ".agents"
AGENTS_DATA_DIR = DATOS / ".agents" / "data"


def comando_python(script, *args):
    """
    Comando para lanzar un script de la flota como proceso independiente.

    En el exe los hijos son el propio exe en modo --ejecutar-script (PST_MASTER.py): llevan empaquetadas
    todas sus dependencias, asi que no dependen de ningun Python instalado en el PC.
    """
    if not CONGELADO:
        return [sys.executable, "-u", str(script), *args]
    return [sys.executable, "--ejecutar-script", str(script), *args]
