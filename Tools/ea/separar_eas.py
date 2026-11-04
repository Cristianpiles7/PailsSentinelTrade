"""
Migracion v1 -> v2 de los EAs (2026-11-04): un fichero por despliegue (estrategia x simbolo x TF x magic).

Lee MT5/despliegues.json y, para cada despliegue, copia el EA original (campo `origen`) aplicando:
  * simbolo/TF fijos (#define) y comprobados en OnInit (PST_Control_Init),
  * nombre y magic propios,
  * PST_Control.mqh: interruptor de la app, multiplicador de riesgo, cierre a peticion y latido,
  * posicion propia por magic (antes PositionSelect(_Symbol): en hedging podia coger la de otro EA).

Cada sustitucion tiene un numero esperado de apariciones: si no cuadra, el script para sin escribir nada.
Uso (desde repo/):  python Tools/ea/separar_eas.py [--forzar]
Es de un solo uso: a partir de aqui cada EA evoluciona por su cuenta. Se guarda para trazabilidad.
"""
import argparse
import json
import re
import sys
from pathlib import Path

MT5 = Path(__file__).resolve().parents[2] / "MT5"
EXPERTS = MT5 / "Experts"


def sustituir(texto, patron, nuevo, esperado, que, regex=False):
    n = len(re.findall(patron, texto)) if regex else texto.count(patron)
    if esperado == "+" and n == 0 or esperado != "+" and n != esperado:
        raise ValueError(f"{que}: esperado {esperado}, encontrado {n}")
    return re.sub(patron, nuevo, texto) if regex else texto.replace(patron, nuevo)


def convertir(src, d):
    t = src
    cab = (
        "//+------------------------------------------------------------------+\n"
        f"//| {d['id']}.mq5\n"
        f"//| Despliegue: {d['hipotesis']} - {d['estrategia']}\n"
        f"//| Simbolo {d['simbolo']} | TF {d['tf']} | magic {d['magic']} | version {d['version']}\n"
        "//| Un fichero = un despliegue: este EA solo arranca en ese simbolo y TF.\n"
        "//| Control desde la app: MT5/Include/PST_Control.mqh. Catalogo: MT5/CATALOGO_EAS.md\n"
        f"//| Derivado de {d['origen']} (v1, ver git 329ff20). La cabecera original\n"
        "//| sigue debajo; si habla de varios simbolos, aqui ya solo aplica al de arriba.\n"
        "//+------------------------------------------------------------------+\n"
    )
    t = cab + t
    t = sustituir(t, r'#property version\s+"1\.00"', f'#property version   "{d["version"]}0"', 1, "version", regex=True)
    t = sustituir(t, "#include <PST_V2\\PST_OptExport.mqh>",
                  "#include <PST_V2\\PST_OptExport.mqh>\n#include <PST_V2\\PST_Control.mqh>\n\n"
                  f"#define PST_DESPLIEGUE_SIMBOLO \"{d['simbolo']}\"\n"
                  f"#define PST_DESPLIEGUE_TF      PERIOD_{d['tf']}\n"
                  f"#define PST_EA_VERSION         \"{d['version']}\"", 1, "include")
    t = sustituir(t, r'input string   EA_Name             = "[^"]+";',
                  f'input string   EA_Name             = "{d["id"]}";', 1, "EA_Name", regex=True)
    t = sustituir(t, r"input int      InpMagicNumber      = \d+;",
                  f"input int      InpMagicNumber      = {d['magic']};", 1, "magic", regex=True)
    t = sustituir(t, r"int OnInit\(\)\s*\{",
                  "int OnInit()\n  {\n   int ctl = PST_Control_Init(EA_Name, InpMagicNumber, PST_EA_VERSION,\n"
                  "                              PST_DESPLIEGUE_SIMBOLO, PST_DESPLIEGUE_TF);\n"
                  "   if(ctl != INIT_SUCCEEDED)\n      return(ctl);\n", 1, "OnInit", regex=True)
    t = sustituir(t, r"void OnDeinit\(const int reason\)\s*\{",
                  "void OnDeinit(const int reason)\n  {\n   PST_Control_Deinit(reason);", 1, "OnDeinit", regex=True)
    t = sustituir(t, "PositionSelect(_Symbol) && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber",
                  "PST_SelectMyPosition(_Symbol, InpMagicNumber)", 1, "HasOpenPosition")
    t = sustituir(t, "trade.PositionClose(_Symbol)", "PST_CloseMyPosition(trade, _Symbol, InpMagicNumber)", "+"
                  if "trade.PositionClose(_Symbol)" in t else 0, "PositionClose")
    if "trade.PositionModify(_Symbol," in t:
        t = t.replace("trade.PositionModify(_Symbol,", "PST_ModifyMyPosition(trade, _Symbol, InpMagicNumber,")
    t = sustituir(t, "PST_CalcLotByRisk(_Symbol, InpRiskPercent,",
                  "PST_CalcLotByRisk(_Symbol, PST_RiesgoEfectivo(InpRiskPercent),", 2, "lotaje")
    t = sustituir(t, r"\n(\s*)if\(!PST_PortfolioRiskOk\(",
                  r"\n\1// Interruptor de la app (PST_Control): si esta pausado no se abre nada nuevo.\n"
                  r"\1if(!PST_Control_PuedeAbrir())\n\1   return;\n\1if(!PST_PortfolioRiskOk(", 1, "PuedeAbrir", regex=True)
    resto = [l for l in t.splitlines() if "PositionSelect(_Symbol)" in l or "PositionClose(_Symbol" in l]
    if resto:
        raise ValueError(f"quedan selecciones por simbolo: {resto}")
    t = t.rstrip() + ("\n\n//+------------------------------------------------------------------+\n"
                      "void OnTimer()\n  {\n   PST_Control_OnTimer(trade);\n  }\n"
                      "//+------------------------------------------------------------------+\n")
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--forzar", action="store_true", help="sobrescribir si ya existen")
    a = ap.parse_args()
    despliegues = json.loads((MT5 / "despliegues.json").read_text(encoding="utf-8"))["despliegues"]
    salida = {}
    for d in despliegues:
        src = (EXPERTS / d["origen"]).read_text(encoding="utf-8")
        try:
            salida[EXPERTS / d["categoria"] / f"{d['id']}.mq5"] = convertir(src, d)
        except ValueError as e:
            sys.exit(f"{d['id']}: {e}")
    for ruta, texto in salida.items():
        if ruta.exists() and not a.forzar:
            sys.exit(f"{ruta} ya existe (usa --forzar)")
    for ruta, texto in salida.items():
        ruta.write_text(texto, encoding="utf-8", newline="\r\n")
        print("escrito", ruta.relative_to(MT5))
    print(f"{len(salida)} EAs generados")


if __name__ == "__main__":
    main()
