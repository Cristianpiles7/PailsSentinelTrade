#!/usr/bin/env python3
"""
Fase 4 — Pipeline de etiquetado + entrenamiento MANUAL (LightGBM) para
PST-MomentumPulse / PST-DivergenceReversion. Reentrenamiento siempre bajo demanda
del usuario — sin scheduling automático.

Simplificaciones deliberadas de v1 (documentadas, no descuidos):
  · Entrena sobre el TF ancla (m5) de AdaptiveTFSelector, no sobre una réplica exacta
    de qué TF habría elegido el selector barra a barra. Razonable porque
    FeatureBuilder ya normaliza todo (distancias en ATR, percentiles 0-100) — el
    espacio de features es comparable entre timeframes por diseño (ver
    PST_Core/ml/feature_builder.py).
  · Las barreras SL/TP del etiquetado triple-barrera usan el múltiplo ATR por
    defecto de cada estrategia (PSTBacktestEngine.DEFAULT_SL_MULT/DEFAULT_TP_MULT),
    no el SL/TP estructural exacto que la estrategia calcularía en cada barra — así
    el label no depende de los parámetros actuales de una instancia concreta.
  · Un modelo por (estrategia, clase de activo) — con el universo piloto (4 símbolos,
    3 clases de activo) la muestra por grupo es pequeña; esto vale para validar que
    el pipeline funciona de punta a punta, no para tener ya un modelo de producción
    (eso requiere expandir el universo, ver Fase 8 del plan).

Uso:
    python Tools/pst_train_ml.py --strategy PST-MomentumPulse \
        --symbols US100.cash,US30.cash,XAUUSD,EURUSD --days 20
"""
import sys
import os
import argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pandas_ta as ta  # noqa: E402

from Tools.ml_data import get_full_mtf_data  # noqa: E402
from PST_Core.ml.feature_builder import FeatureBuilder  # noqa: E402
from PST_Core.ml.labeling import triple_barrier_labels, walk_forward_embargo_split  # noqa: E402
from PST_Core.ml.model_registry import ModelRegistry  # noqa: E402
from PST_Core.backtesting.engine import PSTBacktestEngine  # noqa: E402
from PST_Core.utils.tech_utils import detect_divergence, get_asset_class  # noqa: E402

# Horizonte del etiquetado triple-barrera, en barras del TF ancla (m5).
MAX_HOLDING_BARS = {"PST-MomentumPulse": 60, "PST-DivergenceReversion": 40}


def _direction_series_momentum(df: pd.DataFrame, feats_series: dict) -> np.ndarray:
    """Gate de MomentumPulse: alineación de EMA 9/21/50 — ya viene vectorizada."""
    align = feats_series.get("ema_alignment")
    if align is None:
        return np.zeros(len(df))
    return align.fillna(0.0).values


def _direction_series_divergence(df: pd.DataFrame, feats_series: dict, window: int = 100) -> np.ndarray:
    """Gate de DivergenceReversion: tipo de divergencia RSI en cada barra. No es
    vectorizable (detect_divergence escanea hacia atrás por diseño) — ventana de
    contexto acotada para que el coste sea O(n) y no O(n^2)."""
    n = len(df)
    directions = np.zeros(n)
    rsi_series = ta.rsi(df["close"], length=14)
    df_rsi = df.copy()
    df_rsi["rsi"] = rsi_series
    for i in range(60, n):
        sub = df_rsi.iloc[max(0, i - window):i]
        div_type, _ = detect_divergence(sub, window=5, order=2, return_pivots=True)
        if div_type == "BULLISH":
            directions[i] = 1.0
        elif div_type == "BEARISH":
            directions[i] = -1.0
    return directions


_DIRECTION_FN = {
    "PST-MomentumPulse": _direction_series_momentum,
    "PST-DivergenceReversion": _direction_series_divergence,
}


def assemble_features_df(df: pd.DataFrame, h1_df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Construye un DataFrame con TODAS las columnas de FeatureBuilder.FEATURE_NAMES,
    alineadas fila a fila con df — el mismo contrato que build() produce en vivo,
    solo que vectorizado sobre toda la serie en vez de un snapshot escalar."""
    feats_series = FeatureBuilder.build_series(df)
    if not feats_series:
        return None, None

    out = pd.DataFrame(index=df.index)
    for name in FeatureBuilder._SERIES_FEATURE_NAMES:
        out[name] = feats_series.get(name, pd.Series(0.0, index=df.index)).values

    # HTF (h1) adx/chop alineado por tiempo — el h1 vigente en cada barra m5 es el
    # último h1 cerrado a esa hora (merge_asof "backward", sin mirar al futuro).
    htf_series = FeatureBuilder.build_series(h1_df) if h1_df is not None else {}
    if htf_series:
        h1_feat_df = pd.DataFrame({
            "time": h1_df["time"].values,
            "htf_adx": htf_series.get("adx", pd.Series(0.0, index=h1_df.index)).values,
            "htf_chop": htf_series.get("chop", pd.Series(50.0, index=h1_df.index)).values,
        })
        merged = pd.merge_asof(
            pd.DataFrame({"time": df["time"].values}).reset_index(drop=True),
            h1_feat_df.sort_values("time"), on="time", direction="backward",
        )
        out["htf_adx"] = merged["htf_adx"].values
        out["htf_chop"] = merged["htf_chop"].fillna(50.0).values
    else:
        out["htf_adx"] = 0.0
        out["htf_chop"] = 50.0

    for k, v in FeatureBuilder.asset_class_features(symbol).items():
        out[k] = v

    sessions = df["time"].apply(lambda t: FeatureBuilder.session_features(t.to_pydatetime()))
    for s_key in (f"session_{s.lower()}" for s in FeatureBuilder._SESSIONS):
        out[s_key] = sessions.apply(lambda d: d.get(s_key, 0.0)).values

    out = out[list(FeatureBuilder.FEATURE_NAMES)].fillna(0.0)
    return out, feats_series


def build_dataset(strategy_name: str, symbol: str, data: dict):
    """Devuelve (X, y) para un símbolo, ya filtrado a filas con setup válido +
    etiqueta resoluble. None si no hay datos suficientes."""
    df = data.get("m5")
    h1_df = data.get("h1")
    if df is None or len(df) < 200:
        return None, None

    feat_df, feats_series = assemble_features_df(df, h1_df, symbol)
    if feat_df is None:
        return None, None

    direction = _DIRECTION_FN[strategy_name](df, feats_series)

    sl_mult = PSTBacktestEngine.DEFAULT_SL_MULT.get(strategy_name, 2.0)
    asset_class = get_asset_class(symbol)
    tp_mult = PSTBacktestEngine.DEFAULT_TP_MULT.get(asset_class, 4.0)
    atr = ta.atr(df["high"], df["low"], df["close"], length=FeatureBuilder.ATR_LEN)
    sl_dist = (atr * sl_mult).fillna(0.0).values
    tp_dist = (atr * tp_mult).fillna(0.0).values

    max_holding = MAX_HOLDING_BARS.get(strategy_name, 50)
    labels = triple_barrier_labels(df, sl_dist, tp_dist, direction, max_holding)

    mask = (direction != 0) & ~np.isnan(labels)
    if mask.sum() == 0:
        return None, None

    X = feat_df.loc[mask, list(FeatureBuilder.FEATURE_NAMES)].values
    y = labels[mask]
    return X, y, asset_class


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strategy", required=True, choices=list(MAX_HOLDING_BARS.keys()))
    ap.add_argument("--symbols", default="US100.cash,US30.cash,XAUUSD,EURUSD")
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--embargo", type=int, default=None, help="Barras de embargo en cada frontera de fold (default = MAX_HOLDING_BARS de la estrategia)")
    args = ap.parse_args()

    strategy_name = args.strategy
    embargo = args.embargo if args.embargo is not None else MAX_HOLDING_BARS[strategy_name]
    symbols = [s.strip() for s in args.symbols.split(",")]

    # Agrupa por clase de activo — un modelo por (estrategia, grupo).
    by_group: dict = {}
    for sym in symbols:
        data = get_full_mtf_data(sym, args.days, args.refresh)
        if not data:
            print(f"  sin datos para {sym}, saltando.")
            continue
        built = build_dataset(strategy_name, sym, data)
        if built[0] is None:
            print(f"  {sym}: sin ejemplos de entrenamiento (0 setups con etiqueta resoluble)")
            continue
        X, y, group = built
        by_group.setdefault(group, []).append((sym, X, y))
        print(f"  {sym} ({group}): {len(y)} ejemplos, {int(y.sum())} positivos ({100*y.mean():.1f}%)")

    if not by_group:
        print("\nSin datos suficientes para entrenar nada. Abortando.")
        return

    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score, precision_score

    for group, entries in by_group.items():
        Xs = np.concatenate([e[1] for e in entries], axis=0)
        ys = np.concatenate([e[2] for e in entries], axis=0)
        n = len(ys)
        print(f"\n{'='*70}\n  Grupo {group} ({strategy_name}) — {n} ejemplos totales ({[e[0] for e in entries]})\n{'='*70}")

        if n < 80:
            print(f"  MUESTRA INSUFICIENTE ({n} < 80) — se necesita más histórico/símbolos "
                  f"de este grupo antes de entrenar algo usable. Saltando.")
            continue

        train_s, val_s, test_s = walk_forward_embargo_split(n, train_frac=0.7, val_frac=0.15, embargo=embargo)
        X_train, y_train = Xs[train_s], ys[train_s]
        X_val, y_val = Xs[val_s], ys[val_s]
        X_test, y_test = Xs[test_s], ys[test_s]
        print(f"  split (cronológico, embargo={embargo}): train={len(y_train)} val={len(y_val)} test={len(y_test)}")

        if len(y_train) < 30 or len(set(y_train)) < 2:
            print("  train insuficiente o de una sola clase — saltando.")
            continue

        train_set = lgb.Dataset(X_train, label=y_train, feature_name=list(FeatureBuilder.FEATURE_NAMES))
        val_set = lgb.Dataset(X_val, label=y_val, reference=train_set) if len(y_val) >= 10 else None

        params = {
            "objective": "binary", "metric": "auc", "verbosity": -1,
            "num_leaves": 15, "min_data_in_leaf": 10, "learning_rate": 0.05,
        }
        callbacks = [lgb.log_evaluation(period=0)]
        if val_set is not None:
            callbacks.append(lgb.early_stopping(stopping_rounds=20, verbose=False))
            booster = lgb.train(params, train_set, num_boost_round=200, valid_sets=[val_set], callbacks=callbacks)
        else:
            booster = lgb.train(params, train_set, num_boost_round=50)

        metrics = {}
        if len(y_test) >= 20 and len(set(y_test)) > 1:
            proba_test = booster.predict(X_test)
            metrics["auc_test"] = float(roc_auc_score(y_test, proba_test))
            metrics["precision_at_50_test"] = float(precision_score(y_test, (proba_test >= 0.5).astype(int), zero_division=0))
            metrics["n_test"] = len(y_test)
            print(f"  TEST (held-out, {len(y_test)} ej.): AUC {metrics['auc_test']:.3f} | "
                  f"precision@0.5 {metrics['precision_at_50_test']:.3f}")
        else:
            metrics["n_test"] = len(y_test)
            print(f"  TEST insuficiente para una AUC fiable (solo {len(y_test)} ej.) — modelo guardado "
                  f"igualmente pero SIN validar; no usar hasta reentrenar con más histórico/símbolos.")
        metrics["n_train"] = len(y_train)
        metrics["n_val"] = len(y_val)
        metrics["symbols"] = [e[0] for e in entries]

        path = ModelRegistry.save(strategy_name, group, booster, list(FeatureBuilder.FEATURE_NAMES), metrics)
        print(f"  Guardado en {path}")

    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    main()
