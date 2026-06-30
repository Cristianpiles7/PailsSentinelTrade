import React, { useState } from 'react';
import {
    AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
    ReferenceLine, CartesianGrid
} from 'recharts';
import {
    FlaskConical, Play, Target, ShieldAlert, Activity,
    TrendingUp, BarChart2, Zap, AlertCircle, CheckCircle2,
    XCircle, ChevronRight, Clock
} from 'lucide-react';

const API_BASE = 'http://localhost:8000';

const STRATEGIES = [
    {
        id: 'PST-AlphaTrend',
        label: 'Alpha Trend',
        desc: 'Trend-following multitimeframe H1/M15',
        color: '#6366f1',
        icon: TrendingUp,
    },
    {
        id: 'PST-RangeBreaker',
        label: 'Range Breaker',
        desc: 'Breakout de rangos M15/H1 con ATR',
        color: '#f59e0b',
        icon: Zap,
    },
    {
        id: 'PST-PrecisionScalping',
        label: 'Precision Scalping',
        desc: 'Scalping de alta precisión M1/M5',
        color: '#10b981',
        icon: Target,
    },
];

const TIMEFRAMES = ['M1', 'M5', 'M15', 'H1'];
const DAY_OPTIONS = [
    { value: 7,   label: '7 días' },
    { value: 14,  label: '14 días' },
    { value: 30,  label: '30 días' },
    { value: 60,  label: '60 días' },
    { value: 90,  label: '90 días' },
];

function MetricCard({ icon: Icon, label, value, sub, color = 'text-white', border = 'border-zinc-800' }) {
    return (
        <div className={`bg-[#0d0d0d] border ${border} rounded-2xl p-5 flex flex-col gap-1.5`}>
            <div className="flex items-center gap-2 text-zinc-500">
                <Icon size={14} />
                <span className="text-[10px] font-black uppercase tracking-widest">{label}</span>
            </div>
            <span className={`text-2xl font-black ${color} leading-none`}>{value}</span>
            {sub && <span className="text-[11px] text-zinc-600 font-medium">{sub}</span>}
        </div>
    );
}

const CustomTooltip = ({ active, payload }) => {
    if (!active || !payload?.length) return null;
    const d = payload[0].payload;
    const isPos = d.equity_r >= 0;
    return (
        <div className="bg-zinc-900 border border-zinc-700 rounded-xl px-4 py-3 text-xs shadow-2xl">
            <div className="text-zinc-400 mb-1">Trade #{d.trade} — {d.entry_time}</div>
            <div className={`font-black text-base ${isPos ? 'text-emerald-400' : 'text-rose-400'}`}>
                {isPos ? '+' : ''}{d.equity_r.toFixed(2)}R acumulado
            </div>
            <div className={`text-[11px] mt-1 font-bold ${d.result === 'WIN' ? 'text-emerald-500' : 'text-rose-500'}`}>
                {d.result === 'WIN' ? '✓ WIN' : '✗ LOSS'} · {d.direction}
            </div>
        </div>
    );
};

export function StrategyLab({ symbols }) {
    const [selectedStrategy, setSelectedStrategy] = useState(STRATEGIES[0]);
    const firstSymbol = symbols?.find(s => s.is_active)?.symbol ?? 'US500.cash';
    const [config, setConfig] = useState({ symbol: firstSymbol, timeframe: 'H1', days: 30 });
    const [isRunning, setIsRunning] = useState(false);
    const [results, setResults] = useState(null);
    const [error, setError] = useState(null);
    const [loadingStep, setLoadingStep] = useState('');

    const activeSymbols = symbols
        ?.filter(s => s.is_active)
        .map(s => s.symbol)
        ?? ['US500.cash', 'XAUUSD', 'BTCUSD'];

    const token = localStorage.getItem('pst_token');

    const handleRun = async () => {
        setIsRunning(true);
        setResults(null);
        setError(null);

        const steps = [
            'Conectando con MT5...',
            'Descargando datos históricos...',
            'Ejecutando señales barra a barra...',
            'Calculando métricas...',
        ];
        let stepIdx = 0;
        setLoadingStep(steps[0]);
        const stepTimer = setInterval(() => {
            stepIdx = Math.min(stepIdx + 1, steps.length - 1);
            setLoadingStep(steps[stepIdx]);
        }, 2000);

        try {
            const resp = await fetch(`${API_BASE}/api/backtest/run`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(token ? { 'X-PST-Token': token } : {}),
                },
                body: JSON.stringify({
                    strategy: selectedStrategy.id,
                    symbol: config.symbol,
                    days: config.days,
                    timeframe: config.timeframe,
                }),
            });

            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.detail || `Error ${resp.status}`);
            }

            const data = await resp.json();
            setResults(data);
        } catch (e) {
            setError(e.message);
        } finally {
            clearInterval(stepTimer);
            setIsRunning(false);
            setLoadingStep('');
        }
    };

    const perfColor = results
        ? results.total_r >= 0 ? 'text-emerald-400' : 'text-rose-400'
        : 'text-white';

    const pfColor = results
        ? results.profit_factor >= 1.5 ? 'text-emerald-400' : results.profit_factor >= 1.0 ? 'text-amber-400' : 'text-rose-400'
        : 'text-white';

    const minEquity = results ? Math.min(...results.curve.map(c => c.equity_r)) : 0;
    const maxEquity = results ? Math.max(...results.curve.map(c => c.equity_r)) : 0;

    return (
        <div className="w-full h-full flex flex-col p-4 sm:p-6 gap-5 bg-[#080808] overflow-y-auto">

            {/* ── Header ── */}
            <div className="flex items-center justify-between shrink-0">
                <div>
                    <h2 className="text-2xl font-black text-white tracking-tight uppercase flex items-center gap-2.5">
                        <FlaskConical className="text-indigo-500" size={22} />
                        Strategy <span className="text-indigo-400">Lab</span>
                    </h2>
                    <p className="text-[11px] font-bold text-zinc-600 uppercase tracking-[0.25em] mt-0.5">
                        Backtesting con datos reales de MT5
                    </p>
                </div>
                {results && (
                    <div className="hidden sm:flex items-center gap-2 text-[11px] font-bold text-zinc-500 bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-2">
                        <Clock size={12} />
                        {results.period}
                    </div>
                )}
            </div>

            <div className="flex flex-col xl:flex-row gap-5 flex-1 min-h-0">

                {/* ── Panel Izquierdo: Configuración ── */}
                <div className="w-full xl:w-72 shrink-0 flex flex-col gap-4">

                    {/* Selector de estrategia */}
                    <div className="bg-[#0d0d0d] border border-zinc-800 rounded-2xl p-4 flex flex-col gap-3">
                        <span className="text-[10px] font-black uppercase tracking-[0.3em] text-zinc-500">Motor</span>
                        {STRATEGIES.map(st => {
                            const isSelected = selectedStrategy.id === st.id;
                            const Icon = st.icon;
                            return (
                                <button
                                    key={st.id}
                                    onClick={() => setSelectedStrategy(st)}
                                    className={`flex items-center gap-3 p-3 rounded-xl border transition-all text-left ${
                                        isSelected
                                            ? 'border-opacity-60 bg-zinc-800'
                                            : 'border-zinc-800 hover:border-zinc-700 hover:bg-zinc-900'
                                    }`}
                                    style={isSelected ? { borderColor: st.color } : {}}
                                >
                                    <div
                                        className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0"
                                        style={{ backgroundColor: isSelected ? st.color + '30' : '#1a1a1a' }}
                                    >
                                        <Icon size={15} style={{ color: isSelected ? st.color : '#52525b' }} />
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <div className={`text-xs font-black ${isSelected ? 'text-white' : 'text-zinc-400'}`}>
                                            {st.label}
                                        </div>
                                        <div className="text-[10px] text-zinc-600 truncate">{st.desc}</div>
                                    </div>
                                    {isSelected && <ChevronRight size={14} style={{ color: st.color }} />}
                                </button>
                            );
                        })}
                    </div>

                    {/* Parámetros */}
                    <div className="bg-[#0d0d0d] border border-zinc-800 rounded-2xl p-4 flex flex-col gap-4">
                        <span className="text-[10px] font-black uppercase tracking-[0.3em] text-zinc-500">Parámetros</span>

                        <div className="flex flex-col gap-1.5">
                            <label className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest">Activo</label>
                            <select
                                value={config.symbol}
                                onChange={e => setConfig({ ...config, symbol: e.target.value })}
                                className="w-full bg-zinc-900 border border-zinc-800 text-white rounded-xl px-3 py-2.5 text-sm focus:border-indigo-500 outline-none"
                            >
                                {activeSymbols.map(sym => <option key={sym}>{sym}</option>)}
                            </select>
                        </div>

                        <div className="grid grid-cols-2 gap-3">
                            <div className="flex flex-col gap-1.5">
                                <label className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest">TF Ref.</label>
                                <select
                                    value={config.timeframe}
                                    onChange={e => setConfig({ ...config, timeframe: e.target.value })}
                                    className="w-full bg-zinc-900 border border-zinc-800 text-white rounded-xl px-3 py-2.5 text-sm focus:border-indigo-500 outline-none"
                                >
                                    {TIMEFRAMES.map(tf => <option key={tf}>{tf}</option>)}
                                </select>
                            </div>
                            <div className="flex flex-col gap-1.5">
                                <label className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest">Período</label>
                                <select
                                    value={config.days}
                                    onChange={e => setConfig({ ...config, days: parseInt(e.target.value) })}
                                    className="w-full bg-zinc-900 border border-zinc-800 text-white rounded-xl px-3 py-2.5 text-sm focus:border-indigo-500 outline-none"
                                >
                                    {DAY_OPTIONS.map(d => <option key={d.value} value={d.value}>{d.label}</option>)}
                                </select>
                            </div>
                        </div>
                    </div>

                    {/* Botón Run */}
                    <button
                        onClick={handleRun}
                        disabled={isRunning}
                        className={`w-full py-4 rounded-2xl font-black uppercase tracking-widest text-sm flex items-center justify-center gap-2.5 transition-all shadow-xl ${
                            isRunning
                                ? 'bg-zinc-800 text-zinc-500 cursor-not-allowed'
                                : 'text-white shadow-indigo-500/20 hover:shadow-indigo-500/40 hover:scale-[1.02]'
                        }`}
                        style={!isRunning ? { backgroundColor: selectedStrategy.color } : {}}
                    >
                        {isRunning
                            ? <><Activity className="animate-spin" size={17} /> Procesando...</>
                            : <><Play size={17} fill="white" /> Run Backtest</>
                        }
                    </button>

                    {isRunning && (
                        <div className="text-[11px] text-zinc-500 text-center font-medium animate-pulse">
                            {loadingStep}
                        </div>
                    )}
                </div>

                {/* ── Panel Derecho: Resultados ── */}
                <div className="flex-1 flex flex-col gap-4 min-w-0">

                    {/* Estado vacío */}
                    {!results && !isRunning && !error && (
                        <div className="flex-1 bg-[#0d0d0d] border border-zinc-800 border-dashed rounded-2xl flex flex-col items-center justify-center gap-4 p-8">
                            <FlaskConical size={40} className="text-zinc-800" />
                            <div className="text-center">
                                <p className="text-sm font-bold text-zinc-600">Configura y ejecuta el backtest</p>
                                <p className="text-xs text-zinc-700 mt-1">Los resultados se calculan sobre datos reales de MT5</p>
                            </div>
                        </div>
                    )}

                    {/* Error */}
                    {error && !isRunning && (
                        <div className="bg-rose-950/30 border border-rose-900 rounded-2xl p-5 flex items-start gap-3">
                            <AlertCircle size={18} className="text-rose-500 shrink-0 mt-0.5" />
                            <div>
                                <p className="text-sm font-bold text-rose-400">Error en el backtest</p>
                                <p className="text-xs text-rose-600 mt-1">{error}</p>
                            </div>
                        </div>
                    )}

                    {/* Resultados */}
                    {results && !isRunning && (
                        <>
                            {/* KPIs principales */}
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 shrink-0">
                                <MetricCard
                                    icon={Target}
                                    label="Win Rate"
                                    value={`${results.win_rate}%`}
                                    sub={`${results.total_trades} trades`}
                                    color={results.win_rate >= 55 ? 'text-emerald-400' : results.win_rate >= 45 ? 'text-amber-400' : 'text-rose-400'}
                                />
                                <MetricCard
                                    icon={TrendingUp}
                                    label="Total R"
                                    value={`${results.total_r >= 0 ? '+' : ''}${results.total_r}R`}
                                    sub={`Avg win: +${results.avg_win_r}R`}
                                    color={perfColor}
                                />
                                <MetricCard
                                    icon={BarChart2}
                                    label="Profit Factor"
                                    value={results.profit_factor >= 99 ? '∞' : results.profit_factor}
                                    sub="bruto / pérdidas"
                                    color={pfColor}
                                />
                                <MetricCard
                                    icon={ShieldAlert}
                                    label="Max Drawdown"
                                    value={`-${results.max_drawdown_r}R`}
                                    sub={`Sharpe: ${results.sharpe}`}
                                    color="text-rose-400"
                                    border="border-rose-900/40"
                                />
                            </div>

                            {/* Resumen inline */}
                            <div className="shrink-0 flex flex-wrap gap-3">
                                <div className="flex items-center gap-2 bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-2 text-xs">
                                    <CheckCircle2 size={13} className="text-emerald-500" />
                                    <span className="text-zinc-400">Avg win</span>
                                    <span className="font-black text-emerald-400">+{results.avg_win_r}R</span>
                                </div>
                                <div className="flex items-center gap-2 bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-2 text-xs">
                                    <XCircle size={13} className="text-rose-500" />
                                    <span className="text-zinc-400">Avg loss</span>
                                    <span className="font-black text-rose-400">{results.avg_loss_r}R</span>
                                </div>
                                <div className="flex items-center gap-2 bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-2 text-xs">
                                    <Activity size={13} className="text-indigo-400" />
                                    <span className="text-zinc-400">Sharpe Ratio</span>
                                    <span className="font-black text-indigo-400">{results.sharpe}</span>
                                </div>
                                <div className="flex items-center gap-2 bg-zinc-900 border border-zinc-800 rounded-xl px-3 py-2 text-xs">
                                    <Clock size={13} className="text-zinc-500" />
                                    <span className="text-zinc-500">{results.period}</span>
                                </div>
                            </div>

                            {/* Equity Curve */}
                            <div className="flex-1 bg-[#0d0d0d] border border-zinc-800 rounded-2xl p-5 flex flex-col min-h-[240px]">
                                <div className="flex items-center justify-between mb-4 shrink-0">
                                    <span className="text-[10px] font-black uppercase tracking-[0.3em] text-zinc-500">
                                        Equity Curve (R acumulado)
                                    </span>
                                    <div
                                        className="text-[10px] font-black uppercase tracking-wider px-3 py-1 rounded-lg"
                                        style={{
                                            backgroundColor: selectedStrategy.color + '20',
                                            color: selectedStrategy.color
                                        }}
                                    >
                                        {selectedStrategy.label} · {results.symbol}
                                    </div>
                                </div>

                                {results.curve.length === 0 ? (
                                    <div className="flex-1 flex items-center justify-center text-zinc-700 text-xs font-bold uppercase tracking-widest">
                                        Sin señales en el período seleccionado
                                    </div>
                                ) : (
                                    <div className="flex-1 min-h-0">
                                        <ResponsiveContainer width="100%" height="100%">
                                            <AreaChart data={results.curve} margin={{ top: 8, right: 8, left: -10, bottom: 0 }}>
                                                <defs>
                                                    <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                                                        <stop offset="5%"  stopColor={selectedStrategy.color} stopOpacity={0.35} />
                                                        <stop offset="95%" stopColor={selectedStrategy.color} stopOpacity={0} />
                                                    </linearGradient>
                                                </defs>
                                                <CartesianGrid strokeDasharray="3 3" stroke="#1a1a1a" vertical={false} />
                                                <XAxis
                                                    dataKey="trade"
                                                    tick={{ fill: '#52525b', fontSize: 10, fontWeight: 700 }}
                                                    axisLine={false}
                                                    tickLine={false}
                                                    interval="preserveStartEnd"
                                                    label={{ value: 'Trades', position: 'insideBottomRight', offset: -5, fill: '#3f3f46', fontSize: 10 }}
                                                />
                                                <YAxis
                                                    domain={[Math.min(minEquity * 1.1, minEquity - 0.5), Math.max(maxEquity * 1.1, maxEquity + 0.5)]}
                                                    tick={{ fill: '#52525b', fontSize: 10, fontWeight: 700 }}
                                                    axisLine={false}
                                                    tickLine={false}
                                                    tickFormatter={v => `${v > 0 ? '+' : ''}${v.toFixed(1)}R`}
                                                />
                                                <ReferenceLine y={0} stroke="#3f3f46" strokeDasharray="4 4" />
                                                <Tooltip content={<CustomTooltip />} />
                                                <Area
                                                    type="monotone"
                                                    dataKey="equity_r"
                                                    stroke={selectedStrategy.color}
                                                    strokeWidth={2.5}
                                                    fillOpacity={1}
                                                    fill="url(#eqGrad)"
                                                    dot={results.curve.length <= 40 ? {
                                                        fill: selectedStrategy.color,
                                                        r: 3,
                                                        strokeWidth: 0
                                                    } : false}
                                                    activeDot={{ r: 5, strokeWidth: 0, fill: selectedStrategy.color }}
                                                />
                                            </AreaChart>
                                        </ResponsiveContainer>
                                    </div>
                                )}
                            </div>
                        </>
                    )}

                    {/* Loading overlay */}
                    {isRunning && (
                        <div className="flex-1 bg-[#0d0d0d] border border-zinc-800 rounded-2xl flex flex-col items-center justify-center gap-6 p-8">
                            <div
                                className="w-16 h-16 rounded-2xl flex items-center justify-center"
                                style={{ backgroundColor: selectedStrategy.color + '20' }}
                            >
                                <Activity
                                    size={32}
                                    className="animate-pulse"
                                    style={{ color: selectedStrategy.color }}
                                />
                            </div>
                            <div className="text-center">
                                <p className="text-sm font-black text-white">{loadingStep}</p>
                                <p className="text-xs text-zinc-600 mt-1">
                                    {selectedStrategy.label} · {config.symbol} · {config.days} días
                                </p>
                            </div>
                            <div className="flex gap-1.5">
                                {[0, 1, 2].map(i => (
                                    <div
                                        key={i}
                                        className="w-2 h-2 rounded-full animate-bounce"
                                        style={{
                                            backgroundColor: selectedStrategy.color,
                                            animationDelay: `${i * 0.15}s`
                                        }}
                                    />
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
