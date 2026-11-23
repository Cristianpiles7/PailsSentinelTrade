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


class BacktestRequerido(unittest.TestCase):
    """Un criterio por simbolo solo se activa con un backtest guardado (BBDD temporal, nunca la real)."""

    CRITERIO = "- Entra en el rechazo del nivel con confirmación en M1."

    def setUp(self):
        import registro
        import sqlite3
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._ruta = os.path.join(self._tmp.name, "test.db")
        con = sqlite3.connect(self._ruta)
        con.execute("""CREATE TABLE proposals (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_run_id INTEGER,
            kind TEXT, target TEXT, current_json TEXT, proposed_json TEXT, evidence_json TEXT, rationale TEXT,
            state TEXT, created_at TEXT, decided_by TEXT, decided_at TEXT, applied_at TEXT)""")
        con.commit()
        con.close()
        self._orig = registro.central
        registro.central = lambda: sqlite3.connect(self._ruta)
        self.addCleanup(self._restaurar)

    def _restaurar(self):
        import registro
        registro.central = self._orig
        self._tmp.cleanup()

    def test_sin_backtest_no_se_activa(self):
        with self.assertRaises(ValueError):
            prompts.guardar("EURUSD", self.CRITERIO)
        self.assertTrue(prompts.activo("EURUSD")["defecto"])

    def test_con_backtest_guardado_se_activa(self):
        prompts.registrar_backtest("EURUSD", self.CRITERIO, {"operaciones": 120, "pf": 1.4})
        act = prompts.guardar("EURUSD", self.CRITERIO)
        self.assertEqual(act["texto"], self.CRITERIO)
        self.assertFalse(act["defecto"])

    def test_backtest_de_otro_criterio_no_sirve(self):
        prompts.registrar_backtest("EURUSD", self.CRITERIO + " otro", {"pf": 1.1})
        with self.assertRaises(ValueError):
            prompts.guardar("EURUSD", self.CRITERIO)

    def test_backtest_es_por_simbolo(self):
        prompts.registrar_backtest("EURUSD", self.CRITERIO, {"pf": 1.4})
        with self.assertRaises(ValueError):
            prompts.guardar("GBPUSD", self.CRITERIO)

    def test_backtest_sin_metricas_se_rechaza(self):
        with self.assertRaises(ValueError):
            prompts.registrar_backtest("EURUSD", self.CRITERIO, {})

    def test_restaurar_defecto_no_pide_backtest(self):
        prompts.registrar_backtest("EURUSD", self.CRITERIO, {"pf": 1.4})
        prompts.guardar("EURUSD", self.CRITERIO)
        act = prompts.restaurar_defecto("EURUSD")
        self.assertEqual(act["texto"], prompts.defecto_de("EURUSD"))


if __name__ == "__main__":
    unittest.main()
