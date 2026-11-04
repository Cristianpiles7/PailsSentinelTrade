"""
API de la seccion EAs (rama feature/eas-mt5). Toda la logica esta en PST_Core/ea/servicio.py; aqui solo
validacion, hilos y trabajos en segundo plano (compilar y backtest tardan de segundos a minutos).

  GET  /api/eas                          despliegues + latidos + gestor + configuracion
  GET  /api/eas/eventos                  historial global      GET /api/eas/{id}/eventos
  GET  /api/eas/{id}/backtests           backtests guardados
  POST /api/eas/global                   {"activo": bool}  interruptor de todos los EAs
  POST /api/eas/{id}/{accion}            activar | pausar | cerrar | instalar | retirar
  PUT  /api/eas/{id}/riesgo              {"mult": float}
  PUT  /api/eas/{id}/estado              {"estado": "..."}
  PUT  /api/eas/config                   {"cambios": {...}}
  POST /api/eas/{id}/compilar            -> trabajo
  POST /api/eas/{id}/backtest            {"desde", "hasta", "modelo"?, "is_oos"?} -> trabajo
  GET  /api/eas/trabajos[/{tid}]         estado de los trabajos
"""
import asyncio
import logging
import threading
import time
import uuid
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from PST_Core.ea import mt5cli, planificador, servicio, store

router = APIRouter(prefix="/api/eas", tags=["EAs"])
logger = logging.getLogger("PST-EAs")

_trabajos: Dict[str, dict] = {}
_trabajos_lock = threading.Lock()


class GlobalRequest(BaseModel):
    activo: bool


class RiesgoRequest(BaseModel):
    mult: float


class EstadoRequest(BaseModel):
    estado: str


class ConfigRequest(BaseModel):
    cambios: Dict[str, Any]


class BacktestRequest(BaseModel):
    desde: str
    hasta: str
    modelo: Optional[int] = None
    is_oos: bool = True


def _http(e):
    if isinstance(e, servicio.Prohibido):
        return HTTPException(403, str(e))
    if isinstance(e, KeyError):
        return HTTPException(404, str(e).strip("'\""))
    if isinstance(e, (ValueError, FileNotFoundError, mt5cli.ErrorMT5)):
        return HTTPException(400, str(e))
    logger.exception("Error en la API de EAs")
    return HTTPException(500, f"{type(e).__name__}: {e}")


async def _hilo(func, *a, **k):
    try:
        return await asyncio.to_thread(func, *a, **k)
    except HTTPException:
        raise
    except Exception as e:
        raise _http(e)


def _lanzar_trabajo(tipo, despliegue_id, func, *a, **k):
    with _trabajos_lock:
        if any(t["estado"] == "en_curso" and t["tipo"] in ("backtest", "compilar") for t in _trabajos.values()):
            raise HTTPException(409, "ya hay una compilacion o backtest en marcha (el terminal de pruebas es uno)")
        tid = uuid.uuid4().hex[:10]
        _trabajos[tid] = {"id": tid, "tipo": tipo, "despliegue_id": despliegue_id, "estado": "en_curso",
                          "inicio": time.time(), "fin": None, "resultado": None, "error": None}

    def correr():
        try:
            res = func(*a, **k)
            _trabajos[tid].update(estado="ok" if res.get("ok", True) else "fallo", resultado=res)
        except Exception as e:
            logger.exception("Trabajo %s (%s) fallido", tid, tipo)
            _trabajos[tid].update(estado="error", error=f"{type(e).__name__}: {e}")
        finally:
            _trabajos[tid]["fin"] = time.time()

    threading.Thread(target=correr, daemon=True, name=f"ea-{tipo}-{tid}").start()
    return _trabajos[tid]


# ------------------------------------------------------------------ lectura
@router.get("")
async def resumen():
    return await _hilo(servicio.resumen)


@router.get("/eventos")
async def eventos_globales(limite: int = 200):
    return await _hilo(store.eventos, None, limite)


@router.get("/planificador")
async def estado_planificador():
    return await _hilo(planificador.estado)


@router.post("/planificador/lanzar")
async def lanzar_planificador():
    from PST_Core.ea.rutas import ES_DEV
    if ES_DEV:
        raise HTTPException(400, "en el entorno de pruebas no se lanzan agentes")
    r = await _hilo(planificador.lanzar, "manual desde la app")
    if not r["ok"]:
        raise HTTPException(409, r["error"])
    return r


@router.get("/trabajos")
async def trabajos():
    return sorted(_trabajos.values(), key=lambda t: -t["inicio"])[:30]


@router.get("/trabajos/{tid}")
async def trabajo(tid: str):
    if tid not in _trabajos:
        raise HTTPException(404, "trabajo desconocido")
    return _trabajos[tid]


@router.get("/{despliegue_id}/eventos")
async def eventos(despliegue_id: str, limite: int = 100):
    return await _hilo(store.eventos, despliegue_id, limite)


@router.get("/{despliegue_id}/backtests")
async def backtests(despliegue_id: str):
    return await _hilo(store.backtests, despliegue_id)


# ------------------------------------------------------------------ escritura
@router.post("/gestor/{orden}")
async def orden_gestor(orden: str):
    return {"ok": True, **(await _hilo(servicio.orden_gestor, orden, "app"))}


@router.post("/global")
async def global_(body: GlobalRequest):
    await _hilo(servicio.interruptor_global, body.activo, "app")
    return {"ok": True, "activo": body.activo}


@router.put("/config")
async def guardar_config(body: ConfigRequest):
    await _hilo(store.guardar_config, body.cambios)
    store.evento(None, "config", ", ".join(f"{k}={v}" for k, v in body.cambios.items()), "app")
    await _hilo(servicio.sincronizar)
    return {"ok": True, "config": store.config()}


_ACCIONES = {
    "activar": lambda i: servicio.activar(i, origen="app"),
    "pausar": lambda i: servicio.pausar(i, origen="app"),
    "cerrar": lambda i: {"token": servicio.cerrar_posiciones(i, origen="app")},
    "retirar": lambda i: servicio.retirar(i, origen="app"),
}


@router.post("/{despliegue_id}/compilar")
async def compilar(despliegue_id: str):
    if not store.obtener(despliegue_id):
        raise HTTPException(404, "despliegue desconocido")
    return _lanzar_trabajo("compilar", despliegue_id, servicio.compilar, despliegue_id, origen="app")


@router.post("/{despliegue_id}/instalar")
async def instalar(despliegue_id: str):
    if not store.obtener(despliegue_id):
        raise HTTPException(404, "despliegue desconocido")
    return _lanzar_trabajo("instalar", despliegue_id, servicio.instalar, despliegue_id, origen="app")


@router.post("/{despliegue_id}/backtest")
async def backtest(despliegue_id: str, body: BacktestRequest):
    if not store.obtener(despliegue_id):
        raise HTTPException(404, "despliegue desconocido")
    try:
        if date.fromisoformat(body.desde) >= date.fromisoformat(body.hasta):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "fechas no validas (YYYY-MM-DD, desde < hasta)")
    if body.is_oos:
        return _lanzar_trabajo("backtest", despliegue_id, servicio.backtest_is_oos, despliegue_id, body.desde,
                               body.hasta, modelo=body.modelo, origen="app")
    return _lanzar_trabajo("backtest", despliegue_id, servicio.backtest, despliegue_id, body.desde, body.hasta,
                           modelo=body.modelo, origen="app")


@router.post("/{despliegue_id}/{accion}")
async def accion(despliegue_id: str, accion: str):
    if accion not in _ACCIONES:
        raise HTTPException(404, f"accion desconocida: {accion}")
    res = await _hilo(_ACCIONES[accion], despliegue_id)
    return {"ok": True, **(res or {})}


@router.put("/{despliegue_id}/riesgo")
async def riesgo(despliegue_id: str, body: RiesgoRequest):
    await _hilo(servicio.fijar_riesgo, despliegue_id, body.mult, "app")
    return {"ok": True}


@router.put("/{despliegue_id}/estado")
async def estado(despliegue_id: str, body: EstadoRequest):
    await _hilo(servicio.cambiar_estado, despliegue_id, body.estado, "app")
    return {"ok": True}
