"""
Configuracion del agente de scalping (.agents/data/llm_scalping.json). Modulo ligero: lo usan a la vez el
agente (PST_LLM/scalping.py, que la relee en cada vuelta) y la API de la app (PST_API/llm_api.py).

Los simbolos NO estan aqui: cada simbolo elige su estrategia en la BBDD (registro.estrategia_de).
"""
import json

import rutas

DATA = rutas.DATOS / ".agents" / "data"
CONFIG_FILE = DATA / "llm_scalping.json"

DEFECTO = {
    "enabled": True,
    "model": "haiku",
    "effort": "low",
    "live": False,                  # False = simulacion. True = orden real, solo en cuenta DEMO
    "cadencia_s": 60,               # cada cuantos segundos se llama a cada simbolo. 0 = continuo
    "pausa_minima_s": 1,            # pausa minima entre vueltas aunque la cadencia sea 0 o ya se cumpla
    "riesgo_pct": 0.25,             # % de equity arriesgado por operacion (si el simbolo no tiene el suyo)
    "riesgo_por_simbolo": {},       # {simbolo: %} de equity por operacion, por simbolo de scalping
    "max_hold_min": 20,             # tiempo maximo dentro de la operacion; despues se cierra
    "max_abiertas": 2,              # operaciones simultaneas, en todos los simbolos del scalping
    "max_perdida_dia_r": 3.0,       # si el dia suma -R, no se abre nada mas
    "sl_atr": [1.0, 2.0],           # stop en ATR de M1, rango que puede elegir el modelo
    "tp_r": [1.5, 3.0],             # objetivo en R
    "max_spread_sobre_stop": 0.10,  # el spread no puede pasar de esta fraccion de la distancia al stop
    "max_coste_r": 0.10,            # spread + comision ida y vuelta, en fraccion del R
    "max_llamadas_dia": 1500,       # tope propio de este agente (no cuenta el de la flota)
}

# Claves editables desde la app y su rango. Lo que no esta aqui no se puede cambiar desde la API.
RANGOS = {
    "cadencia_s": (0, 600),
    "pausa_minima_s": (1, 60),
    "riesgo_pct": (0.05, 1.0),
    "max_hold_min": (1, 240),
    "max_abiertas": (1, 5),
    "max_perdida_dia_r": (0.5, 20.0),
    "max_spread_sobre_stop": (0.01, 0.5),
    "max_coste_r": (0.01, 0.5),
    "max_llamadas_dia": (0, 100000),
}
BOOLEANAS = ("enabled", "live")
MODELOS = ("haiku", "sonnet", "opus")
ESFUERZOS = ("low", "medium", "high")


def leer():
    """Config efectiva: los valores del fichero sobre los por defecto. Sin fichero, lo crea con los por defecto."""
    cfg = dict(DEFECTO)
    try:
        if CONFIG_FILE.is_file():
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig")))
        else:
            DATA.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(DEFECTO, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[scalping] config ilegible, uso la de por defecto: {e}", flush=True)
    return cfg


def guardar(cambios):
    """Valida y guarda solo las claves editables. Lanza ValueError con el motivo si algo no vale."""
    limpio = {}
    for clave, valor in cambios.items():
        if clave in BOOLEANAS:
            if not isinstance(valor, bool):
                raise ValueError(f"{clave} debe ser true o false")
            limpio[clave] = valor
        elif clave == "model":
            if valor not in MODELOS:
                raise ValueError(f"modelo no valido: {valor} (usa {', '.join(MODELOS)})")
            limpio[clave] = valor
        elif clave == "effort":
            if valor not in ESFUERZOS:
                raise ValueError(f"esfuerzo no valido: {valor} (usa {', '.join(ESFUERZOS)})")
            limpio[clave] = valor
        elif clave == "riesgo_por_simbolo":
            if not isinstance(valor, dict):
                raise ValueError("riesgo_por_simbolo debe ser un objeto {simbolo: %}")
            lo, hi = RANGOS["riesgo_pct"]
            mapa = {}
            for sym, pct in valor.items():
                try:
                    v = float(pct)
                except (TypeError, ValueError):
                    raise ValueError(f"riesgo de {sym} debe ser un numero")
                if not lo <= v <= hi:
                    raise ValueError(f"riesgo de {sym} fuera de rango: usa entre {lo} y {hi}")
                mapa[sym] = round(v, 4)
            limpio[clave] = mapa
        elif clave in RANGOS:
            lo, hi = RANGOS[clave]
            try:
                v = float(valor)
            except (TypeError, ValueError):
                raise ValueError(f"{clave} debe ser un numero")
            if not lo <= v <= hi:
                raise ValueError(f"{clave} fuera de rango: usa entre {lo} y {hi}")
            limpio[clave] = int(v) if clave in ("cadencia_s", "pausa_minima_s", "max_hold_min", "max_abiertas",
                                                 "max_llamadas_dia") else round(v, 4)
        else:
            raise ValueError(f"{clave} no es una clave editable")
    actual = {k: v for k, v in leer().items() if k in DEFECTO}
    actual.update(limpio)
    DATA.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(actual, indent=2), encoding="utf-8")
    return leer()


def riesgo_de(symbol, cfg=None):
    """% de equity por operacion de este simbolo: el suyo si lo tiene, si no el general del scalping."""
    cfg = cfg or leer()
    return float((cfg.get("riesgo_por_simbolo") or {}).get(symbol, cfg["riesgo_pct"]))


def fijar_riesgo_simbolo(symbol, riesgo_pct):
    """Cambia el riesgo de un simbolo de scalping. Lanza ValueError si el valor no vale."""
    try:
        valor = float(riesgo_pct)
    except (TypeError, ValueError):
        raise ValueError("riesgo no valido: debe ser un numero")
    mapa = dict(leer().get("riesgo_por_simbolo") or {})
    mapa[symbol] = valor
    guardar({"riesgo_por_simbolo": mapa})
    return {"symbol": symbol, "riesgo_pct": riesgo_de(symbol)}
