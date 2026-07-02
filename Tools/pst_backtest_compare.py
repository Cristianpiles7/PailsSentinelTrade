#!/usr/bin/env python3
"""
PST · Backtest comparativo PrecisionScalping (versión ANTIGUA vs NUEVA)
──────────────────────────────────────────────────────────────────────
Mide el impacto de la optimización de la estrategia comparando, sobre los
MISMOS datos históricos y con idéntica configuración de SL/TP del engine,
la versión actual del working tree contra una referencia de git.

Por rendimiento usa una VENTANA ACOTADA de lookback (simulación lineal en
lugar del O(N²) del engine con ventana creciente). La estrategia solo mira
velas recientes (EMAs, ATR, RSI, ADX/Chop, Donchian y el VWAP de sesión del
día en curso), así que acotar la ventana no altera la señal — y como se aplica
IGUAL a ambas versiones, la comparación es apples-to-apples.

Uso:
    python Tools/pst_backtest_compare.py
    python Tools/pst_backtest_compare.py --symbols EURUSD,XAUUSD,BTCUSD --days 5
    python Tools/pst_backtest_compare.py --old-ref v2.3.1 --threshold 70
"""
import sys
import os
import asyncio
import argparse
import logging
import importlib.util
import subprocess
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.WARNING, format="[%(asctime)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("PST-BTCompare")

from PST_Core.backtesting.engine import BacktestTrade, BacktestResult, PSTBacktestEngine  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRAT_REL = "PST_Core/strategies/pst_precision_scalping.py"


# ---------------------------------------------------------------- carga de estrategias
def _load_class_from_source(source: str, tag: str):
    """Carga PSTPrecisionScalping desde un string de código fuente, aislado."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=f"_{tag}.py", delete=False, encoding="utf-8")
    tmp.write(source)
    tmp.close()
    spec = importlib.util.spec_from_file_location(f"pst_ps_{tag}", tmp.name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PSTPrecisionScalping


def load_new_strategy():
    with open(os.path.join(REPO_ROOT, STRAT_REL), encoding="utf-8") as f:
        return _load_class_from_source(f.read(), "NEW")


def load_old_strategy(ref: str):
    src = subprocess.check_output(["git", "show", f"{ref}:{STRAT_REL}"], cwd=REPO_ROOT, text=True, encoding="utf-8")
    return _load_class_from_source(src, "OLD")


# ---------------------------------------------------------------- datos MT5
def fetch_mt5(symbol: str, days: int) -> dict:
    import MetaTrader5 as mt5
    import pandas as pd

    if not mt5.initialize():
        logger.error(f"No se pudo inicializar MT5: {mt5.last_error()}")
        sys.exit(1)

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days)
    out = {}
    for name, const in {"m1": mt5.TIMEFRAME_M1, "m5": mt5.TIMEFRAME_M5}.items():
        rates = mt5.copy_rates_range(symbol, const, start_dt, end_dt)
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            out[name] = df
            print(f"    {symbol} {name:>3}: {len(df):>6} barras")
        else:
            print(f"    {symbol} {name:>3}: sin datos")
    return out


# ---------------------------------------------------------------- simulación windowed
async def simulate(strategy, symbol: str, data: dict, threshold: int,
                   lookback_m1: int = 720, lookback_m5: int = 240,
                   warmup: int = 240) -> BacktestResult:
    """
    Réplica fiel del bucle de PSTBacktestEngine.run pero con ventana acotada:
      · 1 trade abierto a la vez; mientras hay trade abierto no se evalúan señales.
      · Gate de entrada idéntico: score>=threshold, entry!=0, atr>0, R:R>=1.2.
      · SL/TP por múltiplos ATR del engine (scalping 1.25x / TP por clase de activo).
      · Resolución intrabar conservadora: se comprueba SL antes que TP.
    """
    import pandas as pd  # noqa: F401

    eng = PSTBacktestEngine(score_threshold=threshold)
    a_class = eng._get_asset_class(symbol)
    sl_mult = PSTBacktestEngine.DEFAULT_SL_MULT["PST-PrecisionScalping"]
    tp_mult = PSTBacktestEngine.DEFAULT_TP_MULT.get(a_class, 4.0)

    df_m1 = data["m1"]
    df_m5 = data["m5"]
    t_m5 = df_m5["time"].values

    period_start = df_m1.iloc[warmup]["time"]
    period_end = df_m1.iloc[-1]["time"]
    res = BacktestResult("PST-PrecisionScalping", symbol, period_start, period_end)

    open_trade = None
    n = len(df_m1)
    for i in range(warmup, n):
        bar = df_m1.iloc[i]

        # --- resolver trade abierto en esta barra (SL antes que TP) ---
        if open_trade is not None:
            h, l = float(bar["high"]), float(bar["low"])
            if open_trade.direction == 1:
                if l <= open_trade.sl_price:
                    open_trade.exit_price, open_trade.result, open_trade.pnl_r = open_trade.sl_price, "LOSS", -1.0
                    open_trade.exit_time = bar["time"]; open_trade = None
                elif h >= open_trade.tp_price:
                    open_trade.exit_price, open_trade.result = open_trade.tp_price, "WIN"
                    open_trade.pnl_r = (open_trade.tp_price - open_trade.entry_price) / max(open_trade.entry_price - open_trade.sl_price, 1e-10)
                    open_trade.exit_time = bar["time"]; open_trade = None
            else:
                if h >= open_trade.sl_price:
                    open_trade.exit_price, open_trade.result, open_trade.pnl_r = open_trade.sl_price, "LOSS", -1.0
                    open_trade.exit_time = bar["time"]; open_trade = None
                elif l <= open_trade.tp_price:
                    open_trade.exit_price, open_trade.result = open_trade.tp_price, "WIN"
                    open_trade.pnl_r = (open_trade.entry_price - open_trade.tp_price) / max(open_trade.sl_price - open_trade.entry_price, 1e-10)
                    open_trade.exit_time = bar["time"]; open_trade = None

        if open_trade is not None:
            continue

        # --- construir ventana MTF acotada hasta la barra i (inclusive) ---
        m1_slice = df_m1.iloc[max(0, i + 1 - lookback_m1): i + 1]
        cur_time = bar["time"]
        end5 = int((t_m5 <= cur_time.to_datetime64()).sum())
        if end5 < 30:
            continue
        m5_slice = df_m5.iloc[max(0, end5 - lookback_m5): end5]

        sig = await strategy.calculate_signal({"m1": m1_slice, "m5": m5_slice}, symbol=symbol)
        score = sig.get("score", 0)
        entry = sig.get("entry", 0)
        atr = sig.get("atr", 0.0)
        if score < threshold or entry == 0 or atr <= 0:
            continue

        direction = int(entry)
        px = float(bar["close"])
        sl_dist, tp_dist = sl_mult * atr, tp_mult * atr
        if tp_dist / sl_dist < 1.2:
            continue

        open_trade = BacktestTrade(
            entry_time=cur_time, exit_time=None, symbol=symbol, strategy="PST-PrecisionScalping",
            direction=direction, entry_price=px,
            sl_price=px - direction * sl_dist, tp_price=px + direction * tp_dist,
            score=abs(score), atr=atr,
        )
        res.trades.append(open_trade)

    # marcar el que quede abierto
    if open_trade is not None:
        last_close = float(df_m1.iloc[-1]["close"])
        open_trade.exit_price, open_trade.exit_time, open_trade.result = last_close, period_end, "OPEN"
        sld = abs(open_trade.entry_price - open_trade.sl_price)
        if sld > 0:
            open_trade.pnl_r = ((last_close - open_trade.entry_price) if direction == 1 else (open_trade.entry_price - last_close)) / sld

    return res


# ---------------------------------------------------------------- reporte
def print_compare(symbol: str, old: BacktestResult, new: BacktestResult):
    def row(label, o, n, better="high"):
        try:
            of, nf = float(str(o).rstrip("%R")), float(str(n).rstrip("%R"))
            d = nf - of
            arrow = "→"
            if abs(d) > 1e-9:
                good = (d > 0) if better == "high" else (d < 0)
                arrow = "▲" if good else "▼"
            delta = f"{d:+.2f}"
        except Exception:
            delta, arrow = "", "→"
        print(f"  {label:<20} {str(o):>12} {str(n):>12}   {arrow} {delta}")

    so, sn = old.summary(), new.summary()
    print(f"\n{'='*62}\n  {symbol}   |   OLD (v2.3.1)  vs  NEW (optimizada)\n{'='*62}")
    print(f"  {'métrica':<20} {'ANTIGUA':>12} {'NUEVA':>12}   Δ")
    print(f"  {'-'*58}")
    row("Trades cerrados", so["total_trades"], sn["total_trades"], "high")
    row("Win Rate", so["win_rate"], sn["win_rate"], "high")
    row("Profit Factor", so["profit_factor"], sn["profit_factor"], "high")
    row("Sharpe", so["sharpe"], sn["sharpe"], "high")
    row("Max Drawdown", so["max_drawdown_r"], sn["max_drawdown_r"], "low")
    row("Total R", so["total_r"], sn["total_r"], "high")
    row("Avg Win", so["avg_win_r"], sn["avg_win_r"], "high")
    row("Avg Loss", so["avg_loss_r"], sn["avg_loss_r"], "high")
    return old, new


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default="EURUSD,XAUUSD,BTCUSD")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--threshold", type=int, default=70, help="Score mínimo de entrada (default 70 = régimen normal)")
    ap.add_argument("--old-ref", default="v2.3.1", help="Ref git de la versión antigua (default v2.3.1)")
    ap.add_argument("--lookback-m1", type=int, default=720)
    args = ap.parse_args()

    print(f"\n{'#'*62}\n#  PST BACKTEST COMPARATIVO — PrecisionScalping\n"
          f"#  OLD ref: {args.old_ref}   |   días: {args.days}   |   threshold: {args.threshold}\n{'#'*62}")

    OldStrat = load_old_strategy(args.old_ref)
    NewStrat = load_new_strategy()

    symbols = [s.strip() for s in args.symbols.split(",")]
    agg = {"old": [], "new": []}
    for sym in symbols:
        print(f"\n  Descargando {sym} ({args.days} días)...")
        data = fetch_mt5(sym, args.days)
        if "m1" not in data or "m5" not in data or len(data["m1"]) < 300:
            print(f"    ⚠️  datos insuficientes para {sym}, saltando.")
            continue
        r_old = await simulate(OldStrat(), sym, data, args.threshold, lookback_m1=args.lookback_m1)
        r_new = await simulate(NewStrat(), sym, data, args.threshold, lookback_m1=args.lookback_m1)
        print_compare(sym, r_old, r_new)
        agg["old"].append(r_old); agg["new"].append(r_new)

    # --- agregado global ---
    def blend(results):
        ct = [t for r in results for t in r.closed_trades]
        if not ct:
            return None
        import numpy as np
        wr = sum(1 for t in ct if t.result == "WIN") / len(ct) * 100
        gp = sum(t.pnl_r for t in ct if t.pnl_r > 0); gl = abs(sum(t.pnl_r for t in ct if t.pnl_r < 0))
        pf = gp / gl if gl > 0 else float("inf")
        pnls = [t.pnl_r for t in ct]; sr = (np.mean(pnls) / np.std(pnls, ddof=1) * np.sqrt(252)) if len(ct) > 1 and np.std(pnls, ddof=1) > 0 else 0.0
        return {"trades": len(ct), "wr": wr, "pf": pf, "sharpe": sr, "total_r": sum(pnls), "exp": np.mean(pnls)}

    bo, bn = blend(agg["old"]), blend(agg["new"])
    if bo and bn:
        print(f"\n{'='*62}\n  GLOBAL (todos los símbolos)\n{'='*62}")
        print(f"  {'métrica':<20} {'ANTIGUA':>12} {'NUEVA':>12}")
        print(f"  {'-'*58}")
        print(f"  {'Trades':<20} {bo['trades']:>12} {bn['trades']:>12}")
        print(f"  {'Win Rate':<20} {bo['wr']:>11.1f}% {bn['wr']:>11.1f}%")
        print(f"  {'Profit Factor':<20} {bo['pf']:>12.2f} {bn['pf']:>12.2f}")
        print(f"  {'Sharpe':<20} {bo['sharpe']:>12.2f} {bn['sharpe']:>12.2f}")
        print(f"  {'Total R':<20} {bo['total_r']:>12.2f} {bn['total_r']:>12.2f}")
        print(f"  {'Expectancy (R/op)':<20} {bo['exp']:>12.3f} {bn['exp']:>12.3f}")

    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:
        pass
    print("\nComparativa finalizada.\n")


if __name__ == "__main__":
    asyncio.run(main())
