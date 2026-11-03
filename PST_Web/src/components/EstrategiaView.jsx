import { useCallback, useEffect, useMemo, useState } from 'react'
import { Layers, Bot, Activity } from 'lucide-react'
import { ConfigForm } from './ConfigForm'
import { LlmCriterioPanel } from './LlmCriterioPanel'

// Una pantalla por estrategia. Pestañas: Resumen (que hace y como va), Simbolos (los agentes de esa
// estrategia, con su configuracion propia desplegable) y Configuracion (parametros de la estrategia).
// Las estrategias vienen de la API: una nueva (EAs de MT5 mas adelante) aparece sin tocar esta pantalla.

const SECCIONES = [['resumen', 'Resumen'], ['simbolos', 'Símbolos'], ['config', 'Configuración']]

// Que agentes pertenecen a cada estrategia. `ambos` cuenta en las dos.
const incluye = (agente, id) => agente.estrategia === id || agente.estrategia === 'ambos'

const TARJETA = 'bg-[#050505] border border-zinc-800/40 rounded-2xl p-4'

function Stat({ etiqueta, valor }) {
  return (
    <div className={TARJETA}>
      <p className="text-[11px] font-bold text-zinc-500 uppercase tracking-widest">{etiqueta}</p>
      <p className="text-2xl font-bold text-white mt-1">{valor}</p>
    </div>
  )
}

function FilaSimbolo({ agente, estrategia, esquema, API_BASE, api, abierta, onAbrir, posiciones, activo }) {
  const [valores, setValores] = useState(null)
  const [error, setError] = useState('')

  const cargar = useCallback(async () => {
    try {
      const r = await api.get(`${API_BASE}/estrategias/${estrategia}/simbolos/${agente.symbol}/config`)
      setValores(r.data.valores)
      setError('')
    } catch (err) {
      setError(err?.response?.data?.detail || 'No se pudo cargar la configuración')
    }
  }, [api, API_BASE, estrategia, agente.symbol])

  useEffect(() => { if (abierta) cargar() }, [abierta, cargar])

  const defsSimbolo = esquema.filter(d => d.estrategia === estrategia && d.ambitos.includes('simbolo'))

  const guardar = async (cambios) => {
    const r = await api.put(`${API_BASE}/estrategias/${estrategia}/simbolos/${agente.symbol}/config`, { cambios })
    setValores(r.data.valores)
  }

  return (
    <div className={`${TARJETA} flex flex-col gap-3 ${activo ? '' : 'opacity-60'}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Activity size={14} className={activo ? 'text-indigo-400' : 'text-zinc-600'} />
          <span className="text-sm font-bold text-white">{agente.symbol}</span>
          {agente.clase && <span className="text-[10px] font-bold text-zinc-600 uppercase tracking-widest px-1.5 py-0.5 bg-white/5 rounded">{agente.clase}</span>}
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[10px] font-bold uppercase tracking-widest text-zinc-500">
          {estrategia === 'flota' && <span>{agente.modelo} · {agente.timeframe} · riesgo {agente.riesgo_pct}%</span>}
          {estrategia === 'scalping' && <span>riesgo {agente.riesgo_scalping}%</span>}
          <span className={posiciones > 0 ? 'text-indigo-400' : ''}>{posiciones} abierta{posiciones === 1 ? '' : 's'}</span>
          <span className={`px-1.5 py-0.5 rounded ${agente.live ? 'bg-rose-500/10 text-rose-300' : 'bg-zinc-800 text-zinc-400'}`}>
            {agente.live ? 'real' : 'simulación'}
          </span>
          <span className={`px-1.5 py-0.5 rounded ${activo ? 'bg-emerald-500/10 text-emerald-300' : 'bg-zinc-800 text-zinc-500'}`}>
            {activo ? 'activo' : 'parado'}
          </span>
          <button onClick={() => onAbrir(agente.symbol)}
            className="px-2 py-1 rounded-lg border border-zinc-700 text-zinc-300 hover:text-white hover:border-indigo-500/60">
            {abierta ? 'Cerrar' : 'Configurar'}
          </button>
        </div>
      </div>

      {abierta && (
        <div className="border-t border-white/5 pt-4">
          {error && <p className="text-[11px] text-rose-400 font-bold uppercase tracking-wider mb-3">{error}</p>}
          {valores && <ConfigForm definiciones={defsSimbolo} valores={valores} onGuardar={guardar}
            vacio="Este simbolo no tiene parámetros propios en esta estrategia" />}
        </div>
      )}
    </div>
  )
}

export function EstrategiaView({ api, API_BASE, agentes, symbols }) {
  const [estrategias, setEstrategias] = useState([])
  const [esquema, setEsquema] = useState([])
  const [activa, setActiva] = useState(null)
  const [seccion, setSeccion] = useState('resumen')
  const [abierto, setAbierto] = useState(null)
  const [configEst, setConfigEst] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([api.get(`${API_BASE}/estrategias`), api.get(`${API_BASE}/config/esquema`)])
      .then(([e, s]) => {
        setEstrategias(e.data)
        setEsquema(s.data)
        setActiva(prev => prev || e.data[0]?.id || null)
      })
      .catch(() => setError('No se pudieron cargar las estrategias'))
  }, [api, API_BASE])

  useEffect(() => {
    if (!activa || seccion !== 'config') return
    api.get(`${API_BASE}/estrategias/${activa}/config`)
      .then(r => setConfigEst(r.data.valores))
      .catch(() => setConfigEst(null))
  }, [api, API_BASE, activa, seccion])

  const [scal, setScal] = useState(null)
  const [ocupadoScal, setOcupadoScal] = useState(false)
  const cargarScal = useCallback(() => {
    api.get(`${API_BASE}/estrategias/scalping/estado`).then(r => setScal(r.data)).catch(() => {})
  }, [api, API_BASE])
  useEffect(() => { cargarScal() }, [cargarScal])

  const accionScal = async (ruta) => {
    setOcupadoScal(true)
    setError('')
    try {
      const r = await api.post(`${API_BASE}/estrategias/scalping/${ruta}`)
      setScal(r.data)
    } catch (err) {
      setError(err?.response?.data?.detail || 'No se pudo cambiar el agente de scalping')
    } finally {
      setOcupadoScal(false)
    }
  }

  const estrategia = estrategias.find(e => e.id === activa)
  const agentesDe = useMemo(() => (estrategia ? agentes.filter(a => incluye(a, estrategia.id)) : []), [agentes, estrategia])
  const posicionesPor = useMemo(() => Object.fromEntries((symbols || []).map(s => [s.symbol, s.open_positions || 0])), [symbols])
  const totalPosiciones = agentesDe.reduce((n, a) => n + (posicionesPor[a.symbol] || 0), 0)
  const activos = agentesDe.filter(a => a.activo).length
  const reales = agentesDe.filter(a => a.live).length

  const defsEstrategia = esquema.filter(d => estrategia && d.estrategia === estrategia.id && d.ambitos.includes('estrategia'))

  const guardarEstrategia = async (cambios) => {
    const r = await api.put(`${API_BASE}/estrategias/${activa}/config`, { cambios })
    setConfigEst(r.data.valores)
  }

  if (error) return <p className="text-[11px] text-rose-400 font-bold uppercase tracking-wider">{error}</p>
  if (!estrategia) return <p className="text-[11px] text-zinc-600 font-bold uppercase tracking-wider">Cargando estrategias…</p>

  return (
    <div className="max-w-5xl mx-auto flex flex-col gap-6">
      {/* Selector de estrategia */}
      <div className="flex flex-wrap items-center gap-2">
        {estrategias.map(e => (
          <button key={e.id} onClick={() => { setActiva(e.id); setAbierto(null); }}
            className={`flex items-center gap-2 text-xs font-bold uppercase tracking-widest px-4 py-2 rounded-xl border transition-all ${activa === e.id ? 'bg-indigo-600/20 border-indigo-500/50 text-indigo-200' : 'border-zinc-800 text-zinc-500 hover:text-zinc-300'}`}>
            <Layers size={14} />{e.nombre}
            <span className="text-zinc-600">{e.n_simbolos}</span>
          </button>
        ))}
      </div>

      {/* Pestañas de la estrategia */}
      <div className="flex items-center gap-1 border-b border-zinc-800/60">
        {SECCIONES.map(([id, nombre]) => (
          <button key={id} onClick={() => setSeccion(id)}
            className={`text-[11px] font-bold uppercase tracking-widest px-4 py-2 -mb-px border-b-2 transition-all ${seccion === id ? 'border-indigo-500 text-indigo-200' : 'border-transparent text-zinc-500 hover:text-zinc-300'}`}>
            {nombre}
          </button>
        ))}
      </div>

      {seccion === 'resumen' && (
        <div className="flex flex-col gap-4">
          <p className="text-sm text-zinc-400">{estrategia.descripcion}</p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Stat etiqueta="Símbolos" valor={agentesDe.length} />
            <Stat etiqueta="Activos" valor={activos} />
            <Stat etiqueta="Ordenes reales" valor={reales} />
            <Stat etiqueta="Posiciones abiertas" valor={totalPosiciones} />
          </div>
          {estrategia.id === 'scalping' && scal && (
            <div className={`${TARJETA} flex flex-wrap items-center justify-between gap-4`}>
              <div className="flex flex-col gap-1">
                <p className="text-[11px] font-bold text-zinc-500 uppercase tracking-widest">Agente de scalping</p>
                <p className={`text-sm font-bold uppercase tracking-wider ${scal.corriendo ? 'text-emerald-300' : 'text-zinc-400'}`}>
                  {ocupadoScal ? 'Cambiando…' : scal.corriendo ? `En marcha (PID ${scal.pid})` : 'Parado'}
                </p>
                <p className="text-[11px] text-zinc-600 font-bold uppercase tracking-wider">
                  {scal.encendido ? 'Se relanza al abrir la app' : 'No se relanza al abrir la app'}
                </p>
              </div>
              {scal.corriendo ? (
                <button disabled={ocupadoScal} onClick={() => accionScal('parar')}
                  className="text-[11px] font-bold uppercase tracking-widest px-4 py-2 rounded-xl border border-rose-500/50 text-rose-300 disabled:opacity-40">
                  Parar
                </button>
              ) : (
                <button disabled={ocupadoScal} onClick={() => accionScal('arrancar')}
                  className="text-[11px] font-bold uppercase tracking-widest px-4 py-2 rounded-xl border border-emerald-500/50 text-emerald-300 disabled:opacity-40">
                  Arrancar
                </button>
              )}
            </div>
          )}
          <p className="text-[11px] text-zinc-600 font-bold uppercase tracking-wider">
            Para cambiar el comportamiento de un símbolo, ábrelo en la pestaña Símbolos. Los parámetros comunes están en Configuración.
          </p>
        </div>
      )}

      {seccion === 'simbolos' && (
        <div className="flex flex-col gap-3">
          {agentesDe.length === 0 && <p className="text-[11px] text-zinc-600 font-bold uppercase tracking-wider">Ningún símbolo asignado a esta estrategia</p>}
          {agentesDe.map(a => (
            <FilaSimbolo key={`${estrategia.id}-${a.symbol}`} agente={a} estrategia={estrategia.id} esquema={esquema}
              API_BASE={API_BASE} api={api} abierta={abierto === a.symbol}
              activo={estrategia.id === 'scalping' ? !!(scal && scal.corriendo && scal.encendido) : !!a.activo}
              onAbrir={sym => setAbierto(abierto === sym ? null : sym)} posiciones={posicionesPor[a.symbol] || 0} />
          ))}
        </div>
      )}

      {seccion === 'config' && (
        <div className={`${TARJETA} flex flex-col gap-4`}>
          <div className="flex items-center gap-2 text-zinc-400">
            <Bot size={14} />
            <span className="text-[11px] font-bold uppercase tracking-widest">Parámetros de {estrategia.nombre}</span>
          </div>
          {configEst
            ? <ConfigForm definiciones={defsEstrategia} valores={configEst} onGuardar={guardarEstrategia}
                vacio="Esta estrategia no tiene parámetros propios: sus símbolos se configuran en la pestaña Símbolos" />
            : <p className="text-[11px] text-zinc-600 font-bold uppercase tracking-wider">Cargando…</p>}
        </div>
      )}

      {seccion === 'config' && estrategia.id === 'flota' && (
        <div className="flex flex-col gap-3">
          <p className="text-[11px] font-bold text-zinc-500 uppercase tracking-[0.2em]">Prompts de los símbolos</p>
          <LlmCriterioPanel api={api} API_BASE={API_BASE} agentes={agentesDe} />
        </div>
      )}
    </div>
  )
}
