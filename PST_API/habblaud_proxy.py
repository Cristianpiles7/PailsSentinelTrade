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
def _host_is_local(host: str | None) -> bool:
    """¿El Host entrante es loopback? Sin puerto y en minusculas."""
    if not host:
        return False
    name = host.rsplit(":", 1)[0].strip().lower().strip("[]")
    return name == "localhost" or name.endswith(".localhost") or name == "::1" or name.startswith("127.")


async def _mirror(request: Request) -> Response:
    """Reenvia cualquier peticion a habblaud quitando las cabeceras de framing."""
    # GUARD ANTI-DNS-REBINDING. Imprescindible, y su ausencia era un agujero real:
    # habblaud se protege del rebinding validando el Host entrante (guard.ts, hostAllowed),
    # pero este espejo le manda SIEMPRE `Host: 127.0.0.1:4747`, asi que desde su punto de
    # vista toda peticion es local y su guard queda anulado.
    #
    # Sin esta comprobacion: el usuario visita evil.example mientras PST corre; ese dominio
    # resuelve primero a su IP real y, pasado el TTL, a 127.0.0.1. El navegador manda
    # `Host: evil.example:4748`, el espejo lo traduce a loopback, y la pagina -ya
    # same-origin con el espejo tras el rebinding, por lo que no necesita preflight- puede
    # leer /api/snapshot y hacer POST contra la API de habblaud en la maquina que opera la
    # cuenta. El guard lo corta en la puerta del espejo, que es donde habblaud ya no llega.
    if not _host_is_local(request.headers.get("host")):
        logger.warning("[HABBLAUD] Espejo: Host no loopback rechazado (%s)", request.headers.get("host"))
        return Response(content=b"Forbidden", status_code=403, media_type="text/plain")

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


def _is_listening(host: str, port: int, timeout: float = 0.5) -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


_proceso_habblaud = None   # el Popen que lanzo PST (None si habblaud ya corria o no se lanzo)


def _construir_habblaud(habblaud_dir):
    """Primera vez: npm install + npm run build. Corre en un hilo. Si todo va bien, arranca habblaud."""
    import subprocess
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    for comando in ("npm install", "npm run build"):
        logger.info("[HABBLAUD] Ejecutando: %s", comando)
        try:
            r = subprocess.run(comando, cwd=habblaud_dir, shell=True, capture_output=True, text=True,
                               timeout=900, creationflags=creationflags)
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.error("[HABBLAUD] No se pudo ejecutar '%s': %s", comando, e)
            return
        if r.returncode != 0:
            logger.error("[HABBLAUD] Falló '%s': %s", comando, (r.stderr or r.stdout)[-300:])
            return
    logger.info("[HABBLAUD] Build listo: arrancando")
    ensure_habblaud_running()


def ensure_habblaud_running() -> bool:
    """
    Levanta habblaud si no esta escuchando. OPT-IN: solo actua con HABBLAUD_AUTOSTART=1.

    Apagado por defecto a proposito. Lanzar un proceso de Node desde la app que vigila la
    cuenta en cada arranque es invasivo, y en Windows un hijo no muere con el padre: cada
    reinicio de PST dejaria un habblaud huerfano acumulandose. Lo normal es gobernarlo a
    mano con Tools/habblaud.ps1, que ademas da stop/status/logs.

    Devuelve True si al terminar habblaud esta escuchando.
    """
    port = _port_of(HABBLAUD_URL)
    if _is_listening("127.0.0.1", port):
        return True

    if os.getenv("HABBLAUD_AUTOSTART", "0") not in ("1", "true", "True"):
        logger.info("[HABBLAUD] Apagado y autostart desactivado. Para levantarlo: Tools/habblaud.ps1 start")
        return False

    habblaud_dir = os.getenv("HABBLAUD_DIR")
    if not habblaud_dir:
        # Hermana del repo: <...>/PailsSentinelTrade/habblaud, con PST en <...>/PailsSentinelTrade/PailsSentinelTrade
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        habblaud_dir = os.path.join(os.path.dirname(repo_root), "habblaud")

    entry = os.path.join(habblaud_dir, "dist", "server", "index.js")
    if not os.path.isfile(entry):
        if not os.path.isdir(habblaud_dir):
            logger.warning("[HABBLAUD] No existe la carpeta %s", habblaud_dir)
            return False
        # Primera vez en este PC: se construye en segundo plano para no retrasar el arranque de PST.
        logger.warning("[HABBLAUD] Falta el build en %s: lo construyo en segundo plano (npm install + build)", habblaud_dir)
        import threading
        threading.Thread(target=_construir_habblaud, args=(habblaud_dir,), daemon=True).start()
        return False

    import subprocess
    try:
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        global _proceso_habblaud
        _proceso_habblaud = subprocess.Popen(
            ["node", "dist/server/index.js"],
            cwd=habblaud_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except (OSError, ValueError) as e:
        logger.warning("[HABBLAUD] No se pudo lanzar node: %s", e)
        return False

    import time
    for _ in range(20):
        if _is_listening("127.0.0.1", port):
            logger.info("[HABBLAUD] Arrancado automaticamente desde %s", habblaud_dir)
            return True
        time.sleep(0.5)

    logger.warning("[HABBLAUD] Lanzado pero no llego a escuchar en el puerto %s", port)
    return False


def detener_habblaud() -> None:
    """
    Para habblaud si lo levanto PST (autostart). Si ya estaba corriendo antes, no se toca.
    taskkill /T mata tambien los hijos de node.
    """
    proc = _proceso_habblaud
    if proc is None or proc.poll() is not None:
        return
    import subprocess
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                   creationflags=creationflags)
    logger.info("[HABBLAUD] Parado al cerrar PST")


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
