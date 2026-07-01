import React, { useMemo } from 'react'
import { Target } from 'lucide-react'

export function PerformanceCalendar({ trades = [] }) {
  // Aggregate profits by distinct YYYY-MM-DD days
  const dailyData = useMemo(() => {
    const data = {}
    trades.forEach(t => {
      // Use time_out (MT5 exit time), fallback to time_in or timestamp
      const timeStr = t.time_out || t.time_in || t.timestamp
      if (!timeStr) return
      
      // Match formats like "2026.03.18", "2026-03-18", "2026/03/18"
      const match = timeStr.match(/(\d{4})[./-](\d{2})[./-](\d{2})/)
      if (!match) return
      
      const dateStr = `${match[1]}-${match[2]}-${match[3]}`
      if (!data[dateStr]) data[dateStr] = { profit: 0, count: 0 }
      
      data[dateStr].profit += parseFloat(t.profit || 0)
      data[dateStr].count += 1
    })
    return data
  }, [trades])

  // Generate an 84-day grid (12 weeks * 7 days)
  const daysGrid = useMemo(() => {
    const grid = []
    const today = new Date()
    for (let i = 83; i >= 0; i--) {
      const d = new Date(today)
      d.setDate(today.getDate() - i)
      const ds = d.toISOString().split('T')[0]
      grid.push({
        date: ds,
        label: `${d.getDate()}/${d.getMonth() + 1}`,
        profit: dailyData[ds]?.profit,
        count: dailyData[ds]?.count || 0
      })
    }
    return grid
  }, [dailyData])

  // Map profit to Tailwind classes (Heatmap Logic)
  const getColor = (profit) => {
    if (profit === undefined) return 'bg-zinc-800/40 border-zinc-800'
    if (profit > 50) return 'bg-emerald-500 shadow-[0_0_10px_rgba(16,185,129,0.5)] border-emerald-400'
    if (profit > 0) return 'bg-emerald-500/40 border-emerald-500/50'
    if (profit < -50) return 'bg-rose-600 shadow-[0_0_10px_rgba(225,29,72,0.5)] border-rose-500'
    if (profit < 0) return 'bg-rose-500/40 border-rose-500/50'
    return 'bg-zinc-700 border-zinc-600'
  }

  // Weeks mapping for the CSS grid
  const weeks = []
  for (let i = 0; i < daysGrid.length; i += 7) {
    weeks.push(daysGrid.slice(i, i + 7))
  }

  return (
    <div className="bg-[#050505] p-6 border border-zinc-800/80 rounded-[2rem] shadow-2xl relative overflow-hidden group">
      <div className="absolute top-0 right-0 w-32 h-32 bg-indigo-500/5 blur-3xl rounded-full" />
      
      <div className="flex justify-between items-end mb-6 relative z-10 px-2">
        <div>
          <h3 className="text-sm font-black text-white italic tracking-tighter uppercase flex items-center gap-2 mb-1">
            <Target size={16} className="text-amber-500" />
            Tactical PnL Map
          </h3>
          <p className="text-[9px] text-zinc-500 font-bold uppercase tracking-widest">
            84-Day Global Profit Realization Heatmap
          </p>
        </div>
        <div className="flex gap-4">
          <div className="flex items-center gap-1.5"><div className="w-2.5 h-2.5 rounded-sm bg-rose-600 shadow-[0_0_8px_rgba(225,29,72,0.4)]"></div><span className="text-[8px] text-zinc-400 font-bold uppercase">Strong Loss</span></div>
          <div className="flex items-center gap-1.5"><div className="w-2.5 h-2.5 rounded-sm bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.4)]"></div><span className="text-[8px] text-zinc-400 font-bold uppercase">Strong Win</span></div>
        </div>
      </div>
      
      <div className="flex gap-1.5 overflow-x-auto pb-4 custom-scrollbar relative z-10">
        {weeks.map((week, wIndex) => (
          <div key={wIndex} className="flex flex-col gap-1.5">
            {week.map((day, dIndex) => (
              <div
                key={dIndex}
                title={`${day.date}\nPnL: ${day.profit !== undefined ? day.profit.toFixed(2) + '€' : 'No trades'}\nTrades: ${day.count}`}
                className={`w-3.5 h-3.5 rounded-sm border transition-all duration-300 hover:scale-[1.5] hover:z-20 cursor-crosshair ${getColor(day.profit)}`}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}
