import React from 'react'

export function TradeRiskVisualizer({ trade }) {
  const { type, price_open, price_current, sl, tp } = trade

  // If SL or TP aren't set, we show a simplified view
  if (!sl || !tp) {
    return (
      <div className="flex flex-col gap-1 w-full bg-black/20 p-2 rounded-lg border border-white/5">
        <div className="flex justify-between text-[7px] font-black text-zinc-500 uppercase tracking-widest">
          <span>{type}: {price_open.toFixed(5)}</span>
          <span className="text-indigo-400">NOW: {price_current.toFixed(5)}</span>
        </div>
        <div className="h-1 w-full bg-zinc-800 rounded-full overflow-hidden relative">
           <div 
             className={`h-full ${price_current >= price_open ? 'bg-emerald-500' : 'bg-rose-500'} transition-all duration-500`}
             style={{ width: `${Math.min(100, Math.max(0, (Math.abs(price_current - price_open) / price_open) * 1000))}%` }}
           />
        </div>
        <span className="text-[6px] text-zinc-700 font-bold uppercase text-center italic">No SL/TP guards defined</span>
      </div>
    )
  }

  // Calculate percentage of current price between SL and TP
  let min, max, current, entry
  if (type === 'BUY') {
    min = sl
    max = tp
    current = price_current
    entry = price_open
  } else {
    // For SELL, SL is usually > PriceOpen > TP
    min = tp
    max = sl
    current = price_current
    entry = price_open
  }

  const range = max - min
  const currentPct = ((current - min) / range) * 100
  const entryPct = ((entry - min) / range) * 100
  
  // Is it in profit?
  const isProfit = type === 'BUY' ? price_current >= price_open : price_current <= price_open
  const isBE = type === 'BUY' ? sl >= price_open : sl <= price_open

  return (
    <div className="flex flex-col gap-1.5 w-full bg-black/40 p-2.5 rounded-xl border border-white/5 shadow-inner">
      <div className="flex justify-between items-center text-[7px] font-black uppercase tracking-tighter">
        <span className="text-rose-500 flex items-center gap-1">
          {isBE ? 'SHIELD ON' : 'STOP LOSS'} <span className="opacity-60">[{sl.toFixed(5)}]</span>
        </span>
        <span className="text-emerald-500">TAKE PROFIT <span className="opacity-60">[{tp.toFixed(5)}]</span></span>
      </div>

      <div className="h-1.5 w-full bg-zinc-900 rounded-full relative group/risk">
        {/* Background track */}
        <div className="absolute inset-0 bg-white/[0.02] rounded-full" />
        
        {/* Entry Point Marker */}
        <div 
          className="absolute top-1/2 -translate-y-1/2 w-0.5 h-3 bg-white/40 z-10 shadow-[0_0_5px_rgba(255,255,255,0.2)]"
          style={{ left: `${Math.min(99, Math.max(1, type === 'BUY' ? entryPct : 100 - entryPct))}%` }}
          title={`Entry: ${price_open}`}
        />

        {/* Current Price Marker */}
        <div 
          className={`absolute top-1/2 -translate-y-1/2 w-2 h-2 rounded-full z-20 transition-all duration-700 shadow-lg ${isProfit ? 'bg-emerald-400 shadow-emerald-500/50' : 'bg-rose-500 shadow-rose-500/50'}`}
          style={{ 
            left: `${Math.min(98, Math.max(1, type === 'BUY' ? currentPct : 100 - currentPct))}%`,
            transition: 'left 0.7s cubic-bezier(0.4, 0, 0.2, 1)'
          }}
        />

        {/* Visual Fill between Entry and Current */}
        <div 
          className={`absolute top-0 h-full opacity-30 ${isProfit ? 'bg-emerald-500' : 'bg-rose-500'}`}
          style={{ 
            left: `${Math.min(entryPct, currentPct)}%`,
            width: `${Math.abs(currentPct - entryPct)}%`,
            display: type === 'BUY' ? 'block' : 'none' // Simplified for BUY, need inverted logic for display if complex
          }}
        />
      </div>

      <div className="flex justify-between text-[6px] font-black text-zinc-600 uppercase tracking-widest leading-none">
        <span>Guard: {(Math.abs(price_current - sl)).toFixed(5)} pips</span>
        <span className={isProfit ? 'text-emerald-400' : 'text-rose-400'}>
          {isProfit ? 'PROFIT TRACKER' : 'RECOVERY MODE'}
        </span>
      </div>
    </div>
  )
}
