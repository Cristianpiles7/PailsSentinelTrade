"""
MetaTrader por linea de comandos (probado el 2026-11-04, ver MT5/DESARROLLO_EAS.md §4):

  compilar   MetaEditor64.exe /compile:<mq5> /inc:<carpeta MQL5> /log:<log>   (log en UTF-16)
  backtest   terminal64.exe /portable /config:<ini>  en el terminal PORTABLE de pruebas. Arranca, prueba,
             deja el informe .htm y se cierra (ShutdownTerminal=1). Nunca en el terminal vivo: el vivo no
             puede lanzar el tester por CLI y reiniciarlo pararia los EAs que operan.

Un solo backtest a la vez (el portable no admite dos instancias): lo garantiza un fichero de bloqueo.
"""
import html
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import rutas

TF_VALIDOS = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1")


class ErrorMT5(RuntimeError):
    pass


# ------------------------------------------------------------------ instalar fuentes en un terminal
def copiar_includes(terminal_datos):
    destino = Path(terminal_datos) / "MQL5" / "Include" / "PST_V2"
    destino.mkdir(parents=True, exist_ok=True)
    for f in rutas.INCLUDE_REPO.glob("*.mqh"):
        shutil.copy2(f, destino / f.name)


def copiar_fuente(terminal_datos, fuente: Path):
    destino = Path(terminal_datos) / "MQL5" / "Experts" / "PST_V2" / fuente.name
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(fuente, destino)
    return destino


def _metaeditor(instalacion):
    for nombre in ("MetaEditor64.exe", "metaeditor64.exe"):
        p = Path(instalacion) / nombre
        if p.exists():
            return p
    raise ErrorMT5(f"no encuentro MetaEditor64.exe en {instalacion}")


def compilar(terminal_datos, instalacion, fuente: Path):
    """Copia includes + fuente al terminal y compila. Devuelve {ok, errores, avisos, ex5, log}."""
    fuente = Path(fuente)
    if not fuente.exists():
        raise ErrorMT5(f"no existe {fuente}")
    copiar_includes(terminal_datos)
    mq5 = copiar_fuente(terminal_datos, fuente)
    log = mq5.with_suffix(".compile.log")
    log.unlink(missing_ok=True)
    subprocess.run([str(_metaeditor(instalacion)), f"/compile:{mq5}", f"/inc:{Path(terminal_datos) / 'MQL5'}",
                    f"/log:{log}"], capture_output=True, timeout=300)
    texto = ""
    if log.exists():
        raw = log.read_bytes()
        texto = raw.decode("utf-16", errors="ignore") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "ignore")
    m = re.search(r"Result:\s*(\d+)\s*errors?,\s*(\d+)\s*warnings?", texto)
    errores, avisos = (int(m.group(1)), int(m.group(2))) if m else (None, None)
    ex5 = mq5.with_suffix(".ex5")
    lineas_error = [l.strip() for l in texto.splitlines() if " error " in l or ": error" in l]
    return {"ok": errores == 0 and ex5.exists(), "errores": errores, "avisos": avisos, "ex5": str(ex5),
            "detalle_errores": lineas_error[:30], "log": "\n".join(texto.splitlines()[-15:])}


# ------------------------------------------------------------------ backtest
def _leer_informe(ruta: Path):
    raw = ruta.read_bytes()
    texto = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "ignore")
    celdas = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
              for c in re.findall(r"<td[^>]*>(.*?)</td>", texto, re.S)]
    pares = {}
    for i, c in enumerate(celdas[:-1]):
        if c.endswith(":") and celdas[i + 1]:
            pares.setdefault(c[:-1].strip(), celdas[i + 1])
    return pares


def _num(texto):
    """'10 000.00' -> 10000.0 ; '2.80% (288.02)' -> 2.80 ; '17' -> 17.0"""
    if texto is None:
        return None
    m = re.search(r"-?\d[\d\s]*(?:[.,]\d+)?", texto.replace("\xa0", " "))
    if not m:
        return None
    return float(m.group(0).replace(" ", "").replace(",", "."))


# Etiquetas del informe en espanol (idioma del terminal) e ingles por si se cambia.
_ETIQUETAS = {
    "neto": ("Beneficio Neto", "Total Net Profit"),
    "pf": ("Factor de Beneficio", "Profit Factor"),
    "sharpe": ("Ratio de Sharpe", "Sharpe Ratio"),
    "ops": ("Total de operaciones ejecutadas", "Total Trades"),
    "dd_equity_pct": ("Reducción relativa de la equidad", "Equity Drawdown Relative"),
    "calidad_pct": ("Calidad del historial", "History Quality"),
    "esperado": ("Beneficio Esperado", "Expected Payoff"),
    "recuperacion": ("Factor de Recuperación", "Recovery Factor"),
}


def metricas_de_informe(ruta: Path):
    pares = _leer_informe(ruta)
    m = {}
    for clave, etiquetas in _ETIQUETAS.items():
        for e in etiquetas:
            if e in pares:
                m[clave] = _num(pares[e])
                break
        else:
            m[clave] = None
    if m.get("ops") is not None:
        m["ops"] = int(m["ops"])
    return m, pares


def backtest(tester_dir, despliegue, desde, hasta, modelo=1, deposito=10000, divisa="EUR", apalancamiento=100,
             login="", server="", inputs=None, timeout_s=1800):
    """Lanza un backtest en el terminal portable y devuelve {metricas, informe, segundos}.

    desde/hasta: 'YYYY-MM-DD'. El .ex5 tiene que estar compilado en ese terminal (compilar() antes).
    """
    tester_dir = Path(tester_dir)
    terminal = tester_dir / "terminal64.exe"
    if not terminal.exists():
        raise ErrorMT5(f"no encuentro el terminal de pruebas en {terminal}")
    ex5 = tester_dir / "MQL5" / "Experts" / "PST_V2" / f"{despliegue['id']}.ex5"
    if not ex5.exists():
        raise ErrorMT5(f"{ex5.name} no esta compilado en el terminal de pruebas")
    if despliegue["tf"] not in TF_VALIDOS:
        raise ErrorMT5(f"TF no valido: {despliegue['tf']}")

    bloqueo = tester_dir / ".pst_backtest.lock"
    if bloqueo.exists() and time.time() - bloqueo.stat().st_mtime < timeout_s:
        raise ErrorMT5("ya hay un backtest en marcha en el terminal de pruebas")
    bloqueo.write_text(str(os.getpid()))
    try:
        (tester_dir / "informes").mkdir(exist_ok=True)
        nombre = f"{despliegue['id']}_{time.strftime('%Y%m%d_%H%M%S')}"
        informe_rel = f"informes\\{nombre}"
        lineas = []
        if login:
            lineas += ["[Common]", f"Login={login}"] + ([f"Server={server}"] if server else [])
        lineas += ["[Tester]",
                   f"Expert=PST_V2\\{despliegue['id']}.ex5",
                   f"Symbol={despliegue['simbolo']}",
                   f"Period={despliegue['tf']}",
                   f"Model={int(modelo)}",
                   f"FromDate={desde.replace('-', '.')}",
                   f"ToDate={hasta.replace('-', '.')}",
                   f"Deposit={int(deposito)}", f"Currency={divisa}", f"Leverage={int(apalancamiento)}",
                   "Optimization=0", f"Report={informe_rel}", "ReplaceReport=1", "ShutdownTerminal=1", "Visual=0"]
        if inputs:
            lineas += ["[TesterInputs]"] + [f"{k}={v}" for k, v in inputs.items()]
        ini = tester_dir / "informes" / f"{nombre}.ini"
        ini.write_text("\r\n".join(lineas) + "\r\n", encoding="utf-8")

        t0 = time.time()
        subprocess.run([str(terminal), "/portable", f"/config:{ini}"], cwd=str(tester_dir), timeout=timeout_s)
        informe = tester_dir / "informes" / f"{nombre}.htm"
        if not informe.exists():
            raise ErrorMT5("el tester termino sin informe (¿EA no encontrado, simbolo sin historial o "
                           "terminal ya abierto?). Mira los logs de " + str(tester_dir / "logs"))
        metricas, _ = metricas_de_informe(informe)
        return {"metricas": metricas, "informe": str(informe), "segundos": round(time.time() - t0, 1)}
    finally:
        bloqueo.unlink(missing_ok=True)
