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


def plantilla(despliegue, inputs=None):
    """Plantilla de grafico minima con el EA cargado y la carpeta de control correcta."""
    entradas = {"InpPstControlDir": rutas.carpeta_control_nombre(), **(inputs or {})}
    lineas_inputs = "\r\n".join(f"{k}={v}" for k, v in entradas.items())
    return ("<chart>\r\n<expert>\r\n"
            f"name={despliegue['id']}\r\n"
            f"path=Experts\\PST_V2\\{despliegue['id']}.ex5\r\n"
            "expertmode=5\r\n"
            f"<inputs>\r\n{lineas_inputs}\r\n</inputs>\r\n"
            "</expert>\r\n</chart>\r\n")


def escribir_plantilla(terminal_datos, despliegue, inputs=None):
    ruta = Path(terminal_datos) / "MQL5" / "Profiles" / "Templates" / "PST_EA" / f"{despliegue['id']}.tpl"
    _escribir_atomico(ruta, plantilla(despliegue, inputs))
    return ruta


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
