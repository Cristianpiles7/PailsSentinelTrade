"""
PST Diagnostic Tool — pst_diag.py
Uso: python pst_diag.py [--lines N] [--level LEVEL] [--since MINUTES]

Agrega en un solo bloque de texto los errores y advertencias más recientes
de las 3 fuentes de logs del bot (archivo .log, DB system_logs, signal_logs bloqueadas).
Claude lo lee con una sola llamada para diagnosticar fallos y mejorar el código.
"""
import argparse
import os
import sys
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

# La consola por defecto de Windows (cp1252) revienta con los emojis del reporte
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent  # el script vive en Tools/

# ── Configuración de rutas ────────────────────────────────────────────────────
LOG_FILES = [
    REPO_ROOT / "PST_Startup.log",
    REPO_ROOT / "v2_sentinel_prime.log",
]

# La DB se localiza igual que en config.py (Autodiscovery básico)
def find_db() -> Path | None:
    candidates = [
        REPO_ROOT / "PST_Core" / "data" / "pst_trading.db",
        REPO_ROOT.parent / "PST_Core" / "data" / "pst_trading.db",
    ]
    env_path = os.getenv("DB_PATH")
    if env_path:
        candidates.insert(0, Path(env_path))
    for c in candidates:
        if c.exists() and c.stat().st_size > 0:
            return c
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────
SEPARATOR = "─" * 80

def header(title: str) -> str:
    return f"\n{'═'*80}\n  {title}\n{'═'*80}"


def tail_log_file(path: Path, lines: int, since: datetime | None, level_filter: str) -> list[str]:
    """Lee las últimas N líneas de un .log filtrando por nivel y fecha."""
    if not path.exists():
        return [f"[ARCHIVO NO ENCONTRADO: {path}]"]

    level_keywords = {
        "ERROR":   ["ERROR", "CRITICAL", "CRITICO", "FALLO", "❌", "🚨"],
        "WARNING": ["ERROR", "CRITICAL", "WARNING", "WARN", "⚠️", "❌", "🚨"],
        "INFO":    [],  # Sin filtro
    }.get(level_filter.upper(), [])

    results = []
    with open(path, encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()

    for line in all_lines[-lines * 3:]:  # Leer más y filtrar
        line = line.rstrip()
        if not line:
            continue
        if level_keywords and not any(kw in line for kw in level_keywords):
            continue
        if since:
            # Intentar parsear timestamp del inicio de línea, con o sin corchete
            # (formatos: [YYYY-MM-DD HH:MM:SS] / YYYY-MM-DD HH:MM:SS / [HH:MM:SS])
            bare = line.lstrip("[")
            try:
                ts = datetime.strptime(bare[:19], "%Y-%m-%d %H:%M:%S")
                if ts < since:
                    continue
            except ValueError:
                try:
                    today = datetime.now().date()
                    ts = datetime.combine(today, datetime.strptime(bare[:8], "%H:%M:%S").time())
                    if ts < since:
                        continue
                except ValueError:
                    pass  # Sin timestamp reconocible → incluir siempre
        results.append(line)

    return results[-lines:] if len(results) > lines else results


def query_db_logs(db_path: Path, lines: int, since: datetime | None, level_filter: str) -> list[str]:
    """Lee system_logs de la DB SQLite."""
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        level_clause = ""
        params: list = []
        if level_filter.upper() == "ERROR":
            level_clause = "AND level IN ('ERROR','CRITICAL')"
        elif level_filter.upper() == "WARNING":
            level_clause = "AND level IN ('ERROR','CRITICAL','WARNING')"

        since_clause = ""
        if since:
            since_clause = "AND timestamp >= ?"
            params.append(since.strftime("%Y-%m-%d %H:%M:%S"))

        params.append(lines)
        cur.execute(f"""
            SELECT timestamp, level, source, message
            FROM system_logs
            WHERE 1=1 {level_clause} {since_clause}
            ORDER BY id DESC
            LIMIT ?
        """, params)
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return ["(Sin entradas en system_logs para los filtros dados)"]

        result = []
        for r in reversed(rows):
            result.append(f"[{r['timestamp']}] {r['level']:8s} | {r['source']:25s} | {r['message']}")
        return result
    except Exception as e:
        return [f"[ERROR leyendo DB: {e}]"]


def query_blocked_signals(db_path: Path, lines: int, since: datetime | None) -> list[str]:
    """Lee operaciones bloqueadas de signal_logs (las que no se ejecutaron y por qué)."""
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        since_clause = ""
        params: list = []
        if since:
            since_clause = "AND timestamp >= ?"
            params.append(since.strftime("%Y-%m-%d %H:%M:%S"))

        params.append(lines)
        cur.execute(f"""
            SELECT timestamp, symbol, regime, strategy, signal_type, score, price, blocked_reason
            FROM signal_logs
            WHERE signal_type LIKE 'BLOCKED%' {since_clause}
            ORDER BY id DESC
            LIMIT ?
        """, params)
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return ["(Sin señales bloqueadas en el período)"]

        result = []
        for r in reversed(rows):
            result.append(
                f"[{r['timestamp']}] {r['symbol']:12s} | Score {r['score']:5.1f} | "
                f"Estrategia: {r['strategy']:25s} | Motivo: {r['blocked_reason'] or r['signal_type']}"
            )
        return result
    except Exception as e:
        return [f"[ERROR leyendo signal_logs: {e}"]


def query_recent_trades(db_path: Path, lines: int, since: datetime | None) -> list[str]:
    """Lee las operaciones ejecutadas (abiertas y cerradas) para contexto."""
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        since_clause = ""
        params: list = []
        if since:
            since_clause = "AND time_in >= ?"
            params.append(since.strftime("%Y-%m-%d %H:%M:%S"))

        params.append(lines)
        cur.execute(f"""
            SELECT time_in, time_out, symbol, type, strategy_name, price_in, price_out, profit, sl, tp
            FROM trades
            WHERE 1=1 {since_clause}
            ORDER BY rowid DESC
            LIMIT ?
        """, params)
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return ["(Sin trades en el período)"]

        result = []
        for r in reversed(rows):
            status = "OPEN" if not r["time_out"] or r["time_out"] == "" else f"CLOSED P&L:{r['profit']:+.2f}"
            result.append(
                f"[{r['time_in']}] {r['symbol']:12s} {r['type']:4s} | "
                f"Estrategia: {r['strategy_name']:25s} | IN:{r['price_in']:.5f} "
                f"SL:{r['sl']:.5f} TP:{r['tp']:.5f} | {status}"
            )
        return result
    except Exception as e:
        return [f"[ERROR leyendo trades: {e}]"]


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="PST Diagnostic Tool")
    parser.add_argument("--lines",  type=int, default=100,  help="Líneas por sección (default 100)")
    parser.add_argument("--level",  type=str, default="WARNING", help="Nivel mínimo: INFO | WARNING | ERROR (default WARNING)")
    parser.add_argument("--since",  type=int, default=120,  help="Solo logs de los últimos N minutos (default 120)")
    parser.add_argument("--all",    action="store_true",    help="Sin filtro de tiempo (todo el historial)")
    args = parser.parse_args()

    since_dt = None if args.all else (datetime.now() - timedelta(minutes=args.since))
    since_str = "todo el historial" if args.all else f"últimos {args.since} min ({since_dt.strftime('%H:%M')} →)"

    print(f"\n{'█'*80}")
    print(f"  PST DIAGNOSTIC REPORT — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Nivel: {args.level.upper()} | Período: {since_str} | Líneas/sección: {args.lines}")
    print(f"{'█'*80}")

    db_path = find_db()
    if db_path:
        print(f"\n  DB detectada: {db_path} ({db_path.stat().st_size/1024:.0f} KB)")
    else:
        print("\n  ⚠️  DB NO ENCONTRADA — solo se leerán archivos .log")

    # ── 1. Archivos de log ────────────────────────────────────────────────────
    for log_file in LOG_FILES:
        print(header(f"LOG FILE: {log_file.name}"))
        lines = tail_log_file(log_file, args.lines, since_dt, args.level)
        if lines:
            print("\n".join(lines))
        else:
            print("(Sin entradas para los filtros dados)")

    if not db_path:
        print("\n[FIN — DB no disponible, solo archivos .log]\n")
        return

    # ── 2. system_logs (DB) ───────────────────────────────────────────────────
    print(header("DB → system_logs (logs del motor en tiempo real)"))
    db_lines = query_db_logs(db_path, args.lines, since_dt, args.level)
    print("\n".join(db_lines))

    # ── 3. Señales BLOQUEADAS ─────────────────────────────────────────────────
    print(header("DB → signal_logs BLOQUEADAS (operaciones que no se ejecutaron)"))
    blocked = query_blocked_signals(db_path, args.lines, since_dt)
    print("\n".join(blocked))

    # ── 4. Trades ejecutados ──────────────────────────────────────────────────
    print(header("DB → trades (operaciones ejecutadas en el período)"))
    trades = query_recent_trades(db_path, min(args.lines, 30), since_dt)
    print("\n".join(trades))

    print(f"\n{'█'*80}")
    print(f"  FIN DEL REPORTE — {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'█'*80}\n")


if __name__ == "__main__":
    main()
