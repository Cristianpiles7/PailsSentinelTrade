
import sys
import os

if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')

# Al estar en la raíz, no necesitamos hacks de sys.path para desarrollo,
# pero para el EXE es bueno asegurar que sys._MEIPASS sea la prioridad.
if hasattr(sys, '_MEIPASS'):
    project_root = sys._MEIPASS
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

from PST_API.main import start_app

if __name__ == "__main__":
    # Arrancar la aplicación unificada (FastAPI + pywebview)
    start_app()
