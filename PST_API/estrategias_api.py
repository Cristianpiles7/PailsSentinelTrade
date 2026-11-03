"""
API de estrategias y de configuracion (Fase 1): una pantalla por estrategia y un unico esquema de parametros.

  GET  /api/estrategias                              estrategias y sus simbolos
  GET  /api/config/esquema                           definicion de cada parametro editable (la app pinta el formulario)
  GET  /api/config/global                            valores globales (Ajustes)
  PUT  /api/config/global                            guarda valores globales
  GET  /api/estrategias/{id}/config                  valores a nivel de estrategia
  PUT  /api/estrategias/{id}/config                  guarda valores a nivel de estrategia
  GET  /api/estrategias/{id}/simbolos/{sym}/config   valores efectivos de un simbolo (con su origen)
  PUT  /api/estrategias/{id}/simbolos/{sym}/config   guarda valores propios de un simbolo
  GET  /api/config/divergencias                      BBDD frente a JSON de espejo (vacio = en orden)
  GET  /api/config/migracion                         informe de lo que importaria la migracion (no escribe)

La logica vive en PST_LLM/config_store.py. Aqui solo hay validacion de forma y llamadas en un hilo.
"""
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_LLM = str(Path(__file__).resolve().parents[1] / "PST_LLM")
if _LLM not in sys.path:
    sys.path.insert(0, _LLM)

import config_store   # noqa: E402  (PST_LLM/config_store.py)
import registro       # noqa: E402  (PST_LLM/registro.py)
import scalping_proceso  # noqa: E402  (PST_LLM/scalping_proceso.py)

router = APIRouter(tags=["Estrategias"])
logger = logging.getLogger("PST-Config")


class CambiosRequest(BaseModel):
    cambios: Dict[str, Any]


def _definicion_publica(d):
    campos = {k: d[k] for k in ("clave", "estrategia", "tipo", "grupo", "etiqueta", "ayuda", "defecto",
                                "min", "max", "paso", "opciones") if k in d}
    campos["ambitos"] = list(d.get("ambitos") or (["simbolo"] if d.get("simbolo") else
                                                  ["global"] if d["estrategia"] == config_store.GLOBAL else ["estrategia"]))
    return campos


def _valores(estrategia, simbolo=""):
    """Lista de {clave, valor, origen} de los parametros de ese ambito. origen: simbolo | estrategia | global | defecto."""
    ambito = config_store.ambito_de(estrategia, simbolo)
    out = []
    vistos = set()
    for d in config_store.ESQUEMA:
        if d["clave"] in vistos or config_store.IDX.get((d["clave"], ambito, d["estrategia"])) is not d:
            continue
        if d["estrategia"] != estrategia:
            continue
        vistos.add(d["clave"])
        valor, nivel = config_store.leer_valor(d["clave"], estrategia, simbolo)
        if valor is None:
            origen, valor = "defecto", d["defecto"]
        elif nivel[1]:
            origen = "simbolo"
        elif nivel[0] == config_store.GLOBAL:
            origen = "global"
        else:
            origen = "estrategia"
        out.append({"clave": d["clave"], "valor": valor, "origen": origen})
    return out


def _simbolos_validos():
    return {s.symbol for s in registro.simbolos_disponibles()}


def _validar_estrategia(estrategia):
    ids = {e["id"] for e in config_store.estrategias_bd()}
    if estrategia not in ids:
        raise HTTPException(status_code=404, detail=f"estrategia desconocida: {estrategia}")


async def _guardar(estrategia, simbolo, body):
    try:
        await asyncio.to_thread(config_store.guardar, estrategia, simbolo, body.cambios, "app")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        # Guardado en BBDD pero el espejo JSON fallo: se avisa, no se oculta
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/estrategias")
async def listar_estrategias():
    def cargar():
        out = []
        for e in config_store.estrategias_bd():
            simbolos = registro.simbolos_con(e["id"])
            out.append({**e, "simbolos": simbolos, "n_simbolos": len(simbolos)})
        return out
    return await asyncio.to_thread(cargar)


@router.get("/api/config/esquema")
async def esquema():
    return [_definicion_publica(d) for d in config_store.ESQUEMA]


@router.get("/api/config/global")
async def leer_global():
    return {"estrategia": config_store.GLOBAL, "valores": await asyncio.to_thread(_valores, config_store.GLOBAL)}


@router.put("/api/config/global")
async def guardar_global(body: CambiosRequest):
    await _guardar(config_store.GLOBAL, "", body)
    return {"estrategia": config_store.GLOBAL, "valores": await asyncio.to_thread(_valores, config_store.GLOBAL)}


@router.get("/api/estrategias/{estrategia}/config")
async def leer_estrategia(estrategia: str):
    _validar_estrategia(estrategia)
    return {"estrategia": estrategia, "valores": await asyncio.to_thread(_valores, estrategia)}


@router.put("/api/estrategias/{estrategia}/config")
async def guardar_estrategia(estrategia: str, body: CambiosRequest):
    _validar_estrategia(estrategia)
    await _guardar(estrategia, "", body)
    return {"estrategia": estrategia, "valores": await asyncio.to_thread(_valores, estrategia)}


def _validar_simbolo(estrategia, symbol):
    _validar_estrategia(estrategia)
    if symbol not in _simbolos_validos():
        raise HTTPException(status_code=404, detail=f"{symbol} no esta en el universo ni cargado en la app")


@router.get("/api/estrategias/{estrategia}/simbolos/{symbol}/config")
async def leer_simbolo(estrategia: str, symbol: str):
    _validar_simbolo(estrategia, symbol)
    return {"estrategia": estrategia, "simbolo": symbol,
            "valores": await asyncio.to_thread(_valores, estrategia, symbol)}


@router.put("/api/estrategias/{estrategia}/simbolos/{symbol}/config")
async def guardar_simbolo(estrategia: str, symbol: str, body: CambiosRequest):
    _validar_simbolo(estrategia, symbol)
    await _guardar(estrategia, symbol, body)
    return {"estrategia": estrategia, "simbolo": symbol,
            "valores": await asyncio.to_thread(_valores, estrategia, symbol)}


@router.get("/api/estrategias/scalping/estado")
async def estado_scalping():
    return await asyncio.to_thread(scalping_proceso.estado)


@router.post("/api/estrategias/scalping/arrancar")
async def arrancar_scalping():
    # La intencion queda guardada: al abrir la app se relanza solo
    await _guardar("scalping", "", CambiosRequest(cambios={"enabled": True}))
    await asyncio.to_thread(scalping_proceso.arrancar)
    return await asyncio.to_thread(scalping_proceso.estado)


@router.post("/api/estrategias/scalping/parar")
async def parar_scalping():
    await _guardar("scalping", "", CambiosRequest(cambios={"enabled": False}))
    await asyncio.to_thread(scalping_proceso.parar)
    return await asyncio.to_thread(scalping_proceso.estado)


@router.get("/api/config/divergencias")
async def divergencias():
    return await asyncio.to_thread(config_store.divergencias)


@router.get("/api/config/migracion")
async def informe_migracion():
    return await asyncio.to_thread(config_store.migrar, False)
