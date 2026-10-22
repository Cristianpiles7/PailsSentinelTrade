#!/usr/bin/env python
"""
Planificador de la flota de agentes PST.

Lo lanza el Programador de tareas de Windows (ver Tools/scheduler_install.ps1). Decide
QUE agentes corren esta noche y cuales no, y lo hace de forma SECUENCIAL.

POR QUE DISPARO POR CONDICION Y NO POR RELOJ
    Medido el 2026-10-20: una pasada del reconciliador que no produjo nada costo 132.759
    tokens. Casi todo es entrada fija -esquema, prompt de sistema y las definiciones de las
    13 tools del MCP-, asi que el SUELO por pasada es alto hagan lo que hagan. Lanzar "todas
    las noches pase lo que pase" quema cuota para que el agente conteste que no hay datos.
    Por eso cada agente tiene una PUERTA que se evalua con SQL (gratis) antes de gastar nada.

POR QUE SECUENCIAL
    La cuota (ventana de 5h + tope semanal) se comparte con el trabajo interactivo del
    usuario. Varios agentes a la vez se comen la ventana y dejan al usuario sin ella.

Uso:
    python .agents/scheduler.py            # evalua y ejecuta lo que toque
    python .agents/scheduler.py --dry-run  # dice que haria, sin gastar nada
    python .agents/scheduler.py --force reconciliador   # salta la puerta de ese agente
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

USAGE_FILE = Path(os.path.expanduser("~/.habblaud/usage/.claude.json"))

# ── Presupuesto de cuota ─────────────────────────────────────────────────────
# Topes conservadores: el usuario tiene prioridad sobre la flota. Si la ventana de 5h ya
# va cargada, no se lanza nada; si la semanal va justa, solo los baratos.
MAX_5H_PARA_LANZAR = 60      # % de la ventana de 5h por encima del cual no se lanza nada
MAX_SEMANAL_BARATOS = 85     # % semanal por encima del cual ni los baratos
MAX_SEMANAL_CAROS = 50       # % semanal por encima del cual no se lanzan los de opus
USAGE_MAX_EDAD_H = 12        # si el dato de cuota es mas viejo, se considera desconocido


def leer_cuota():
    """
    Uso de cuota en vivo, escrito por el mod de habblaud. Devuelve None si no hay dato
    fresco: en ese caso se es conservador y solo se permiten los agentes baratos.
    """
    if not USAGE_FILE.is_file():
        return None
    try:
        d = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
        edad_h = (time.time() - d.get("fetchedAt", 0) / 1000) / 3600
        if edad_h > USAGE_MAX_EDAD_H:
            return None
        return {
            "cinco_h": d.get("five_hour", {}).get("utilization"),
            "semanal": d.get("seven_day", {}).get("utilization"),
            "edad_h": round(edad_h, 1),
        }
    except Exception:
        return None


# ── Puertas por agente ───────────────────────────────────────────────────────
# Cada puerta devuelve (puede_correr: bool, motivo: str). Se evaluan con SQL, gratis.

def _db():
    import sqlite3
    from PST_Core.config import DB_PATH
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=15)
    con.row_factory = sqlite3.Row
    return con


def _cierres_desde_ultima_pasada(agente: str) -> tuple:
    """(cierres nuevos, fecha de la ultima pasada ok). Si nunca corrio, cuenta todos."""
    con = _db()
    fila = con.execute(
        "SELECT finished_at FROM agent_runs WHERE agent = ? AND status = 'ok' "
        "ORDER BY id DESC LIMIT 1", (agente,)).fetchone()
    desde = fila["finished_at"] if fila else None
    if desde:
        n = con.execute(
            "SELECT COUNT(*) FROM trades WHERE time_out IS NOT NULL AND time_out != '' "
            "AND time_out > ?", (desde,)).fetchone()[0]
    else:
        n = con.execute(
            "SELECT COUNT(*) FROM trades WHERE time_out IS NOT NULL AND time_out != ''").fetchone()[0]
    con.close()
    return n, desde


def _dias_desde_ultima_pasada(agente: str):
    con = _db()
    fila = con.execute(
        "SELECT finished_at FROM agent_runs WHERE agent = ? AND status = 'ok' "
        "ORDER BY id DESC LIMIT 1", (agente,)).fetchone()
    con.close()
    if not fila or not fila["finished_at"]:
        return None
    try:
        ultima = datetime.fromisoformat(str(fila["finished_at"]).replace("Z", ""))
        return (datetime.now() - ultima).days
    except Exception:
        return None


def puerta_autopsia():
    """
    Hay perdidas sin diseccionar desde la ultima autopsia.

    En LOTE y no por operacion a proposito: el suelo medido por pasada es ~132k tokens,
    asi que cinco autopsias sueltas costarian cinco veces mas que una de las cinco juntas.
    Y lo que tiene valor es la causa COMUN entre varias perdidas: una autopsia por
    operacion no puede ver ese patron ni aunque quiera.
    """
    con = _db()
    fila = con.execute(
        "SELECT finished_at FROM agent_runs WHERE agent = 'autopsia' AND status = 'ok' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    desde = fila["finished_at"] if fila else None
    if desde:
        n = con.execute(
            "SELECT COUNT(*) FROM trades WHERE profit < 0 AND time_out IS NOT NULL "
            "AND time_out != '' AND time_out > ?", (desde,)).fetchone()[0]
    else:
        n = con.execute(
            "SELECT COUNT(*) FROM trades WHERE profit < 0 AND time_out IS NOT NULL "
            "AND time_out != '' AND time_out >= datetime('now','-2 days')").fetchone()[0]
    con.close()
    if n == 0:
        return False, "sin pérdidas nuevas que diseccionar"
    return True, f"{n} pérdida(s) sin diseccionar"


def puerta_reconciliador():
    n, desde = _cierres_desde_ultima_pasada("reconciliador")
    if n < 20:
        return False, f"solo {n} cierres nuevos desde la última pasada (hacen falta 20)"
    return True, f"{n} cierres nuevos por reconciliar"


def puerta_juez():
    n, _ = _cierres_desde_ultima_pasada("juez")
    dias = _dias_desde_ultima_pasada("juez")
    if n >= 30:
        return True, f"{n} cierres nuevos"
    if dias is not None and dias >= 7 and n >= 10:
        return True, f"{dias} días desde la última y {n} cierres nuevos"
    return False, f"{n} cierres nuevos (hacen falta 30, o 10 si pasó una semana)"


def puerta_cazador():
    # El cazador va DESPUES del reconciliador, por diseño: optimizar contra un juez que no
    # cuadra con el live industrializa el error.
    con = _db()
    rec = con.execute(
        "SELECT finished_at FROM agent_runs WHERE agent='reconciliador' AND status='ok' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    if not rec:
        return False, "el reconciliador no ha corrido aún con éxito; la brecha juez-live sigue sin desglosar"
    if datetime.now().weekday() != 5:  # 5 = sábado
        return False, "solo corre los sábados (es el caro)"
    return True, "sábado y el reconciliador ya pasó"


# agente -> (puerta, es_caro). "caro" = opus, que consume mucho más de la ventana.
AGENDA = {
    # La autopsia va primero: es la mas barata de justificar (basta una perdida) y la que
    # da contexto cualitativo al resto. Ademas no escribe propuestas, solo un informe.
    "autopsia":      (puerta_autopsia, False),
    "reconciliador": (puerta_reconciliador, False),
    "juez":          (puerta_juez, False),
    "cazador":       (puerta_cazador, True),
    # revisor: no se programa, se lanza antes de cada release.
    # ingeniero-mql5: no se programa, solo tras una propuesta aprobada.
}


async def registrar_salto(agente: str, motivo: str):
    """
    Un salto también se registra. Sin esto, una mañana sin resultados es indistinguible de
    un cron roto, y acabas depurando a ciegas.
    """
    from PST_Core.models.database import PSTDatabase
    from PST_Core.config import DB_PATH
    db = PSTDatabase(db_path=DB_PATH)
    await db.initialize()
    run_id = await db.start_agent_run(agente, model=None, effort=None)
    await db.finish_agent_run(run_id, "skipped", summary=f"No se lanzó: {motivo}",
                              cost_tokens=0, duration_s=0.0)


def lanzar(agente: str) -> int:
    """Lanza un agente por el runner. Devuelve el código de salida."""
    cmd = [sys.executable, "-u", str(REPO_ROOT / ".agents" / "run_agent.py"), agente]
    print(f"  → lanzando {agente}...", flush=True)
    r = subprocess.run(cmd, cwd=str(REPO_ROOT))
    return r.returncode


def actualizar_doc_estrategias():
    """
    Regenera manual_docs/ESTRATEGIAS_ACTIVAS.md. NO es un agente LLM a proposito: son
    estadisticas del historial de MT5, no consumen cuota y no pasan por las puertas.
    Un fallo aqui no debe impedir que corran los agentes.
    """
    script = REPO_ROOT / "Tools" / "update_active_strategies.py"
    try:
        r = subprocess.run([sys.executable, str(script)], cwd=str(REPO_ROOT),
                           capture_output=True, text=True, timeout=120,
                           stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
        print(f"  ✓ doc de estrategias: {(r.stdout or '').strip()}" if r.returncode == 0
              else f"  ! doc de estrategias falló: {(r.stderr or '').strip()[-300:]}")
    except Exception as e:
        print(f"  ! doc de estrategias falló: {e}")


def main():
    ap = argparse.ArgumentParser(description="Planificador de la flota PST")
    ap.add_argument("--dry-run", action="store_true", help="Evalúa las puertas sin lanzar nada")
    ap.add_argument("--force", metavar="AGENTE", help="Salta la puerta de ese agente")
    ap.add_argument("--ignorar-cuota", action="store_true", help="No mirar el presupuesto (para pruebas)")
    args = ap.parse_args()

    print(f"[scheduler] {datetime.now():%Y-%m-%d %H:%M}")

    # Antes del freno de cuota: no gasta ninguna.
    if args.dry_run:
        print("  ✓ doc de estrategias: se actualizaría (sin LLM)")
    elif not args.force:
        actualizar_doc_estrategias()

    cuota = leer_cuota()
    if cuota:
        print(f"[scheduler] cuota: 5h={cuota['cinco_h']}% · semanal={cuota['semanal']}% "
              f"(dato de hace {cuota['edad_h']}h)")
    else:
        print("[scheduler] cuota: SIN DATO fresco → modo conservador, solo agentes baratos")

    # Freno global por ventana de 5h
    if cuota and not args.ignorar_cuota and cuota["cinco_h"] is not None:
        if cuota["cinco_h"] > MAX_5H_PARA_LANZAR:
            print(f"[scheduler] ABORTA: la ventana de 5h va al {cuota['cinco_h']}% "
                  f"(tope {MAX_5H_PARA_LANZAR}%). El usuario tiene prioridad.")
            return

    lanzados = 0
    for agente, (puerta, es_caro) in AGENDA.items():
        if args.force and args.force != agente:
            continue

        # Presupuesto semanal
        if cuota and not args.ignorar_cuota and cuota["semanal"] is not None:
            if cuota["semanal"] > MAX_SEMANAL_BARATOS:
                motivo = f"semanal al {cuota['semanal']}% (tope {MAX_SEMANAL_BARATOS}%)"
                print(f"  ✗ {agente}: {motivo}")
                if not args.dry_run:
                    asyncio.run(registrar_salto(agente, motivo))
                continue
            if es_caro and cuota["semanal"] > MAX_SEMANAL_CAROS:
                motivo = f"agente caro y semanal al {cuota['semanal']}% (tope {MAX_SEMANAL_CAROS}%)"
                print(f"  ✗ {agente}: {motivo}")
                if not args.dry_run:
                    asyncio.run(registrar_salto(agente, motivo))
                continue
        elif es_caro and not args.ignorar_cuota:
            motivo = "agente caro sin dato de cuota fresco"
            print(f"  ✗ {agente}: {motivo}")
            if not args.dry_run:
                asyncio.run(registrar_salto(agente, motivo))
            continue

        # Puerta del agente
        if args.force == agente:
            puede, motivo = True, "forzado por --force"
        else:
            try:
                puede, motivo = puerta()
            except Exception as e:
                puede, motivo = False, f"no se pudo evaluar la puerta: {e}"

        if not puede:
            print(f"  ✗ {agente}: {motivo}")
            if not args.dry_run:
                asyncio.run(registrar_salto(agente, motivo))
            continue

        print(f"  ✓ {agente}: {motivo}")
        if args.dry_run:
            continue

        code = lanzar(agente)
        lanzados += 1
        if code != 0:
            print(f"  ! {agente} terminó con código {code} (queda registrado en agent_runs)")

    print(f"[scheduler] {'(dry-run) ' if args.dry_run else ''}agentes lanzados: {lanzados}")


if __name__ == "__main__":
    main()
