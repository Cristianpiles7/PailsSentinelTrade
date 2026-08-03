#!/usr/bin/env python3
"""
Barrido de entry_tf (M1 vs M2 vs M3) por símbolo, sobre el perfil REALMENTE shippeado
en la BBDD viva (validation_tf fijo en M5). Usa el motor fiel (FaithfulScalpingEngine).

No escribe nada en la BBDD — solo reporta expectancy/sharpe/trades por variante.
"""
import sys
import os
import json
import sqlite3
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pst_bt_lab import get_data, run_variant, metrics, load_working_tree  # noqa: E402

DB_PATH = r"C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PST_Core\data\pst_trading.db"
DAYS = 45
THRESHOLD = 70
MIN_TRADES = 30


def _shipped_rows():
    c = sqlite3.connect(DB_PATH)
    rows = c.execute(
        "SELECT symbol, filter_profile, sl_mult, min_rr, be_mult, tp_mult "
        "FROM symbol_strategies WHERE strategy_name='PST-PrecisionScalping' AND is_active=1 "
        "ORDER BY symbol"
    ).fetchall()
    c.close()
    return rows


async def main():
    WT = load_working_tree()
    rows = _shipped_rows()
    print(f"# Sweep entry_tf (M1/M2/M3, validation_tf=M5) — {len(rows)} símbolos, {DAYS}d\n")

    summary = []
    for symbol, fp_json, sl_mult, min_rr, be_mult, tp_mult in rows:
        data = get_data(symbol, DAYS, refresh=True)
        if not data:
            print(f"  ⚠️  sin datos para {symbol}, saltando.")
            continue
        base_filter = json.loads(fp_json) if fp_json else {}
        top = {"sl_mult": sl_mult, "min_rr": min_rr, "be_mult": be_mult, "tp_mult": tp_mult}

        variant_results = {}
        for tf in ("m1", "m2", "m3"):
            f = dict(base_filter)
            f["entry_tf"] = tf
            f["validation_tf"] = "m5"
            profile = {"filter_profile": json.dumps(f), **top}
            res = await run_variant(WT, symbol, data, THRESHOLD, 200, profile)
            variant_results[tf] = metrics(res)

        m1r = variant_results["m1"]
        print(f"\n{'='*72}\n  {symbol}\n{'='*72}")
        print(f"  {'tf':<4}{'trades':>8}{'win%':>8}{'PF':>8}{'sharpe':>9}{'expR':>9}{'Δexp vs M1':>12}")
        best_tf, best_exp = "m1", m1r["expectancy"]
        for tf in ("m1", "m2", "m3"):
            m = variant_results[tf]
            dexp = m["expectancy"] - m1r["expectancy"]
            print(f"  {tf.upper():<4}{m['trades']:>8}{m['win_rate']:>8.1f}{m['profit_factor']:>8.2f}"
                  f"{m['sharpe']:>9.2f}{m['expectancy']:>+9.3f}{dexp:>+12.3f}")
            if m["trades"] >= MIN_TRADES and m["expectancy"] > best_exp:
                best_tf, best_exp = tf, m["expectancy"]

        reliable = variant_results[best_tf]["trades"] >= MIN_TRADES and variant_results["m1"]["trades"] >= MIN_TRADES
        if best_tf != "m1" and reliable:
            summary.append((symbol, best_tf, best_exp - m1r["expectancy"], variant_results[best_tf]["trades"]))

    print(f"\n\n{'#'*72}\n#  CANDIDATOS (entry_tf distinto de M1 mejora expectancy, muestra >= {MIN_TRADES} trades)\n{'#'*72}")
    if not summary:
        print("  Ninguno. M1 sigue siendo el mejor timeframe de entrada en todo el universo probado.")
    else:
        for sym, tf, dexp, n in sorted(summary, key=lambda r: -r[2]):
            print(f"  {sym:<14} → {tf.upper()}  (Δexp {dexp:+.3f}R, {n} trades)")


if __name__ == "__main__":
    asyncio.run(main())
