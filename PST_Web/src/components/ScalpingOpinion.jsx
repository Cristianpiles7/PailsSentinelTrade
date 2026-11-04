import { useEffect, useRef, useState } from 'react'
import { Zap, Bot } from 'lucide-react'
import { usePollingWhenVisible } from '../hooks/usePolling'
import { ESTADOS, REGIMENES, SETUPS, textoOrden, hace } from './ui/AgentStatusBadge'

// Lo que el agente (scalping o flota) ve de este simbolo: regimen, orden pendiente, posicion, ultimas decisiones y
// ordenes. Datos de /api/estrategias/agente/detalle/{symbol} (PST_LLM/estado_agentes.detalle_agente).

const ACCION = { buy: ['COMPRA', 'text-emerald-400'], sell: ['VENTA', 'text-rose-400'], close: ['CIERRE', 'text-indigo-300'], hold: ['HOLD', 'text-zinc-400'] }
const ESTADO_ORDEN = {
  pendiente: 'text-amber-300', disparada: 'text-emerald-400', cancelada: 'text-zinc-500', caducada: 'text-zinc-500',
  invalidada: 'text-rose-300', rechazada: 'text-orange-300',
}

function Chip({ cls, children }) {
  return <span className={`px-2 py-0.5 rounded-md border text-[10px] font-bold uppercase tracking-wider whitespace-nowrap ${cls}`}>{children}</span>
}

function Dato({ k, v, cls = 'text-zinc-200' }) {
  return (
    <div className="flex flex-col">
      <span className="text-[9px] font-bold uppercase tracking-widest text-zinc-600">{k}</span>
      <span className={`text-xs font-bold ${cls}`}>{v ?? '—'}</span>
    </div>
  )
}

export function ScalpingOpinion({ api, API_BASE, symbol }) {
  const [d, setD] = useState(null)
  const cargarRef = useRef(null)

  useEffect(() => {
    let vivo = true
    const cargar = async () => {
      try {
        const r = await api.get(`${API_BASE}/estrategias/agente/detalle/${encodeURIComponent(symbol)}`)
        if (vivo) setD(r.data)
      } catch (e) { /* se reintenta en el siguiente sondeo */ }
    }
    cargarRef.current = cargar
    cargar()
    return () => { vivo = false }
  }, [api, API_BASE, symbol])

  usePollingWhenVisible(() => cargarRef.current?.(), 5000)

  if (!d) return null
  const e = d.estado || {}
  const est = ESTADOS[e.estado] || ESTADOS.buscando
  const reg = REGIMENES[e.regimen]
  const p = d.posicion
  const o = e.orden
  const ultima = d.decisiones?.[0]
  const flota = d.tipo === 'flota'
  const Icono = flota ? Bot : Zap
  // La flota escribe 'Lectura: ...' y 'Riesgo: ...' en dos lineas: se muestra en una sin repetir la etiqueta.
  const lectura = String(ultima?.razon || '').replace(/^\s*Lectura:\s*/i, '').replace(/\s*\n+\s*/g, ' · ')

  return (
    <div className="col-span-full grid lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] gap-x-8 gap-y-4">
      {/* Izquierda: que hace ahora */}
      <div className="flex flex-col gap-3 min-w-0">
      {/* Cabecera */}
      <div className="flex flex-wrap items-center gap-3">
        <Icono size={16} className={flota ? 'text-indigo-400' : 'text-amber-300'} />
        <span className="text-xs font-bold uppercase tracking-[0.3em] text-zinc-400">{flota ? 'Agente de la flota' : 'Agente de scalping'}</span>
        <Chip cls={est.cls}>{est.label}</Chip>
        {reg && <Chip cls={`bg-black/30 ${reg.cls}`}>{reg.label}</Chip>}
        {e.edad_s != null && <span className="text-xs text-zinc-600">última decisión {hace(e.edad_s)}</span>}
        {d.hoy && (
          <span className="ml-auto text-xs text-zinc-500">
            hoy: {d.hoy.ops} ops · <span className={(d.hoy.r || 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}>{(d.hoy.r || 0) >= 0 ? '+' : ''}{Number(d.hoy.r || 0).toFixed(2)} R</span>
          </span>
        )}
      </div>

      {/* Lo que va a hacer: orden, posicion o espera */}
      {o && (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 bg-amber-500/5 border border-amber-400/30 rounded-xl px-4 py-3">
          <div className="flex flex-col">
            <span className="text-[9px] font-bold uppercase tracking-widest text-amber-300/70">Orden pendiente</span>
            <span className="text-sm font-bold text-amber-100">{textoOrden(o)}</span>
          </div>
          <Dato k="Tipo" v={o.tipo === 'stop' ? 'ruptura' : 'retroceso'} />
          <Dato k="Se anula en" v={o.invalidacion} cls="text-rose-300" />
          <Dato k="Setup" v={SETUPS[o.setup] || o.setup} />
          {o.timeframe && <Dato k="Marco" v={o.timeframe} />}
          <Dato k="Caduca en" v={o.min_restantes != null ? `${o.min_restantes} min` : null} />
          {o.confianza != null && <Dato k="Confianza" v={Number(o.confianza).toFixed(2)} />}
        </div>
      )}
      {p && (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 bg-indigo-500/5 border border-indigo-500/30 rounded-xl px-4 py-3">
          <div className="flex flex-col">
            <span className="text-[9px] font-bold uppercase tracking-widest text-indigo-300/70">Posición {p.modo === 'live' ? 'demo' : 'simulada'}</span>
            <span className={`text-sm font-bold ${p.lado === 'buy' ? 'text-emerald-400' : 'text-rose-400'}`}>{p.lado === 'buy' ? 'Compra' : 'Venta'} en {p.precio}</span>
          </div>
          <Dato k="Stop" v={p.be_movido ? `${Number(p.sl).toFixed(2)} (BE)` : Number(p.sl).toFixed(2)} cls="text-rose-300" />
          <Dato k="Objetivo" v={Number(p.tp).toFixed(2)} cls="text-emerald-300" />
          <Dato k="Setup" v={SETUPS[p.setup] || p.setup} />
          {p.origen && <Dato k="Entrada" v={p.origen === 'orden' ? 'por orden' : 'a mercado'} />}
          {p.mfe_r != null && <Dato k="Máx. a favor / contra" v={`${Number(p.mfe_r || 0).toFixed(2)} / ${Number(p.mae_r || 0).toFixed(2)} R`} />}
          {p.flotante_eur != null && <Dato k="Flotante" v={`${p.flotante_eur >= 0 ? '+' : ''}${Number(p.flotante_eur).toFixed(2)} €`} cls={p.flotante_eur >= 0 ? 'text-emerald-400' : 'text-rose-400'} />}
          {p.motivo_entrada && <p className="basis-full text-[11px] text-zinc-400 truncate" title={p.motivo_entrada}>{p.motivo_entrada}</p>}
        </div>
      )}
      {!o && !p && (e.esperando || e.estado === 'bloqueado') && (
        <div className="bg-zinc-950/60 border border-zinc-800/60 rounded-xl px-4 py-3">
          <span className="text-[9px] font-bold uppercase tracking-widest text-zinc-600">{e.estado === 'bloqueado' ? 'Bloqueado' : 'Esperando'}</span>
          <p className="text-sm font-bold text-zinc-200">{e.estado === 'bloqueado' ? e.texto : e.esperando}</p>
        </div>
      )}
      {lectura && <p className="text-[11px] text-zinc-400 leading-relaxed line-clamp-3" title={ultima.razon}><span className="font-bold text-indigo-300 uppercase tracking-wider">Lectura:</span> {lectura}</p>}
      </div>

      {/* Derecha: historial corto */}
      <div className="flex flex-col gap-4 min-w-0 lg:border-l lg:border-zinc-800/60 lg:pl-8">
        <div className="flex flex-col gap-1">
          <span className="text-[9px] font-bold uppercase tracking-widest text-zinc-600">Últimas decisiones</span>
          {(d.decisiones || []).slice(0, 5).map((x, i) => {
            const [txt, cls] = ACCION[x.accion] || [String(x.accion || '-').toUpperCase(), 'text-zinc-400']
            return (
              <div key={i} className="flex items-center gap-2 text-[11px] min-w-0" title={[x.razon, x.resultado].filter(Boolean).join(' · ')}>
                <span className="text-zinc-600 tabular-nums">{(x.ts || '').slice(11, 16)}</span>
                <span className={`font-bold w-14 shrink-0 ${cls}`}>{txt}</span>
                <span className="text-zinc-500 w-20 shrink-0 truncate">{REGIMENES[x.regimen]?.label || ''}</span>
                <span className="text-zinc-300 truncate">{x.esperando || (x.resultado && x.resultado !== '-' ? x.resultado : x.razon)}</span>
              </div>
            )
          })}
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[9px] font-bold uppercase tracking-widest text-zinc-600">Órdenes recientes</span>
          {(d.ordenes || []).length === 0 && <span className="text-[11px] text-zinc-600">Aún ninguna</span>}
          {(d.ordenes || []).slice(0, 4).map((x, i) => (
            <div key={i} className="flex items-center gap-2 text-[11px] min-w-0" title={x.resultado || ''}>
              <span className="text-zinc-600 tabular-nums">{(x.ts || '').slice(11, 16)}</span>
              <span className="text-zinc-200 truncate">{textoOrden(x)}</span>
              <span className={`ml-auto font-bold uppercase text-[10px] ${ESTADO_ORDEN[x.estado] || 'text-zinc-400'}`}>{x.estado}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
