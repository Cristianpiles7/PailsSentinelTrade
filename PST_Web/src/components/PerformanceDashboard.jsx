import React, { useMemo } from 'react'
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Cell, PieChart, Pie, Legend
} from 'recharts'
import { TrendingUp, TrendingDown, Target, Activity, Clock, Wallet, ShieldAlert, BarChart3 } from 'lucide-react'
import { PerformanceCalendar } from './ui/PerformanceCalendar'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function calcDrawdownSeries(equityCurve) {
  if (!equityCurve || equityCurve.length < 2) return []
  let peak = equityCurve[0]?.equity ?? 0
  return equityCurve.map((pt, i) => {
    if (pt.equity > peak) peak = pt.equity
    const dd = peak > 0 ? ((pt.equity - peak) / peak) * 100 : 0
    return { label: i, time: pt.time, drawdown: parseFloat(dd.toFixed(2)) }
  })
}

function StatKPI({ label, value, sub, color = 'text-white', icon: Icon }) {
  return (
    <div className="bg-zinc-900/30 border border-white/5 rounded-[2rem] p-6 flex flex-col gap-3 hover:bg-zinc-900/50 transition-all">
      <div className={`p-3 rounded-2xl bg-white/5 w-fit ${color}`}>
        <Icon size={20} />
      </div>
      <div>
        <p className="text-[10px] font-black text-zinc-500 uppercase tracking-widest mb-1">{label}</p>
        <h3 className={`text-2xl font-black italic tracking-tighter ${color}`}>{value}</h3>
        {sub && <p className="text-[9px] text-zinc-600 font-bold uppercase tracking-widest mt-1">{sub}</p>}
      </div>
    </div>
  )
}

const BUCKET_COLORS = { TREND: '#6366f1', RANGE: '#f59e0b', SCALPING: '#10b981' }

// ─── Main Component ────────────────────────────────────────────────────────────

export function PerformanceDashboard({
  perfData,
  equityCurve,
  stratPerf,
  historyTrades,
  bucketData
}) {
  const ddSeries = useMemo(() => calcDrawdownSeries(equityCurve), [equityCurve])
  const maxDrawdown = useMemo(() => {
    if (!ddSeries.length) return 0
    return Math.min(...ddSeries.map(d => d.drawdown))
  }, [ddSeries])

  const bucketWeights = bucketData?.weights ?? {}
  const bucketPnl = bucketData?.monthly_pnl ?? {}

  const pieData = Object.entries(bucketWeights).map(([cat, pct]) => ({
    name: cat,
    value: parseFloat(pct.toFixed(1)),
    fill: BUCKET_COLORS[cat] ?? '#6366f1'
  }))

  const stratRows = useMemo(() => {
    if (!stratPerf || stratPerf.length === 0) return []
    return [...stratPerf].sort((a, b) => (b.profit || 0) - (a.profit || 0))
  }, [stratPerf])

  return (
    <div className="w-full px-2 pb-10 animate-in fade-in slide-in-from-bottom-2 duration-700 space-y-8">

      {/* Header */}
      <div className="px-4 pt-2">
        <h2 className="text-2xl font-black text-white italic tracking-tighter uppercase">Performance Dashboard</h2>
        <p className="text-[9px] text-zinc-500 font-bold uppercase tracking-[0.2em]">Sistema de Análisis de Rendimiento · FASE 4.1</p>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4 px-4">
        <StatKPI
          label="Net P&L"
          value={`${(perfData?.net_profit ?? 0) >= 0 ? '+' : ''}${(perfData?.net_profit ?? 0).toFixed(2)}€`}
          color={(perfData?.net_profit ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}
          icon={Wallet}
        />
        <StatKPI
          label="Win Rate"
          value={`${perfData?.win_rate ?? 0}%`}
          sub={`${perfData?.total_trades ?? 0} trades`}
          color={parseFloat(perfData?.win_rate ?? 0) >= 50 ? 'text-emerald-400' : 'text-rose-400'}
          icon={Target}
        />
        <StatKPI
          label="Profit Factor"
          value={parseFloat(perfData?.profit_factor ?? 0).toFixed(2)}
          color={parseFloat(perfData?.profit_factor ?? 0) >= 1.5 ? 'text-indigo-400' : parseFloat(perfData?.profit_factor ?? 0) >= 1 ? 'text-amber-400' : 'text-rose-400'}
          icon={Activity}
        />
        <StatKPI
          label="Max Drawdown"
          value={`${maxDrawdown.toFixed(2)}%`}
          color={maxDrawdown > -5 ? 'text-amber-400' : 'text-rose-400'}
          icon={ShieldAlert}
        />
        <StatKPI
          label="Total Trades"
          value={perfData?.total_trades ?? 0}
          icon={BarChart3}
          color="text-white"
        />
        <StatKPI
          label="Avg Duration"
          value={`${perfData?.avg_duration_minutes ?? 0}m`}
          icon={Clock}
          color="text-zinc-300"
        />
      </div>

      {/* Equity Curve */}
      <div className="px-4">
        <div className="bg-[#050505] border border-white/5 rounded-[3rem] p-8 shadow-2xl">
          <div className="flex items-center justify-between mb-6">
            <h3 className="text-lg font-black text-white italic tracking-tighter uppercase">Equity Curve</h3>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 bg-indigo-500 rounded-full shadow-[0_0_10px_rgba(99,102,241,0.5)]" />
              <span className="text-[10px] font-black text-zinc-400 uppercase tracking-widest">Cumulative Balance</span>
            </div>
          </div>
          <div className="h-[280px]">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={equityCurve}>
                <defs>
                  <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#6366f1" stopOpacity={0.35} />
                    <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#ffffff05" vertical={false} />
                <XAxis dataKey="time" stroke="#ffffff15" fontSize={9}
                  tickFormatter={v => v === 'Start' ? 'INICIO' : v?.slice(0, 5) ?? ''} />
                <YAxis stroke="#ffffff15" fontSize={9} tickFormatter={v => `${v}€`} width={55} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#0a0a0a', border: '1px solid #ffffff10', borderRadius: '1rem', fontSize: 12 }}
                  labelStyle={{ color: '#888', fontSize: 10 }}
                  formatter={v => [`${v}€`, 'Balance']}
                />
                <Area type="monotone" dataKey="equity" stroke="#6366f1" strokeWidth={2.5}
                  fillOpacity={1} fill="url(#eqGrad)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Drawdown + Capital Buckets */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 px-4">

        {/* Drawdown Chart */}
        <div className="bg-[#050505] border border-white/5 rounded-[3rem] p-8 shadow-2xl">
          <div className="flex items-center justify-between mb-6">
            <h3 className="text-lg font-black text-white italic tracking-tighter uppercase">Drawdown</h3>
            <span className={`text-sm font-black font-mono ${maxDrawdown < -5 ? 'text-rose-400' : 'text-amber-400'}`}>
              Max: {maxDrawdown.toFixed(2)}%
            </span>
          </div>
          <div className="h-[220px]">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={ddSeries}>
                <defs>
                  <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#f43f5e" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#f43f5e" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#ffffff05" vertical={false} />
                <XAxis dataKey="label" hide />
                <YAxis stroke="#ffffff15" fontSize={9} tickFormatter={v => `${v}%`} width={40} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#0a0a0a', border: '1px solid #ffffff10', borderRadius: '1rem', fontSize: 12 }}
                  formatter={v => [`${v}%`, 'Drawdown']}
                  labelFormatter={() => ''}
                />
                <Area type="monotone" dataKey="drawdown" stroke="#f43f5e" strokeWidth={2}
                  fillOpacity={1} fill="url(#ddGrad)" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Capital Buckets */}
        <div className="bg-[#050505] border border-white/5 rounded-[3rem] p-8 shadow-2xl">
          <h3 className="text-lg font-black text-white italic tracking-tighter uppercase mb-6">Capital Buckets</h3>
          <div className="flex items-center gap-6">
            <div className="w-[140px] h-[140px] flex-shrink-0">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={pieData} cx="50%" cy="50%" innerRadius={40} outerRadius={65}
                    paddingAngle={3} dataKey="value">
                    {pieData.map((entry, i) => (
                      <Cell key={i} fill={entry.fill} stroke="transparent" />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{ backgroundColor: '#0a0a0a', border: '1px solid #ffffff10', borderRadius: '0.75rem', fontSize: 11 }}
                    formatter={v => [`${v}%`, '']}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="flex-1 space-y-4">
              {Object.entries(bucketWeights).map(([cat, pct]) => (
                <div key={cat}>
                  <div className="flex justify-between items-center mb-1">
                    <span className="text-[10px] font-black uppercase tracking-widest text-zinc-400">{cat}</span>
                    <div className="flex items-center gap-3">
                      <span className={`text-[9px] font-black font-mono ${(bucketPnl[cat] ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                        {(bucketPnl[cat] ?? 0) >= 0 ? '+' : ''}{(bucketPnl[cat] ?? 0).toFixed(2)}€
                      </span>
                      <span className="text-xs font-black text-white">{pct.toFixed(1)}%</span>
                    </div>
                  </div>
                  <div className="h-1.5 w-full bg-zinc-800/50 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-1000"
                      style={{ width: `${pct}%`, backgroundColor: BUCKET_COLORS[cat] ?? '#6366f1' }}
                    />
                  </div>
                </div>
              ))}
              <p className="text-[8px] text-zinc-700 font-bold uppercase tracking-widest mt-2">
                Rebalanceo mensual automático · Umbral 15%
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Strategy Comparison */}
      {stratRows.length > 0 && (
        <div className="px-4">
          <div className="bg-[#050505] border border-white/5 rounded-[3rem] p-8 shadow-2xl">
            <h3 className="text-lg font-black text-white italic tracking-tighter uppercase mb-6">Strategy Comparison</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-white/5">
                    {['Strategy', 'Trades', 'PnL Total', 'Win Rate*'].map(h => (
                      <th key={h} className="pb-3 px-4 text-[9px] font-black text-zinc-500 uppercase tracking-widest">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.03]">
                  {stratRows.map(s => {
                    const winRate = s.win_rate ?? null
                    return (
                      <tr key={s.strategy} className="hover:bg-white/[0.01] transition-colors">
                        <td className="py-4 px-4">
                          <div className="flex items-center gap-2">
                            <div className="w-2 h-2 rounded-full"
                              style={{ backgroundColor: BUCKET_COLORS[
                                s.strategy?.includes('Alpha') ? 'TREND' :
                                s.strategy?.includes('Range') ? 'RANGE' : 'SCALPING'
                              ] ?? '#6366f1' }}
                            />
                            <span className="text-sm font-black text-white italic">{s.strategy}</span>
                          </div>
                        </td>
                        <td className="py-4 px-4">
                          <span className="text-sm font-black text-zinc-300 font-mono">{s.trades_count ?? 0}</span>
                        </td>
                        <td className="py-4 px-4">
                          <span className={`text-sm font-black italic font-mono ${(s.profit ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                            {(s.profit ?? 0) >= 0 ? '+' : ''}{(s.profit ?? 0).toFixed(2)}€
                          </span>
                        </td>
                        <td className="py-4 px-4">
                          {winRate !== null ? (
                            <div className="flex items-center gap-2">
                              <div className="w-16 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                                <div
                                  className={`h-full rounded-full ${winRate >= 50 ? 'bg-emerald-500' : 'bg-rose-500'}`}
                                  style={{ width: `${winRate}%` }}
                                />
                              </div>
                              <span className={`text-[10px] font-black ${winRate >= 50 ? 'text-emerald-400' : 'text-rose-400'}`}>
                                {winRate}%
                              </span>
                            </div>
                          ) : (
                            <span className="text-[10px] text-zinc-600 font-bold">N/A</span>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              <p className="text-[8px] text-zinc-700 font-bold uppercase tracking-widest mt-4 px-4">
                * Win rate calculado sobre trades cerrados con datos disponibles
              </p>
            </div>
          </div>
        </div>
      )}

      {/* PnL Calendar */}
      <div className="px-4">
        <PerformanceCalendar trades={historyTrades} />
      </div>

    </div>
  )
}
