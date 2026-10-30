#!/usr/bin/env python
"""
Experimento: un agente Claude que decide operaciones sobre UN simbolo (por defecto BTCUSD) en
una cuenta DEMO, para medir cuantos tokens/cuota cuesta de verdad y como razona. NO es una
estrategia con edge probado: las familias de analisis tecnico probadas en el proyecto no lo
tenian y esto es lo mismo con otro narrador. Se conserva el registro de cada decision.

DISENO
    * El agente NO tiene herramientas de trading ni de nada: `--tools ""` y sin MCP. Solo recibe
      un JSON de estado y devuelve una decision validada con --json-schema. Con herramientas, el
      MCP mete decenas de miles de tokens de definiciones en cada llamada.
    * Este script es el unico que toca MT5, y aplica los limites sin preguntar al modelo.
    * Se ejecuta en un directorio vacio (no hereda CLAUDE.md ni memoria del proyecto) y con
      --system-prompt propio (sustituye al de Claude Code): menos tokens de suelo por llamada.
    * Va por `claude -p` (suscripcion, sin API key). La cuota (5h + semanal) se comparte con el
      trabajo interactivo, asi que se respeta el mismo freno que el planificador de la flota.

MODOS
    (defecto)  SIMULACION: decide y registra, no envia ordenes.
    --live     Envia ordenes reales. Se niega si la cuenta no es DEMO.

USO
    python PST_LLM/llm_trader.py --once            # una decision ya, en simulacion
    python PST_LLM/llm_trader.py                   # bucle: decide al cierre de cada vela H1
    python PST_LLM/llm_trader.py --live            # igual pero operando en la demo
    python PST_LLM/llm_trader.py --report          # tokens, coste y resultado acumulados
    Apagado de emergencia: crear el fichero .agents/data/llm_trader.STOP
"""
import argparse
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

import prompts                        # criterio de entrada por simbolo, en BBDD (PST_LLM/prompts.py)
import calendario                     # puerta de calendario FTMO (PST_LLM/calendario.py)
import sesiones                       # paradas recurrentes del mercado: cierre antes del fin de semana (PST_LLM/sesiones.py)
import registro                       # BBDD central: decisiones y agentes para la app
import operaciones                    # una fila por operacion cerrada en llm_operaciones (PST_LLM/operaciones.py)
import universo
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))             # raíz del repo: para importar PST_Core
from PST_Core.utils.reloj_mt5 import local_desde_mt5, broker_desde_mt5   # horas de MT5: ver utils/reloj_mt5
from PST_Core.utils import alertas                                       # avisos criticos por Telegram (PST_Core/utils/alertas.py)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

import rutas                                           # rutas de ejecucion: repo o exe (PST_LLM/rutas.py)
REPO_ROOT = rutas.DATOS
DATA_DIR = REPO_ROOT / ".agents" / "data"
DB_FILE = DATA_DIR / "llm_trader.db"
STOP_FILE = DATA_DIR / "llm_trader.STOP"
# Directorio de trabajo del agente. TIENE que quedar FUERA del repo: Claude Code toma el CLAUDE.md
# y la memoria automatica de la raiz git del cwd, asi que un subdirectorio del repo hereda todo el
# contexto del proyecto (medido 2026-10-21: el agente citaba las notas de memoria sobre cripto en
# sus razonamientos, lo que sesga el experimento y encarece cada llamada). El nombre del directorio
# es el que Habblaud muestra como sala, y se elige con "nombre" en la configuracion: un agente por
# simbolo = un nombre distinto = una sala distinta.
NOMBRE_DEFECTO = "Agente_LLM"
NOMBRE = NOMBRE_DEFECTO          # se fija en configurar_agente


def cwd_agente(nombre):
    d = Path.home() / ".pst_llm_trader" / nombre
    d.mkdir(parents=True, exist_ok=True)
    return d

MAGIC = 109001                # se sobreescribe por agente (--magic): uno distinto por simbolo
COMMENT = "LLM_TRADER"
STOP_GLOBAL = DATA_DIR / "llm_trader.STOP"     # apaga TODOS los agentes
MAGICS_FLOTA = set(range(109001, 109200))      # todos los agentes LLM (universo.py usa 109005-109014): limite conjunto


def configurar_agente(nombre, magic):
    """
    Cada agente (nombre) tiene sus propios fichero de configuracion, BBDD y STOP, para que varios
    procesos convivan sin pisarse. El agente por defecto conserva los nombres historicos
    (llm_trader.json / .db / .STOP); los demas llevan el nombre como sufijo.
    """
    global DB_FILE, STOP_FILE, CONFIG_FILE, MAGIC, NOMBRE
    suf = "" if nombre == NOMBRE_DEFECTO else f"_{nombre}"
    DB_FILE = DATA_DIR / f"llm_trader{suf}.db"
    STOP_FILE = DATA_DIR / f"llm_trader{suf}.STOP"
    CONFIG_FILE = DATA_DIR / f"llm_trader{suf}.json"
    MAGIC = magic
    NOMBRE = nombre
RIESGO_PCT = 0.5            # % de equity arriesgado por operacion
SL_MULT = (1.0, 3.0)        # el modelo elige dentro de este rango (x ATR)
TP_R = (1.8, 4.0)           # y el TP como multiplo del riesgo. Minimo 1,8 (plan v3 §1.2): tp_r <= 1,5 rendia -0,19 R
# Marcos en los que se puede basar el stop. Semana del 23-30 oct: M1 -0,66 R, M5 -0,09 R, M15 +0,20 R (plan v3 §1.2).
# M5 sigue en el snapshot para afinar la entrada, pero el stop no se mide con el.
STOP_TFS = ("M15", "H1", "H4")
# Costes y exposicion (plan v3 fase 2)
MAX_COSTE_R = 0.10          # spread + comision ida y vuelta, como fraccion del R: por encima no se abre
# Stop minimo por clase, en ATR de M15 (no del marco que elija el agente): si el stop sale mas corto se ensancha
# (el lote baja, el riesgo no cambia). Valores iniciales: calibrar con el MAE de llm_operaciones.
STOP_MIN_ATR_M15 = {"indice": 1.0, "accion_eeuu": 1.0, "forex": 1.0, "materias": 1.2, "cripto": 1.5}
MAX_MISMO_LADO_CLASE = 3    # posiciones abiertas de la flota en el mismo lado y la misma clase
MAX_MISMO_LADO_TOTAL = 5    # y en el mismo lado en total

# En Windows, cada `claude` lanzado desde aqui abria una consola negra. CREATE_NO_WINDOW la evita:
# el proceso sigue en el Administrador de tareas, pero sin ventana. En otros SO es 0 (sin efecto).
_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)

MAX_SPREAD_SOBRE_STOP = 0.15   # se rechaza la orden si el spread supera este % de la distancia al stop

# Parte FIJA del prompt (formato, costes, cierres, campos). El CRITERIO de entrada va aparte, uno por
# simbolo, en la BBDD (PST_LLM/prompts.py): lo editas en Ajustes o lo cambia una propuesta aprobada.
PROMPT_FIJO = (
    f"""ROLE
You are a discretionary trader on a DEMO account managing one symbol. You are called at regular intervals
('call_interval'). The call interval is NOT your trading timeframe: each snapshot holds several timeframes at
once and YOU decide which timeframe each trade is based on.

OUTPUT
Reply ONLY with the JSON decision requested by the schema. 'action' is one of hold, buy, sell, close. With no
open position choose hold, buy or sell; with an open position choose hold or close. Follow the TRADING CRITERIA
section below to decide whether and where to enter.
- 'timeframe': the timeframe whose structure justifies the trade, one of {", ".join(STOP_TFS)}. The stop distance
  (sl_atr_mult x ATR) is measured on THAT timeframe's ATR, so a wider timeframe means a wider stop and a longer
  holding time. Shorter timeframes (M1, M5) stay in the snapshot to time the entry but cannot carry the stop.
- 'sl_atr_mult': stop distance in ATRs of that timeframe, between {SL_MULT[0]} and {SL_MULT[1]}.
- 'tp_r': take-profit as a multiple of the risk, between {TP_R[0]} and {TP_R[1]}.
- 'confidence': between 0 and 1.
- 'reason': IN SPANISH, labelled lines separated by newlines, at most 700 characters, in this order:
  'Tendencia:' (higher timeframes and their direction), 'Estructura:' (the levels and indicator readings you
  used, naming each timeframe and the actual numbers from the snapshot), 'Decisión:' (what you do and why),
  'Riesgo:' (what would prove the idea wrong, or why you stay out). Be specific and concrete, no generic phrases.
Values outside these ranges are clamped by the system, not rejected: stay inside them.

TRADING CRITERIA (specific to this symbol, written in Spanish; it may be revised over time)
{{criterio}}

COSTS AND SIZING (enforced by the system)
- The system rejects any trade whose stop is less than ~{1 / MAX_SPREAD_SOBRE_STOP:.1f}x the spread, or whose total
  cost (spread + round-trip commission) is above {MAX_COSTE_R * 100:.0f}% of the risk.
  'spread_atr' (spread / ATR) is given per timeframe: avoid timeframes where it is high.
- The fleet may hold at most {MAX_MISMO_LADO_CLASE} positions on the same side per asset class and {MAX_MISMO_LADO_TOTAL} on the
  same side in total: check 'cartera' before proposing a trade that stacks the same exposure.
- Risk per trade is sized by the system. You only choose the stop distance and the take-profit multiple.

CLOSING RULES (enforced by the system)
- A close is rejected while position.close_locked_for_min is greater than 0. That field gives the minutes
  left before a close is allowed.
- After that, a close is accepted only if 'invalidated_by' names the CONCRETE structure that broke
  (timeframe + level or indicator).
- 'invalidated_by' must name a structure on H1, H4 or D1. A break on M1, M5 or M15 never justifies a close.
- The system manages the position itself: at +1R it moves the stop to the entry, and at +1.5R it closes half and
  leaves the rest with the stop at +0.5R. Do not try to do that by hand; your only close is a real invalidation.
- Being in a loss inside the stop is NOT a reason to close, and neither is a small bounce: the stop and the
  target exist to do that job.

STATE FIELDS (units in brackets)
- symbol; call_interval (the clock, not your trading timeframe); spread_price [price units]; equity [account currency].
- timeframes.<TF>.closed_at: time of the last closed candle (broker time).
- timeframes.<TF>.candles_ohlc: last closed candles as [open, high, low, close], oldest first; the last one is the most recent closed candle.
- timeframes.<TF>.ind: atr [price units]; rsi [0-100]; adx [0-100]; pdi, mdi [0-100]; ema20, ema50, ema200 [price units]; bb_pos [0 = lower Bollinger band, 1 = upper band; outside 0-1 means price is beyond the band].
- timeframes.<TF>.px_vs_ema20_atr: signed distance of the last close to EMA20 [ATRs].
- timeframes.<TF>.spread_atr: spread / ATR [ratio].
- own_history: your last closed trades, oldest first: side; opened [local time]; minutes; exit (STOP, TAKE PROFIT, manual, agent, revisor, other); R (approximate: net result after costs / risk at entry, so -1 is a full stop); net_eur. Empty list if none. Use it to calibrate, do not chase the last result.
- timeframes.<TF>.niveles: structure levels of that timeframe. maximo_20 / minimo_20 [price units]: highest high and lowest low of the last 20 closed candles. swing_alto / swing_bajo [price units]: most recent swing high / low in the last 60 candles (a candle higher / lower than its 2 neighbours on each side), null if none. dist_*_atr: signed distance from the last close to each level [ATRs of that timeframe]; negative = the level is below the close.
- dia_anterior: the previous daily candle: maximo, minimo, cierre [price units]; dist_maximo_atr, dist_minimo_atr [ATRs of the call-interval timeframe]; null if unavailable.
- cartera: open positions of ALL the LLM agents together (the flota): abiertas; por_lado {{buy, sell}}; flotante_eur and realizado_hoy_eur [account currency]; posiciones: symbol, side, clase (asset class), floating_eur. Your own symbol appears there too if you are in a trade. Use it to avoid stacking exposure to the same risk (for example, several index shorts at once).
- position (null if none): side; open_price; sl; tp [price units]; floating_pnl [account currency]; minutes_held; close_locked_for_min [minutes].
"""
)

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["hold", "buy", "sell", "close"]},
        "timeframe": {"type": "string", "enum": list(STOP_TFS)},
        "sl_atr_mult": {"type": "number", "minimum": SL_MULT[0], "maximum": SL_MULT[1]},
        "tp_r": {"type": "number", "minimum": TP_R[0], "maximum": TP_R[1]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
        "invalidated_by": {"type": "string"},      # obligatorio para cerrar: que se rompio, en que marco
    },
    "required": ["action", "reason"],
}


def montar_prompt(criterio):
    return PROMPT_FIJO.replace("{criterio}", criterio.strip())


# Prompt completo del agente: fijo + criterio de su simbolo. Se recalcula en cada vuelta (prompt_actual);
# si cambia, la sesion continua se reabre con el nuevo y la version nueva queda guardada para la app.
SYSTEM_PROMPT = montar_prompt(prompts.CRITERIO_DEFECTO)
PROMPT_VERSION = registro.prompt_version(SYSTEM_PROMPT)
_VERSIONES_GUARDADAS = set()


def prompt_actual(symbol):
    """Fija SYSTEM_PROMPT/PROMPT_VERSION con el criterio activo del simbolo. Nunca lanza."""
    global SYSTEM_PROMPT, PROMPT_VERSION
    SYSTEM_PROMPT = montar_prompt(prompts.texto_seguro(symbol))
    PROMPT_VERSION = registro.prompt_version(SYSTEM_PROMPT)
    if PROMPT_VERSION not in _VERSIONES_GUARDADAS:
        registro.guardar_prompt(PROMPT_VERSION, SYSTEM_PROMPT)
        _VERSIONES_GUARDADAS.add(PROMPT_VERSION)
    _SESION.prompt = SYSTEM_PROMPT
    return SYSTEM_PROMPT, PROMPT_VERSION


# ── BBDD de decisiones ───────────────────────────────────────────────────────
def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_FILE, timeout=15)
    con.execute("""CREATE TABLE IF NOT EXISTS decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, mode TEXT, model TEXT,
        llm_called INTEGER, gate_reason TEXT, action TEXT, sl_mult REAL, tp_r REAL,
        confidence REAL, reason TEXT, tokens_in INTEGER, tokens_out INTEGER,
        tokens_cache_read INTEGER, tokens_cache_write INTEGER, cost_usd REAL, duration_s REAL,
        executed INTEGER, exec_result TEXT, ticket INTEGER, state_json TEXT)""")
    for col in ("quota_5h REAL", "quota_7d REAL", "timeframe TEXT", "horizonte TEXT", "recorte TEXT", "prompt_version TEXT"):   # tablas creadas antes de esto
        try:
            con.execute(f"ALTER TABLE decisions ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    return con


# ── Configuracion en caliente ────────────────────────────────────────────────
# Fichero editable (a mano hoy, desde un panel de la app mañana). Se relee en cada vuelta del
# bucle, asi que cambiar la cadencia o apagarlo no exige reiniciar el proceso. Lo que ponga
# aqui manda sobre los argumentos de la linea de comandos.
CONFIG_FILE = DATA_DIR / "llm_trader.json"
CONFIG_CLAVES = {"enabled": bool, "symbol": str, "timeframe": str, "model": str, "effort": str,
                 "live": bool, "sin_filtro": bool, "max_llamadas_dia": int, "max_perdida_dia": float,
                 "sesion_continua": bool, "reiniciar_cada": int, "telegram": bool, "aviso_cooldown_min": int,
                 "timeframes_analisis": list, "max_perdida_flota_dia": float, "equity_minima": float,
                 "enfriamiento_min": int, "min_cierre_min": int, "margen_sesion_min": int}
TF_SEGUNDOS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
# "timeframe" es CADA CUANTO se llama al agente (el reloj). "timeframes_analisis" son los marcos
# que ve en cada llamada; el agente elige en cual se apoya cada operacion.
TF_ANALISIS_DEFECTO = ["M5", "M15", "H1", "H4", "D1"]


def aplicar_config(args):
    """Vuelca el fichero sobre args. Devuelve la lista de cambios (para avisar por consola)."""
    if not CONFIG_FILE.is_file():
        return []
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))   # tolera BOM (Notepad/PowerShell)
    except Exception as e:
        print(f"[llm_trader] config ilegible, se ignora: {e}", flush=True)
        return []
    cambios = []
    for k, tipo in CONFIG_CLAVES.items():
        if k in cfg:
            try:
                if tipo is list:
                    v = [t for t in cfg[k] if t in TF_SEGUNDOS]
                    if not v:
                        continue
                else:
                    v = tipo(cfg[k])
            except (TypeError, ValueError):
                continue
            if k == "timeframe" and v not in TF_SEGUNDOS:
                continue
            if getattr(args, k, None) != v:
                cambios.append(f"{k}: {getattr(args, k, None)} -> {v}")
                setattr(args, k, v)
    return cambios


def log_decision(con, **k):
    k.setdefault("prompt_version", PROMPT_VERSION)
    cols = ", ".join(k)
    con.execute(f"INSERT INTO decisions ({cols}) VALUES ({', '.join('?' for _ in k)})", tuple(k.values()))
    con.commit()
    _registrar_central(k)


_ULT_TICK = {"time": None, "visto": 0.0}


def mercado_vivo(tick, ventana_s=1200):
    """True si el ultimo tick cambio hace menos de ventana_s segundos (reloj local, no el del broker)."""
    t = getattr(tick, "time", None)
    ahora = time.time()
    if _ULT_TICK["time"] != t:
        _ULT_TICK.update(time=t, visto=ahora)
        return True
    return ahora - _ULT_TICK["visto"] < ventana_s


def _validacion(k):
    """Que paso con la orden: None si no hubo orden que validar."""
    if k.get("action") not in ("buy", "sell") or not k.get("llm_called"):
        return None
    return "ejecutada" if k.get("executed") else f"no ejecutada: {k.get('exec_result', '')}"[:200]


def _registrar_central(k):
    """Copia la decision a la BBDD central (pst_trading.db) que lee la app. Nunca para al agente."""
    simbolo = k.get("symbol")
    clase = next((s.clase for s in universo.UNIVERSO if s.symbol == simbolo), None)
    respuesta = {x: k.get(x) for x in ("action", "sl_mult", "tp_r", "confidence", "reason", "horizonte") if k.get(x) is not None}
    registro.registrar_decision(
        ts=k.get("ts"), symbol=simbolo, agente=NOMBRE, clase=clase, modo=k.get("mode"), modelo=k.get("model"),
        prompt_version=PROMPT_VERSION, llamado=k.get("llm_called"), motivo_puerta=k.get("gate_reason"),
        entrada_json=k.get("state_json"), respuesta_json=json.dumps(respuesta, ensure_ascii=False) if respuesta else None,
        accion=k.get("action"), confianza=k.get("confidence"), validacion=_validacion(k),
        ejecutado=k.get("executed"), resultado=k.get("exec_result"), tokens_in=k.get("tokens_in"),
        tokens_out=k.get("tokens_out"), tokens_cache=k.get("tokens_cache_read"), coste_usd=k.get("cost_usd"))


# ── Indicadores (numpy, Wilder donde toca) ───────────────────────────────────
def ema(x, n):
    a = 2 / (n + 1)
    out = np.empty_like(x, dtype=float)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def wilder(x, n):
    out = np.empty_like(x, dtype=float)
    out[:n] = np.nan
    out[n - 1] = np.mean(x[:n])
    for i in range(n, len(x)):
        out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out


def indicadores(o, h, l, c):
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    atr = wilder(tr, 14)
    d = np.diff(c, prepend=c[0])
    rsi_v = 100 - 100 / (1 + wilder(np.where(d > 0, d, 0), 14) / np.maximum(wilder(np.where(d < 0, -d, 0), 14), 1e-12))
    up, dn = h - np.r_[h[0], h[:-1]], np.r_[l[0], l[:-1]] - l
    pdm, mdm = np.where((up > dn) & (up > 0), up, 0), np.where((dn > up) & (dn > 0), dn, 0)
    pdi, mdi = 100 * wilder(pdm, 14) / atr, 100 * wilder(mdm, 14) / atr
    dx = 100 * abs(pdi - mdi) / np.maximum(pdi + mdi, 1e-12)
    adx = wilder(np.nan_to_num(dx), 14)
    sma20 = np.convolve(c, np.ones(20) / 20, mode="valid")
    sd20 = np.array([np.std(c[i - 19:i + 1]) for i in range(19, len(c))])
    return {"atr": atr[-1], "rsi": rsi_v[-1], "adx": adx[-1], "pdi": pdi[-1], "mdi": mdi[-1],
            "ema20": ema(c, 20)[-1], "ema50": ema(c, 50)[-1], "ema200": ema(c, 200)[-1],
            "bb_pos": (c[-1] - (sma20[-1] - 2 * sd20[-1])) / max(4 * sd20[-1], 1e-12)}


def niveles(o, h, l, c, atr, digits, n_rango=20, n_swing=60):
    """
    Niveles de estructura del marco: maximo y minimo de las ultimas n_rango velas cerradas y el ultimo
    swing alto/bajo (una vela mas alta/baja que sus 2 vecinas a cada lado) de las ultimas n_swing.
    Distancias con signo en ATRs de ESTE marco: negativa = el nivel esta por debajo del cierre.
    """
    cierre = c[-1]
    alto_r, bajo_r = h[-n_rango:].max(), l[-n_rango:].min()
    sw_alto = sw_bajo = None
    for i in range(len(h) - 3, max(len(h) - n_swing, 2) - 1, -1):
        if sw_alto is None and h[i] > max(h[i - 2], h[i - 1]) and h[i] > max(h[i + 1], h[i + 2]):
            sw_alto = float(h[i])
        if sw_bajo is None and l[i] < min(l[i - 2], l[i - 1]) and l[i] < min(l[i + 1], l[i + 2]):
            sw_bajo = float(l[i])
        if sw_alto is not None and sw_bajo is not None:
            break
    def dist(nivel):
        return None if nivel is None else round((nivel - cierre) / atr, 2)
    return {"maximo_20": round(float(alto_r), digits), "minimo_20": round(float(bajo_r), digits),
            "dist_maximo_20_atr": dist(alto_r), "dist_minimo_20_atr": dist(bajo_r),
            "swing_alto": None if sw_alto is None else round(sw_alto, digits), "dist_swing_alto_atr": dist(sw_alto),
            "swing_bajo": None if sw_bajo is None else round(sw_bajo, digits), "dist_swing_bajo_atr": dist(sw_bajo)}


def dia_anterior(mt5, symbol, atr, cierre, digits):
    """Maximo, minimo y cierre de la ultima vela diaria cerrada, con distancia al cierre del marco del reloj."""
    d1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 3)
    if d1 is None or len(d1) < 2:
        return None
    p = d1[-2]                                    # d1[-1] es el dia en curso
    alto, bajo = float(p["high"]), float(p["low"])
    return {"maximo": round(alto, digits), "minimo": round(bajo, digits), "cierre": round(float(p["close"]), digits),
            "dist_maximo_atr": round((alto - cierre) / atr, 2), "dist_minimo_atr": round((bajo - cierre) / atr, 2)}


def cartera(mt5, symbol):
    """Posiciones abiertas de TODA la flota de agentes LLM: el agente ve donde esta expuesto el conjunto."""
    pos = [p for p in (mt5.positions_get() or []) if p.magic in MAGICS_FLOTA]
    realizado, flotante = pnl_flota(mt5, MAGICS_FLOTA)
    return {"abiertas": len(pos), "por_lado": {"buy": sum(1 for p in pos if p.type == 0),
                                               "sell": sum(1 for p in pos if p.type == 1)},
            "flotante_eur": round(flotante, 2), "realizado_hoy_eur": round(realizado, 2),
            "posiciones": [{"symbol": p.symbol, "side": "buy" if p.type == 0 else "sell",
                            "clase": next((s.clase for s in universo.UNIVERSO if s.symbol == p.symbol), None),
                            "floating_eur": round(p.profit, 2)} for p in pos[:20]]}


# ── Estado y puerta previa ───────────────────────────────────────────────────
def historial_propio(mt5, n=5):
    """
    Las ultimas n operaciones YA cerradas de ESTE agente, de la mas antigua a la mas reciente: lado,
    hora de entrada (local), minutos, salida, R aproximado (neto / riesgo en la entrada) y neto en EUR.
    Plan §3.2 punto 6: el agente ve sus propios resultados. Las abiertas no cuentan.
    """
    if not DB_FILE.is_file():
        return []
    c = sqlite3.connect(DB_FILE, timeout=15)
    filas = c.execute("SELECT action, ticket, exec_result FROM decisions WHERE executed=1 AND action IN ('buy','sell') "
                      "AND ticket IS NOT NULL ORDER BY id DESC LIMIT ?", (n * 3,)).fetchall()
    c.close()
    out = []
    for accion, ticket, res in filas:
        if mt5.positions_get(ticket=ticket):
            continue                                  # sigue abierta: aun no es historia
        deals = mt5.history_deals_get(position=ticket) or []
        ent = [d for d in deals if d.entry == 0]
        sal = [d for d in deals if d.entry in (1, 2, 3)]
        if not ent or not sal:
            continue
        m = re.search(r"riesgo ~(\d+)", res or "")
        riesgo = float(m.group(1)) if m else None
        neto = sum(d.profit + d.commission + d.swap for d in deals)
        s = sal[-1]
        salida = "revisor" if "REVISOR" in (s.comment or "").upper() else RAZON_DEAL.get(s.reason, "otra")
        out.append({"side": accion, "opened": local_desde_mt5(ent[0].time).strftime("%m-%d %H:%M"),
                    "minutes": int((s.time - ent[0].time) / 60), "exit": salida,
                    "R": round(neto / riesgo, 2) if riesgo else None, "net_eur": round(neto, 2)})
        if len(out) == n:
            break
    return out[::-1]


def snapshot(mt5, symbol, cadencia, tfs):
    """
    Estado multi-marco. `cadencia` es el reloj (cada cuanto se llama) y entra siempre en el
    analisis; `tfs` son los marcos que ve el agente. Devuelve ind_por_tf: {tf: indicadores}.
    """
    tfs = sorted({t for t in (tfs or TF_ANALISIS_DEFECTO) if t in TF_SEGUNDOS} | {cadencia}, key=TF_SEGUNDOS.get)
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    spread = tick.ask - tick.bid
    marcos, ind_por_tf = {}, {}
    for tf in tfs:
        rates = mt5.copy_rates_from_pos(symbol, getattr(mt5, f"TIMEFRAME_{tf}"), 0, 320)
        if rates is None or len(rates) < 250:
            continue                              # p. ej. D1 con poco historial: ese marco se omite
        cerradas = rates[:-1]                     # la ultima esta formandose
        o, h, l, c = (cerradas[k].astype(float) for k in ("open", "high", "low", "close"))
        ind = indicadores(o, h, l, c)
        ind_por_tf[tf] = ind
        n = 24 if tf == cadencia else 8          # mas detalle en el marco del reloj
        marcos[tf] = {
            "closed_at": broker_desde_mt5(int(cerradas[-1]["time"])).strftime("%m-%d %H:%M"),
            # Decimales del SIMBOLO, no 2 fijos: con 2, EURUSD (5 decimales) llegaba al agente con todas las
            # velas en 1.12 y ATR 0 (detectado el 2026-10-21 porque el propio agente lo dijo). Los
            # indicadores en escala de precio usan los decimales del simbolo; los acotados (RSI, ADX...) no.
            "candles_ohlc": [[round(float(x), info.digits) for x in (o[i], h[i], l[i], c[i])] for i in range(-n, 0)],
            "ind": {k: round(float(v), info.digits if k in ("atr", "ema20", "ema50", "ema200") else 2)
                    for k, v in ind.items()},
            "px_vs_ema20_atr": round(float((c[-1] - ind["ema20"]) / ind["atr"]), 2),
            "spread_atr": round(float(spread / ind["atr"]), 3),
            "niveles": niveles(o, h, l, c, ind["atr"], info.digits),
        }
    if cadencia not in ind_por_tf:
        return None
    pos = [p for p in (mt5.positions_get(symbol=symbol) or []) if p.magic == MAGIC]
    cuenta = mt5.account_info()
    estado = {
        "symbol": symbol, "call_interval": cadencia, "spread_price": round(float(spread), info.digits),
        "equity": round(cuenta.equity, 2), "timeframes": marcos, "position": None,
    }
    estado["own_history"] = historial_propio(mt5)
    estado["dia_anterior"] = dia_anterior(mt5, symbol, ind_por_tf[cadencia]["atr"],
                                          marcos[cadencia]["candles_ohlc"][-1][3], info.digits)
    estado["cartera"] = cartera(mt5, symbol)
    if pos:
        p = pos[0]
        estado["position"] = {
            "side": "buy" if p.type == 0 else "sell", "open_price": p.price_open, "sl": p.sl, "tp": p.tp,
            "floating_pnl": round(p.profit, 2), "minutes_held": int((tick.time - p.time) // 60),
        }
    return estado, ind_por_tf, tick, info, pos


def puerta(ind, hay_pos, sin_filtro):
    """(llamar?, motivo). Gratis: se evalua antes de gastar un solo token."""
    if sin_filtro:
        return True, "sin filtro"
    if hay_pos:
        return True, "posicion abierta que gestionar"
    if ind["adx"] > 25:
        return True, f"ADX {ind['adx']:.0f} > 25"
    if ind["rsi"] < 30 or ind["rsi"] > 70:
        return True, f"RSI {ind['rsi']:.0f} en extremo"
    if ind["bb_pos"] < 0 or ind["bb_pos"] > 1:
        return True, "precio fuera de Bollinger"
    return False, "sin setup (ADX<=25, RSI neutro, dentro de Bollinger)"


# ── Llamada al agente ────────────────────────────────────────────────────────
def _resolve_claude():
    spec = importlib.util.spec_from_file_location("run_agent", rutas.AGENTS_CODE_DIR / "run_agent.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.resolve_claude()


LLM_GLOBAL = DATA_DIR / "llm_global.json"   # tope diario global (lo escribe la app en Ajustes); 0 = sin tope


def _tope_diario():
    """Llamadas al dia permitidas para todos los agentes. Se relee en cada llamada; sin fichero = sin tope."""
    try:
        return max(0, int(json.loads(LLM_GLOBAL.read_text(encoding="utf-8-sig")).get("max_llamadas_dia", 0)))
    except (FileNotFoundError, ValueError, TypeError):
        return 0


def cuota_ok():
    spec = importlib.util.spec_from_file_location("scheduler", rutas.AGENTS_CODE_DIR / "scheduler.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    q = m.leer_cuota()
    topes = m.leer_topes_cuota()                       # configurables en la app (Ajustes > Flota LLM)
    if q and q.get("cinco_h") is not None and q["cinco_h"] > topes["max_5h"]:
        return False, f"ventana de 5h al {q['cinco_h']}% (tope {topes['max_5h']}%)", q
    # La semanal es la restriccion de fondo si esto corre 24/7 (la de 5h se recupera sola en horas).
    if q and q.get("semanal") is not None and q["semanal"] > topes["max_semanal"]:
        return False, f"cuota semanal al {q['semanal']}% (tope {topes['max_semanal']}%)", q
    return True, "", q


def _comando_base(model, effort, prompt=None, schema=None):
    return [_resolve_claude(), "-p", "--model", model, "--effort", effort,
            "--json-schema", json.dumps(schema or DECISION_SCHEMA), "--system-prompt", prompt or SYSTEM_PROMPT,
            "--tools", "", "--strict-mcp-config", "--permission-prompts", "none"]


def mantener_despierto():
    """
    Pide a Windows que no suspenda el equipo por inactividad MIENTRAS este proceso viva (no cambia
    ningun ajuste del sistema: al terminar el proceso se libera solo). Sin esto, de madrugada el PC
    se duerme y los agentes se paran sin avisar. No impide el apagado manual ni cerrar la tapa.
    """
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)   # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    except Exception:
        pass


def _uso(env, dur):
    u = env.get("usage") or {}
    return {"tokens_in": u.get("input_tokens"), "tokens_out": u.get("output_tokens"),
            "tokens_cache_read": u.get("cache_read_input_tokens"),
            "tokens_cache_write": u.get("cache_creation_input_tokens"),
            "cost_usd": env.get("total_cost_usd"), "duration_s": round(dur, 1)}


def _decision(env):
    res = env.get("structured_output") or env.get("result", env)
    return json.loads(res) if isinstance(res, str) else res


def preguntar_suelto(estado, model, effort, nombre):
    """Una sesion nueva por decision: el proceso vive unos segundos y se va."""
    cmd = _comando_base(model, effort, SYSTEM_PROMPT) + ["--output-format", "json", json.dumps(estado, separators=(",", ":"))]
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(cwd_agente(nombre)), capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       timeout=180, encoding="utf-8", errors="replace", creationflags=_SIN_VENTANA)
    dur = time.time() - t0
    if r.returncode != 0:
        raise RuntimeError(f"claude -p fallo ({r.returncode}): {(r.stderr or r.stdout)[-400:]}")
    env = json.loads(r.stdout)
    return _decision(env), _uso(env, dur)


class SesionContinua:
    """
    UN proceso `claude` que se queda abierto entre decisiones (entrada/salida stream-json).

    Por que: Habblaud solo ve sesiones abiertas. Con una sesion por decision la sala aparecia y
    desaparecia en 3-6 s; con esta el agente esta siempre y se ve ocupado solo mientras decide.
    Efecto secundario util: recuerda lo que decidio en las velas anteriores.
    Coste: el historial crece y cada llamada lo relee (de cache, pero crece), asi que la sesion se
    reinicia cada `reiniciar_cada` decisiones. Ese es el unico parpadeo.
    """
    def __init__(self, prompt=None, schema=None):
        self.proc = None
        self.firma = None
        self.turnos = 0
        self._cola = None
        self.prompt, self.schema = prompt, schema      # por defecto los del trader (los usa tambien el revisor)

    def _abrir(self, model, effort, nombre):
        import queue, threading
        cmd = _comando_base(model, effort, self.prompt, self.schema) + [
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
        self.proc = subprocess.Popen(cmd, cwd=str(cwd_agente(nombre)), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", bufsize=1,
                                     creationflags=_SIN_VENTANA)
        self._cola = queue.Queue()
        def _leer(p, q):
            for linea in p.stdout:
                q.put(linea)
            q.put(None)                       # EOF: el proceso termino
        threading.Thread(target=_leer, args=(self.proc, self._cola), daemon=True).start()
        self.firma, self.turnos = (model, effort, nombre, self.prompt), 0

    def asegurar(self, model, effort, nombre):
        """Mantiene la sesion ABIERTA aunque no haya nada que decidir (sin mensajes no gasta nada):
        un proceso `claude` en espera ya crea su fichero de sesion, que es lo que ve Habblaud."""
        # El prompt forma parte de la firma: si cambia el criterio del simbolo, se reabre con el nuevo.
        if self.proc is None or self.proc.poll() is not None or self.firma != (model, effort, nombre, self.prompt):
            self.cerrar()
            self._abrir(model, effort, nombre)

    def cerrar(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
        self.proc = None

    def preguntar(self, estado, model, effort, nombre, reiniciar_cada):
        import queue
        self.asegurar(model, effort, nombre)
        t0 = time.time()
        msg = {"type": "user", "message": {"role": "user", "content": json.dumps(estado, separators=(",", ":"))}}
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
            while True:
                try:
                    linea = self._cola.get(timeout=150)
                except queue.Empty:
                    raise RuntimeError("la sesion no respondio en 150s")
                if linea is None:
                    raise RuntimeError("la sesion se cerro sola")
                try:
                    ev = json.loads(linea)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "result":
                    if ev.get("is_error"):
                        raise RuntimeError(f"error del agente: {str(ev.get('result'))[:300]}")
                    self.turnos += 1
                    res = _decision(ev), _uso(ev, time.time() - t0)
                    if reiniciar_cada and self.turnos >= reiniciar_cada:
                        # Reinicio YA, no en la siguiente decision: asi la sala no queda vacia
                        # hasta la proxima vela. El hueco es el par de segundos de relevo.
                        self.cerrar()
                        self._abrir(model, effort, nombre)
                    return res
        except Exception:
            self.cerrar()          # el siguiente ciclo del bucle la reabre limpia
            raise


_SESION = SesionContinua()


def preguntar(estado, args):
    if args.sesion_continua:
        return _SESION.preguntar(estado, args.model, args.effort, args.sala, args.reiniciar_cada)
    _SESION.cerrar()               # por si se acaba de apagar el modo continuo desde el fichero
    return preguntar_suelto(estado, args.model, args.effort, args.sala)


# ── Ejecucion ────────────────────────────────────────────────────────────────
def calcular_orden(mt5, d, ind_por_tf, tick, info, cuenta, cadencia):
    """
    Traduce la decision (buy/sell + marco + stop en ATRs + TP en R) a una orden concreta: precio,
    SL, TP y lotes por riesgo. La usan por igual la simulacion (para que el usuario vea el nivel
    exacto y pueda replicarlo a mano) y el modo live. Devuelve (orden | None, motivo_si_None).
    El stop se mide con el ATR del marco que el agente eligio (si no es valido, el del reloj).
    """
    buy = d.get("action") == "buy"
    # El stop solo se mide en marcos de STOP_TFS. Un marco corto (M1/M5) o ausente se lleva a M15 (se acota, no se rechaza).
    tf = d.get("timeframe") if d.get("timeframe") in STOP_TFS else STOP_TFS[0]
    if tf not in ind_por_tf:
        return None, f"sin indicadores de {tf} para medir el stop"
    ind = ind_por_tf[tf]
    sl_mult = min(max(float(d.get("sl_atr_mult") or 1.5), SL_MULT[0]), SL_MULT[1])
    tp_r = min(max(float(d.get("tp_r") or 2.0), TP_R[0]), TP_R[1])
    precio = tick.ask if buy else tick.bid
    dist = ind["atr"] * sl_mult
    # Stop minimo por clase, en ATR de M15: se ensancha (el lote baja, el riesgo no cambia).
    min_atr = STOP_MIN_ATR_M15.get(clase_de(info.name))
    if min_atr and "M15" in ind_por_tf and dist < min_atr * ind_por_tf["M15"]["atr"]:
        dist = min_atr * ind_por_tf["M15"]["atr"]
        sl_mult = dist / ind["atr"]
    # Regla dura de costes: medido el 2026-10-21, dos propuestas de ETH en M1 (stop ~2 puntos, spread
    # 0,6) salieron por stop en la vela siguiente. Un stop que el spread se come no se abre.
    spread = tick.ask - tick.bid
    if spread > MAX_SPREAD_SOBRE_STOP * dist:
        return None, (f"coste: el spread ({spread:.5g}) es el {spread / dist * 100:.0f}% del stop en {tf} "
                      f"(maximo {MAX_SPREAD_SOBRE_STOP * 100:.0f}%)")
    sl = precio - dist if buy else precio + dist
    tp = precio + dist * tp_r if buy else precio - dist * tp_r
    # Riesgo por lote en moneda de la cuenta, calculado por MT5 (sirve para cualquier simbolo)
    perdida_1lote = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL, info.name, 1.0, precio, sl)
    if not perdida_1lote:
        return None, "no se pudo calcular el riesgo por lote"
    # Coste total en R = spread + comision ida y vuelta, ambos entre el riesgo de 1 lote (no depende del tamano).
    coste_spread = spread / dist
    coste_comision = comision_por_lote(mt5, info.name) / abs(perdida_1lote)
    coste_r = coste_spread + coste_comision
    if coste_r > MAX_COSTE_R:
        return None, (f"coste: spread {coste_spread * 100:.1f}% + comision {coste_comision * 100:.1f}% = "
                      f"{coste_r * 100:.1f}% del R en {tf} (maximo {MAX_COSTE_R * 100:.0f}%)")
    riesgo = cuenta.equity * RIESGO_PCT / 100
    paso = info.volume_step
    lotes = max(info.volume_min, min(info.volume_max, int(riesgo / abs(perdida_1lote) / paso) * paso))
    if lotes * abs(perdida_1lote) > riesgo * 2:
        return None, f"lote minimo arriesga {lotes * abs(perdida_1lote):.2f} > 2x objetivo {riesgo:.2f}"
    # Tope de margen por operacion. Con stops cortos (M5) el lote sale enorme: medido el 2026-10-21,
    # BTC en M5 pedia 0,38 lotes = 9.046 EUR de margen sobre 9.865 de equity. Se reduce el lote hasta
    # caber (arriesga menos, nunca mas); si ni el lote minimo cabe, no se abre.
    cap = MARGEN_MAX_ORDEN * cuenta.equity
    margen = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL, info.name, lotes, precio)
    if margen and margen > cap:
        lotes = int(lotes * cap / margen / paso) * paso
        if lotes < info.volume_min:
            return None, f"margen: ni el lote minimo cabe en el {MARGEN_MAX_ORDEN * 100:.0f}% del equity"
    return {"buy": buy, "precio": precio, "sl": sl, "tp": tp, "lotes": lotes, "tf": tf, "digits": info.digits,
            "riesgo_eur": lotes * abs(perdida_1lote), "sl_mult": sl_mult, "tp_r": tp_r, "coste_r": coste_r}, ""


_COMISION_LOTE = {}             # symbol -> (instante, EUR por lote ida y vuelta)


def comision_por_lote(mt5, symbol, dias=30):
    """Comision real del broker por lote, ida y vuelta, en moneda de la cuenta: comisiones de los deals de los
    ultimos `dias` dias del simbolo entre los lotes abiertos. 0 si no hay historia (entonces solo cuenta el spread).
    Se cachea 1 h por simbolo."""
    previo = _COMISION_LOTE.get(symbol)
    if previo and time.time() - previo[0] < 3600:
        return previo[1]
    deals = mt5.history_deals_get(datetime.now() - timedelta(days=dias), datetime.now() + timedelta(days=1), group=symbol) or []
    lotes = sum(x.volume for x in deals if x.entry == 0)
    valor = sum(abs(x.commission + getattr(x, "fee", 0.0)) for x in deals) / lotes if lotes else 0.0
    _COMISION_LOTE[symbol] = (time.time(), valor)
    return valor


def clase_de(symbol):
    return next((s.clase for s in universo.UNIVERSO if s.symbol == symbol), None)


def exposicion_excedida(mt5, symbol, lado):
    """Motivo por el que NO se abre por exposicion de la flota (mismo lado por clase y en total), o None."""
    tipo = 0 if lado == "buy" else 1
    mismo = [p for p in (mt5.positions_get() or []) if p.magic in MAGICS_FLOTA and p.type == tipo]
    if len(mismo) >= MAX_MISMO_LADO_TOTAL:
        return f"exposicion: la flota ya tiene {len(mismo)} posiciones {lado} (maximo {MAX_MISMO_LADO_TOTAL})"
    clase = clase_de(symbol)
    en_clase = sum(1 for p in mismo if clase_de(p.symbol) == clase) if clase else 0
    if en_clase >= MAX_MISMO_LADO_CLASE:
        return f"exposicion: la flota ya tiene {en_clase} posiciones {lado} en {clase} (maximo {MAX_MISMO_LADO_CLASE})"
    return None


MARGEN_LIBRE_MIN = 0.25         # tras abrir, el margen libre no puede bajar del 25% del equity
MARGEN_MAX_ORDEN = 0.30         # una sola operacion no puede inmovilizar mas del 30% del equity en margen
_ULTIMO_REGISTRO = {"t": 0.0}
_ORDEN = {}                     # nombre -> ultima orden calculada en live (para el aviso de Telegram)


def _filling(mt5, info):
    """Modo de ejecucion que admite el simbolo (no todos aceptan IOC: lo vimos al revisar el codigo)."""
    fm = info.filling_mode
    if fm & 2:
        return mt5.ORDER_FILLING_IOC
    if fm & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def pnl_flota(mt5, magics):
    """(realizado hoy, flotante) de los magics dados: lo que llevan perdido o ganado los agentes."""
    hoy = datetime.combine(datetime.now().date(), datetime.min.time())
    realizado = sum(x.profit + x.commission + x.swap
                    for x in (mt5.history_deals_get(hoy, datetime.now() + timedelta(days=1)) or [])
                    if x.magic in magics)
    flotante = sum(p.profit + p.swap for p in (mt5.positions_get() or []) if p.magic in magics)
    return realizado, flotante


def bloqueo_cierre(p, invalidada_por, ahora_broker, min_cierre_min):
    """Motivo por el que un cierre a mano NO se acepta, o None si se acepta. Lo usan agentes y revisor."""
    min_abierta = (ahora_broker - p.time) / 60
    if min_abierta < min_cierre_min:
        return f"cierre bloqueado: lleva {min_abierta:.0f} min abierta (minimo {min_cierre_min})"
    if not invalidada_por or len(str(invalidada_por).strip()) < 10:
        return "cierre bloqueado: falta 'invalidated_by' con la invalidacion concreta (marco + nivel/indicador)"
    # Plan v3 fase 3: el stop y el parcial los gestionan las reglas (gestion_salidas.py); el agente solo cierra si se
    # rompio estructura en H1 o superior. Una rotura en M1-M15 es ruido para una operacion con stop en M15+.
    if not re.search(r"\b(H1|H4|D1)\b", str(invalidada_por)):
        return "cierre bloqueado: la invalidacion debe nombrar un marco H1, H4 o D1"
    return None


def enfriamiento_restante(mt5, magic, minutos, ahora_broker):
    """
    Segundos que faltan para poder volver a abrir tras la ultima salida de este agente. Medido el
    2026-10-22: ETH abria y cerraba a los 15-25 min una y otra vez; cada vuelta paga comision
    (las comisiones se comieron el 38% del beneficio bruto de la primera noche).
    """
    desde = datetime.now() - timedelta(days=1)
    salidas = [x.time for x in (mt5.history_deals_get(desde, datetime.now() + timedelta(days=1)) or [])
               if x.magic == magic and x.entry in (1, 2, 3)]
    if not salidas or minutos <= 0:
        return 0
    return max(0, int(minutos * 60 - (ahora_broker - max(salidas))))     # ambos en hora del broker


def _aviso_trading_apagado(retcode):
    """10027: el terminal tiene el trading automatico desactivado. Se avisa como mucho una vez por hora."""
    if retcode == 10027:
        alertas.avisar("autotrading_off", "MT5 rechaza las órdenes (retcode 10027): el trading automático está "
                       "desactivado en el terminal. Vuelve a activar el botón 'Algo Trading'.", 3600)


def enviar_orden(mt5, req, cuenta):
    """
    order_check ANTES de order_send: valida stops, volumen, tipo de ejecucion y margen sin tocar la
    cuenta. Devuelve (ok, texto, order_ticket). Solo se envia si el chequeo pasa y el margen libre
    resultante no baja de MARGEN_LIBRE_MIN del equity (los EAs de MT5 comparten el mismo margen).
    """
    chk = mt5.order_check(req)
    if chk is None or chk.retcode != 0:
        _aviso_trading_apagado(getattr(chk, "retcode", None))
        return False, f"order_check rechazo la orden: {getattr(chk, 'retcode', None)} {getattr(chk, 'comment', '')}", None
    if "position" not in req and chk.margin_free < MARGEN_LIBRE_MIN * cuenta.equity:
        return False, (f"margen: quedarian {chk.margin_free:.0f} libres "
                       f"(< {MARGEN_LIBRE_MIN * 100:.0f}% del equity {cuenta.equity:.0f})"), None
    r = mt5.order_send(req)
    _aviso_trading_apagado(getattr(r, "retcode", None))
    ok = r is not None and r.retcode == mt5.TRADE_RETCODE_DONE
    return ok, f"retcode {getattr(r, 'retcode', None)} {getattr(r, 'comment', '')}".strip(), getattr(r, "order", None)


def ejecutar(mt5, d, ind, tick, info, pos, cuenta, args):
    """Aplica la decision con los limites duros. Devuelve (ejecutado, texto, ticket)."""
    sym = info.name
    accion = d.get("action")
    if accion == "close":
        if not pos:
            return False, "close sin posicion", None
        p = pos[0]
        # Cierres a mano: medido el 2026-10-22, los de los agentes costaron ~390 EUR frente a dejar actuar
        # al stop y al objetivo. Se exige un minimo de tiempo y una invalidacion concreta.
        bloqueo = bloqueo_cierre(p, d.get("invalidated_by"), tick.time, args.min_cierre_min)
        if bloqueo:
            return False, bloqueo, None
        buy = p.type == 0
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": p.volume, "position": p.ticket,
               "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
               "price": tick.bid if buy else tick.ask, "deviation": 30, "magic": MAGIC,
               "comment": COMMENT, "type_filling": _filling(mt5, info)}
        ok, txt, _ = enviar_orden(mt5, req, cuenta)
        return ok, f"cierre {txt}", p.ticket
    if accion not in ("buy", "sell"):
        return False, "hold", None
    if pos:
        return False, "ya hay posicion: se ignora abrir", None

    # Limites duros, de menor a mayor alcance. Cuentan lo realizado hoy MAS lo flotante.
    real, flot = pnl_flota(mt5, {MAGIC})
    if real + flot <= -args.max_perdida_dia:
        return False, f"parada diaria del agente: {real + flot:.2f} <= -{args.max_perdida_dia}", None
    real, flot = pnl_flota(mt5, MAGICS_FLOTA)
    if real + flot <= -args.max_perdida_flota_dia:
        return False, f"parada diaria de la flota: {real + flot:.2f} <= -{args.max_perdida_flota_dia}", None
    if cuenta.equity < args.equity_minima:
        return False, f"equity {cuenta.equity:.0f} por debajo del minimo {args.equity_minima:.0f}", None
    resta = enfriamiento_restante(mt5, MAGIC, args.enfriamiento_min, tick.time)
    if resta > 0:
        return False, f"enfriamiento: cerro hace menos de {args.enfriamiento_min} min (quedan {resta // 60 + 1} min)", None

    exceso = exposicion_excedida(mt5, sym, accion)
    if exceso:
        return False, exceso, None

    o, motivo = calcular_orden(mt5, d, ind, tick, info, cuenta, args.timeframe)
    if o is None:
        return False, motivo, None
    _ORDEN[args.nombre] = o
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": round(o["lotes"], 2),
           "type": mt5.ORDER_TYPE_BUY if o["buy"] else mt5.ORDER_TYPE_SELL, "price": o["precio"],
           "sl": round(o["sl"], info.digits), "tp": round(o["tp"], info.digits), "deviation": 30,
           "magic": MAGIC, "comment": COMMENT, "type_filling": _filling(mt5, info)}
    ok, txt, ticket = enviar_orden(mt5, req, cuenta)
    dg = o["digits"]                      # decimales del simbolo (EURUSD salia como 1.12 / 1.12 / 1.12 en el log)
    return (ok, f"{accion} {o['lotes']:.2f} lotes @ {o['precio']:.{dg}f} · SL {o['sl']:.{dg}f} · TP {o['tp']:.{dg}f} · "
                f"riesgo ~{o['riesgo_eur']:.0f} EUR · marco {o['tf']} · coste {o['coste_r'] * 100:.1f}% R · {txt}", ticket)


# ── Avisos por Telegram ──────────────────────────────────────────────────────
# Credenciales: las mismas del proyecto (.env en la raiz: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).
# Se lee el fichero a mano (sin dependencias) y el token NUNCA se imprime ni se registra.
_ULTIMO_AVISO = {}      # nombre -> (accion, instante): antirrepeticion en simulacion


def _env_telegram():
    cred = {}
    try:
        for linea in (REPO_ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
            k, _, v = linea.partition("=")
            if k.strip() in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
                cred[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return cred.get("TELEGRAM_BOT_TOKEN"), cred.get("TELEGRAM_CHAT_ID")


def telegram_enviar(texto, html=False):
    """
    (ok, detalle). Con html=True el texto lleva etiquetas <b>/<i>/<code> y TODO lo que venga del
    modelo debe pasar antes por html.escape (en texto plano no hace falta).
    """
    import urllib.request, urllib.error
    token, chat = _env_telegram()
    if not token or not chat:
        return False, "faltan TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID en .env"
    # De 00:00 a 08:00 (hora local) el aviso llega SILENCIOSO: queda en el chat para leerlo despues
    # sin que el movil suene de madrugada.
    silencio = 0 <= datetime.now().hour < 8
    payload = {"chat_id": chat, "text": texto, "disable_notification": silencio}
    if html:
        payload["parse_mode"] = "HTML"
        payload["disable_web_page_preview"] = True
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:         # no incluir e.url: lleva el token
        return False, f"HTTP {e.code} {e.reason}"
    except Exception as e:
        return False, type(e).__name__


def _n(v, dec):
    """Numero con separador de miles y los decimales del simbolo."""
    return f"{v:,.{dec}f}"


def razon_html(texto, maximo=900):
    """El razonamiento del agente (lineas Etiqueta: texto) como HTML de Telegram, con viñetas en negrita."""
    import html as _h
    lineas = []
    for linea in str(texto or "").strip().splitlines():
        linea = linea.strip()
        if not linea:
            continue
        m = re.match(r"^([^:]{2,22}):\s*(.*)$", linea)
        if m:
            lineas.append(f"▪️ <b>{_h.escape(m.group(1))}:</b> {_h.escape(m.group(2))}")
        else:
            lineas.append(f"▪️ {_h.escape(linea)}")
    out = "\n".join(lineas)
    return out if len(out) <= maximo else out[:maximo - 1] + "…"


def mensaje_operacion(args, accion, d, o, live, ticket=None, pos=None):
    """HTML para Telegram: apertura (live), propuesta (simulacion) o cierre decidido por el agente."""
    import html as _h
    razon = razon_html(d.get("reason", ""))
    conf = d.get("confidence")
    conf_txt = f" · confianza {float(conf) * 100:.0f}%" if conf is not None else ""
    hora = datetime.now().strftime("%H:%M")
    pie = f"🕐 {hora}" + (f" · 🎫 #{ticket}" if ticket else "") + " · demo"
    if accion == "close":
        p = pos[0] if pos else None
        lado = ("compra" if p.type == 0 else "venta") if p else ""
        res = f"{p.profit:+,.2f} €" if p else "—"
        icono = "✅" if p and p.profit >= 0 else "🔻"
        return (f"{icono} <b>POSICIÓN CERRADA</b> · <b>{_h.escape(args.symbol)}</b>\n"
                f"<i>{_h.escape(args.nombre)} · cierra su {lado}</i>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"💶 Resultado: <b>{res}</b>\n"
                f"💬 Razonamiento:\n{razon}\n{pie}")
    dec = o["digits"]
    compra = o["buy"]
    entrada, sl, tp = o["precio"], o["sl"], o["tp"]
    pct_sl, pct_tp = abs(entrada - sl) / entrada * 100, abs(tp - entrada) / entrada * 100
    icono, lado = ("🟢", "COMPRA") if compra else ("🔴", "VENTA")
    titulo = f"{lado} ABIERTA" if live else f"PROPUESTA DE {lado}"
    nota = "" if live else "\n🧪 <i>Simulación: no se ha enviado ninguna orden</i>"
    return (f"{icono} <b>{titulo}</b> · <b>{_h.escape(args.symbol)}</b>\n"
            f"<i>{_h.escape(args.nombre)} · marco {o['tf']} (reloj {args.timeframe})</i>{nota}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"▫️ Entrada   <code>{_n(entrada, dec)}</code>\n"
            f"🛑 Stop         <code>{_n(sl, dec)}</code>  (−{pct_sl:.2f}%)\n"
            f"🎯 Objetivo  <code>{_n(tp, dec)}</code>  (+{pct_tp:.2f}%)\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📦 {o['lotes']:.2f} lotes · riesgo ≈ <b>{o['riesgo_eur']:.0f} €</b> · R:R 1:{o['tp_r']:.1f}\n"
            f"🧭 Basado en {o['tf']}{conf_txt}\n"
            f"💬 Razonamiento:\n{razon}\n{pie}")


def avisar(args, accion, d, ejecutado, uso_live, o=None, ticket=None, pos=None):
    """Manda la propuesta/operacion por Telegram si toca. Devuelve True si envio."""
    if not args.telegram or accion not in ("buy", "sell", "close"):
        return False
    if accion in ("buy", "sell") and o is None:
        return False                            # rechazada por las reglas: no hay nada que avisar
    if uso_live and accion == "close":
        return False        # el cierre lo avisa PST_LLM/llm_telegram_bot.py con la tarjeta completa (datos + motivo)
    if not uso_live:
        if accion == "close":
            return False                        # en simulacion no hay posicion que cerrar
        # Simulacion: el agente esta siempre "sin posicion", asi que repetiria la misma compra
        # vela tras vela. Se avisa al cambiar de sentido o pasado el enfriamiento.
        ult = _ULTIMO_AVISO.get(args.nombre)
        if ult and ult[0] == accion and time.time() - ult[1] < args.aviso_cooldown_min * 60:
            return False
    elif not ejecutado:
        return False                            # en live solo se avisa de lo que de verdad se envio
    ok, det = telegram_enviar(mensaje_operacion(args, accion, d, o, uso_live, ticket, pos), html=True)
    if ok:
        _ULTIMO_AVISO[args.nombre] = (accion, time.time())
    else:
        print(f"[llm_trader] aviso Telegram fallido: {det}", flush=True)
    return ok


def recortes(d):
    """Lo que el modelo pidio fuera de rango y el sistema recorto (calcular_orden). None si no hubo recorte."""
    out = []
    for clave, rango in (("sl_atr_mult", SL_MULT), ("tp_r", TP_R)):
        if d.get(clave) is None:
            continue
        try:
            v = float(d[clave])
        except (TypeError, ValueError):
            continue
        if not rango[0] <= v <= rango[1]:
            out.append(f"{clave} {v:g}->{min(max(v, rango[0]), rango[1]):g}")
    return "; ".join(out) or None


# ── Ciclo ────────────────────────────────────────────────────────────────────
_ULTIMO_CIERRE_SESION = {"t": 0.0}


def cerrar_por_sesion(mt5, p, tick, info, cuenta):
    """Cierre a mercado por parada del mercado: no pasa por el modelo ni por el minimo de tiempo."""
    buy = p.type == 0
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": info.name, "volume": p.volume, "position": p.ticket,
           "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
           "price": tick.bid if buy else tick.ask, "deviation": 30, "magic": MAGIC,
           "comment": COMMENT, "type_filling": _filling(mt5, info)}
    ok, txt, _ = enviar_orden(mt5, req, cuenta)
    return ok, txt


def vigilar_sesion(mt5, args, con):
    """
    Cada vuelta del bucle (no solo al cerrar vela, para que un agente en H1 no se pierda la ventana):
    si el mercado va a parar durante un rato largo, cierra lo abierto de este agente.
    Reintenta como mucho cada 60 s para no inundar el log si el broker rechaza la orden.
    """
    tick = mt5.symbol_info_tick(args.symbol)
    info = mt5.symbol_info(args.symbol)
    if tick is None or info is None:
        return
    pos = [p for p in (mt5.positions_get(symbol=args.symbol) or []) if p.magic == MAGIC]
    if not pos:
        return
    sesion = sesiones.estado(mt5, args.symbol, tick.time, args.margen_sesion_min * 60)
    if not sesion["cerrar"] or time.time() - _ULTIMO_CIERRE_SESION["t"] < 60:
        return
    _ULTIMO_CIERRE_SESION["t"] = time.time()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    motivo = f"cierre por sesion: {sesion['motivo']}"
    if not args.live:
        print(f"[{ts}] SIM: cerraria por sesion ({sesion['motivo']})", flush=True)
        return
    ok, txt = cerrar_por_sesion(mt5, pos[0], tick, info, mt5.account_info())
    print(f"[{ts}] {motivo} · {txt}", flush=True)
    log_decision(con, ts=ts, symbol=args.symbol, mode="live", model=args.model, llm_called=0,
                 gate_reason=motivo, action="close", executed=int(ok), exec_result=txt, reason=motivo, state_json="{}")


def ciclo(mt5, args, con):
    snap = snapshot(mt5, args.symbol, args.timeframe, args.timeframes_analisis)
    if snap is None:
        print("[llm_trader] sin velas suficientes", flush=True)
        return
    estado, ind, tick, info, pos = snap          # ind = {marco: indicadores}
    if estado.get("position"):                    # que el agente sepa si puede cerrar ya o no
        estado["position"]["close_locked_for_min"] = max(0, int(args.min_cierre_min - estado["position"]["minutes_held"]))
    modo = "live" if args.live else "sim"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Parada larga del mercado a la vista: no se llama al modelo ni se abre nada (lo abierto lo cierra vigilar_sesion).
    sesion = sesiones.estado(mt5, args.symbol, tick.time, args.margen_sesion_min * 60)
    if sesion["cerrar"]:
        print(f"[{ts}] sin llamada: parada de sesion ({sesion['motivo']})", flush=True)
        log_decision(con, ts=ts, symbol=args.symbol, mode=modo, model=args.model, llm_called=0,
                     gate_reason=f"parada de sesion: {sesion['motivo']}", action="-", state_json=json.dumps(estado))
        return
    llamar, motivo = puerta(ind[args.timeframe], bool(pos), args.sin_filtro)
    # Mercado cerrado: si la cotizacion no cambia en 20 min, no se llama al modelo (ni de noche ni en fin de semana).
    if not mercado_vivo(tick):
        llamar, motivo = False, "mercado sin cotizaciones nuevas (20 min): no se llama"
    # Calendario FTMO: sin posicion abierta, no se consulta al modelo ni se abre nada en la ventana
    # de una publicacion restringida. Con posicion, el agente sigue pudiendo cerrar.
    if not pos:
        bloq_cal, motivo_cal = calendario.bloqueo_entrada(args.symbol)
        if bloq_cal:
            llamar, motivo = False, motivo_cal
    q = None
    if llamar:
        ok, why, q = cuota_ok()
        if not ok:
            llamar, motivo = False, f"freno de cuota: {why}"
    # Cuota en el momento de la decision: el delta entre la primera y la ultima fila es el
    # coste REAL en el plan del usuario, que es lo que importa (los tokens son solo proxy).
    extra = {"quota_5h": (q or {}).get("cinco_h"), "quota_7d": (q or {}).get("semanal"),
             "timeframe": args.timeframe}
    if llamar:
        n_hoy = con.execute("SELECT COUNT(*) FROM decisions WHERE llm_called=1 AND ts>=?",
                            (datetime.now().strftime("%Y-%m-%d"),)).fetchone()[0]
        tope = _tope_diario()
        if tope and n_hoy >= tope:
            llamar, motivo = False, f"tope de {tope} llamadas/dia"
    if not llamar:
        print(f"[{ts}] sin llamada: {motivo}", flush=True)
        log_decision(con, ts=ts, symbol=args.symbol, mode=modo, model=args.model, llm_called=0,
                     gate_reason=motivo, action="-", state_json=json.dumps(estado), **extra)
        return

    try:
        d, uso = preguntar(estado, args)
    except Exception as e:
        print(f"[{ts}] error del agente: {e}", flush=True)
        log_decision(con, ts=ts, symbol=args.symbol, mode=modo, model=args.model, llm_called=1,
                     gate_reason=motivo, action="error", reason=str(e)[:300], state_json=json.dumps(estado), **extra)
        return

    accion = d.get("action", "hold")
    ejecutado, texto, ticket, o = False, "simulacion: no se envia", None, None
    if args.live:
        ejecutado, texto, ticket = ejecutar(mt5, d, ind, tick, info, pos, mt5.account_info(), args)
        o = _ORDEN.pop(args.nombre, None)
        if accion in ("buy", "sell") and not ejecutado and texto not in ("ya hay posicion: se ignora abrir",):
            print(f"[{ts}] orden NO enviada: {texto}", flush=True)
    elif accion in ("buy", "sell") and not pos:
        # Simulacion: se guarda el nivel exacto que habria usado, para replicarlo a mano o juzgarlo.
        o, why = calcular_orden(mt5, d, ind, tick, info, mt5.account_info(), args.timeframe)
        texto = (f"SIM {accion.upper()} {o['lotes']:.2f} lotes @ {o['precio']:.{o['digits']}f} · SL {o['sl']:.{o['digits']}f} · "
                 f"TP {o['tp']:.{o['digits']}f} · riesgo ~{o['riesgo_eur']:.0f} EUR · marco {o['tf']} · coste {o['coste_r'] * 100:.1f}% R") if o else f"SIM {accion}: rechazada ({why})"
    elif accion == "close":
        texto = "SIM: cerraria la posicion"
    rec = recortes(d)
    print(f"[{ts}] {accion.upper()} | {d.get('reason', '')} | tokens in={uso['tokens_in']} out={uso['tokens_out']} "
          f"cache={uso['tokens_cache_read']} coste={uso['cost_usd']} | {texto}"
          + (f" | RECORTE {rec}" if rec else ""), flush=True)
    log_decision(con, ts=ts, symbol=args.symbol, mode=modo, model=args.model, llm_called=1, gate_reason=motivo,
                 action=accion, sl_mult=d.get("sl_atr_mult"), tp_r=d.get("tp_r"), confidence=d.get("confidence"),
                 reason=d.get("reason"), executed=int(ejecutado), exec_result=texto, ticket=ticket,
                 state_json=json.dumps(estado), horizonte=d.get("timeframe"), recorte=rec, **uso, **extra)
    avisar(args, accion, d, ejecutado, args.live, o, ticket, pos)


# ── Seguimiento de resultados ────────────────────────────────────────────────
RAZON_DEAL = {0: "manual", 1: "movil", 2: "web", 3: "agente", 4: "STOP LOSS", 5: "TAKE PROFIT", 6: "stop-out"}


def _habria_pasado(mt5, t, s):
    """
    Si el agente (o el revisor) NO hubiera cerrado a mano, que habria pasado: repite el precio desde
    el cierre, en velas de 1 minuto y hasta 24 h, hasta tocar el stop o el objetivo. Si en una misma
    vela toca ambos se asume el stop primero (lectura pesimista). Devuelve (resultado, neto_hipotetico).
    """
    if t["sl"] is None or t["tp"] is None:
        return None, None
    buy = t["side"] == "buy"
    ahora = int(mt5.symbol_info_tick(t["symbol"]).time)
    barras = mt5.copy_rates_range(t["symbol"], mt5.TIMEFRAME_M1, int(s.time), min(int(s.time) + 86400, ahora))
    tipo = mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL
    comision = sum(d.commission for d in (mt5.history_deals_get(position=t["ticket"]) or []))
    for b in (barras if barras is not None else []):
        toca_sl = b["low"] <= t["sl"] if buy else b["high"] >= t["sl"]
        toca_tp = b["high"] >= t["tp"] if buy else b["low"] <= t["tp"]
        if toca_sl or toca_tp:
            precio, res = (t["sl"], "habria dado STOP") if toca_sl else (t["tp"], "habria dado OBJETIVO")
            return res, (mt5.order_calc_profit(tipo, t["symbol"], t["volumen"], t["entrada"], precio) or 0) + comision
    if barras is not None and len(barras):
        ult = float(barras[-1]["close"])
        return "seguiria abierta", (mt5.order_calc_profit(tipo, t["symbol"], t["volumen"], t["entrada"], ult) or 0) + comision
    return None, None


def calcular_resultados(mt5, db_file):
    """Operaciones de UN agente ya ejecutadas en MT5 (por ticket), con su resultado real."""
    import re
    if not Path(db_file).is_file():
        return []
    c = sqlite3.connect(db_file)
    filas = c.execute("SELECT ts, symbol, action, ticket, horizonte, confidence, reason, exec_result FROM decisions "
                      "WHERE executed=1 AND action IN ('buy','sell') AND ticket IS NOT NULL ORDER BY id").fetchall()
    c.close()
    out = []
    for ts, sym, accion, ticket, hor, conf, razon, res in filas:
        deals = mt5.history_deals_get(position=ticket) or []
        ent = [d for d in deals if d.entry == 0]
        if not ent:
            continue
        sal = [d for d in deals if d.entry in (1, 2, 3)]
        m = re.search(r"@ ([\d.]+) · SL ([\d.]+) · TP ([\d.]+) · riesgo ~(\d+)", res or "")
        e = ent[0]
        # Stop y objetivo REALES de la orden de apertura en MT5. Antes se leian del texto del log, que
        # hasta el 2026-10-22 redondeaba a 2 decimales (EURUSD: SL 1.12 / TP 1.12) y falseaba el analisis.
        sl = tp = None
        for o_ in (mt5.history_orders_get(position=ticket) or []):
            if o_.sl or o_.tp:
                sl, tp = o_.sl or None, o_.tp or None
                break
        if sl is None and m:
            sl, tp = float(m.group(2)), float(m.group(3))
        t = {"ts": ts, "symbol": sym, "side": accion, "ticket": ticket, "horizonte": hor or "?", "conf": conf,
             "entrada": e.price, "volumen": e.volume, "sl": sl, "tp": tp,
             "riesgo": float(m.group(4)) if m else None,
             "abierta": bool(mt5.positions_get(ticket=ticket)),
             "neto": sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0.0) for d in deals)}
        pos = mt5.positions_get(ticket=ticket)
        if t["abierta"]:
            t["flotante"] = pos[0].profit
        if sal and not t["abierta"]:
            s = sal[-1]
            t["salida"] = "revisor" if "REVISOR" in (s.comment or "").upper() else RAZON_DEAL.get(s.reason, str(s.reason))
            t["minutos"] = int((s.time - e.time) / 60)
            if t["salida"] in ("agente", "revisor"):
                t["que_habria"], t["neto_habria"] = _habria_pasado(mt5, t, s)
        out.append(t)
    return out


def imprimir_resultados(trades, titulo, detalle=True):
    cerradas = [t for t in trades if not t["abierta"] and "salida" in t]
    abiertas = [t for t in trades if t["abierta"]]
    print(f"\n=== {titulo}")
    if not trades:
        print("  sin operaciones ejecutadas todavia")
        return
    gan = [t["neto"] for t in cerradas if t["neto"] > 0]
    per = [t["neto"] for t in cerradas if t["neto"] <= 0]
    n = len(cerradas)
    pf = (sum(gan) / -sum(per)) if per and sum(per) < 0 else None
    print(f"  Cerradas {n} · ganadas {len(gan)} ({(len(gan) / n * 100 if n else 0):.0f}%) · neto {sum(t['neto'] for t in cerradas):+.2f} EUR"
          + (f" · factor de beneficio {pf:.2f}" if pf else "")
          + (f" · medio ganada {sum(gan) / len(gan):+.1f} / perdida {sum(per) / len(per):+.1f}" if gan and per else ""))
    if abiertas:
        print(f"  Abiertas {len(abiertas)} · flotante {sum(t.get('flotante', 0) for t in abiertas):+.2f} EUR")
    if detalle and cerradas:
        for clave, nombre in (("salida", "por salida"), ("horizonte", "por marco en que se apoyo")):
            grupos = {}
            for t in cerradas:
                grupos.setdefault(t[clave], []).append(t["neto"])
            print(f"  {nombre}: " + " · ".join(f"{k} {len(v)} ops {sum(v):+.0f}€" for k, v in sorted(grupos.items())))
    # Evaluacion de los cierres a mano: el dato clave para saber si cortar antes ayuda o no
    manuales = [t for t in cerradas if t.get("neto_habria") is not None]
    if manuales:
        real = sum(t["neto"] for t in manuales)
        hip = sum(t["neto_habria"] for t in manuales)
        print(f"  Cierres a mano (agente/revisor): {len(manuales)} · real {real:+.1f}€ · si NO hubieran cerrado {hip:+.1f}€ "
              f"-> cerrar antes {'AYUDO' if real >= hip else 'COSTO'} {abs(real - hip):.1f}€")
        if detalle:
            for t in manuales:
                print(f"     {t['ts'][5:16]} {t['symbol']:<10} {t['side']:<4} real {t['neto']:+7.1f}€ ({t['minutos']} min)  "
                      f"{t['que_habria']} -> {t['neto_habria']:+.1f}€")


def ultimas(con, n, solo_ops):
    """Ultimas decisiones, mas reciente primero. Con solo_ops, solo buy/sell/close."""
    filtro = "AND action IN ('buy','sell','close')" if solo_ops else "AND llm_called=1"
    filas = con.execute(f"SELECT ts, symbol, timeframe, action, exec_result, confidence, reason "
                        f"FROM decisions WHERE 1=1 {filtro} ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    if not filas:
        print("Sin decisiones" + (" de operar todavia (solo ha dicho hold)." if solo_ops else "."))
    for ts, sym, tf, accion, res, conf, razon in filas:
        print(f"{ts} {sym} {tf or ''} {(accion or '').upper()}" + (f" (confianza {conf})" if conf is not None else ""))
        if accion in ("buy", "sell", "close") and res:
            print(f"   {res}")
        print(f"   {razon}")


def informe(con):
    f = con.execute("""SELECT COUNT(*), SUM(llm_called), SUM(tokens_in), SUM(tokens_out), SUM(tokens_cache_read),
                       SUM(tokens_cache_write), SUM(cost_usd), AVG(CASE WHEN llm_called=1 THEN duration_s END)
                       FROM decisions""").fetchone()
    n, llam, ti, to, cr, cw, coste, dur = (x or 0 for x in f)
    print(f"Ciclos: {n} · llamadas al agente: {llam} · saltadas por el filtro/freno: {n - llam}")
    print(f"Tokens: entrada {ti:,} · salida {to:,} · caché leída {cr:,} · caché escrita {cw:,}")
    if llam:
        print(f"Por llamada: ~{(ti + to + cr + cw) / llam:,.0f} tokens totales · {dur:.1f}s")
    print(f"Coste reportado por el CLI: {coste:.4f} USD (referencia: en suscripción lo que cuenta es la cuota)")
    print("Acciones:", dict(con.execute("SELECT action, COUNT(*) FROM decisions WHERE llm_called=1 GROUP BY action").fetchall()))
    print("Ejecutadas en MT5:", con.execute("SELECT COUNT(*) FROM decisions WHERE executed=1").fetchone()[0])
    # Coste real en cuota: primera y ultima fila con dato. Si la ventana de 5h se reinicio entre
    # medias, el delta de la de 5h no vale (sale negativo): mirar entonces la semanal.
    filas = con.execute("SELECT ts, quota_5h, quota_7d FROM decisions WHERE quota_5h IS NOT NULL "
                        "ORDER BY id").fetchall()
    if len(filas) >= 2:
        (t0, a0, w0), (t1, a1, w1) = filas[0], filas[-1]
        print(f"Cuota {t0} -> {t1}: ventana 5h {a0}% -> {a1}% ({a1 - a0:+.1f} pts) · semanal {w0}% -> {w1}% ({w1 - w0:+.1f} pts)")
    else:
        print("Cuota: aún sin dos lecturas (hace falta que el mod de habblaud tenga el dato fresco)")
    for tf, n, ll, tok in con.execute("SELECT timeframe, COUNT(*), SUM(llm_called), "
                                      "SUM(COALESCE(tokens_in,0)+COALESCE(tokens_out,0)+COALESCE(tokens_cache_read,0)+COALESCE(tokens_cache_write,0)) "
                                      "FROM decisions GROUP BY timeframe"):
        print(f"  TF {tf or 'H1'}: {n} ciclos, {ll or 0} llamadas, {tok or 0:,} tokens")


def main():
    ap = argparse.ArgumentParser(description="Agente LLM operando un simbolo (experimento, DEMO)")
    ap.add_argument("--symbol", default="BTCUSD")
    ap.add_argument("--model", default="haiku", help="haiku (defecto, barato) | sonnet")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--timeframe", default="H1", choices=sorted(TF_SEGUNDOS, key=TF_SEGUNDOS.get),
                    help="CADA CUANTO se llama al agente (cierre de vela de este marco). No es el marco "
                         "en el que opera: eso lo elige el agente entre --timeframes-analisis")
    ap.add_argument("--timeframes-analisis", dest="timeframes_analisis", nargs="+", default=TF_ANALISIS_DEFECTO,
                    choices=sorted(TF_SEGUNDOS, key=TF_SEGUNDOS.get), help="marcos que ve el agente en cada llamada")
    ap.add_argument("--enabled", type=lambda s: s.lower() != "false", default=True)
    ap.add_argument("--nombre", default=NOMBRE_DEFECTO,
                    help="nombre del analista = sufijo de sus ficheros (config, BBDD, STOP)")
    ap.add_argument("--sala", default=NOMBRE_DEFECTO,
                    help="sala en Habblaud: los analistas con la misma sala salen juntos, como un equipo")
    ap.add_argument("--magic", type=int, default=109001, help="magic number de sus operaciones (uno por agente)")
    ap.add_argument("--sesion-continua", dest="sesion_continua", action="store_true",
                    help="un unico proceso claude abierto entre decisiones (visible fijo en Habblaud)")
    ap.add_argument("--reiniciar-cada", dest="reiniciar_cada", type=int, default=12,
                    help="en sesion continua, reiniciar tras N decisiones (0 = nunca); acota el historial")
    ap.add_argument("--live", action="store_true", help="enviar ordenes reales (solo cuenta demo)")
    ap.add_argument("--once", action="store_true", help="una decision ahora y salir")
    ap.add_argument("--sin-filtro", dest="sin_filtro", action="store_true", help="llamar al agente en cada vela")
    ap.add_argument("--max-llamadas-dia", dest="max_llamadas_dia", type=int, default=30)
    ap.add_argument("--max-perdida-dia", dest="max_perdida_dia", type=float, default=1000000.0,
                    help="EUR; parada diaria de ESTE agente (realizado + flotante)")
    ap.add_argument("--max-perdida-flota-dia", dest="max_perdida_flota_dia", type=float, default=1000000.0,
                    help="EUR; parada diaria conjunta de todos los agentes LLM")
    ap.add_argument("--equity-minima", dest="equity_minima", type=float, default=0.0,
                    help="EUR; no se abren operaciones si el equity de la cuenta baja de esto")
    ap.add_argument("--telegram", type=lambda s: s.lower() != "false", default=True,
                    help="avisar por Telegram cuando proponga/ejecute comprar o vender (defecto true)")
    ap.add_argument("--aviso-cooldown-min", dest="aviso_cooldown_min", type=int, default=15,
                    help="en simulacion, no repetir el mismo aviso (misma direccion) antes de N minutos")
    ap.add_argument("--probar-telegram", dest="probar_telegram", action="store_true",
                    help="enviar un mensaje de prueba y salir")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--resultados", action="store_true",
                    help="resultado real (MT5) de las operaciones de este agente y que habria pasado sin los cierres a mano")
    ap.add_argument("--min-cierre-min", dest="min_cierre_min", type=int, default=90,
                    help="minutos minimos que una operacion debe llevar abierta antes de aceptar un cierre a mano")
    ap.add_argument("--margen-sesion-min", dest="margen_sesion_min", type=int, default=30,
                    help="minutos antes de una parada larga del mercado (fin de semana) en los que se cierra lo abierto "
                         "y no se abre nada")
    ap.add_argument("--enfriamiento-min", dest="enfriamiento_min", type=int, default=30,
                    help="minutos sin abrir en este simbolo tras cerrar una posicion (evita abrir y cerrar sin parar)")
    ap.add_argument("--ultimas", type=int, metavar="N", help="listar las ultimas N decisiones")
    ap.add_argument("--solo-operaciones", dest="solo_ops", action="store_true",
                    help="con --ultimas: solo las que proponen comprar, vender o cerrar")
    args = ap.parse_args()
    configurar_agente(args.nombre, args.magic)

    con = db()
    if args.report:
        informe(con)
        return 0
    if args.ultimas:
        ultimas(con, args.ultimas, args.solo_ops)
        return 0
    if args.resultados:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            print(f"MT5 no disponible: {mt5.last_error()}", file=sys.stderr)
            return 1
        try:
            imprimir_resultados(calcular_resultados(mt5, DB_FILE), f"{args.nombre} · resultados reales")
        finally:
            mt5.shutdown()
        return 0
    if args.probar_telegram:
        ok, det = telegram_enviar(f"🤖 Prueba de {args.nombre}: si lees esto, los avisos de compra/venta llegarán aquí.")
        print(f"Telegram: {'enviado' if ok else 'FALLO'} ({det})")
        return 0 if ok else 1

    import MetaTrader5 as mt5
    if not mt5.initialize():
        print(f"MT5 no disponible: {mt5.last_error()}", file=sys.stderr)
        return 1
    try:
        cuenta = mt5.account_info()
        es_demo = cuenta.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
        aplicar_config(args)
        if args.live and not es_demo:
            print("live solo se permite en cuenta DEMO. Abortado.", file=sys.stderr)
            return 1
        if not mt5.symbol_select(args.symbol, True):
            print(f"Simbolo {args.symbol} no disponible", file=sys.stderr)
            return 1
        print(f"[llm_trader] {args.symbol} {args.timeframe} · modo {'LIVE (demo)' if args.live else 'SIMULACION'} · "
              f"modelo {args.model}/{args.effort} · filtro {'NO' if args.sin_filtro else 'si'} · "
              f"tope {_tope_diario() or 'sin'} llamadas/dia (global) · cuenta {cuenta.login}", flush=True)
        prompt_actual(args.symbol)
        if args.once:
            ciclo(mt5, args, con)
            return 0
        print(f"[llm_trader] agente '{args.nombre}' magic {MAGIC} · config en caliente: {CONFIG_FILE.name} "
              f"(se relee cada vuelta) · parar: crear {STOP_FILE.name} (o llm_trader.STOP para todos)", flush=True)

        ultima, (simbolo_ant, tf_ant) = None, (args.symbol, args.timeframe)
        while not (STOP_FILE.exists() or STOP_GLOBAL.exists()):
            mantener_despierto()
            for c in aplicar_config(args):
                print(f"[llm_trader] config: {c}", flush=True)
            if args.live and not es_demo:
                args.live = False                  # nunca live fuera de demo, ni por fichero
            if (args.symbol, args.timeframe) != (simbolo_ant, tf_ant):
                simbolo_ant, tf_ant, ultima = args.symbol, args.timeframe, None   # reiniciar el reloj de velas
                mt5.symbol_select(args.symbol, True)
            prompt_actual(args.symbol)                 # criterio del simbolo (BBDD): cambio en caliente
            if time.time() - _ULTIMO_REGISTRO["t"] > 300:      # operaciones cerradas -> llm_operaciones (solo las nuevas)
                _ULTIMO_REGISTRO["t"] = time.time()
                operaciones.registrar_agente(mt5, DB_FILE)
            if args.sesion_continua and args.enabled:
                try:
                    _SESION.asegurar(args.model, args.effort, args.sala)
                except Exception as e:
                    print(f"[llm_trader] no se pudo abrir la sesion: {e}", flush=True)
            elif not args.sesion_continua:
                _SESION.cerrar()
            try:                                       # en cada vuelta, tambien con el agente parado
                vigilar_sesion(mt5, args, con)
            except Exception as e:
                print(f"[llm_trader] vigilancia de sesion: {type(e).__name__}: {e}", flush=True)
            if args.enabled:
                r = mt5.copy_rates_from_pos(args.symbol, getattr(mt5, f"TIMEFRAME_{args.timeframe}"), 0, 1)
                if r is not None and len(r):
                    formandose = int(r[0]["time"])
                    if ultima is None:
                        ultima = formandose        # arrancar no cuenta como vela nueva
                    elif formandose != ultima:
                        ultima = formandose
                        ciclo(mt5, args, con)
            time.sleep(5 if TF_SEGUNDOS[args.timeframe] <= 60 else 10 if TF_SEGUNDOS[args.timeframe] <= 300 else 20)
        print("[llm_trader] fichero STOP detectado: parado.", flush=True)
    finally:
        _SESION.cerrar()
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
