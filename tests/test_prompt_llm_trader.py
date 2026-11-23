"""
Pruebas del prompt y del estado de los agentes LLM de trading (LOCAL-13).

Ejecutar desde la raiz del repo:  python -m unittest discover -s tests -v
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "PST_LLM"))

import estadistica_propia as ep   # noqa: E402
import llm_trader as lt           # noqa: E402
import prompts                    # noqa: E402


class PromptCoherente(unittest.TestCase):
    """CA-1 y CA-2: una sola regla de entrada y sin frases de estilo repetidas."""

    def setUp(self):
        self.completo = lt.montar_prompt(prompts.CRITERIO_DEFECTO)

    def test_ca1_no_hay_a_la_vez_quedarse_fuera_y_no_quedarse_fuera(self):
        tiene_quedarse_fuera = re.search(r"quédate fuera|quedate fuera|stay out by default", self.completo, re.I)
        tiene_no_quedarse = re.search(r"no te quedes fuera|no quedes fuera|do not stay out", self.completo, re.I)
        self.assertTrue(tiene_quedarse_fuera, "el criterio por defecto deberia dar la regla de entrada")
        self.assertIsNone(tiene_no_quedarse, "el prompt completo contradice la regla de entrada")

    def test_ca2_frases_del_criterio_no_repetidas_en_el_prompt_fijo(self):
        for linea in prompts.CRITERIO_DEFECTO.splitlines():
            frase = linea.strip().removeprefix("- ").strip()
            if frase:
                self.assertNotIn(frase, lt.PROMPT_FIJO, f"frase repetida en el prompt fijo: {frase!r}")


class EstadisticaPropia(unittest.TestCase):
    """CA-3, CA-4 y CA-5: tasa de aciertos, R medio, minimo de aciertos y historial vacio."""

    HISTORIAL = [  # de la mas antigua a la mas reciente; neto positivo = acierto
        {"net_eur": 10.0, "R": 1.0},
        {"net_eur": -5.0, "R": -0.5},
        {"net_eur": 4.0, "R": 0.5},
        {"net_eur": -8.0, "R": -1.0},
        {"net_eur": 6.0, "R": 1.0},
    ]

    def test_ca3_tasa_de_aciertos_y_r_medio_del_historial_de_ejemplo(self):
        r = ep.resumen(self.HISTORIAL, 1.8)
        self.assertEqual(r["operaciones"], 5)
        self.assertEqual(r["tasa_aciertos_pct"], 60.0)   # 3 de 5 con neto positivo
        self.assertEqual(r["r_medio"], 0.2)              # (1 - 0,5 + 0,5 - 1 + 1) / 5

    def test_ca3_r_medio_ignora_operaciones_sin_R(self):
        r = ep.resumen([{"net_eur": 1.0, "R": None}, {"net_eur": -1.0, "R": -1.0}], 1.8)
        self.assertEqual(r["r_medio"], -1.0)
        self.assertEqual(r["tasa_aciertos_pct"], 50.0)

    def test_ca4_minimo_de_aciertos_con_tp_r_1_8(self):
        self.assertEqual(ep.aciertos_minimos_pct(1.8), 35.7)
        self.assertEqual(ep.resumen(self.HISTORIAL, 1.8)["aciertos_minimos_pct"], 35.7)

    def test_ca5_historial_vacio_no_falla_y_avisa(self):
        r = ep.resumen([], 1.8)
        self.assertEqual(r["operaciones"], 0)
        self.assertIn("sin operaciones previas", r["aviso"])
        self.assertNotIn("tasa_aciertos_pct", r)
        self.assertEqual(r["aciertos_minimos_pct"], 35.7)


if __name__ == "__main__":
    unittest.main()
