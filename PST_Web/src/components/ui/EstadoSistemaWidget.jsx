import React, { useEffect, useState } from 'react'
import axios from 'axios'
import { Activity } from 'lucide-react'

// Estado de la app en la cabecera: MT5, antigüedad del calendario de noticias y de la última copia de la BBDD.
// Se consulta /api/health (PST_API/main.py). Si la API no responde, lo dice en vez de quedarse con datos viejos.
const REFRESCO_MS = 30000
const MIN_CALENDARIO = 6 * 60        // por encima, el calendario se considera desactualizado (ver news_manager.esta_obsoleto)
const MIN_COPIA = 36 * 60            // la copia diaria debe tener menos de 36 h

function minutosDesde(t) {
  return t == null ? null : (Date.now() / 1000 - t) / 60
}

function formatear(min) {
  if (min == null) return '—'
  if (min < 60) return `${Math.round(min)} min`
  return `${Math.round(min / 60)} h`
}

function Punto({ ok, texto }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className={`inline-block w-1.5 h-1.5 rounded-full ${ok ? 'bg-emerald-400' : 'bg-rose-500 animate-pulse'}`} />
      <span className={ok ? 'text-zinc-400' : 'text-rose-300'}>{texto}</span>
    </span>
  )
}

export function EstadoSistemaWidget({ url }) {
  const [salud, setSalud] = useState(null)
  const [error, setError] = useState(false)

  const cargar = () => {
    axios.get(url, { headers: { 'X-PST-Token': localStorage.getItem('pst_token') || '' } })
      .then(({ data }) => { setSalud(data); setError(false) })
      .catch(() => setError(true))
  }

  useEffect(() => {
    cargar()
    const refresco = setInterval(cargar, REFRESCO_MS)
    return () => clearInterval(refresco)
  }, [url])

  if (error) {
    return (
      <div className="flex items-center gap-2 bg-rose-500/5 px-4 py-2.5 rounded-2xl border border-rose-500/40 text-[11px] font-bold uppercase tracking-widest">
        <Activity size={14} className="text-rose-400" />
        <span className="text-rose-300">API sin respuesta</span>
      </div>
    )
  }
  if (!salud) return null

  const calendario = minutosDesde(salud.calendario_t)
  const copia = minutosDesde(salud.copia_t)

  return (
    <div className="flex items-center gap-4 bg-zinc-900/50 px-5 py-2.5 rounded-2xl border border-zinc-800/50 text-[11px] font-bold uppercase tracking-widest">
      <Activity size={14} className="text-zinc-500" />
      <Punto ok={salud.mt5} texto={salud.mt5 ? 'MT5 conectado' : 'MT5 desconectado'} />
      <Punto ok={calendario != null && calendario < MIN_CALENDARIO} texto={`Noticias ${formatear(calendario)}`} />
      <Punto ok={copia != null && copia < MIN_COPIA} texto={`Copia ${formatear(copia)}`} />
    </div>
  )
}
