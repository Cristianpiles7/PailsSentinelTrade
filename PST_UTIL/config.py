import os
import sys

# Forzar UTF-8 en terminales de Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def get_real_db_path():
    """
    Busca la base de datos de producción (fuera del repo) 
    o la de desarrollo (dentro del repo) de forma inteligente.
    """
    # 1. Intentar detectar si estamos en la carpeta del repositorio
    # y buscar una carpeta PST_Core al mismo nivel que el repositorio (Producción)
    current_dir = os.path.dirname(os.path.abspath(__file__)) # PST_UTIL/
    repo_root = os.path.dirname(current_dir) # Raíz del repo
    parent_dir = os.path.dirname(repo_root) # Carpeta BOLSA/PailsSentinelTrade/

    # Ruta de "Producción" (donde está el EXE usualmente)
    prod_path = os.path.join(parent_dir, "PST_Core", "data", "pst_trading.db")
    
    # Ruta de "Desarrollo" (dentro del repo)
    dev_path = os.path.join(repo_root, "PST_Core", "data", "pst_trading.db")

    print(f"[UTIL-DEBUG] Buscando Prod: {prod_path}")
    if os.path.exists(prod_path):
        size = os.path.getsize(prod_path)
        print(f"[UTIL-DEBUG] Encontrada Prod ({size} bytes)")
        if size > 1024 * 1024:
            return os.path.abspath(prod_path)

    print(f"[UTIL-DEBUG] Buscando Dev: {dev_path}")
    if os.path.exists(dev_path):
        print(f"[UTIL-DEBUG] Encontrada Dev")
        return os.path.abspath(dev_path)

    # Fallback al que detecte PST_Core.config
    try:
        sys.path.insert(0, repo_root)
        from PST_Core.config import DB_PATH
        return DB_PATH
    except:
        return prod_path # Esperanza

def setup_paths():
    """Añade la raíz del repo al sys.path para poder importar PST_Core."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(current_dir)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

DB_PATH = get_real_db_path()
