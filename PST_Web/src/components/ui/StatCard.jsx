import React from 'react'

export function StatCard({ title, value, icon, subtitle, trend, className }) {
  return (
    <div className={`bg-zinc-900/40 p-10 border border-zinc-800/50 rounded-[3rem] hover:border-indigo-500/30 transition-all duration-700 backdrop-blur-md group hover:shadow-[0_0_50px_rgba(99,102,241,0.05)] ${className}`}>
      <div className="flex justify-between items-start mb-8">
        <div className="p-5 bg-zinc-800 border border-zinc-700/50 rounded-2xl group-hover:bg-indigo-500/10 group-hover:text-indigo-400 group-hover:border-indigo-500/20 transition-all duration-700 shadow-2xl">
          {icon}
        </div>
        {trend && (
          <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold border shadow-lg ${trend === 'up' ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20' : trend === 'down' ? 'text-rose-400 bg-rose-500/10 border-rose-500/20' : 'text-zinc-400 bg-zinc-500/10 border-zinc-500/20'}`}>
            {trend === 'up' ? '↗' : trend === 'down' ? '↘' : '→'}
          </div>
        )}
      </div>
      <div>
        <h2 className="text-[10px] text-zinc-500 font-black uppercase tracking-[0.3em] mb-2">{title}</h2>
        <p className="text-4xl font-black text-white tracking-tight mb-2">{value}</p>
        <p className="text-[10px] text-zinc-700 font-bold uppercase tracking-widest opacity-60 font-mono italic">{subtitle}</p>
      </div>
    </div>
  );
}
