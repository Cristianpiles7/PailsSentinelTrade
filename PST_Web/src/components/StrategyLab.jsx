import React, { useState } from 'react';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { FlaskConical, Play, Target, ShieldAlert, BadgeCent, Activity } from 'lucide-react';

export function StrategyLab({ symbols }) {
    const [config, setConfig] = useState({
        strategy: 'PST-Scalper-Pro',
        symbol: 'US30',
        timeframe: 'M1',
        days: 7,
        riskPoints: 15
    });

    const [isSimulating, setIsSimulating] = useState(false);
    const [results, setResults] = useState(null);

    const activeSymbols = symbols.filter(s => s.is_active).map(s => s.symbol.replace('.a', ''));
    
    // Lista dummy de estrategias conocidas
    const strategyList = ['PST-Scalper-Pro', 'PST-Ema-Flow', 'PST-Channel-Master'];

    const handleSimulate = () => {
        setIsSimulating(true);
        // Simulador Frontend Dummy (Puesto que Backtesting real requiere BD masiva)
        setTimeout(() => {
            // Generar curva de equidad procedural mock basada en la configuración
            const baseCapital = 1000;
            let currentCap = baseCapital;
            const curve = [];
            const winRate = config.strategy === 'PST-Scalper-Pro' ? 0.65 : 0.55;
            
            for(let i=1; i<=30; i++) {
                const isWin = Math.random() < winRate;
                const change = isWin ? (Math.random() * 25 + 10) : -(Math.random() * 20 + 5);
                currentCap += change;
                curve.push({
                    trade: i,
                    balance: currentCap,
                    pnl: change
                });
            }

            const maxCap = Math.max(...curve.map(c => c.balance));
            const minCapId = curve.findIndex(c => c.balance === Math.min(...curve.map(c => c.balance)));
            // Calculo dummy de drawdown
            const drawdown = ((maxCap - currentCap) / maxCap * 100).toFixed(2);
            
            setResults({
                winRate: (winRate * 100).toFixed(1),
                totalTrades: 30,
                netProfit: (currentCap - baseCapital).toFixed(2),
                drawdown: drawdown,
                curve: curve
            });
            setIsSimulating(false);
        }, 1500);
    };

    return (
        <div className="w-full h-full flex flex-col p-4 sm:p-6 gap-6 bg-[#0a0a0a]">
            {/* Header */}
            <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
                <div className="flex flex-col">
                    <h2 className="text-2xl font-black text-white tracking-tight uppercase flex items-center gap-2">
                        <FlaskConical className="text-indigo-500" /> Strategy <span className="text-emerald-400">Lab</span>
                    </h2>
                    <p className="text-sm font-medium text-zinc-500 uppercase tracking-widest mt-1">Backtesting Environment</p>
                </div>
            </div>

            <div className="flex flex-col lg:flex-row gap-6 h-full">
                {/* Panel de Configuración Lateral */}
                <div className="w-full lg:w-80 bg-[#050505] border border-zinc-900 rounded-[2rem] p-6 flex flex-col gap-6 shadow-2xl shrink-0">
                    <h3 className="text-[10px] font-black uppercase tracking-[0.3em] text-zinc-500 mb-2">Sim Parameters</h3>
                    
                    <div className="space-y-4">
                        <div className="flex flex-col gap-2">
                            <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">Strategy Engine</label>
                            <select 
                                value={config.strategy}
                                onChange={e => setConfig({...config, strategy: e.target.value})}
                                className="w-full bg-zinc-900 border border-zinc-800 text-white rounded-xl px-4 py-3 text-sm focus:border-indigo-500 outline-none"
                            >
                                {strategyList.map(st => <option key={st}>{st}</option>)}
                            </select>
                        </div>
                        
                        <div className="flex flex-col gap-2">
                            <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">Asset</label>
                            <select 
                                value={config.symbol}
                                onChange={e => setConfig({...config, symbol: e.target.value})}
                                className="w-full bg-zinc-900 border border-zinc-800 text-white rounded-xl px-4 py-3 text-sm focus:border-indigo-500 outline-none"
                            >
                                {activeSymbols.length > 0 ? activeSymbols.map(sym => <option key={sym}>{sym}</option>) : <option>US30</option>}
                            </select>
                        </div>
                        
                        <div className="flex gap-4">
                            <div className="flex flex-col gap-2 flex-1">
                                <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">Timeframe</label>
                                <select 
                                    value={config.timeframe}
                                    onChange={e => setConfig({...config, timeframe: e.target.value})}
                                    className="w-full bg-zinc-900 border border-zinc-800 text-white rounded-xl px-4 py-3 text-sm focus:border-indigo-500 outline-none"
                                >
                                    <option>M1</option>
                                    <option>M5</option>
                                    <option>M15</option>
                                </select>
                            </div>
                            <div className="flex flex-col gap-2 flex-1">
                                <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">Days Scope</label>
                                <select 
                                    value={config.days}
                                    onChange={e => setConfig({...config, days: parseInt(e.target.value)})}
                                    className="w-full bg-zinc-900 border border-zinc-800 text-white rounded-xl px-4 py-3 text-sm focus:border-indigo-500 outline-none"
                                >
                                    <option value="1">1 Day</option>
                                    <option value="7">7 Days</option>
                                    <option value="30">30 Days</option>
                                </select>
                            </div>
                        </div>
                    </div>

                    <div className="mt-auto pt-6 border-t border-zinc-900">
                        <button 
                            onClick={handleSimulate}
                            disabled={isSimulating}
                            className={`w-full py-4 rounded-xl font-black uppercase tracking-widest text-sm flex items-center justify-center gap-2 transition-all shadow-xl ${
                                isSimulating ? 'bg-zinc-800 text-zinc-500 cursor-not-allowed' : 'bg-indigo-600 hover:bg-indigo-500 text-white shadow-indigo-500/20'
                            }`}
                        >
                            {isSimulating ? <Activity className="animate-spin" size={18} /> : <Play size={18} />}
                            {isSimulating ? 'Simulating...' : 'Run Simulation'}
                        </button>
                    </div>
                </div>

                {/* Panel Central de Resultados */}
                <div className="flex-1 flex flex-col gap-6">
                    {/* Estadísticas de Resultados */}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        <div className="bg-[#050505] border border-zinc-900 rounded-3xl p-6 flex flex-col gap-2">
                            <div className="flex items-center gap-2 text-zinc-500"><Target size={16} /><span className="text-[10px] font-bold uppercase tracking-widest">Win Rate</span></div>
                            <span className="text-3xl font-black text-white">{results ? results.winRate : '--'}%</span>
                        </div>
                        <div className="bg-[#050505] border border-zinc-900 rounded-3xl p-6 flex flex-col gap-2">
                            <div className="flex items-center gap-2 text-zinc-500"><BadgeCent size={16} /><span className="text-[10px] font-bold uppercase tracking-widest">Net Profit</span></div>
                            <span className={`text-3xl font-black ${!results ? 'text-white' : parseFloat(results.netProfit) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                                {results ? `${parseFloat(results.netProfit) >= 0 ? '+' : ''}${results.netProfit}€` : '--€'}
                            </span>
                        </div>
                        <div className="bg-[#050505] border border-zinc-900 rounded-3xl p-6 flex flex-col gap-2">
                            <div className="flex items-center gap-2 text-zinc-500"><ShieldAlert size={16} /><span className="text-[10px] font-bold uppercase tracking-widest">Drawdown</span></div>
                            <span className="text-3xl font-black text-rose-400">{results ? results.drawdown : '--'}%</span>
                        </div>
                        <div className="bg-[#050505] border border-zinc-900 rounded-3xl p-6 flex flex-col gap-2">
                            <div className="flex items-center gap-2 text-zinc-500"><Activity size={16} /><span className="text-[10px] font-bold uppercase tracking-widest">Total Trades</span></div>
                            <span className="text-3xl font-black text-white">{results ? results.totalTrades : '--'}</span>
                        </div>
                    </div>

                    {/* Gráfico de Equity Chart */}
                    <div className="flex-1 bg-[#050505] border border-zinc-900 rounded-[2rem] p-6 shadow-2xl relative overflow-hidden flex flex-col">
                        <span className="text-[10px] font-black uppercase tracking-[0.3em] text-zinc-500 absolute top-6 left-6 z-10">Equity Curve Simulation</span>
                        
                        {!results && !isSimulating && (
                            <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                                <span className="text-[10px] font-black uppercase tracking-[0.5em] text-zinc-800 italic">Configure & Run Simulation</span>
                            </div>
                        )}

                        {isSimulating && (
                            <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none bg-black/40 backdrop-blur-sm z-20 gap-4">
                                <Activity className="text-indigo-500 animate-pulse" size={48} />
                                <span className="text-[10px] font-black uppercase tracking-[0.5em] text-zinc-500">Processing Historical Ticks...</span>
                            </div>
                        )}

                        {results && !isSimulating && (
                            <div className="w-full h-full pt-8">
                                <ResponsiveContainer width="100%" height="100%">
                                    <AreaChart data={results.curve} margin={{ top: 10, right: 0, left: 0, bottom: 0 }}>
                                        <defs>
                                            <linearGradient id="simEq" x1="0" y1="0" x2="0" y2="1">
                                                <stop offset="5%" stopColor="#4f46e5" stopOpacity={0.3}/>
                                                <stop offset="95%" stopColor="#4f46e5" stopOpacity={0}/>
                                            </linearGradient>
                                        </defs>
                                        <XAxis dataKey="trade" hide />
                                        <YAxis domain={['auto', 'auto']} hide />
                                        <Tooltip 
                                            contentStyle={{ backgroundColor: '#18181b', borderColor: '#27272a', borderRadius: '12px' }}
                                            itemStyle={{ color: '#fff', fontWeight: 'bold' }}
                                            labelStyle={{ display: 'none' }}
                                            formatter={(value) => [`${parseFloat(value).toFixed(2)}€`, 'Balance']}
                                        />
                                        <Area type="monotone" dataKey="balance" stroke="#6366f1" strokeWidth={3} fillOpacity={1} fill="url(#simEq)" />
                                    </AreaChart>
                                </ResponsiveContainer>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
