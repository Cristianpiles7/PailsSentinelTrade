#!/usr/bin/env python3
"""
Script de humo — Fase 1 del rediseño de estrategias.
Verifica FeatureBuilder + AdaptiveTFSelector sobre datos reales cacheados de los
símbolos piloto, sin necesidad de MT5 en vivo tras la primera descarga.

Uso:
    python Tools/pst_ml_smoke.py --symbols US100.cash,US30.cash,XAUUSD,EURUSD --days 20
"""
import sys
import os
import argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Tools.ml_data import get_full_mtf_data  # noqa: E402
from PST_Core.ml.feature_builder import FeatureBuilder  # noqa: E402
from PST_Core.ml.tf_selector import AdaptiveTFSelector  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default="US100.cash,US30.cash,XAUUSD,EURUSD")
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]

    for sym in symbols:
        print(f"\n{'='*70}\n  {sym}\n{'='*70}")
        data = get_full_mtf_data(sym, args.days, args.refresh)
        if not data:
            print(f"  SIN DATOS para {sym}, saltando.")
            continue

        # mtf_data en el formato que consumen las estrategias: dict tf -> df.
        mtf_data = {tf: data[tf] for tf in ("m2", "m3", "m5", "m15", "m30", "h1", "h4") if tf in data}

        # --- AdaptiveTFSelector: recorrer el histórico M5 barra a barra y ver
        # cómo varía la selección de TF con la volatilidad relativa. ---
        selector = AdaptiveTFSelector(anchor_tf="m5")
        m5 = mtf_data["m5"]
        n = len(m5)
        sample_idxs = [int(n * f) for f in (0.3, 0.5, 0.7, 0.9, 0.99)]
        print("  AdaptiveTFSelector (muestreado a lo largo del histórico M5):")
        for idx in sample_idxs:
            sliced = {"m5": m5.iloc[:idx]}
            tf_chosen, diag = selector.select(sliced)
            pct = diag.get("atr_pct_rank")
            pct_s = f"{pct:5.1f}" if pct is not None else "  N/A"
            print(f"    barra {idx:>6}/{n} | ATR pct_rank {pct_s} | raw={diag.get('raw_tf'):>4} -> elegido={tf_chosen:>4}")

        # --- FeatureBuilder: snapshot completo sobre el TF elegido al final. ---
        final_tf, final_diag = selector.select(mtf_data)
        print(f"\n  TF activo (último estado): {final_tf}  (diag={final_diag})")

        feats = FeatureBuilder.build(mtf_data, active_tf=final_tf, symbol=sym, htf_tf="h1")
        print(f"  FeatureBuilder.build() -> {len(feats)} features:")
        for name in FeatureBuilder.FEATURE_NAMES:
            print(f"    {name:<24} {feats[name]: .4f}")

        vec = FeatureBuilder.vector(feats)
        print(f"  vector() -> len={len(vec)} (debe == {len(FeatureBuilder.FEATURE_NAMES)})")
        assert len(vec) == len(FeatureBuilder.FEATURE_NAMES), "Vector length mismatch!"

        # --- Sanity check de build_series() vs build(): el último valor de la serie
        # debe coincidir exactamente con el snapshot (garantía anti-divergencia). ---
        series_map = FeatureBuilder.build_series(mtf_data[final_tf])
        mismatches = []
        for name in FeatureBuilder._SERIES_FEATURE_NAMES:
            last_series_val = FeatureBuilder._last(series_map.get(name))
            if abs(last_series_val - feats[name]) > 1e-9:
                mismatches.append((name, last_series_val, feats[name]))
        if mismatches:
            print(f"  [FAIL] MISMATCH build() vs build_series(): {mismatches}")
        else:
            print(f"  [OK] build() y build_series() coinciden exactamente (sin drift train/live).")

    print("\nSmoke test finalizado.\n")
    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    main()
