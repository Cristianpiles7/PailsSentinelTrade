"""Rutas de los EAs. Los valores configurables viven en ea_config (store); aqui solo los defectos y derivados."""
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MT5_REPO = REPO / "MT5"
EXPERTS_REPO = MT5_REPO / "Experts"
INCLUDE_REPO = MT5_REPO / "Include"
DESPLIEGUES_JSON = MT5_REPO / "despliegues.json"

APPDATA = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
METAQUOTES = APPDATA / "MetaQuotes" / "Terminal"
# Common\Files es UNO por usuario de Windows y lo comparten todos los terminales (vivo y portable).
COMMON_FILES = METAQUOTES / "Common" / "Files"

ES_DEV = os.environ.get("PST_ENTORNO") == "dev"


def carpeta_control_nombre():
    """Subcarpeta de Common\\Files que leen los EAs (input InpPstControlDir). Pruebas usa otra."""
    return os.environ.get("PST_EA_CONTROL_DIR") or ("PST_EA_DEV" if ES_DEV else "PST_EA")


def carpeta_control():
    return COMMON_FILES / carpeta_control_nombre()


def detectar_terminal_vivo():
    """Carpeta de datos del terminal instalado (no portable). Prefiere el de FTMO."""
    candidatos = []
    if METAQUOTES.exists():
        for d in METAQUOTES.iterdir():
            origen = d / "origin.txt"
            if origen.exists():
                try:
                    texto = origen.read_bytes().decode("utf-16", errors="ignore")
                except Exception:
                    texto = ""
                candidatos.append((0 if "FTMO" in texto else 1, d, texto.strip()))
    candidatos.sort(key=lambda c: c[0])
    return str(candidatos[0][1]) if candidatos else ""


def detectar_instalacion(terminal_datos):
    """Carpeta de instalacion (la de terminal64.exe) a partir de origin.txt de la carpeta de datos."""
    try:
        texto = (Path(terminal_datos) / "origin.txt").read_bytes().decode("utf-16", errors="ignore").strip()
        return texto.strip("\ufeff\x00 ")
    except Exception:
        return r"C:\Program Files\FTMO Global Markets MT5 Terminal"


def nombres_por_magic():
    """magic -> nombre visible del EA (el id sin 'PST_'). Fuente unica: MT5/despliegues.json."""
    import json
    try:
        datos = json.loads(DESPLIEGUES_JSON.read_text(encoding="utf-8"))["despliegues"]
    except (OSError, ValueError, KeyError):
        return {}
    nombres = {int(d["magic"]): d["id"].removeprefix("PST_") for d in datos}
    nombres.setdefault(101801, "LiquiditySweep_EURUSD_M5 (variante retirada)")   # historico, ya no se despliega
    return nombres


def fuente_de(despliegue_id):
    """Ruta del .mq5 del despliegue en el repo (MT5/Experts/<categoria>/<id>.mq5)."""
    for p in EXPERTS_REPO.rglob(f"{despliegue_id}.mq5"):
        return p
    return None
