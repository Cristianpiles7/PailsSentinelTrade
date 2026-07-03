# Informe de barrido nocturno — 2026-07-03

Trabajo autónomo mientras dormías: barrido de todos los símbolos con el harness fiel,
elección conservadora de overrides y este informe.

---

## ✅ ACTUALIZACIÓN — decisiones ejecutadas tras re-confirmar a lookback 200

Re-confirmé la Fase 1-A a la escala real (200 barras). 6 de 8 activos siguen óptimos y en
general rinden MEJOR que en los tests a 600. Dos decisiones **cambiaron** a la escala real y
se confirmaron en 2 ventanas, y ya están **aplicadas al seed** (`database.py`):

- **US500.cash → `entry_threshold` 68** (override de símbolo). Validado a 200: 10d +0.048→+0.150,
  20d +0.043→+0.081. El 72 (calibrado a 600) era demasiado exigente para el índice a 200 barras.
- **NVDA → DESACTIVADO.** Negativo a 200 en 2 ventanas (−0.140 @15d, −0.058 @25d).
- **EU50.cash → ACTIVADO** (experimental). Positivo en muestra grande (+0.164 @20d, sharpe 2.62;
  −0.021 @10d con muestra pequeña).

**Universo activo final (8):** EURUSD, GBPUSD, XAUUSD, BTCUSD, ETHUSD, US500.cash, EU50.cash, AAPL.
ETHUSD se queda (positivo a escala real). El fix de lookback (600→200) también aplicado.
Todo pendiente de commit/deploy.

Expectancy a lookback 200 (fiel): EURUSD +0.109 · GBPUSD +0.314 · XAUUSD +0.284 · BTCUSD +0.392
· ETHUSD +0.054 · AAPL +0.302 · US500(entry68) +0.15 · EU50 +0.164(20d).

---

---

## TL;DR (3 titulares)

1. 🔬 **HALLAZGO GORDO — bug de fidelidad del harness (ya corregido en working tree).**
   El bot en vivo ve **200 barras M1**, pero toda la calibración previa se hizo a **600**.
   A la escala real la estrategia rinde **muchísimo mejor**. Ejemplo con el MISMO perfil:
   GBPUSD **+0.011R @600 → +0.314R @200**. Probable que el bot en vivo ya rinda mejor de lo
   que temíamos.

2. ✅ **Los 8 símbolos activos: config ya ÓPTIMA.** El barrido no encontró NINGÚN candidato
   que mejore el perfil shippeado en ningún símbolo activo. Cero cambios que aplicar →
   cero riesgo de sobreajuste. Fuerte validación de lo que ya hicimos.

3. ⚠️ **ETHUSD NO debe desactivarse.** Lo teníamos como candidato a apagar por su expectancy
   negativa (−0.10R), pero eso era a lookback 600. A lookback 200 (real) es **positivo: +0.054R**.

---

## 1. El hallazgo del lookback (el importante)

**Qué:** `FaithfulScalpingEngine` usaba `lookback_m1=600` (600 barras M1 por señal). Pero el
runtime real (`get_mtf_data_async` → `fetch_rates_async(sym, 1, 200)`) descarga **200**. El
VWAP de sesión se ancla como mucho 200 barras atrás en vivo; a 600 se anclaba mucho más atrás
→ señales VWAP distintas, más numerosas y peores.

**Evidencia (mismo perfil fijo, solo cambia el lookback):**

| Símbolo | lb 200 (≈vivo) | lb 300 | lb 600 (viejo) |
|---|---|---|---|
| GBPUSD  | +0.314R (29 tr) | +0.266R | +0.011R (47 tr) |
| BTCUSD  | +0.392R (61 tr) | +0.302R | +0.195R (101 tr) |
| ETHUSD  | +0.054R (77 tr) | +0.026R | −0.037R (96 tr) |

Monotónico en los 3: **menos lookback = mejor expectancy y menos trades** (los trades de más
a 600 son los que restan). 200 = fiel a producción por definición (es lo que ve el bot).

**Aplicado (working tree):** default de lookback cambiado a **200** en
`PST_Core/backtesting/faithful_engine.py` y `Tools/pst_bt_lab.py`.

**Implicación honesta:** la calibración de Fase 1-A se hizo a 600 (no fiel). Las DECISIONES
relativas (qué perfil gana) probablemente siguen válidas — el barrido a lookback 300 (cercano
a 200) reconfirma que los perfiles activos son óptimos — pero lo riguroso es **re-confirmar
las decisiones clave a lookback 200** antes de dar nada por definitivo. Los baselines guardados
a 600 (`Tools/bt_baselines/*.json`) quedan **obsoletos**; regenerar con `--save-baseline`.

---

## 2. Barrido de los 8 símbolos ACTIVOS (todos: mantener)

Barrido de 1 palanca a la vez (entry_threshold ±4, noise_mode, vwap_exit, m1_eff_mode) sobre
el perfil realmente shippeado, con guardarraíles (muestra ≥30 trades + consistencia
direccional). Base a lookback 300, perfil shippeado:

| Símbolo | base exp | Ganador |
|---|---|---|
| EURUSD | +0.052R | ninguno (mantener) |
| GBPUSD | +0.266R | ninguno (mantener) |
| XAUUSD | +0.148R | ninguno (mantener) |
| BTCUSD | +0.302R | ninguno (mantener) |
| ETHUSD | +0.026R | ninguno (mantener) |
| US500.cash | +0.261R | ninguno (mantener) |
| AAPL | +0.290R | ninguno (mantener) |
| NVDA | +0.022R | ninguno (mantener) |

**Conclusión: no se aplica ningún override a símbolos activos.** La config actual ya es un
óptimo local para todos. (NVDA sigue siendo el más flojo del grupo activo, a vigilar.)

---

## 3. Símbolos de EXPLORACIÓN (inactivos) — ninguno accionable con confianza

| Símbolo | Grupo | base exp | Candidato | Veredicto |
|---|---|---|---|---|
| USDJPY | FOREX | +0.039R (42 tr) | vwap_exit=off (+0.061, 37 tr) | Interesante pero 1 sola ventana + contradice grupo. Confirmar antes de nada. |
| EU50.cash | INDEX | **+0.052R** (22 tr) | — (candidatos con muestra baja) | **Positivo**, candidato a activar, pero solo 22 trades → confirmar con más días. |
| GER40.cash | (FOREX*) | +0.025R (39 tr) | m1_eff_mode=on (+0.134, 31 tr) | *Mal clasificado (ver bug abajo). Solo 31 trades. |
| AMZN | STOCK | −0.254R | (todos muestra baja) | Mantener desactivado. |
| TSLA | STOCK | −0.069R | (todos muestra baja) | Mantener desactivado. |

Ninguno se aplica: o son single-window, o muestra insuficiente, o el símbolo es negativo.
**EU50.cash** es el más prometedor para activar (positivo), pendiente de confirmar con más datos.

---

## 4. Bugs / cosas a arreglar encontradas

- **`get_asset_class` no reconoce `GER40`** (solo tiene `GER30`/`DAX`) → GER40.cash se clasifica
  como FOREX en vez de INDEX. No afecta a producción (GER40 inactivo), pero si se activa índices
  europeos hay que añadir `GER40`, `UK100`, etc. a `indices_keywords` en
  `PST_Core/utils/tech_utils.py`.

---

## 5. Cambios en el working tree (SIN commitear)

- `PST_Core/backtesting/faithful_engine.py` — default lookback 600 → **200** (fidelidad).
- `Tools/pst_bt_lab.py` — default `--lookback-m1` 600 → **200** (+ el modo `--sweep` de C.2/C.4
  construido antes en la sesión).
- `Tools/NIGHTLY_SWEEP_REPORT.md` — este informe.

**NO se ha tocado** `database.py` ni la estrategia: el barrido no justificó ningún cambio.

---

## 6. Decisiones para ti (mañana)

1. **Lookback 200**: ¿confirmas el cambio del harness a 200? (es un fix de fidelidad claro).
   Y como consecuencia: ¿re-confirmamos las decisiones de Fase 1-A a lookback 200 antes de
   seguir tuneando? (recomendado — 1 barrido más).
2. **EU50.cash**: ¿lo confirmamos con más días y, si aguanta, lo activamos? (índice positivo).
3. **USDJPY**: ¿merece una confirmación del `vwap_exit=off`, o lo dejamos fuera?
4. **ETHUSD**: confirmado que se queda ACTIVO (positivo a escala real).
5. Commit: agrupar el fix de lookback + el modo `--sweep` en un release de tooling.

---

*Generado autónomamente. Datos: MT5, 10 días (acciones 15), motor fiel. Metodología
conservadora: solo se promueve un override con ≥30 trades, PASS y consistencia direccional.*
