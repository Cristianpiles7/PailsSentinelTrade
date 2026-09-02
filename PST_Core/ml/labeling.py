"""
Etiquetado triple-barrera para entrenamiento offline + split walk-forward con
embargo (guardia estándar contra fuga de datos: una etiqueta cerca de la frontera
de un fold depende de barras que caen en el fold siguiente, así que esas barras se
descartan de la muestra más antigua en vez de dejarlas "ver" el futuro del fold que
viene después).
"""
import numpy as np
import pandas as pd


def triple_barrier_labels(df: pd.DataFrame, sl_dist, tp_dist, direction, max_holding_bars: int) -> np.ndarray:
    """
    Para cada barra i (salvo las últimas max_holding_bars), simula abrir una posición
    en direction[i] al close[i] con SL a sl_dist[i] y TP a tp_dist[i], y mira qué
    barrera toca primero dentro de las siguientes max_holding_bars velas.

    sl_dist/tp_dist/direction: escalares o array-like de longitud len(df).
    Devuelve un array float: 1.0 = TP tocado antes que SL/timeout, 0.0 = SL tocado o
    timeout sin tocar ninguna, np.nan = sin barras futuras suficientes para evaluar
    o setup inválido (dirección 0 / distancias <= 0).
    """
    n = len(df)
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values

    sl_arr = np.full(n, sl_dist, dtype=float) if np.isscalar(sl_dist) else np.asarray(sl_dist, dtype=float)
    tp_arr = np.full(n, tp_dist, dtype=float) if np.isscalar(tp_dist) else np.asarray(tp_dist, dtype=float)
    dir_arr = np.full(n, direction, dtype=float) if np.isscalar(direction) else np.asarray(direction, dtype=float)

    labels = np.full(n, np.nan)
    for i in range(max(0, n - max_holding_bars)):
        d = dir_arr[i]
        if d == 0 or sl_arr[i] <= 0 or tp_arr[i] <= 0:
            continue
        entry = close[i]
        sl_price = entry - d * sl_arr[i]
        tp_price = entry + d * tp_arr[i]
        label = 0.0
        for j in range(i + 1, i + 1 + max_holding_bars):
            if d == 1:
                hit_sl, hit_tp = low[j] <= sl_price, high[j] >= tp_price
            else:
                hit_sl, hit_tp = high[j] >= sl_price, low[j] <= tp_price
            # Empate intrabar: SL gana (mismo criterio conservador que
            # PSTBacktestEngine.run(), que también resuelve SL antes que TP).
            if hit_sl:
                label = 0.0
                break
            if hit_tp:
                label = 1.0
                break
        labels[i] = label
    return labels


def walk_forward_embargo_split(n: int, train_frac: float = 0.7, val_frac: float = 0.15, embargo: int = 0):
    """Split cronológico (nunca barajado) en train/val/test, con `embargo` barras
    descartadas al final de train y de val — su etiqueta triple-barrera podría
    depender de barras que caen en el fold siguiente.

    Devuelve (train_slice, val_slice, test_slice)."""
    train_end = int(n * train_frac)
    val_end = int(n * (train_frac + val_frac))
    train_slice = slice(0, max(0, train_end - embargo))
    val_slice = slice(train_end, max(train_end, val_end - embargo))
    test_slice = slice(val_end, n)
    return train_slice, val_slice, test_slice
