"""
Varios agentes LLM en un mismo proceso (punto 2 del plan de optimizacion).

Cada agente se carga como una instancia propia de llm_trader.py: sus globales (BBDD, STOP, config,
magic, sesion claude, riesgo) son independientes. Cada instancia corre en su propio hilo. Los imports
pesados y la conexion a MT5 se comparten. La salida de cada agente va a su propio .log y .err,
como cuando iba en un proceso aparte.

Uso:  python llm_worker.py <fichero.json>
  El json es una lista de agentes. Cada uno tiene:
    "argv": argumentos de llm_trader.py (sin el nombre del script)
    "log":  fichero donde va lo que imprime el agente
    "err":  fichero donde van sus errores
"""
import importlib.util
import json
import sys
import threading
import traceback
from pathlib import Path

SCRIPT = Path(__file__).with_name("llm_trader.py")


def _pst_conectar_mt5(mt5):
    """mt5.initialize() con la ruta del terminal configurado (nunca "el ultimo arrancado").
    Ver PST_Core/utils/terminal_mt5.py y MT5/DESARROLLO_EAS.md (incidente 2026-11-04)."""
    try:
        from PST_Core.utils.terminal_mt5 import conectar
    except ImportError:
        import os as _os
        import sys as _sys
        _d = _os.path.dirname(_os.path.abspath(__file__))
        while not _os.path.isdir(_os.path.join(_d, "PST_Core")) and _os.path.dirname(_d) != _d:
            _d = _os.path.dirname(_d)
        _sys.path.insert(0, _d)
        from PST_Core.utils.terminal_mt5 import conectar
    return conectar(mt5)


class SalidaPorAgente:
    """Reparte lo que escribe cada hilo al fichero de su agente. Lo que no es de un agente va a la consola."""

    def __init__(self, destino_por_hilo, consola):
        self.destino = destino_por_hilo        # nombre de hilo -> fichero
        self.consola = consola

    def _fichero(self):
        return self.destino.get(threading.current_thread().name, self.consola)

    def write(self, texto):
        return self._fichero().write(texto)

    def flush(self):
        for f in set(self.destino.values()) | {self.consola}:
            f.flush()


def cargar_instancia(indice):
    """Carga llm_trader.py como un modulo nuevo, con sus propios globales."""
    spec = importlib.util.spec_from_file_location(f"llm_trader_agente_{indice}", SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def correr_agente(modulo, argv, nombre):
    try:
        modulo.main(argv, mt5_compartido=_MT5)
    except Exception:
        # Un agente que falla no debe tumbar a los demas: se registra y el hilo termina.
        print(f"[worker] {nombre} ha fallado:\n{traceback.format_exc()}", file=sys.stderr, flush=True)


_MT5 = None


def nombre_de(argv):
    """Mismo valor por defecto que llm_trader.py (--nombre)."""
    return argv[argv.index("--nombre") + 1] if "--nombre" in argv else "Agente_LLM"


def main(fichero):
    global _MT5
    agentes = json.loads(Path(fichero).read_text(encoding="utf-8-sig"))
    if not agentes:
        print("[worker] lista de agentes vacia", file=sys.stderr)
        return 1

    import MetaTrader5 as mt5
    if not _pst_conectar_mt5(mt5):
        print(f"[worker] MT5 no disponible: {mt5.last_error()}", file=sys.stderr)
        return 1
    _MT5 = mt5

    # Cada hilo se llama "agente-<nombre>": ese nombre decide a que fichero va su salida
    ficheros, salida_out, salida_err = [], {}, {}
    for a in agentes:
        hilo = f"agente-{nombre_de(a['argv'])}"
        salida_out[hilo] = open(a["log"], "a", encoding="utf-8")
        salida_err[hilo] = open(a["err"], "w", encoding="utf-8")
        ficheros += [salida_out[hilo], salida_err[hilo]]

    consola_out, consola_err = sys.stdout, sys.stderr
    sys.stdout = SalidaPorAgente(salida_out, consola_out)
    sys.stderr = SalidaPorAgente(salida_err, consola_err)
    try:
        hilos = []
        for i, a in enumerate(agentes):
            modulo = cargar_instancia(i)        # instancia propia: globales independientes
            hilo = threading.Thread(target=correr_agente, args=(modulo, a["argv"], nombre_de(a["argv"])),
                                    name=f"agente-{nombre_de(a['argv'])}", daemon=True)
            hilos.append(hilo)
            hilo.start()
        for hilo in hilos:
            hilo.join()
    finally:
        sys.stdout, sys.stderr = consola_out, consola_err
        for f in ficheros:
            f.close()
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("uso: python llm_worker.py <fichero.json>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
