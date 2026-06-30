import React from 'react'

export function NavItem({ icon, active = false, onClick, label, badge = 0 }) {
  return (
    <button
      onClick={onClick}
      className={`p-4 rounded-[1.5rem] transition-all duration-500 group relative flex items-center justify-center ${active ? 'bg-indigo-600/20 text-indigo-400 shadow-[0_0_30px_rgba(99,102,241,0.2)] border border-indigo-500/30' : 'text-zinc-700 hover:text-zinc-200 hover:bg-zinc-800/40'}`}
      title={label}
    >
      {icon}
      {badge > 0 && (
        <div className="absolute -top-1 -right-1 min-w-[20px] h-5 bg-rose-600 text-white text-[10px] font-black flex items-center justify-center rounded-full border-2 border-[#070707] shadow-[0_0_15px_rgba(225,29,72,0.4)] animate-in zoom-in duration-300">
          {badge}
        </div>
      )}
      {active && <div className="absolute -right-0.5 top-1/2 -translate-y-1/2 w-2 h-10 bg-indigo-500 rounded-l-full shadow-[0_0_20px_#6366f1]" />}
    </button>
  )
}
