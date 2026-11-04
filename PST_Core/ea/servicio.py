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


def backtest(despliegue_id, desde, hasta, modelo=None, tramo="completo", origen="app", compilar_antes=True):
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
                        login=cfg["tester_login"], server=cfg["tester_server"], timeout_s=cfg["tester_timeout_s"])
    m = r["metricas"]
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


def backtest_is_oos(despliegue_id, desde, hasta, modelo=None, origen="app"):
    """Periodo completo + mitad dentro de muestra (IS) + mitad fuera (OOS), y veredicto con los criterios."""
    d0, d1 = date.fromisoformat(desde), date.fromisoformat(hasta)
    medio = (d0 + (d1 - d0) / 2).isoformat()
    filas = []
    for i, (tramo, a, b) in enumerate((("completo", desde, hasta), ("IS", desde, medio), ("OOS", medio, hasta))):
        r = backtest(despliegue_id, a, b, modelo=modelo, tramo=tramo, origen=origen, compilar_antes=(i == 0))
        if not r["ok"]:
            return r
        filas.append(r["backtest"])
    veredicto = evaluar(filas)
    store.evento(despliegue_id, "veredicto", ("APROBADO" if veredicto["aprobado"] else "NO aprobado: ")
                 + "; ".join(veredicto["motivos"]), origen)
    return {"ok": True, "backtests": filas, "veredicto": veredicto}
