import React, { useState, useEffect, useMemo, useRef } from 'react'



import axios from 'axios'



import {
  TrendingUp, TrendingDown, Wallet, Activity, BarChart3, Settings, ShieldCheck,
  ArrowUpRight, RefreshCw, Power, Target, Clock, ShieldAlert, BookMarked,
  Image as ImageIcon, FileText, ChevronRight, Save, X, MoreVertical, LayoutDashboard,
  LayoutGrid, History, ShoppingCart, Zap, Trash2, ChevronUp, ChevronDown, Cpu,
  Terminal, Info, Lock, Shield, Volume2, VolumeX, StretchHorizontal, FlaskConical
} from 'lucide-react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell, PieChart, Pie
} from 'recharts'
import { motion, AnimatePresence } from 'framer-motion'



import { TradingChart } from './components/TradingChart'
import { MarketHeatmap } from './components/MarketHeatmap'
import { MultiChartWorkspace } from './components/MultiChartWorkspace'
import { StrategyLab } from './components/StrategyLab'
import { PerformanceDashboard } from './components/PerformanceDashboard'
import { NavItem } from './components/ui/NavItem'
import { Sparkline } from './components/ui/Sparkline'
import { Toast } from './components/ui/Toast'
import { StatBox } from './components/ui/StatBox'
import { LiveConsole } from './components/ui/LiveConsole'
import { StatCard } from './components/ui/StatCard'
import { EquityCurve } from './components/ui/EquityCurve'
import { PerformanceCalendar } from './components/ui/PerformanceCalendar'
import { TradeRiskVisualizer } from './components/ui/TradeRiskVisualizer'
import { TradeTerminalTable } from './components/ui/TradeTerminalTable'







const API_BASE = import.meta.env.DEV ? "http://127.0.0.1:8000/api" : "/api";

// Interceptores Globales (FASE 43)
axios.interceptors.response.use(
  response => response,
  error => {
    if (error.response?.status === 401) {
      localStorage.removeItem('pst_token');
      window.location.reload(); // Forzar recarga completa para limpiar estado
    }
    return Promise.reject(error);
  }
);

const api = {
  get: (url, cfg) => axios.get(url, { ...(cfg || {}), headers: { 'X-PST-Token': localStorage.getItem('pst_token') || '', ...((cfg || {}).headers || {}) } }),
  post: (url, data, cfg) => axios.post(url, data, { ...(cfg || {}), headers: { 'X-PST-Token': localStorage.getItem('pst_token') || '', ...((cfg || {}).headers || {}) } }),
  put: (url, data, cfg) => axios.put(url, data, { ...(cfg || {}), headers: { 'X-PST-Token': localStorage.getItem('pst_token') || '', ...((cfg || {}).headers || {}) } }),
  delete: (url, cfg) => axios.delete(url, { ...(cfg || {}), headers: { 'X-PST-Token': localStorage.getItem('pst_token') || '', ...((cfg || {}).headers || {}) } }),
};










// ─────────────────────────────────────────────────────────────────────────────
// Fase 3 · Matrix Editor: modal de configuración por símbolo
// ─────────────────────────────────────────────────────────────────────────────
const MODAL_STRATS = ["PST-RangeBreaker", "PST-PrecisionScalping"];
const MODAL_STRAT_LABELS = { "PST-RangeBreaker": "Range Breaker", "PST-PrecisionScalping": "Precision Scalp" };
// Knobs de filter_profile expuestos (solo PrecisionScalping)
const FILTER_KNOBS = [
  { k: "entry_threshold", label: "Entry Score", hint: "Score mínimo para entrar (régimen normal)" },
  { k: "entry_threshold_volatile", label: "Entry Score · Volátil", hint: "Score mínimo en régimen VOLÁTIL" },
  { k: "adx_clean", label: "ADX limpio", hint: "ADX ≥ y Chop ≤ límite → tendencia limpia (+6)" },
  { k: "chop_clean", label: "Chop limpio", hint: "Chop ≤ este valor → tendencia limpia" },
  { k: "adx_ok", label: "ADX ok", hint: "ADX ≥ este valor → aceptable (neutro)" },
  { k: "chop_ok", label: "Chop ok", hint: "Chop ≤ este valor → aceptable (neutro)" },
  { k: "adx_extreme", label: "ADX extremo", hint: "ADX < y Chop > → lateral extrema (veto)" },
  { k: "chop_extreme", label: "Chop extremo", hint: "Chop > y ADX < → lateral extrema (veto)" },
  { k: "rsi_strong", label: "RSI fuerte", hint: "RSI M5 momentum fuerte (short usa 100-x)" },
  { k: "rsi_ok", label: "RSI ok", hint: "RSI M5 momentum aceptable" },
];
const FILTER_KNOB_KEYS = FILTER_KNOBS.map(f => f.k).concat(["noise_mode", "vwap_exit", "entry_tf", "validation_tf"]);
const TF_OPTIONS = [{ value: 'm1', label: 'M1' }, { value: 'm2', label: 'M2' }, { value: 'm3', label: 'M3' }, { value: 'm5', label: 'M5' }];

function _parseFilter(raw) {
  let knobs = {}, advanced = {};
  try {
    const o = raw ? JSON.parse(raw) : {};
    for (const [k, v] of Object.entries(o)) {
      if (FILTER_KNOB_KEYS.includes(k)) knobs[k] = v; else advanced[k] = v;
    }
  } catch { /* ignora perfil corrupto */ }
  return { knobs, advancedStr: Object.keys(advanced).length ? JSON.stringify(advanced) : "" };
}

function _buildDraft(fm) {
  const d = {};
  for (const strat of MODAL_STRATS) {
    const s = fm[strat] || {};
    d[strat] = {
      is_active: !!s.is_active,
      risk_mode: s.risk_mode || "MONEY",
      risk_value: s.risk_value ?? "",
      sl_mult: s.sl_mult ?? "",
      tp_mult: s.tp_mult ?? "",
      score_threshold: s.score_threshold ?? "",
      use_breakeven: !!s.use_breakeven,
      be_mult: s.be_mult ?? "",
      use_trailing: !!s.use_trailing,
      ts_mult: s.ts_mult ?? "",
      min_rr: s.min_rr ?? "",
    };
    if (strat === "PST-PrecisionScalping") {
      const f = _parseFilter(s.filter_profile);
      d[strat].__noise = f.knobs.noise_mode || "on";
      d[strat].__vwapExit = f.knobs.vwap_exit || "on";
      d[strat].__entryTf = f.knobs.entry_tf || "m1";
      d[strat].__validationTf = f.knobs.validation_tf || "m5";
      d[strat].__knobs = {};
      for (const kk of FILTER_KNOBS) d[strat].__knobs[kk.k] = f.knobs[kk.k] ?? "";
      d[strat].__advanced = f.advancedStr;
    }
  }
  return d;
}

// Campos reutilizables (nivel de módulo → identidad estable, sin pérdida de foco)
const Num = ({ label, value, onChange, hint, ph, color = "text-white", disabled }) => (
  <label className="flex flex-col gap-1" title={hint || ""}>
    <span className="text-[8px] font-black text-zinc-500 uppercase tracking-widest">{label}</span>
    <input type="number" step="0.1" value={value} placeholder={ph || ""} disabled={disabled}
      onChange={e => onChange(e.target.value)}
      className={`bg-black/40 border border-white/10 rounded-lg px-2.5 py-1.5 text-[12px] font-black ${color} outline-none focus:border-indigo-500/50 disabled:opacity-40 disabled:cursor-not-allowed`} />
  </label>
);
const Toggle = ({ label, on, onClick, hint }) => (
  <button onClick={onClick} title={hint || ""}
    className={`flex items-center justify-between gap-2 px-3 py-2 rounded-xl border transition-all ${on ? 'bg-indigo-500/10 border-indigo-500/40' : 'bg-black/30 border-white/10'}`}>
    <span className={`text-[9px] font-black uppercase tracking-widest ${on ? 'text-indigo-300' : 'text-zinc-500'}`}>{label}</span>
    <div className={`w-8 h-4 rounded-full relative transition-all ${on ? 'bg-indigo-600' : 'bg-zinc-800'}`}>
      <div className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-all ${on ? 'right-0.5' : 'left-0.5'}`} />
    </div>
  </button>
);
const Seg = ({ options, value, onChange }) => (
  <div className="flex items-center bg-black/40 p-1 rounded-xl border border-white/10 gap-1">
    {options.map(o => (
      <button key={o.value} onClick={() => onChange(o.value)}
        className={`px-3 py-1.5 rounded-lg text-[9px] font-black uppercase tracking-widest transition-all ${value === o.value ? 'bg-indigo-600 text-white' : 'text-zinc-500 hover:text-zinc-300'}`}>
        {o.label}
      </button>
    ))}
  </div>
);

function ConfigModal({ symbol, symData, onClose, onSaved, addToast }) {
  const [tab, setTab] = useState("PST-PrecisionScalping");
  const [draft, setDraft] = useState(() => _buildDraft(symData.factors_map || {}));
  const [saving, setSaving] = useState(false);

  const setField = (strat, field, val) => setDraft(p => ({ ...p, [strat]: { ...p[strat], [field]: val } }));
  const setKnob = (strat, k, val) => setDraft(p => ({ ...p, [strat]: { ...p[strat], __knobs: { ...p[strat].__knobs, [k]: val } } }));

  const buildFilterProfile = (sd) => {
    let obj = {};
    if (sd.__advanced && sd.__advanced.trim()) {
      obj = { ...JSON.parse(sd.__advanced) }; // lanza si es inválido → capturado en save()
    }
    obj.noise_mode = sd.__noise;
    obj.vwap_exit = sd.__vwapExit;
    obj.entry_tf = sd.__entryTf;
    obj.validation_tf = sd.__validationTf;
    for (const [k, v] of Object.entries(sd.__knobs || {})) {
      if (v !== "" && v !== null && v !== undefined) obj[k] = Number(v);
    }
    return JSON.stringify(obj);
  };

  const numOrNull = (v) => (v === "" || v === null || v === undefined ? null : Number(v));

  const save = async () => {
    setSaving(true);
    try {
      for (const strat of MODAL_STRATS) {
        const sd = draft[strat];
        const fields = {
          is_active: sd.is_active,
          risk_mode: sd.risk_mode,
          risk_value: numOrNull(sd.risk_value),
          sl_mult: numOrNull(sd.sl_mult),
          tp_mult: numOrNull(sd.tp_mult),
          score_threshold: numOrNull(sd.score_threshold),
          use_breakeven: sd.use_breakeven,
          be_mult: numOrNull(sd.be_mult),
          use_trailing: sd.use_trailing,
          ts_mult: numOrNull(sd.ts_mult),
          min_rr: numOrNull(sd.min_rr),
        };
        if (strat === "PST-PrecisionScalping") fields.filter_profile = buildFilterProfile(sd);
        await api.post(`${API_BASE}/config/strategy`, { symbol, strategy: strat, ...fields });
      }
      addToast(`${symbol} configurado`, "success");
      onSaved();
      onClose();
    } catch (e) {
      const msg = e?.response?.data?.detail || (e instanceof SyntaxError ? "JSON avanzado inválido" : e.message) || "Error al guardar";
      addToast(msg, "error");
    } finally {
      setSaving(false);
    }
  };

  const sd = draft[tab];
  const isScalp = tab === "PST-PrecisionScalping";

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      className="fixed inset-0 z-[100] flex items-center justify-center p-6 bg-[#020202]/90 backdrop-blur-md" onClick={onClose}>
      <motion.div initial={{ scale: 0.9, opacity: 0, y: 30 }} animate={{ scale: 1, opacity: 1, y: 0 }} exit={{ scale: 0.9, opacity: 0, y: 30 }}
        onClick={e => e.stopPropagation()}
        className="w-full max-w-4xl bg-[#050505] border border-white/10 rounded-[2.5rem] shadow-[0_0_100px_rgba(0,0,0,0.8)] overflow-hidden flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="px-8 py-6 border-b border-white/5 flex items-center justify-between bg-white/[0.02]">
          <div className="flex items-center gap-3">
            <div className="w-11 h-11 bg-indigo-500/10 rounded-2xl flex items-center justify-center border border-indigo-500/20">
              <Settings className="text-indigo-400" size={20} />
            </div>
            <div>
              <h2 className="text-xl font-black text-white italic tracking-tighter uppercase">Configurar {symbol}</h2>
              <p className="text-[9px] text-zinc-600 font-black uppercase tracking-[0.3em] mt-0.5">{symData.type || ""} · aplica en ~10s</p>
            </div>
          </div>
          <button onClick={onClose} className="p-3 bg-zinc-900 border border-white/5 rounded-2xl text-zinc-500 hover:text-white hover:border-rose-500/30 transition-all">
            <X size={20} />
          </button>
        </div>

        {/* Tabs de estrategia */}
        <div className="px-8 pt-5 flex items-center gap-2">
          {MODAL_STRATS.map(st => (
            <button key={st} onClick={() => setTab(st)}
              className={`px-4 py-2 rounded-xl text-[10px] font-black uppercase tracking-widest transition-all ${tab === st ? 'bg-indigo-600 text-white' : 'bg-white/[0.03] text-zinc-500 hover:text-zinc-300'}`}>
              {MODAL_STRAT_LABELS[st]}
              <span className={`ml-2 inline-block w-1.5 h-1.5 rounded-full ${draft[st].is_active ? 'bg-emerald-400' : 'bg-zinc-700'}`} />
            </button>
          ))}
        </div>

        {/* Cuerpo */}
        <div className="flex-1 p-8 overflow-y-auto space-y-6">
          <Toggle label="Estrategia activa para este símbolo" on={sd.is_active} onClick={() => setField(tab, 'is_active', !sd.is_active)} />

          {/* Riesgo */}
          <div className="space-y-3">
            <p className="text-[9px] font-black text-zinc-600 uppercase tracking-[0.25em]">Riesgo</p>
            <div className="flex items-end gap-4 flex-wrap">
              <Seg options={[{ value: 'LOTS', label: 'Lotes' }, { value: 'PCT', label: '%' }, { value: 'MONEY', label: '€' }]}
                value={sd.risk_mode} onChange={v => setField(tab, 'risk_mode', v)} />
              <Num label="Valor de riesgo" value={sd.risk_value} onChange={v => setField(tab, 'risk_value', v)} ph="auto" color="text-indigo-300" hint="Riesgo por operación en la unidad elegida" />
            </div>
          </div>

          {/* SL / TP */}
          <div className="space-y-3">
            <p className="text-[9px] font-black text-zinc-600 uppercase tracking-[0.25em]">Stop Loss / Take Profit</p>
            <div className="grid grid-cols-3 gap-4">
              <Num label="SL × ATR" value={sd.sl_mult} onChange={v => setField(tab, 'sl_mult', v)} color="text-rose-300" hint="Multiplicador de Stop Loss (fallback si no hay SL estructural)" />
              <Num label={isScalp ? "TP (usa técnico)" : "TP × ATR"} value={sd.tp_mult} onChange={v => setField(tab, 'tp_mult', v)} color="text-emerald-300" disabled={isScalp} hint={isScalp ? "PrecisionScalping usa TP técnico/banda VWAP" : "Multiplicador de Take Profit"} />
              <Num label="Min R:R" value={sd.min_rr} onChange={v => setField(tab, 'min_rr', v)} color="text-indigo-300" hint="Ratio R:R mínimo para ejecutar" />
            </div>
          </div>

          {/* Gestión */}
          <div className="space-y-3">
            <p className="text-[9px] font-black text-zinc-600 uppercase tracking-[0.25em]">Gestión de la posición</p>
            <div className="grid grid-cols-2 gap-4">
              <div className="flex items-end gap-3">
                <Toggle label="Breakeven" on={sd.use_breakeven} onClick={() => setField(tab, 'use_breakeven', !sd.use_breakeven)} />
                <Num label="BE × ATR" value={sd.be_mult} onChange={v => setField(tab, 'be_mult', v)} color="text-amber-300" disabled={!sd.use_breakeven} />
              </div>
              <div className="flex items-end gap-3">
                <Toggle label="Trailing" on={sd.use_trailing} onClick={() => setField(tab, 'use_trailing', !sd.use_trailing)} />
                <Num label="TS × ATR" value={sd.ts_mult} onChange={v => setField(tab, 'ts_mult', v)} color="text-emerald-300" disabled={!sd.use_trailing} />
              </div>
            </div>
          </div>

          {/* Señal — solo RangeBreaker usa score_threshold como gate. En PrecisionScalping
              el umbral real es entry_threshold (sección Filtros), así que se oculta aquí. */}
          {!isScalp && (
            <div className="space-y-3">
              <p className="text-[9px] font-black text-zinc-600 uppercase tracking-[0.25em]">Señal</p>
              <div className="grid grid-cols-3 gap-4">
                <Num label="Score threshold" value={sd.score_threshold} onChange={v => setField(tab, 'score_threshold', v)} color="text-amber-300" hint="Score mínimo del orquestador para ejecutar" />
              </div>
            </div>
          )}

          {/* Filtros (solo PrecisionScalping) */}
          {isScalp && (
            <div className="space-y-3 pt-2 border-t border-white/5">
              <div className="flex items-center gap-2">
                <Zap size={13} className="text-indigo-400" />
                <p className="text-[9px] font-black text-indigo-300 uppercase tracking-[0.25em]">Filtros de precisión (filter_profile)</p>
              </div>
              <div className="flex items-center gap-3 flex-wrap">
                <span className="text-[8px] font-black text-zinc-500 uppercase tracking-widest">Filtro de ruido</span>
                <Seg options={[{ value: 'on', label: 'On' }, { value: 'soft', label: 'Soft' }, { value: 'off', label: 'Off' }]}
                  value={sd.__noise} onChange={v => setField(tab, '__noise', v)} />
                <span className="text-[8px] text-zinc-600 italic">on=penaliza ruido · soft=solo veto extremo · off=sin penalización</span>
              </div>
              <div className="flex items-center gap-3 flex-wrap">
                <span className="text-[8px] font-black text-zinc-500 uppercase tracking-widest">Salida VWAP dinámica</span>
                <Seg options={[{ value: 'on', label: 'On' }, { value: 'off', label: 'Off' }]}
                  value={sd.__vwapExit} onChange={v => setField(tab, '__vwapExit', v)} />
                <span className="text-[8px] text-zinc-600 italic">on=cierra si el precio cruza VWAP en contra · off=deja correr el trade (mejor en cripto/índices, validado)</span>
              </div>
              <div className="flex items-center gap-3 flex-wrap">
                <span className="text-[8px] font-black text-zinc-500 uppercase tracking-widest">Timeframe de entrada</span>
                <Seg options={TF_OPTIONS} value={sd.__entryTf} onChange={v => setField(tab, '__entryTf', v)} />
                <span className="text-[8px] text-zinc-600 italic">vela de gate EMA9/21 y estructura SL/TP · default M1</span>
              </div>
              <div className="flex items-center gap-3 flex-wrap">
                <span className="text-[8px] font-black text-zinc-500 uppercase tracking-widest">Timeframe de validación</span>
                <Seg options={TF_OPTIONS} value={sd.__validationTf} onChange={v => setField(tab, '__validationTf', v)} />
                <span className="text-[8px] text-zinc-600 italic">contexto RSI/ADX/Chop · default M5</span>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {FILTER_KNOBS.map(kk => (
                  <Num key={kk.k} label={kk.label} value={sd.__knobs[kk.k]} onChange={v => setKnob(tab, kk.k, v)} ph="def" hint={kk.hint} color="text-zinc-200" />
                ))}
              </div>
              <label className="flex flex-col gap-1">
                <span className="text-[8px] font-black text-zinc-500 uppercase tracking-widest">JSON avanzado (claves no expuestas · se fusiona al guardar)</span>
                <textarea rows={2} value={sd.__advanced} onChange={e => setField(tab, '__advanced', e.target.value)}
                  placeholder='{"noise_penalty": 10}'
                  className="bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-[11px] font-mono text-zinc-300 outline-none focus:border-indigo-500/50 resize-none" />
              </label>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-8 py-5 border-t border-white/5 flex items-center justify-end gap-3 bg-white/[0.02]">
          <button onClick={onClose} className="px-5 py-2.5 rounded-xl text-[10px] font-black uppercase tracking-widest text-zinc-400 hover:text-white border border-white/10 transition-all">Cancelar</button>
          <button onClick={save} disabled={saving}
            className={`px-6 py-2.5 rounded-xl text-[10px] font-black uppercase tracking-widest flex items-center gap-2 transition-all ${saving ? 'bg-zinc-900 text-zinc-700 cursor-not-allowed' : 'bg-indigo-600 text-white hover:bg-indigo-500 shadow-lg shadow-indigo-600/20'}`}>
            {saving ? <RefreshCw size={13} className="animate-spin" /> : <Save size={13} />}
            {saving ? 'Guardando' : 'Guardar configuración'}
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
}

function App() {





  // FASE 39 AUTH
  const [isAuthenticated, setIsAuthenticated] = useState(!!localStorage.getItem('pst_token'))
  const [viewMode, setViewMode] = useState('grid'); // 'grid' | 'nano'
  const [activeTab, setActiveTab] = useState('all');
  const [isFocusMode, setIsFocusMode] = useState(false);
  const [toasts, setToasts] = useState([]);
  const [passwordInput, setPasswordInput] = useState('')
  const [loginError, setLoginError] = useState('')

  const [account, setAccount] = useState(null)



  const [trades, setTrades] = useState([])
  const sortedTrades = useMemo(() => {
    return [...trades].sort((a, b) => (b.profit || 0) - (a.profit || 0));
  }, [trades]);

  const [symbols, setSymbols] = useState([])



  const [selectedSymbol, setSelectedSymbol] = useState("GOOG")



  const [ohlc, setOhlc] = useState([])



  const [loading, setLoading] = useState(true)



  const [currentView, setCurrentView] = useState('home') // 'home' | 'surveillance' | 'config' | 'heatmap' | 'multichart'



  const [manualLot, setManualLot] = useState(0.10)



  const [isSmartMode, setIsSmartMode] = useState(true)
  const [riskAmount, setRiskAmount] = useState(25)
  const [slPrice, setSlPrice] = useState('')
  const [tpPrice, setTpPrice] = useState('')
  const [rrRatio, setRrRatio] = useState(2.0)
  const [isExecuting, setIsExecuting] = useState(false)



  const [isAutoTrading, setIsAutoTrading] = useState(true)





  const [historyTrades, setHistoryTrades] = useState([])

  // Fase 3: símbolo seleccionado para el modal de configuración del Matrix Editor
  const [configModal, setConfigModal] = useState(null)

  const [activeStrategyTab, setActiveStrategyTab] = useState(null)



  const [selectedTimeframe, setSelectedTimeframe] = useState("M5")



  const [time, setTime] = useState(new Date().toLocaleTimeString())



  const [telemetry, setTelemetry] = useState(null)



  const [matrixData, setMatrixData] = useState([])



  const [editingParams, setEditingParams] = useState({}) // {symbol: {lot_size, sl_mult, tp_mult, score_threshold}}



  const [savingSymbol, setSavingSymbol] = useState(null)

  // -- FASE 35 RISK PROFILES --
  const [riskProfiles, setRiskProfiles] = useState([])
  const [selectedProfileId, setSelectedProfileId] = useState('')
  const [newProfileName, setNewProfileName] = useState('')
  const [logs, setLogs] = useState([])
  const [isMuted, setIsMuted] = useState(false)
  const [replayTrade, setReplayTrade] = useState(null)
  const [upcomingNews, setUpcomingNews] = useState([])
  const [isNewsBlocked, setIsNewsBlocked] = useState(false)
  const prevSignalsRef = useRef({}) // {symbol: score}

  // -- FASE 51-53 EXPANSION --
  const [activeView, setActiveView] = useState('home') // 'home', 'history', 'analytics', 'heatmap', 'multichart', 'strategylab'
  const [analyticsData, setAnalyticsData] = useState(null)
  const [journalSnapshot, setJournalSnapshot] = useState(null)
  const [isJournalLoading, setIsJournalLoading] = useState(false)

  const [perfData, setPerfData] = useState(null)
  const [equityCurve, setEquityCurve] = useState([])
  const [stratPerf, setStratPerf] = useState([])
  const [bucketData, setBucketData] = useState(null)
  const [hourlyData, setHourlyData] = useState([])

  const [terminalTradesFilter, setTerminalTradesFilter] = useState('ACTIVE') // 'ACTIVE', 'HISTORY', 'ALL'
  const activeSymbols = symbols.filter(s => s.is_active)
  const strategies = symbols.length > 0 ? Object.keys(symbols[0].factors_map || {}) : []










  useEffect(() => {
    const timer = setInterval(() => setTime(new Date().toLocaleTimeString()), 1000)
    return () => clearInterval(timer)
  }, [])

  // ENGINE: Sincronización Automática (FASE 43)
  useEffect(() => {
    if (!isAuthenticated) return;

    // Carga inicial inmediata
    fetchData();
    fetchBotConfig();
    fetchPerformance();
    fetchHistory();
    fetchLogs();
    fetchMatrix();
    fetchNews();

    // Polling rápido cada 3 segundos para datos críticos
    const syncTimer = setInterval(() => {
      fetchData();
      fetchLogs();
    }, 3000);

    // Polling medio cada 10 segundos para datos analíticos e históricos
    const analyticsTimer = setInterval(() => {
      fetchAnalytics();
      fetchHistory();
      fetchNews();
    }, 10000);

    return () => {
      clearInterval(syncTimer);
      clearInterval(analyticsTimer);
    }
  }, [isAuthenticated])

  // FASE 50: SENTINEL ALERTS (Audio & Visual)
  useEffect(() => {
    if (isMuted || symbols.length === 0) return;

    let hasAlert = false;
    const newSignals = {};

    symbols.forEach(s => {
      newSignals[s.symbol] = s.score;
      const prevScore = prevSignalsRef.current[s.symbol] || 0;

      // Alerta de Señal Crítica (>= 80)
      if (s.score >= 80 && prevScore < 80) {
        hasAlert = true;
        addToast(`SENTINEL SIGNAL: ${s.symbol} ready for ${s.signal_direction}`, 'success');
        playSentinelSound('high');
      }
      // Alerta de Pre-Señal (>= 70)
      else if (s.score >= 70 && prevScore < 70) {
        hasAlert = true;
        addToast(`PRE-SIGNAL: ${s.symbol} charging (${s.score}%)`, 'info');
        playSentinelSound('low');
      }
    });

    if (hasAlert) {
      flashTabTitle();
    }

    prevSignalsRef.current = newSignals;
  }, [symbols, isMuted])

  const playSentinelSound = (type) => {
    if (isMuted) return;
    try {
      const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();

      osc.type = 'sine';
      osc.frequency.setValueAtTime(type === 'high' ? 880 : 440, audioCtx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(type === 'high' ? 220 : 110, audioCtx.currentTime + 0.5);

      gain.gain.setValueAtTime(0.1, audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.5);

      osc.connect(gain);
      gain.connect(audioCtx.destination);

      osc.start();
      osc.stop(audioCtx.currentTime + 0.5);
    } catch (e) { console.error("Audio Alert failed:", e) }
  }

  const flashTabTitle = () => {
    const originalTitle = "Sentinel Trade";
    let count = 0;
    const interval = setInterval(() => {
      document.title = count % 2 === 0 ? "⚠️ SIGNAL READY" : "PST SENTINEL";
      count++;
      if (count > 10) {
        clearInterval(interval);
        document.title = originalTitle;
      }
    }, 500);
  }

  // SURVEILLANCE: Carga de Gráficos y Telemetría (FASE 44)
  useEffect(() => {
    if (!isAuthenticated || currentView !== 'surveillance' || !selectedSymbol) return;

    // Carga inmediata
    fetchOhlc(selectedSymbol, selectedTimeframe);
    fetchTelemetry(selectedSymbol);

    // Polling de 10s para gráficos y telemetría detallada
    const survTimer = setInterval(() => {
      fetchOhlc(selectedSymbol, selectedTimeframe);
      fetchTelemetry(selectedSymbol);
    }, 10000);

    return () => clearInterval(survTimer);
  }, [isAuthenticated, currentView, selectedSymbol, selectedTimeframe])











  const addToast = (message, type = 'info') => {
    const id = Date.now()
    setToasts(prev => [...prev, { id, message, type }])
    setTimeout(() => {
      removeToast(id)
    }, 3000) // Reducido a 3s para v1.5.0
  }

  const removeToast = (id) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }

  const fetchData = async () => {
    // Intentamos cargar cada bloque de forma independiente para que un 503 no rompa todo el Dashboard
    const loadAccount = async () => {
      try {
        const res = await api.get(`${API_BASE}/account`);
        setAccount(res.data);
      } catch (e) { console.error("Account fetch failed:", e.message); }
    };

    const loadTrades = async () => {
      try {
        const res = await api.get(`${API_BASE}/trades/active`);
        setTrades(res.data);
      } catch (e) { console.error("Trades fetch failed:", e.message); }
    };

    const loadSymbols = async () => {
      try {
        const res = await api.get(`${API_BASE}/symbols`);
        setSymbols(res.data);
      } catch (e) { console.error("Symbols fetch failed:", e.message); }
    };

    await Promise.allSettled([loadAccount(), loadTrades(), loadSymbols()]);
    setLoading(false);
  }

  const fetchBotConfig = async () => {
    try {
      const resp = await api.get(`${API_BASE}/config/bot/trading_mode?default=AUTO`)
      setIsAutoTrading(resp.data.value === 'AUTO')
    } catch (err) { console.error(err) }
  }

  const fetchPerformance = async () => {
    try {
      const [perfRes, eqRes, stratRes, buckRes, hourlyRes] = await Promise.all([
        api.get(`${API_BASE}/performance`),
        api.get(`${API_BASE}/performance/equity`),
        api.get(`${API_BASE}/performance/strategies`),
        api.get(`${API_BASE}/performance/buckets`),
        api.get(`${API_BASE}/performance/hourly`)
      ])
      setPerfData(perfRes.data)
      setEquityCurve(eqRes.data || [])
      setStratPerf(stratRes.data || [])
      setBucketData(buckRes.data || null)
      setHourlyData(hourlyRes.data || [])
    } catch (err) { console.error(err) }
  }

  const fetchHistory = async () => {
    try {
      const resp = await api.get(`${API_BASE}/history?limit=30`)
      setHistoryTrades(resp.data || [])
    } catch (err) { console.error(err) }
  }

  const fetchLogs = async () => {
    try {
      const resp = await api.get(`${API_BASE}/logs?limit=50`)
      setLogs(resp.data.logs || [])
    } catch (err) { console.error(err) }
  }

  const fetchMatrix = async () => {
    try {
      const resp = await api.get(`${API_BASE}/matrix`)
      setMatrixData(resp.data)
    } catch (err) { console.error(err) }
  }

  const fetchOhlc = async (sym, tf) => {
    if (!sym) return;
    try {
      // Ajuste de ruta (FASE 44): la API escucha en /ohlc/{symbol}
      const resp = await api.get(`${API_BASE}/ohlc/${sym}?timeframe=${tf}`)
      setOhlc(resp.data || [])
    } catch (err) { console.error(err) }
  }

  const fetchTelemetry = async (sym) => {
    if (!sym) return;
    try {
      const resp = await api.get(`${API_BASE}/symbols/${sym}/telemetry`)
      setTelemetry(resp.data)
    } catch (err) { console.error(err) }
  }

  const fetchProfiles = async () => {
    try {
      const resp = await api.get(`${API_BASE}/config/risk_profiles`)
      setRiskProfiles(resp.data)
    } catch (err) { console.error(err) }
  }

  const toggleAutoTrading = async () => {
    const newVal = isAutoTrading ? 'MANUAL' : 'AUTO'
    try {
      await api.post(`${API_BASE}/config/bot`, { key: 'trading_mode', value: newVal })
      setIsAutoTrading(!isAutoTrading)
      addToast(`Trading mode set to ${newVal}`, 'success')
    } catch (err) { addToast('Error toggling bot', 'error') }
  }

  const handleCloseTrade = async (ticket) => {
    if (!window.confirm(`¿Cerrar posición ${ticket}?`)) return
    try {
      await api.post(`${API_BASE}/trades/close`, { ticket })
      addToast(`Posición ${ticket} cerrada`, 'success')
      fetchData()
    } catch (err) { addToast('Error al cerrar posición', 'error') }
  }

  const handlePurgeSymbol = async () => {
    if (!selectedSymbol) return;
    if (!window.confirm(`¿Purgar ${selectedSymbol}? Se detendrán todas las estrategias.`)) return;
    try {
      await api.post(`${API_BASE}/config/toggle`, { symbol: selectedSymbol, is_active: false });
      addToast(`${selectedSymbol} deactivated`, 'info');
      fetchMatrix();
    } catch (err) {
      addToast('Error purging symbol', 'error');
    }
  }

  const handleViewTrade = (trade) => {
    setReplayTrade(trade);
    setSelectedSymbol(trade.symbol);
    setCurrentView('surveillance');
    setSelectedTimeframe('M15');
    addToast(`Replaying Mission: ${trade.symbol}`, 'info');
  }

  const handleCloseSymbol = async (symbol) => {
    if (!window.confirm(`Close ALL trades for ${symbol}?`)) return
    try {
      await api.delete(`${API_BASE}/trades/close/${symbol}`)
      addToast(`Closing trades for ${symbol}...`, 'info')
      setTimeout(fetchData, 1000)
    } catch (err) { addToast('Error closing trades', 'error') }
  }

  const saveSymbolParams = async (symbol) => {
    const params = editingParams[symbol]
    if (!params) return
    setSavingSymbol(symbol)
    try {
      await api.put(`${API_BASE}/symbols/${symbol}/params`, params)
      addToast(`Params for ${symbol} updated`, 'success')
      setSavingSymbol(null)
      fetchMatrix()
    } catch (err) {
      addToast('Error saving params', 'error')
      setSavingSymbol(null)
    }
  }

  const updateParam = (symbol, field, val) => {
    setEditingParams(prev => ({
      ...prev,
      [symbol]: {
        ...(prev[symbol] || (symbols.find(s => s.symbol === symbol) ? {
          lot_size: symbols.find(s => s.symbol === symbol).lot_size,
          sl_mult: symbols.find(s => s.symbol === symbol).sl_mult,
          tp_mult: symbols.find(s => s.symbol === symbol).tp_mult,
          score_threshold: symbols.find(s => s.symbol === symbol).score_threshold,
          risk_mode: symbols.find(s => s.symbol === symbol).risk_mode || 'LOTS',
          risk_value: symbols.find(s => s.symbol === symbol).risk_value || 0.01
        } : {})),
        [field]: val
      }
    }))
  }

  const updateStrategyConfig = async (symbol, strategy, field, val) => {
    try {
      await api.post(`${API_BASE}/config/strategy`, {
        symbol,
        strategy,
        [field]: val
      })
      addToast(`${strategy} updated`, 'success')
      fetchMatrix()
    } catch (err) {
      addToast('Error updating strategy', 'error')
    }
  }

  const toggleSymbolStatus = async (symbol, isActive) => {
    try {
      await api.post(`${API_BASE}/config/symbol`, { symbol, is_active: !isActive })
      addToast(`${symbol} status updated`, 'success')
      fetchData()
      fetchMatrix()
    } catch (err) { addToast('Error updating status', 'error') }
  }

  const handleManualTrade = async (symbol, action) => {
    setIsExecuting(true)
    try {
      await api.post(`${API_BASE}/trades/manual`, {
        symbol,
        action,
        volume: manualLot,
        is_smart: isSmartMode,
        risk_amount: isSmartMode ? riskAmount : null,
        sl_price: isSmartMode && slPrice !== '' ? parseFloat(slPrice) : null,
        tp_price: isSmartMode && tpPrice !== '' ? parseFloat(tpPrice) : null,
        rr_ratio: isSmartMode ? rrRatio : null
      })
      addToast(`${isSmartMode ? 'Smart' : 'Market'} ${action} ${symbol} executed`, 'success')
      fetchData()
    } catch (err) { addToast('Error executing trade', 'error') }
    finally { setIsExecuting(false) }
  }

  const fetchNews = async () => {
    try {
      const res = await api.get(`${API_BASE}/news/upcoming`)
      setUpcomingNews(res.data.upcoming || [])
      setIsNewsBlocked(res.data.is_global_block || false)
    } catch (err) {
      console.error("Error fetching news:", err)
    }
  }

  const getNewsCountdown = (timeIso) => {
    if (!timeIso) return ""
    const target = new Date(timeIso)
    const diff = target - new Date()
    if (diff <= 0) return "IMM"
    const mins = Math.floor(diff / 60000)
    const hours = Math.floor(mins / 60)
    if (hours > 0) return `${hours}h ${mins % 60}m`
    return `${mins}m`
  }

  const fetchAnalytics = async () => {
    try {
      const resp = await api.get(`${API_BASE}/performance/analytics`)
      setAnalyticsData(resp.data)
    } catch (err) { console.error("Error fetching analytics:", err) }
  }

  const updateTradeNotes = async (ticket, notes) => {
    try {
      await api.post(`${API_BASE}/trades/notes`, { ticket, notes })
      fetchHistory() // Refresh history to see changes
      addToast(`Notes updated for Ticket #${ticket}`, 'success')
    } catch (err) {
      console.error("Error updating notes:", err)
      addToast("Failed to update notes", 'error')
    }
  }

  const fetchJournalSnapshot = async (ticket) => {
    setIsJournalLoading(true)
    try {
      const resp = await api.get(`${API_BASE}/trades/${ticket}/snapshot`)
      setJournalSnapshot(resp.data)
    } catch (err) {
      console.error("Error fetching journal snapshot:", err)
      addToast("Snapshot not found for this trade", 'info')
    } finally {
      setIsJournalLoading(false)
    }
  }

  const navigateToSurveillance = (symbol) => {
    setReplayTrade(null);
    setSelectedSymbol(symbol)
    setCurrentView('surveillance')
  }

  const handleLogin = async (e) => {
    e.preventDefault()
    try {
      const resp = await axios.post(`${API_BASE}/auth/login`, { password: passwordInput })
      if (resp.data.status === 'authenticated') {
        localStorage.setItem('pst_token', resp.data.token)
        axios.defaults.headers.common['X-PST-Token'] = resp.data.token
        setIsAuthenticated(true)
        setLoginError('')
      }
    } catch (err) {
      setLoginError('Invalid access protocol')
    }
  }
  if (!isAuthenticated) {
    return (
      <div className="min-h-screen bg-[#020202] flex items-center justify-center p-4 selection:bg-indigo-500/30 font-sans">
        <div className="w-full max-w-md bg-[#050505] border border-zinc-800/80 rounded-[3rem] p-10 shadow-[0_0_100px_rgba(0,0,0,0.8)] flex flex-col items-center">
          <div className="w-20 h-20 bg-indigo-500/10 rounded-full flex items-center justify-center mb-8 border border-indigo-500/20 shadow-[0_0_30px_rgba(99,102,241,0.2)]">
            <Lock className="text-indigo-500" size={36} />
          </div>
          <h1 className="text-3xl font-black text-white tracking-widest uppercase italic mb-2 text-center">Restricted Area</h1>
          <p className="text-xs text-zinc-500 font-bold uppercase tracking-[0.2em] mb-10 text-center">Pails Sentinel Trade Engine</p>

          <form onSubmit={handleLogin} className="w-full">
            <div className="mb-6">
              <input
                type="password"
                value={passwordInput}
                onChange={e => setPasswordInput(e.target.value)}
                placeholder="ENTER ACCESS PROTOCOL"
                className="w-full bg-zinc-900/50 border border-zinc-800 rounded-2xl px-6 py-4 text-white text-sm font-black tracking-widest outline-none focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/50 transition-all text-center"
              />
            </div>
            {loginError && <p className="text-rose-500 text-[10px] font-black uppercase tracking-widest text-center mb-6">{loginError}</p>}

            <button type="submit" className="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-black text-xs tracking-[0.3em] uppercase py-4 rounded-2xl transition-all shadow-[0_0_20px_rgba(99,102,241,0.3)] hover:shadow-[0_0_30px_rgba(99,102,241,0.5)]">
              Authenticate
            </button>
          </form>
        </div>
      </div>
    )
  }

  return (
    <div className="h-screen w-screen bg-[#030303] text-zinc-100 font-sans selection:bg-indigo-500/30 overflow-hidden relative">



      {/* Notification Toast Hub */}



      <div className="fixed top-8 right-8 z-[100] flex flex-col gap-3 pointer-events-none">



        {toasts.map(t => (



          <Toast key={t.id} {...t} onClose={removeToast} />



        ))}



      </div>



      {/* Sidebar / Nav */}



      <nav className="fixed left-0 top-0 h-full w-20 border-r border-white/5 bg-[#050505]/60 backdrop-blur-2xl flex flex-col items-center py-8 gap-8 z-50">



        <div className="w-12 h-12 bg-indigo-600 rounded-2xl flex items-center justify-center shadow-lg shadow-indigo-500/20 cursor-pointer" onClick={() => setCurrentView('home')}>



          <ShieldCheck className="text-white" size={28} />



        </div>



        <div className="flex flex-col gap-6 mt-4">



          <NavItem



            icon={<LayoutGrid size={24} />}



            active={currentView === 'home'}



            onClick={() => setCurrentView('home')}



            label="Home"



          />



          <NavItem



            icon={<BarChart3 size={24} />}



            active={currentView === 'surveillance'}



            onClick={() => setCurrentView('surveillance')}



            label="Live Detail"



          />



          <NavItem



            icon={<Terminal size={24} />}



            active={currentView === 'terminal'}



            onClick={() => setCurrentView('terminal')}



            label="Terminal"



            badge={trades.length}



          />



          <NavItem



            icon={<History size={24} />}



            active={currentView === 'history'}



            onClick={() => setCurrentView('history')}



            label="Historial"



          />



          <NavItem
            icon={<TrendingUp size={24} />}
            active={currentView === 'performance'}
            onClick={() => { setCurrentView('performance'); fetchPerformance(); fetchHistory(); }}
            label="Perf"
          />

          <NavItem



            icon={<Settings size={24} />}



            active={currentView === 'config'}



            onClick={() => setCurrentView('config')}



            label="Matrix"



          />



        </div>



      </nav>







      {/* Top Bar - Fija y Global */}



      <div className="fixed top-0 left-20 right-0 h-20 border-b border-white/5 bg-[#050505]/40 backdrop-blur-3xl z-40 flex items-center justify-between px-8 shadow-2xl">



        <div className="flex items-center gap-4 bg-white/[0.03] border border-white/10 px-6 py-2.5 rounded-2xl shadow-[0_8px_32px_rgba(0,0,0,0.5)] backdrop-blur-xl">



          <div className="flex flex-col">



            <span className="text-[9px] font-black text-zinc-500 uppercase tracking-widest leading-none mb-1">Core Balance</span>



            <span className="text-lg font-black text-white italic tracking-tighter">{account?.balance?.toLocaleString() || '0.00'}€</span>



          </div>



          <div className="w-px h-8 bg-zinc-800/50 mx-2" />



          <div className="flex flex-col">



            <span className="text-[9px] font-black text-zinc-500 uppercase tracking-widest leading-none mb-1">Active Equity</span>



            <span className="text-lg font-black text-white italic tracking-tighter">{account?.equity?.toLocaleString() || '0.00'}€</span>



          </div>



          <div className="w-px h-8 bg-zinc-800/50 mx-2" />



          <div className="flex flex-col">



            <span className="text-[9px] font-black text-zinc-500 uppercase tracking-widest leading-none mb-1">Active Fleet PnL</span>



            <span className={`text-lg font-black italic tracking-tighter ${account?.active_pnl >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>



              {account?.active_pnl >= 0 ? '+' : ''}{account?.active_pnl?.toFixed(2) || '0.00'}€



            </span>



          </div>



          <div className="w-px h-8 bg-zinc-800/50 mx-2" />



          <div className="flex flex-col">



            <div className="flex items-center gap-2 mb-1">



              <span className="text-[9px] font-black text-zinc-500 uppercase tracking-widest leading-none">Day Performance</span>



              <div className={`w-1 h-1 rounded-full ${account?.daily_pnl >= 0 ? 'bg-emerald-500 shadow-[0_0_10px_rgba(16,185,129,0.5)]' : 'bg-rose-500 shadow-[0_0_10px_rgba(244,63,94,0.5)]'}`} />



            </div>



            <span className={`text-lg font-black italic tracking-tighter ${account?.daily_pnl >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>



              {account?.daily_pnl >= 0 ? '+' : ''}{account?.daily_pnl?.toFixed(2)}€



            </span>



          </div>



        </div>        <div className="flex items-center gap-2 bg-zinc-900/50 p-1 rounded-2xl border border-zinc-800/50">
          <button
            onClick={() => { setActiveView('home'); setCurrentView('home'); }}
            className={`w-12 h-12 rounded-2xl flex items-center justify-center transition-all duration-300 relative group
              ${activeView === 'home' && currentView === 'home'
                ? 'bg-indigo-500/20 text-indigo-400 shadow-[0_0_15px_rgba(99,102,241,0.3)]'
                : 'text-zinc-500 hover:text-indigo-400 hover:bg-zinc-800/50'}`}
          >
            <Activity size={24} />
            <div className="absolute left-14 bg-zinc-800 text-white text-xs px-2 py-1 rounded opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap z-50">
              Dashboard
            </div>
          </button>

          <button
            onClick={() => { setActiveView('heatmap'); setCurrentView('heatmap'); }}
            className={`w-12 h-12 rounded-2xl flex items-center justify-center transition-all duration-300 relative group
              ${activeView === 'heatmap'
                ? 'bg-amber-500/20 text-amber-400 shadow-[0_0_15px_rgba(245,158,11,0.3)]'
                : 'text-zinc-500 hover:text-amber-400 hover:bg-zinc-800/50'}`}
          >
            <LayoutGrid size={24} />
            <div className="absolute left-14 bg-zinc-800 text-white text-xs px-2 py-1 rounded opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap z-50">
              Market Heatmap
            </div>
          </button>

          <button
            onClick={() => { setActiveView('multichart'); setCurrentView('multichart'); }}
            className={`w-12 h-12 rounded-2xl flex items-center justify-center transition-all duration-300 relative group
              ${activeView === 'multichart'
                ? 'bg-emerald-500/20 text-emerald-400 shadow-[0_0_15px_rgba(16,185,129,0.3)]'
                : 'text-zinc-500 hover:text-emerald-400 hover:bg-zinc-800/50'}`}
          >
            <LayoutDashboard size={24} />
            <div className="absolute left-14 bg-zinc-800 text-white text-xs px-2 py-1 rounded opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap z-50">
              Multi-Chart Workspace
            </div>
          </button>

          <button
            onClick={() => { setActiveView('strategylab'); setCurrentView('strategylab'); }}
            className={`w-12 h-12 rounded-2xl flex items-center justify-center transition-all duration-300 relative group
              ${activeView === 'strategylab'
                ? 'bg-purple-500/20 text-purple-400 shadow-[0_0_15px_rgba(168,85,247,0.3)]'
                : 'text-zinc-500 hover:text-purple-400 hover:bg-zinc-800/50'}`}
          >
            <FlaskConical size={24} />
            <div className="absolute left-14 bg-zinc-800 text-white text-xs px-2 py-1 rounded opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap z-50">
              Strategy Lab Sim
            </div>
          </button>

          <button
            onClick={() => {
              setCurrentView('history')
              fetchHistory()
            }}
            className={`flex items-center gap-2 px-4 py-2 rounded-xl text-[10px] font-black uppercase tracking-widest transition-all ${currentView === 'history' ? 'bg-indigo-500 text-white shadow-[0_0_20px_rgba(99,102,241,0.3)]' : 'text-zinc-500 hover:text-zinc-300'}`}
          >
            <BookMarked size={14} />
            History
          </button>
          <button
            onClick={() => {
              setCurrentView('analytics')
              fetchAnalytics()
            }}
            className={`flex items-center gap-2 px-4 py-2 rounded-xl text-[10px] font-black uppercase tracking-widest transition-all ${currentView === 'analytics' ? 'bg-indigo-500 text-white shadow-[0_0_20px_rgba(99,102,241,0.3)]' : 'text-zinc-500 hover:text-zinc-300'}`}
          >
            <BarChart3 size={14} />
            Insights
          </button>
        </div>

        <div className="flex items-center gap-8">



          <div className="flex items-center gap-4 bg-zinc-900/50 px-6 py-2.5 rounded-2xl border border-zinc-800/50 min-w-[140px] justify-center">



            <Clock size={16} className="text-indigo-500" />



            <span className="text-sm font-black font-mono text-zinc-300 tracking-tighter">{time}</span>



          </div>

          {/* News Guard Segment (Fase 50) */}
          {upcomingNews.length > 0 && (
            <div className={`flex items-center gap-4 bg-zinc-900/50 px-5 py-2.5 rounded-2xl border transition-all duration-500 ${isNewsBlocked ? 'border-rose-500/40 bg-rose-500/5 shadow-[0_0_20px_rgba(244,63,94,0.1)]' : 'border-zinc-800/50'}`}>
              <ShieldAlert size={16} className={isNewsBlocked ? 'text-rose-400 animate-pulse' : 'text-zinc-500'} />
              <div className="flex flex-col">
                <span className="text-[9px] font-black text-zinc-500 uppercase tracking-widest leading-none mb-1">
                  {isNewsBlocked ? 'NEWS BLOCK ACTIVE' : 'UPCOMING NEWS'}
                </span>
                <div className="flex items-center gap-2">
                  <span className={`text-[10px] font-black ${isNewsBlocked ? 'text-rose-400' : 'text-zinc-300'} truncate max-w-[150px]`}>
                    {upcomingNews[0].country} {upcomingNews[0].title}
                  </span>
                  <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-md bg-zinc-800 border border-white/5 ${isNewsBlocked ? 'text-rose-300' : 'text-indigo-400'}`}>
                    {getNewsCountdown(upcomingNews[0].time_utc)}
                  </span>
                </div>
              </div>
            </div>
          )}

          <div className="flex items-center gap-6">



            {/* Kill Switch Toggle */}



            <div className="flex flex-col items-end mr-2">



              <span className="text-[9px] font-black text-zinc-500 uppercase tracking-[0.2em] leading-none mb-1.5">Trading Mode</span>



              <button



                onClick={toggleAutoTrading}



                className={`flex items-center gap-2 px-3 py-1.5 rounded-xl border transition-all active:scale-95 ${isAutoTrading



                  ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400 shadow-[0_0_15px_rgba(16,185,129,0.15)]'



                  : 'bg-zinc-800 border-zinc-700 text-zinc-500'



                  }`}



              >



                <div className={`w-1.5 h-1.5 rounded-full ${isAutoTrading ? 'bg-emerald-500 animate-pulse' : 'bg-zinc-600'}`} />



                <span className="text-[10px] font-black uppercase tracking-widest">{isAutoTrading ? 'Auto' : 'Manual'}</span>



              </button>



            </div>







            <div className="flex flex-col items-end">



              <span className="text-[9px] font-black text-zinc-500 uppercase tracking-[0.2em] leading-none mb-1">Engine Load</span>

              <span className="text-[10px] font-black text-indigo-400 italic">OPTIMAL</span>
            </div>

            {/* Mute Toggle */}
            <button
              onClick={() => setIsMuted(!isMuted)}
              className={`p-3 rounded-xl border transition-all active:scale-90 ${isMuted ? 'bg-rose-500/10 border-rose-500/20 text-rose-500' : 'bg-indigo-600/10 border-indigo-500/20 text-indigo-500'}`}
              title={isMuted ? 'Unmute Alerts' : 'Mute Alerts'}
            >
              {isMuted ? <VolumeX size={20} /> : <Volume2 size={20} />}
            </button>

            <button onClick={fetchData} className="p-3 bg-indigo-600/10 border border-indigo-500/20 text-indigo-500 rounded-xl hover:bg-indigo-600 hover:text-white transition-all active:scale-90">

              <RefreshCw size={20} className={loading ? "animate-spin" : ""} />



            </button>



          </div>



        </div>



      </div>







      {/* Main Content */}



      <main className="fixed left-20 right-0 top-20 bottom-0 overflow-y-auto pt-6 px-6 pb-10 transition-all duration-500">



        {currentView !== 'home' && currentView !== 'surveillance' && currentView !== 'terminal' && (



          <header className="w-full flex justify-between items-center mb-6 px-6">



            <div>



              <h1 className="text-3xl font-black tracking-tighter text-white mb-1 uppercase italic">



                {currentView === 'surveillance' && `${selectedSymbol} Analysis`}



                {currentView === 'config' && 'Permissions Matrix'}



              </h1>



              <div className="flex items-center gap-3">



                <span className="text-zinc-600 font-black font-mono text-[9px] tracking-[0.3em] uppercase">PST-CORE: V2.6.12 SMC</span>



                <div className="w-1.5 h-1.5 bg-indigo-500 rounded-full animate-pulse shadow-[0_0_10px_rgba(99,102,241,0.5)]" />



                <span className="text-zinc-500 font-bold font-mono text-[9px] tracking-widest uppercase text-indigo-400/80">



                  {currentView === 'surveillance' && 'Real-time Charting & Execution Hub'}



                  {currentView === 'config' && 'Asset Configuration & Global Toggles'}



                </span>



              </div>



            </div>



          </header>



        )}







        {currentView === 'performance' && (
          <PerformanceDashboard
            perfData={perfData}
            equityCurve={equityCurve}
            stratPerf={stratPerf}
            historyTrades={historyTrades}
            bucketData={bucketData}
            hourlyData={hourlyData}
          />
        )}

        {currentView === 'analytics' && (
          <div className="w-full px-2 animate-in fade-in slide-in-from-bottom-2 duration-700">
            <div className="mb-4" />

            <div className="grid grid-cols-1 lg:grid-cols-4 gap-6 mb-8 px-4">
              {[
                { label: 'Win Rate', value: `${analyticsData?.metrics?.win_rate || 0}%`, icon: Target, color: 'text-emerald-400' },
                { label: 'Profit Factor', value: analyticsData?.metrics?.profit_factor || '0.00', icon: Activity, color: 'text-indigo-400' },
                { label: 'Max Win Streak', value: analyticsData?.metrics?.max_win_streak || 0, icon: TrendingUp, color: 'text-emerald-500' },
                { label: 'Realized PnL', value: `${(analyticsData?.metrics?.gross_profit - analyticsData?.metrics?.gross_loss || 0).toFixed(2)}€`, icon: Wallet, color: 'text-white' },
              ].map((kpi, idx) => (
                <div key={idx} className="bg-zinc-900/30 border border-white/5 rounded-[2rem] p-6 hover:bg-zinc-900/50 transition-all">
                  <div className="flex items-center justify-between mb-4">
                    <div className={`p-3 rounded-2xl bg-white/5 ${kpi.color}`}>
                      <kpi.icon size={20} />
                    </div>
                  </div>
                  <p className="text-[10px] font-black text-zinc-500 uppercase tracking-widest mb-1">{kpi.label}</p>
                  <h3 className={`text-3xl font-black italic tracking-tighter ${kpi.color}`}>{kpi.value}</h3>
                </div>
              ))}
            </div>

            <div className="px-4">
              <div className="bg-[#050505] border border-white/5 rounded-[3rem] p-8 shadow-2xl overflow-hidden">
                <div className="flex items-center justify-between mb-8">
                  <h3 className="text-xl font-black text-white italic tracking-tighter uppercase">Equity Curve Surveillance</h3>
                  <div className="flex items-center gap-2">
                    <div className="w-3 h-3 bg-indigo-500 rounded-full shadow-[0_0_10px_rgba(99,102,241,0.5)]" />
                    <span className="text-[10px] font-black text-zinc-400 uppercase tracking-widest">Cumulative Growth</span>
                  </div>
                </div>
                <div className="h-[400px] w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={analyticsData?.equity_curve || []}>
                      <defs>
                        <linearGradient id="colorEquity" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
                          <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="#ffffff05" vertical={false} />
                      <XAxis dataKey="time" stroke="#ffffff20" fontSize={10} tickFormatter={(val) => val === 'Start' ? 'INIT' : new Date(val).toLocaleDateString()} />
                      <YAxis stroke="#ffffff20" fontSize={10} tickFormatter={(val) => `${val}€`} />
                      <Tooltip
                        contentStyle={{ backgroundColor: '#0a0a0a', border: '1px solid #ffffff10', borderRadius: '1rem' }}
                        itemStyle={{ color: '#fff', fontSize: '12px', fontWeight: 'bold' }}
                      />
                      <Area type="monotone" dataKey="equity" stroke="#6366f1" strokeWidth={3} fillOpacity={1} fill="url(#colorEquity)" />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </div>

            <div className="px-4 mt-8">
              <PerformanceCalendar trades={historyTrades} />
            </div>

            <div className="px-4 mt-8">
              <div className="bg-[#050505] border border-white/5 rounded-[3rem] p-8 shadow-2xl">
                <h3 className="text-xl font-black text-white italic tracking-tighter uppercase mb-8">Intelligence Breakdown: Strategy Win Rates</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
                  {analyticsData?.metrics?.by_strategy && Object.entries(analyticsData.metrics.by_strategy).map(([name, data]) => (
                    <div key={name} className="bg-zinc-900/30 border border-white/5 rounded-3xl p-6 hover:bg-zinc-900/50 transition-all group">
                      <div className="flex justify-between items-start mb-4">
                        <div>
                          <p className="text-[10px] font-black text-zinc-500 uppercase tracking-[0.2em] mb-1">Tactical System</p>
                          <h4 className="text-lg font-black text-white italic tracking-tighter uppercase">{name}</h4>
                        </div>
                        <div className="text-right">
                          <div className={`px-3 py-1 rounded-full text-[10px] font-black mb-2 ${data.win_rate >= 50 ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'}`}>
                            {data.win_rate}% WR
                          </div>
                          <div className={`text-xs font-black font-mono block ${data.profit >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                            {data.profit >= 0 ? '+' : ''}{data.profit.toFixed(2)} $
                          </div>
                        </div>
                      </div>
                      <div className="space-y-3">
                        <div className="flex justify-between items-center text-[11px]">
                          <span className="text-zinc-500 font-bold uppercase tracking-widest">Total Engagements</span>
                          <span className="text-white font-black font-mono">{data.total}</span>
                        </div>
                        <div className="w-full h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                          <div
                            className={`h-full transition-all duration-1000 ${data.win_rate >= 50 ? 'bg-emerald-500 shadow-[0_0_10px_rgba(16,185,129,0.5)]' : 'bg-rose-500 shadow-[0_0_10px_rgba(244,63,94,0.5)]'}`}
                            style={{ width: `${data.win_rate}%` }}
                          />
                        </div>
                        <div className="flex justify-between items-center text-[11px]">
                          <span className="text-zinc-500 font-bold uppercase tracking-widest">Confirmed Wins</span>
                          <span className="text-emerald-400 font-black font-mono">{data.wins}</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {currentView === 'history' && (
          <div className="w-full px-4 animate-in fade-in slide-in-from-bottom-2 duration-700">
            <div className="mb-4" />

            <div className="bg-[#050505] border border-white/5 rounded-[3rem] overflow-hidden shadow-2xl transition-all">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="bg-white/5">
                    <th className="px-6 py-5 text-[10px] font-black text-zinc-500 uppercase tracking-widest">Trade Info</th>
                    <th className="px-6 py-5 text-[10px] font-black text-zinc-500 uppercase tracking-widest text-center">Outcome</th>
                    <th className="px-6 py-5 text-[10px] font-black text-zinc-500 uppercase tracking-widest">Journal & Snapshot</th>
                    <th className="px-6 py-5 text-[10px] font-black text-zinc-500 uppercase tracking-widest text-right">Execute</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.03]">
                  {historyTrades.length === 0 ? (
                    <tr>
                      <td colSpan="4" className="py-20 text-center">
                        <div className="flex flex-col items-center gap-4 opacity-20">
                          <BookMarked size={48} />
                          <p className="text-xs font-black uppercase tracking-[0.4em]">Historical Nodes Empty</p>
                        </div>
                      </td>
                    </tr>
                  ) : (
                    historyTrades.map((t) => (
                      <tr key={t.ticket} className="hover:bg-white/[0.01] transition-colors group">
                        <td className="px-6 py-6">
                          <div className="flex items-center gap-4">
                            <div className={`w-10 h-10 rounded-xl flex items-center justify-center font-black italic text-sm ${t.type === 'BUY' ? 'bg-emerald-500/10 text-emerald-500' : 'bg-rose-500/10 text-rose-500'}`}>
                              {t.type[0]}
                            </div>
                            <div>
                              <div className="flex items-center gap-2">
                                <h4 className="text-base font-black text-white italic tracking-tighter">{t.symbol}</h4>
                                <span className="text-[10px] font-bold text-zinc-600 uppercase">#{t.ticket}</span>
                              </div>
                              <p className="text-[10px] font-black text-zinc-500 uppercase tracking-widest">{t.strategy}</p>
                            </div>
                          </div>
                        </td>
                        <td className="px-6 py-6 text-center">
                          <div className={`text-lg font-black italic tracking-tighter ${t.profit >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                            {t.profit >= 0 ? '+' : ''}{t.profit.toFixed(2)}€
                          </div>
                          <p className="text-[9px] font-black text-zinc-600 uppercase tracking-widest">Realized PnL</p>
                        </td>
                        <td className="px-6 py-6">
                          <div className="flex items-center gap-3">
                            <button
                              onClick={() => fetchJournalSnapshot(t.ticket)}
                              className="p-3 bg-zinc-900 border border-white/5 rounded-xl text-zinc-400 hover:text-white hover:border-indigo-500/50 transition-all flex items-center gap-2"
                            >
                              <ImageIcon size={16} />
                              <span className="text-[9px] font-black uppercase tracking-widest">Snapshot</span>
                            </button>
                            <div className="flex-1 min-w-[200px]">
                              <input
                                type="text"
                                defaultValue={t.notes || ''}
                                placeholder="Add trade insights..."
                                onBlur={(e) => updateTradeNotes(t.ticket, e.target.value)}
                                className="w-full bg-black/40 border border-white/5 rounded-xl px-4 py-2.5 text-[11px] font-medium text-zinc-300 outline-none focus:border-indigo-500/30 transition-all"
                              />
                            </div>
                          </div>
                        </td>
                        <td className="px-6 py-6 text-right">
                          <button
                            onClick={() => navigateToSurveillance(t.symbol)}
                            className="p-3 bg-zinc-900 border border-white/5 rounded-xl text-zinc-500 hover:text-indigo-400 hover:border-indigo-500/50 transition-all"
                          >
                            <ChevronRight size={18} />
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {currentView === 'heatmap' && (
          <div className="w-full h-full animate-in fade-in slide-in-from-bottom-2 duration-700">
            <MarketHeatmap
                symbols={symbols}
                onSelectSymbol={(sym) => {
                    setSelectedSymbol(sym);
                    setCurrentView('surveillance');
                    setActiveView('home');
                }}
            />
          </div>
        )}

        {currentView === 'multichart' && (
          <div className="w-full h-full animate-in fade-in slide-in-from-bottom-2 duration-700">
            <MultiChartWorkspace symbols={symbols} api={api} />
          </div>
        )}

        {currentView === 'strategylab' && (
          <div className="w-full h-full animate-in fade-in slide-in-from-bottom-2 duration-700">
            <StrategyLab symbols={symbols} />
          </div>
        )}

        {activeView === 'home' && currentView === 'home' && (
          <div className="w-full px-2 animate-in fade-in slide-in-from-bottom-2 duration-700">
            {/* Home Header with Focus Mode Toggle */}
            <div className="flex justify-start mb-6 px-4 gap-4">
              {/* View Mode Toggle */}
              <div className="flex items-center gap-2 bg-black/40 backdrop-blur-xl p-1.5 rounded-2xl border border-white/5">
                <button
                  onClick={() => setViewMode('grid')}
                  className={`p-2 rounded-xl transition-all ${viewMode === 'grid' ? 'bg-indigo-500 text-white shadow-lg' : 'text-zinc-500 hover:text-zinc-300'}`}
                  title="Grid View"
                >
                  <LayoutGrid size={16} />
                </button>
                <button
                  onClick={() => setViewMode('nano')}
                  className={`p-2 rounded-xl transition-all ${viewMode === 'nano' ? 'bg-indigo-500 text-white shadow-lg' : 'text-zinc-500 hover:text-zinc-300'}`}
                  title="Nano View (Horizontal)"
                >
                  <StretchHorizontal size={16} />
                </button>
              </div>

              {/* Focus Mode Toggle */}
              <div className="flex items-center gap-4 bg-black/40 backdrop-blur-xl p-2 rounded-2xl border border-white/5">
                <div className="flex flex-col items-end px-2">
                  <span className="text-[9px] font-black text-zinc-500 uppercase tracking-widest">Opportunity Rank</span>
                  <span className={`text-[10px] font-bold ${isFocusMode ? 'text-indigo-400' : 'text-zinc-600'}`}>{isFocusMode ? 'ACTIVE' : 'STANDBY'}</span>
                </div>
                <button
                  onClick={() => setIsFocusMode(!isFocusMode)}
                  className={`w-14 h-8 rounded-xl border transition-all duration-500 flex items-center px-1 ${isFocusMode ? 'bg-indigo-500/20 border-indigo-500/50 justify-end' : 'bg-zinc-800/50 border-white/5 justify-start'}`}
                >
                  <div className={`w-6 h-6 rounded-lg shadow-xl transition-all duration-500 flex items-center justify-center ${isFocusMode ? 'bg-indigo-500' : 'bg-zinc-600'}`}>
                    <Target size={14} className="text-white" />
                  </div>
                </button>
              </div>
            </div>

            {/* NEW: ACTIVE FLEET TRACKER (FASE 54) */}
            {sortedTrades.length > 0 && (
              <div className="mb-8 px-4 animate-in slide-in-from-top-4 duration-700">
                <div className="flex items-center gap-3 mb-4">
                  <div className="p-2 bg-indigo-500/10 rounded-xl border border-indigo-500/20">
                    <Zap className="text-indigo-500 animate-pulse" size={18} />
                  </div>
                  <div>
                    <h3 className="text-sm font-black text-white italic tracking-[0.2em] uppercase">Active Fleet Intelligence</h3>
                    <p className="text-[8px] text-zinc-500 font-bold uppercase tracking-widest">Live Monitoring & Quick Tactical Exit</p>
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 3xl:grid-cols-6 gap-4">
                  {sortedTrades.map(t => (
                    <motion.div
                      key={t.ticket}
                      layoutId={`trade-${t.ticket}`}
                      className={`bg-zinc-900/30 border rounded-2xl p-4 flex flex-col gap-3 group transition-all hover:bg-zinc-900/50 ${t.profit >= 0 ? 'border-emerald-500/30' : 'border-rose-500/30'}`}
                    >
                      <div className="flex justify-between items-start">
                        <div className="flex items-center gap-3">
                          <div className={`w-8 h-8 rounded-lg flex items-center justify-center font-black italic text-[10px] ${t.type === 'BUY' ? 'bg-emerald-500 text-white shadow-[0_0_15px_rgba(16,185,129,0.4)]' : 'bg-rose-500 text-white shadow-[0_0_15px_rgba(244,63,94,0.4)]'}`}>
                            {t.type[0]}
                          </div>
                          <div>
                            <h4 className="text-white font-black italic text-sm leading-none">{t.symbol}</h4>
                            <div className="flex flex-col mt-1">
                                <span className="text-[8px] font-bold text-zinc-500 uppercase tracking-tighter">TIC: {t.ticket}</span>
                                <span className="text-[11px] font-black text-indigo-400 uppercase tracking-widest mt-0.5">
                                    {t.price_current?.toFixed((t.symbol.includes('EURUSD') || t.symbol.includes('GBPUSD')) ? 5 : (t.symbol.includes('JPY') || t.symbol.includes('XAU')) ? 3 : 2)}
                                </span>
                            </div>
                          </div>
                        </div>
                        <div className="text-right">
                          <span className={`text-base font-black italic tracking-tighter ${t.profit >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                            {t.profit >= 0 ? '+' : ''}{t.profit.toFixed(2)}€
                          </span>
                          <div className="text-[7px] font-black text-zinc-600 uppercase">Yield</div>
                        </div>
                      </div>

                      <TradeRiskVisualizer trade={t} />

                      <div className="flex items-center justify-between gap-2 border-t border-white/5 pt-3 mt-1">
                        <div className="flex flex-col">
                          <span className="text-[8px] font-black text-zinc-600 uppercase">Strategy</span>
                          <span className="text-[11px] font-black text-indigo-400 uppercase truncate max-w-[120px]">{t.strategy}</span>
                        </div>
                        <div className="flex gap-2">
                          <button
                            onClick={() => navigateToSurveillance(t.symbol)}
                            className="p-2 bg-zinc-800 hover:bg-zinc-700 rounded-lg text-zinc-400 hover:text-white transition-all"
                          >
                            <BarChart3 size={14} />
                          </button>
                          <button
                            onClick={() => handleCloseTrade(t.ticket)}
                            className="px-3 py-1 bg-rose-600/20 border border-rose-500/40 text-rose-400 rounded-lg text-[9px] font-black uppercase hover:bg-rose-600/40 transition-all"
                          >
                            EXIT
                          </button>
                        </div>
                      </div>
                    </motion.div>
                  ))}
                </div>
              </div>
            )}

            <div
              className={`grid gap-4 w-full ${viewMode === 'nano' ? 'grid-cols-1' : 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 3xl:grid-cols-6'}`}
            >
              {activeSymbols.length > 0 ? (isFocusMode ? [...activeSymbols].sort((a, b) => (b.score || 0) - (a.score || 0)) : activeSymbols).map(s => {
                const hasSignal = s.signal_direction && s.signal_direction !== 'NONE';
                const isBuy = s.signal_direction === 'BUY';
                const isSell = s.signal_direction === 'SELL';







                let colorBase = 'indigo';
                let statusLabel = 'SCANNING';
                let signalText = 'NO-SIGNAL';
                const isBlocked = s.gate_failed || false;

                if (hasSignal && !isBlocked) {
                  colorBase = isBuy ? 'emerald' : 'rose';
                  statusLabel = isBuy ? '🟢 BUY READY' : '🔴 SELL READY';
                  signalText = isBuy ? 'BULLISH INTENT' : 'BEARISH INTENT';
                } else if (isBlocked && s.score >= 70) {
                  colorBase = 'amber';
                  statusLabel = '⛔ BLOCKED: FILTERS';
                } else if (s.score >= 70) {
                  colorBase = 'amber';
                  statusLabel = '🟡 PRE-SIGNAL';
                }



                let isClosed = s.market_open === false;
                if (isClosed) {
                  statusLabel = '💤 CLOSED';
                  colorBase = 'zinc';
                }



                const colorClass = `text-${colorBase}-500`;
                const bgClass = `bg-${colorBase}-500`;
                const borderClass = `border-${colorBase}-500/40`;

                // Sentinel Heatmap Logic (Daily PnL)
                const hasProfit = (s.daily_pnl || 0) > 0;
                const hasLoss = (s.daily_pnl || 0) < 0;
                let heatClass = borderClass;
                let heatGlow = '';

                if (hasProfit) {
                  heatClass = 'border-emerald-500/60 shadow-[0_0_20px_rgba(16,185,129,0.1)]';
                  heatGlow = 'bg-emerald-500/5';
                } else if (hasLoss) {
                  heatClass = 'border-rose-500/60 shadow-[0_0_20px_rgba(244,63,94,0.1)]';
                  heatGlow = 'bg-rose-500/5';
                }

                const glowClass = (hasSignal && !isClosed) ? `shadow-[0_0_40px_rgba(${isBuy ? '16,185,129' : '244,63,94'},0.15)]` : '';
                const closedClass = isClosed ? 'opacity-40 grayscale-[0.8]' : 'opacity-100';

                if (viewMode === 'nano') {
                  return (
                    <div
                      key={s.symbol}
                      onClick={() => navigateToSurveillance(s.symbol)}
                      className={`glass-card ${heatClass} rounded-2xl p-3 hover:bg-zinc-900/60 transition-all duration-500 group cursor-pointer relative overflow-hidden shadow-xl ${glowClass} ${closedClass} flex items-center justify-between gap-4`}
                    >
                      <div className="flex items-center gap-3 min-w-[120px]">
                        <div className={`p-1.5 bg-black/40 rounded-lg border border-white/5 group-hover:neon-border-${colorBase} transition-all`}>
                          <ShieldCheck size={14} className={colorClass} />
                        </div>
                        <div>
                          <h3 className="text-white text-[11px] font-black uppercase tracking-widest leading-none">{s.symbol}</h3>
                          <span className="text-[7px] font-bold text-zinc-500 tracking-tighter uppercase">{s.regime}</span>
                        </div>
                      </div>

                      <div className="flex items-center gap-4 flex-1">
                        <div className="flex flex-col items-center min-w-[70px]">
                          <span className="text-base font-black text-white italic tracking-tighter leading-none">
                            {s.price?.toFixed((s.symbol.includes('EURUSD') || s.symbol.includes('GBPUSD')) ? 5 : s.symbol.includes('JPY') ? 3 : 2)}
                          </span>
                          <span className={`text-[9px] font-black italic ${s.daily_change_pct >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                            {s.daily_change_pct >= 0 ? '+' : ''}{s.daily_change_pct?.toFixed(2)}%
                          </span>
                        </div>
                        <div className="h-6 w-20 flex-shrink-0">
                          {s.sparkline && s.sparkline.length > 0 && <Sparkline data={s.sparkline} color={colorBase} />}
                        </div>
                        <div className="flex flex-col items-center min-w-[60px] border-l border-white/5 pl-4">
                          <span className="text-[6px] font-bold text-zinc-600 mb-0.5 uppercase tracking-tighter">Day PNL</span>
                          <span className={`text-[10px] font-black ${(s.daily_pnl || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                            {(s.daily_pnl || 0) >= 0 ? '+' : ''}{(s.daily_pnl || 0).toFixed(2)}€
                          </span>
                        </div>
                      </div>

                      <div className="flex items-center gap-2">
                        <div className="flex flex-col items-end mr-2">
                          <div className="flex items-center gap-1.5">
                            {isBlocked && <div className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse" title="GATED" />}
                            <span className={`text-[10px] font-black ${colorClass}`}>{Math.round(s.score)}%</span>
                          </div>
                          <div className="flex gap-1 mt-0.5">
                            {["PST-RangeBreaker", "PST-PrecisionScalping"].map(strat => {
                              const score = s.factors_map?.[strat]?.score || 0;
                              return (
                                <div key={strat} className={`w-1.5 h-1.5 rounded-full ${score >= 70 ? 'bg-indigo-500' : 'bg-zinc-800'}`} title={`${strat}: ${Math.round(score)}%`} />
                              );
                            })}
                          </div>
                          <span className="text-[5px] font-black text-zinc-700 uppercase tracking-widest mt-1">
                            {(() => {
                              const factors = s.factors_map ? Object.entries(s.factors_map) : [];
                              if (factors.length === 0) return s.active_strategy || "PST-AUTO";
                              const best = factors.reduce((a, b) => (a[1].score > b[1].score ? a : b));
                              return best[1].score > 0 ? best[0].replace("PST-", "").toUpperCase() : (s.active_strategy || "PST-AUTO");
                            })()}
                          </span>
                        </div>
                        <div className="flex gap-1.5">
                          <button
                            onClick={(e) => { e.stopPropagation(); handleManualTrade(s.symbol, 'BUY') }}
                            disabled={isClosed}
                            className={`px-3 py-1.5 rounded-lg border border-emerald-500/30 bg-emerald-500/5 text-emerald-400 text-[9px] font-black uppercase tracking-widest hover:bg-emerald-500/20 transition-all ${isClosed ? 'opacity-30' : ''}`}
                          >
                            BUY
                          </button>
                          <button
                            onClick={(e) => { e.stopPropagation(); handleManualTrade(s.symbol, 'SELL') }}
                            disabled={isClosed}
                            className={`px-3 py-1.5 rounded-lg border border-rose-500/30 bg-rose-500/5 text-rose-400 text-[9px] font-black uppercase tracking-widest hover:bg-rose-500/20 transition-all ${isClosed ? 'opacity-30' : ''}`}
                          >
                            SELL
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                }

                return (
                  <div
                    key={s.symbol}
                    onClick={() => navigateToSurveillance(s.symbol)}
                    className={`glass-card ${heatClass} rounded-[2rem] p-6 hover:bg-zinc-900/60 transition-all duration-500 group cursor-pointer relative overflow-hidden shadow-2xl ${glowClass} ${closedClass} flex flex-col gap-5 min-h[420px]`}
                  >
                    {heatGlow && <div className={`absolute inset-0 ${heatGlow} animate-pulse duration-[4000ms] pointer-events-none`} />}
                    {/* Header: Title & Status */}
                    <div className="flex justify-between items-center relative z-10">
                      <div className="flex items-center gap-3">
                        <div className={`p-2 bg-black/40 rounded-xl border border-white/5 group-hover:neon-border-${colorBase} group-hover:scale-110 transition-all`}>
                          <ShieldCheck size={18} className={colorClass} />
                        </div>
                        <div>
                          <h3 className="text-white text-sm font-black uppercase tracking-widest leading-none">{s.symbol}</h3>
                          <span className="text-[8px] font-bold text-zinc-500 tracking-[0.2em]">{s.regime}</span>
                        </div>
                      </div>
                      <div className={`px-2.5 py-1 rounded-lg text-[8px] font-black uppercase border ${bgClass}/10 ${colorClass} ${borderClass} backdrop-blur-md`}>
                        {statusLabel}
                      </div>
                    </div>

                    {/* Price & Change */}
                    <div className="flex justify-between items-end relative z-10 px-1">
                      <div>
                        <span className="text-3xl font-black text-white italic tracking-tighter block leading-none mb-1">
                          {s.price?.toFixed((s.symbol.includes('EURUSD') || s.symbol.includes('GBPUSD')) ? 5 : s.symbol.includes('JPY') ? 3 : 2)}
                        </span>
                        <div className="flex items-center gap-2">
                          <span className={`text-xs font-black italic ${s.daily_change_pct >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                            {s.daily_change_pct >= 0 ? '+' : ''}{s.daily_change_pct?.toFixed(2)}%
                          </span>
                          {(s.floating_pnl !== undefined) && (
                            <span className={`px-2 py-0.5 rounded-md text-[9px] font-black uppercase ${s.floating_pnl > 0 ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : s.floating_pnl < 0 ? 'bg-rose-500/10 text-rose-400 border border-rose-500/20' : 'bg-zinc-800 text-zinc-500 border border-white/5'}`}>
                              Live: {s.floating_pnl > 0 ? '+' : ''}{s.floating_pnl.toFixed(2)}€
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="w-24 h-10">
                        {s.sparkline && s.sparkline.length > 0 && <Sparkline data={s.sparkline} color={colorBase} />}
                      </div>
                    </div>

                    {/* Telemetry Grid (Multi-Timeframe + Daily PnL) */}
                    <div className="grid grid-cols-4 gap-2 bg-black/20 p-3 rounded-2xl border border-white/5 relative z-10">
                      <div className="flex flex-col items-center justify-center border-r border-white/5 px-1 min-w-[65px]">
                        <div className="flex flex-col items-center mb-1">
                          <span className="text-[7px] font-black text-zinc-600 uppercase">PNL 24H</span>
                          <span className={`text-[10px] font-black leading-none ${(s.daily_pnl || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                            {(s.daily_pnl || 0) >= 0 ? '+' : ''}{(s.daily_pnl || 0).toFixed(2)}€
                          </span>
                        </div>
                        <div className="flex flex-col items-center border-t border-white/5 pt-1 w-full">
                          <span className="text-[7px] font-black text-zinc-600 uppercase">TOTAL PNL</span>
                          <span className={`text-[11px] font-black leading-none ${(s.total_pnl || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                            {(s.total_pnl || 0) >= 0 ? '+' : ''}{(s.total_pnl || 0).toFixed(2)}€
                          </span>
                        </div>
                      </div>
                      {['M5', 'M15', 'H1'].map(tf => {
                        const tel = s.telemetry?.[tf] || {};
                        const rsi = tel.rsi;
                        const adx = tel.adx;
                        let rsiColor = 'text-zinc-500';
                        if (rsi >= 70) rsiColor = 'text-rose-400';
                        else if (rsi <= 30) rsiColor = 'text-emerald-400';
                        else if (rsi) rsiColor = 'text-white';

                        return (
                          <div key={tf} className="flex flex-col items-center">
                            <span className="text-[7px] font-black text-zinc-600 mb-1">{tf}</span>
                            <div className="flex flex-col gap-0.5 items-center">
                              <span className={`text-[10px] font-black ${rsiColor}`}>{rsi || '--'}</span>
                              <span className="text-[7px] font-bold text-zinc-500">A:{adx || '--'}</span>
                            </div>
                          </div>
                        );
                      })}
                    </div>

                    {/* Core Strategies Scores */}
                    <div className="flex justify-between items-center gap-1.5 px-3 py-1.5 bg-black/40 rounded-xl border border-white/5 relative z-10">
                      {["PST-RangeBreaker", "PST-PrecisionScalping"].map(strat => {
                        const score = s.factors_map?.[strat]?.score || 0;
                        const label = strat.replace("PST-", "").replace("RangeBreaker", "Range").replace("PrecisionScalping", "Scalp").toUpperCase();
                        return (
                          <div key={strat} className="flex flex-col items-center flex-1 border-r last:border-0 border-white/5">
                            <span className="text-[7px] font-black text-zinc-600 mb-0.5">{label}</span>
                            <span className={`text-[11px] font-black ${score >= 70 ? 'text-indigo-400' : 'text-zinc-500'}`}>{Math.round(score)}</span>
                          </div>
                        );
                      })}
                    </div>

                    {/* Sentiment / Score Bar */}
                    <div className="space-y-1.5 relative z-10 px-1">
                      <div className="flex justify-between items-center text-[8px] font-black uppercase tracking-widest text-zinc-500">
                        <span>
                          {(() => {
                            const factors = s.factors_map ? Object.entries(s.factors_map) : [];
                            if (factors.length === 0) return s.active_strategy || "PST-AUTO";
                            const best = factors.reduce((a, b) => (a[1].score > b[1].score ? a : b));
                            return best[1].score > 0 ? best[0].replace("PST-", "").toUpperCase() : (s.active_strategy || "PST-AUTO");
                          })()}
                        </span>
                        <div className="flex items-center gap-2">
                          {isBlocked && <span className="text-amber-500 text-[7px] border border-amber-500/30 px-1 rounded">GATED</span>}
                          <span className={`text-xs font-black ${colorClass}`}>{Math.round(s.score)}%</span>
                        </div>
                      </div>
                      <div className="h-1.5 w-full bg-zinc-800/50 rounded-full border border-white/5 overflow-hidden">
                        <div
                          className={`h-full ${bgClass} transition-all duration-1000 shadow-[0_0_10px] shadow-${colorBase}-500/50`}
                          style={{ width: `${s.score}%` }}
                        />
                      </div>
                    </div>

                    {/* Quick Actions */}
                    <div className="grid grid-cols-2 gap-3 relative z-10">
                      <button
                        onClick={(e) => { e.stopPropagation(); handleManualTrade(s.symbol, 'BUY') }}
                        disabled={isClosed}
                        className={`py-2 rounded-xl border border-emerald-500/30 bg-emerald-500/5 text-emerald-400 text-[10px] font-black uppercase tracking-widest hover:bg-emerald-500/20 transition-all ${isClosed ? 'opacity-30' : ''}`}
                      >
                        BUY
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleManualTrade(s.symbol, 'SELL') }}
                        disabled={isClosed}
                        className={`py-2 rounded-xl border border-rose-500/30 bg-rose-500/5 text-rose-400 text-[10px] font-black uppercase tracking-widest hover:bg-rose-500/20 transition-all ${isClosed ? 'opacity-30' : ''}`}
                      >
                        SELL
                      </button>
                    </div>

                    {/* Decor Glow */}
                    <div className={`absolute -bottom-10 -left-10 w-32 h-32 blur-3xl rounded-full opacity-10 group-hover:opacity-20 transition-opacity ${bgClass}`} />
                  </div>
                );
              }) : (



                <div className="col-span-full py-24 flex flex-col items-center justify-center bg-zinc-900/10 border-2 border-dashed border-zinc-800 rounded-[3rem] opacity-50 grayscale">



                  <ShieldCheck size={64} className="text-zinc-700 mb-6" />



                  <p className="text-xl font-black text-zinc-600 uppercase tracking-widest">No Authorized Assets Detected</p>



                  <p className="text-sm font-bold text-zinc-800 mt-2 uppercase italic tracking-tighter">Use permissions matrix to activate symbols</p>



                </div>



              )}



            </div>



          </div>



        )}







        {currentView === 'surveillance' && (



          <div className="fixed inset-0 top-20 left-20 flex gap-3 p-3 animate-in fade-in duration-500 overflow-hidden">







            {/* Chart Column € takes all available width */}



            <div className="flex-1 flex flex-col gap-3 min-w-0">



              {/* Chart wrapper */}



              <div className="flex-1 bg-zinc-900/20 border border-zinc-800/50 rounded-[2rem] overflow-hidden shadow-2xl flex flex-col">



                {/* Compact header bar */}



                <div className="flex items-center justify-between px-5 py-3 border-b border-zinc-800/50 flex-shrink-0">



                  <div className="flex items-center gap-3">



                    <div className="w-1 h-6 bg-indigo-500 rounded-full" />






                    <div className="flex items-center gap-1 bg-zinc-900/80 p-1 rounded-xl border border-zinc-800/50 ml-6 mr-4">



                      {['M1', 'M5', 'M15', 'H1', 'D1'].map(tf => (



                        <button



                          key={tf}



                          onClick={() => setSelectedTimeframe(tf)}



                          className={`px-3 py-1 rounded-lg text-[10px] font-black transition-all ${selectedTimeframe === tf



                            ? 'bg-indigo-600 text-white shadow-lg shadow-indigo-500/20'



                            : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800'



                            }`}



                        >



                          {tf}



                        </button>



                      ))}



                    </div>



                    {(() => {



                      const sym = symbols.find(s => s.symbol === selectedSymbol);



                      return sym ? (



                        <span className={`px-2 py-0.5 rounded-full text-[8px] font-black uppercase tracking-widest border ${sym.signal_direction === 'BUY' ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-400' :



                          sym.signal_direction === 'SELL' ? 'bg-rose-500/15 border-rose-500/40 text-rose-400' :



                            'bg-indigo-500/10 border-indigo-500/20 text-indigo-400'



                          }`}>{sym.signal_direction === 'BUY' ? '? BUY' : sym.signal_direction === 'SELL' ? '? SELL' : '? SCAN'}</span>



                      ) : null;



                    })()}



                  </div>



                  <button onClick={() => setCurrentView('home')} className="px-4 py-1.5 bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 rounded-xl text-[9px] font-black uppercase tracking-widest text-zinc-500 transition-all">



                    ? Back



                  </button>



                </div>



                {/* Chart fills remaining space */}



                <div className="flex-1 min-h-0">



                  <TradingChart
                    data={ohlc}
                    symbol={selectedSymbol}
                    activeTrade={trades.find(t => t.symbol === selectedSymbol)}
                    replayTrade={replayTrade}
                  />



                </div>



              </div>







              {/* Strategy Breakdown Panel € below the chart */}



              <div className="bg-[#050505] border border-zinc-800/80 rounded-[2rem] p-6 shadow-2xl flex flex-col gap-5 overflow-visible transition-all duration-500" style={{ height: '320px' }}>



                <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4 shrink-0">



                  <div className="flex items-center gap-3">



                    <div className="w-1.5 h-5 bg-indigo-500 rounded-full shadow-[0_0_10px_rgba(99,102,241,0.5)]" />






                  </div>







                  {/* Strategy Tabs with Scores */}



                  <div className="flex items-center gap-1.5 bg-zinc-900/50 p-1 rounded-xl border border-zinc-800/50 overflow-x-auto no-scrollbar max-w-[70%]">



                    {(() => {



                      const sym = symbols.find(s => s.symbol === selectedSymbol);



                      if (!sym || !sym.factors_map) return null;



                      const allStrategies = Object.keys(sym.factors_map);
                      const strategies = allStrategies.filter(s => {
                        const name = String(s).toUpperCase();
                        if (name.includes('CHANNEL')) return false;
                        return true;
                      });
                      if (strategies.length === 0) return null;







                      return strategies.map(strat => {



                        const stratData = sym.factors_map[strat];



                        const score = stratData?.score || 0;



                        const isActive = activeStrategyTab === strat;







                        return (



                          <button



                            key={strat}



                            onClick={() => setActiveStrategyTab(strat)}



                            className={`px-3 py-1.5 rounded-xl text-[10px] font-black uppercase tracking-widest transition-all flex items-center gap-2.5 whitespace-nowrap border ${isActive



                              ? 'bg-indigo-600 border-indigo-500 text-white shadow-lg shadow-indigo-500/20'



                              : 'bg-zinc-800/40 border-zinc-700/30 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800'



                              }`}



                          >



                            <span>{strat}</span>



                            <div className={`px-1.5 py-0.5 rounded-lg text-[9px] font-black border ${isActive ? 'bg-white/10 border-white/20' : 'bg-black/20 border-white/5'}`}>



                              {score.toFixed(0)}



                            </div>



                          </button>



                        );



                      });



                    })()}



                  </div>



                </div>







                <div className="flex-1 overflow-visible">



                  <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-2.5">



                    {(() => {



                      const sym = symbols.find(s => s.symbol === selectedSymbol);



                      if (!sym) return null;







                      let currentFactors = [];



                      if (sym.factors_map && Object.keys(sym.factors_map).length > 0) {



                        currentFactors = sym.factors_map[activeStrategyTab]?.factors ||



                          (Object.values(sym.factors_map)[0]?.factors || []);



                      } else {



                        currentFactors = sym.factors || [];



                      }







                      if (currentFactors.length === 0) {



                        return (



                          <div className="col-span-full py-8 flex flex-col items-center justify-center gap-2 opacity-20 grayscale">



                            <Activity size={24} className="animate-pulse" />



                            <p className="text-[8px] font-black uppercase tracking-[0.4em] italic">Intelligence Standby</p>



                          </div>



                        );



                      }

                      return currentFactors.slice(0, 10).map((f, i) => (
                        <div key={i} className="bg-zinc-900/40 border border-zinc-800/40 rounded-xl p-2.5 flex flex-col justify-between hover:bg-zinc-800/20 transition-all group relative hover:z-50">
                          <div className="flex justify-between items-start mb-0.5">
                            <div className="flex items-center gap-1.5 relative group/tooltip">
                              <span className="text-[7px] font-black text-zinc-600 uppercase tracking-widest leading-none">{f.k}</span>
                              {f.desc && (
                                <>
                                  <Info size={10} className="text-indigo-500/50 cursor-help hover:text-indigo-400 transition-colors" />
                                  <div className={`absolute ${i < 5 ? 'top-full mt-2' : 'bottom-full mb-2'} left-0 w-64 p-3 bg-[#080808] border border-indigo-500/30 rounded-xl text-[10px] text-zinc-300 opacity-0 group-hover/tooltip:opacity-100 pointer-events-none transition-all duration-300 z-[100] shadow-2xl translate-y-1 group-hover/tooltip:translate-y-0 backdrop-blur-xl`}>
                                    <div className="text-indigo-400 font-black mb-1.5 uppercase tracking-widest text-[8px] border-b border-indigo-500/10 pb-1">{f.k}</div>
                                    <div className="leading-relaxed">{f.desc}</div>
                                  </div>
                                </>
                              )}
                            </div>
                            <span className={`text-[9px] font-black italic tracking-tighter ${f.score > 0 ? 'text-emerald-500' : f.score < 0 ? 'text-rose-500' : 'text-zinc-400'}`}>
                              {f.score > 0 ? '+' : ''}{f.score.toFixed(0)}
                            </span>
                          </div>
                          <p className="text-[10px] font-black text-zinc-300 tracking-tighter uppercase line-clamp-1" title={f.v}>{f.v}</p>
                        </div>
                      ));
                    })()}



                  </div>



                </div>



              </div>



            </div>







            {/* Right Panel Column */}



            < div className="w-72 flex-shrink-0 flex flex-col gap-3 overflow-y-auto pb-3" >







              {/* Sentinel Intel Panel */}



              {



                (() => {



                  const sym = symbols.find(s => s.symbol === selectedSymbol);



                  const activeTrade = trades.find(t => t.symbol === selectedSymbol);



                  if (!sym) return null;



                  const hasSignal = sym.signal_direction && sym.signal_direction !== 'NONE';



                  const isBuy = sym.signal_direction === 'BUY';



                  const regimeColors = {



                    'TREND': 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',



                    'VOLATILE': 'text-amber-400 bg-amber-500/10 border-amber-500/20',



                    'RANGE': 'text-blue-400 bg-blue-500/10 border-blue-500/20',



                  };



                  const regimeClass = regimeColors[sym.regime] || 'text-zinc-400 bg-zinc-800 border-zinc-700';



                  const score = sym.score || 0;



                  const circumference = 2 * Math.PI * 36;



                  const dashOffset = circumference - (score / 100) * circumference;



                  return (



                    <div className="bg-[#050505] border border-zinc-800/80 rounded-[2rem] p-4 shadow-2xl space-y-4 flex-shrink-0">



                      <div className="flex items-center justify-between">



                        <div>



                          <p className="text-[9px] font-black text-zinc-600 uppercase tracking-[0.3em]">Sentinel Intel</p>



                          <h3 className="text-base font-black text-white italic tracking-tighter mt-0.5">{selectedSymbol}</h3>



                        </div>



                        <div className={`px-2.5 py-1 rounded-full text-[8px] font-black uppercase tracking-widest border ${hasSignal ? (isBuy ? 'bg-emerald-500/15 border-emerald-500/40 text-emerald-400' : 'bg-rose-500/15 border-rose-500/40 text-rose-400') : 'bg-indigo-500/10 border-indigo-500/20 text-indigo-400'}`}>



                          {hasSignal ? (isBuy ? '? BUY' : '? SELL') : '? SCAN'}



                        </div>



                      </div>



                      <div className="flex items-center gap-3">



                        <div className="relative w-16 h-16 flex-shrink-0">



                          <svg viewBox="0 0 80 80" className="w-full h-full -rotate-90">



                            <circle cx="40" cy="40" r="36" fill="none" stroke="#18181b" strokeWidth="7" />



                            <circle cx="40" cy="40" r="36" fill="none" stroke={hasSignal ? (isBuy ? '#10b981' : '#f43f5e') : '#6366f1'} strokeWidth="7" strokeLinecap="round" strokeDasharray={circumference} strokeDashoffset={dashOffset} className="transition-all duration-1000" />



                          </svg>



                          <div className="absolute inset-0 flex flex-col items-center justify-center">



                            <span className="text-sm font-black text-white leading-none">{Math.round(score)}</span>



                            <span className="text-[7px] font-bold text-zinc-600 uppercase">score</span>



                          </div>



                        </div>



                        <div className="flex-1 min-w-0">



                          <p className="text-base font-black text-white italic tracking-tighter leading-none truncate">



                            {sym.price?.toFixed((selectedSymbol.includes('EURUSD') || selectedSymbol.includes('GBPUSD')) ? 5 : selectedSymbol.includes('JPY') ? 3 : 2)}



                          </p>



                          <p className={`text-sm font-black mt-1 ${sym.daily_change_pct >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>



                            {sym.daily_change_pct >= 0 ? '+' : ''}{sym.daily_change_pct?.toFixed(2)}%



                          </p>



                          <span className={`inline-block mt-1 px-2 py-0.5 rounded-full text-[7px] font-black uppercase tracking-widest border ${regimeClass}`}>{sym.regime || 'SCAN'}</span>



                        </div>



                      </div>



                      {activeTrade && (



                        <div className={`p-2.5 rounded-xl border flex justify-between items-center ${activeTrade.profit >= 0 ? 'bg-emerald-500/5 border-emerald-500/15' : 'bg-rose-500/5 border-rose-500/15'}`}>



                          <div>



                            <p className="text-[7px] font-black text-zinc-600 uppercase tracking-widest">Active PnL</p>



                            <p className={`text-sm font-black italic mt-0.5 ${activeTrade.profit >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>{activeTrade.profit >= 0 ? '+' : ''}{activeTrade.profit?.toFixed(2)}€</p>



                          </div>



                          <div className="text-right">



                            <p className="text-[7px] font-black text-zinc-600 uppercase tracking-widest">Vol</p>



                            <p className="text-sm font-black text-white">{activeTrade.volume}L</p>



                          </div>
                          <button
                            onClick={(e) => { e.stopPropagation(); handleCloseTrade(activeTrade.ticket) }}
                            title="Cerrar posición"
                            className="ml-1 px-2 py-1.5 rounded-lg bg-rose-600/20 border border-rose-500/40 text-rose-400 text-[9px] font-black uppercase tracking-wide hover:bg-rose-600/40 hover:text-rose-300 transition-all active:scale-95"
                          >
                            × Close
                          </button>



                        </div>



                      )}



                    </div>



                  );



                })()



              }







              {/* Manual Execution Node */}



              <div className="bg-[#050505] border border-zinc-800/80 rounded-[2rem] p-4 shadow-2xl flex flex-col gap-4 flex-shrink-0">



                {/* Smart Mode Toggle */}



                <div className="flex items-center justify-between p-3 bg-indigo-500/5 border border-indigo-500/20 rounded-xl">



                  <div className="flex items-center gap-2">



                    <div className={`p-1.5 rounded-lg transition-all ${isSmartMode ? 'bg-indigo-500 text-white shadow-[0_0_20px_rgba(99,102,241,0.5)]' : 'bg-zinc-800 text-zinc-600'}`}>



                      <ShieldCheck size={14} />



                    </div>



                    <div>



                      <p className="text-[9px] font-black text-white uppercase tracking-widest leading-none italic">Smart Entry</p>



                      <p className="text-[7px] font-bold text-zinc-500 uppercase tracking-tighter leading-none mt-0.5">Auto SL/TP & Risk</p>



                    </div>



                  </div>



                  <button onClick={() => setIsSmartMode(!isSmartMode)} className={`w-10 h-5 rounded-full relative transition-all duration-500 ${isSmartMode ? 'bg-indigo-600' : 'bg-zinc-800'}`}>



                    <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full transition-all duration-500 ${isSmartMode ? 'right-0.5' : 'left-0.5'}`} />



                  </button>



                </div>
                {/* Smart Risk Configuration (FASE 72) */}
                <AnimatePresence>
                  {isSmartMode && (
                    <motion.div 
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      className="overflow-hidden space-y-3"
                    >
                      <div className="grid grid-cols-2 gap-2">
                        <div className="space-y-1.5 p-2.5 bg-zinc-900/30 border border-zinc-800 rounded-xl">
                          <p className="text-[8px] font-black text-zinc-500 uppercase tracking-widest">Riesgo (€)</p>
                          <div className="flex items-center gap-2">
                            <Zap size={10} className="text-amber-500" />
                            <input 
                              type="number" 
                              value={riskAmount} 
                              onChange={(e) => setRiskAmount(Number(e.target.value))}
                              className="w-full bg-transparent text-sm font-black text-white italic outline-none border-none p-0"
                            />
                          </div>
                        </div>
                        <div className="space-y-1.5 p-2.5 bg-zinc-900/30 border border-zinc-800 rounded-xl">
                          <p className="text-[8px] font-black text-zinc-500 uppercase tracking-widest">RR Ratio</p>
                          <div className="flex items-center gap-2">
                            <Target size={10} className="text-indigo-400" />
                            <input 
                              type="number" 
                              step="0.1"
                              value={rrRatio} 
                              onChange={(e) => setRrRatio(Number(e.target.value))}
                              className="w-full bg-transparent text-sm font-black text-white italic outline-none border-none p-0"
                            />
                          </div>
                        </div>
                      </div>

                      <div className="space-y-1.5 p-2.5 bg-zinc-900/30 border border-zinc-800 rounded-xl">
                        <p className="text-[8px] font-black text-zinc-500 uppercase tracking-widest">Stop Loss Price (Optional)</p>
                        <div className="flex items-center gap-2">
                          <Shield size={10} className="text-rose-500" />
                          <input 
                            type="number" 
                            step="0.00001"
                            placeholder="Vacío = Estructural"
                            value={slPrice} 
                            onChange={(e) => setSlPrice(e.target.value)}
                            className="w-full bg-transparent text-xs font-bold text-white outline-none border-none p-0 placeholder:text-zinc-700 placeholder:italic"
                          />
                        </div>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>

                {/* Volume Selector */}



                <div className={`space-y-2 transition-all duration-500 ${isSmartMode ? 'opacity-30 grayscale pointer-events-none scale-95' : 'opacity-100'}`}>



                  <span className="text-[9px] font-black text-zinc-600 uppercase tracking-widest">Volume (Lots)</span>



                  <div className="flex items-center justify-between bg-zinc-900/40 p-2 border border-zinc-800 rounded-xl">



                    <button onClick={() => setManualLot(Math.max(0.01, manualLot - 0.01))} className="p-1.5 hover:bg-zinc-800 rounded-lg transition-all text-zinc-400 active:scale-90"><ChevronDown size={16} /></button>



                    <span className="text-xl font-black font-mono text-white italic">{manualLot.toFixed(2)}</span>



                    <button onClick={() => setManualLot(manualLot + 0.01)} className="p-1.5 hover:bg-zinc-800 rounded-lg transition-all text-zinc-400 active:scale-90"><ChevronUp size={16} /></button>



                  </div>



                  <div className="grid grid-cols-4 gap-1.5">



                    {[0.01, 0.1, 0.5, 1.0].map(v => (



                      <button key={v} onClick={() => setManualLot(v)} className={`py-1.5 rounded-lg text-[9px] font-black border transition-all ${manualLot === v ? 'bg-indigo-500/20 border-indigo-500 text-indigo-400' : 'bg-zinc-900 border-zinc-800 text-zinc-600 hover:text-white'}`}>{v.toFixed(2)}</button>



                    ))}



                  </div>



                </div>







                {/* Premium Execution Buttons */}



                <div className="space-y-2">



                  <button disabled={isExecuting} onClick={() => handleManualTrade(selectedSymbol, 'BUY')} className={`w-full py-4 rounded-xl font-black tracking-[0.15em] transition-all flex items-center justify-center gap-2 active:scale-[0.97] disabled:opacity-60 group text-sm ${isSmartMode ? 'bg-gradient-to-r from-indigo-600 to-violet-600 text-white shadow-[0_4px_20px_rgba(99,102,241,0.4)] hover:shadow-[0_4px_30px_rgba(99,102,241,0.6)] border border-indigo-500/50' : 'bg-gradient-to-r from-emerald-500 to-green-400 text-white shadow-[0_4px_20px_rgba(16,185,129,0.35)] hover:shadow-[0_4px_30px_rgba(16,185,129,0.55)] border border-emerald-400/50'}`}>



                    {isExecuting ? <><RefreshCw size={14} className="animate-spin" /> EXECUTING...</> : <><TrendingUp size={14} className="group-hover:scale-110 transition-transform" /> {isSmartMode ? 'SMART BUY' : 'MARKET BUY'}</>}



                  </button>



                  <button disabled={isExecuting} onClick={() => handleManualTrade(selectedSymbol, 'SELL')} className={`w-full py-4 rounded-xl font-black tracking-[0.15em] transition-all flex items-center justify-center gap-2 active:scale-[0.97] disabled:opacity-60 group text-sm ${isSmartMode ? 'bg-gradient-to-r from-indigo-600 to-violet-600 text-white shadow-[0_4px_20px_rgba(99,102,241,0.4)] hover:shadow-[0_4px_30px_rgba(99,102,241,0.6)] border border-indigo-500/50' : 'bg-gradient-to-r from-rose-600 to-red-500 text-white shadow-[0_4px_20px_rgba(244,63,94,0.35)] hover:shadow-[0_4px_30px_rgba(244,63,94,0.55)] border border-rose-500/50'}`}>



                    {isExecuting ? <><RefreshCw size={14} className="animate-spin" /> EXECUTING...</> : <><TrendingDown size={14} className="group-hover:scale-110 transition-transform" /> {isSmartMode ? 'SMART SELL' : 'MARKET SELL'}</>}



                  </button>



                  <button disabled={isExecuting} onClick={handlePurgeSymbol} className="w-full py-2.5 bg-zinc-900 hover:bg-rose-950/50 text-zinc-500 hover:text-rose-400 border border-zinc-800 hover:border-rose-800/50 rounded-xl font-black text-[9px] tracking-[0.3em] transition-all flex items-center justify-center gap-2 active:scale-95 disabled:opacity-50">



                    <Trash2 size={12} /> PURGE {selectedSymbol}



                  </button>



                </div>



              </div>







              {/* Multi-TF Telemetry */}



              {



                telemetry && (



                  <div className="bg-[#050505] border border-zinc-800/80 rounded-[2rem] p-4 shadow-2xl flex-shrink-0">



                    <div className="flex items-center gap-2 mb-3">



                      <div className="w-1 h-3 bg-indigo-500 rounded-full" />



                      <p className="text-[8px] font-black text-zinc-600 uppercase tracking-[0.25em]">Multi-TF Radar</p>



                    </div>



                    <div className="grid grid-cols-2 gap-2">



                      {['M1', 'M5', 'M15', 'H1'].map(tf => {



                        const d = telemetry[tf] || {};



                        const rsi = d.rsi, adx = d.adx;



                        const rsiColor = rsi === null ? 'text-zinc-700' : rsi > 70 ? 'text-rose-400' : rsi < 30 ? 'text-emerald-400' : 'text-zinc-300';



                        const rsiBg = rsi === null ? 'bg-zinc-900 border-zinc-800' : rsi > 70 ? 'bg-rose-500/10 border-rose-500/20' : rsi < 30 ? 'bg-emerald-500/10 border-emerald-500/20' : 'bg-zinc-900 border-zinc-800';



                        const adxColor = adx === null ? 'text-zinc-700' : adx > 25 ? 'text-amber-400' : 'text-zinc-500';



                        return (



                          <div key={tf} className={`p-2.5 rounded-xl border flex flex-col gap-1.5 ${rsiBg}`}>



                            <span className="text-[9px] font-black text-zinc-600 uppercase tracking-widest">{tf}</span>



                            <div className="flex justify-between items-center">



                              <p className="text-[7px] font-bold text-zinc-700 uppercase">RSI</p>



                              <p className={`text-sm font-black ${rsiColor}`}>{rsi !== null ? rsi : '€'}</p>



                            </div>



                            <div className="flex justify-between items-center">



                              <p className="text-[7px] font-bold text-zinc-700 uppercase">ADX</p>



                              <p className={`text-sm font-black ${adxColor}`}>{adx !== null ? adx : '€'}</p>



                            </div>



                          </div>



                        );



                      })}



                    </div>



                  </div>



                )



              }



            </div>



          </div>



        )
        }







        {



          currentView === 'terminal' && (
            <div className="w-full animate-in slide-in-from-right-4 duration-500 pr-6">
              <div className="space-y-6">
                <section>
                  <LiveConsole logs={logs} />
                </section>
                <TradeTerminalTable 
                  trades={trades}
                  historyTrades={historyTrades}
                  filter={terminalTradesFilter}
                  setFilter={setTerminalTradesFilter}
                  onSymbolClick={(sym) => { setSelectedSymbol(sym); setCurrentView('surveillance'); }}
                />
              </div>
            </div>
          )
        }







        {/* --- NEW: HISTORY VIEW --- */}











        {



          currentView === 'config' && (



            <div className="w-full px-6 animate-in fade-in slide-in-from-bottom-2 duration-500">



              {/* Header */}



              <div className="flex items-center gap-6 mb-8">



                <div className="w-16 h-16 bg-indigo-500/10 rounded-[1.5rem] flex items-center justify-center border border-indigo-500/20 shadow-[0_0_40px_rgba(99,102,241,0.15)]">



                  <Settings className="text-indigo-500" size={32} />



                </div>



                <div>



                  <h2 className="text-3xl font-black text-white tracking-widest uppercase italic leading-none">Matrix Editor</h2>



                  <p className="text-[9px] text-zinc-600 font-bold uppercase tracking-[0.35em] mt-1.5">Control total de parámetros por activo · Persistencia en tiempo real</p>



                </div>



                <div className="ml-auto flex items-center gap-2 px-4 py-2 bg-emerald-500/5 border border-emerald-500/15 rounded-full">



                  <div className="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-pulse" />



                  <span className="text-[9px] font-black text-emerald-400 uppercase tracking-widest">Live Sync</span>



                </div>



              </div>







              {/* Legend */}



              <div className="flex gap-4 mb-6">



                {[



                  { label: 'Lot Size', desc: 'Volumen base del bot', color: 'indigo' },



                  { label: 'SL/ATR', desc: 'Multiplicador de Stop Loss', color: 'rose' },



                  { label: 'TP/ATR', desc: 'Multiplicador de Take Profit', color: 'emerald' },



                  { label: 'Min Score', desc: 'Umbral de señal mínima', color: 'amber' },



                ].map(l => (



                  <div key={l.label} className={`flex items-center gap-2 px-3 py-1.5 bg-${l.color}-500/5 border border-${l.color}-500/20 rounded-xl`}>



                    <div className={`w-2 h-2 bg-${l.color}-500 rounded-full`} />



                    <div>



                      <p className={`text-[9px] font-black text-${l.color}-400 uppercase tracking-widest leading-none`}>{l.label}</p>



                      <p className="text-[7px] text-zinc-600 font-bold uppercase tracking-widest">{l.desc}</p>



                    </div>



                  </div>



                ))}



              </div>







              {/* Symbol Cards Grid */}



              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-5">



                {(matrixData.length > 0 ? matrixData : symbols).map(s => {
                  const params = editingParams[s.symbol] || {
                    lot_size: s.lot_size ?? 0.01,
                    sl_mult: s.sl_mult ?? 2.5,
                    tp_mult: s.tp_mult ?? 6.0,
                    score_threshold: s.score_threshold && s.score_threshold >= 80 ? s.score_threshold : 80.0,
                    risk_mode: s.risk_mode ?? 'LOTS',
                    risk_value: s.risk_value ?? 0.01
                  }

                  const isSaving = savingSymbol === s.symbol
                  // Asegurar que mostramos todas las estrategias globales si el mapa está vacío
                  const symbolStrats = s.factors_map || {};

                  // Mapeo de nombres limpios y estratégicos
                  const STRAT_NAME_MAP = {
                    "PST-RangeBreaker":      "Range Breaker",
                    "PST-PrecisionScalping": "Precision Scalp",
                  };

                  // Estrategias CORE activas (v3.0)
                  const CORE_STRATS = ["PST-RangeBreaker", "PST-PrecisionScalping"];
                  const knownStrategies = Array.from(new Set([
                    ...strategies,
                    ...matrixData.flatMap(m => Object.keys(m.factors_map || {})),
                    ...symbols.flatMap(sym => Object.keys(sym.factors_map || {}))
                  ]))
                    .filter(st => CORE_STRATS.includes(st))
                    .sort();

                  const allStratNames = CORE_STRATS; // Forzamos a que siempre aparezcan estas dos

                  return (
                    <div
                      key={s.symbol}
                      onClick={() => setConfigModal(s.symbol)}
                      className={`group bg-[#050505] border rounded-[1.5rem] p-4 flex flex-col gap-3 cursor-pointer transition-all duration-300 hover:border-indigo-500/60 hover:shadow-[0_0_30px_rgba(99,102,241,0.15)] ${s.is_active ? 'border-indigo-500/40' : 'border-zinc-800/40 opacity-60'}`}
                    >
                      {/* Header: símbolo + tipo + toggle rápido */}
                      <div className="flex justify-between items-center">
                        <div className="flex items-center gap-2">
                          <Activity size={16} className={s.is_active ? 'text-indigo-400 animate-pulse' : 'text-zinc-600'} />
                          <h3 className="text-base font-black text-white italic tracking-tighter">{s.symbol}</h3>
                          {s.type ? <span className="text-[7px] font-black text-zinc-600 uppercase tracking-widest px-1.5 py-0.5 bg-white/5 rounded">{s.type}</span> : null}
                        </div>
                        <button
                          onClick={(e) => { e.stopPropagation(); toggleSymbolStatus(s.symbol, s.is_active); }}
                          className={`w-9 h-4.5 rounded-full relative transition-all ${s.is_active ? 'bg-indigo-600' : 'bg-zinc-800'}`}
                        >
                          <div className={`absolute top-0.5 w-3.5 h-3.5 rounded-full bg-white transition-all ${s.is_active ? 'right-0.5' : 'left-0.5'}`} />
                        </button>
                      </div>

                      {/* Resumen por estrategia (solo lectura) */}
                      <div className="flex flex-col gap-2">
                        {allStratNames.map(strat => {
                          const stratData = symbolStrats[strat] || {};
                          // v2.6.7: antes, si is_active era 0 (deshabilitado a propósito, p.ej.
                          // RangeBreaker en los símbolos nuevos sin edge) el fallback lo mostraba
                          // "ON" igualmente para RangeBreaker. Ahora refleja el valor real de BBDD.
                          const isEnabled = !!stratData.is_active;
                          const displayName = STRAT_NAME_MAP[strat] || strat.replace("PST-", "").replace(/-/g, " ");
                          let noiseMode = null;
                          let vwapExitOff = false;
                          // Para PrecisionScalping el umbral real de entrada es entry_threshold
                          // (del filter_profile), no la columna score_threshold. Mostramos ese.
                          let scoreVal = stratData.score_threshold ?? '—';
                          if (strat === "PST-PrecisionScalping" && stratData.filter_profile) {
                            try {
                              const fp = JSON.parse(stratData.filter_profile);
                              noiseMode = fp.noise_mode;
                              vwapExitOff = String(fp.vwap_exit || 'on').toLowerCase() === 'off';
                              scoreVal = fp.entry_threshold ?? '—';   // '—' = usa el default de la estrategia
                            } catch { /* noop */ }
                          }
                          const stats = [
                            { label: 'RISK', val: `${stratData.risk_value ?? '—'}${stratData.risk_mode === 'MONEY' ? '€' : stratData.risk_mode === 'PCT' ? '%' : ''}`, color: 'indigo' },
                            { label: 'SL', val: stratData.sl_mult ?? '—', color: 'rose' },
                            { label: 'TP', val: strat === 'PST-PrecisionScalping' ? 'TEC' : (stratData.tp_mult ?? '—'), color: 'emerald' },
                            { label: 'SCORE', val: scoreVal, color: 'amber' },
                            ...(noiseMode ? [{ label: 'NOISE', val: String(noiseMode).toUpperCase(), color: 'indigo' }] : []),
                            ...(vwapExitOff ? [{ label: 'VWAP-EXIT', val: 'OFF', color: 'rose' }] : []),
                          ];
                          return (
                            <div key={strat} className={`flex flex-col gap-1.5 p-2.5 rounded-xl border ${isEnabled ? 'bg-indigo-500/[0.06] border-indigo-500/25' : 'bg-transparent border-zinc-800/40 opacity-50'}`}>
                              <div className="flex items-center justify-between">
                                <span className={`text-[10px] font-black uppercase ${isEnabled ? 'text-white' : 'text-zinc-500'}`}>{displayName}</span>
                                <span className={`text-[7px] font-black uppercase px-1.5 py-0.5 rounded-full ${isEnabled ? 'bg-emerald-500/15 text-emerald-400' : 'bg-zinc-800 text-zinc-600'}`}>{isEnabled ? 'ON' : 'OFF'}</span>
                              </div>
                              <div className="flex items-center gap-1.5 flex-wrap">
                                {stats.map(st => (
                                  <div key={st.label} className="flex items-center gap-1 bg-black/30 px-1.5 py-1 rounded-md border border-white/5">
                                    <span className="text-[7px] font-black text-zinc-600 uppercase">{st.label}</span>
                                    <span className={`text-[10px] font-black text-${st.color}-400`}>{st.val}</span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          );
                        })}
                      </div>

                      {/* Pie: abrir configuración */}
                      <div className="mt-auto flex items-center justify-center gap-2 py-2 rounded-xl bg-white/[0.03] border border-white/5 text-zinc-500 group-hover:text-indigo-400 group-hover:border-indigo-500/20 transition-all">
                        <Settings size={12} />
                        <span className="text-[9px] font-black uppercase tracking-widest">Configurar</span>
                      </div>
                    </div>
                  )
                })}



              </div>







              {/* Footer Info */}



              <div className="mt-8 p-4 bg-zinc-900/30 border border-zinc-800/50 rounded-2xl flex items-start gap-3">



                <div className="p-1.5 bg-indigo-500/10 rounded-lg mt-0.5"><Info size={14} className="text-indigo-400" /></div>



                <div>



                  <p className="text-[9px] font-black text-zinc-400 uppercase tracking-widest">Los cambios se aplican en el próximo ciclo del bot (~10s)</p>



                  <p className="text-[8px] text-zinc-600 font-bold uppercase tracking-wider mt-0.5">El orquestador lee `lot_size`, `sl_mult` y `tp_mult` antes de cada operación. `score_threshold` filtra señales de baja calidad.</p>



                </div>



              </div>



            </div>



          )



        }
        {/* Mobile Bottom Nav */}
        <div className="md:hidden fixed bottom-0 left-0 w-full bg-[#050505]/90 backdrop-blur-xl border-t border-zinc-800 flex justify-between px-6 py-4 z-50 shadow-[0_-10px_40px_rgba(0,0,0,0.8)]">
          <NavItem icon={<LayoutGrid size={24} />} isActive={currentView === 'home'} onClick={() => setCurrentView('home')} />
          <NavItem icon={<Terminal size={24} />} isActive={currentView === 'surveillance'} onClick={() => setCurrentView('surveillance')} />
          <NavItem icon={<History size={24} />} isActive={currentView === 'history'} onClick={() => setCurrentView('history')} />
          <NavItem icon={<Settings size={24} />} isActive={currentView === 'config'} onClick={() => setCurrentView('config')} />
          <NavItem icon={<Target size={24} />} isActive={currentView === 'matrix'} onClick={() => setCurrentView('matrix')} />
        </div>








        {/* Sentinel Journal Modal (Fase 53) */}
        <AnimatePresence>
          {journalSnapshot && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-[100] flex items-center justify-center p-6 bg-[#020202]/90 backdrop-blur-md"
            >
              <motion.div
                initial={{ scale: 0.9, opacity: 0, y: 30 }}
                animate={{ scale: 1, opacity: 1, y: 0 }}
                exit={{ scale: 0.9, opacity: 0, y: 30 }}
                className="w-full max-w-5xl bg-[#050505] border border-white/10 rounded-[3rem] shadow-[0_0_100px_rgba(0,0,0,0.8)] overflow-hidden flex flex-col max-h-[90vh]"
              >
                {/* Modal Header */}
                <div className="px-10 py-8 border-b border-white/5 flex items-center justify-between bg-white/[0.02]">
                  <div>
                    <h2 className="text-2xl font-black text-white italic tracking-tighter uppercase mb-1">
                      Tactical Snapshot: {journalSnapshot.symbol} #{journalSnapshot.ticket}
                    </h2>
                    <div className="flex items-center gap-3">
                      <span className="text-zinc-600 font-black font-mono text-[9px] tracking-[0.3em] uppercase">Entry Surveillance Archive</span>
                      <div className="w-1.5 h-1.5 bg-indigo-500 rounded-full animate-pulse" />
                      <span className="text-zinc-500 font-bold font-mono text-[9px] tracking-widest uppercase">{journalSnapshot.timestamp}</span>
                    </div>
                  </div>
                  <button
                    onClick={() => setJournalSnapshot(null)}
                    className="p-4 bg-zinc-900 border border-white/5 rounded-2xl text-zinc-500 hover:text-white transition-all shadow-lg hover:border-rose-500/30"
                  >
                    <X size={24} />
                  </button>
                </div>

                {/* Modal Content - Chart */}
                <div className="flex-1 p-10 overflow-y-auto">
                  <div className="bg-black/40 border border-white/5 rounded-[2rem] p-6 mb-8 h-[450px]">
                    <h3 className="text-xs font-black text-zinc-500 uppercase tracking-widest mb-6 px-4">Entry M15 Context (50 Bars)</h3>
                    <ResponsiveContainer width="100%" height="90%">
                      <AreaChart data={journalSnapshot.ohlc}>
                        <defs>
                          <linearGradient id="colorOhlc" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor="#6366f1" stopOpacity={0.2} />
                            <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                          </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" stroke="#ffffff05" vertical={false} />
                        <XAxis dataKey="time" hide />
                        <YAxis domain={['auto', 'auto']} stroke="#ffffff15" fontSize={9} />
                        <Tooltip
                          contentStyle={{ backgroundColor: '#0a0a0a', border: '1px solid #ffffff10', borderRadius: '1rem' }}
                        />
                        <Area
                          type="monotone"
                          dataKey="close"
                          stroke="#6366f1"
                          strokeWidth={2}
                          fillOpacity={1}
                          fill="url(#colorOhlc)"
                          animationDuration={1500}
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>

                  <div className="grid grid-cols-3 gap-6">
                    <div className="bg-white/[0.02] border border-white/5 p-6 rounded-3xl">
                      <p className="text-[10px] font-black text-zinc-600 uppercase tracking-[0.2em] mb-3">Snapshot Security</p>
                      <p className="text-[11px] font-medium text-zinc-400 leading-relaxed uppercase italic">
                        The integrity of this data node is verified. Entry context stored at the exact moment of execution on decentralized Sentinel engine.
                      </p>
                    </div>
                    <div className="col-span-2 bg-indigo-500/5 border border-indigo-500/20 p-6 rounded-3xl flex items-center gap-6">
                      <div className="p-4 bg-indigo-500/10 rounded-2xl text-indigo-400">
                        <ShieldCheck size={32} />
                      </div>
                      <div>
                        <h4 className="text-sm font-black text-white uppercase tracking-widest mb-1 italic">Surveillance Verified</h4>
                        <p className="text-[10px] text-indigo-300 font-bold uppercase tracking-wider opacity-60">Verified by PST-Core surveillance module.</p>
                      </div>
                    </div>
                  </div>
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Config Modal (Fase 3) — configuración por símbolo del Matrix Editor */}
        <AnimatePresence>
          {configModal && (() => {
            const symData = (matrixData.length > 0 ? matrixData : symbols).find(m => m.symbol === configModal)
            if (!symData) return null
            return (
              <ConfigModal
                symbol={configModal}
                symData={symData}
                onClose={() => setConfigModal(null)}
                onSaved={fetchMatrix}
                addToast={addToast}
              />
            )
          })()}
        </AnimatePresence>

      </main >



    </div >



  )



}

export default App
