import sys
import os
import logging

# Configuración de logs de emergencia para el ejecutable
def setup_emergency_logging():
    log_path = "PST_Startup.log"
    if hasattr(sys, '_MEIPASS'):
        exe_dir = os.path.dirname(sys.executable)
        log_path = os.path.join(exe_dir, "PST_Startup.log")

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(log_path, encoding='utf-8'),
            logging.StreamHandler(sys.stdout) if sys.stdout else logging.NullHandler()
        ]
    )
    return logging.getLogger("PST-Startup")

logger = setup_emergency_logging()

if hasattr(sys, '_MEIPASS'):
    project_root = sys._MEIPASS
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

# Redirigir stdout y stderr si no existen (en modo windowed de PyInstaller)
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')

from PST_API.main import start_app

if __name__ == "__main__":
    try:
        logger.info("🚀 Iniciando Pails Sentinel Trade (PST)...")
        start_app()
    except Exception as e:
        logger.critical(f"❌ FALLO CRÍTICO EN EL ARRANQUE: {e}", exc_info=True)
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, f"Error al iniciar PST:\n{str(e)}\n\nRevisa PST_Startup.log para más detalles.", "PST Error", 0x10)
        except:
            pass
        sys.exit(1)
