"""
Entorno de pruebas de PST: arranca la app desde el codigo del repo, sin release, sin tag y sin exe.

Convive con el PST_MASTER.exe de produccion sin pisarlo:
  * Datos propios en PST_DEV_ROOT (por defecto C:\\Users\\<usuario>\\PST_DEV): copia de la BBDD y de
    las configs de los agentes. Nunca escribe en la BBDD de produccion.
  * Otro puerto (8100 por defecto). Produccion sigue en el 8000.
  * Sin ordenes: MetaTrader5.order_send queda bloqueado (salvo --permitir-ordenes).
  * Sin agentes: no lanza procesos de PST_LLM/.agents ni puede matar los de produccion.
  * Sin Telegram, sin proteccion de sesion (cierra posiciones reales) y sin habblaud.

Uso (desde repo/):
    python Tools/dev/arrancar_dev.py                 # API + panel ya compilado en http://127.0.0.1:8100
    python Tools/dev/arrancar_dev.py --web           # ademas Vite con recarga en caliente en http://127.0.0.1:5174
    python Tools/dev/arrancar_dev.py --refrescar-bd  # vuelve a copiar la BBDD de produccion antes de arrancar
    python Tools/dev/arrancar_dev.py --mt5-tester    # conecta al terminal portable de pruebas, no al vivo

Detalle: manual_docs/ENTORNO_PRUEBAS.md
"""
import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROD_ROOT = REPO.parent                                   # C:\Users\crist\Desktop\PailsSentinelTrade
PROD_DB = PROD_ROOT / "PST_Core" / "data" / "pst_trading.db"
PROD_AGENTES = PROD_ROOT / ".agents" / "data"
DEV_ROOT = Path(os.environ.get("PST_DEV_ROOT") or Path.home() / "PST_DEV")
DEV_DB = DEV_ROOT / "PST_Core" / "data" / "pst_trading.db"
DEV_AGENTES = DEV_ROOT / ".agents" / "data"
MT5_TESTER = Path(os.environ.get("PST_MT5_TESTER") or Path.home() / "PST_MT5_Tester") / "terminal64.exe"

# Procesos que el entorno de pruebas no puede lanzar: agentes que operan y el actualizador.
_PROHIBIDOS = ("PST_LLM", ".agents", "PST_LAUNCHER", "PST_MASTER.exe")


for _s in (sys.stdout, sys.stderr):      # la consola de Windows es cp1252: los emojis del log rompen el print
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def log(msg):
    try:
        print(f"[DEV] {msg}", flush=True)
    except Exception:
        pass


def copiar_bd(forzar):
    if DEV_DB.exists() and not forzar:
        log(f"BBDD de pruebas: {DEV_DB} (usa --refrescar-bd para copiarla de nuevo)")
        return
    if not PROD_DB.exists():
        sys.exit(f"[DEV] No encuentro la BBDD de produccion en {PROD_DB}")
    DEV_DB.parent.mkdir(parents=True, exist_ok=True)
    log(f"Copiando BBDD de produccion -> {DEV_DB} (solo lectura sobre produccion)...")
    origen = sqlite3.connect(f"file:{PROD_DB.as_posix()}?mode=ro", uri=True)
    tmp = DEV_DB.with_suffix(".db.tmp")
    destino = sqlite3.connect(tmp)
    with destino:
        origen.backup(destino)            # copia consistente aunque la app de produccion este escribiendo
    destino.close()
    origen.close()
    os.replace(tmp, DEV_DB)
    log(f"BBDD copiada ({DEV_DB.stat().st_size / 1e6:.0f} MB)")


def copiar_configs_agentes(forzar):
    """Solo los .json (configs y estado): sin .db, logs ni ficheros STOP de produccion."""
    DEV_AGENTES.mkdir(parents=True, exist_ok=True)
    if not PROD_AGENTES.exists():
        return
    n = 0
    for f in PROD_AGENTES.glob("*.json"):
        d = DEV_AGENTES / f.name
        if forzar or not d.exists():
            shutil.copy2(f, d)
            n += 1
    if n:
        log(f"{n} configs de agentes copiadas a {DEV_AGENTES}")


def preparar_entorno(puerto, mt5_tester):
    os.environ["PST_ENTORNO"] = "dev"
    os.environ["PST_DATA_ROOT"] = str(DEV_ROOT)
    os.environ["DB_PATH"] = str(DEV_DB)
    os.environ["API_PORT"] = str(puerto)
    os.environ["HABBLAUD_AUTOSTART"] = "0"
    os.environ["HABBLAUD_MIRROR_PORT"] = str(puerto + 1)
    os.environ["TELEGRAM_BOT_TOKEN"] = ""          # vacio: el .env no la pisa (override=False en dev)
    os.environ["TELEGRAM_CHAT_ID"] = ""
    if mt5_tester:
        if not MT5_TESTER.exists():
            sys.exit(f"[DEV] No encuentro el terminal de pruebas en {MT5_TESTER}")
        os.environ["PST_MT5_PATH"] = str(MT5_TESTER)


def instalar_protecciones(permitir_ordenes):
    import MetaTrader5 as mt5
    import psutil

    # 1) MT5: terminal elegido y ordenes bloqueadas
    ruta = os.environ.get("PST_MT5_PATH")
    if ruta:
        _init = mt5.initialize

        def initialize(*a, **k):
            if not a and "path" not in k:
                k["path"], k["portable"] = ruta, True
            return _init(*a, **k)
        mt5.initialize = initialize
        log(f"MT5: terminal de pruebas {ruta}")
    else:
        log("MT5: terminal vivo (solo lectura)")

    if not permitir_ordenes:
        def order_send(request, *a, **k):
            log(f"⛔ order_send BLOQUEADO en pruebas: {request}")
            return None
        mt5.order_send = order_send
        log("Ordenes: BLOQUEADAS")
    else:
        log("⚠️  Ordenes: PERMITIDAS (--permitir-ordenes)")

    # 2) No lanzar agentes ni el actualizador
    _Popen = subprocess.Popen

    class PopenVigilado(_Popen):
        def __init__(self, args, *a, **k):
            texto = " ".join(map(str, args)) if isinstance(args, (list, tuple)) else str(args)
            if any(p in texto for p in _PROHIBIDOS):
                log(f"⛔ Proceso BLOQUEADO en pruebas: {texto[:200]}")
                raise PermissionError("Entorno de pruebas: no se lanzan agentes ni el actualizador")
            super().__init__(args, *a, **k)
    subprocess.Popen = PopenVigilado

    # 3) No matar procesos que no sean nuestros (los agentes de produccion se buscan por nombre)
    yo = os.getpid()

    def _es_mio(proc):
        try:
            return any(p.pid == yo for p in proc.parents())
        except Exception:
            return False

    for metodo in ("kill", "terminate"):
        original = getattr(psutil.Process, metodo)

        def vigilado(self, _orig=original, _m=metodo):
            if not _es_mio(self):
                log(f"⛔ {_m}({self.pid}) BLOQUEADO: no es un proceso del entorno de pruebas")
                raise psutil.AccessDenied(self.pid)
            return _orig(self)
        setattr(psutil.Process, metodo, vigilado)


def lanzar_vite(puerto_api, puerto_web):
    web = REPO / "PST_Web"
    env = {**os.environ, "VITE_API_URL": f"http://127.0.0.1:{puerto_api}/api"}
    npm = shutil.which("npm") or "npm"
    log(f"Vite (recarga en caliente): http://127.0.0.1:{puerto_web}")
    return subprocess.Popen([npm, "run", "dev", "--", "--host", "127.0.0.1", "--port", str(puerto_web), "--strictPort"],
                            cwd=str(web), env=env, shell=os.name == "nt")


def main():
    ap = argparse.ArgumentParser(description="Entorno de pruebas de PST")
    ap.add_argument("--puerto", type=int, default=8100)
    ap.add_argument("--web", action="store_true", help="lanzar tambien Vite con recarga en caliente")
    ap.add_argument("--puerto-web", type=int, default=5174)
    ap.add_argument("--refrescar-bd", action="store_true", help="copiar de nuevo la BBDD y configs de produccion")
    ap.add_argument("--mt5-tester", action="store_true", help="usar el terminal portable de pruebas")
    ap.add_argument("--permitir-ordenes", action="store_true", help="NO bloquear order_send (cuenta demo)")
    a = ap.parse_args()

    if a.puerto == 8000:
        sys.exit("[DEV] El 8000 es el puerto de produccion. Usa otro.")

    copiar_bd(a.refrescar_bd)
    copiar_configs_agentes(a.refrescar_bd)
    preparar_entorno(a.puerto, a.mt5_tester)

    vite = lanzar_vite(a.puerto, a.puerto_web) if a.web else None   # antes de las protecciones
    instalar_protecciones(a.permitir_ordenes)

    os.chdir(REPO)
    sys.path.insert(0, str(REPO))
    import uvicorn
    from PST_API.main import app

    log(f"API + panel: http://127.0.0.1:{a.puerto}   (Ctrl+C para parar)")
    try:
        uvicorn.run(app, host="127.0.0.1", port=a.puerto, log_level="info", access_log=False)
    finally:
        if vite:
            vite.terminate()


if __name__ == "__main__":
    main()
