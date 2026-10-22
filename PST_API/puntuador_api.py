"""
API del agente puntuador (PST_LLM/puntuador.py): configuracion, estado y gasto para la pestaña Puntuador.

La logica vive en el agente; aqui solo hay validacion de entrada. La configuracion se lee en cada
vuelta del bucle, asi que un PUT aplica en menos de 15 s sin reiniciar la app.
"""
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_LLM = str(Path(__file__).resolve().parents[1] / "PST_LLM")
if _LLM not in sys.path:
    sys.path.insert(0, _LLM)

import puntuador      # noqa: E402  (PST_LLM/puntuador.py)

router = APIRouter(tags=["Puntuador"])


class PuntuadorConfigUpdate(BaseModel):
    activo: Optional[bool] = None
    modelo: Optional[str] = None
    cadencia_min: Optional[int] = None
    solo_mercado_abierto: Optional[bool] = None
    cuota_max_5h: Optional[int] = None
    cuota_max_semanal: Optional[int] = None


@router.get("/api/puntuador")
async def leer_puntuador():
    """Configuracion, estado del agente, gasto (hoy, 7 dias y ultimas llamadas)."""
    return {
        "config": puntuador.leer_config(),
        "estado": puntuador.estado(),
        "gasto": puntuador.gasto(),
    }


@router.put("/api/puntuador/config")
async def guardar_puntuador(body: PuntuadorConfigUpdate):
    try:
        cfg = puntuador.guardar_config(body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"config": cfg}
