"""
Puente PST <-> habblaud (https://github.com/marmottajr/habblaud, MIT).

habblaud es la "oficina virtual" que visualiza las sesiones de Claude Code. Escucha en
127.0.0.1:4747 y su modelo de seguridad bloquea deliberadamente las dos formas obvias de
integrarlo, verificado contra el servidor real el 2026-10-20:

  GET /              -> X-Frame-Options: DENY                     => iframe directo imposible
  GET /api/*         -> Cross-Origin-Resource-Policy: same-origin  => fetch desde React bloqueado
                        (y CORS no implementado a proposito)

De ahi las dos piezas de este modulo, que resuelven cada bloqueo por separado:

1. `router` — proxy JSON montado en la propia API de PST (`/api/habblaud/...`). El fetch lo
   hace el servidor Python, no el navegador, asi que no hay CORS de por medio y el front lo
   consume same-origin con rutas relativas (como ya hace con el resto de axios).

2. `create_mirror_app()` + `start_mirror_in_thread()` — espejo 1:1 en un puerto propio (4748)
   que reenvia todo a habblaud quitando X-Frame-Options. Hace falta un puerto aparte y no un
   subpath porque la pagina de habblaud referencia rutas ABSOLUTAS (`/assets/...`, `/bundle/...`,
   `/manifest.webmanifest`) y `/assets` ya esta ocupado por el bundle de React de PST
   (ver el app.mount de PST_API/main.py): servirla bajo un prefijo le romperia los subrecursos.

SEGURIDAD — el espejo se ata a 127.0.0.1 y nunca a 0.0.0.0. habblaud deja activos terminal,
permisos y mensajes solo cuando la peticion llega con Host loopback; este proxy se lo manda
(IP literal, que su guard.ts acepta), asi que a traves del espejo esas funciones siguen vivas.
Exponerlo a la red seria dar una terminal remota en la maquina que opera la cuenta: no se hace.

Usa aiohttp y no httpx a proposito: aiohttp esta en requirements.txt, httpx solo llega de forma
transitiva y la GitHub Action instala exactamente requirements.txt (el exe se romperia).
"""
import asyncio
import logging
import os
import threading

import aiohttp
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

logger = logging.getLogger("PST-Habblaud")

# ── Configuracion ─────────────────────────────────────────────────────────────
HABBLAUD_URL = os.getenv("HABBLAUD_URL", "http://127.0.0.1:4747").rstrip("/")
MIRROR_HOST = "127.0.0.1"  # NUNCA 0.0.0.0 — ver nota de seguridad arriba
MIRROR_PORT = int(os.getenv("HABBLAUD_MIRROR_PORT", 4748))
TIMEOUT_SECONDS = 5

# Cabeceras que no se reenvian: de framing (lo que venimos a quitar), hop-by-hop
# (las gestiona el servidor nuevo) y de longitud/codificacion (el cuerpo se re-emite).
_STRIPPED_RESPONSE_HEADERS = {
    "x-frame-options",
    "content-security-policy",
    "content-security-policy-report-only",
    "content-length",
    "content-encoding",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "upgrade",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
}

# Rutas de la API de habblaud que el front de PST puede leer. Lista blanca explicita:
# el resto (World, admin, demo) no pinta nada en PST y no hay motivo para exponerlo.
_ALLOWED_API_PREFIXES = (
    "api/health",
    "api/mod/summary",
    "api/snapshot",
    "api/stats",
    "api/sessions/recent",
)


# ── Pieza 1: proxy JSON para el widget ───────────────────────────────────────
router = APIRouter(tags=["Habblaud"])


@router.get("/api/habblaud-info")
async def habblaud_info():
    """
    Donde vive la oficina, para que el front no hardcodee puertos.

    `mirror_url` es lo que la pestaña Oficina mete en el iframe (el espejo sin
    X-Frame-Options, no habblaud directo: ese responde DENY y el iframe saldria en blanco).
    """
    return {
        "mirror_url": f"http://{MIRROR_HOST}:{MIRROR_PORT}",
        "habblaud_url": HABBLAUD_URL,
    }


@router.get("/api/habblaud/{path:path}")
async def habblaud_api_proxy(path: str):
    """
    Reenvia una lectura de la API de habblaud en el origen de PST.

    Devuelve 502/503 con cuerpo JSON estable (`{"habblaud": "offline"}`) cuando habblaud no
    esta corriendo, para que el front pueda pintar "oficina apagada" en vez de romperse: es
    el estado normal cuando el usuario no ha levantado habblaud, no una incidencia.
    """
    clean = path.strip("/")
    if not any(clean == p or clean.startswith(p + "/") for p in _ALLOWED_API_PREFIXES):
        return JSONResponse({"error": "ruta no permitida", "path": clean}, status_code=403)

    url = f"{HABBLAUD_URL}/{clean}"
    try:
        timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Host con IP literal: lo acepta el guard anti-DNS-rebinding de habblaud.
            # Sin Origin: su originAllowed() trata la ausencia como peticion local.
            async with session.get(url, headers={"Host": f"127.0.0.1:{_port_of(HABBLAUD_URL)}"}) as resp:
                body = await resp.read()
                return Response(
                    content=body,
                    status_code=resp.status,
                    media_type=resp.headers.get("Content-Type", "application/json"),
                )
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.debug("habblaud no disponible en %s: %s", url, e)
        return JSONResponse(
            {"habblaud": "offline", "detail": "habblaud no esta corriendo en " + HABBLAUD_URL},
            status_code=503,
        )


def _port_of(base_url: str) -> int:
    tail = base_url.rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else 80


# ── Pieza 2: espejo para el iframe ───────────────────────────────────────────
async def _mirror(request: Request) -> Response:
    """Reenvia cualquier peticion a habblaud quitando las cabeceras de framing."""
    path = request.url.path.lstrip("/")
    url = f"{HABBLAUD_URL}/{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    # Se reenvian solo las cabeceras que habblaud necesita. `accept-encoding: identity`
    # evita tener que recomprimir el cuerpo al re-emitirlo.
    forwarded = {
        "Host": f"127.0.0.1:{_port_of(HABBLAUD_URL)}",
        "Accept": request.headers.get("accept", "*/*"),
        "Accept-Encoding": "identity",
        "User-Agent": request.headers.get("user-agent", "PST-Habblaud-Mirror"),
    }
    for passthrough in ("content-type", "if-none-match", "if-modified-since", "cookie", "last-event-id"):
        value = request.headers.get(passthrough)
        if value:
            forwarded[passthrough.title()] = value

    body = await request.body()
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=TIMEOUT_SECONDS))
    try:
        resp = await session.request(request.method, url, headers=forwarded, data=body or None)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        await session.close()
        logger.debug("espejo: habblaud no disponible (%s)", e)
        return Response(
            content=b"habblaud no esta corriendo. Arrancalo con `npm start` en su carpeta.",
            status_code=503,
            media_type="text/plain; charset=utf-8",
        )

    headers = {k: v for k, v in resp.headers.items() if k.lower() not in _STRIPPED_RESPONSE_HEADERS}

    async def stream():
        """Se emite en trozos para que el SSE de /api/stream funcione a traves del espejo."""
        try:
            async for chunk in resp.content.iter_any():
                yield chunk
        finally:
            resp.release()
            await session.close()

    return StreamingResponse(
        stream(),
        status_code=resp.status,
        headers=headers,
        media_type=resp.headers.get("Content-Type"),
    )


def create_mirror_app() -> Starlette:
    """App ASGI que espeja habblaud en su propio origen, sin X-Frame-Options."""
    return Starlette(
        routes=[
            Route("/{path:path}", _mirror, methods=["GET", "POST", "HEAD", "PUT", "DELETE", "PATCH"]),
        ]
    )


def start_mirror_in_thread() -> None:
    """
    Arranca el espejo en un hilo daemon. Es best-effort: si el puerto esta ocupado o uvicorn
    falla, se registra y PST sigue arrancando — la oficina es un extra, no una dependencia.
    """
    def run():
        try:
            import uvicorn

            uvicorn.run(
                create_mirror_app(),
                host=MIRROR_HOST,
                port=MIRROR_PORT,
                log_level="warning",
            )
        except Exception as e:  # noqa: BLE001 — nunca debe tumbar el arranque de PST
            logger.warning("No se pudo arrancar el espejo de habblaud en %s:%s: %s", MIRROR_HOST, MIRROR_PORT, e)

    threading.Thread(target=run, daemon=True, name="habblaud-mirror").start()
    logger.info("[HABBLAUD] Espejo escuchando en http://%s:%s (origen de habblaud: %s)", MIRROR_HOST, MIRROR_PORT, HABBLAUD_URL)
