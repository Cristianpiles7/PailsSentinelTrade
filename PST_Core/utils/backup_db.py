"""
Copia de seguridad de la BBDD principal con la API de backup de SQLite. Es en caliente: no hace falta parar la
app y la copia es consistente aunque haya escrituras en curso. Se guardan las ultimas CONSERVAR copias.
"""
import sqlite3
from datetime import datetime
from pathlib import Path

CONSERVAR = 14


def hacer_copia(db_path, carpeta=None, conservar=CONSERVAR):
    origen = Path(db_path)
    carpeta = Path(carpeta) if carpeta else origen.parent / "backups"
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"{origen.stem}_{datetime.now():%Y%m%d_%H%M%S}.db"
    src = sqlite3.connect(origen, timeout=30)
    dst = sqlite3.connect(destino)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    copias = sorted(carpeta.glob(f"{origen.stem}_*.db"))
    for vieja in copias[:-conservar]:
        vieja.unlink()
    return destino


def ultima_copia(db_path, carpeta=None):
    """Ruta de la copia mas reciente, o None si no hay ninguna."""
    origen = Path(db_path)
    carpeta = Path(carpeta) if carpeta else origen.parent / "backups"
    copias = sorted(carpeta.glob(f"{origen.stem}_*.db"))
    return copias[-1] if copias else None
