#!/usr/bin/env python
"""
Vigia de logs de la flota: lee los .log y .err de .agents/data y pinta en la terminal, con colores,
errores, avisos, operaciones e info. No avisa por Telegram ni escribe en los logs.

SOLO LECTURA sobre los logs. Lo unico que escribe es su propio estado (vigia_logs_estado.json), que
guarda por donde va en cada fichero: al reiniciar sigue donde se quedo y no repite lo ya pintado.

    python PST_UTIL/vigia_logs.py --datos "C:\\Users\\crist\\Desktop\\PailsSentinelTrade"              # en continuo
    python PST_UTIL/vigia_logs.py --datos "..." --todo                    # incluye tambien la info
    python PST_UTIL/vigia_logs.py --datos "..." --desde-inicio --una-vez  # repasa todo lo escrito y sale
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

VERDE, AMARILLO, ROJO, GRIS, AZUL, RESET = "\033[92m", "\033[93m", "\033[91m", "\033[90m", "\033[94m", "\033[0m"
COLOR = {"ERROR": ROJO, "AVISO": AMARILLO, "OK": VERDE, "INFO": GRIS}

LECTURA_MAX = 1_000_000        # bytes leidos por fichero y trozo: un log enorme nunca se carga entero
TROZOS_MAX = 20                # trozos por fichero y vuelta (tope de lectura por pasada)
ENTRADA_MAX = 4000             # caracteres de una entrada: una linea desbocada no ocupa memoria sin limite

# Orden importa: lo que es error se mira antes que nada (p. ej. "no ejecutada" contiene "ejecutad").
ERROR = re.compile(r"traceback|error|excepci|no se pudo|no encontrado|retcode (?!10009)\d{4,5}", re.I)
AVISO = re.compile(r"no ejecutad|no enviada|rechaz|aviso|warning|sin hueco|freno|tope de|stop detectado|parado", re.I)
OPERACION = re.compile(r"^\[(?P<ts>[^\]]+)\] (?P<accion>BUY|SELL|CLOSE)\b")
HOLD = re.compile(r"^\[[^\]]+\] HOLD\b")
EJECUTADA = re.compile(r"retcode 10009|ejecutad", re.I)


def agrupar(texto):
    """Une las lineas de continuacion (el razonamiento ocupa varias lineas) con la entrada a la que pertenecen."""
    entradas = []
    for linea in texto.splitlines():
        if not linea.strip():
            continue
        if linea.startswith("[") or linea.startswith("Traceback") or not entradas:
            entradas.append(linea.rstrip()[:ENTRADA_MAX])
        elif len(entradas[-1]) < ENTRADA_MAX:
            entradas[-1] = (entradas[-1] + " " + linea.strip())[:ENTRADA_MAX]
    return entradas


def nivel(entrada):
    """(nivel, etiqueta) de una entrada del log."""
    if ERROR.search(entrada):
        return "ERROR", "ERROR"
    m = OPERACION.match(entrada)
    if m:
        etiqueta = {"BUY": "COMPRA", "SELL": "VENTA", "CLOSE": "CIERRE"}[m.group("accion")]
        return ("OK", etiqueta + " ejecutada") if EJECUTADA.search(entrada) else ("AVISO", etiqueta + " sin ejecutar")
    if HOLD.match(entrada):                                    # decision de no operar: normal, no es aviso
        return "INFO", "INFO"
    if AVISO.search(entrada):
        return "AVISO", "AVISO"
    return "INFO", "INFO"


def resultado(entrada):
    """Lo que interesa mostrar: para una operacion, su resultado (lo que va tras el ultimo '|')."""
    if OPERACION.match(entrada) and " | " in entrada:
        return entrada.rsplit(" | ", 1)[-1]
    return entrada


def agente(fichero):
    nombre = fichero.stem.replace("llm_trader_", "")
    return "flota" if nombre == "llm_trader" else nombre


def leer_trozo(fichero, desde, hasta):
    """Lee [desde, hasta) como texto, cortando en el ultimo salto de linea. Devuelve (texto, bytes_consumidos)."""
    with open(fichero, "rb") as f:
        f.seek(desde)
        bruto = f.read(hasta - desde)
    corte = bruto.rfind(b"\n")
    if corte < 0:
        if len(bruto) < LECTURA_MAX:                          # linea a medias: se espera a la siguiente vuelta
            return "", 0
        corte = len(bruto) - 1                                # linea enorme: se corta para no quedarse atascado
    return bruto[:corte + 1].decode("utf-8", errors="replace"), corte + 1


def escanear(carpeta, offsets, desde_inicio, todo):
    for fichero in sorted(carpeta.glob("*.log")) + sorted(carpeta.glob("*.err")):
        try:
            tam = fichero.stat().st_size
        except OSError:
            continue
        clave = fichero.name
        if desde_inicio:
            offsets[clave] = 0
        elif clave not in offsets:
            offsets[clave] = tam                              # primera vez: solo lo que llegue a partir de ahora
        if tam < offsets[clave]:                              # recreado o vaciado: solo su final reciente
            offsets[clave] = max(0, tam - LECTURA_MAX)
        for _ in range(TROZOS_MAX):
            pos = offsets[clave]
            if tam - pos <= 0:
                break
            texto, leido = leer_trozo(fichero, pos, min(tam, pos + LECTURA_MAX))
            if leido == 0:
                break
            offsets[clave] = pos + leido
            for entrada in agrupar(texto):
                niv, etiqueta = nivel(entrada)
                if niv != "INFO" or todo:
                    ahora = datetime.now().strftime("%H:%M:%S")
                    print(f"{GRIS}{ahora}{RESET} {COLOR[niv]}{etiqueta:<18}{RESET} {AZUL}{agente(fichero):<10}{RESET} "
                          f"{resultado(entrada).strip()[:180]}", flush=True)


def cargar(ruta):
    try:
        return json.loads(ruta.read_text(encoding="utf-8")).get("offsets", {})
    except (FileNotFoundError, ValueError, AttributeError):
        return {}


def guardar(ruta, offsets):
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps({"offsets": offsets}, indent=1), encoding="utf-8")
    os.replace(tmp, ruta)


def main():
    ap = argparse.ArgumentParser(description="Vigia de logs de la flota (solo lectura sobre los logs)")
    ap.add_argument("--datos", default=os.environ.get("PST_DATA_ROOT") or str(Path(__file__).resolve().parents[1]),
                    help="raiz de datos (la que contiene .agents/data)")
    ap.add_argument("--cada", type=float, default=15.0, help="segundos entre lecturas (modo continuo)")
    ap.add_argument("--todo", action="store_true", help="mostrar tambien las lineas de info")
    ap.add_argument("--desde-inicio", action="store_true", help="releer los logs desde el principio")
    ap.add_argument("--una-vez", action="store_true", help="leer una vez y salir")
    a = ap.parse_args()

    if os.name == "nt":
        os.system("")                                          # colores ANSI en la consola de Windows
    try:
        sys.stdout.reconfigure(encoding="utf-8")               # los logs llevan acentos
    except Exception:
        pass

    carpeta = Path(a.datos) / ".agents" / "data"
    if not carpeta.is_dir():
        print(f"No encuentro {carpeta}", file=sys.stderr)
        return 1

    ruta_estado = carpeta / "vigia_logs_estado.json"
    offsets = cargar(ruta_estado)
    print(f"Vigilando {carpeta} cada {a.cada:g} s · Ctrl+C para parar", flush=True)
    try:
        while True:
            escanear(carpeta, offsets, a.desde_inicio, a.todo)
            guardar(ruta_estado, offsets)
            if a.una_vez:
                return 0
            time.sleep(a.cada)
    except KeyboardInterrupt:
        print("\nVigilancia parada.", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
