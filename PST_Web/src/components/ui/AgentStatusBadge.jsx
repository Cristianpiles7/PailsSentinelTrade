import React from 'react'

// Etiqueta del estado del agente de un símbolo (PST_LLM/estado_agentes.py vía /api/symbols → s.agente).
// Dice qué hace ahora el agente: en posición, esperando un setup concreto, buscando, frenado o bloqueado.

const ESTADOS = {
  posicion: { label: 'En posición', cls: 'text-indigo-300 bg-indigo-500/10 border-indigo-500/30', punto: 'bg-indigo-400 animate-pulse' },
  esperando: { label: 'Esperando', cls: 'text-amber-300 bg-amber-500/10 border-amber-500/30', punto: 'bg-amber-400 animate-pulse' },
  senal: { label: 'Señal', cls: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/30', punto: 'bg-emerald-400 animate-pulse' },
  frenada: { label: 'Entrada frenada', cls: 'text-orange-300 bg-orange-500/10 border-orange-500/30', punto: 'bg-orange-400' },
  buscando: { label: 'Buscando', cls: 'text-zinc-300 bg-zinc-800/60 border-white/10', punto: 'bg-zinc-400' },
  bloqueado: { label: 'Bloqueado', cls: 'text-rose-300 bg-rose-500/10 border-rose-500/30', punto: 'bg-rose-400' },
  parado: { label: 'Sin actividad', cls: 'text-zinc-500 bg-zinc-900/60 border-white/5', punto: 'bg-zinc-600' },
  sin_dato: { label: 'Sin datos', cls: 'text-zinc-500 bg-zinc-900/60 border-white/5', punto: 'bg-zinc-600' },
}

const REGIMENES = {
  tendencia_fuerte: { label: 'Tendencia fuerte', cls: 'text-sky-300 border-sky-500/30' },
  tendencia: { label: 'Tendencia', cls: 'text-sky-400/80 border-sky-500/20' },
  rango: { label: 'Rango', cls: 'text-violet-300 border-violet-500/30' },
  ruptura: { label: 'Ruptura', cls: 'text-fuchsia-300 border-fuchsia-500/30' },
}

const SETUPS = { breakout: 'continuación/ruptura', pullback: 'pullback', reversion: 'reversión', rechazo_nivel: 'rechazo de nivel' }

function hace(edad) {
  if (edad == null) return ''
  if (edad < 60) return `hace ${edad}s`
  if (edad < 3600) return `hace ${Math.round(edad / 60)} min`
  return `hace ${Math.round(edad / 3600)} h`
}

export function AgentStatusBadge({ agente, compact = false }) {
  if (!agente) {
    return compact ? null : (
      <div className="text-[11px] font-bold text-zinc-600 uppercase tracking-widest">Agente · sin dato</div>
    )
  }
  const est = ESTADOS[agente.estado] || ESTADOS.buscando
  const reg = REGIMENES[agente.regimen]
  const lado = agente.lado === 'buy' ? 'COMPRA' : agente.lado === 'sell' ? 'VENTA' : null
  const colorLado = agente.lado === 'buy' ? 'text-emerald-400' : 'text-rose-400'
  const setup = SETUPS[agente.setup]
  const titulo = [agente.agente === 'scalping' ? 'Agente de scalping' : 'Agente de la flota', agente.texto, hace(agente.edad_s)]
    .filter(Boolean).join(' · ')

  if (compact) {
    return (
      <span title={titulo} className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md border text-[10px] font-bold uppercase tracking-wider whitespace-nowrap ${est.cls}`}>
        <span className={`w-1.5 h-1.5 rounded-full ${est.punto}`} />
        {est.label}{lado ? ` · ${lado}` : ''}
      </span>
    )
  }

  return (
    <div className="flex flex-col gap-1.5" title={titulo}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-bold text-zinc-600 uppercase tracking-widest">
          {agente.agente === 'scalping' ? 'Scalping' : 'Agente'}
        </span>
        <div className="flex items-center gap-1.5 min-w-0">
          {reg && (
            <span className={`px-1.5 py-0.5 rounded-md border bg-black/30 text-[10px] font-bold uppercase tracking-wider whitespace-nowrap ${reg.cls}`}>
              {reg.label}
            </span>
          )}
          <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md border text-[10px] font-bold uppercase tracking-wider whitespace-nowrap ${est.cls}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${est.punto}`} />
            {est.label}
            {lado && <span className={colorLado}>{lado}</span>}
          </span>
        </div>
      </div>
      <div className="text-[11px] leading-snug text-zinc-400 line-clamp-2 min-h-[2.4em]">
        {setup && <span className="text-zinc-300 font-bold">{setup}: </span>}
        {agente.texto || <span className="text-zinc-600">—</span>}
      </div>
    </div>
  )
}
