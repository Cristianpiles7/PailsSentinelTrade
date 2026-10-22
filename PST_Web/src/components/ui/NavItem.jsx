import React from 'react'

export function NavItem({ icon, active = false, onClick, label, badge = 0, showLabel = false }) {
  return (
    <button
      onClick={onClick}
      className={`${showLabel ? 'p-3 gap-1 flex-col' : 'p-4'} rounded-2xl transition-all duration-500 group relative flex items-center justify-center ${active ? 'bg-indigo-600/20 text-indigo-400 border border-indigo-500/30' : 'text-zinc-700 hover:text-zinc-200 hover:bg-zinc-800/40'}`}
      title={label}
    >
      {icon}
      {showLabel && <span className="text-xs font-semibold tracking-wide leading-none">{label}</span>}
      {badge > 0 && (
        <div className="absolute -top-1 -right-1 min-w-[20px] h-5 bg-rose-600 text-white text-xs font-bold flex items-center justify-center rounded-full border-2 border-[#070707] animate-in zoom-in duration-300">
          {badge}
        </div>
      )}
      {active && <div className="absolute -right-0.5 top-1/2 -translate-y-1/2 w-2 h-10 bg-indigo-500 rounded-l-full" />}
    </button>
  )
}
