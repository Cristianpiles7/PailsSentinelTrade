"""
Configuracion del agente de scalping (.agents/data/llm_scalping.json). Modulo ligero: lo usan a la vez el
agente (PST_LLM/scalping.py, que la relee en cada vuelta) y la API de la app (PST_API/llm_api.py).

Los simbolos NO estan aqui: cada simbolo elige su estrategia en la BBDD (registro.estrategia_de).
"""
import json

import rutas

import config_store

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
    "tp_r": [2.0, 3.0],             # objetivo en R de los setups de tendencia (minimo 2: con 1,5 el acierto necesario era 40 %, y se acierta 25-33 %)
    "tp_r_reversion": [1.2, 2.0],   # objetivo en R de reversion y rechazo_nivel: en rango el recorrido es corto (necesita mas acierto)
    "cooldown_tras_stop_min": 10,   # tras un stop, minutos sin entrar en ese simbolo
    "max_spread_sobre_stop": 0.10,  # el spread no puede pasar de esta fraccion de la distancia al stop
    "max_coste_r": 0.10,            # spread + comision ida y vuelta, en fraccion del R
    "confianza_minima": 0.6,        # no se abre si el modelo da menos confianza que esto
    "be_r": 1.0,                    # al llegar a este R el stop pasa a la entrada (break-even). 0 = no se mueve
    "umbral_llamada_atr": 0.3,      # sin posicion, se llama al modelo si el precio se movio este ATR desde la ultima llamada
    "max_llamada_s": 300,           # sin posicion, como minimo se llama cada tantos segundos aunque no se mueva
    "cadencia_vigilancia_s": 10,    # cada cuantos segundos se vigila lo abierto y las sombras (sin llamar al modelo)
    "max_perdidas_seguidas": 3,     # tras tantas perdidas seguidas en un simbolo, no se entra en el ese dia. 0 = sin tope
    "bloquear_misma_direccion": True,  # no abrir el mismo sentido en dos simbolos a la vez (los indices se mueven juntos)
}

# Claves editables desde la app y su rango. Lo que no esta aqui no se puede cambiar desde la API.
# Rangos: salen del esquema de config_store (un solo sitio). Solo los numericos que se editan.
RANGOS = {k: config_store.rango(k, "scalping") for k in (
    "cadencia_s", "pausa_minima_s", "riesgo_pct", "max_hold_min", "max_abiertas", "max_perdida_dia_r",
    "cooldown_tras_stop_min", "max_spread_sobre_stop", "max_coste_r")}
BOOLEANAS = ("enabled", "live")
MODELOS = ("haiku", "sonnet", "opus")
ESFUERZOS = ("low", "medium", "high")


def leer():
    """Config efectiva: JSON sobre los por defecto y, por encima, lo guardado en la BBDD (config_store).
    Sin fichero, lo crea con los por defecto."""
    cfg = dict(DEFECTO)
    try:
        if CONFIG_FILE.is_file():
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig")))
        else:
            DATA.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(DEFECTO, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[scalping] config ilegible, uso la de por defecto: {e}", flush=True)
    cfg.update(config_store.overlay("scalping"))
    por_simbolo = dict(cfg.get("riesgo_por_simbolo") or {})
    for sym, vals in config_store.overlay_simbolos("scalping").items():
        if "riesgo_pct" in vals:
            por_simbolo[sym] = vals["riesgo_pct"]
    cfg["riesgo_por_simbolo"] = por_simbolo
    return cfg


def guardar(cambios):
    """Valida y guarda en la BBDD (config_store), que refleja el cambio en el JSON. Lanza ValueError con el motivo."""
    if not isinstance(cambios, dict):
        raise ValueError("el cambio debe ser un objeto {clave: valor}")
    generales = {}
    por_simbolo = {}
    for clave, valor in cambios.items():
        if clave == "riesgo_por_simbolo":
            if not isinstance(valor, dict):
                raise ValueError("riesgo_por_simbolo debe ser un objeto {simbolo: %}")
            por_simbolo = valor
        else:
            generales[clave] = valor
    d = config_store.IDX[("riesgo_pct", "simbolo", "scalping")]
    for sym, pct in por_simbolo.items():
        config_store.normalizar(d, pct)          # valida antes de escribir nada
    if generales:
        config_store.guardar("scalping", "", generales)
    for sym, pct in por_simbolo.items():
        config_store.guardar("scalping", sym, {"riesgo_pct": pct})
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
    config_store.guardar("scalping", symbol, {"riesgo_pct": valor})
    return {"symbol": symbol, "riesgo_pct": riesgo_de(symbol)}
