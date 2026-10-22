#!/usr/bin/env python
"""
Agente PUNTUADOR: da una puntuacion de -100 a +100 por simbolo, para la tarjeta de la home.
Es solo visual: cercania a una zona de compra (+) o de venta (-). NO decide, NO opera y NO es una senal.

DISENO
    * Agente propio: no es ninguno de los agentes de trading ni esta en universo.py.
    * Una sola llamada `claude -p` por ciclo para TODOS los simbolos (no una por simbolo).
    * Sin herramientas, en su propio directorio de trabajo.
    * Configuracion editable desde la app (pestaña Puntuador): modelo, cadencia, activo, topes de cuota.
      Se lee en cada vuelta del bucle, asi que un cambio aplica sin reiniciar.
    * Cada llamada se registra en .agents/data/puntuador_usos.jsonl (gasto y tokens).
    * Arranca con la app (PST_API/main.py -> arrancar()) y para al cerrarla.
    * Salida: .agents/data/puntuador.json, que lee GET /api/symbols.

USO
    python PST_LLM/puntuador.py --once     # una pasada ya, para probar
    python PST_LLM/puntuador.py            # bucle (lo normal es que lo arranque la app)
    Parada: crear .agents/data/puntuador.STOP
"""
import argparse
import importlib.util
import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import llm_trader as lt               # reutiliza el comando `claude -p`, el cwd de los agentes y el parseo

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

NOMBRE = "Puntuador"                  # sala y directorio de trabajo propios (no es un agente de trading)
REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / ".agents" / "data"
SALIDA = DATA_DIR / "puntuador.json"
CONFIG_FILE = DATA_DIR / "puntuador_config.json"
USOS_FILE = DATA_DIR / "puntuador_usos.jsonl"
STOP_FILE = DATA_DIR / "puntuador.STOP"
VENTANA_VELAS = 20                    # M5 cerradas que ve el agente (igual que el sparkline de la home)
ESFUERZO = "low"
MERCADO_CERRADO_S = 1200              # sin ticks nuevos en 20 min = mercado cerrado (como llm_trader)
TICK_S = 15                           # cada cuanto mira el bucle si toca ejecutar (y si cambio la config)
REINICIAR_CADA = 15                   # llamadas por sesion: acota el historial que relee cada llamada

MODELOS = ("haiku", "sonnet", "opus")
DEFECTOS = {"activo": True, "modelo": "haiku", "cadencia_min": 5, "solo_mercado_abierto": True,
            "cuota_max_5h": 60, "cuota_max_semanal": 70}

_detener = threading.Event()          # lo activa parar() al cerrar la app
_estado = {"ultima_utc": None, "proxima_utc": None, "ultimo_error": None, "ultimo_error_utc": None,
           "ultimo_aviso": None}
_hilo = None

SYSTEM_PROMPT = """Eres un medidor de cercania a zonas de entrada para una pantalla. NO decides operaciones,
NO das consejos y NO hablas de ordenes.

Para cada simbolo devuelve "puntuacion", un entero de -100 a +100:
- Positivo (hacia comprar): el precio esta en la zona baja de su rango reciente, sobre todo si cae hacia ella
  o la caida se esta agotando. Cuanto mas baja y mas reciente la caida, mas alto.
- Negativo (hacia vender): el precio esta en la zona alta de su rango reciente, sobre todo si sube hacia ella
  o la subida se esta agotando. Cuanto mas alta y mas reciente la subida, mas bajo.
- Cerca de 0: en medio del rango, o con senales contrapuestas.
Una caida reciente sube la puntuacion; una subida reciente la baja. Usa SOLO los datos del mensaje.

"frase": una frase corta en espanol (maximo 12 palabras) con el motivo.
Devuelve exclusivamente el JSON pedido, con un elemento por cada simbolo del mensaje."""

SCHEMA = {
    "type": "object",
    "properties": {
        "puntuaciones": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "puntuacion": {"type": "integer", "minimum": -100, "maximum": 100},
                    "frase": {"type": "string"},
                },
                "required": ["symbol", "puntuacion", "frase"],
            },
        }
    },
    "required": ["puntuaciones"],
}

_SESION = lt.SesionContinua(prompt=SYSTEM_PROMPT, schema=SCHEMA)     # una sesion abierta, propia del puntuador


def _log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _ahora_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- configuracion

def validar(cfg):
    """Devuelve la configuracion normalizada o lanza ValueError con el motivo."""
    out = {}
    for clave, defecto in DEFECTOS.items():
        valor = cfg.get(clave, defecto)
        if isinstance(defecto, bool):
            if not isinstance(valor, bool):
                raise ValueError(f"{clave} debe ser true o false")
        elif isinstance(defecto, int):
            if isinstance(valor, bool) or not isinstance(valor, int):
                raise ValueError(f"{clave} debe ser un entero")
        out[clave] = valor
    if out["modelo"] not in MODELOS:
        raise ValueError(f"modelo debe ser uno de {MODELOS}")
    if not 1 <= out["cadencia_min"] <= 60:
        raise ValueError("cadencia_min debe estar entre 1 y 60")
    for clave in ("cuota_max_5h", "cuota_max_semanal"):
        if not 10 <= out[clave] <= 100:
            raise ValueError(f"{clave} debe estar entre 10 y 100")
    return out


def leer_config():
    """Configuracion vigente. Si el fichero esta roto, se usan los valores por defecto."""
    try:
        return validar({**DEFECTOS, **json.loads(CONFIG_FILE.read_text(encoding="utf-8"))})
    except FileNotFoundError:
        return dict(DEFECTOS)
    except Exception as e:
        _log(f"config ilegible, uso valores por defecto: {e}")
        return dict(DEFECTOS)


def guardar_config(cambios):
    """Aplica cambios parciales, valida y escribe. Devuelve la configuracion resultante."""
    nueva = validar({**leer_config(), **{k: v for k, v in cambios.items() if v is not None}})
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(nueva, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_FILE)
    return nueva


# ---------------------------------------------------------------- datos de mercado

def simbolos_llm_activos():
    """Solo los simbolos con agente LLM activo (registro de la flota). Asi el prompt es mas corto."""
    import registro
    con = registro.central()
    try:
        return [r[0] for r in con.execute("SELECT symbol FROM llm_agentes WHERE activo = 1 ORDER BY symbol")]
    finally:
        con.close()


def _rsi14(cierres):
    d = np.diff(cierres)
    if len(d) < 14:
        return None
    ganancia = np.where(d > 0, d, 0.0)
    perdida = np.where(d < 0, -d, 0.0)
    g, p = ganancia[:14].mean(), perdida[:14].mean()
    for i in range(14, len(d)):                  # suavizado de Wilder
        g = (g * 13 + ganancia[i]) / 14
        p = (p * 13 + perdida[i]) / 14
    return 100.0 if p == 0 else round(100 - 100 / (1 + g / p), 1)


def estado_simbolo(mt5, symbol, ahora):
    """Hechos del simbolo para el agente. Devuelve None si no hay datos."""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 1, 60)     # 1 = solo velas cerradas
    if rates is None or len(rates) < 20:
        return None
    cierres = rates["close"].astype(float)
    ultimo = float(cierres[-1])
    ventana = cierres[-VENTANA_VELAS:]
    minimo, maximo = ventana.min(), ventana.max()
    tick = mt5.symbol_info_tick(symbol)
    abierto = bool(tick) and (ahora - tick.time) < MERCADO_CERRADO_S
    return {
        "symbol": symbol,
        "mercado_abierto": abierto,
        "precio_ultimo": ultimo,
        "cambio_20v_pct": round((ultimo / ventana[0] - 1) * 100, 3),
        "posicion_en_rango_20v": round(float((ultimo - minimo) / (maximo - minimo)), 3) if maximo > minimo else 0.5,
        "rsi14_m5": _rsi14(cierres),
        # Forma de la serie sin precio absoluto: cada cierre en % respecto al ultimo.
        "cierres_pct_vs_ultimo": [round((c / ultimo - 1) * 100, 3) for c in ventana],
    }


# ---------------------------------------------------------------- salida y registro

def leer_salida():
    try:
        return json.loads(SALIDA.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _escribir_salida(datos):
    tmp = SALIDA.with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SALIDA)                      # escritura atomica: la API nunca lee un fichero a medias


def _registrar_uso(ok, **campos):
    linea = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"), "ok": ok, **campos}
    with open(USOS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(linea, ensure_ascii=False) + "\n")


def gasto(dias=7):
    """Resumen de gasto para la app: hoy, ultimos N dias y las ultimas llamadas."""
    usos = []
    try:
        for linea in USOS_FILE.read_text(encoding="utf-8").splitlines():
            if linea.strip():
                usos.append(json.loads(linea))
    except FileNotFoundError:
        pass
    ahora = datetime.now().astimezone()
    hoy = ahora.date()

    def resumen(lista):
        ok = [u for u in lista if u.get("ok")]
        n = len(ok)
        coste = sum(u.get("cost_usd") or 0 for u in ok)
        return {"llamadas": n, "fallos": len(lista) - n, "coste_usd": round(coste, 4),
                "tokens_in": sum(u.get("tokens_in") or 0 for u in ok),
                "tokens_out": sum(u.get("tokens_out") or 0 for u in ok),
                "coste_medio_usd": round(coste / n, 5) if n else None}

    de_hoy = [u for u in usos if datetime.fromisoformat(u["ts"]).date() == hoy]
    semana = [u for u in usos if (ahora - datetime.fromisoformat(u["ts"])).days < dias]
    return {"hoy": resumen(de_hoy), f"ultimos_{dias}_dias": resumen(semana),
            "ultimas": list(reversed(usos[-10:]))}


def estado():
    return {"hilo_vivo": bool(_hilo and _hilo.is_alive()), **_estado}


# ---------------------------------------------------------------- ejecucion

def _cuota_ok(cfg):
    spec = importlib.util.spec_from_file_location("scheduler", REPO_ROOT / ".agents" / "scheduler.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    q = m.leer_cuota() or {}
    if q.get("cinco_h") is not None and q["cinco_h"] > cfg["cuota_max_5h"]:
        return False, f"ventana de 5h al {q['cinco_h']}% (tope {cfg['cuota_max_5h']}%)"
    if q.get("semanal") is not None and q["semanal"] > cfg["cuota_max_semanal"]:
        return False, f"cuota semanal al {q['semanal']}% (tope {cfg['cuota_max_semanal']}%)"
    return True, ""


def pasada(mt5, cfg):
    """Una vuelta: estado de los simbolos, una llamada al agente y escritura de la salida."""
    ok, motivo = _cuota_ok(cfg)
    if not ok:
        _estado["ultimo_aviso"] = f"no se llama: {motivo}"
        _log(_estado["ultimo_aviso"])
        return
    ahora = time.time()
    estados = [e for e in (estado_simbolo(mt5, s, ahora) for s in simbolos_llm_activos()) if e]
    enviar = [e for e in estados if e["mercado_abierto"]] if cfg["solo_mercado_abierto"] else estados
    if not enviar:
        _estado["ultimo_aviso"] = "todos los mercados cerrados: no se llama"
        _log(_estado["ultimo_aviso"])
        return

    anterior = (leer_salida().get("puntuaciones") or {})
    payload = {"ahora_utc": _ahora_iso(), "simbolos": enviar}
    try:
        # Sesion persistente (como los agentes LLM): Habblaud solo muestra sesiones abiertas.
        resultado, uso = _SESION.preguntar(payload, cfg["modelo"], ESFUERZO, NOMBRE, REINICIAR_CADA)
        if not isinstance(resultado, dict) or not isinstance(resultado.get("puntuaciones"), list):
            raise RuntimeError("respuesta sin la lista 'puntuaciones' (salida estructurada invalida)")
    except Exception as e:
        # Un fallo no borra la ultima puntuacion buena: solo se anota el error.
        _registrar_uso(False, modelo=cfg["modelo"], n_simbolos=len(enviar), error=str(e)[:300])
        previa = leer_salida()
        previa.update(error=str(e)[:500], error_at=_ahora_iso())
        _escribir_salida(previa)
        _estado.update(ultimo_error=str(e)[:300], ultimo_error_utc=_ahora_iso())
        _log(f"error: {e}")
        return

    _registrar_uso(True, modelo=cfg["modelo"], n_simbolos=len(enviar), **uso)

    por_simbolo = {}
    for item in resultado.get("puntuaciones", []):
        sym = item.get("symbol")
        por_simbolo[sym] = {
            "puntuacion": item.get("puntuacion"),
            "anterior": (anterior.get(sym) or {}).get("puntuacion"),
            "frase": item.get("frase", ""),
        }
    # Los simbolos que no se enviaron (mercado cerrado) conservan su ultimo valor, marcados como cerrados.
    for e in estados:
        if e["symbol"] not in por_simbolo:
            previo = anterior.get(e["symbol"]) or {}
            por_simbolo[e["symbol"]] = {"puntuacion": previo.get("puntuacion"), "anterior": previo.get("puntuacion"),
                                        "frase": previo.get("frase", "mercado cerrado")}
        por_simbolo[e["symbol"]]["mercado_abierto"] = e["mercado_abierto"]

    _escribir_salida({"generado": _ahora_iso(), "modelo": cfg["modelo"], "puntuaciones": por_simbolo,
                      "uso": uso, "error": None})
    _estado.update(ultima_utc=_ahora_iso(), ultimo_error=None, ultimo_aviso=None)
    _log(f"{len(por_simbolo)} simbolos puntuados ({len(enviar)} enviados, modelo {cfg['modelo']}), "
         f"{uso['duration_s']}s")


def _iniciar_mt5():
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise RuntimeError(f"MT5 no disponible: {mt5.last_error()}")
    return mt5


def bucle():
    """Bucle del agente. Mira la config cada TICK_S y ejecuta cuando toca. Sale con parar() o STOP."""
    mt5 = None
    ultima_ejecucion = 0.0
    _log(f"{NOMBRE} arrancado")
    try:
        while not _detener.is_set() and not STOP_FILE.exists():
            cfg = leer_config()
            cadencia_s = cfg["cadencia_min"] * 60
            _estado["proxima_utc"] = None if not cfg["activo"] else datetime.fromtimestamp(
                ultima_ejecucion + cadencia_s, timezone.utc).isoformat(timespec="seconds")
            # La sesion queda abierta todo el tiempo que el agente esta activo (asi se ve fija en Habblaud);
            # en pausa se cierra. Sin mensajes no gasta cuota: solo existe el proceso en espera.
            if cfg["activo"]:
                try:
                    _SESION.asegurar(cfg["modelo"], ESFUERZO, NOMBRE)
                except Exception as e:
                    _log(f"no se pudo abrir la sesion: {e}")
            else:
                _SESION.cerrar()
            if cfg["activo"] and time.time() - ultima_ejecucion >= cadencia_s:
                ultima_ejecucion = time.time()
                try:
                    if mt5 is None:
                        mt5 = _iniciar_mt5()
                    pasada(mt5, cfg)
                except Exception as e:
                    _estado.update(ultimo_error=str(e)[:300], ultimo_error_utc=_ahora_iso())
                    _log(f"pasada fallida: {e}")
                    if mt5 is not None:
                        mt5.shutdown()
                        mt5 = None
            _detener.wait(TICK_S)
    finally:
        _SESION.cerrar()
        if mt5 is not None:
            mt5.shutdown()
        _log(f"{NOMBRE} parado")


def arrancar():
    """Lanza el agente en un hilo daemon. Lo llama la app al iniciarse. Idempotente."""
    global _hilo
    STOP_FILE.unlink(missing_ok=True)
    _detener.clear()
    if _hilo and _hilo.is_alive():
        return
    _hilo = threading.Thread(target=bucle, name="puntuador", daemon=True)
    _hilo.start()


def parar():
    """Lo llama la app al cerrarse."""
    _detener.set()


def main():
    ap = argparse.ArgumentParser(description="Agente puntuador de la home (solo visual)")
    ap.add_argument("--once", action="store_true", help="una pasada y salir")
    args = ap.parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if args.once:
        try:
            mt5 = _iniciar_mt5()
        except RuntimeError as e:
            print(e, file=sys.stderr)
            return 1
        try:
            pasada(mt5, leer_config())
        finally:
            _SESION.cerrar()
            mt5.shutdown()
        return 0
    bucle()
    return 0


if __name__ == "__main__":
    sys.exit(main())
