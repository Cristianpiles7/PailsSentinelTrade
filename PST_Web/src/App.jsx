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



const EquityCurve = ({ data }) => {
  if (!data || data.length < 2) return (



    <div className="h-full flex items-center justify-center text-zinc-700 font-black uppercase text-[10px] italic">



      Insufficient Data for Curve Analysis



    </div>



  );







  const padding = 20;



  const width = 800;



  const height = 200;







  const balances = data.map(d => d.balance);



  const min = Math.min(...balances);



  const max = Math.max(...balances);



  const range = max - min || 1;







  const points = data.map((d, i) => {



    const x = padding + (i * (width - 2 * padding)) / (data.length - 1);



    const y = height - padding - ((d.balance - min) * (height - 2 * padding)) / range;



    return `${x},${y}`;

  }).join(' ');








  return (



    <div className="w-full h-full p-4">



      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-full drop-shadow-[0_0_15px_rgba(99,102,241,0.3)]">



        <defs>



          <linearGradient id="curveGradient" x1="0" y1="0" x2="0" y2="1">



            <stop offset="0%" stopColor="#4f46e5" stopOpacity="0.5" />



            <stop offset="100%" stopColor="#4f46e5" stopOpacity="0" />



          </linearGradient>



        </defs>



        <path



          d={`M ${points} L ${width - padding},${height} L ${padding},${height} Z`}



          fill="url(#curveGradient)"



        />



        <polyline



          fill="none"



          stroke="#6366f1"



          strokeWidth="3"



          strokeLinecap="round"



          strokeLinejoin="round"



          points={points}



        />



        {/* Puntos de control visual */}



        {data.map((d, i) => {



          if (i % Math.ceil(data.length / 10) !== 0 && i !== data.length - 1) return null;



          const x = padding + (i * (width - 2 * padding)) / (data.length - 1);



          const y = height - padding - ((d.balance - min) * (height - 2 * padding)) / range;



          return (



            <g key={i}>



              <circle cx={x} cy={y} r="3" fill="#6366f1" className="animate-pulse" />



              <text x={x} y={y - 8} fontSize="8" fill="#64748b" fontWeight="black" textAnchor="middle">{d.balance.toFixed(0)}</text>



            </g>



          );



        })}



      </svg>



    </div>



  );
};







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



  const [isExecuting, setIsExecuting] = useState(false)



  const [isAutoTrading, setIsAutoTrading] = useState(true)





  const [historyTrades, setHistoryTrades] = useState([])



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
      setToasts(prev => prev.filter(t => t.id !== id))
    }, 5000)
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
      const [perfRes, eqRes, stratRes] = await Promise.all([
        api.get(`${API_BASE}/performance`),
        api.get(`${API_BASE}/performance/equity`),
        api.get(`${API_BASE}/performance/strategies`)
      ])
      setPerfData(perfRes.data)
      setEquityCurve(eqRes.data || [])
      setStratPerf(stratRes.data || [])
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
        is_smart: isSmartMode
      })
      addToast(`${action} ${symbol} executed`, 'success')
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



          <Toast key={t.id} {...t} />



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



                <span className="text-zinc-600 font-black font-mono text-[9px] tracking-[0.3em] uppercase">PST-CORE: V8.3</span>



                <div className="w-1.5 h-1.5 bg-indigo-500 rounded-full animate-pulse shadow-[0_0_10px_rgba(99,102,241,0.5)]" />



                <span className="text-zinc-500 font-bold font-mono text-[9px] tracking-widest uppercase text-indigo-400/80">



                  {currentView === 'surveillance' && 'Real-time Charting & Execution Hub'}



                  {currentView === 'config' && 'Asset Configuration & Global Toggles'}



                </span>



              </div>



            </div>



          </header>



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
                          <div className={`px-3 py-1 rounded-full text-[10px] font-black mb-2 inline-block ${data.win_rate >= 50 ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'}`}>
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
                            <span className="text-[8px] font-bold text-zinc-500 uppercase tracking-tighter">TIC: {t.ticket}</span>
                          </div>
                        </div>
                        <div className="text-right">
                          <span className={`text-base font-black italic tracking-tighter ${t.profit >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                            {t.profit >= 0 ? '+' : ''}{t.profit.toFixed(2)}€
                          </span>
                          <div className="text-[7px] font-black text-zinc-600 uppercase">Yield</div>
                        </div>
                      </div>

                      <div className="flex items-center justify-between gap-2 border-t border-white/5 pt-3 mt-1">
                        <div className="flex flex-col">
                          <span className="text-[7px] font-black text-zinc-600 uppercase">Strategy</span>
                          <span className="text-[9px] font-black text-indigo-400 uppercase truncate max-w-[120px]">{t.strategy}</span>
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

                // Sentinel Heatmap Logic (PnL 24h)
                const hasProfit = (s.profit_24h || 0) > 0;
                const hasLoss = (s.profit_24h || 0) < 0;
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
                        <div className="flex-1 hidden xl:flex items-center justify-around gap-2 px-2 border-l border-white/5">
                          {['M5', 'M15', 'H1'].map(tf => {
                            const tel = s.telemetry?.[tf] || {};
                            return (
                              <div key={tf} className="flex flex-col items-center">
                                <span className="text-[6px] font-bold text-zinc-600 leading-none">{tf}</span>
                                <span className={`text-[9px] font-black ${tel.rsi >= 70 ? 'text-rose-400' : tel.rsi <= 30 ? 'text-emerald-400' : 'text-zinc-300'}`}>{tel.rsi || '--'}</span>
                              </div>
                            );
                          })}
                        </div>
                        <div className="flex flex-col items-center min-w-[60px] border-l border-white/5 pl-4">
                          <span className="text-[6px] font-bold text-zinc-600 mb-0.5 uppercase">PnL 24H</span>
                          <span className={`text-[10px] font-black ${(s.profit_24h || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                            {(s.profit_24h || 0) >= 0 ? '+' : ''}{(s.profit_24h || 0).toFixed(2)}€
                          </span>
                        </div>
                      </div>

                      <div className="flex items-center gap-2">
                        <div className="flex flex-col items-end mr-2">
                          <div className="flex items-center gap-1.5">
                            {isBlocked && <div className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse" title="GATED" />}
                            <span className={`text-[10px] font-black ${colorClass}`}>{Math.round(s.score)}%</span>
                          </div>
                          <span className="text-[6px] font-black text-zinc-600 uppercase tracking-widest">
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

                    {/* Telemetry Grid (Multi-Timeframe + PnL 24h) */}
                    <div className="grid grid-cols-4 gap-2 bg-black/20 p-3 rounded-2xl border border-white/5 relative z-10">
                      <div className="flex flex-col items-center justify-center border-r border-white/5">
                        <span className="text-[7px] font-black text-zinc-600 mb-1">PNL 24H</span>
                        <span className={`text-[10px] font-black ${(s.profit_24h || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                          {(s.profit_24h || 0) >= 0 ? '+' : ''}{(s.profit_24h || 0).toFixed(2)}€
                        </span>
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
                          <span className={colorClass}>{Math.round(s.score)}%</span>
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



                        return !name.includes('CHANNEL') && !name.includes('MASTER');



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



                {/* System Live Logs Console */}



                <section>



                  <LiveConsole logs={logs} />



                </section>







                {/* Positions Table */}



                <section className="bg-zinc-900/30 border border-zinc-800/50 rounded-[2.5rem] overflow-hidden backdrop-blur-2xl shadow-2xl">



                  <div className="p-6 border-b border-zinc-800 flex justify-between items-center bg-[#070707]/60">



                    <div className="flex items-center gap-4">



                      <div className="p-3 bg-indigo-500/10 border border-indigo-500/20 rounded-xl">



                        <ShoppingCart className="text-indigo-500" size={24} />



                      </div>



                      <div>









                      </div>



                    </div>



                    <div className="flex items-center gap-4">



                      <div className="px-5 py-1.5 bg-zinc-900/50 border border-zinc-800 rounded-xl">



                        <span className="text-[9px] font-black text-zinc-500 uppercase tracking-widest">Active Fleet: </span>



                        <span className="text-xs font-black text-indigo-400 italic font-mono">{trades.length} Trades</span>



                      </div>



                    </div>



                  </div>



                  <div className="overflow-x-auto">



                    <table className="w-full text-left">



                      <thead className="text-[9px] uppercase text-zinc-600 font-black tracking-[0.25em]">



                        <tr className="border-b border-zinc-800/30">



                          <th className="px-10 py-5">Security Identification</th>



                          <th className="px-10 py-5">Action</th>



                          <th className="px-10 py-5">Volume</th>



                          <th className="px-10 py-5 text-right">Yield</th>



                        </tr>



                      </thead>



                      <tbody className="divide-y divide-zinc-800/20">



                        {sortedTrades.length === 0 ? (



                          <tr>



                            <td colSpan="4" className="px-10 py-16 text-center text-zinc-700 font-black uppercase tracking-[0.5em] italic opacity-40 text-[10px]">System Idle € No Active Positions monitored</td>



                          </tr>



                        ) : (



                          sortedTrades.map((trade) => (



                            <tr key={trade.ticket} onClick={() => { setSelectedSymbol(trade.symbol); setCurrentView('surveillance'); }} className={`hover:bg-indigo-500/5 transition-all group cursor-pointer border-l-4 border-transparent`}>



                              <td className="px-10 py-5 text-lg font-black text-white uppercase italic tracking-tighter">



                                {trade.symbol} <span className="text-[9px] text-zinc-600 ml-3 font-mono not-italic tracking-normal">#{trade.ticket}</span>



                              </td>



                              <td className="px-10 py-5">



                                <span className={`px-3 py-1.5 rounded-lg text-[9px] font-black tracking-[0.2em] border shadow-2xl ${trade.type === 'BUY' ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20' : 'bg-rose-500/10 text-rose-500 border-rose-500/20'}`}>



                                  {trade.type}



                                </span>



                              </td>



                              <td className="px-10 py-5 font-mono text-zinc-400 font-bold italic text-xs">



                                {trade.volume.toFixed(2)} LOTS



                              </td>



                              <td className={`px-10 py-5 text-right font-black text-2xl italic tracking-tighter ${trade.profit >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>



                                {trade.profit >= 0 ? '+' : ''}{trade.profit.toFixed(2)}€



                              </td>



                            </tr>



                          ))



                        )}



                      </tbody>



                    </table>



                  </div>



                </section>



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



                  <p className="text-[9px] text-zinc-600 font-bold uppercase tracking-[0.35em] mt-1.5">Control total de par€metros por activo € Persistencia en tiempo real</p>



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



                  { label: 'SL€ATR', desc: 'Multiplicador de Stop Loss', color: 'rose' },



                  { label: 'TP€ATR', desc: 'Multiplicador de Take Profit', color: 'emerald' },



                  { label: 'Min Score', desc: 'Umbral de se€al m€nima', color: 'amber' },



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
                    "PST-EMA-Flow": "EMA Flow",
                    "PST-Mean-Reversion": "Mean Reversion",
                    "PST-Scalper-Pro": "Scalper Pro"
                  };

                  // Obtener solo las estrategias CORE solicitadas: EMA Flow, Mean Reversion y Scalper Pro
                  const CORE_STRATS = ["PST-EMA-Flow", "PST-Mean-Reversion", "PST-Scalper-Pro"];
                  const knownStrategies = Array.from(new Set([
                    ...strategies,
                    ...matrixData.flatMap(m => Object.keys(m.factors_map || {})),
                    ...symbols.flatMap(sym => Object.keys(sym.factors_map || {}))
                  ]))
                    .filter(st => CORE_STRATS.includes(st))
                    .sort();

                  const allStratNames = CORE_STRATS; // Forzamos a que siempre aparezcan estas dos

                  return (
                    <div key={s.symbol} className={`bg-[#050505] border rounded-[1.5rem] p-3 flex flex-col gap-2 transition-all duration-300 hover:shadow-[0_0_30px_rgba(99,102,241,0.15)] ${s.is_active
                      ? 'border-indigo-500/40'
                      : 'border-zinc-800/40 opacity-50'
                      }`}>

                      {/* Nano Header - Larger symbols */}
                      <div className="flex justify-between items-center mb-0.5">
                        <div className="flex items-center gap-2">
                          <Activity size={16} className={s.is_active ? 'text-indigo-400 animate-pulse' : 'text-zinc-600'} />
                          <h3 className="text-base font-black text-white italic tracking-tighter">{s.symbol}</h3>
                        </div>
                        <button
                          onClick={() => toggleSymbolStatus(s.symbol, s.is_active)}
                          className={`w-9 h-4.5 rounded-full relative transition-all ${s.is_active ? 'bg-indigo-600' : 'bg-zinc-800'}`}
                        >
                          <div className={`absolute top-0.5 w-3.5 h-3.5 rounded-full bg-white transition-all ${s.is_active ? 'right-0.5' : 'left-0.5'}`} />
                        </button>
                      </div>

                      {/* Global Params Row: Larger fonts */}
                      <div className="flex items-center justify-between gap-1 bg-zinc-900/40 p-2 rounded-xl border border-zinc-800/50">
                        {[
                          { id: 'sl_mult', label: 'SL', color: 'rose' },
                          { id: 'tp_mult', label: 'TP', color: 'emerald' },
                          { id: 'score_threshold', label: 'SCORE', color: 'amber' }
                        ].map(p => (
                          <div key={p.id} className="flex-1 flex items-center justify-center gap-1.5">
                            <span className="text-[9px] font-black text-zinc-500 uppercase">{p.label}</span>
                            <input
                              type="text"
                              value={params[p.id]}
                              onChange={(e) => updateParam(s.symbol, p.id, parseFloat(e.target.value) || 0)}
                              className={`w-10 bg-transparent text-[11px] font-black text-${p.color}-500 text-center outline-none`}
                            />
                          </div>
                        ))}
                      </div>

                      {/* Strategy Controller: Core logic for defaults */}
                      <div className="flex-1 min-h-[160px] flex flex-col bg-black/50 rounded-xl border border-zinc-800/40 p-2.5 overflow-hidden">
                        <div className="flex items-center justify-between mb-2.5 px-1">
                          <span className="text-[9px] font-black text-zinc-600 uppercase tracking-widest">Active Systems</span>
                          <span className="text-[9px] font-bold text-indigo-500/40 uppercase">{allStratNames.length}</span>
                        </div>

                        <div className="flex-1 overflow-y-auto no-scrollbar space-y-1.5 pr-1">
                          {allStratNames.map(strat => {
                            const stratData = symbolStrats[strat];

                            // Lógica de activación por defecto
                            const isCore = ["PST-EMA-Flow"].includes(strat); // Mean Reversion ya no es core activa por defecto
                            const isEnabled = stratData ? (!!stratData.is_active) : isCore;

                            // Riesgo por defecto para TODAS las estrategias: 25€ (MONEY)
                            const sRiskMode = stratData?.risk_mode || 'MONEY';
                            const sRiskVal = stratData?.risk_value || 25;

                            const displayName = STRAT_NAME_MAP[strat] || strat.replace("PST-", "").replace(/-/g, " ");

                            return (
                              <div key={strat} className={`group/strat flex flex-col p-2 rounded-lg border transition-all ${isEnabled ? 'bg-indigo-500/[0.06] border-indigo-500/30 shadow-md' : 'bg-transparent border-zinc-800/30 opacity-40 hover:opacity-100'}`}>
                                <div className="flex items-center justify-between mb-1.5">
                                  <span className={`text-[10px] font-black uppercase truncate max-w-[130px] ${isEnabled ? 'text-white' : 'text-zinc-500'}`}>{displayName}</span>
                                  <button
                                    onClick={() => updateStrategyConfig(s.symbol, strat, 'is_active', !isEnabled)}
                                    className={`w-6 h-3 rounded-full relative transition-all ${isEnabled ? 'bg-indigo-500' : 'bg-zinc-800'}`}
                                  >
                                    <div className={`absolute top-0.5 w-2 h-2 rounded-full bg-white transition-all ${isEnabled ? 'right-0.5' : 'left-0.5'}`} />
                                  </button>
                                </div>

                                <div className="flex items-center justify-between gap-0.5">
                                  <div className="flex items-center bg-black/60 p-0.5 rounded-md border border-zinc-800/60">
                                    {['L', '%', '€'].map((mLabel, idx) => {
                                      const mVal = idx === 0 ? 'LOTS' : idx === 1 ? 'PCT' : 'MONEY';
                                      const isSelected = sRiskMode === mVal;
                                      return (
                                        <button
                                          key={mLabel}
                                          onClick={() => updateStrategyConfig(s.symbol, strat, 'risk_mode', isSelected ? null : mVal)}
                                          className={`px-1 py-0.5 rounded-sm text-[8px] font-black transition-all ${isSelected ? 'bg-indigo-600 text-white' : 'text-zinc-600 hover:text-zinc-400'}`}
                                        >
                                          {mLabel}
                                        </button>
                                      );
                                    })}
                                    <input
                                      type="number"
                                      placeholder="Auto"
                                      value={sRiskVal || ""}
                                      onChange={(e) => updateStrategyConfig(s.symbol, strat, 'risk_value', parseFloat(e.target.value) || null)}
                                      className="w-8 bg-transparent text-[10px] font-black text-indigo-400 text-right outline-none placeholder:text-zinc-800"
                                    />
                                  </div>

                                  <div className="flex items-center gap-0.5">
                                    <div className={`flex items-center gap-0.5 rounded-md border transition-all ${stratData?.use_breakeven ? 'bg-amber-500/10 border-amber-500/30' : 'border-zinc-800'}`}>
                                      <button
                                        onClick={() => updateStrategyConfig(s.symbol, strat, 'use_breakeven', !(stratData?.use_breakeven))}
                                        title="Break-Even (BE)"
                                        className={`px-1 py-0.5 rounded-sm text-[8px] font-black transition-all ${stratData?.use_breakeven ? 'text-amber-400' : 'bg-transparent text-zinc-600 hover:text-amber-500/50'}`}
                                      >
                                        BE
                                      </button>
                                      {stratData?.use_breakeven ? (
                                        <input
                                          type="number"
                                          step="0.1"
                                          defaultValue={stratData?.be_mult || ""}
                                          placeholder="2.0"
                                          onBlur={(e) => updateStrategyConfig(s.symbol, strat, 'be_mult', parseFloat(e.target.value) || null)}
                                          onKeyDown={(e) => e.key === 'Enter' && e.target.blur()}
                                          title="BE Multiplier (ATR)"
                                          className="w-8 bg-black/40 text-[9px] font-black text-amber-300 text-center outline-none px-0.5 border-l border-amber-500/30 rounded-r-sm shadow-inner cursor-text"
                                        />
                                      ) : null}
                                    </div>

                                    <div className={`flex items-center gap-0.5 rounded-md border transition-all ${stratData?.use_trailing ? 'bg-emerald-500/10 border-emerald-500/30' : 'border-zinc-800'}`}>
                                      <button
                                        onClick={() => updateStrategyConfig(s.symbol, strat, 'use_trailing', !(stratData?.use_trailing))}
                                        title="Trailing Stop (TS)"
                                        className={`px-1 py-0.5 rounded-sm text-[8px] font-black transition-all ${stratData?.use_trailing ? 'text-emerald-400' : 'bg-transparent text-zinc-600 hover:text-emerald-500/50'}`}
                                      >
                                        TS
                                      </button>
                                      {stratData?.use_trailing ? (
                                        <input
                                          type="number"
                                          step="0.1"
                                          defaultValue={stratData?.ts_mult || ""}
                                          placeholder="2.5"
                                          onBlur={(e) => updateStrategyConfig(s.symbol, strat, 'ts_mult', parseFloat(e.target.value) || null)}
                                          onKeyDown={(e) => e.key === 'Enter' && e.target.blur()}
                                          title="TS Multiplier (ATR)"
                                          className="w-8 bg-black/40 text-[9px] font-black text-emerald-300 text-center outline-none px-0.5 border-l border-emerald-500/30 rounded-r-sm shadow-inner cursor-text"
                                        />
                                      ) : null}
                                    </div>
                                  </div>

                                  <div className="flex items-center gap-0.5 text-[9px] font-black ml-auto">
                                    <div className="flex items-center gap-0.5 bg-black/20 px-0.5 py-0.5 rounded border border-white/5" title="Score Threshold Filter">
                                      <span className="text-[6px] text-zinc-500 uppercase font-black">SC</span>
                                      <input
                                        type="text"
                                        value={stratData?.score_threshold || ""}
                                        placeholder={params.score_threshold}
                                        onChange={(e) => updateStrategyConfig(s.symbol, strat, 'score_threshold', parseFloat(e.target.value) || null)}
                                        className="w-3 bg-transparent text-amber-400 outline-none text-center text-[8px] font-black"
                                      />
                                    </div>
                                    <div className="flex items-center gap-0.5 bg-black/20 px-0.5 py-0.5 rounded border border-white/5" title="Min Risk:Reward Filter">
                                      <span className="text-[6px] text-zinc-500 uppercase font-black">R:R</span>
                                      <input
                                        type="text"
                                        value={stratData?.min_rr || ""}
                                        placeholder="1.5"
                                        onChange={(e) => updateStrategyConfig(s.symbol, strat, 'min_rr', parseFloat(e.target.value) || null)}
                                        className="w-3 bg-transparent text-indigo-400 outline-none text-center text-[8px] font-black"
                                      />
                                    </div>
                                    <input
                                      type="text"
                                      value={stratData?.sl_mult || ""}
                                      placeholder={params.sl_mult}
                                      onChange={(e) => updateStrategyConfig(s.symbol, strat, 'sl_mult', parseFloat(e.target.value) || null)}
                                      className="w-3 bg-transparent text-rose-500 outline-none text-center"
                                    />
                                    <span className="text-zinc-700">/</span>
                                    <input
                                      type="text"
                                      value={stratData?.tp_mult || ""}
                                      placeholder={params.tp_mult}
                                      readOnly={strat === "PST-Mean-Reversion" || strat === "PST-Scalper-Pro"}
                                      title={(strat === "PST-Mean-Reversion" || strat === "PST-Scalper-Pro") ? "Usa TP Técnico" : "TP Multiplier"}
                                      onClick={(e) => {
                                        if (strat === "PST-Mean-Reversion" || strat === "PST-Scalper-Pro") {
                                          // Optional: Show a toast here explaining it uses technical TP
                                        }
                                      }}
                                      onChange={(e) => updateStrategyConfig(s.symbol, strat, 'tp_mult', parseFloat(e.target.value) || null)}
                                      className={`w-3 bg-transparent outline-none text-center ${(strat === "PST-Mean-Reversion" || strat === "PST-Scalper-Pro") ? 'text-zinc-600 cursor-not-allowed' : 'text-emerald-500'}`}
                                    />
                                  </div>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      </div>

                      {/* Nano Sync Button */}
                      <button
                        onClick={() => saveSymbolParams(s.symbol)}
                        disabled={isSaving}
                        className={`w-full py-2.5 rounded-xl font-black text-[10px] tracking-widest uppercase transition-all flex items-center justify-center gap-2 active:scale-[0.98] ${isSaving
                          ? 'bg-zinc-950 text-zinc-800 cursor-not-allowed border border-zinc-900'
                          : 'bg-indigo-600 text-white hover:bg-indigo-500 shadow-lg shadow-indigo-600/20 active:bg-indigo-700'
                          }`}
                      >
                        {isSaving ? <RefreshCw size={12} className="animate-spin" /> : <Save size={12} />}
                        {isSaving ? 'Syncing' : 'Sync Símbolo'}
                      </button>
                    </div>
                  )
                })}



              </div>







              {/* Footer Info */}



              <div className="mt-8 p-4 bg-zinc-900/30 border border-zinc-800/50 rounded-2xl flex items-start gap-3">



                <div className="p-1.5 bg-indigo-500/10 rounded-lg mt-0.5"><Info size={14} className="text-indigo-400" /></div>



                <div>



                  <p className="text-[9px] font-black text-zinc-400 uppercase tracking-widest">Los cambios se aplican en el pr€ximo ciclo del bot (~10s)</p>



                  <p className="text-[8px] text-zinc-600 font-bold uppercase tracking-wider mt-0.5">El orquestador lee `lot_size`, `sl_mult` y `tp_mult` antes de cada operaci€n. `score_threshold` filtra se€ales de baja calidad.</p>



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

      </main >



    </div >



  )



}







function NavItem({ icon, active = false, onClick, label, badge = 0 }) {



  return (



    <button



      onClick={onClick}



      className={`p-4 rounded-[1.5rem] transition-all duration-500 group relative flex items-center justify-center ${active ? 'bg-indigo-600/20 text-indigo-400 shadow-[0_0_30px_rgba(99,102,241,0.2)] border border-indigo-500/30' : 'text-zinc-700 hover:text-zinc-200 hover:bg-zinc-800/40'}`}



      title={label}



    >



      {icon}



      {badge > 0 && (



        <div className="absolute -top-1 -right-1 min-w-[20px] h-5 bg-rose-600 text-white text-[10px] font-black flex items-center justify-center rounded-full border-2 border-[#070707] shadow-[0_0_15px_rgba(225,29,72,0.4)] animate-in zoom-in duration-300">



          {badge}



        </div>



      )}



      {active && <div className="absolute -right-0.5 top-1/2 -translate-y-1/2 w-2 h-10 bg-indigo-500 rounded-l-full shadow-[0_0_20px_#6366f1]" />}



    </button>



  )



}







function Sparkline({ data, color }) {



  if (!data || data.length < 2) return null;



  const min = Math.min(...data);



  const max = Math.max(...data);



  const range = max - min || 1;



  const width = 100;



  const height = 40;







  const points = data.map((val, i) => {



    const x = (i / (data.length - 1)) * width;



    const y = height - ((val - min) / range) * height;



    return `${x},${y}`;



  }).join(' ');







  const strokeColor = color === 'emerald' ? '#10b981' : (color === 'rose' ? '#f43f5e' : (color === 'amber' ? '#f59e0b' : '#6366f1'));







  return (



    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-full overflow-visible">



      <defs>



        <linearGradient id={`grad-${color}`} x1="0" y1="0" x2="0" y2="1">



          <stop offset="0%" stopColor={strokeColor} stopOpacity="0.2" />



          <stop offset="100%" stopColor={strokeColor} stopOpacity="0" />



        </linearGradient>



      </defs>



      <polyline



        fill="none"



        stroke={strokeColor}



        strokeWidth="2.5"



        strokeLinecap="round"



        strokeLinejoin="round"



        points={points}



        className="drop-shadow-[0_0_8px_rgba(0,0,0,0.5)]"



      />



      <polygon



        points={`0,${height} ${points} ${width},${height}`}



        fill={`url(#grad-${color})`}



      />



    </svg>



  );



}















function Toast({ message, type, id }) {



  const icons = {



    success: <TrendingUp size={16} className="text-emerald-400" />,



    error: <ShieldAlert size={16} className="text-rose-400" />,



    info: <Activity size={16} className="text-indigo-400" />



  };







  const colors = {



    success: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400 shadow-[0_0_30px_rgba(16,185,129,0.1)]',



    error: 'border-rose-500/30 bg-rose-500/10 text-rose-400 shadow-[0_0_30px_rgba(244,63,94,0.1)]',



    info: 'border-indigo-500/30 bg-indigo-500/10 text-indigo-400 shadow-[0_0_30px_rgba(99,102,241,0.1)]'



  };







  return (



    <div className={`flex items-center gap-4 px-6 py-4 rounded-2xl border backdrop-blur-xl animate-in slide-in-from-right-8 fade-in duration-500 ${colors[type]}`}>



      <div className="p-2 bg-black/20 rounded-lg">{icons[type]}</div>



      <p className="text-xs font-black uppercase tracking-widest italic">{message}</p>



    </div>



  );



}







function StatBox({ title, value, icon, color, positive }) {



  const colors = {



    emerald: 'text-emerald-500 bg-emerald-500/10 border-emerald-500/20',



    rose: 'text-rose-500 bg-rose-500/10 border-rose-500/20',



    indigo: 'text-indigo-400 bg-indigo-500/10 border-indigo-500/20',



    amber: 'text-amber-500 bg-amber-500/10 border-amber-500/20',



    zinc: 'text-zinc-400 bg-zinc-800 border-zinc-700/50'



  };







  const currentBoxColor = positive !== undefined ? (positive ? colors.emerald : colors.rose) : colors[color];







  return (



    <div className="bg-[#090909] border border-zinc-800/60 rounded-2xl p-4 transition-all flex flex-col justify-between relative overflow-hidden group">



      <div className="absolute top-0 right-0 w-16 h-16 bg-white/[0.02] blur-2xl rounded-full -mr-8 -mt-8" />



      <div className="flex justify-between items-start relative z-10">



        <div className={`p-1.5 rounded-lg border ${currentBoxColor}`}>



          {icon}



        </div>



        <span className="text-[7px] font-black text-zinc-500 uppercase tracking-widest">{title}</span>



      </div>



      <p className={`text-2xl font-black italic tracking-tighter mt-4 relative z-10 ${positive !== undefined ? (positive ? 'text-emerald-500' : 'text-rose-500') : 'text-white'}`}>



        {value}



      </p>



    </div>



  );



}







function LiveConsole({ logs }) {
  const [cleared, setCleared] = useState(0)
  const [smartFilter, setSmartFilter] = useState(true)

  const KEYWORDS = ['orden', 'order', 'trade', 'ejecut', 'cerr', 'close', 'ticket',
    'error', 'critical', 'warn', 'rechazad', 'r:r', 'signal', 'blocked', 'sync', 'cleanup', 'profit']

  const visibleLogs = useMemo(() => {
    let list = logs.slice(cleared)
    if (smartFilter) {
      list = list.filter(log => {
        if (log.level === 'ERROR' || log.level === 'CRITICAL' || log.level === 'WARNING') return true
        const m = (log.message || '').toLowerCase()
        return KEYWORDS.some(k => m.includes(k))
      })
    }
    return list
  }, [logs, cleared, smartFilter])

  return (
    <div className="bg-[#050505] border border-zinc-800/80 rounded-[2rem] p-6 shadow-2xl h-[400px] flex flex-col gap-4 overflow-hidden">
      <div className="flex justify-between items-center shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-2 h-2 bg-indigo-500 rounded-full animate-pulse shadow-[0_0_10px_#6366f1]" />
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setSmartFilter(!smartFilter)}
            title={smartFilter ? 'Ver todos los logs' : 'Solo eventos importantes'}
            className={`px-2.5 py-1 rounded-lg text-[8px] font-black uppercase tracking-wider border transition-all ${smartFilter ? 'bg-indigo-600/20 border-indigo-500/40 text-indigo-400' : 'bg-zinc-900 border-zinc-700 text-zinc-500 hover:text-zinc-300'}`}
          >
            {smartFilter ? 'Smart' : 'All'}
          </button>
          <button
            onClick={() => setCleared(logs.length)}
            title="Limpiar consola"
            className="px-2.5 py-1 rounded-lg text-[8px] font-black uppercase tracking-wider border border-zinc-700 bg-zinc-900 text-zinc-500 hover:text-rose-400 hover:border-rose-500/40 transition-all"
          >
            Clear
          </button>
          <div className="px-3 py-1 bg-zinc-900 rounded-full border border-zinc-800">
            <span className="text-[8px] font-black text-zinc-500 uppercase tracking-widest">{visibleLogs.length} / {logs.length - cleared}</span>
          </div>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto font-mono text-[10px] space-y-1.5 no-scrollbar pr-2">
        {visibleLogs.length === 0 ? (
          <div className="h-full flex items-center justify-center text-zinc-800 italic uppercase tracking-widest text-[8px]">
            {smartFilter ? 'Sin eventos importantes...' : 'Waiting for telemetry uplink...'}
          </div>
        ) : (
          visibleLogs.map((log, i) => {
            const isError = log.level === 'ERROR' || log.level === 'CRITICAL';
            const isWarning = log.level === 'WARNING';
            const isTrade = (log.message || '').toLowerCase().includes('trade') || (log.message || '').toLowerCase().includes('orden');
            let color = 'text-zinc-500';
            if (isError) color = 'text-rose-500 font-bold';
            else if (isWarning) color = 'text-amber-500 font-bold';
            else if (isTrade) color = 'text-emerald-400 font-bold';
            else if (log.level === 'INFO') color = 'text-indigo-400';
            return (
              <div key={log.id || i} className="flex gap-4 group animate-in slide-in-from-left-2 duration-300">
                <span className="text-zinc-800 shrink-0 whitespace-nowrap">[{(log.timestamp || '').split(' ')[1] || log.timestamp}]</span>
                <span className={`${color} break-all opacity-90 group-hover:opacity-100 transition-opacity`}>
                  <span className="opacity-50 mr-2 uppercase">[{log.level}]</span>
                  {log.message}
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

function StatCard({ title, value, icon, subtitle, trend, className }) {
  return (
    <div className={`bg-zinc-900/40 p-10 border border-zinc-800/50 rounded-[3rem] hover:border-indigo-500/30 transition-all duration-700 backdrop-blur-md group hover:shadow-[0_0_50px_rgba(99,102,241,0.05)] ${className}`}>
      <div className="flex justify-between items-start mb-8">
        <div className="p-5 bg-zinc-800 border border-zinc-700/50 rounded-2xl group-hover:bg-indigo-500/10 group-hover:text-indigo-400 group-hover:border-indigo-500/20 transition-all duration-700 shadow-2xl">
          {icon}
        </div>
        {trend && (
          <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold border shadow-lg ${trend === 'up' ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20' : trend === 'down' ? 'text-rose-400 bg-rose-500/10 border-rose-500/20' : 'text-zinc-400 bg-zinc-500/10 border-zinc-500/20'}`}>
            {trend === 'up' ? '↗' : trend === 'down' ? '↘' : '→'}
          </div>
        )}
      </div>
      <div>
        <h2 className="text-[10px] text-zinc-500 font-black uppercase tracking-[0.3em] mb-2">{title}</h2>
        <p className="text-4xl font-black text-white tracking-tight mb-2">{value}</p>
        <p className="text-[10px] text-zinc-700 font-bold uppercase tracking-widest opacity-60 font-mono italic">{subtitle}</p>
      </div>
    </div>
  );
}

export default App
