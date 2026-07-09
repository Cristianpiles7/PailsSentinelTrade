#!/usr/bin/env python3
"""
PST · Backtest LAB — juez fiel de PrecisionScalping (candidato vs baseline)
──────────────────────────────────────────────────────────────────────────
Mide la estrategia con el motor FIEL (FaithfulScalpingEngine): SL/TP estructural,
salida VWAP+momentum, parciales, BE, timeout y spread real — no el proxy de R:R fijo.

Datos deterministas: se descargan una vez de MT5 y se cachean en Tools/.bt_cache/,
para que baseline y candidato corran sobre BARRAS IDÉNTICAS.

Uso:
    # Comparar working tree (candidato) contra una ref de git (baseline)
    python Tools/pst_bt_lab.py --symbols EURUSD,XAUUSD,BTCUSD,ETHUSD --days 10 --baseline v2.3.1

    # Guardar el estado actual como baseline persistente (guardarraíl)
    python Tools/pst_bt_lab.py --symbols EURUSD,XAUUSD,BTCUSD --days 10 --save-baseline scalping_v1

    # Juzgar el working tree contra un baseline guardado (VERDE/ROJO)
    python Tools/pst_bt_lab.py --symbols EURUSD,XAUUSD,BTCUSD --days 10 --vs-baseline scalping_v1
"""
import sys
import os
import json
import asyncio
import argparse
import pickle
import subprocess
import tempfile
import importlib.util
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PST_Core.backtesting.faithful_engine import FaithfulScalpingEngine  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRAT_REL = "PST_Core/strategies/pst_precision_scalping.py"
CACHE_DIR = os.path.join(REPO_ROOT, "Tools", ".bt_cache")
BASELINE_DIR = os.path.join(REPO_ROOT, "Tools", "bt_baselines")

TOL_EXP = 0.02      # tolerancia de expectancy para el veredicto
TOL_SHARPE = 0.10   # tolerancia de sharpe para el veredicto


# ─────────────────────────────────────────── carga de estrategias
def _load_from_source(src, tag):
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=f"_{tag}.py", delete=False, encoding="utf-8")
    tmp.write(src); tmp.close()
    spec = importlib.util.spec_from_file_location(f"pst_ps_{tag}", tmp.name)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod.PSTPrecisionScalping


def load_working_tree():
    with open(os.path.join(REPO_ROOT, STRAT_REL), encoding="utf-8") as f:
        return _load_from_source(f.read(), "WT")


def load_git_ref(ref):
    src = subprocess.check_output(["git", "show", f"{ref}:{STRAT_REL}"], cwd=REPO_ROOT, text=True, encoding="utf-8")
    return _load_from_source(src, ref.replace(".", "_").replace("/", "_"))


# ─────────────────────────────────────────── datos (cache determinista)
def get_data(symbol, days, refresh=False):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{symbol}_{days}d.pkl")
    if os.path.exists(path) and not refresh:
        with open(path, "rb") as f:
            return pickle.load(f)

    import MetaTrader5 as mt5
    import pandas as pd
    if not mt5.initialize():
        print(f"    ⚠️  MT5 init falló: {mt5.last_error()}"); return None
    # Símbolos fuera del Market Watch (no "visible") necesitan symbol_select antes de poder
    # pedirles histórico — sin esto, copy_rates_range se cuelga/devuelve vacío indefinidamente.
    mt5.symbol_select(symbol, True)

    info = mt5.symbol_info(symbol)
    point = info.point if info else None
    spread_dist = (info.spread * info.point) if info else 0.0
    # tick_value/tick_size: para convertir la comisión round-turn a R en el motor fiel.
    tick_value = getattr(info, "trade_tick_value", None) if info else None
    tick_size = getattr(info, "trade_tick_size", None) if info else None

    end_dt = datetime.now(); start_dt = end_dt - timedelta(days=days)
    out = {"point": point, "spread_dist": spread_dist,
           "tick_value": tick_value, "tick_size": tick_size}
    for name, const in {"m1": mt5.TIMEFRAME_M1, "m5": mt5.TIMEFRAME_M5}.items():
        rates = mt5.copy_rates_range(symbol, const, start_dt, end_dt)
        if rates is None or len(rates) == 0:
            print(f"    {symbol} {name}: sin datos"); return None
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        out[name] = df
        print(f"    {symbol} {name:>3}: {len(df):>6} barras")
    with open(path, "wb") as f:
        pickle.dump(out, f)
    return out


# ─────────────────────────────────────────── métricas
def metrics(res):
    ct = res.closed_trades
    exp = (sum(t.pnl_r for t in ct) / len(ct)) if ct else 0.0
    return {
        "trades": len(ct),
        "win_rate": res.win_rate,
        "profit_factor": res.profit_factor,
        "sharpe": res.sharpe_ratio,
        "sortino": res.sortino_ratio,
        "avg_rr": res.avg_rr_realized,
        "avg_win": res.avg_win_r,
        "avg_loss": res.avg_loss_r,
        "max_dd": res.max_drawdown_r,
        "total_r": res.total_r,
        "expectancy": exp,
    }


async def run_variant(StratCls, symbol, data, threshold, lookback_m1, profile=None):
    eng = FaithfulScalpingEngine(threshold=threshold, lookback_m1=lookback_m1)
    res = await eng.run(StratCls(), symbol, data, profile=(profile or {}),
                        point=data.get("point"), spread_dist=data.get("spread_dist", 0.0))
    return res


# perfiles por grupo de activo (para A/B testing de Fase 2)
_PROFILES = {}


def _load_profiles(path):
    global _PROFILES
    if not path:
        return
    with open(path, encoding="utf-8") as f:
        _PROFILES = json.load(f)  # {"CRYPTO": {...}, "FOREX": {...}, ...}


def _profile_for(symbol):
    """Devuelve el kwarg filter_profile para el grupo del símbolo, o {} si no hay perfil."""
    if not _PROFILES:
        return {}
    try:
        from PST_Core.utils.tech_utils import get_asset_class
        group = get_asset_class(symbol)
    except Exception:
        group = "CRYPTO" if any(k in symbol.upper() for k in ("BTC", "ETH", "SOL")) else "FOREX"
    prof = _PROFILES.get(group)
    return {"filter_profile": prof} if prof else {}


# ─────────────────────────────────────────── C.2: sweep por símbolo
# Espejo de GROUP_DEFAULTS en PST_Core/models/database.py — la fuente de verdad de lo que
# REALMENTE corre en producción hoy. Un baseline construido con los defaults del código
# (sin esto) NO representa lo shippeado (p.ej. entry_threshold real es 72, no 70) y
# contamina cualquier comparación de una sola palanca. Mantener sincronizado a mano con
# GROUP_DEFAULTS si se cambia allí.
SHIPPED_PROFILES = {
    # adx_ok 18->21 en FOREX/COMMODITY/METAL/INDEX: group sweep 2026-07-05, confirmado en
    # 4 grupos (INDEX/FOREX/METAL/pool) — ver GROUP_DEFAULTS en database.py.
    "FOREX":     {"noise_mode": "on",   "entry_threshold": 72, "adx_ok": 21},
    "COMMODITY": {"noise_mode": "on",   "entry_threshold": 72, "adx_ok": 21},
    "METAL":     {"noise_mode": "on",   "entry_threshold": 72, "adx_ok": 21},
    "STOCK":     {"noise_mode": "on",   "entry_threshold": 72},
    "INDEX":     {"noise_mode": "on",   "entry_threshold": 72, "vwap_exit": "off", "adx_ok": 21},
    "CRYPTO":    {"noise_mode": "soft", "entry_threshold": 72, "vwap_exit": "off"},
}
SYMBOL_PROFILE_OVERRIDES = {
    # Sincronizado a mano con SYMBOL_FILTER_OVERRIDES en PST_Core/models/database.py.
    "ETHUSD": {"m1_eff_mode": "on", "entry_threshold": 80},
    "UK100.cash": {"adx_ok": 18, "entry_threshold": 76, "m1_eff_mode": "on"},
    "US30.cash": {"m1_eff_mode": "on", "entry_threshold": 76},
    "US500.cash": {"entry_threshold": 68},
    "GBPUSD": {"entry_threshold": 76},
    "XAUUSD": {"entry_threshold": 76},
    "EU50.cash": {"entry_threshold": 76},
    "US100.cash": {"entry_threshold": 76},
    "GER40.cash": {"entry_threshold": 76, "m1_eff_mode": "on"},
    "BTCUSD": {"entry_threshold": 76, "m1_eff_mode": "on"},
}


# C.4: SL/TP/BE compartidos hoy por TODOS los símbolos (_SCALP_BASE en database.py).
# Nunca tuneados por símbolo — el sweep los trata como palancas "top" (columnas de
# symbol_strategies), separadas del "filter" (JSON de filter_profile).
# tp_mult solo gobierna el TP ATR-based (índices y fallback); en forex/metal/cripto el
# TP técnico (VWAP/Donchian) tiene prioridad y esta palanca apenas mueve nada.
SHIPPED_RISK = {"sl_mult": 1.6, "min_rr": 1.8, "be_mult": 3.5, "tp_mult": 2.5}


def _get_group(symbol):
    try:
        from PST_Core.utils.tech_utils import get_asset_class
        return get_asset_class(symbol)
    except Exception:
        return "CRYPTO" if any(k in symbol.upper() for k in ("BTC", "ETH", "SOL")) else "FOREX"


def _shipped_profile_for(symbol):
    """Perfil de FILTROS realmente shippeado para el símbolo (grupo + override)."""
    group = _get_group(symbol)
    prof = dict(SHIPPED_PROFILES.get(group, {"noise_mode": "on", "entry_threshold": 72}))
    prof.update(SYMBOL_PROFILE_OVERRIDES.get(symbol, {}))
    return prof


def _shipped_full_profile_for(symbol):
    """Perfil COMPLETO shippeado: filtros + SL/R:R/BE (para el sweep C.2+C.4)."""
    return {"filter": _shipped_profile_for(symbol), "top": dict(SHIPPED_RISK)}


# Palancas a barrer, UNA a la vez, sobre el perfil shippeado (nunca combinadas entre sí).
# Cada entrada: (scope, clave, valor alternativo). scope='filter' → vive dentro de
# filter_profile (JSON); scope='top' → columna directa de symbol_strategies (sl_mult/min_rr/be_mult).
def _sweep_candidates(shipped_full: dict):
    f, t = shipped_full["filter"], shipped_full["top"]
    et = int(f.get("entry_threshold", 72))
    noise = f.get("noise_mode", "on")
    vwap = f.get("vwap_exit", "on")
    m1eff = f.get("m1_eff_mode", "off")
    out = []
    for alt in sorted({max(50, et - 4), min(90, et + 4)} - {et}):
        out.append(("filter", "entry_threshold", alt))
    for alt in [v for v in ("on", "soft", "off") if v != noise]:
        out.append(("filter", "noise_mode", alt))
    out.append(("filter", "vwap_exit", "off" if str(vwap).lower() == "on" else "on"))
    out.append(("filter", "m1_eff_mode", "off" if str(m1eff).lower() == "on" else "on"))
    # C.4: SL/R:R/BE — variaciones acotadas alrededor del valor shippeado.
    sl = float(t.get("sl_mult", 1.6))
    for alt in (round(sl - 0.3, 2), round(sl + 0.3, 2)):
        if alt > 0:
            out.append(("top", "sl_mult", alt))
    rr = float(t.get("min_rr", 1.8))
    for alt in (round(rr - 0.3, 2), round(rr + 0.3, 2)):
        if alt >= 1.0:
            out.append(("top", "min_rr", alt))
    be = float(t.get("be_mult", 3.5))
    for alt in (round(be - 1.0, 2), round(be + 1.0, 2)):
        if alt >= 1.5:
            out.append(("top", "be_mult", alt))
    # tp_mult: TP más corto (sube WR, baja avg win) vs más largo (al revés). El juez
    # decide por expectancy+sharpe, no por WR — el chequeo direccional filtra ruido.
    tp = float(t.get("tp_mult", 2.5))
    for alt in (round(tp - 0.5, 2), round(tp + 0.5, 2)):
        if alt >= 1.0:
            out.append(("top", "tp_mult", alt))
    return out


# ─────────────────────────────────────────── reporte
def _fmt(v, pct=False, suf=""):
    if isinstance(v, float) and v == float("inf"):
        return "inf"
    return f"{v:.1f}%" if pct else f"{v:.2f}{suf}"


def print_pair(symbol, base, cand, base_label, cand_label):
    print(f"\n{'='*66}\n  {symbol}   |   {base_label}  vs  {cand_label}\n{'='*66}")
    print(f"  {'métrica':<18} {base_label:>13} {cand_label:>13}   Δ")
    print(f"  {'-'*60}")
    rows = [
        ("Trades", "trades", False, "high"),
        ("Win Rate", "win_rate", True, "high"),
        ("Profit Factor", "profit_factor", False, "high"),
        ("Sharpe", "sharpe", False, "high"),
        ("Sortino", "sortino", False, "high"),
        ("Avg R:R realiz.", "avg_rr", False, "high"),
        ("Avg Win (R)", "avg_win", False, "high"),
        ("Avg Loss (R)", "avg_loss", False, "high"),
        ("Max DD (R)", "max_dd", False, "low"),
        ("Total R", "total_r", False, "high"),
        ("Expectancy (R)", "expectancy", False, "high"),
    ]
    for label, key, pct, better in rows:
        o, n = base[key], cand[key]
        try:
            d = n - o
            arrow = "→"
            if abs(d) > 1e-9:
                good = (d > 0) if better == "high" else (d < 0)
                arrow = "▲" if good else "▼"
            delta = f"{d:+.2f}"
        except Exception:
            arrow, delta = "→", ""
        bs = _fmt(o, pct); ns = _fmt(n, pct)
        print(f"  {label:<18} {bs:>13} {ns:>13}   {arrow} {delta}")


def verdict(base, cand):
    """PASS si el candidato no empeora expectancy ni sharpe más allá de la tolerancia."""
    de = cand["expectancy"] - base["expectancy"]
    ds = cand["sharpe"] - base["sharpe"]
    ok = (de >= -TOL_EXP) and (ds >= -TOL_SHARPE)
    tag = "✅ PASS" if ok else "❌ FAIL"
    return ok, f"{tag}  (Δexpectancy {de:+.3f}, Δsharpe {ds:+.2f})"


def aggregate(results):
    """Métricas agregadas sobre el pool de trades cerrados de todos los símbolos."""
    ct = [t for r in results for t in r.closed_trades]
    if not ct:
        return None
    import numpy as np
    pnls = [t.pnl_r for t in ct]
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p < 0]
    wr = len(wins) / len(ct) * 100
    gp = sum(wins); gl = abs(sum(losses))
    std = np.std(pnls, ddof=1) if len(ct) > 1 else 0.0
    sr = (np.mean(pnls) / std * np.sqrt(252)) if std > 0 else 0.0
    downside = [p for p in pnls if p < 0]
    dstd = np.sqrt(np.mean(np.square(downside))) if downside else 0.0
    sortino = (np.mean(pnls) / dstd * np.sqrt(252)) if dstd > 0 else 0.0
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    avg_rr = (avg_win / abs(avg_loss)) if (wins and losses) else 0.0
    # Max DD sobre la curva de equity encadenada
    eq, peak, mdd = 0.0, 0.0, 0.0
    for p in pnls:
        eq += p; peak = max(peak, eq); mdd = max(mdd, peak - eq)
    return {"trades": len(ct), "win_rate": wr, "profit_factor": (gp / gl if gl > 0 else float("inf")),
            "sharpe": sr, "sortino": sortino, "avg_rr": avg_rr, "avg_win": avg_win, "avg_loss": avg_loss,
            "max_dd": mdd, "total_r": sum(pnls), "expectancy": float(np.mean(pnls))}


# ─────────────────────────────────────────── main
async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default="EURUSD,XAUUSD,BTCUSD,ETHUSD")
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--threshold", type=int, default=70)
    ap.add_argument("--lookback-m1", type=int, default=200,
                    help="Barras M1 por señal. Default 200 = fiel a producción (el bot descarga 200). "
                         "OJO: los baselines guardados a 600 quedan obsoletos; regenerar con --save-baseline.")
    ap.add_argument("--baseline", default="v2.3.1", help="Ref git para el baseline en modo comparación")
    ap.add_argument("--refresh", action="store_true", help="Fuerza re-descarga de datos (ignora caché)")
    ap.add_argument("--save-baseline", default=None, help="Guarda métricas del working tree como baseline NOMBRE")
    ap.add_argument("--vs-baseline", default=None, help="Juzga el working tree contra el baseline NOMBRE guardado")
    ap.add_argument("--trace", type=int, default=0, help="Imprime N trades de ejemplo (motivo de salida) por símbolo")
    ap.add_argument("--profiles", default=None, help="JSON grupo→filter_profile aplicado al candidato (A/B de Fase 2)")
    ap.add_argument("--sweep", action="store_true",
                    help="C.2: barre 1 palanca a la vez desde el perfil REALMENTE shippeado (SHIPPED_PROFILES) "
                         "y sugiere overrides ganadores por símbolo (con guardarraíl de muestra mínima)")
    ap.add_argument("--sweep-min-trades", type=int, default=30,
                    help="Trades mínimos en la muestra para que un PASS del sweep cuente como fiable (default 30)")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    _load_profiles(args.profiles)
    WT = load_working_tree()

    # Modo SWEEP (C.2): 1 palanca a la vez desde el perfil REALMENTE shippeado.
    if args.sweep:
        for sym in symbols:
            data = get_data(sym, args.days, args.refresh)
            if not data:
                print(f"  ⚠️  sin datos para {sym}, saltando."); continue

            shipped_full = _shipped_full_profile_for(sym)
            shipped_f, shipped_t = shipped_full["filter"], shipped_full["top"]

            def _make_profile(f, t):
                return {"filter_profile": f, "sl_mult": t["sl_mult"], "min_rr": t["min_rr"],
                        "be_mult": t["be_mult"], "tp_mult": t.get("tp_mult", 2.5)}

            print(f"\n{'='*70}\n  SWEEP {sym}  (grupo {_get_group(sym)})\n"
                  f"  filtros shippeados: {shipped_f}\n  riesgo shippeado:   {shipped_t}\n{'='*70}")
            base_res = await run_variant(WT, sym, data, args.threshold, args.lookback_m1, _make_profile(shipped_f, shipped_t))
            base_m = metrics(base_res)
            print(f"  BASELINE (shippeado): {base_m['trades']} trades | exp {base_m['expectancy']:+.3f}R | "
                  f"sharpe {base_m['sharpe']:.2f} | DD {base_m['max_dd']:.1f}R")

            rows = []
            for scope, key, val in _sweep_candidates(shipped_full):
                cand_f, cand_t = dict(shipped_f), dict(shipped_t)
                (cand_f if scope == "filter" else cand_t)[key] = val
                res = await run_variant(WT, sym, data, args.threshold, args.lookback_m1, _make_profile(cand_f, cand_t))
                m = metrics(res)
                ok, _ = verdict(base_m, m)
                reliable = m["trades"] >= args.sweep_min_trades
                if not reliable:
                    tag = "⚪ MUESTRA INSUFICIENTE"
                elif ok:
                    tag = "✅ PASS"
                else:
                    tag = "❌ FAIL"
                label = f"{key}" if scope == "filter" else f"{key} (risk)"
                rows.append([scope, key, val, label, m, m["expectancy"] - base_m["expectancy"],
                            m["sharpe"] - base_m["sharpe"], tag, reliable, ok])

            # Chequeo de consistencia direccional: para palancas numéricas de 2 lados
            # (entry_threshold, sl_mult, min_rr, be_mult) probadas en ambas direcciones,
            # si AMBAS mejoran el expectancy a la vez no es señal real de "hay que mover
            # el parámetro" — es ruido de muestra (una señal genuina mejora en una
            # dirección y empeora en la otra, como el entry_threshold de BTCUSD). Se anula
            # la candidatura de esa palanca aunque ambos lados hayan dado PASS.
            by_key = {}
            for r in rows:
                by_key.setdefault(r[1], []).append(r)
            for key, group in by_key.items():
                if len(group) == 2 and all(r[8] and r[5] > 0 for r in group):
                    for r in group:
                        r[7] = "🟡 RUIDO (ambas direcciones mejoran, no promover)"
                        r[9] = False  # ya no cuenta como PASS promovible

            rows.sort(key=lambda r: r[5], reverse=True)
            print(f"\n  {'palanca':<20}{'valor':>8}{'trades':>8}{'expR':>9}{'Δexp':>9}{'Δsharpe':>10}   veredicto")
            print(f"  {'-'*70}")
            for scope, key, val, label, m, dexp, dsharpe, tag, reliable, ok in rows:
                print(f"  {label:<20}{str(val):>8}{m['trades']:>8}{m['expectancy']:>+9.3f}{dexp:>+9.3f}{dsharpe:>+10.2f}   {tag}")

            winners = [r for r in rows if r[8] and r[9]]  # fiable, PASS y sin ruido direccional
            if winners:
                scope, key, val = winners[0][0], winners[0][1], winners[0][2]
                dest = "SYMBOL_FILTER_OVERRIDES" if scope == "filter" else "override de sl_mult/min_rr/be_mult"
                suggested = dict(shipped_f) if scope == "filter" else dict(shipped_t)
                suggested[key] = val
                print(f"\n  💡 Sugerencia (fiable, Δexp {winners[0][5]:+.3f}, scope={scope}): {json.dumps(suggested)}")
                print(f"     → revisar y, si se aprueba, añadir a {dest} en database.py")
            else:
                print(f"\n  Sin candidatos fiables que batan al perfil shippeado. Mantener como está.")
        _shutdown_mt5(); print("\nSweep finalizado.\n"); return

    # Modo A: guardar baseline
    if args.save_baseline:
        os.makedirs(BASELINE_DIR, exist_ok=True)
        store = {"days": args.days, "threshold": args.threshold, "created": str(datetime.now()), "symbols": {}}
        print(f"\n# Guardando baseline '{args.save_baseline}' (working tree)")
        for sym in symbols:
            data = get_data(sym, args.days, args.refresh)
            if not data:
                continue
            res = await run_variant(WT, sym, data, args.threshold, args.lookback_m1, _profile_for(sym))
            store["symbols"][sym] = metrics(res)
            print(f"  {sym}: {store['symbols'][sym]['trades']} trades | exp {store['symbols'][sym]['expectancy']:.3f}R")
        path = os.path.join(BASELINE_DIR, f"{args.save_baseline}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
        print(f"\n✅ Baseline guardado en {path}")
        _shutdown_mt5(); return

    # Modo B: juzgar contra baseline guardado
    if args.vs_baseline:
        path = os.path.join(BASELINE_DIR, f"{args.vs_baseline}.json")
        with open(path, encoding="utf-8") as f:
            store = json.load(f)
        print(f"\n# Working tree  vs  baseline guardado '{args.vs_baseline}'")
        all_ok = True
        cand_all = []
        for sym in symbols:
            data = get_data(sym, args.days, args.refresh)
            if not data or sym not in store["symbols"]:
                continue
            res = await run_variant(WT, sym, data, args.threshold, args.lookback_m1, _profile_for(sym))
            cand = metrics(res); base = store["symbols"][sym]; cand_all.append(res)
            print_pair(sym, base, cand, f"BASE:{args.vs_baseline}", "WORKING")
            ok, msg = verdict(base, cand); all_ok = all_ok and ok
            print(f"  → {msg}")
            _maybe_trace(res, args.trace)
        print(f"\n{'='*66}\n  VEREDICTO GLOBAL: {'✅ VERDE — no hay regresión' if all_ok else '❌ ROJO — hay regresión'}\n{'='*66}")
        _shutdown_mt5(); return

    # Modo C (default): working tree vs baseline de git
    print(f"\n{'#'*66}\n#  PST BACKTEST LAB (motor fiel)  |  días {args.days}  thr {args.threshold}\n"
          f"#  baseline (git): {args.baseline}   candidato: working tree\n{'#'*66}")
    BASE = load_git_ref(args.baseline)
    base_all, cand_all = [], []
    for sym in symbols:
        print(f"\n  Datos {sym} ({args.days}d)...")
        data = get_data(sym, args.days, args.refresh)
        if not data:
            continue
        r_base = await run_variant(BASE, sym, data, args.threshold, args.lookback_m1)
        r_cand = await run_variant(WT, sym, data, args.threshold, args.lookback_m1, _profile_for(sym))
        base_all.append(r_base); cand_all.append(r_cand)
        print_pair(sym, metrics(r_base), metrics(r_cand), f"BASE:{args.baseline}", "WORKING")
        ok, msg = verdict(metrics(r_base), metrics(r_cand))
        print(f"  → {msg}")
        _maybe_trace(r_cand, args.trace)

    ba, ca = aggregate(base_all), aggregate(cand_all)
    if ba and ca:
        print_pair("GLOBAL", ba, ca, f"BASE:{args.baseline}", "WORKING")
        ok, msg = verdict(ba, ca)
        print(f"\n  VEREDICTO GLOBAL → {msg}")
    _shutdown_mt5()
    print("\nLab finalizado.\n")


def _maybe_trace(res, n):
    if n <= 0:
        return
    print(f"    · trazas ({min(n, len(res.trades))} de {len(res.trades)}):")
    for t in res.trades[:n]:
        print(f"      {('BUY ' if t.direction==1 else 'SELL')} @ {t.entry_price:.5f} "
              f"SL {t.sl_price:.5f} TP {t.tp_price:.5f} → {t.exit_reason or t.result} "
              f"pnl {t.pnl_r:+.2f}R")


def _shutdown_mt5():
    try:
        import MetaTrader5 as mt5
        mt5.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
