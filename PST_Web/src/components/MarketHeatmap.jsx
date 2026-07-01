import React, { useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { TrendingUp, TrendingDown, Activity, AlertCircle, Maximize2 } from 'lucide-react';

export function MarketHeatmap({ symbols, onSelectSymbol }) {
    // Ordenar símbolos por score
    const sortedSymbols = useMemo(() => {
        return [...symbols].sort((a, b) => b.score - a.score);
    }, [symbols]);

    // Calcular estadísticas globales
    const stats = useMemo(() => {
        const activeCount = symbols.filter(s => s.is_active).length;
        const totalScore = symbols.reduce((acc, s) => acc + s.score, 0);
        const avgScore = activeCount > 0 ? (totalScore / activeCount).toFixed(1) : 0;
        const highScorers = symbols.filter(s => s.score >= 80).length;
        const warmingScorers = symbols.filter(s => s.score >= 60 && s.score < 80).length;

        return { activeCount, avgScore, highScorers, warmingScorers };
    }, [symbols]);

    // Función para determinar el color de cada bloque basado en el score y dirección
    const getBlockStyle = (score, direction) => {
        if (score >= 80) {
            if (direction === 1) return 'bg-emerald-500/20 border-emerald-500 shadow-[0_0_15px_rgba(16,185,129,0.3)] text-emerald-400';
            if (direction === -1) return 'bg-rose-500/20 border-rose-500 shadow-[0_0_15px_rgba(244,63,94,0.3)] text-rose-400';
            return 'bg-amber-500/20 border-amber-500 shadow-[0_0_15px_rgba(245,158,11,0.3)] text-amber-400';
        }
        if (score >= 60) {
            if (direction === 1) return 'bg-emerald-500/10 border-emerald-500/50 text-emerald-400/80';
            if (direction === -1) return 'bg-rose-500/10 border-rose-500/50 text-rose-400/80';
            return 'bg-amber-500/10 border-amber-500/50 text-amber-400/80';
        }
        if (score >= 30) {
            return 'bg-zinc-800/80 border-zinc-700/50 text-zinc-300';
        }
        return 'bg-zinc-900 border-zinc-800 text-zinc-500';
    };

    // Función para determinar el tamaño visual basado en score (opcional para un Treemap real, aquí usaremos flex)
    const getFlexBasis = (score) => {
        if (score >= 80) return 'col-span-2 row-span-2';
        if (score >= 60) return 'col-span-1 row-span-2';
        return 'col-span-1 row-span-1';
    };

    return (
        <div className="w-full h-full flex flex-col gap-6 p-6">
            {/* Cabecera y Estadísticas */}
            <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
                <div className="flex flex-col">
                    <h2 className="text-2xl font-black text-white tracking-tight uppercase flex items-center gap-2">
                        <Activity className="text-indigo-500" /> Market <span className="text-transparent bg-clip-text bg-gradient-to-r from-rose-500 to-orange-500">Heatmap</span>
                    </h2>
                    <p className="text-sm font-medium text-zinc-500 uppercase tracking-widest mt-1">Real-Time Volatility Scanner</p>
                </div>
                
                <div className="flex gap-4">
                    <div className="bg-[#050505] border border-zinc-900 rounded-2xl p-4 flex flex-col items-center justify-center min-w-[120px]">
                        <span className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest mb-1">High Volatility</span>
                        <div className="flex items-end gap-1">
                            <span className="text-3xl font-black text-white leading-none">{stats.highScorers}</span>
                            <span className="text-xs text-rose-500 font-bold mb-0.5 animate-pulse">ALERTS</span>
                        </div>
                    </div>
                    <div className="bg-[#050505] border border-zinc-900 rounded-2xl p-4 flex flex-col items-center justify-center min-w-[120px]">
                        <span className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest mb-1">Average Score</span>
                        <div className="flex items-end gap-1">
                            <span className="text-3xl font-black text-white leading-none">{stats.avgScore}</span>
                            <span className="text-xs text-zinc-500 font-bold mb-0.5">/ 100</span>
                        </div>
                    </div>
                    <div className="bg-[#050505] border border-zinc-900 rounded-2xl p-4 flex flex-col items-center justify-center min-w-[120px]">
                        <span className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest mb-1">Active Pairs</span>
                        <div className="flex items-end gap-1">
                            <span className="text-3xl font-black text-white leading-none">{stats.activeCount}</span>
                            <span className="text-xs text-zinc-500 font-bold mb-0.5">SYMBOLS</span>
                        </div>
                    </div>
                </div>
            </div>

            {/* Grid del Heatmap */}
            <div className="flex-1 bg-[#050505] rounded-[2rem] border border-zinc-900/50 p-6 overflow-hidden flex flex-col">
                <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 auto-rows-[100px] gap-3 h-full overflow-y-auto pr-2 custom-scrollbar">
                    <AnimatePresence>
                        {sortedSymbols.map((s) => {
                            if (!s.is_active) return null;
                            const isHot = s.score >= 80;
                            const isWarm = s.score >= 60 && s.score < 80;
                            const styleClass = getBlockStyle(s.score, s.signal_direction);
                            const sizeClass = getFlexBasis(s.score);
                            
                            return (
                                <motion.div
                                    layout
                                    initial={{ opacity: 0, scale: 0.8 }}
                                    animate={{ opacity: 1, scale: 1 }}
                                    exit={{ opacity: 0, scale: 0.8 }}
                                    key={s.symbol}
                                    onClick={() => onSelectSymbol(s.symbol)}
                                    className={`relative rounded-2xl border transition-all duration-300 cursor-pointer overflow-hidden group hover:scale-[1.02] hover:z-10 ${styleClass} ${sizeClass}`}
                                >
                                    {/* Efecto de Pulso para Señales Altas */}
                                    {isHot && (
                                        <div className="absolute inset-0 bg-white/5 animate-pulse" />
                                    )}

                                    <div className="absolute inset-0 p-4 flex flex-col justify-between">
                                        <div className="flex items-start justify-between">
                                            <span className={`font-black tracking-tight ${isHot ? 'text-lg' : 'text-sm'}`}>
                                                {s.symbol.replace('.a', '')}
                                            </span>
                                            {s.signal_direction === 1 && <TrendingUp size={isHot ? 20 : 16} strokeWidth={3} className="opacity-80" />}
                                            {s.signal_direction === -1 && <TrendingDown size={isHot ? 20 : 16} strokeWidth={3} className="opacity-80" />}
                                        </div>

                                        <div className="flex flex-col items-center justify-center flex-1">
                                            <span className={`font-black leading-none tracking-tighter ${isHot ? 'text-5xl drop-shadow-lg' : isWarm ? 'text-3xl' : 'text-xl opacity-50'}`}>
                                                {s.score}
                                            </span>
                                        </div>

                                        <div className="flex items-end justify-between w-full">
                                            <span className="text-[9px] font-black uppercase tracking-widest opacity-60">
                                                {s.regime_m5 === 1 ? 'BULL' : s.regime_m5 === -1 ? 'BEAR' : 'NEUT'}
                                            </span>
                                            <Maximize2 size={12} className="opacity-0 group-hover:opacity-100 transition-opacity" />
                                        </div>
                                    </div>
                                    
                                    {/* Barra de Progreso Visual */}
                                    <div className="absolute bottom-0 left-0 h-1 bg-black/20 w-full">
                                        <div 
                                            className="h-full bg-current opacity-50 transition-all duration-500 ease-out"
                                            style={{ width: `${s.score}%` }}
                                        />
                                    </div>
                                </motion.div>
                            );
                        })}
                    </AnimatePresence>
                </div>
            </div>
        </div>
    );
}
