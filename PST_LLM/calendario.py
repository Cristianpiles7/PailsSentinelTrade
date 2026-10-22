"""
Puerta de calendario economico para los agentes LLM.

Por que existe: FTMO no deja operar alrededor de ciertas publicaciones. La lista que marca
FTMO como "evento restringido" NO sigue el impacto (medio y bajo tambien entran); sigue el
tipo de dato (CPI, PPI, RPI, PSNB, inventarios de crudo). Comprobado contra las capturas de la
semana 19-25 oct 2026: 5 eventos el lunes y 8 el miercoles, 13 en total. Ver
docs/PLAN_AGENTES_V2.md, seccion 11.

Fuente: feed publico de ForexFactory (JSON). El calendario de FTMO exige login y no se usa.

Regla de bloqueo: no se abren posiciones nuevas desde MINUTOS_ANTES hasta MINUTOS_DESPUES
alrededor de un evento restringido de una divisa del simbolo. Si el feed no se puede leer y la
copia local tiene mas de 48 h, se bloquean las entradas (fallo cerrado).

Uso:
    python PST_LLM/calendario.py --semana              # lista los eventos restringidos de la semana
    python PST_LLM/calendario.py --probar US500.cash   # estado de la puerta ahora mismo para un simbolo
"""
import argparse
import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import rutas                                           # rutas de ejecucion: repo o exe (PST_LLM/rutas.py)
REPO_ROOT = rutas.DATOS
DATA = REPO_ROOT / ".agents" / "data"
CACHE = DATA / "calendario_ff.json"
FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

MINUTOS_ANTES = 30
MINUTOS_DESPUES = 15
EDAD_MAX_CACHE_H = 6          # por encima, se intenta bajar el feed otra vez
EDAD_MAX_FALLO_H = 48         # por encima sin poder bajarlo, se bloquean las entradas

# Tipos de dato que FTMO restringe (verificado con las capturas de la semana 19-25 oct 2026).
# Regla por titulo, no por impacto. Sin validar mas alla de esa semana.
RESTRINGIDO = re.compile(
    r"\b(CPI|PPI|RPI|Public Sector Net Borrowing|Crude Oil Inventories)\b", re.IGNORECASE)

# Divisas que mueven cada simbolo. Un evento de una divisa bloquea los simbolos que la tienen.
DIVISAS = {
    "US500.cash": {"USD"}, "US100.cash": {"USD"}, "US30.cash": {"USD"},
    "GER40.cash": {"EUR"}, "EU50.cash": {"EUR"}, "UK100.cash": {"GBP"},
    "MSFT": {"USD"}, "AVGO": {"USD"}, "BRK.B": {"USD"}, "XOM": {"USD"}, "AMD": {"USD"}, "BA": {"USD"},
    "BABA": {"USD"}, "CVX": {"USD"}, "GM": {"USD"}, "INTC": {"USD"}, "KO": {"USD"}, "MCD": {"USD"},
    "NFLX": {"USD"}, "PLTR": {"USD"}, "ZM": {"USD"}, "GOOG": {"USD"}, "META": {"USD"}, "AMZN": {"USD"},
    "TSLA": {"USD"}, "NVDA": {"USD"}, "AAPL": {"USD"}, "RTX": {"USD"}, "FDX": {"USD"}, "ASML": {"USD"},
    "FRA40.cash": {"EUR"},
    "EURUSD": {"EUR", "USD"}, "XAUUSD": {"USD"},
}


def _descargar():
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": "PST_LLM/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        datos = json.loads(r.read().decode("utf-8"))
    if not isinstance(datos, list):
        raise ValueError("el feed no es una lista de eventos")
    return datos


def _eventos():
    """Eventos del feed (cacheados). Devuelve (lista, antiguedad_h) o lanza si no hay nada usable."""
    ahora = datetime.now(timezone.utc)
    if CACHE.is_file():
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
        edad = (ahora - datetime.fromisoformat(cache["descargado"])).total_seconds() / 3600
        if edad < EDAD_MAX_CACHE_H:
            return cache["eventos"], edad
    try:
        eventos = _descargar()
        DATA.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"descargado": ahora.isoformat(), "eventos": eventos},
                                    ensure_ascii=False), encoding="utf-8")
        return eventos, 0.0
    except Exception as e:
        if CACHE.is_file():
            cache = json.loads(CACHE.read_text(encoding="utf-8"))
            edad = (ahora - datetime.fromisoformat(cache["descargado"])).total_seconds() / 3600
            return cache["eventos"], edad
        raise RuntimeError(f"sin calendario: {e}")


def restringidos(eventos=None):
    """Eventos restringidos de la semana: lista de (instante_utc, divisa, titulo)."""
    if eventos is None:
        eventos, _ = _eventos()
    out = []
    for e in eventos:
        if not RESTRINGIDO.search(e.get("title", "")):
            continue
        t = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
        out.append((t, e.get("country", ""), e["title"]))
    return sorted(out)


def bloqueo_entrada(symbol, ahora=None):
    """(bloqueada, motivo). Llamar solo para abrir posiciones nuevas, no para cerrar."""
    ahora = ahora or datetime.now(timezone.utc)
    try:
        eventos, edad = _eventos()
    except RuntimeError as e:
        return True, f"calendario no disponible ({e}): no se abre"
    if edad > EDAD_MAX_FALLO_H:
        return True, f"calendario sin actualizar hace {edad:.0f} h: no se abre"
    divisas = DIVISAS.get(symbol)
    if divisas is None:
        return True, f"{symbol} sin divisas definidas en calendario.py: no se abre"
    for t, div, titulo in restringidos(eventos):
        if div not in divisas:
            continue
        if t - timedelta(minutes=MINUTOS_ANTES) <= ahora <= t + timedelta(minutes=MINUTOS_DESPUES):
            return True, f"evento restringido {div} '{titulo}' a las {t:%H:%M} UTC"
    return False, ""


def proximo(ahora=None):
    """Siguiente evento restringido, o el que sigue bloqueando ahora. None si no queda ninguno esta semana."""
    ahora = ahora or datetime.now(timezone.utc)
    try:
        eventos, edad = _eventos()
    except RuntimeError as e:
        return {"error": str(e)}
    for t, div, titulo in restringidos(eventos):
        if t + timedelta(minutes=MINUTOS_DESPUES) >= ahora:
            return {
                "instante_utc": t.isoformat(),
                "inicio_bloqueo_utc": (t - timedelta(minutes=MINUTOS_ANTES)).isoformat(),
                "fin_bloqueo_utc": (t + timedelta(minutes=MINUTOS_DESPUES)).isoformat(),
                "divisa": div,
                "titulo": titulo,
                "edad_h": round(edad, 1),
            }
    return None


def a_avisar(minutos_antes, ahora=None):
    """Eventos cuyo bloqueo empieza dentro de los proximos `minutos_antes` (y aun no ha empezado)."""
    ahora = ahora or datetime.now(timezone.utc)
    try:
        eventos, _ = _eventos()
    except RuntimeError:
        return []
    limite = ahora + timedelta(minutes=minutos_antes)
    return [(t, div, titulo) for t, div, titulo in restringidos(eventos)
            if ahora < t - timedelta(minutes=MINUTOS_ANTES) <= limite]


def _semana(eventos):
    print(f"{'Cuando (Madrid)':<17} {'Div':<4} Evento")
    tz = timezone(timedelta(hours=2))      # CEST en octubre
    for t, div, titulo in restringidos(eventos):
        print(f"{t.astimezone(tz):%a %d/%m %H:%M}  {div:<4} {titulo}")
    print(f"\nTotal: {len(restringidos(eventos))} eventos restringidos")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--semana", action="store_true")
    g.add_argument("--probar", metavar="SIMBOLO")
    args = ap.parse_args()
    if args.semana:
        eventos, edad = _eventos()
        print(f"(copia de {edad:.1f} h)")
        _semana(eventos)
    else:
        bloq, motivo = bloqueo_entrada(args.probar)
        print(f"{args.probar}: {'BLOQUEADA' if bloq else 'libre'}" + (f" · {motivo}" if motivo else ""))


if __name__ == "__main__":
    main()
