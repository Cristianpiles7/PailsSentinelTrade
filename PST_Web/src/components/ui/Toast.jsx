import React from 'react'
import { TrendingUp, ShieldAlert, Activity } from 'lucide-react'

export function Toast({ message, type, id }) {
  const icons = {
    success: <TrendingUp size={16} className="text-emerald-400" />,
    error: <ShieldAlert size={16} className="text-rose-400" />,
    info: <Activity size={16} className="text-indigo-400" />
  };

  const colors = {
    success: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400 shadow-[0_0_30px_rgba(16,185,129,0.1)]',
    error: 'border-rose-500/30 bg-rose-500/10 text-rose-400 shadow-[0_0_30px_rgba(244,63,94,0.1)]',
    info: 'border-indigo-500/30 bg-indigo-500/10 text-indigo-400 shadow-[0_0_30px_rgba(99,102,241,0.1)]'
  };

  return (
    <div className={`flex items-center gap-4 px-6 py-4 rounded-2xl border backdrop-blur-xl animate-in slide-in-from-right-8 fade-in duration-500 ${colors[type]}`}>
      <div className="p-2 bg-black/20 rounded-lg">{icons[type]}</div>
      <p className="text-xs font-black uppercase tracking-widest italic">{message}</p>
    </div>
  );
}
