import React, { useState, useEffect } from 'react';
import { TradingChart } from './TradingChart';
import { LayoutGrid, Maximize2, Settings, RefreshCw, X } from 'lucide-react';

const API_BASE = import.meta.env.DEV ? "http://127.0.0.1:8000/api" : "/api";

export function MultiChartWorkspace({ symbols, api }) {
    // Estado inicial: 4 ranuras (slots)
    const [slots, setSlots] = useState([
        { id: 1, symbol: 'US30' },
        { id: 2, symbol: 'EURUSD' },
        { id: 3, symbol: 'XAUUSD' },
        { id: 4, symbol: 'GER40' }
    ]);

    const [chartData, setChartData] = useState({});
    const timeframe = 'M1'; // Multi-chart se asume que es para scalping rápido (M1)

    // Polling de datos para las 4 gráficas
    useEffect(() => {
        const fetchAllData = async () => {
            const newData = {};
            for (const slot of slots) {
                if (!slot.symbol) continue;
                try {
                    const resp = await api.get(`${API_BASE}/ohlc/${slot.symbol}?timeframe=${timeframe}`);
                    newData[slot.symbol] = resp.data || [];
                } catch (err) {
                    console.error(`Error fetching data for ${slot.symbol}`, err);
                }
            }
            setChartData(prev => ({ ...prev, ...newData }));
        };

        // Carga Inicial
        fetchAllData();

        // Polling cada 5 segundos (más agresivo porque son varios gráficos en M1)
        const timer = setInterval(fetchAllData, 5000);
        return () => clearInterval(timer);
    }, [slots, api, timeframe]);

    const handleSymbolChange = (slotId, newSymbol) => {
        setSlots(slots.map(s => s.id === slotId ? { ...s, symbol: newSymbol } : s));
    };

    const activeSymbols = symbols.filter(s => s.is_active).map(s => s.symbol.replace('.a', '')); // Sanitizamos por si acaso

    return (
        <div className="w-full h-full flex flex-col p-4 sm:p-6 gap-4 sm:gap-6 bg-[#0a0a0a]">
            {/* Header */}
            <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
                <div className="flex flex-col">
                    <h2 className="text-2xl font-black text-white tracking-tight uppercase flex items-center gap-2">
                        <LayoutGrid className="text-indigo-500" /> Multi-Chart <span className="text-zinc-500 font-medium">Workspace</span>
                    </h2>
                    <p className="text-sm font-medium text-zinc-500 uppercase tracking-widest mt-1">Simultaneous Surveillance</p>
                </div>
            </div>

            {/* Grid 2x2 para Gráficos */}
            <div className="flex-1 grid grid-cols-1 md:grid-cols-2 grid-rows-2 gap-4">
                {slots.map(slot => (
                    <div key={slot.id} className="relative bg-[#050505] rounded-3xl border border-zinc-900/50 shadow-2xl flex flex-col overflow-hidden">
                        
                        {/* Control Bar over Chart */}
                        <div className="absolute top-4 left-4 right-4 z-20 flex justify-between items-center pointer-events-auto">
                            <div className="relative group">
                                <select
                                    value={slot.symbol}
                                    onChange={(e) => handleSymbolChange(slot.id, e.target.value)}
                                    className="appearance-none bg-zinc-900/80 backdrop-blur-md border border-zinc-800 text-white font-black uppercase text-sm px-4 py-2 pr-10 rounded-xl cursor-pointer hover:border-indigo-500/50 transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-500/50"
                                >
                                    <option value="">-- SELECT PARR --</option>
                                    {activeSymbols.map(sym => (
                                        <option key={sym} value={sym}>{sym}</option>
                                    ))}
                                </select>
                                <div className="absolute inset-y-0 right-3 flex items-center pointer-events-none text-zinc-500 group-hover:text-indigo-400">
                                    <Settings size={14} />
                                </div>
                            </div>
                            
                            {/* Metadata Status */}
                            {slot.symbol && symbols.find(s => s.symbol.replace('.a', '') === slot.symbol) && (
                                <div className="bg-zinc-900/80 backdrop-blur-md border border-zinc-800 px-3 py-1.5 rounded-xl flex items-center gap-2">
                                    <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                                    <span className="text-[10px] font-black text-emerald-400 tracking-widest">
                                        SC: {symbols.find(s => s.symbol.replace('.a', '') === slot.symbol).score}
                                    </span>
                                </div>
                            )}
                        </div>

                        {/* Chart Container */}
                        <div className="flex-1 relative w-full h-full pt-16">
                            {slot.symbol && chartData[slot.symbol] ? (
                                <TradingChart 
                                    data={chartData[slot.symbol]} 
                                    symbol={slot.symbol} 
                                    activeTrade={null} 
                                    replayTrade={null} 
                                />
                            ) : (
                                <div className="absolute inset-0 flex items-center justify-center">
                                    {slot.symbol ? (
                                        <div className="flex flex-col items-center gap-3">
                                            <RefreshCw className="animate-spin text-zinc-700" size={24} />
                                            <span className="text-[10px] text-zinc-600 font-bold uppercase tracking-widest">Loading Telemetry</span>
                                        </div>
                                    ) : (
                                        <span className="text-xs text-zinc-700 font-bold uppercase tracking-widest">Available Slot</span>
                                    )}
                                </div>
                            )}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}
