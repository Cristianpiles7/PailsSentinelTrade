import React, { useEffect, useState } from 'react'
import axios from 'axios'
import { Clock, ShieldAlert } from 'lucide-react'

// Cuenta atras al proximo evento restringido de FTMO (PST_LLM/calendario.py), o al fin del bloqueo si ya esta activo.
const REFRESCO_MS = 60000

function formatear(ms) {
  const total = Math.max(0, Math.floor(ms / 1000))
  const d = Math.floor(total / 86400)
  const h = String(Math.floor((total % 86400) / 3600)).padStart(2, '0')
  const m = String(Math.floor((total % 3600) / 60)).padStart(2, '0')
  const s = String(total % 60).padStart(2, '0')
  return `${d > 0 ? `${d}d ` : ''}${h}:${m}:${s}`
}

function hora(iso) {
  return new Date(iso).toLocaleTimeString('es-ES', { timeZone: 'Europe/Madrid', hour: '2-digit', minute: '2-digit' })
}

export function CalendarioRestringidoWidget({ url }) {
  const [evento, setEvento] = useState(null)
  const [error, setError] = useState(false)
  const [ahora, setAhora] = useState(Date.now())

  const cargar = () => {
    axios.get(url)
      .then(({ data }) => {
        setError(Boolean(data?.error))
        setEvento(data?.error ? null : data)
      })
      .catch(() => setError(true))
  }

  useEffect(() => {
    cargar()
    const refresco = setInterval(cargar, REFRESCO_MS)
    return () => clearInterval(refresco)
  }, [url])

  useEffect(() => {
    const reloj = setInterval(() => setAhora(Date.now()), 1000)
    return () => clearInterval(reloj)
  }, [])

  // Si el bloqueo ya termino, pide el siguiente evento sin esperar al refresco de 60 s
  useEffect(() => {
    if (evento && ahora > new Date(evento.fin_bloqueo_utc).getTime()) cargar()
  }, [ahora, evento])

  if (error) {
    return (
      <div className="flex items-center gap-4 bg-zinc-900/50 px-5 py-2.5 rounded-2xl border border-zinc-800/50" title="No se pudo leer el calendario restringido">
        <ShieldAlert size={16} className="text-rose-400" />
        <span className="text-xs font-bold text-rose-400 uppercase tracking-widest">Calendario no disponible</span>
      </div>
    )
  }

  if (!evento) {
    return (
      <div className="flex items-center gap-4 bg-zinc-900/50 px-5 py-2.5 rounded-2xl border border-zinc-800/50">
        <Clock size={16} className="text-zinc-500" />
        <span className="text-xs font-bold text-zinc-500 uppercase tracking-widest">Sin eventos restringidos esta semana</span>
      </div>
    )
  }

  const inicio = new Date(evento.inicio_bloqueo_utc).getTime()
  const fin = new Date(evento.fin_bloqueo_utc).getTime()
  const activo = ahora >= inicio && ahora <= fin
  const meta = activo ? fin : inicio
  const etiqueta = activo ? 'BLOQUEO ACTIVO · libera en' : 'Bloqueo de entradas en'
  const tooltip = `${evento.divisa} · ${evento.titulo} · evento ${hora(evento.instante_utc)} · bloqueo ${hora(evento.inicio_bloqueo_utc)}–${hora(evento.fin_bloqueo_utc)} (Madrid)`

  return (
    <div
      title={tooltip}
      className={`flex items-center gap-4 px-5 py-2.5 rounded-2xl border transition-all duration-500 ${activo ? 'border-rose-500/40 bg-rose-500/5' : 'bg-zinc-900/50 border-zinc-800/50'}`}
    >
      <ShieldAlert size={16} className={activo ? 'text-rose-400 animate-pulse' : 'text-zinc-500'} />
      <div className="flex flex-col">
        <span className={`text-[11px] font-bold uppercase tracking-widest leading-none mb-1 ${activo ? 'text-rose-400' : 'text-zinc-500'}`}>
          {etiqueta}
        </span>
        <div className="flex items-center gap-2">
          <span className="text-xs font-bold text-zinc-300 truncate max-w-[150px]">
            {evento.divisa} {evento.titulo}
          </span>
          <span className={`text-[11px] font-bold font-mono px-1.5 py-0.5 rounded-md bg-zinc-800 border border-white/5 ${activo ? 'text-rose-300' : 'text-indigo-400'}`}>
            {formatear(meta - ahora)}
          </span>
        </div>
      </div>
    </div>
  )
}
