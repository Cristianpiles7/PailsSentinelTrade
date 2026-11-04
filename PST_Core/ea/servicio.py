"""
Acciones sobre los EAs. Unico punto de entrada para la app (PST_API/eas_api.py) y los agentes (PST_MCP).

`origen` dice quien actua: "app" (el usuario desde el panel) o "agente:<nombre>". Los agentes solo pueden
hacer sin aprobacion lo que diga ea_config.agentes_pueden (por defecto pausar, compilar y backtest).
Todo queda en ea_eventos.
"""
import time
from datetime import date

from . import control, mt5cli, rutas, store


class Prohibido(PermissionError):
    pass


def _permiso(accion, origen):
    if origen.startswith("agente"):
        if accion not in store.config().get("agentes_pueden", []):
            raise Prohibido(f"un agente no puede '{accion}' sin aprobacion (ea_config.agentes_pueden). "
                            "Propon el cambio y que lo apruebe el usuario en la app.")


def _desp(despliegue_id):
    d = store.obtener(despliegue_id)
    if not d:
        raise KeyError(f"despliegue desconocido: {despliegue_id}")
    return d


# ------------------------------------------------------------------ terminales
def terminal_ejecucion():
    """(carpeta de datos, carpeta de instalacion) del terminal donde corren los EAs.

    En el entorno de pruebas es el portable: nunca se instala nada en el terminal vivo desde pruebas.
    """
    cfg = store.config()
    if rutas.ES_DEV:
        return cfg["tester_dir"], cfg["tester_dir"]
    datos = cfg["terminal_vivo_dir"] or rutas.detectar_terminal_vivo()
    instalacion = cfg["terminal_vivo_instalacion"] or rutas.detectar_instalacion(datos)
    return datos, instalacion


def terminal_tester():
    d = store.config()["tester_dir"]
    return d, d


# ------------------------------------------------------------------ estado
def sincronizar():
    """Reescribe control.csv y despliegue.csv desde la BBDD. Se llama tras cada cambio."""
    despliegues = store.listar()
    control.escribir_control(despliegues, store.config()["interruptor_global"])
    control.escribir_despliegue(despliegues)


def resumen():
    """Todo lo que pinta la seccion EAs: despliegues + latido + gestor + ultimo backtest + config."""
    despliegues = store.listar()
    latidos = control.leer_latidos()
    gestor = control.leer_gestor()
    ultimos = {}
    for b in store.backtests(limite=500):
        ultimos.setdefault(b["despliegue_id"], b)
    for d in despliegues:
        d["latido"] = latidos.get(d["magic"])
        d["gestor"] = gestor["despliegues"].get(d["id"])
        d["ultimo_backtest"] = ultimos.get(d["id"])
        d["fuente"] = str(rutas.fuente_de(d["id"]) or "")
    datos, instalacion = terminal_ejecucion()
    return {
        "despliegues": despliegues,
        "config": store.config(),
        "gestor": {k: v for k, v in gestor.items() if k != "despliegues"},
        "entorno": "dev" if rutas.ES_DEV else "prod",
        "carpeta_control": str(rutas.carpeta_control()),
        "terminal_ejecucion": {"datos": datos, "instalacion": instalacion},
    }


# ------------------------------------------------------------------ interruptores
def interruptor_global(activo, origen="app"):
    _permiso("activar" if activo else "pausar", origen)
    store.guardar_config({"interruptor_global": bool(activo)})
    store.evento(None, "global_on" if activo else "global_off", "", origen)
    sincronizar()


def activar(despliegue_id, origen="app"):
    _permiso("activar", origen)
    d = _desp(despliegue_id)
    if d["estado"] == "retirada":
        raise ValueError("un despliegue retirado no se activa; cambia antes su estado")
    nuevo = "activa" if d["estado"] in ("pausada", "activa") else d["estado"]
    store.actualizar(despliegue_id, activo=1, estado=nuevo)
    store.evento(despliegue_id, "activar", "", origen)
    sincronizar()


def pausar(despliegue_id, motivo="", origen="app"):
    _permiso("pausar", origen)
    d = _desp(despliegue_id)
    nuevo = "pausada" if d["estado"] in ("activa", "pausada") else d["estado"]
    store.actualizar(despliegue_id, activo=0, estado=nuevo)
    store.evento(despliegue_id, "pausar", motivo, origen)
    sincronizar()


def fijar_riesgo(despliegue_id, mult, origen="app"):
    _permiso("riesgo", origen)
    _desp(despliegue_id)
    tope = float(store.config()["mult_riesgo_max"])
    mult = float(mult)
    if not 0 <= mult <= tope:
        raise ValueError(f"multiplicador fuera de rango [0, {tope}]")
    store.actualizar(despliegue_id, mult_riesgo=mult)
    store.evento(despliegue_id, "riesgo", f"x{mult:.2f}", origen)
    sincronizar()


def cerrar_posiciones(despliegue_id, origen="app"):
    _permiso("cerrar", origen)
    _desp(despliegue_id)
    token = int(time.time())
    store.actualizar(despliegue_id, cerrar_token=token)
    store.evento(despliegue_id, "cerrar_posiciones", f"token {token}", origen)
    sincronizar()
    return token


def cambiar_estado(despliegue_id, estado, origen="app"):
    if origen != "app":
        raise Prohibido("el estado del ciclo de vida solo lo cambia el usuario")
    if estado not in store.ESTADOS:
        raise ValueError(f"estado no valido: {estado}")
    _desp(despliegue_id)
    campos = {"estado": estado}
    if estado in ("retirada", "pausada"):
        campos["activo"] = 0
    store.actualizar(despliegue_id, **campos)
    store.evento(despliegue_id, "estado", estado, origen)
    sincronizar()


# ------------------------------------------------------------------ instalar en el terminal
def instalar(despliegue_id, origen="app"):
    """Compila en el terminal de ejecucion, escribe su plantilla y pide al gestor que lo ponga en un grafico.

    No lo activa: entra pausado. Activar es otro paso.
    """
    _permiso("instalar", origen)
    d = _desp(despliegue_id)
    fuente = rutas.fuente_de(despliegue_id)
    if not fuente:
        raise FileNotFoundError(f"no hay fuente {despliegue_id}.mq5 en MT5/Experts")
    datos, instalacion = terminal_ejecucion()
    res = mt5cli.compilar(datos, instalacion, fuente)
    if not res["ok"]:
        store.evento(despliegue_id, "instalar_fallo", f"compilacion: {res['errores']} errores", origen)
        return {"ok": False, "compilacion": res}
    control.escribir_plantilla(datos, d)
    store.actualizar(despliegue_id, instalar=1)
    store.evento(despliegue_id, "instalar", f"compilado en {datos}", origen)
    sincronizar()
    return {"ok": True, "compilacion": res}


def retirar(despliegue_id, origen="app"):
    """Pide al gestor que quite el EA de su grafico. Lo pausa antes."""
    _permiso("retirar", origen)
    _desp(despliegue_id)
    store.actualizar(despliegue_id, instalar=0, activo=0)
    store.evento(despliegue_id, "retirar", "", origen)
    sincronizar()


def orden_gestor(orden, origen="app"):
    """cerrar_sin_ea: cierra los graficos sin EA del terminal. reaplicar: estilo/inputs de nuevo en los graficos."""
    if origen != "app":
        raise Prohibido("las ordenes al gestor solo las da el usuario")
    if orden == "reaplicar":                      # plantillas al dia antes de reaplicarlas
        datos, _ = terminal_ejecucion()
        for d in store.listar():
            if d["instalar"]:
                control.escribir_plantilla(datos, d)
    token = control.escribir_orden_gestor(orden)
    store.evento(None, f"gestor_{orden}", f"token {token}", origen)
    return {"token": token}


# ------------------------------------------------------------------ compilar y backtest
def compilar(despliegue_id, origen="app"):
    """Compila en el terminal de PRUEBAS (portable). No toca el terminal vivo."""
    _permiso("compilar", origen)
    _desp(despliegue_id)
    fuente = rutas.fuente_de(despliegue_id)
    if not fuente:
        raise FileNotFoundError(f"no hay fuente {despliegue_id}.mq5 en MT5/Experts")
    datos, instalacion = terminal_tester()
    res = mt5cli.compilar(datos, instalacion, fuente)
    store.evento(despliegue_id, "compilar", f"{res['errores']} errores, {res['avisos']} avisos", origen)
    return res


def backtest(despliegue_id, desde, hasta, modelo=None, tramo="completo", origen="app", compilar_antes=True,
             parametros=None):
    """parametros: inputs del EA para esta pasada, p. ej. {"InpRiskPercent": 0.5}. Quedan guardados en metricas."""
    _permiso("backtest", origen)
    d = _desp(despliegue_id)
    cfg = store.config()
    if compilar_antes:
        res = compilar(despliegue_id, origen=origen if origen == "app" else origen)
        if not res["ok"]:
            return {"ok": False, "compilacion": res}
    modelo = cfg["tester_modelo"] if modelo is None else modelo
    r = mt5cli.backtest(cfg["tester_dir"], d, desde, hasta, modelo=modelo, deposito=cfg["tester_deposito"],
                        divisa=cfg["tester_divisa"], apalancamiento=cfg["tester_apalancamiento"],
                        login=cfg["tester_login"], server=cfg["tester_server"], timeout_s=cfg["tester_timeout_s"],
                        inputs={**({"InpMaxTotalLossAmount": 1000000} if cfg.get("tester_sin_freno_total", True) else {}),
                                **(parametros or {})} or None)
    m = r["metricas"]
    if parametros:
        m["parametros"] = parametros
    fila = {"ts": time.time(), "despliegue_id": despliegue_id, "version": d["version"], "simbolo": d["simbolo"],
            "tf": d["tf"], "desde": desde, "hasta": hasta, "modelo": modelo, "tramo": tramo,
            "neto": m.get("neto"), "pf": m.get("pf"), "sharpe": m.get("sharpe"), "ops": m.get("ops"),
            "dd_equity_pct": m.get("dd_equity_pct"), "calidad_pct": m.get("calidad_pct"),
            "informe": r["informe"], "metricas": __import__("json").dumps(m), "origen": origen}
    fila["id"] = store.guardar_backtest(fila)
    store.evento(despliegue_id, "backtest",
                 f"{tramo} {desde}->{hasta}: neto {m.get('neto')} PF {m.get('pf')} ops {m.get('ops')}", origen)
    return {"ok": True, "backtest": fila, "segundos": r["segundos"]}


def evaluar(filas):
    """Compara backtests con los criterios de ea_config. Devuelve {aprobado, motivos}."""
    cfg = store.config()
    motivos = []
    total = next((f for f in filas if f.get("tramo") == "completo"), filas[0] if filas else None)
    if not total:
        return {"aprobado": False, "motivos": ["sin backtests"]}
    if (total.get("pf") or 0) < cfg["criterio_pf_min"]:
        motivos.append(f"PF {total.get('pf')} < {cfg['criterio_pf_min']}")
    if (total.get("sharpe") or 0) < cfg["criterio_sharpe_min"]:
        motivos.append(f"Sharpe {total.get('sharpe')} < {cfg['criterio_sharpe_min']}")
    if (total.get("ops") or 0) < cfg["criterio_ops_min"]:
        motivos.append(f"{total.get('ops')} operaciones < {cfg['criterio_ops_min']}")
    if (total.get("dd_equity_pct") or 0) > cfg["criterio_dd_max_pct"]:
        motivos.append(f"DD {total.get('dd_equity_pct')}% > {cfg['criterio_dd_max_pct']}%")
    if cfg["criterio_is_oos_positivos"]:
        for f in filas:
            if f.get("tramo") in ("IS", "OOS") and (f.get("neto") or 0) <= 0:
                motivos.append(f"tramo {f['tramo']} no positivo (neto {f.get('neto')})")
    return {"aprobado": not motivos, "motivos": motivos}


def backtest_is_oos(despliegue_id, desde, hasta, modelo=None, origen="app", parametros=None):
    """Periodo completo + mitad dentro de muestra (IS) + mitad fuera (OOS), y veredicto con los criterios."""
    d0, d1 = date.fromisoformat(desde), date.fromisoformat(hasta)
    medio = (d0 + (d1 - d0) / 2).isoformat()
    filas = []
    for i, (tramo, a, b) in enumerate((("completo", desde, hasta), ("IS", desde, medio), ("OOS", medio, hasta))):
        r = backtest(despliegue_id, a, b, modelo=modelo, tramo=tramo, origen=origen, compilar_antes=(i == 0),
                     parametros=parametros)
        if not r["ok"]:
            return r
        filas.append(r["backtest"])
    veredicto = evaluar(filas)
    store.evento(despliegue_id, "veredicto", ("APROBADO" if veredicto["aprobado"] else "NO aprobado: ")
                 + "; ".join(veredicto["motivos"]), origen)
    return {"ok": True, "backtests": filas, "veredicto": veredicto}


# ------------------------------------------------------------------ alta de un EA nuevo
_TF = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
MAGIC_MIN, MAGIC_MAX = 100000, 108999      # 109xxx es de la flota LLM


def validar_alta(datos):
    """Reglas de MT5/DESARROLLO_EAS.md: un EA = estrategia x simbolo x TF; nombre sin codigos de hipotesis."""
    import re
    errores = []
    i, sim, tf, magic = datos["id"], datos["simbolo"], datos["tf"], int(datos["magic"])
    if not re.fullmatch(r"PST_[A-Za-z0-9]+_[A-Za-z0-9]+_(M1|M5|M15|M30|H1|H4|D1)", i):
        errores.append("id debe ser PST_<Estrategia>_<SIMBOLO>_<TF> (solo letras y numeros en cada parte)")
    if re.match(r"PST_H\d+b?_", i) or re.search(r"_H\d+b?_", i):
        errores.append("el nombre no puede llevar codigos de hipotesis (H5, H25...)")
    if tf not in _TF or not i.endswith(f"_{tf}"):
        errores.append(f"el id debe terminar en _{tf} y el TF ser uno de {_TF}")
    if sim.split(".")[0].upper() not in i.upper():
        errores.append(f"el id debe contener el simbolo ({sim.split('.')[0]})")
    if not MAGIC_MIN <= magic <= MAGIC_MAX:
        errores.append(f"magic fuera de rango [{MAGIC_MIN}, {MAGIC_MAX}] (109xxx es de la flota LLM)")
    for d in store.listar():
        if d["id"] == i:
            errores.append("ya existe un EA con ese id")
        if int(d["magic"]) == magic:
            errores.append(f"magic {magic} ya lo usa {d['id']}")
        if (d["estado"] != "retirada" and d["simbolo"] == sim and d["tf"] == tf
                and d["estrategia"].strip().lower() == datos["estrategia"].strip().lower()):
            errores.append(f"ya hay un EA de esa estrategia en {sim} {tf}: {d['id']} (mejoralo con una version nueva)")
    fuente = rutas.EXPERTS_REPO / datos["categoria"] / f"{i}.mq5"
    if not fuente.exists():
        errores.append(f"no existe {fuente.relative_to(rutas.REPO)}")
    else:
        src = fuente.read_text(encoding="utf-8", errors="ignore")
        if "PST_Control.mqh" not in src:
            errores.append("el .mq5 no incluye PST_Control.mqh")
        if f'#define PST_DESPLIEGUE_SIMBOLO "{sim}"' not in src:
            errores.append(f'falta #define PST_DESPLIEGUE_SIMBOLO "{sim}"')
        if f"PERIOD_{tf}" not in src:
            errores.append(f"falta #define PST_DESPLIEGUE_TF PERIOD_{tf}")
        if str(magic) not in src:
            errores.append(f"el magic {magic} no aparece en el .mq5 (InpMagicNumber)")
        if "PST_Control_PuedeAbrir()" not in src:
            errores.append("el EA no consulta PST_Control_PuedeAbrir() antes de abrir")
    return errores


def alta(datos, origen="app"):
    """Registra un EA nuevo (estado experimental, pausado) en la BBDD y en MT5/despliegues.json."""
    import json
    errores = validar_alta(datos)
    if errores:
        return {"ok": False, "errores": errores}
    nuevo = {"id": datos["id"], "estrategia": datos["estrategia"], "categoria": datos["categoria"],
             "simbolo": datos["simbolo"], "tf": datos["tf"], "magic": int(datos["magic"]), "version": "1.0",
             "estado_inicial": "experimental", "origen": f"alta por {origen}"}
    contenido = json.loads(rutas.DESPLIEGUES_JSON.read_text(encoding="utf-8"))
    contenido["despliegues"].append(nuevo)
    rutas.DESPLIEGUES_JSON.write_text(json.dumps(contenido, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    store.inicializar()                       # da de alta lo nuevo del json
    store.actualizar(nuevo["id"], estado="experimental")
    store.evento(nuevo["id"], "alta_ea", f"{nuevo['estrategia']} {nuevo['simbolo']} {nuevo['tf']} magic {nuevo['magic']}", origen)
    sincronizar()
    return {"ok": True, "despliegue": store.obtener(nuevo["id"])}
