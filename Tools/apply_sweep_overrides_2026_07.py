#!/usr/bin/env python3
"""
Overrides propuestos por el barrido de parametros (pst_bt_lab.py --sweep, 45d,
--sweep-min-trades 30) + barrido de entry_tf (Tools/sweep_entry_tf.py, 45d).

Cada entrada lleva la evidencia (Delta expectancy del juez fiel, trades de muestra)
en el comentario. Solo se incluyen palancas que la propia herramienta marco como
PASS fiable (no ruido: descarta los casos donde ambas direcciones de una palanca
mejoraban a la vez, sintoma de sobreajuste a la muestra).

USO:
    python Tools/apply_sweep_overrides_2026_07.py               # dry-run: solo imprime
    python Tools/apply_sweep_overrides_2026_07.py --apply        # aplica a la BBDD viva

No toca is_active, riesgo en euros, ni ningun simbolo fuera de esta lista.
"""
import argparse
import json
import sqlite3

DB_PATH = r"C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PST_Core\data\pst_trading.db"

# ------------------------------------------------------------------ #
# scope="top"    -> columna directa de symbol_strategies (sl_mult/min_rr/be_mult/tp_mult)
# scope="filter" -> se fusiona dentro del JSON filter_profile existente
# ------------------------------------------------------------------ #
OVERRIDES = [
    # ---- Grupo EQUITIES: SL demasiado ceñido (sl_mult 1.6 -> 1.9 ATR) ----
    # Patron consistente en 6 acciones distintas, todas mejoran en la misma
    # direccion (nunca al reves) -> senal de grupo, no ruido de un simbolo.
    {"symbol": "BA",   "scope": "top", "sl_mult": 1.9,
     "why": "Δexp +0.145R (80 trades, 45d)"},
    {"symbol": "MCD",  "scope": "top", "sl_mult": 1.9,
     "why": "Δexp +0.143R (64 trades, 45d)"},
    {"symbol": "ARM",  "scope": "top", "sl_mult": 1.9,
     "why": "Δexp +0.092R (92 trades, 45d)"},
    {"symbol": "NFLX", "scope": "top", "sl_mult": 1.9,
     "why": "Δexp +0.087R (65 trades, 45d)"},
    {"symbol": "CVX",  "scope": "top", "sl_mult": 1.9,
     "why": "Δexp +0.082R (59 trades, 45d)"},
    {"symbol": "KO",   "scope": "top", "sl_mult": 1.9,
     "why": "Δexp +0.077R (57 trades, 45d)"},

    # ---- Grupo EQUITIES: apagar salida VWAP dinamica (ya validado en INDEX/CRYPTO) ----
    {"symbol": "MSFT", "scope": "filter", "filter": {"vwap_exit": "off"},
     "why": "Δexp +0.137R (57 trades, 45d)"},
    {"symbol": "GM",   "scope": "filter", "filter": {"vwap_exit": "off"},
     "why": "Δexp +0.090R (86 trades, 45d)"},

    # ---- Grupo INDEX/FOREX: subir entry_threshold (mas selectivo) ----
    {"symbol": "UK100.cash", "scope": "filter", "filter": {"entry_threshold": 80},
     "why": "Δexp +0.139R (61 trades, 45d)"},
    {"symbol": "GER40.cash", "scope": "filter", "filter": {"entry_threshold": 80},
     "why": "Δexp +0.101R (67 trades, 45d)"},
    {"symbol": "AUS200.cash", "scope": "filter", "filter": {"entry_threshold": 76},
     "why": "Δexp +0.094R (94 trades, 45d)"},
    {"symbol": "GBPUSD", "scope": "filter", "filter": {"entry_threshold": 80},
     "why": "Δexp +0.055R (75 trades, 45d)"},
    {"symbol": "US500.cash", "scope": "filter", "filter": {"entry_threshold": 72},
     "why": "Δexp +0.059R (144 trades, 45d) -- sube desde el 68 actual (override especifico)"},

    # ---- BTCUSD: m1_eff_mode ON hoy empeora en la ventana de 45d ----
    {"symbol": "BTCUSD", "scope": "filter", "filter": {"m1_eff_mode": "off"},
     "why": "Δexp +0.152R (36 trades, 45d) -- OJO: muestra mas pequena que el resto, vigilar"},

    # ---- entry_tf: acciones que pasan de negativo/marginal a positivo con M2/M3 ----
    {"symbol": "AVGO", "scope": "filter", "filter": {"entry_tf": "m2"},
     "why": "entry_tf sweep 45d: +0.18R -> +0.46R (46 trades)"},
    {"symbol": "MSFT", "scope": "filter", "filter": {"entry_tf": "m3"},
     "why": "entry_tf sweep 45d: +0.05R -> +0.21R (34 trades)"},
    {"symbol": "INTC", "scope": "filter", "filter": {"entry_tf": "m2"},
     "why": "entry_tf sweep 45d: -0.02R -> +0.11R (46 trades)"},
]

# Palancas descartadas por debilidad (Δexp < 0.05R) o por depender de una unica
# muestra sin confirmacion cruzada -- NO incluidas arriba, solo documentadas:
#   AAPL m1_eff_mode=on (+0.161R pero baseline ya muy negativo, vigilar antes de aplicar)
#   AMD min_rr=2.1 (+0.031R, marginal)
#   BRK.B m1_eff_mode=on (+0.046R, marginal)
#   EU50.cash entry_threshold=80 (+0.030R, marginal)
#   US100.cash entry_threshold=80 (+0.032R, marginal)
#   INTC entry_threshold=68 (+0.041R, marginal Y en direccion opuesta al resto de INDEX/FOREX)
#   US30.cash -- sin ganador (Δexp +0.000R)
#   PLTR m1_eff_mode=on (+0.137R, pero PLTR no tiene edge de base solido, vigilar)
#   BABA entry_threshold=76 (+0.081R, ya cerca del limite de fiabilidad de muestra)


def _fetch_row(conn, symbol):
    cur = conn.execute(
        "SELECT filter_profile, sl_mult, min_rr, be_mult, tp_mult FROM symbol_strategies "
        "WHERE symbol=? AND strategy_name='PST-PrecisionScalping'", (symbol,))
    return cur.fetchone()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Aplica los cambios (por defecto solo imprime)")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    print(f"{'APLICANDO' if args.apply else 'DRY-RUN (nada se escribe)'} -- {len(OVERRIDES)} overrides\n")

    for ov in OVERRIDES:
        sym = ov["symbol"]
        row = _fetch_row(conn, sym)
        if row is None:
            print(f"  ⚠️  {sym}: no encontrado en symbol_strategies, saltando.")
            continue
        cur_fp, cur_sl, cur_rr, cur_be, cur_tp = row
        cur_filter = json.loads(cur_fp) if cur_fp else {}

        if ov["scope"] == "top":
            field = "sl_mult" if "sl_mult" in ov else ("min_rr" if "min_rr" in ov else "tp_mult")
            new_val = ov[field]
            old_val = {"sl_mult": cur_sl, "min_rr": cur_rr, "tp_mult": cur_tp}[field]
            print(f"  {sym:<14} {field}: {old_val} -> {new_val}   ({ov['why']})")
            if args.apply:
                conn.execute(
                    f"UPDATE symbol_strategies SET {field}=? WHERE symbol=? AND strategy_name='PST-PrecisionScalping'",
                    (new_val, sym))
        else:
            merged = dict(cur_filter)
            merged.update(ov["filter"])
            print(f"  {sym:<14} filter_profile: {ov['filter']}   ({ov['why']})")
            print(f"                 antes: {cur_filter}")
            print(f"                 despues: {merged}")
            if args.apply:
                conn.execute(
                    "UPDATE symbol_strategies SET filter_profile=? WHERE symbol=? AND strategy_name='PST-PrecisionScalping'",
                    (json.dumps(merged), sym))

    if args.apply:
        conn.commit()
        print("\n✅ Cambios aplicados y commiteados en la BBDD viva.")
    else:
        print("\nNada escrito. Revisa la lista y relanza con --apply para aplicar.")
    conn.close()


if __name__ == "__main__":
    main()
