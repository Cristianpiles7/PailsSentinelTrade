import React from 'react'
import { ShoppingCart } from 'lucide-react'

export function TradeTerminalTable({ 
  trades, 
  historyTrades, 
  filter, 
  setFilter, 
  onSymbolClick 
}) {
  const displayList = React.useMemo(() => {
    let list = []
    if (filter === 'ACTIVE' || filter === 'ALL') {
      list = [...list, ...trades.map(t => ({ ...t, status: 'OPEN' }))]
    }
    if (filter === 'HISTORY' || filter === 'ALL') {
      list = [...list, ...historyTrades.map(t => ({ ...t, status: 'CLOSED' }))]
    }
    return list.sort((a, b) => new Date(b.time_in || 0) - new Date(a.time_in || 0))
  }, [trades, historyTrades, filter])

  return (
    <section className="bg-zinc-900/30 border border-zinc-800/50 rounded-[2.5rem] overflow-hidden backdrop-blur-2xl shadow-2xl">
      <div className="p-6 border-b border-zinc-800 flex justify-between items-center bg-[#070707]/60">
        <div className="flex items-center gap-4">
          <div className="p-3 bg-indigo-500/10 border border-indigo-500/20 rounded-xl">
            <ShoppingCart className="text-indigo-500" size={24} />
          </div>
          <div className="flex items-center gap-2">
            {['ACTIVE', 'HISTORY', 'ALL'].map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-4 py-1.5 rounded-xl text-[8px] font-black uppercase tracking-widest border transition-all ${filter === f 
                  ? 'bg-indigo-600 border-indigo-500 text-white shadow-lg shadow-indigo-500/20' 
                  : 'bg-zinc-900/50 border-zinc-800 text-zinc-500 hover:text-zinc-300'}`}
              >
                {f === 'ACTIVE' ? 'En Curso' : f === 'HISTORY' ? 'Cerradas' : 'Todo'}
              </button>
            ))}
          </div>
        </div>
        <div className="px-5 py-1.5 bg-zinc-900/50 border border-zinc-800 rounded-xl">
          <span className="text-[9px] font-black text-zinc-500 uppercase tracking-widest">Registros: </span>
          <span className="text-xs font-black text-indigo-400 italic font-mono">{displayList.length}</span>
        </div>
      </div>

      <div className="overflow-x-auto min-h-[300px]">
        <table className="w-full text-left">
          <thead className="text-[9px] uppercase text-zinc-600 font-black tracking-[0.2em]">
            <tr className="border-b border-zinc-800/30">
              <th className="px-10 py-5">Identificación de Activo</th>
              <th className="px-10 py-5">Tipo</th>
              <th className="px-10 py-5">Volumen</th>
              <th className="px-10 py-5 text-right">Resultado</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/20">
            {displayList.length === 0 ? (
              <tr>
                <td colSpan="4" className="px-10 py-16 text-center text-zinc-700 font-black uppercase tracking-[0.5em] italic opacity-40 text-[10px]">
                  Sin operaciones registradas
                </td>
              </tr>
            ) : (
              displayList.map((trade) => (
                <tr 
                  key={trade.ticket || Math.random()} 
                  onClick={() => onSymbolClick?.(trade.symbol)} 
                  className={`hover:bg-indigo-500/5 transition-all group cursor-pointer border-l-4 ${trade.status === 'OPEN' ? 'border-indigo-500' : 'border-transparent opacity-60'}`}
                >
                  <td className="px-10 py-5 text-lg font-black text-white uppercase italic tracking-tighter">
                    {trade.symbol} <span className="text-[9px] text-zinc-600 ml-3 font-mono not-italic tracking-normal">#{trade.ticket}</span>
                    {trade.status === 'CLOSED' && <span className="ml-2 text-[7px] text-zinc-500 uppercase bg-zinc-800 px-1 rounded">Past</span>}
                  </td>
                  <td className="px-10 py-5">
                    <span className={`px-3 py-1.5 rounded-lg text-[9px] font-black tracking-[0.2em] border shadow-2xl ${trade.type === 'BUY' ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20' : 'bg-rose-500/10 text-rose-500 border-rose-500/20'}`}>
                      {trade.type}
                    </span>
                  </td>
                  <td className="px-10 py-5 font-mono text-zinc-400 font-bold italic text-xs">
                    {(trade.volume || 0).toFixed(2)} LOTS
                  </td>
                  <td className={`px-10 py-5 text-right font-black text-2xl italic tracking-tighter ${trade.profit >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
                    {trade.profit >= 0 ? '+' : ''}{(trade.profit || 0).toFixed(2)}€
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
