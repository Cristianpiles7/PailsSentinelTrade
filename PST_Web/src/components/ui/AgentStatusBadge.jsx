import React from 'react'

// Etiqueta del estado del agente de un símbolo (PST_LLM/estado_agentes.py vía /api/symbols → s.agente).
// Una línea de chips (régimen + estado) y una línea corta con lo esencial; el texto largo va en el tooltip.

export const ESTADOS = {
  posicion: { label: 'En posición', cls: 'text-indigo-300 bg-indigo-500/10 border-indigo-500/30', punto: 'bg-indigo-400 animate-pulse' },
  orden: { label: 'Orden lista', cls: 'text-amber-200 bg-amber-500/15 border-amber-400/40', punto: 'bg-amber-300 animate-pulse' },
  esperando: { label: 'Esperando', cls: 'text-amber-300/90 bg-amber-500/5 border-amber-500/20', punto: 'bg-amber-400/70' },
  senal: { label: 'Señal', cls: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/30', punto: 'bg-emerald-400 animate-pulse' },
  frenada: { label: 'Frenada', cls: 'text-orange-300 bg-orange-500/10 border-orange-500/30', punto: 'bg-orange-400' },
  buscando: { label: 'Buscando', cls: 'text-zinc-300 bg-zinc-800/60 border-white/10', punto: 'bg-zinc-400' },
  bloqueado: { label: 'Bloqueado', cls: 'text-rose-300 bg-rose-500/10 border-rose-500/30', punto: 'bg-rose-400' },
  parado: { label: 'Sin actividad', cls: 'text-zinc-500 bg-zinc-900/60 border-white/5', punto: 'bg-zinc-600' },
  sin_dato: { label: 'Sin datos', cls: 'text-zinc-500 bg-zinc-900/60 border-white/5', punto: 'bg-zinc-600' },
}

export const REGIMENES = {
  tendencia_fuerte: { label: 'Tend. fuerte', cls: 'text-sky-300 border-sky-500/30' },
  tendencia: { label: 'Tendencia', cls: 'text-sky-400/80 border-sky-500/20' },
  rango: { label: 'Rango', cls: 'text-violet-300 border-violet-500/30' },
  ruptura: { label: 'Ruptura', cls: 'text-fuchsia-300 border-fuchsia-500/30' },
}

export const SETUPS = { breakout: 'continuación', pullback: 'pullback', reversion: 'reversión', rechazo_nivel: 'rechazo de nivel' }

export function textoOrden(o) {
  if (!o) return ''
  const lado = o.lado === 'buy' ? 'Compra' : 'Venta'
  const como = o.tipo === 'stop' ? (o.lado === 'buy' ? 'si sube a' : 'si baja a') : (o.lado === 'buy' ? 'si baja a' : 'si sube a')
  return `${lado} ${como} ${o.precio}`
}

export function hace(edad) {
  if (edad == null) return ''
  if (edad < 60) return `hace ${edad}s`
  if (edad < 3600) return `hace ${Math.round(edad / 60)} min`
  return `hace ${Math.round(edad / 3600)} h`
}

// Línea corta: lo que el agente está haciendo, en pocas palabras.
function lineaCorta(a) {
  if (a.estado === 'orden' && a.orden) {
    const extra = [a.orden.invalidacion != null && `anula ${a.orden.invalidacion}`, a.orden.min_restantes != null && `${a.orden.min_restantes} min`]
    return [textoOrden(a.orden), ...extra].filter(Boolean).join(' · ')
  }
  if (a.estado === 'posicion') return SETUPS[a.setup] ? `por ${SETUPS[a.setup]}` : ''
  if (a.estado === 'esperando' || (a.estado === 'buscando' && a.esperando)) return a.esperando || a.texto
  return a.texto
}

export function AgentStatusBadge({ agente, compact = false }) {
  if (!agente) {
    return compact ? null : (
      <div className="text-[11px] font-bold text-zinc-600 uppercase tracking-widest">Agente · sin dato</div>
    )
  }
  const est = ESTADOS[agente.estado] || ESTADOS.buscando
  const reg = REGIMENES[agente.regimen]
  const lado = agente.lado === 'buy' ? 'compra' : agente.lado === 'sell' ? 'venta' : null
  const colorLado = agente.lado === 'buy' ? 'text-emerald-400' : 'text-rose-400'
  const linea = lineaCorta(agente)
  const titulo = [agente.agente === 'scalping' ? 'Agente de scalping' : 'Agente de la flota', agente.texto, hace(agente.edad_s)]
    .filter(Boolean).join(' · ')

  const chipEstado = (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md border text-[10px] font-bold uppercase tracking-wider whitespace-nowrap ${est.cls}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${est.punto}`} />
      {est.label}
      {lado && agente.estado !== 'orden' && <span className={colorLado}>{lado}</span>}
    </span>
  )

  if (compact) return <span title={titulo}>{chipEstado}</span>

  return (
    <div className="flex flex-col gap-1" title={titulo}>
      <div className="flex items-center gap-1.5 min-w-0">
        {chipEstado}
        {reg && (
          <span className={`px-1.5 py-0.5 rounded-md border bg-black/30 text-[10px] font-bold uppercase tracking-wider whitespace-nowrap ${reg.cls}`}>
            {reg.label}
          </span>
        )}
      </div>
      <div className={`text-[11px] leading-tight truncate ${agente.estado === 'orden' ? 'text-amber-100 font-bold' : 'text-zinc-400'}`}>
        {linea || <span className="text-zinc-600">—</span>}
      </div>
    </div>
  )
}
