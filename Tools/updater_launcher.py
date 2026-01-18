import os
import sys
import time
import requests
import subprocess
import shutil

# ---------------------------------------------------------
# CONFIGURACIÓN DEL REPOSITORIO (EDITAR ESTO)
# ---------------------------------------------------------
# Reemplaza con tu usuario y repo. Ejemplo: "TuUsuario/PailsSentinelTrade"
GITHUB_USER = "Cristianpiles7" 
GITHUB_REPO = "PailsSentinelTrade" 
BRANCH = "master"

# URLs crudas (Raw)
BASE_URL = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{BRANCH}"
URL_VERSION = f"{BASE_URL}/version.txt"
URL_EXE = f"{BASE_URL}/RELEASE/PST_MASTER.exe" # Asumiremos que subes el EXE compilado aquí

APP_NAME = "PST_MASTER.exe"
VERSION_FILE = "version.txt"

def log(msg):
    print(f"[LAUNCHER] {msg}")

def get_remote_version():
    try:
        log(f"Comprobando actualizaciones en conrol de versiones: {URL_VERSION}")
        r = requests.get(URL_VERSION, timeout=5)
        if r.status_code == 200:
            return r.text.strip()
        else:
            log(f"Error checking version: {r.status_code}")
            return None
    except Exception as e:
        log(f"Connection error: {e}")
        return None

def get_local_version():
    if os.path.exists(VERSION_FILE):
        with open(VERSION_FILE, 'r') as f:
            return f.read().strip()
    return "0.0.0"

def download_update():
    log("🔽 Descargando nueva versión... (Puede tardar)")
    try:
        # Descargar como temporal
        r = requests.get(URL_EXE, stream=True)
        if r.status_code == 200:
            with open(APP_NAME + ".new", 'wb') as f:
                shutil.copyfileobj(r.raw, f)
            
            # Reemplazar
            if os.path.exists(APP_NAME):
                os.remove(APP_NAME)
            os.rename(APP_NAME + ".new", APP_NAME)
            
            # Actualizar archivo versión local
            remote_ver = get_remote_version()
            if remote_ver:
                with open(VERSION_FILE, 'w') as f:
                    f.write(remote_ver)
            
            log("✅ Actualización completada con éxito.")
            return True
        else:
            log(f"❌ Error descargando EXE: {r.status_code}")
            return False
    except Exception as e:
        log(f"❌ Exception en descarga: {e}")
        return False

def main():
    print("==========================================")
    print("   🚀 PAILS SENTINEL LAUNCHER (V1)   ")
    print("==========================================")
    
    local = get_local_version()
    remote = get_remote_version()
    
    if remote and remote != local:
        print(f"📢 ACTUALIZACIÓN DETECTADA: {local} -> {remote}")
        if download_update():
            print("Reiniciando con nueva versión...")
            time.sleep(2)
    else:
        print(f"✅ Estás al día (v{local})")
        
    if os.path.exists(APP_NAME):
        print(f"🚀 Lanzando {APP_NAME}...")
        subprocess.Popen([APP_NAME])
    else:
        print(f"❌ ERROR: No se encuentra {APP_NAME}")
        print("Descárgalo manualmente o revisa la conexión.")
        input("Presiona ENTER para salir...")

if __name__ == "__main__":
    main()
