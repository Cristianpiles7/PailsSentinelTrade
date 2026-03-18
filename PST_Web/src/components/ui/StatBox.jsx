import React from 'react'

export function StatBox({ title, value, icon, color, positive }) {
  const colors = {
    emerald: 'text-emerald-500 bg-emerald-500/10 border-emerald-500/20',
    rose: 'text-rose-500 bg-rose-500/10 border-rose-500/20',
    indigo: 'text-indigo-400 bg-indigo-500/10 border-indigo-500/20',
    amber: 'text-amber-500 bg-amber-500/10 border-amber-500/20',
    zinc: 'text-zinc-400 bg-zinc-800 border-zinc-700/50'
  };

  const currentBoxColor = positive !== undefined ? (positive ? colors.emerald : colors.rose) : colors[color];

  return (
    <div className="bg-[#090909] border border-zinc-800/60 rounded-2xl p-4 transition-all flex flex-col justify-between relative overflow-hidden group">
      <div className="absolute top-0 right-0 w-16 h-16 bg-white/[0.02] blur-2xl rounded-full -mr-8 -mt-8" />
      <div className="flex justify-between items-start relative z-10">
        <div className={`p-1.5 rounded-lg border ${currentBoxColor}`}>
          {icon}
        </div>
        <span className="text-[7px] font-black text-zinc-500 uppercase tracking-widest">{title}</span>
      </div>
      <p className={`text-2xl font-black italic tracking-tighter mt-4 relative z-10 ${positive !== undefined ? (positive ? 'text-emerald-500' : 'text-rose-500') : 'text-white'}`}>
        {value}
      </p>
    </div>
  );
}
