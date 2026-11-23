"""
Resumen estadistico de las operaciones propias de un agente LLM, para el estado que recibe el modelo.

Funciones puras (sin MT5 ni BBDD): llm_trader.snapshot() les pasa el historial ya leido con
historial_propio(), y asi se pueden probar con un historial de ejemplo (tests/test_prompt_llm_trader.py).
"""

N_ULTIMAS = 20          # operaciones cerradas que entran en la estadistica (pendiente de confirmar en la spec)


def aciertos_minimos_pct(tp_r):
    """Porcentaje de aciertos que compensa antes de costes con ese tp_r: 1 / (1 + tp_r)."""
    return round(100 / (1 + tp_r), 1)


def resumen(operaciones, tp_r_referencia):
    """
    `operaciones`: lista de dicts con 'net_eur' y 'R' (la mas antigua primero, como historial_propio).
    Acierto = operacion con neto positivo tras costes. R medio = media de los R conocidos.
    `tp_r_referencia`: el tp_r con el que se calcula el minimo de aciertos (el minimo permitido).
    """
    minimo = aciertos_minimos_pct(tp_r_referencia)
    if not operaciones:
        return {"operaciones": 0, "aviso": "sin operaciones previas: no hay tasa de aciertos ni R medio",
                "aciertos_minimos_pct": minimo, "tp_r_referencia": tp_r_referencia}
    ganadoras = sum(1 for o in operaciones if (o.get("net_eur") or 0) > 0)
    rs = [o["R"] for o in operaciones if o.get("R") is not None]
    return {"operaciones": len(operaciones),
            "tasa_aciertos_pct": round(100 * ganadoras / len(operaciones), 1),
            "r_medio": round(sum(rs) / len(rs), 2) if rs else None,
            "aciertos_minimos_pct": minimo, "tp_r_referencia": tp_r_referencia}
