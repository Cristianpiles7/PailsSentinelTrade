import React from 'react'

// Varias de las 9 estrategias reales en MT5 no fijan un TP en precio (p.ej. H5 sale al volver
// a la banda de Bollinger central, H21 puede usar salida dinamica) -- solo tienen SL. Antes esto
// colapsaba a una barra "sin guardas" que perdia la info de riesgo (distancia al stop), que es
// justo lo mas relevante para monitorizar. Cuando falta un lado, se usa la distancia de riesgo
// del otro lado (SL<->entrada) como referencia visual "1R", dejado claro con la etiqueta.
export function TradeRiskVisualizer({ trade }) {
  const { type, price_open, price_current, sl, tp } = trade

  const hasSl = !!sl
  const hasTp = !!tp

  if (!hasSl && !hasTp) {
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
        <span className="text-[6px] text-zinc-700 font-bold uppercase text-center italic">Sin SL/TP definidos</span>
      </div>
    )
  }

  // Riesgo (distancia entrada->SL) usado como referencia "1R" cuando falta el lado sin guarda.
  const risk = hasSl ? Math.abs(price_open - sl) : Math.abs(tp - price_open)
  const effectiveSl = hasSl ? sl : (type === 'BUY' ? price_open - risk : price_open + risk)
  const effectiveTp = hasTp ? tp : (type === 'BUY' ? price_open + risk : price_open - risk)

  let min, max, current, entry
  if (type === 'BUY') {
    min = effectiveSl
    max = effectiveTp
    current = price_current
    entry = price_open
  } else {
    // Para SELL, SL suele ser > PrecioApertura > TP
    min = effectiveTp
    max = effectiveSl
    current = price_current
    entry = price_open
  }

  const range = max - min
  const currentPct = ((current - min) / range) * 100
  const entryPct = ((entry - min) / range) * 100

  const isProfit = type === 'BUY' ? price_current >= price_open : price_current <= price_open
  const isBE = hasSl && (type === 'BUY' ? sl >= price_open : sl <= price_open)

  return (
    <div className="flex flex-col gap-2 w-full bg-black/60 p-3 rounded-xl border border-white/5 shadow-inner scale-[1.02] origin-left">
      <div className="flex justify-between items-center text-[11px] font-black uppercase tracking-tight">
        <span className="text-rose-500 flex items-center gap-1.5">
          {isBE ? 'SHIELD ON' : 'STOP LOSS'} <span className="text-zinc-400 font-bold bg-rose-500/10 px-1 rounded">[{hasSl ? sl.toFixed(5) : 'sin SL'}]</span>
        </span>
        <span className="text-emerald-500 flex items-center gap-1.5">
          {hasTp ? 'TAKE PROFIT' : 'SALIDA POR ESTRATEGIA'} <span className="text-zinc-400 font-bold bg-emerald-500/10 px-1 rounded">[{hasTp ? tp.toFixed(5) : '~1R ref.'}]</span>
        </span>
      </div>

      <div className="h-2 w-full bg-zinc-900 rounded-full relative group/risk overflow-visible my-1">
        {/* Background track */}
        <div className="absolute inset-0 bg-white/[0.05] rounded-full" />

        {/* Entry Point Marker */}
        <div
          className="absolute top-1/2 -translate-y-1/2 w-0.5 h-4 bg-white/60 z-10 shadow-[0_0_8px_rgba(255,255,255,0.4)]"
          style={{ left: `${Math.min(99, Math.max(1, type === 'BUY' ? entryPct : 100 - entryPct))}%` }}
          title={`Entry: ${price_open}`}
        />

        {/* Current Price Marker */}
        <div
          className={`absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full z-20 transition-all duration-700 shadow-xl border border-white/20 ${isProfit ? 'bg-emerald-400 shadow-emerald-500/40' : 'bg-rose-500 shadow-rose-500/40'}`}
          style={{
            left: `${Math.min(98, Math.max(1, type === 'BUY' ? currentPct : 100 - currentPct))}%`,
            transition: 'left 0.7s cubic-bezier(0.4, 0, 0.2, 1)'
          }}
        />

        {/* Visual Fill between Entry and Current */}
        <div
          className={`absolute top-0 h-full opacity-40 ${isProfit ? 'bg-emerald-500' : 'bg-rose-500'}`}
          style={{
            left: `${Math.min(entryPct, currentPct)}%`,
            width: `${Math.abs(currentPct - entryPct)}%`,
            display: type === 'BUY' ? 'block' : 'none'
          }}
        />

        {/* Lado sin guarda real: raya diagonal para dejar claro que el TP/SL de ese lado es
            solo una referencia visual (1R), no una orden real en MT5 */}
        {!hasTp && (
          <div
            className="absolute top-0 h-full opacity-20 bg-[repeating-linear-gradient(45deg,#fff,#fff_2px,transparent_2px,transparent_6px)]"
            style={{ left: type === 'BUY' ? '50%' : '0%', width: '50%' }}
          />
        )}
      </div>

      <div className="flex justify-between items-baseline text-[10px] font-black text-zinc-500 uppercase tracking-widest leading-none">
        <span className="flex items-baseline gap-1">
          GUARD: <span className="text-zinc-200">{hasSl ? Math.abs(price_current - sl).toFixed(5) : '--'}</span> <span className="text-[7px] opacity-50">PIPS</span>
        </span>
        <span className={isProfit ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold'}>
          {isProfit ? 'PROFIT TRACKER' : 'RECOVERY MODE'}
        </span>
      </div>
    </div>
  )
}
