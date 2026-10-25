#!/usr/bin/env python
"""
Lanzador de los agentes LLM (uno por simbolo) de PST_LLM/llm_trader.py.

Cada agente es un proceso `llm_trader.py` independiente con su propia configuracion en caliente,
BBDD y STOP. Este script solo los arranca/para/lista a partir de UN registro:

    .agents/data/llm_trader_flota.json   ->  lista de {nombre, symbol, timeframe, magic, sin_filtro}

Anadir un simbolo = anadir una entrada al registro y ejecutar --iniciar (solo arranca los que
faltan). Quitarlo = parar y borrar la entrada.

    python PST_LLM/llm_trader_flota.py --estado     # quien corre y que gasta cada uno
    python PST_LLM/llm_trader_flota.py --iniciar    # arranca los que no esten corriendo
    python PST_LLM/llm_trader_flota.py --parar      # los apaga limpiamente (fichero STOP)

Por que procesos separados y no uno solo con todos los simbolos: cada agente mantiene su propia
sesion de Claude (su historial), y asi uno que falle no tumba al resto. El coste de tener N
sesiones abiertas en espera es cero; solo se paga por llamada.
"""
import argparse
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import psutil

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

import rutas                                           # rutas de ejecucion: repo o exe (PST_LLM/rutas.py)
REPO_ROOT = rutas.DATOS
DATA = REPO_ROOT / ".agents" / "data"
REGISTRO = DATA / "llm_trader_flota.json"
SCRIPT = rutas.PST_LLM_DIR / "llm_trader.py"
NOMBRE_DEFECTO = "Agente_LLM"      # el agente historico (BTC) conserva los nombres de fichero sin sufijo

REGISTRO_INICIAL = [
    {"nombre": "Agente_LLM", "symbol": "BTCUSD", "timeframe": "M1", "magic": 109001, "sin_filtro": True},
    {"nombre": "Agente_ETH", "symbol": "ETHUSD", "timeframe": "M1", "magic": 109002, "sin_filtro": True},
    {"nombre": "Agente_XAU", "symbol": "XAUUSD", "timeframe": "M15", "magic": 109003, "sin_filtro": True},
    {"nombre": "Agente_EURUSD", "symbol": "EURUSD", "timeframe": "M15", "magic": 109004, "sin_filtro": True},
    {"nombre": "Agente_US500", "symbol": "US500.cash", "timeframe": "M15", "magic": 109005, "sin_filtro": True},
]


def sufijo(nombre):
    return "" if nombre == NOMBRE_DEFECTO else f"_{nombre}"


def ficheros(nombre):
    s = sufijo(nombre)
    return {k: DATA / f"llm_trader{s}.{e}" for k, e in (("config", "json"), ("db", "db"), ("stop", "STOP"),
                                                         ("log", "log"), ("err", "err"))}


def cargar_registro():
    if not REGISTRO.is_file():
        REGISTRO.write_text(json.dumps(REGISTRO_INICIAL, indent=2), encoding="utf-8")
    return json.loads(REGISTRO.read_text(encoding="utf-8-sig"))


def procesos_corriendo():
    """nombre -> pid, leyendo la linea de comandos de los llm_trader.py vivos."""
    vivos = {}
    for p in psutil.process_iter(["pid", "cmdline"]):
        c = p.info["cmdline"] or []
        if any(a.endswith("llm_trader.py") for a in c):
            nombre = c[c.index("--nombre") + 1] if "--nombre" in c else NOMBRE_DEFECTO
            vivos[nombre] = p.info["pid"]
    return vivos


REVISOR = rutas.PST_LLM_DIR / "llm_revisor.py"
REVISOR_STOP = DATA / "llm_revisor.STOP"


def revisor_pid():
    for p in psutil.process_iter(["pid", "cmdline"]):
        if any(a.endswith("llm_revisor.py") for a in (p.info["cmdline"] or [])):
            return p.info["pid"]
    return None


BOT = rutas.PST_LLM_DIR / "llm_telegram_bot.py"
BOT_STOP = DATA / "llm_telegram.STOP"


def bot_pid():
    for p in psutil.process_iter(["pid", "cmdline"]):
        if any(a.endswith("llm_telegram_bot.py") for a in (p.info["cmdline"] or [])):
            return p.info["pid"]
    return None


def iniciar_bot():
    pid = bot_pid()
    if pid:
        print(f"  = Panel de Telegram ya corre (PID {pid})")
        return
    BOT_STOP.unlink(missing_ok=True)
    salida, error = open(DATA / "llm_telegram.log", "w", encoding="utf-8"), open(DATA / "llm_telegram.err", "w", encoding="utf-8")
    flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    p = subprocess.Popen(rutas.comando_python(BOT), cwd=str(REPO_ROOT), stdout=salida, stderr=error,
                         stdin=subprocess.DEVNULL, creationflags=flags)
    print(f"  + Panel de Telegram arrancado, PID {p.pid}")


def iniciar_revisor():
    pid = revisor_pid()
    if pid:
        print(f"  = Revisor ya corre (PID {pid})")
        return
    REVISOR_STOP.unlink(missing_ok=True)
    salida, error = open(DATA / "llm_revisor.log", "w", encoding="utf-8"), open(DATA / "llm_revisor.err", "w", encoding="utf-8")
    flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    p = subprocess.Popen(rutas.comando_python(REVISOR), cwd=str(REPO_ROOT), stdout=salida, stderr=error,
                         stdin=subprocess.DEVNULL, creationflags=flags)
    print(f"  + Revisor de posiciones arrancado, PID {p.pid}")


MEJORA = rutas.PST_LLM_DIR / "llm_mejora_prompt.py"
MEJORA_STOP = DATA / "llm_mejora_prompt.STOP"


def mejora_pid():
    for p in psutil.process_iter(["pid", "cmdline"]):
        if any(a.endswith("llm_mejora_prompt.py") for a in (p.info["cmdline"] or [])):
            return p.info["pid"]
    return None


def iniciar_mejora():
    """Agente de mejora del criterio (solo propone; ver PST_LLM/llm_mejora_prompt.py)."""
    pid = mejora_pid()
    if pid:
        print(f"  = Mejora de criterio ya corre (PID {pid})")
        return
    MEJORA_STOP.unlink(missing_ok=True)
    salida, error = open(DATA / "llm_mejora_prompt.log", "a", encoding="utf-8"), open(DATA / "llm_mejora_prompt.err", "w", encoding="utf-8")
    flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    p = subprocess.Popen(rutas.comando_python(MEJORA), cwd=str(REPO_ROOT), stdout=salida, stderr=error,
                         stdin=subprocess.DEVNULL, creationflags=flags)
    print(f"  + Mejora de criterio arrancada, PID {p.pid}")


def config_por_defecto(a):
    return {"enabled": True, "symbol": a["symbol"], "timeframe": a["timeframe"],
            "timeframes_analisis": ["M5", "M15", "H1", "H4", "D1"], "model": "haiku", "effort": "low",
            "live": True, "sin_filtro": a.get("sin_filtro", True), "max_llamadas_dia": 300,
            "max_perdida_dia": 1000000.0, "max_perdida_flota_dia": 1000000.0, "equity_minima": 0.0,
            "sesion_continua": True, "reiniciar_cada": 8,
            "telegram": True, "aviso_cooldown_min": 15}


def iniciar():
    vivos = procesos_corriendo()
    for a in cargar_registro():
        f = ficheros(a["nombre"])
        if a["nombre"] in vivos:
            print(f"  = {a['nombre']} ({a['symbol']}) ya corre (PID {vivos[a['nombre']]})")
            continue
        if not f["config"].is_file():          # nunca se pisa una configuracion existente
            f["config"].write_text(json.dumps(config_por_defecto(a), indent=2), encoding="utf-8")
        f["stop"].unlink(missing_ok=True)
        args = rutas.comando_python(SCRIPT)
        if a["nombre"] != NOMBRE_DEFECTO:
            args += ["--nombre", a["nombre"]]
        args += ["--magic", str(a["magic"])]
        salida, error = open(f["log"], "w", encoding="utf-8"), open(f["err"], "w", encoding="utf-8")
        # Proceso independiente: sobrevive a que se cierre la terminal que lo lanzo.
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        p = subprocess.Popen(args, cwd=str(REPO_ROOT), stdout=salida, stderr=error, stdin=subprocess.DEVNULL,
                             creationflags=flags)
        print(f"  + {a['nombre']} ({a['symbol']} {a['timeframe']}) arrancado, PID {p.pid}")
    iniciar_revisor()
    iniciar_bot()
    iniciar_mejora()


def parar():
    reg = cargar_registro()
    for a in reg:
        ficheros(a["nombre"])["stop"].write_text("stop", encoding="utf-8")
    REVISOR_STOP.write_text("stop", encoding="utf-8")
    BOT_STOP.write_text("stop", encoding="utf-8")
    MEJORA_STOP.write_text("stop", encoding="utf-8")
    print("  STOP enviado a todos; esperando a que cierren sus sesiones...")
    limite = time.time() + 60
    while time.time() < limite and (procesos_corriendo() or revisor_pid() or bot_pid() or mejora_pid()):
        time.sleep(2)
    if bot_pid():
        print("  ! el panel de Telegram no cerro a tiempo, se mata")
        psutil.Process(bot_pid()).kill()
    for nombre, pid in procesos_corriendo().items():
        print(f"  ! {nombre} no cerro a tiempo, se mata (PID {pid})")
        psutil.Process(pid).kill()
    if revisor_pid():
        print("  ! el revisor no cerro a tiempo, se mata")
        psutil.Process(revisor_pid()).kill()
    for a in reg:
        ficheros(a["nombre"])["stop"].unlink(missing_ok=True)
    if mejora_pid():
        print("  ! la mejora de criterio no cerro a tiempo, se mata")
        psutil.Process(mejora_pid()).kill()
    REVISOR_STOP.unlink(missing_ok=True)
    BOT_STOP.unlink(missing_ok=True)
    MEJORA_STOP.unlink(missing_ok=True)
    print("  parados.")


def estado():
    vivos = procesos_corriendo()
    q = {}
    try:
        q = json.loads((Path.home() / ".habblaud" / "usage" / ".claude.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    print(f"Cuota: 5h {q.get('five_hour', {}).get('utilization', '?')}% · semanal {q.get('seven_day', {}).get('utilization', '?')}%\n")
    print(f"{'Agente':<15} {'Simbolo':<11} {'TF':<4} {'Vivo':<5} {'Llam.':>5} {'Tokens':>9} {'tok/llam':>8} {'Props':>5}  Ultima decision")
    tot_l = tot_t = 0
    for a in cargar_registro():
        f = ficheros(a["nombre"])
        llam = tok = props = 0
        ultima = "-"
        if f["db"].is_file():
            con = sqlite3.connect(f["db"])
            llam, tok = con.execute("SELECT COUNT(*), SUM(COALESCE(tokens_in,0)+COALESCE(tokens_out,0)+"
                                    "COALESCE(tokens_cache_read,0)+COALESCE(tokens_cache_write,0)) "
                                    "FROM decisions WHERE llm_called=1").fetchone()
            tok = tok or 0
            props = con.execute("SELECT COUNT(*) FROM decisions WHERE action IN ('buy','sell')").fetchone()[0]
            u = con.execute("SELECT ts, action FROM decisions WHERE llm_called=1 ORDER BY id DESC LIMIT 1").fetchone()
            ultima = f"{u[1].upper()} {u[0][11:16]}" if u else "-"
            con.close()
        tot_l += llam
        tot_t += tok
        print(f"{a['nombre']:<15} {a['symbol']:<11} {a['timeframe']:<4} {'si' if a['nombre'] in vivos else 'NO':<5} "
              f"{llam:>5} {tok:>9,} {(tok // llam if llam else 0):>8,} {props:>5}  {ultima}")
    print(f"{'TOTAL':<15} {'':<11} {'':<4} {'':<5} {tot_l:>5} {tot_t:>9,}")
    # Revisor de posiciones
    rdb, n_rev, cierres, ult = DATA / "llm_revisor.db", 0, 0, "-"
    if rdb.is_file():
        con = sqlite3.connect(rdb)
        n_rev, cierres = con.execute("SELECT COUNT(*), COALESCE(SUM(closed),0) FROM revisions").fetchone()
        u = con.execute("SELECT ts, decision, symbol FROM revisions ORDER BY id DESC LIMIT 1").fetchone()
        ult = f"{u[2]} {u[1].upper()} {u[0][11:16]}" if u else "-"
        con.close()
    print(f"\nRevisor: {'vivo' if revisor_pid() else 'NO corre'} · revisiones {n_rev} · cierres {cierres} · ultima {ult}")
    print(f"Panel de Telegram: {'vivo' if bot_pid() else 'NO corre'}")
    try:
        for a in cargar_registro():
            cfg = json.loads(ficheros(a["nombre"])["config"].read_text(encoding="utf-8-sig"))
            if cfg.get("live"):
                break
        else:
            cfg = None
        print(f"Modo de los agentes: {'OPERAN DE VERDAD (demo)' if cfg else 'simulacion'}")
    except Exception:
        pass


def resultados():
    """Resultado real en MT5 de cada agente y del conjunto, con la evaluacion de los cierres a mano."""
    import importlib.util
    import MetaTrader5 as mt5
    spec = importlib.util.spec_from_file_location("lt", SCRIPT)
    lt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lt)
    if not mt5.initialize():
        print(f"MT5 no disponible: {mt5.last_error()}")
        return
    try:
        todas = []
        for a in cargar_registro():
            t = lt.calcular_resultados(mt5, ficheros(a["nombre"])["db"])
            for x in t:
                x["agente"] = a["nombre"]
            todas += t
            lt.imprimir_resultados(t, f"{a['nombre']} ({a['symbol']})", detalle=False)
        lt.imprimir_resultados(todas, "TODOS LOS AGENTES", detalle=True)
    finally:
        mt5.shutdown()


def main():
    ap = argparse.ArgumentParser(description="Lanzador de agentes LLM")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--resultados", action="store_true", help="resultado real de las operaciones, por agente y total")
    g.add_argument("--estado", action="store_true")
    g.add_argument("--iniciar", action="store_true")
    g.add_argument("--parar", action="store_true")
    a = ap.parse_args()
    if a.resultados:
        resultados()
        return
    if a.iniciar:
        iniciar()
    elif a.parar:
        parar()
    estado() if a.estado or a.iniciar else None


if __name__ == "__main__":
    main()
