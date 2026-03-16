import os
import sys
import time
import requests
import subprocess
import shutil
import zipfile

# ---------------------------------------------------------
# CONFIGURACIÓN DEL REPOSITORIO DE GITHUB
# ---------------------------------------------------------
GITHUB_USER = "Cristianpiles7" 
GITHUB_REPO = "PailsSentinelTrade" 
API_URL = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/releases/latest"

APP_NAME = "PST_MASTER.exe"
VERSION_FILE = "version.txt"

def log(msg):
    print(f"[LAUNCHER] {msg}")

def get_local_version():
    if os.path.exists(VERSION_FILE):
        with open(VERSION_FILE, 'r') as f:
            return f.read().strip()
    return "v0.0.0"

def get_remote_release():
    """Consulta la API de GitHub para la última Release."""
    try:
        log(f"Comprobando actualizaciones en GitHub: {GITHUB_USER}/{GITHUB_REPO}...")
        r = requests.get(API_URL, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data
        else:
            log(f"No se pudo contactar con GitHub. HTTP {r.status_code}")
            return None
    except Exception as e:
        log(f"Error de conexión con GitHub: {e}")
        return None

def download_update(download_url, new_version):
    """Descarga e instala el binario desde los assets de GitHub."""
    log("🔽 Descargando nueva versión... (Puede tardar dependiento de tu conexión)")
    try:
        # Descarga el asset
        r = requests.get(download_url, stream=True)
        if r.status_code == 200:
            temp_name = APP_NAME + ".new"
            with open(temp_name, 'wb') as f:
                shutil.copyfileobj(r.raw, f)
            
            log("📦 Instalando los nuevos archivos...")
            # Si el bot ya está corriendo, Windows bloqueará el borrado. Matamos el proceso.
            subprocess.run(f"taskkill /F /IM {APP_NAME}", shell=True, capture_output=True)
            time.sleep(2)
            
            if os.path.exists(APP_NAME):
                os.remove(APP_NAME)
                
            os.rename(temp_name, APP_NAME)
            
            # Guardamos la constancia de que estamos en la nueva versión
            with open(VERSION_FILE, 'w') as f:
                f.write(new_version)
            
            log("✅ Actualización completada con éxito.")
            return True
        else:
            log(f"❌ Error descargando EXE desde GitHub: {r.status_code}")
            return False
    except Exception as e:
        log(f"❌ Exception en actualización: {e}")
        return False

def main():
    print("==========================================")
    print("   🚀 PAILS SENTINEL LAUNCHER   ")
    print("==========================================")
    
    local_ver = get_local_version()
    release_data = get_remote_release()
    
    if release_data:
        remote_ver = release_data.get('tag_name', '')
        
        if remote_ver and remote_ver != local_ver:
            print(f"📢 ACTUALIZACIÓN DETECTADA: {local_ver} -> {remote_ver}")
            
            # Buscar nuestro EXE en los assets de la Release
            assets = release_data.get('assets', [])
            download_url = None
            for asset in assets:
                if asset['name'] == APP_NAME:
                    download_url = asset['browser_download_url']
                    break
            
            if download_url:
                if download_update(download_url, remote_ver):
                    print("Reiniciando el bot Sentinel...")
                    time.sleep(2)
            else:
                log(f"⚠️ La release {remote_ver} no contiene un archivo {APP_NAME} empaquetado.")
        else:
            print(f"✅ Estás en la última versión estable (V {local_ver})")
    
    # Arranque final
    if os.path.exists(APP_NAME):
        print(f"🚀 Lanzando Sentinel Bot...")
        # Start detached
        os.startfile(APP_NAME)
        # El launcher termina y el Bot sigue vivo
        sys.exit(0)
    else:
        print(f"❌ ERROR CRÍTICO: No se encuentra {APP_NAME}")
        print("El binario del bot está ausente o fue eliminado por un Antivirus.")
        input("Presiona ENTER para salir...")

if __name__ == "__main__":
    main()
