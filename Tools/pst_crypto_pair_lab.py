#!/usr/bin/env python3
"""
PST · Laboratorio de validación — PST-CryptoRelativeStrength (BTC<->ETH)
────────────────────────────────────────────────────────────────────────
Valida la estrategia de rotación de fuerza relativa (PST_Core/strategies/
pst_crypto_relative_strength.py) sobre datos reales cacheados de BTCUSD/ETHUSD.
No usa PSTBacktestEngine ni FaithfulMLEngine (ambos asumen una estrategia de UN
símbolo) — implementa su propio bucle simple porque esta estrategia necesita los
dos símbolos a la vez, ninguno de los motores existentes lo soporta.

Simulación DELIBERADAMENTE simple (SL/TP fijos, sin BE/trailing/parciales,
spread estático como el resto de motores del proyecto): el objetivo aquí es medir
si la TESIS (rezago BTC<->ETH) tiene edge antes de invertir en una réplica fiel
de gestión — si el resultado es prometedor, ESE sería el siguiente paso natural
(mismo orden que se siguió con MomentumPulse/DivergenceReversion: reglas ->
smoke -> motor fiel -> solo entonces ML/activación).

Costes: comisión pct_notional de cripto (cost_model.commission_r) + medio spread
en entrada y salida (mismo patrón que faithful_engine.py/faithful_ml_engine.py).
Sin swap (horizonte esperado corto, TP capado por duración).

Uso:
    python Tools/pst_crypto_pair_lab.py --days 20
    python Tools/pst_crypto_pair_lab.py --days 20 --threshold 40
"""
import sys
import os
import argparse
import asyncio

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas_ta as pta  # noqa: E402
from Tools.ml_data import get_full_mtf_data  # noqa: E402
from PST_Core.strategies.pst_crypto_relative_strength import PSTCryptoRelativeStrength  # noqa: E402
from PST_Core.backtesting.cost_model import commission_r  # noqa: E402

WINDOW = 100  # ventana deslizante pasada a evaluate_pair — SWING_LOOKBACK=20/ATR,RSI=14/LOOKBACK_BARS=12 sobran margen


class Trade:
    def __init__(self, symbol, direction, entry_time, entry_price, sl_price, tp_price, half_spread):
        self.symbol = symbol
        self.direction = direction
        self.entry_time = entry_time
        self.entry_price = entry_price
        self.sl_price = sl_price
        self.tp_price = tp_price
        self.half_spread = half_spread
        self.exit_time = None
        self.exit_price = None
        self.exit_reason = None
        self.pnl_r = 0.0
        self.result = "OPEN"


async def run(days: int, refresh: bool, threshold, min_leader_move_atr, lag_ratio_max):
    strat = PSTCryptoRelativeStrength()
    if threshold is not None:
        strat.ENTRY_THRESHOLD = threshold
    if min_leader_move_atr is not None:
        strat.MIN_LEADER_MOVE_ATR = min_leader_move_atr
    if lag_ratio_max is not None:
        strat.LAG_RATIO_MAX = lag_ratio_max

    btc = get_full_mtf_data("BTCUSD", days, refresh)
    eth = get_full_mtf_data("ETHUSD", days, refresh)
    if not btc or not eth:
        print("Faltan datos cacheados de BTCUSD/ETHUSD."); return

    btc_m5, eth_m5 = btc["m5"].reset_index(drop=True), eth["m5"].reset_index(drop=True)
    common_times = btc_m5[["time"]].merge(eth_m5[["time"]], on="time", how="inner")["time"]
    print(f"Barras M5 comunes BTCUSD/ETHUSD: {len(common_times)} (BTC {len(btc_m5)}, ETH {len(eth_m5)})")

    spread_btc = float(btc.get("spread_dist") or 0.0)
    spread_eth = float(eth.get("spread_dist") or 0.0)
    half_spread = {"BTCUSD": spread_btc / 2.0, "ETHUSD": spread_eth / 2.0}

    btc_times = btc_m5["time"].values
    eth_times = eth_m5["time"].values

    trades = []
    open_trade = None
    warmup = WINDOW + 5
    n = len(common_times)

    for i in range(warmup, n):
        t = common_times.iloc[i]
        btc_end = int((btc_times <= t.to_datetime64()).sum())
        eth_end = int((eth_times <= t.to_datetime64()).sum())
        if btc_end < WINDOW or eth_end < WINDOW:
            continue
        btc_slice = btc_m5.iloc[max(0, btc_end - WINDOW):btc_end]
        eth_slice = eth_m5.iloc[max(0, eth_end - WINDOW):eth_end]

        # --- gestionar posición abierta ---
        if open_trade is not None:
            bar = btc_slice.iloc[-1] if open_trade.symbol == "BTCUSD" else eth_slice.iloc[-1]
            h, l = float(bar["high"]), float(bar["low"])
            d = open_trade.direction
            hit = None
            if d == 1:
                if l <= open_trade.sl_price: hit = ("SL", open_trade.sl_price)
                elif h >= open_trade.tp_price: hit = ("TP", open_trade.tp_price)
            else:
                if h >= open_trade.sl_price: hit = ("SL", open_trade.sl_price)
                elif l <= open_trade.tp_price: hit = ("TP", open_trade.tp_price)
            if hit:
                reason, price = hit
                hs = open_trade.half_spread
                eff = price - d * hs  # cruza el spread de salida
                sl_dist = abs(open_trade.entry_price - open_trade.sl_price)
                r = (d * (eff - open_trade.entry_price)) / sl_dist
                comm = commission_r("CRYPTO", open_trade.entry_price, sl_dist, None)
                open_trade.pnl_r = r - comm
                open_trade.exit_time, open_trade.exit_price, open_trade.exit_reason = t, price, reason
                open_trade.result = "WIN" if open_trade.pnl_r > 0 else "LOSS"
                trades.append(open_trade)
                open_trade = None
            continue

        # --- evaluar entrada: probar BTC-lidera-ETH y ETH-lidera-BTC ---
        for follower_sym, follower_slice, leader_sym, leader_slice in (
            ("ETHUSD", eth_slice, "BTCUSD", btc_slice),
            ("BTCUSD", btc_slice, "ETHUSD", eth_slice),
        ):
            sig = await strat.evaluate_pair(follower_sym, {"m5": follower_slice}, leader_sym, {"m5": leader_slice},
                                             spread_dist=half_spread[follower_sym] * 2.0)
            if sig.get("entry", 0) != 0:
                direction = sig["entry"]
                fill = float(follower_slice.iloc[-1]["close"])
                hs = half_spread[follower_sym]
                fill_adj = fill + direction * hs  # medio spread en la entrada
                meta = sig.get("metadata", {})
                atr_f = float(sig.get("atr", 0.0)) or (fill * 0.01)
                sl = meta.get("target_price_sl", 0) or (fill_adj - direction * atr_f * 2.0)
                tp = sig.get("tp_price", 0) or (fill_adj + direction * atr_f * 3.0)
                if abs(fill_adj - sl) > 0:
                    open_trade = Trade(follower_sym, direction, t, fill_adj, sl, tp, hs)
                break  # solo 1 señal por ciclo

    closed = [tr for tr in trades if tr.result in ("WIN", "LOSS")]
    if not closed:
        print("Sin trades cerrados — la estrategia no generó señales ejecutables en esta ventana."); return

    import numpy as np
    pnls = [tr.pnl_r for tr in closed]
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p < 0]
    wr = 100 * len(wins) / len(closed)
    pf = (sum(wins) / abs(sum(losses))) if losses else float("inf")
    exp = float(np.mean(pnls))
    std = np.std(pnls, ddof=1) if len(pnls) > 1 else 0.0
    sharpe = (exp / std * np.sqrt(252)) if std > 0 else 0.0

    print(f"\n{'='*74}\n  PST-CryptoRelativeStrength | {days}d | thr={strat.ENTRY_THRESHOLD} "
          f"min_leader_atr={strat.MIN_LEADER_MOVE_ATR} lag_max={strat.LAG_RATIO_MAX}\n{'='*74}")
    print(f"  trades: {len(closed)} | WR {wr:.1f}% | PF {pf:.2f} | expectancy {exp:+.3f}R | "
          f"sharpe {sharpe:.2f} | total {sum(pnls):+.2f}R")
    by_follower = {}
    for tr in closed:
        by_follower.setdefault(tr.symbol, []).append(tr.pnl_r)
    for sym, pl in by_follower.items():
        w = sum(1 for p in pl if p > 0)
        print(f"    como seguidor {sym}: {len(pl)} trades | WR {100*w/len(pl):.1f}% | exp {np.mean(pl):+.3f}R")
    print(f"\n  ejemplos (hasta 6):")
    for tr in closed[:6]:
        print(f"    {tr.entry_time} {('BUY' if tr.direction==1 else 'SELL')} {tr.symbol} entry={tr.entry_price:.2f} "
              f"-> {tr.exit_reason} {tr.pnl_r:+.2f}R")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--threshold", type=int, default=None)
    ap.add_argument("--min-leader-atr", type=float, default=None, dest="min_leader_atr")
    ap.add_argument("--lag-max", type=float, default=None, dest="lag_max")
    args = ap.parse_args()
    asyncio.run(run(args.days, args.refresh, args.threshold, args.min_leader_atr, args.lag_max))


if __name__ == "__main__":
    main()
