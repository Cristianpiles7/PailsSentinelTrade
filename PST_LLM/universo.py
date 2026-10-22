"""
Universo de la flota LLM: los simbolos que tienen (o tendran) agente, agrupados por clase de mercado.

Estructura (decidida 2026-10-22): un agente de estrategia por clase de mercado, y un subagente por
simbolo de esa clase, activo solo durante la sesion de su mercado. Ver docs/PLAN_AGENTES_V2.md.

Magic: uno por simbolo, en 109005-109035 (el revisor vigila 109001-109199, ver lt.MAGICS_FLOTA).
Los magic 109001-109004 pertenecen a agentes antiguos ya parados (BTC, ETH, XAU, EURUSD) y no se reutilizan.

Modelo: Sonnet para los simbolos principales, Haiku para el resto (decision del usuario).

Comprobar: python PST_LLM/universo.py
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Simbolo:
    symbol: str          # nombre exacto en el broker (verificado en Market Watch 2026-10-22)
    clase: str           # indice | accion_eeuu | accion_eu | cripto | materias
    magic: int
    modelo: str          # sonnet | haiku


# Pool de rotacion: todo lo que puede operar el sistema (indices y acciones de EE. UU. y Europa).
# No se excluye nada por resultados de juez de estrategias antiguas (TA): no dicen nada de los
# agentes LLM. La seleccion la hace rotacion.py con puntuacion fija, no esta lista.
UNIVERSO = [
    # Indices
    Simbolo("US500.cash", "indice", 109005, "sonnet"),
    Simbolo("US100.cash", "indice", 109006, "sonnet"),
    Simbolo("US30.cash", "indice", 109007, "sonnet"),
    Simbolo("GER40.cash", "indice", 109008, "sonnet"),
    Simbolo("EU50.cash", "indice", 109009, "haiku"),
    Simbolo("UK100.cash", "indice", 109010, "haiku"),
    Simbolo("FRA40.cash", "indice", 109035, "haiku"),
    # Acciones de EE. UU. (sesion NY)
    Simbolo("MSFT", "accion_eeuu", 109011, "haiku"),
    Simbolo("AVGO", "accion_eeuu", 109012, "haiku"),
    Simbolo("BRK.B", "accion_eeuu", 109013, "haiku"),
    Simbolo("XOM", "accion_eeuu", 109014, "haiku"),
    Simbolo("AMD", "accion_eeuu", 109015, "haiku"),
    Simbolo("BA", "accion_eeuu", 109016, "haiku"),
    Simbolo("BABA", "accion_eeuu", 109017, "haiku"),
    Simbolo("CVX", "accion_eeuu", 109018, "haiku"),
    Simbolo("GM", "accion_eeuu", 109019, "haiku"),
    Simbolo("INTC", "accion_eeuu", 109020, "haiku"),
    Simbolo("KO", "accion_eeuu", 109021, "haiku"),
    Simbolo("MCD", "accion_eeuu", 109022, "haiku"),
    Simbolo("NFLX", "accion_eeuu", 109023, "haiku"),
    Simbolo("PLTR", "accion_eeuu", 109024, "haiku"),
    Simbolo("ZM", "accion_eeuu", 109025, "haiku"),
    Simbolo("GOOG", "accion_eeuu", 109026, "haiku"),
    Simbolo("META", "accion_eeuu", 109027, "haiku"),
    Simbolo("AMZN", "accion_eeuu", 109028, "haiku"),
    Simbolo("TSLA", "accion_eeuu", 109029, "haiku"),
    Simbolo("NVDA", "accion_eeuu", 109030, "haiku"),
    Simbolo("AAPL", "accion_eeuu", 109031, "haiku"),
    Simbolo("RTX", "accion_eeuu", 109032, "haiku"),
    Simbolo("FDX", "accion_eeuu", 109033, "haiku"),
    Simbolo("ASML", "accion_eeuu", 109034, "haiku"),
    # Cripto (24/7; los que tienen precio visible en el broker). El coste (spread frente al stop) lo puntua rotacion.py.
    Simbolo("BTCUSD", "cripto", 109036, "haiku"),
    Simbolo("ETHUSD", "cripto", 109037, "haiku"),
    Simbolo("SOLUSD", "cripto", 109038, "haiku"),
    Simbolo("LTCUSD", "cripto", 109039, "haiku"),
    Simbolo("ADAUSD", "cripto", 109040, "haiku"),
    Simbolo("DOTUSD", "cripto", 109041, "haiku"),
    Simbolo("LNKUSD", "cripto", 109042, "haiku"),
    Simbolo("XRPUSD", "cripto", 109043, "haiku"),
    Simbolo("XLMUSD", "cripto", 109044, "haiku"),
    Simbolo("UNIUSD", "cripto", 109045, "haiku"),
    # Materias primas
    Simbolo("XAUUSD", "materias", 109046, "haiku"),
    Simbolo("XAGUSD", "materias", 109047, "haiku"),
    Simbolo("USOIL.cash", "materias", 109048, "haiku"),
    Simbolo("NATGAS.cash", "materias", 109049, "haiku"),
    # Forex: pares mayores con spread bajo. EURUSD (magic 109004) es un magic antiguo, no se reutiliza.
    Simbolo("EURUSD", "forex", 109050, "haiku"),
    Simbolo("GBPUSD", "forex", 109051, "haiku"),
    Simbolo("USDJPY", "forex", 109052, "haiku"),
    Simbolo("AUDUSD", "forex", 109053, "haiku"),
    Simbolo("USDCAD", "forex", 109054, "haiku"),
    Simbolo("USDCHF", "forex", 109055, "haiku"),
]


def por_clase():
    out = {}
    for s in UNIVERSO:
        out.setdefault(s.clase, []).append(s)
    return out


def _comprobar():
    import calendario
    magics = [s.magic for s in UNIVERSO]
    assert len(set(magics)) == len(magics), "magic repetido"
    assert all(109005 <= m <= 109199 for m in magics), "magic fuera del rango del revisor"
    sin_divisas = [s.symbol for s in UNIVERSO if s.symbol not in calendario.DIVISAS]
    assert not sin_divisas, f"sin divisas en calendario.py: {sin_divisas}"
    for clase, lista in por_clase().items():
        print(f"{clase}:")
        for s in lista:
            print(f"  {s.symbol:<11} magic {s.magic}  {s.modelo}")
    print(f"\n{len(UNIVERSO)} simbolos, todos con divisas en calendario.py. OK")


if __name__ == "__main__":
    _comprobar()
