#!/usr/bin/env python3
"""
Auditor de CALIBRACIÓN para PST-PrecisionScalping (juez con visión de futuro)
────────────────────────────────────────────────────────────────────────────
Objetivo: responder si el bot es DEMASIADO EXIGENTE o DEMASIADO LAXO usando
hindsight real, no la propia opinión de la estrategia.

Cómo lo hace (diferencia clave con la versión anterior):
  1. Reproduce la estrategia barra a barra sobre TODOS los cruces frescos EMA9/21
     (el gate obligatorio), tanto los que superan el umbral (ACEPTADAS) como los
     que se quedan cerca por debajo (RECHAZADAS / near-miss).
  2. Para CADA candidata simula la operación hacia delante con el motor FIEL
     (FaithfulScalpingEngine: mismo SL/TP estructural, BE, parcial a 1R, salida
     VWAP+momentum y timeout que en real) → etiqueta WIN/LOSS y pnl_r.
  3. Cruza cada score con su resultado real → matriz de confusión:

        ┌───────────────┬──────────────┬───────────────┐
        │               │ hindsight WIN│ hindsight LOSS│
        ├───────────────┼──────────────┼───────────────┤
        │ estrategia SÍ │   acierto    │ DEMASIADO LAXO│
        │ estrategia NO │ DEMASIADO EX.│    acierto    │
        └───────────────┴──────────────┴───────────────┘

  4. Barrido de umbral: expectancy/win-rate/total-R por banda de score → sugiere
     dónde está el punto óptimo del umbral de entrada.
  5. (Opcional) Cruza con la tabla `trades` para medir COBERTURA DE EJECUCIÓN
     (de las señales aceptadas, ¿cuántas se operaron de verdad?). El desfase de
     reloj MT5↔BBDD se AUTODETECTA, no se hardcodea.

Correcciones frente al script previo:
  · Sin lookahead de resample: usa M5 REAL de MT5 (misma caché que Tools/pst_bt_lab.py),
    no M5 reconstruido desde M1.
  · Sin offset de reloj fijo (±3h): se autodetecta contra los trades reales.
  · Sin doble umbral redundante: la verdad de "aceptada" es el gate real de la
    estrategia (metadata.threshold_used).
  · Dedup por cooldown para no contar el mismo setup N veces.

Requisitos:  pip install pandas pandas_ta MetaTrader5
Uso:
    python audit_precisionscalping.py --symbols EURUSD,BTCUSD --days 5
    python audit_precisionscalping.py --days 3 --near-miss 20 --no-db
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import pickle
import sqlite3
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import pandas_ta as ta

# La consola de Windows suele usar cp1252, que no soporta las flechas/emojis del
# reporte; forzamos UTF-8 para que el script funcione sin PYTHONIOENCODING externo.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from PST_Core.strategies.pst_precision_scalping import PSTPrecisionScalping  # noqa: E402
from PST_Core.backtesting.faithful_engine import FaithfulScalpingEngine  # noqa: E402

try:
    from PST_Core.backtesting.faithful_engine import TP_ATR_BY_CLASS
except Exception:
    TP_ATR_BY_CLASS = {"CRYPTO": 4.0, "INDEX": 5.5, "METAL": 3.5, "FOREX": 6.0}
try:
    from PST_Core.config import SCALPER_PARTIAL_BE_COMMISSION_PADDING_PTS as BE_PTS
except Exception:
    BE_PTS = 2.0

STRATEGY_NAME = "PST-PrecisionScalping"
# Compartimos la MISMA caché de barras que el backtest fiel → mismas barras exactas.
CACHE_DIR = REPO_ROOT / "Tools" / ".bt_cache"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "PST_Core" / "data"


# ───────────────────────────────────────────── datos (caché compartida con pst_bt_lab)
def get_data(symbol: str, days: int, refresh: bool = False) -> Optional[dict]:
    """M1 + M5 REAL de MT5, cacheado igual que Tools/pst_bt_lab.py (barras idénticas)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{symbol}_{days}d.pkl"
    if path.exists() and not refresh:
        with path.open("rb") as f:
            return pickle.load(f)

    try:
        import MetaTrader5 as mt5
    except Exception:
        raise RuntimeError("MetaTrader5 no está instalado. Ejecuta en el mismo entorno que el bot.")
    if not mt5.initialize():
        print(f"    ⚠️  MT5 init falló para {symbol}: {mt5.last_error()}")
        return None

    info = mt5.symbol_info(symbol)
    point = info.point if info else None
    spread_dist = (info.spread * info.point) if info else 0.0
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days)
    out: dict[str, Any] = {"point": point, "spread_dist": spread_dist}
    for name, const in {"m1": mt5.TIMEFRAME_M1, "m5": mt5.TIMEFRAME_M5}.items():
        rates = mt5.copy_rates_range(symbol, const, start_dt, end_dt)
        if rates is None or len(rates) == 0:
            print(f"    {symbol} {name}: sin datos")
            return None
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")  # naive, hora de servidor MT5
        out[name] = df
        print(f"    {symbol} {name:>3}: {len(df):>6} barras")
    with path.open("wb") as f:
        pickle.dump(out, f)
    return out


# ───────────────────────────────────────────── BBDD (símbolos, perfiles, trades)
def resolve_db_path(cli: Optional[str]) -> Optional[str]:
    for cand in [cli,
                 r"C:\Users\crist\Desktop\BOLSA\PailsSentinelTrade\PST_Core\data\pst_trading.db",
                 str(REPO_ROOT / "PST_Core" / "data" / "pst_trading.db")]:
        if cand and Path(cand).exists():
            return str(cand)
    try:
        from PST_Core.config import DB_PATH
        if DB_PATH and Path(DB_PATH).exists():
            return str(DB_PATH)
    except Exception:
        pass
    return None


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_active_symbols(db_path: str) -> list[str]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT symbol FROM symbols_config WHERE is_active = 1 ORDER BY symbol"
        ).fetchall()
    return [r["symbol"] for r in rows]


def get_symbol_profile(db_path: str, symbol: str) -> dict[str, Any]:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM symbol_strategies WHERE symbol = ? AND strategy_name = ? LIMIT 1",
            (symbol, STRATEGY_NAME),
        ).fetchone()
    prof = dict(row) if row else {}
    for k in ("symbol", "strategy_name", "is_active", "id"):
        prof.pop(k, None)
    return prof


def get_trades(db_path: str, symbol: str) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT ticket, symbol, type, price_in, time_in FROM trades "
            "WHERE symbol = ? AND strategy_name = ? ORDER BY time_in",
            (symbol, STRATEGY_NAME),
        ).fetchall()
    out = []
    for r in rows:
        try:
            t = pd.to_datetime(r["time_in"])
        except Exception:
            continue
        out.append({"ticket": r["ticket"], "type": (r["type"] or "").upper(),
                    "price_in": r["price_in"], "time": t})
    return out


# ───────────────────────────────────────────── helpers de señal
def _fresh_cross(m1_slice: pd.DataFrame) -> int:
    """+1/-1 si la última vela M1 tiene un cruce FRESCO EMA9/21; 0 si no. Espeja el gate."""
    close = m1_slice["close"]
    ema9 = ta.ema(close, length=9)
    ema21 = ta.ema(close, length=21)
    if ema9 is None or ema21 is None or len(ema9.dropna()) < 2 or len(ema21.dropna()) < 2:
        return 0
    e9, e9p = float(ema9.iloc[-1]), float(ema9.iloc[-2])
    e21, e21p = float(ema21.iloc[-1]), float(ema21.iloc[-2])
    if e9p <= e21p and e9 > e21:
        return 1
    if e9p >= e21p and e9 < e21:
        return -1
    return 0


def _structural_sltp(m1_slice, price, atr, direction, vwap_upper1, vwap_lower1):
    """
    Réplica del bloque Donchian SL/TP de la estrategia (líneas 459-487) para poder
    simular las near-miss con la MISMA estructura que tendrían si se aceptaran.
    Devuelve (target_price_sl or 0.0, tp_price).
    """
    low, high = m1_slice["low"], m1_slice["high"]
    don_low = float(low.rolling(10).min().iloc[-1])
    don_high = float(high.rolling(10).max().iloc[-1])
    don_low20 = float(low.rolling(20).min().iloc[-1])
    don_high20 = float(high.rolling(20).max().iloc[-1])
    buf = atr * 0.20
    target_sl = 0.0
    if direction == 1:
        cand_sl = don_low - buf
        if atr * 0.8 <= (price - cand_sl) <= atr * 3.0:
            target_sl = cand_sl
        tp_target = max(vwap_upper1, don_high20)
        min_tp = price + max(atr * 1.0, (price - (target_sl or cand_sl)) * 1.2)
        tp = max(tp_target, min_tp)
    else:
        cand_sl = don_high + buf
        if atr * 0.8 <= (cand_sl - price) <= atr * 3.0:
            target_sl = cand_sl
        tp_target = min(vwap_lower1, don_low20)
        min_tp = price - max(atr * 1.0, ((target_sl or cand_sl) - price) * 1.2)
        tp = min(tp_target, min_tp)
    return target_sl, tp


# ───────────────────────────────────────────── registro de candidatas
@dataclass
class Candidate:
    symbol: str
    time: str
    direction: str        # BUY/SELL
    score: int
    threshold: int
    accepted: bool        # score >= threshold (gate real)
    pnl_r: float          # resultado hindsight (motor fiel)
    result: str           # WIN/LOSS
    exit_reason: str
    executed: Optional[bool] = None   # ¿hay trade real en la BBDD? (None si no se cruzó)
    ticket: Optional[int] = None


# ───────────────────────────────────────────── escaneo + simulación hacia delante
async def scan_symbol(symbol, data, profile, eng, near_miss_band, cooldown_min):
    """
    Recorre las barras M1; en cada CRUCE FRESCO evalúa la estrategia y simula la
    operación hacia delante con el motor fiel. Devuelve la lista de Candidate.
    """
    strat = PSTPrecisionScalping()
    df_m1, df_m5 = data["m1"], data["m5"]
    t_m5 = df_m5["time"].values
    a_class = eng._asset_class(symbol)
    tp_mult = TP_ATR_BY_CLASS.get(a_class, 4.0)
    spread = data.get("spread_dist", 0.0) or 0.0
    point = data.get("point") or 10 ** -(eng._infer_digits(df_m1))
    be_pad = BE_PTS * point

    n = len(df_m1)
    out: list[Candidate] = []
    last_cross_time = {1: None, -1: None}
    cooldown = timedelta(minutes=cooldown_min)

    i = eng.warmup
    while i < n:
        bar = df_m1.iloc[i]
        cur_time = bar["time"]
        m1_slice = df_m1.iloc[max(0, i + 1 - eng.lookback_m1): i + 1]

        # Gate obligatorio: solo evaluamos cruces frescos EMA9/21.
        cx = _fresh_cross(m1_slice)
        if cx == 0:
            i += 1
            continue
        # dedup: colapsa cruces del mismo lado dentro del cooldown (mismo setup)
        prev = last_cross_time[cx]
        if prev is not None and (cur_time - prev) < cooldown:
            i += 1
            continue

        end5 = int((t_m5 <= cur_time.to_datetime64()).sum())
        if end5 < 30:
            i += 1
            continue
        m5_slice = df_m5.iloc[max(0, end5 - eng.lookback_m5): end5]

        sig = await strat.calculate_signal({"m1": m1_slice, "m5": m5_slice}, symbol=symbol, **profile)
        score = int(sig.get("score", 0) or 0)
        direction = int(sig.get("direction", 0) or 0)
        atr = float(sig.get("atr", 0.0) or 0.0)
        meta = sig.get("metadata", {}) or {}
        thr = int(meta.get("threshold_used", eng.threshold) or eng.threshold)
        if direction == 0 or atr <= 0:
            i += 1
            continue

        accepted = score >= thr
        # Solo nos interesan aceptadas + rechazadas dentro de la banda near-miss.
        if not accepted and score < (thr - near_miss_band):
            i += 1
            continue

        last_cross_time[cx] = cur_time

        # Para near-miss, la estrategia no calcula SL/TP estructural (solo si entry!=0);
        # lo reconstruimos igual que lo haría si se aceptara, para una simulación fiel.
        sig_sim = dict(sig)
        if not accepted:
            vu1 = float(meta.get("vwap_upper1", bar["close"]))
            vl1 = float(meta.get("vwap_lower1", bar["close"]))
            tsl, tp = _structural_sltp(m1_slice, float(bar["close"]), atr, direction, vu1, vl1)
            sig_sim["tp_price"] = tp
            m2 = dict(meta)
            if tsl > 0:
                m2["target_price_sl"] = tsl
            sig_sim["metadata"] = m2

        trade = _simulate_forward(eng, sig_sim, df_m1, df_m5, t_m5, i, strat,
                                  symbol, atr, direction, tp_mult, spread, be_pad, profile)

        out.append(Candidate(
            symbol=symbol,
            time=str(cur_time),
            direction="BUY" if direction == 1 else "SELL",
            score=score,
            threshold=thr,
            accepted=accepted,
            pnl_r=round(float(trade.pnl_r), 3),
            result=trade.result or ("WIN" if trade.pnl_r > 0 else "LOSS"),
            exit_reason=trade.exit_reason or "?",
        ))
        i += 1

    return out


def _simulate_forward(eng, sig, df_m1, df_m5, t_m5, i, strat,
                      symbol, atr, direction, tp_mult, spread, be_pad, profile):
    """Abre una operación forzada en la barra i y la gestiona hasta el cierre (motor fiel)."""
    bar = df_m1.iloc[i]
    t = eng._open(sig, bar, symbol, atr, direction, tp_mult, spread, be_pad, profile)
    n = len(df_m1)
    j = i + 1
    while j < n:
        if eng._manage(t, df_m1.iloc[j], df_m1, df_m5, t_m5, j, strat, symbol, profile):
            return t
        j += 1
    eng._close_remaining(t, float(df_m1.iloc[-1]["close"]), df_m1.iloc[-1]["time"], "EOD")
    if t.result is None:
        t.result = "WIN" if t.pnl_r > 0 else "LOSS"
    return t


# ───────────────────────────────────────────── cobertura de ejecución (offset autodetect)
def autodetect_offset_hours(accepted: list[Candidate], trades: list[dict]) -> Optional[int]:
    """
    Busca el desfase (horas enteras) MT5→BBDD que maximiza coincidencias de trades
    con señales aceptadas (mismo lado, ±3 min). Evita el hardcode frágil de ±3h.
    """
    if not accepted or not trades:
        return None
    acc = [(pd.to_datetime(c.time), c.direction) for c in accepted]
    best_off, best_hits = None, -1
    for off in range(-8, 9):
        hits = 0
        for tr in trades:
            shifted = tr["time"] - pd.Timedelta(hours=off)
            for at, ad in acc:
                if ad == tr["type"] and abs((shifted - at).total_seconds()) <= 180:
                    hits += 1
                    break
        if hits > best_hits:
            best_hits, best_off = hits, off
    return best_off if best_hits > 0 else None


def mark_execution(accepted: list[Candidate], trades: list[dict], offset_h: int):
    for c in accepted:
        ct = pd.to_datetime(c.time)
        c.executed = False
        for tr in trades:
            shifted = tr["time"] - pd.Timedelta(hours=offset_h)
            if tr["type"] == c.direction and abs((shifted - ct).total_seconds()) <= 300:
                c.executed = True
                c.ticket = tr["ticket"]
                break


# ───────────────────────────────────────────── agregación / reporte
def _stats(cands: list[Candidate]) -> dict:
    if not cands:
        return {"n": 0, "win_rate": 0.0, "expectancy": 0.0, "total_r": 0.0}
    pnls = [c.pnl_r for c in cands]
    wins = [p for p in pnls if p > 0]
    return {"n": len(cands), "win_rate": 100 * len(wins) / len(cands),
            "expectancy": sum(pnls) / len(cands), "total_r": sum(pnls)}


def threshold_sweep(cands: list[Candidate], thresholds: list[int]) -> list[tuple]:
    """Para cada umbral candidato: qué pasa si tomas todas las señales con score >= T."""
    rows = []
    for T in thresholds:
        taken = [c for c in cands if c.score >= T]
        s = _stats(taken)
        rows.append((T, s["n"], s["win_rate"], s["expectancy"], s["total_r"]))
    return rows


def confusion(cands: list[Candidate]) -> dict:
    acc = [c for c in cands if c.accepted]
    rej = [c for c in cands if not c.accepted]
    return {
        "acc_win": sum(1 for c in acc if c.pnl_r > 0),
        "acc_loss": sum(1 for c in acc if c.pnl_r <= 0),
        "rej_win": sum(1 for c in rej if c.pnl_r > 0),
        "rej_loss": sum(1 for c in rej if c.pnl_r <= 0),
        "acc_stats": _stats(acc),
        "rej_stats": _stats(rej),
    }


def write_reports(all_cands: list[Candidate], sweep_thresholds, out_dir: Path,
                  offset_info: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"precision_scalping_calibration_{ts}.csv"
    md_path = out_dir / f"precision_scalping_calibration_{ts}.md"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(all_cands[0]).keys()) if all_cands else
                           list(Candidate.__annotations__.keys()))
        w.writeheader()
        for c in all_cands:
            w.writerow(asdict(c))

    cm = confusion(all_cands)
    sweep = threshold_sweep(all_cands, sweep_thresholds)
    best = max(sweep, key=lambda r: r[4]) if sweep else None

    with md_path.open("w", encoding="utf-8") as f:
        f.write("# Calibración PrecisionScalping (juez con hindsight fiel)\n\n")
        f.write(f"- Generado: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        f.write(f"- Candidatas (cruces frescos evaluados): {len(all_cands)}\n")
        f.write(f"- {offset_info}\n\n")

        f.write("## Matriz de confusión (aceptadas vs rechazadas × resultado real)\n\n")
        f.write("| | hindsight WIN | hindsight LOSS |\n|---|---|---|\n")
        f.write(f"| **Estrategia SÍ (aceptada)** | {cm['acc_win']} acierto | {cm['acc_loss']} → *laxo* |\n")
        f.write(f"| **Estrategia NO (near-miss)** | {cm['rej_win']} → *exigente* | {cm['rej_loss']} acierto |\n\n")
        a, r = cm["acc_stats"], cm["rej_stats"]
        f.write(f"- Aceptadas: n={a['n']} · win={a['win_rate']:.0f}% · expectancy={a['expectancy']:+.3f}R · total={a['total_r']:+.1f}R\n")
        f.write(f"- Rechazadas (near-miss): n={r['n']} · win={r['win_rate']:.0f}% · expectancy={r['expectancy']:+.3f}R · total={r['total_r']:+.1f}R\n\n")
        f.write("**Lectura:** si las *rechazadas* tienen expectancy claramente positiva → el bot es "
                "**demasiado exigente** (bajar umbral). Si las *aceptadas* tienen expectancy negativa "
                "o las marginales pierden → **demasiado laxo** (subir umbral / endurecer filtros).\n\n")

        f.write("## Barrido de umbral (tomar todas las señales con score ≥ T)\n\n")
        f.write("| Umbral T | Señales | Win % | Expectancy (R) | Total (R) |\n|---|---|---|---|---|\n")
        for T, n, wr, exp, tot in sweep:
            f.write(f"| {T} | {n} | {wr:.0f}% | {exp:+.3f} | {tot:+.1f} |\n")
        if best:
            f.write(f"\n**Umbral con mayor R total en la muestra:** {best[0]} "
                    f"({best[1]} señales, expectancy {best[3]:+.3f}R).\n\n")

        exe = [c for c in all_cands if c.accepted and c.executed is not None]
        if exe:
            covered = sum(1 for c in exe if c.executed)
            f.write("## Cobertura de ejecución (aceptadas que el bot operó de verdad)\n\n")
            f.write(f"- {covered}/{len(exe)} señales aceptadas tienen trade real en la BBDD "
                    f"({100*covered/len(exe):.0f}%). El resto son huecos de ejecución "
                    "(cooldown, margen, riesgo, spread, conexión…).\n\n")

    return csv_path, md_path


# ───────────────────────────────────────────── main
async def run(args):
    db_path = resolve_db_path(args.db_path)
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    elif db_path:
        symbols = get_active_symbols(db_path)
    else:
        raise RuntimeError("Sin --symbols y sin BBDD para autodetectar símbolos activos.")
    if not symbols:
        raise RuntimeError("No hay símbolos que analizar.")

    print(f"DB: {db_path or '(no encontrada — se omite cobertura de ejecución)'}")
    print(f"Símbolos: {', '.join(symbols)} | días {args.days} | near-miss band {args.near_miss}")

    eng = FaithfulScalpingEngine(threshold=args.threshold,
                                 lookback_m1=args.lookback_m1, lookback_m5=args.lookback_m5)
    all_cands: list[Candidate] = []
    offset_used: Optional[int] = None

    for sym in symbols:
        print(f"\n▶ {sym}")
        data = get_data(sym, args.days, args.refresh)
        if not data:
            print("  sin datos, se omite.")
            continue
        profile = get_symbol_profile(db_path, sym) if db_path else {}
        cands = await scan_symbol(sym, data, profile, eng, args.near_miss, args.cooldown_min)
        print(f"  candidatas: {len(cands)} "
              f"(aceptadas {sum(c.accepted for c in cands)}, "
              f"near-miss {sum(not c.accepted for c in cands)})")

        if db_path and not args.no_db:
            trades = get_trades(db_path, sym)
            accepted = [c for c in cands if c.accepted]
            off = autodetect_offset_hours(accepted, trades)
            if off is not None:
                offset_used = off
                mark_execution(accepted, trades, off)
                print(f"  offset MT5→BBDD autodetectado: {off:+d}h | trades reales: {len(trades)}")
            elif trades:
                print(f"  ⚠️ no pude alinear {len(trades)} trades con las señales (offset desconocido)")

        all_cands.extend(cands)

    if not all_cands:
        print("\nNo se detectaron cruces frescos evaluables en la ventana.")
        return

    offset_info = (f"Offset MT5→BBDD autodetectado: {offset_used:+d}h" if offset_used is not None
                   else "Cobertura de ejecución no disponible (sin BBDD o sin alineación de trades)")
    csv_path, md_path = write_reports(
        all_cands, args.sweep, Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR, offset_info)

    cm = confusion(all_cands)
    print("\n" + "=" * 60)
    print("RESUMEN GLOBAL")
    print(f"  Aceptadas → WIN {cm['acc_win']} / LOSS {cm['acc_loss']}  "
          f"(expectancy {cm['acc_stats']['expectancy']:+.3f}R)")
    print(f"  Near-miss → WIN {cm['rej_win']} / LOSS {cm['rej_loss']}  "
          f"(expectancy {cm['rej_stats']['expectancy']:+.3f}R)")
    if cm['rej_stats']['expectancy'] > 0.05 and cm['rej_stats']['n'] >= 5:
        print("  ⇒ Señal de que el bot puede ser DEMASIADO EXIGENTE (near-miss rentables).")
    if cm['acc_stats']['expectancy'] < 0 and cm['acc_stats']['n'] >= 5:
        print("  ⇒ Señal de que el bot puede ser DEMASIADO LAXO (aceptadas en pérdida).")
    print(f"\n  CSV: {csv_path}\n  MD : {md_path}")

    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Auditor de calibración PrecisionScalping (hindsight fiel).")
    ap.add_argument("--symbols", default=None, help="Coma-separados. Si se omite, usa los activos de la BBDD.")
    ap.add_argument("--days", type=int, default=5, help="Ventana de histórico (días).")
    ap.add_argument("--threshold", type=int, default=70, help="Umbral por defecto si el perfil no trae threshold_used.")
    ap.add_argument("--near-miss", type=int, default=20, help="Ancho de banda por debajo del umbral a auditar como rechazadas.")
    ap.add_argument("--cooldown-min", type=int, default=5, help="Minutos para colapsar cruces duplicados del mismo lado.")
    ap.add_argument("--lookback-m1", type=int, default=600)
    ap.add_argument("--lookback-m5", type=int, default=240)
    ap.add_argument("--sweep", type=lambda s: [int(x) for x in s.split(",")],
                    default=[55, 60, 65, 70, 72, 75, 80], help="Umbrales a barrer en el reporte.")
    ap.add_argument("--refresh", action="store_true", help="Fuerza re-descarga de datos (ignora caché).")
    ap.add_argument("--no-db", action="store_true", help="No cruzar con la tabla trades (solo calibración).")
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--output-dir", default=None)
    args = ap.parse_args()

    try:
        asyncio.run(run(args))
    except Exception as exc:
        print(f"Error durante la auditoría: {exc}")
        import traceback
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
