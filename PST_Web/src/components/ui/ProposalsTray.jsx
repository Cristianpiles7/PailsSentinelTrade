import React, { useState, useEffect, useCallback } from 'react'
import axios from 'axios'
import { Inbox, Check, X, Eye, RefreshCw, AlertTriangle } from 'lucide-react'

/**
 * Bandeja de propuestas de la flota de agentes.
 *
 * Es la pieza que cierra el circuito: los agentes escriben en la tabla `proposals` en
 * estado 'propuesta' y NO aplican nada; aquí se decide. Sin esta vista, las propuestas se
 * quedan en filas de SQLite que nadie mira.
 *
 * Las transiciones las valida el servidor contra la máquina de estados, así que un 409
 * significa "esa transición no está permitida, o alguien decidió antes que tú" — y se
 * refresca la lista en vez de insistir.
 */

const ESTADO_COLOR = {
  propuesta: 'text-amber-400 bg-amber-500/10 border-amber-500/20',
  shadow:    'text-sky-400 bg-sky-500/10 border-sky-500/20',
  aprobada:  'text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
  viva:      'text-emerald-300 bg-emerald-500/20 border-emerald-500/30',
  rechazada: 'text-zinc-500 bg-zinc-800 border-zinc-700/50',
  revertida: 'text-rose-400 bg-rose-500/10 border-rose-500/20',
}

function Evidencia({ json }) {
  if (!json) return null
  let datos
  try { datos = JSON.parse(json) } catch { return <p className="text-[10px] font-mono text-zinc-600">{json}</p> }
  const filas = Object.entries(datos).filter(([, v]) => v !== null && v !== undefined)
  if (!filas.length) return null
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2">
      {filas.map(([k, v]) => (
        <span key={k} className="text-[10px] font-mono text-zinc-500">
          <span className="text-zinc-600">{k}:</span>{' '}
          <span className="text-zinc-300">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</span>
        </span>
      ))}
    </div>
  )
}

export function ProposalsTray({ pollMs = 20000 }) {
  const [props, setProps] = useState([])
  const [cargando, setCargando] = useState(true)
  const [error, setError] = useState(null)
  const [soloPendientes, setSoloPendientes] = useState(true)

  const cargar = useCallback(async () => {
    try {
      const { data } = await axios.get('/api/agents/proposals', {
        params: soloPendientes ? { state: 'propuesta' } : {},
      })
      setProps(Array.isArray(data) ? data : [])
      setError(null)
    } catch {
      setError('No se pudieron cargar las propuestas')
    } finally {
      setCargando(false)
    }
  }, [soloPendientes])

  useEffect(() => {
    cargar()
    const id = setInterval(cargar, pollMs)
    return () => clearInterval(id)
  }, [cargar, pollMs])

  const decidir = async (id, state) => {
    try {
      await axios.post(`/api/agents/proposals/${id}/decide`, { state })
    } catch (e) {
      // 409 = transición no permitida o alguien decidió antes. Refrescar es la respuesta
      // correcta: el estado real está en el servidor, no en esta pestaña.
      if (e?.response?.status === 409) setError(e.response.data?.detail || 'La propuesta cambió de estado')
      else setError('No se pudo aplicar la decisión')
    }
    cargar()
  }

  return (
    <div className="bg-[#090909] border border-zinc-800/60 rounded-2xl p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="p-1.5 rounded-lg border text-amber-400 bg-amber-500/10 border-amber-500/20">
            <Inbox size={18} />
          </div>
          <div>
            <p className="text-[7px] font-black text-zinc-500 uppercase tracking-widest">Bandeja de propuestas</p>
            <p className="text-sm font-black italic tracking-tighter text-white">
              {cargando ? '...' : `${props.length} ${soloPendientes ? 'pendiente(s)' : 'en total'}`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setSoloPendientes(v => !v)}
            className="px-3 py-1.5 rounded-xl text-[9px] font-black uppercase tracking-widest text-zinc-500 hover:text-zinc-300 transition-all"
          >
            {soloPendientes ? 'Ver todas' : 'Sólo pendientes'}
          </button>
          <button onClick={cargar} className="p-2 rounded-xl text-zinc-500 hover:text-zinc-300 transition-all" title="Refrescar">
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 mb-3 text-[10px] text-amber-400">
          <AlertTriangle size={12} /> {error}
        </div>
      )}

      {!cargando && props.length === 0 && (
        <p className="text-[10px] text-zinc-600 py-6 text-center">
          Sin propuestas. Los agentes escriben aquí cuando encuentran algo con evidencia.
        </p>
      )}

      <div className="space-y-3">
        {props.map(p => (
          <div key={p.id} className="border border-zinc-800/60 rounded-xl p-4 bg-black/30">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`px-2 py-0.5 rounded-md border text-[9px] font-black uppercase tracking-widest ${ESTADO_COLOR[p.state] || ESTADO_COLOR.rechazada}`}>
                    {p.state}
                  </span>
                  <span className="text-[10px] font-black uppercase tracking-widest text-zinc-400">{p.kind}</span>
                  <span className="text-sm font-black italic tracking-tighter text-white">{p.target}</span>
                  {p.judge_delta_eur !== null && p.judge_delta_eur !== undefined && (
                    <span className={`text-[11px] font-black ${p.judge_delta_eur >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
                      {p.judge_delta_eur >= 0 ? '+' : ''}{Number(p.judge_delta_eur).toFixed(2)} €
                    </span>
                  )}
                </div>
                {p.rationale && <p className="text-[11px] text-zinc-400 mt-2 leading-relaxed">{p.rationale}</p>}
                <Evidencia json={p.evidence_json} />
              </div>

              {p.state === 'propuesta' && (
                <div className="flex items-center gap-1 shrink-0">
                  <button onClick={() => decidir(p.id, 'shadow')} title="Probar en shadow"
                    className="p-2 rounded-lg text-sky-400 hover:bg-sky-500/10 transition-all"><Eye size={15} /></button>
                  <button onClick={() => decidir(p.id, 'aprobada')} title="Aprobar"
                    className="p-2 rounded-lg text-emerald-400 hover:bg-emerald-500/10 transition-all"><Check size={15} /></button>
                  <button onClick={() => decidir(p.id, 'rechazada')} title="Rechazar"
                    className="p-2 rounded-lg text-zinc-500 hover:bg-rose-500/10 hover:text-rose-400 transition-all"><X size={15} /></button>
                </div>
              )}
            </div>

            <p className="text-[9px] font-mono text-zinc-700 mt-3">
              #{p.id} · run {p.agent_run_id ?? '—'} · {p.created_at}
              {p.decided_by && ` · decidida por ${p.decided_by}`}
            </p>
          </div>
        ))}
      </div>
    </div>
  )
}
