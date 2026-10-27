"""
API de la flota LLM para la app: ver el universo, activar/desactivar agentes por simbolo y leer
lo que decidio cada uno (prompt, entrada, respuesta, validacion, coste).

La logica vive en PST_LLM/registro.py; aqui solo hay validacion de entrada y llamadas bloqueantes
envueltas en un hilo para no congelar el servidor (arrancar un agente y parar uno tardan).

Seguridad: solo se admiten simbolos cargados en la app (symbols_config), con el universo LLM
(PST_LLM/universo.py) como punto de partida. Un agente nuevo arranca en SIMULACION salvo que el
cliente pida `live`.
"""
import asyncio
import html
import json
import logging
import sys
from datetime import timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_LLM = str(Path(__file__).resolve().parents[1] / "PST_LLM")
if _LLM not in sys.path:
    sys.path.insert(0, _LLM)

import calendario     # noqa: E402  (PST_LLM/calendario.py)
import registro       # noqa: E402  (PST_LLM/registro.py)
import universo       # noqa: E402

router = APIRouter(tags=["LLM"])
logger = logging.getLogger("PST-Calendario")

AVISO_MIN_ANTES = 60        # minutos de antelacion del aviso por Telegram
REVISION_SEG = 60           # cada cuanto se mira si hay un evento que avisar
_avisados = set()           # instantes ya avisados (en memoria: un reinicio dentro de la ventana repite 1 aviso)


@router.get("/api/calendario/proximo")
async def calendario_proximo():
    """Siguiente evento restringido FTMO para la cuenta atras de la cabecera. None si no hay ninguno."""
    return await asyncio.to_thread(calendario.proximo)


def _texto_aviso(t, div, titulo):
    madrid = timezone(timedelta(hours=2))          # CEST, mismo criterio que calendario.py
    inicio = (t - timedelta(minutes=calendario.MINUTOS_ANTES)).astimezone(madrid)
    fin = (t + timedelta(minutes=calendario.MINUTOS_DESPUES)).astimezone(madrid)
    return (f"⏰ <b>Evento restringido {html.escape(div)}</b> en ~{AVISO_MIN_ANTES} min\n"
            f"{html.escape(titulo)}\n"
            f"Sin entradas nuevas de {inicio:%H:%M} a {fin:%H:%M} (hora Madrid)")


async def alertas_calendario():
    """Bucle de fondo: avisa por Telegram antes de cada bloqueo del calendario restringido."""
    from PST_Core.engine.telegram_manager import telegram_bot
    while True:
        try:
            pendientes = await asyncio.to_thread(calendario.a_avisar, AVISO_MIN_ANTES)
            for t, div, titulo in pendientes:
                clave = t.isoformat()
                if clave in _avisados:
                    continue
                _avisados.add(clave)
                await telegram_bot.send_message(_texto_aviso(t, div, titulo))
                logger.info(f"[CALENDARIO] aviso enviado: {div} {titulo} a las {t:%H:%M} UTC")
        except Exception as e:
            logger.error(f"[CALENDARIO] error en la revision de alertas: {e}")
        await asyncio.sleep(REVISION_SEG)


class ActivarRequest(BaseModel):
    live: bool = True         # True = opera en la demo (por defecto). False = simulacion (sin ordenes).


def _validar(symbol):
    if registro._simbolo(symbol) is None:
        raise HTTPException(status_code=400, detail=f"{symbol} no esta cargado en la app")
    return symbol


@router.get("/api/llm/agentes")
async def listar_agentes():
    return await asyncio.to_thread(registro.estado)


@router.get("/api/llm/coste")
async def coste_flota():
    return {"total_usd": await asyncio.to_thread(registro.coste_total)}


@router.get("/api/llm/cuota")
async def cuota_visible():
    """Cuota de Claude (ventana de 5 h y semanal) que escribe el mod de habblaud. Solo lectura."""
    fichero = Path.home() / ".habblaud" / "usage" / ".claude.json"
    try:
        q = json.loads(fichero.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"disponible": False}
    return {
        "disponible": True,
        "cinco_h": (q.get("five_hour") or {}).get("utilization"),
        "cinco_h_reset": (q.get("five_hour") or {}).get("resets_at"),
        "semanal": (q.get("seven_day") or {}).get("utilization"),
        "semanal_reset": (q.get("seven_day") or {}).get("resets_at"),
        "fetched_at": q.get("fetchedAt"),
    }


class RotacionRequest(BaseModel):
    max_activos: int | None = None            # cuantos agentes LLM pueden estar activos a la vez
    max_por_clase: int | None = None          # cuantos por clase de mercado (indice, accion_eeuu...)
    ciclo_min: int | None = None              # cada cuantos minutos se vigilan perdidas y cierres
    cadencia_ranking_min: int | None = None   # cada cuantos minutos se recalculan las propuestas de cambio
    ventana_aprobacion_min: int | None = None  # minutos para responder una propuesta antes de su accion por defecto
    margen_cambio: float | None = None        # cuanto debe puntuar el candidato por encima del peor activo
    max_sustituciones_dia: int | None = None  # sustituciones por perdidas al dia
    penalizacion_cripto: float | None = None  # puntos que pierde cripto en el ranking (menos prioridad para quedarse)
    penalizacion_comision: float | None = None  # puntos que resta un simbolo segun su comision real (tope = penalizacion entera)
    penalizacion_quitar: float | None = None  # puntos que resta un simbolo que quitaste (baja con el tiempo)
    penalizacion_dias: int | None = None      # dias hasta que la penalizacion de quitar llega a cero
    inactividad_min: int | None = None        # minutos sin llamadas al modelo tras los que un agente activo puede salir
    accion_entrada: str | None = None         # sin respuesta en el plazo: aprobar | rechazar | pendiente (no caduca)
    accion_salida: str | None = None          # igual, para las propuestas de salir de un simbolo


@router.get("/api/llm/rotacion")
async def leer_rotacion():
    import rotacion       # noqa: E402  (PST_LLM/rotacion.py)
    lim = await asyncio.to_thread(rotacion._limites)
    activos = sum(1 for a in await asyncio.to_thread(registro.estado) if a["activo"])
    return {**lim, "activos": activos, "universo": len(universo.UNIVERSO)}


@router.put("/api/llm/rotacion")
async def guardar_rotacion(body: RotacionRequest):
    import rotacion       # noqa: E402
    try:
        await asyncio.to_thread(rotacion.guardar_ajustes, body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await leer_rotacion()


class ModeloRequest(BaseModel):
    modelo: str               # haiku | sonnet | opus


@router.post("/api/llm/agentes/{symbol}/modelo")
async def cambiar_modelo_agente(symbol: str, body: ModeloRequest):
    _validar(symbol)
    try:
        return await asyncio.to_thread(registro.cambiar_modelo, symbol, body.modelo)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class TimeframeRequest(BaseModel):
    timeframe: str            # cada cuanto se llama al agente: M1 | M5 | M15 | M30 | H1 | H4 | D1


@router.post("/api/llm/agentes/{symbol}/timeframe")
async def cambiar_timeframe_agente(symbol: str, body: TimeframeRequest):
    _validar(symbol)
    try:
        return await asyncio.to_thread(registro.cambiar_timeframe, symbol, body.timeframe)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class FiltroRequest(BaseModel):
    sin_filtro: bool          # False = solo se llama con setup (ADX/RSI/Bollinger); True = en cada vela


@router.post("/api/llm/agentes/{symbol}/filtro")
async def cambiar_filtro_agente(symbol: str, body: FiltroRequest):
    _validar(symbol)
    try:
        return await asyncio.to_thread(registro.cambiar_filtro, symbol, body.sin_filtro)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class TopeDiarioRequest(BaseModel):
    max_llamadas_dia: int     # llamadas al modelo por dia, para todos los agentes. 0 = sin tope


@router.get("/api/llm/tope-diario")
async def leer_tope_diario():
    return {"max_llamadas_dia": await asyncio.to_thread(registro.leer_tope_diario)}


@router.put("/api/llm/tope-diario")
async def guardar_tope_diario(body: TopeDiarioRequest):
    try:
        return await asyncio.to_thread(registro.guardar_tope_diario, body.max_llamadas_dia)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/llm/agentes/{symbol}/activar")
async def activar_agente(symbol: str, body: ActivarRequest = ActivarRequest()):
    _validar(symbol)
    try:
        return await asyncio.to_thread(registro.activar, symbol, body.live)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"no se pudo activar {symbol}: {e}")


@router.post("/api/llm/agentes/{symbol}/desactivar")
async def desactivar_agente(symbol: str):
    """Excluir: para el agente y lo bloquea. La rotacion no lo vuelve a poner hasta que lo actives a mano."""
    _validar(symbol)
    try:
        return await asyncio.to_thread(registro.desactivar, symbol)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"no se pudo desactivar {symbol}: {e}")


@router.post("/api/llm/agentes/{symbol}/quitar")
async def quitar_agente(symbol: str):
    """Quitar: saca el agente del grupo sin bloquearlo; le resta puntos un tiempo para que no vuelva enseguida."""
    _validar(symbol)
    import rotacion       # noqa: E402
    try:
        return {"mensaje": await asyncio.to_thread(rotacion.quitar_simbolo, symbol, "app")}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/api/llm/agentes/{symbol}/poner")
async def poner_agente(symbol: str):
    """Poner: propone meter este simbolo en el grupo; lo apruebas en la bandeja."""
    _validar(symbol)
    import rotacion       # noqa: E402
    try:
        return {"mensaje": await asyncio.to_thread(rotacion.poner_simbolo, symbol, "app")}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/api/llm/agentes/{symbol}/decisiones")
async def decisiones_agente(symbol: str, limite: int = 50):
    _validar(symbol)
    limite = max(1, min(limite, 500))
    return await asyncio.to_thread(registro.decisiones, symbol, limite)


@router.get("/api/llm/prompts/{version}")
async def prompt_version(version: str):
    fila = await asyncio.to_thread(registro.leer_prompt, version)
    if fila is None:
        raise HTTPException(status_code=404, detail=f"no hay prompt con version {version}")
    return fila


# ── Criterio de entrada por simbolo (PST_LLM/prompts.py) ─────────────────────
import prompts        # noqa: E402  (PST_LLM/prompts.py)


class CriterioRequest(BaseModel):
    texto: str


class MejoraPromptRequest(BaseModel):
    activo: bool | None = None                # el agente de mejora revisa y propone
    min_operaciones: int | None = None        # operaciones cerradas nuevas por simbolo antes de revisarlo
    modelo: str | None = None                 # haiku | sonnet | opus
    esfuerzo: str | None = None               # low | medium | high
    cadencia_min: int | None = None           # cada cuantos minutos mira si algun simbolo toca
    max_caracteres: int | None = None         # longitud maxima del criterio
    max_operaciones_contexto: int | None = None  # operaciones que ve como mucho en cada revision


@router.get("/api/llm/criterio/{symbol}")
async def leer_criterio(symbol: str):
    _validar(symbol)
    act = await asyncio.to_thread(prompts.activo, symbol)
    hist = await asyncio.to_thread(prompts.historial, symbol, 30)
    pend = await asyncio.to_thread(prompts.pendiente_de, symbol)
    return {**act, "historial": hist, "propuesta_pendiente": pend, "defecto_texto": prompts.CRITERIO_DEFECTO,
            "max_caracteres": prompts.ajustes()["max_caracteres"]}


@router.put("/api/llm/criterio/{symbol}")
async def guardar_criterio(symbol: str, body: CriterioRequest):
    _validar(symbol)
    try:
        await asyncio.to_thread(prompts.guardar, symbol, body.texto, "usuario")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await leer_criterio(symbol)


@router.get("/api/llm/mejora-prompt")
async def leer_mejora_prompt():
    return await asyncio.to_thread(prompts.ajustes)


@router.put("/api/llm/mejora-prompt")
async def guardar_mejora_prompt(body: MejoraPromptRequest):
    try:
        return await asyncio.to_thread(prompts.guardar_ajustes, body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
