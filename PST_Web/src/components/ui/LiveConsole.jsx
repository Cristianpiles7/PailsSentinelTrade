import React, { useState, useMemo } from 'react'

export function LiveConsole({ logs }) {
  const [cleared, setCleared] = useState(0)
  const [smartFilter, setSmartFilter] = useState(true)
  const [levelFilter, setLevelFilter] = useState('ALL') // 'ALL', 'ERROR', 'WARNING', 'INFO'

  const KEYWORDS = ['orden', 'order', 'trade', 'ejecut', 'cerr', 'close', 'ticket',
    'error', 'critical', 'warn', 'rechazad', 'r:r', 'signal', 'blocked', 'sync', 'cleanup', 'profit']

  const visibleLogs = useMemo(() => {
    let list = logs.slice(cleared)
    
    // Filtro por Nivel (Si no es ALL)
    if (levelFilter !== 'ALL') {
      list = list.filter(log => {
        if (levelFilter === 'ERROR') return log.level === 'ERROR' || log.level === 'CRITICAL'
        if (levelFilter === 'WARNING') return log.level === 'WARNING'
        if (levelFilter === 'INFO') return log.level === 'INFO'
        return true
      })
    }

    if (smartFilter && levelFilter === 'ALL') {
      list = list.filter(log => {
        if (log.level === 'ERROR' || log.level === 'CRITICAL' || log.level === 'WARNING') return true
        const m = (log.message || '').toLowerCase()
        return KEYWORDS.some(k => m.includes(k))
      })
    }
    return list
  }, [logs, cleared, smartFilter, levelFilter])

  return (
    <div className="bg-[#050505] border border-zinc-800/80 rounded-[2rem] p-6 shadow-2xl h-[400px] flex flex-col gap-4 overflow-hidden">
      <div className="flex justify-between items-center shrink-0">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 bg-indigo-500 rounded-full animate-pulse shadow-[0_0_10px_#6366f1] mr-2" />
          {['ALL', 'ERROR', 'WARNING', 'INFO'].map(lvl => (
            <button
              key={lvl}
              onClick={() => { setLevelFilter(lvl); setSmartFilter(false); }}
              className={`px-2 py-1 rounded-lg text-[7px] font-black uppercase tracking-tighter border transition-all ${levelFilter === lvl 
                ? (lvl === 'ERROR' ? 'bg-rose-600/20 border-rose-500/40 text-rose-400' : 
                   lvl === 'WARNING' ? 'bg-amber-600/20 border-amber-500/40 text-amber-400' :
                   lvl === 'INFO' ? 'bg-indigo-600/20 border-indigo-500/40 text-indigo-400' :
                   'bg-zinc-700 border-zinc-500 text-white')
                : 'bg-zinc-900 border-zinc-800 text-zinc-600 hover:text-zinc-400'}`}
            >
              {lvl}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => { setSmartFilter(!smartFilter); setLevelFilter('ALL'); }}
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
