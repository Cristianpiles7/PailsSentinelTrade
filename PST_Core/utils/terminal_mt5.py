"""
Conexion a MetaTrader 5 SIEMPRE con la ruta del terminal (incidente 2026-11-04, MT5/DESARROLLO_EAS.md §7).

`mt5.initialize()` sin ruta se engancha al ULTIMO terminal arrancado en el PC. Al abrir el terminal
portable de pruebas (C:\\Users\\crist\\PST_MT5_Tester), produccion y sus agentes pasaron a operar a traves
de el. Con la ruta explicita cada proceso se conecta a su terminal y el portable no puede "robarlo".

Orden para elegir terminal:
  1. PST_MT5_PATH          (lo fija el entorno de pruebas con --mt5-tester)
  2. MT5_TERMINAL_PATH     (.env, si se quiere fijar a mano)
  3. autodeteccion: terminal INSTALADO (no portable) cuya carpeta de datos este en
     %APPDATA%\\MetaQuotes\\Terminal, prefiriendo el de FTMO
  4. si no hay nada: initialize() sin ruta, como antes

Solo libreria estandar: lo importan procesos muy distintos (API, agentes, MCP, Tools).
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger("PST-MT5")

_ruta_cache = None


def _autodetectar():
    base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "MetaQuotes" / "Terminal"
    candidatos = []
    if base.exists():
        for d in base.iterdir():
            origen = d / "origin.txt"
            if not origen.exists():
                continue
            try:
                instalacion = origen.read_bytes().decode("utf-16", errors="ignore").strip("\ufeff\x00 \r\n")
            except OSError:
                continue
            exe = Path(instalacion) / "terminal64.exe"
            if exe.exists():
                candidatos.append((0 if "FTMO" in instalacion.upper() else 1, str(exe)))
    candidatos.sort()
    return candidatos[0][1] if candidatos else ""


def ruta_terminal():
    """Ruta de terminal64.exe al que debe conectarse este proceso ('' si no se sabe)."""
    global _ruta_cache
    if _ruta_cache is None:
        ruta = os.environ.get("PST_MT5_PATH") or os.environ.get("MT5_TERMINAL_PATH") or _autodetectar()
        if ruta and Path(ruta).is_dir():
            ruta = str(Path(ruta) / "terminal64.exe")
        _ruta_cache = ruta if ruta and Path(ruta).exists() else ""
    return _ruta_cache


def conectar(mt5, **kwargs):
    """Sustituye a mt5.initialize(). Devuelve lo mismo (True/False)."""
    ruta = ruta_terminal()
    if not ruta:
        return mt5.initialize(**kwargs)
    kwargs.setdefault("path", ruta)
    if os.environ.get("PST_MT5_PORTABLE") == "1":
        kwargs.setdefault("portable", True)
    return mt5.initialize(**kwargs)
