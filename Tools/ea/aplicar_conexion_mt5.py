"""
Cambia cada `mt5.initialize()` del repo por `_pst_conectar_mt5(mt5)`, que pasa la ruta del terminal
(PST_Core/utils/terminal_mt5.py). Uso unico (2026-11-04); se guarda para trazabilidad.

En cada fichero se inserta un envoltorio pequeno antes de la primera definicion de nivel superior. Busca
PST_Core subiendo carpetas, porque varios scripts de PST_LLM se lanzan sin la raiz del repo en sys.path.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXCLUIR = {"Tools/dev/arrancar_dev.py", "Tools/ea/aplicar_conexion_mt5.py", "PST_Core/utils/terminal_mt5.py"}

ENVOLTORIO = '''
def _pst_conectar_mt5(mt5):
    """mt5.initialize() con la ruta del terminal configurado (nunca "el ultimo arrancado").
    Ver PST_Core/utils/terminal_mt5.py y MT5/DESARROLLO_EAS.md (incidente 2026-11-04)."""
    try:
        from PST_Core.utils.terminal_mt5 import conectar
    except ImportError:
        import os as _os
        import sys as _sys
        _d = _os.path.dirname(_os.path.abspath(__file__))
        while not _os.path.isdir(_os.path.join(_d, "PST_Core")) and _os.path.dirname(_d) != _d:
            _d = _os.path.dirname(_d)
        _sys.path.insert(0, _d)
        from PST_Core.utils.terminal_mt5 import conectar
    return conectar(mt5)

'''


def procesar(ruta: Path):
    texto = ruta.read_text(encoding="utf-8")
    if "mt5.initialize()" not in texto or "_pst_conectar_mt5" in texto:
        return 0
    n = texto.count("mt5.initialize()")
    nuevo = texto.replace("mt5.initialize()", "_pst_conectar_mt5(mt5)")
    # Comentarios/docstrings que solo mencionan la llamada: se dejan como estaban
    nuevo = nuevo.replace("No llama a _pst_conectar_mt5(mt5)", "No llama a mt5.initialize()")
    m = re.search(r"^(@|def |async def |class )", nuevo, re.M)
    if not m:
        raise ValueError(f"{ruta}: sin definiciones de nivel superior")
    nuevo = nuevo[:m.start()] + ENVOLTORIO.lstrip("\n") + "\n" + nuevo[m.start():]
    ruta.write_text(nuevo, encoding="utf-8")
    return n


def main():
    total = 0
    for ruta in sorted(REPO.rglob("*.py")):
        rel = ruta.relative_to(REPO).as_posix()
        if "node_modules" in rel or rel in EXCLUIR or "/__pycache__/" in rel:
            continue
        n = procesar(ruta)
        if n:
            print(f"{rel}: {n}")
            total += n
    print(f"total: {total} llamadas")


if __name__ == "__main__":
    sys.exit(main())
