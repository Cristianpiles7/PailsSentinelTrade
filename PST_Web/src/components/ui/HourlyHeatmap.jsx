import React, { useMemo } from 'react'

const SESSION_LABELS = {
  0:  'Asia',  1:  'Asia',  2:  'Asia',  3:  'Asia',
  4:  'Asia',  5:  'Asia',  6:  'Asia',  7:  'Frankfurt',
  8:  'London',9:  'London',10: 'London',11: 'London',
  12: 'London',13: 'London',14: 'NY',    15: 'NY',
  16: 'NY',    17: 'NY',    18: 'NY',    19: 'NY',
  20: 'NY',    21: 'Close', 22: 'Close', 23: 'Close'
}

const SESSION_COLORS = {
  Asia:      '#6366f1',
  Frankfurt: '#f59e0b',
  London:    '#10b981',
  NY:        '#3b82f6',
  Close:     '#71717a'
}

function getHeatColor(pnl, maxAbs, trades) {
  if (trades === 0) return { bg: 'rgba(39,39,42,0.4)', border: 'rgba(63,63,70,0.3)', text: '#52525b' }
  const intensity = Math.min(Math.abs(pnl) / (maxAbs || 1), 1)
  if (pnl > 0) {
    const g = Math.round(120 + intensity * 135)
    const a = 0.15 + intensity * 0.55
    return {
      bg:     `rgba(16,${g},129,${a})`,
      border: `rgba(16,185,129,${0.3 + intensity * 0.5})`,
      text:   '#6ee7b7'
    }
  }
  const r = Math.round(180 + intensity * 75)
  const a = 0.15 + intensity * 0.55
  return {
    bg:     `rgba(${r},29,72,${a})`,
    border: `rgba(244,63,94,${0.3 + intensity * 0.5})`,
    text:   '#fda4af'
  }
}

export function HourlyHeatmap({ data = [] }) {
  const maxAbs = useMemo(() => {
    if (!data.length) return 1
    return Math.max(...data.map(d => Math.abs(d.pnl)), 1)
  }, [data])

  const totals = useMemo(() => {
    const withTrades = data.filter(d => d.trades > 0)
    const bestHour  = withTrades.reduce((a, b) => b.pnl > a.pnl ? b : a, { pnl: -Infinity, hour: null })
    const worstHour = withTrades.reduce((a, b) => b.pnl < a.pnl ? b : a, { pnl: Infinity,  hour: null })
    const totalPnl  = data.reduce((s, d) => s + d.pnl, 0)
    return { bestHour, worstHour, totalPnl }
  }, [data])

  const hasData = data.some(d => d.trades > 0)

  if (!hasData) {
    return (
      <div className="h-full flex items-center justify-center text-zinc-600 text-[11px] font-black uppercase tracking-widest italic">
        Sin datos históricos suficientes
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">

      {/* Resumen rápido */}
      <div className="flex gap-3 flex-wrap">
        {totals.bestHour.hour !== null && (
          <div className="flex-1 min-w-[120px] bg-emerald-500/10 border border-emerald-500/25 rounded-2xl px-4 py-3">
            <p className="text-[9px] font-black text-emerald-500/70 uppercase tracking-widest mb-1">Mejor hora</p>
            <p className="text-xl font-black text-emerald-400 font-mono">{String(totals.bestHour.hour).padStart(2,'0')}:00</p>
            <p className="text-[10px] text-emerald-500/70 font-bold">+{totals.bestHour.pnl.toFixed(2)}€</p>
          </div>
        )}
        {totals.worstHour.hour !== null && (
          <div className="flex-1 min-w-[120px] bg-rose-500/10 border border-rose-500/25 rounded-2xl px-4 py-3">
            <p className="text-[9px] font-black text-rose-400/70 uppercase tracking-widest mb-1">Peor hora</p>
            <p className="text-xl font-black text-rose-400 font-mono">{String(totals.worstHour.hour).padStart(2,'0')}:00</p>
            <p className="text-[10px] text-rose-400/70 font-bold">{totals.worstHour.pnl.toFixed(2)}€</p>
          </div>
        )}
        <div className="flex-1 min-w-[120px] bg-zinc-800/40 border border-zinc-700/30 rounded-2xl px-4 py-3">
          <p className="text-[9px] font-black text-zinc-500 uppercase tracking-widest mb-1">PnL Total</p>
          <p className={`text-xl font-black font-mono ${totals.totalPnl >= 0 ? 'text-white' : 'text-rose-400'}`}>
            {totals.totalPnl >= 0 ? '+' : ''}{totals.totalPnl.toFixed(2)}€
          </p>
          <p className="text-[10px] text-zinc-500 font-bold">{data.reduce((s,d) => s + d.trades, 0)} trades</p>
        </div>
      </div>

      {/* Grid 24h */}
      <div className="grid grid-cols-6 gap-1.5">
        {data.map((d) => {
          const colors = getHeatColor(d.pnl, maxAbs, d.trades)
          const session = SESSION_LABELS[d.hour] ?? ''
          const sessionColor = SESSION_COLORS[session] ?? '#71717a'
          return (
            <div
              key={d.hour}
              title={`${String(d.hour).padStart(2,'0')}:00 | PnL: ${d.pnl >= 0 ? '+' : ''}${d.pnl.toFixed(2)}€ | ${d.trades} trades | WR: ${d.win_rate}%`}
              style={{
                backgroundColor: colors.bg,
                borderColor: colors.border,
                borderWidth: '1px',
                borderStyle: 'solid'
              }}
              className="rounded-xl p-2.5 cursor-default transition-transform hover:scale-105"
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-[9px] font-black text-zinc-400 font-mono">
                  {String(d.hour).padStart(2,'0')}h
                </span>
                {d.trades > 0 && (
                  <span
                    className="text-[7px] font-black rounded px-1"
                    style={{ color: sessionColor, backgroundColor: `${sessionColor}20` }}
                  >
                    {session.slice(0, 2).toUpperCase()}
                  </span>
                )}
              </div>
              {d.trades > 0 ? (
                <>
                  <p style={{ color: colors.text }} className="text-[10px] font-black font-mono leading-none">
                    {d.pnl >= 0 ? '+' : ''}{d.pnl.toFixed(1)}€
                  </p>
                  <p className="text-[8px] text-zinc-500 font-bold mt-0.5">{d.win_rate}% WR</p>
                </>
              ) : (
                <p className="text-[9px] text-zinc-700 font-bold mt-1">—</p>
              )}
            </div>
          )
        })}
      </div>

      {/* Leyenda de sesiones */}
      <div className="flex gap-4 flex-wrap pt-1">
        {Object.entries(SESSION_COLORS).map(([sess, color]) => (
          <div key={sess} className="flex items-center gap-1.5">
            <div className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
            <span className="text-[9px] font-black text-zinc-500 uppercase tracking-widest">{sess}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
