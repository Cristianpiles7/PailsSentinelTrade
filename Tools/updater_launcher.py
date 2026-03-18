import os
import sys
import time
import requests
import subprocess
import shutil

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
        elif r.status_code == 404:
            log("⚠️ Error 404: No se encontró la release.")
            log("Esto suele ocurrir si:")
            log(" 1. El Repositorio es PRIVADO (el actualizador no puede verlo sin permiso).")
            log(" 2. El nombre del usuario o repo son incorrectos.")
            log(" 3. No hay ninguna versión publicada en la pestaña 'Releases' de GitHub.")
            return None
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
        r = requests.get(download_url, stream=True)
        if r.status_code == 200:
            temp_name = APP_NAME + ".new"
            with open(temp_name, 'wb') as f:
                shutil.copyfileobj(r.raw, f)
            
            log("📦 Instalando los nuevos archivos...")
            subprocess.run(f"taskkill /F /IM {APP_NAME}", shell=True, capture_output=True)
            time.sleep(2)
            
            if os.path.exists(APP_NAME):
                os.remove(APP_NAME)
                
            os.rename(temp_name, APP_NAME)
            
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
    try:
        print("==========================================")
        print("   🚀 PAILS SENTINEL LAUNCHER   ")
        print("==========================================")
        
        local_ver = get_local_version()
        release_data = get_remote_release()
        
        if release_data:
            remote_ver = release_data.get('tag_name', '')
            
            if remote_ver and remote_ver != local_ver:
                print(f"📢 ACTUALIZACIÓN DETECTADA: {local_ver} -> {remote_ver}")
                
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
                    log(f"⚠️ La release {remote_ver} no contiene el archivo {APP_NAME}.")
            else:
                print(f"✅ Estás en la última versión estable (V {local_ver})")
        
        # Arranque final
        if os.path.exists(APP_NAME):
            print(f"🚀 Lanzando Sentinel Bot...")
            os.startfile(APP_NAME)
            sys.exit(0)
        else:
            print(f"\n❌ ERROR CRÍTICO: No se encuentra {APP_NAME}")
            print("Pista: Si es la primera vez, el lanzador debería haberlo descargado.")
            print("Si el repositorio es PRIVADO, GitHub bloquea la descarga automática.")
            print("\nAcción recomendada: Pon el repositorio en PÚBLICO o descarga el .exe manualmente.")
            input("\nPresiona ENTER para salir...")
    except Exception as e:
        print(f"\n❌ ERROR INESPERADO: {e}")
        input("\nPresiona ENTER para salir...")

if __name__ == "__main__":
    main()
