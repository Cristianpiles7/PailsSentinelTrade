"""
Ficheros de intercambio con MT5 (en Common\\Files\\<carpeta de control>, ver rutas.carpeta_control):

  control.csv          lo escribe la app; cada EA lo lee cada 5 s (MT5/Include/PST_Control.mqh)
  despliegue.csv       lo escribe la app; el gestor lo lee (MT5/Experts/Sistema/PST_Gestor.mq5)
  estado_<magic>.csv   latido de cada EA
  gestor_estado.csv    lo que ve el gestor en el terminal

Las plantillas de grafico (una por EA, con sus inputs) van a <terminal>\\MQL5\\Profiles\\Templates\\PST_EA\\.
"""
import os
import time
from pathlib import Path

from . import rutas

LATIDO_VIVO_S = 30          # sin latido en este tiempo = EA no esta corriendo


def _escribir_atomico(ruta: Path, texto: str):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(texto, encoding="ascii", errors="replace", newline="")
    for intento in range(20):              # el EA puede tener el fichero abierto un instante
        try:
            os.replace(tmp, ruta)
            return
        except PermissionError:
            time.sleep(0.1)
    raise PermissionError(f"no se pudo escribir {ruta} (bloqueado)")


def escribir_control(despliegues, interruptor_global):
    """control.csv: fila 0 = interruptor global, luego una por EA."""
    lineas = ["magic;activo;mult_riesgo;cerrar_token", f"0;{1 if interruptor_global else 0};1.0;0"]
    for d in despliegues:
        activo = 1 if d["activo"] and d["estado"] not in ("retirada",) else 0
        lineas.append(f"{d['magic']};{activo};{float(d['mult_riesgo']):.2f};{int(d['cerrar_token'] or 0)}")
    _escribir_atomico(rutas.carpeta_control() / "control.csv", "\r\n".join(lineas) + "\r\n")


def escribir_despliegue(despliegues):
    """despliegue.csv para el gestor: que EAs deben estar en un grafico."""
    lineas = ["id;simbolo;tf;instalar"]
    for d in despliegues:
        lineas.append(f"{d['id']};{d['simbolo']};{d['tf']};{1 if d['instalar'] else 0}")
    _escribir_atomico(rutas.carpeta_control() / "despliegue.csv", "\r\n".join(lineas) + "\r\n")


# Estilo de los graficos del usuario (copiado de su perfil el 2026-11-04): velas, fondo negro, sin cuadricula,
# alcistas en verde y bajistas en rojo. Colores en formato MT5 (BGR decimal): 0 negro, 65280 verde, 255 rojo.
ESTILO_GRAFICO = {
    "mode": 1, "grid": 0, "fore": 0, "volume": 0, "ohlc": 0, "bidline": 1, "askline": 0,
    "background_color": 0, "foreground_color": 16777215, "barup_color": 65280, "bardown_color": 255,
    "bullcandle_color": 65280, "bearcandle_color": 255, "chartline_color": 65280, "volumes_color": 3329330,
    "grid_color": 10061943, "bidline_color": 10061943, "askline_color": 255, "stops_color": 255,
}


def plantilla(despliegue, inputs=None):
    """Plantilla de grafico: estilo del usuario + el EA con la carpeta de control correcta."""
    entradas = {"InpPstControlDir": rutas.carpeta_control_nombre(), **(inputs or {})}
    if rutas.ES_DEV:
        # En pruebas el EA nunca abre operaciones. Ni AllowLiveTrading=0 del terminal ni expertmode de la
        # plantilla le quitan el permiso de forma fiable (probado 2026-11-04): el bloqueo va en el propio EA.
        entradas["InpPstBloquearAperturas"] = "true"
    lineas_inputs = "\r\n".join(f"{k}={v}" for k, v in entradas.items())
    estilo = "".join(f"{k}={v}\r\n" for k, v in ESTILO_GRAFICO.items())
    return ("<chart>\r\n" + estilo + "<expert>\r\n"
            f"name={despliegue['id']}\r\n"
            f"path=Experts\\PST_V2\\{despliegue['id']}.ex5\r\n"
            "expertmode=5\r\n"
            f"<inputs>\r\n{lineas_inputs}\r\n</inputs>\r\n"
            "</expert>\r\n</chart>\r\n")


def plantilla_gestor():
    """Plantilla del propio gestor: se la aplica a si mismo para quedar guardado en el perfil del terminal."""
    estilo = "".join(f"{k}={v}\r\n" for k, v in ESTILO_GRAFICO.items())
    return ("<chart>\r\n" + estilo + "<expert>\r\nname=PST_Gestor\r\npath=Experts\\PST_V2\\PST_Gestor.ex5\r\n"
            "expertmode=5\r\n<inputs>\r\n"
            f"InpPstControlDir={rutas.carpeta_control_nombre()}\r\nInpIntervaloSeg=10\r\nInpPersistente=true\r\n"
            "</inputs>\r\n</expert>\r\n</chart>\r\n")


def escribir_plantilla_gestor(terminal_datos):
    ruta = Path(terminal_datos) / "MQL5" / "Profiles" / "Templates" / "PST_EA" / "PST_Gestor.tpl"
    _escribir_atomico(ruta, plantilla_gestor())
    return ruta


def escribir_plantilla(terminal_datos, despliegue, inputs=None):
    ruta = Path(terminal_datos) / "MQL5" / "Profiles" / "Templates" / "PST_EA" / f"{despliegue['id']}.tpl"
    _escribir_atomico(ruta, plantilla(despliegue, inputs))
    return ruta


ORDENES_GESTOR = ("cerrar_sin_ea", "reaplicar")


def escribir_orden_gestor(orden):
    """Orden para el gestor (se ejecuta una vez por token). Conserva el ultimo token de cada orden."""
    if orden not in ORDENES_GESTOR:
        raise ValueError(f"orden desconocida: {orden}")
    ruta = rutas.carpeta_control() / "gestor_ordenes.csv"
    tokens = {}
    if ruta.exists():
        for linea in ruta.read_text(encoding="latin-1").splitlines()[1:]:
            if ";" in linea:
                o, t = linea.split(";", 1)
                tokens[o.strip()] = t.strip()
    tokens[orden] = str(int(time.time()))
    _escribir_atomico(ruta, "orden;token\r\n" + "".join(f"{o};{t}\r\n" for o, t in tokens.items()))
    return tokens[orden]


def _leer_clave_valor(ruta: Path):
    datos = {}
    for linea in ruta.read_text(encoding="latin-1").splitlines()[1:]:
        if ";" in linea:
            k, v = linea.split(";", 1)
            datos[k.strip()] = v.strip()
    return datos


def leer_latidos():
    """magic -> latido del EA, con `edad_s` y `vivo`."""
    salida = {}
    carpeta = rutas.carpeta_control()
    if not carpeta.exists():
        return salida
    ahora = time.time()
    for f in carpeta.glob("estado_*.csv"):
        try:
            datos = _leer_clave_valor(f)
            edad = ahora - f.stat().st_mtime
        except (OSError, ValueError):
            continue
        datos["edad_s"] = round(edad, 1)
        datos["vivo"] = edad < LATIDO_VIVO_S and not datos.get("estado", "").startswith("parado")
        try:
            salida[int(datos.get("magic", f.stem.split("_")[-1]))] = datos
        except ValueError:
            continue
    return salida


def leer_gestor():
    """Estado que publica el gestor: {'vivo', 'edad_s', 'cuenta', 'algo_trading', 'despliegues': {id: {...}}}."""
    ruta = rutas.carpeta_control() / "gestor_estado.csv"
    if not ruta.exists():
        return {"vivo": False, "edad_s": None, "despliegues": {}}
    edad = time.time() - ruta.stat().st_mtime
    salida = {"vivo": edad < 60, "edad_s": round(edad, 1), "despliegues": {}}
    try:
        for linea in ruta.read_text(encoding="latin-1").splitlines()[1:]:
            partes = linea.split(";")
            if linea.startswith("#gestor") and len(partes) >= 4:
                salida.update({"hora": partes[1], "cuenta": partes[2], "algo_trading": partes[3] == "algo_on",
                               "terminal": partes[4] if len(partes) > 4 else ""})
            elif len(partes) >= 5:
                salida["despliegues"][partes[0]] = {"instalar": partes[1] == "1", "grafico": partes[2],
                                                    "cargado": partes[3] == "1", "error": partes[4]}
    except OSError:
        pass
    return salida
