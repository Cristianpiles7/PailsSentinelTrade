"""
Vigia de salud: proceso aparte que comprueba el latido de la app (PST_Core/utils/salud.py) y avisa por Telegram
si la app lleva demasiado tiempo sin latir. No reinicia nada: un reinicio automatico sobre una cuenta real
puede hacer mas dano que una caida, asi que la decision la tomas tu.

  python PST_UTIL/vigia_salud.py            # bucle cada 60 s
  python PST_UTIL/vigia_salud.py --una-vez  # una comprobacion y sale (para el programador de tareas)

Limite: si el PC entero esta apagado, no puede avisar. Solo ve caidas de la app con el PC encendido.
"""
import argparse
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from PST_Core.utils import alertas, salud  # noqa: E402

UMBRAL_S = 5 * 60         # sin latido en 5 min = caida
PAUSA_S = 60
REAVISO_S = 3600          # mientras siga caida, un aviso por hora


def comprobar():
    d = salud.leer()
    if d is None:
        alertas.avisar("latido", "No hay latido de la app: no esta en marcha o nunca ha arrancado.", REAVISO_S)
        return False
    edad = time.time() - d.get("t", 0)
    if edad > UMBRAL_S:
        alertas.avisar("latido", f"La app no late desde hace {edad / 60:.0f} min. Revisa si sigue en marcha.", REAVISO_S)
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description="Vigia de salud de la app por latido")
    ap.add_argument("--una-vez", action="store_true")
    a = ap.parse_args()
    if a.una_vez:
        comprobar()
        return
    while True:
        comprobar()
        time.sleep(PAUSA_S)


if __name__ == "__main__":
    main()
