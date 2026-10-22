import React from 'react'
import { ArrowUp, ArrowDown, Minus } from 'lucide-react'

// Medidor de cercanía a compra (+) o venta (−) que entrega el agente puntuador.
// Es solo visual: no es una señal de entrada.
export function ProximityGauge({ proximidad, compact = false }) {
  if (!proximidad || proximidad.puntuacion == null) {
    return (
      <div className="flex items-center gap-2 text-[11px] font-bold text-zinc-600 uppercase tracking-widest">
        Puntuación · sin dato
      </div>
    )
  }

  const p = Math.max(-100, Math.min(100, proximidad.puntuacion))
  const anterior = proximidad.anterior
  const delta = anterior == null ? 0 : p - anterior
  const abierto = proximidad.mercado_abierto !== false
  const lado = p > 15 ? 'COMPRA' : p < -15 ? 'VENTA' : 'NEUTRO'
  const color = p > 15 ? 'text-emerald-400' : p < -15 ? 'text-rose-400' : 'text-zinc-400'
  const barra = p > 0 ? 'bg-emerald-500/70' : 'bg-rose-500/70'
  const posicion = ((p + 100) / 200) * 100
  const Flecha = delta > 0 ? ArrowUp : delta < 0 ? ArrowDown : Minus
  const colorFlecha = delta > 0 ? 'text-emerald-400' : delta < 0 ? 'text-rose-400' : 'text-zinc-600'

  return (
    <div
      className={`flex flex-col gap-1.5 ${abierto ? '' : 'opacity-50'}`}
      title={`${proximidad.frase || ''} · solo visual, no es una señal de entrada`}
    >
      <div className="flex justify-between items-center">
        <span className="text-[11px] font-bold text-zinc-600 uppercase tracking-widest">Puntuación</span>
        <span className={`flex items-center gap-1 text-[11px] font-bold ${color}`}>
          <Flecha size={12} className={colorFlecha} />
          {p > 0 ? '+' : ''}{p} · {lado}
        </span>
      </div>
      <div className={`relative w-full ${compact ? 'h-1.5' : 'h-2'} rounded-full bg-zinc-900 border border-zinc-800`}>
        <div className="absolute left-1/2 top-[-2px] bottom-[-2px] w-px bg-zinc-600" />
        <div
          className={`absolute top-0 bottom-0 rounded-full ${barra} transition-all duration-700`}
          style={{ left: `${Math.min(50, posicion)}%`, right: `${Math.min(50, 100 - posicion)}%` }}
        />
        <div
          className="absolute top-1/2 w-2.5 h-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white shadow-md transition-all duration-700"
          style={{ left: `${posicion}%` }}
        />
      </div>
      {!compact && (
        <div className="flex justify-between text-[9px] font-bold text-zinc-700 uppercase tracking-widest">
          <span>Venta</span>
          <span>Neutro</span>
          <span>Compra</span>
        </div>
      )}
    </div>
  )
}
