import { useCallback, useEffect, useState } from 'react'
import { Bot, Power, PlayCircle, List, X } from 'lucide-react'

// Flota LLM integrada en las tarjetas de simbolos: cada tarjeta con agente muestra una fila
// compacta (modelo, modo, latido, acciones). Las decisiones se abren en un modal para no
// alargar la tarjeta. Activar arranca el agente (simulacion o demo); parar lo detiene.

// Grupos de la vista. El tipo de symbols_config (INDEX, STOCK...) y la clase LLM (indice,
// accion_eeuu...) caen en el mismo grupo, asi un simbolo solo-LLM encaja con los demas.
const GRUPOS = [
  { id: 'Índices', tipos: ['INDEX', 'INDICE'] },
  { id: 'Acciones', tipos: ['STOCK', 'EQUITY', 'ACCION_EEUU', 'ACCION_EU'] },
  { id: 'Cripto', tipos: ['CRYPTO'] },
  { id: 'Forex', tipos: ['FOREX'] },
  { id: 'Materias primas', tipos: ['COMMODITY', 'METAL'] },
]
export const ORDEN_GRUPOS = [...GRUPOS.map(g => g.id), 'Otros']

const grupoDe = (tipo) => {
  const t = (tipo || '').toUpperCase()
  return GRUPOS.find(g => g.tipos.includes(t))?.id || 'Otros'
}

// Une los simbolos del dashboard con los agentes LLM y los reparte por grupo, en orden fijo.
// Un agente cuyo simbolo no esta en symbols_config tambien aparece, solo con su fila LLM.
// filtro: 'todos' | 'llm' (con agente LLM) | 'activos' (agente LLM activo ahora)
export function agruparTarjetas(symbols, agentes, filtro = 'todos', grupoFiltro = 'todos') {
  const conocidos = new Set(symbols.map(s => s.symbol))
  const solosLlm = agentes
    .filter(a => !conocidos.has(a.symbol))
    .map(a => ({ symbol: a.symbol, type: '', is_active: false, soloLlm: true, clase: a.clase }))
  const porSimbolo = Object.fromEntries(agentes.map(a => [a.symbol, a]))
  const todos = [...symbols, ...solosLlm].filter(s => {
    const a = porSimbolo[s.symbol]
    if (filtro === 'llm') return !!a
    if (filtro === 'activos') return !!(a && a.activo)
    if (grupoFiltro !== 'todos' && grupoDe(s.soloLlm ? s.clase : s.type) !== grupoFiltro) return false
    return true
  })

  const porGrupo = {}
  todos.forEach(s => {
    const g = s.soloLlm ? grupoDe(s.clase) : grupoDe(s.type)
    ;(porGrupo[g] = porGrupo[g] || []).push(s)
  })
  return ORDEN_GRUPOS
    .filter(g => porGrupo[g])
    .map(g => ({ grupo: g, items: porGrupo[g].sort((a, b) => a.symbol.localeCompare(b.symbol)) }))
}

// habilitado=false mientras no hay sesion: sin token la API responde 401 y el interceptor recarga la pagina en bucle.
export function useLlmAgentes(api, API_BASE, habilitado = true) {
  const [agentes, setAgentes] = useState([])
  const [costeTotal, setCosteTotal] = useState(null)
  const [cuota, setCuota] = useState(null)
  const [error, setError] = useState('')
  const [ocupado, setOcupado] = useState({})

  const recargar = useCallback(async () => {
    try {
      const r = await api.get(`${API_BASE}/llm/agentes`)
      setAgentes(r.data)
      setError('')
    } catch (e) {
      setError('No se pudo leer la flota LLM')
    }
    // Coste y cuota son informativos: si fallan, la barra simplemente no los muestra.
    api.get(`${API_BASE}/llm/coste`).then(r => setCosteTotal(r.data.total_usd)).catch(() => {})
    api.get(`${API_BASE}/llm/cuota`).then(r => setCuota(r.data)).catch(() => {})
  }, [api, API_BASE])

  useEffect(() => {
    if (!habilitado) return
    recargar()
    const t = setInterval(recargar, 30000)
    return () => clearInterval(t)
  }, [recargar, habilitado])

  const accion = async (symbol, tipo, body) => {
    setOcupado(o => ({ ...o, [symbol]: true }))
    try {
      await api.post(`${API_BASE}/llm/agentes/${encodeURIComponent(symbol)}/${tipo}`, body || {})
      await recargar()
    } catch (e) {
      setError(`${symbol}: ${e?.response?.data?.detail || 'error'}`)
    } finally {
      setOcupado(o => ({ ...o, [symbol]: false }))
    }
  }

  const cambiarModelo = async (symbol, modelo) => {
    setOcupado(o => ({ ...o, [symbol]: true }))
    try {
      await api.post(`${API_BASE}/llm/agentes/${encodeURIComponent(symbol)}/modelo`, { modelo })
      await recargar()
    } catch (e) {
      setError(`${symbol}: ${e?.response?.data?.detail || 'no se pudo cambiar el modelo'}`)
    } finally {
      setOcupado(o => ({ ...o, [symbol]: false }))
    }
  }

  // Topes de la rotacion (cuantos agentes a la vez y por clase). Se leen y guardan aparte.
  const [limites, setLimites] = useState(null)
  const cargarLimites = useCallback(async () => {
    try {
      const r = await api.get(`${API_BASE}/llm/rotacion`)
      setLimites(r.data)
    } catch (e) {
      setLimites(null)
    }
  }, [api, API_BASE])

  useEffect(() => {
    if (habilitado) cargarLimites()
  }, [cargarLimites, habilitado])

  const guardarLimites = async (cambios) => {
    try {
      const r = await api.put(`${API_BASE}/llm/rotacion`, cambios)
      setLimites(r.data)
      return null
    } catch (e) {
      return e?.response?.data?.detail || 'no se pudieron guardar los topes'
    }
  }

  const cambiarTimeframe = async (symbol, timeframe) => {
    setOcupado(o => ({ ...o, [symbol]: true }))
    try {
      await api.post(`${API_BASE}/llm/agentes/${encodeURIComponent(symbol)}/timeframe`, { timeframe })
      await recargar()
    } catch (e) {
      setError(`${symbol}: ${e?.response?.data?.detail || 'no se pudo cambiar el timeframe'}`)
    } finally {
      setOcupado(o => ({ ...o, [symbol]: false }))
    }
  }

  return { agentes, costeTotal, cuota, error, ocupado, accion, cambiarModelo, cambiarTimeframe, recargar, limites, guardarLimites }
}

// Barra de topes de la flota: cuantos agentes LLM pueden estar activos a la vez y por clase.
// Bajarlo no para los agentes que ya estan activos; solo limita los que entran despues.
export function LlmLimitesBar({ limites, guardar, activosAhora }) {
  const [maxActivos, setMaxActivos] = useState('')
  const [maxClase, setMaxClase] = useState('')
  const [cicloMin, setCicloMin] = useState('')
  const [horas, setHoras] = useState('')
  const [margen, setMargen] = useState('')
  const [sustituciones, setSustituciones] = useState('')
  const [aviso, setAviso] = useState('')

  useEffect(() => {
    if (limites) {
      setMaxActivos(String(limites.max_activos))
      setMaxClase(String(limites.max_por_clase))
      setCicloMin(String(limites.ciclo_min))
      setHoras(limites.horas_reseleccion.join(','))
      setMargen(String(limites.margen_cambio))
      setSustituciones(String(limites.max_sustituciones_dia))
    }
  }, [limites])

  if (!limites) return null

  const guardarAhora = async () => {
    const error = await guardar({
      max_activos: Number(maxActivos),
      max_por_clase: Number(maxClase),
      ciclo_min: Number(cicloMin),
      horas_reseleccion: horas.split(',').map(h => h.trim()).filter(h => h !== '').map(Number),
      margen_cambio: Number(margen),
      max_sustituciones_dia: Number(sustituciones),
    })
    setAviso(error || 'Guardado')
  }

  const campo = 'w-16 text-xs bg-zinc-900 text-zinc-200 border border-zinc-700 rounded-lg px-2 py-1 focus:outline-none focus:border-indigo-500/60'
  const etiqueta = 'flex items-center gap-2 text-[11px] font-bold uppercase tracking-widest text-zinc-500'

  return (
    <div className="flex flex-wrap items-center gap-3 mb-6 p-3 bg-zinc-900/30 border border-zinc-800/50 rounded-2xl">
      <p className="text-[11px] font-bold text-zinc-400 uppercase tracking-widest">Rotación</p>
      <label className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-widest text-zinc-500">
        Máx. activos
        <input type="number" min={1} max={limites.universo} value={maxActivos}
          onChange={e => setMaxActivos(e.target.value)}
          className="w-16 text-xs bg-zinc-900 text-zinc-200 border border-zinc-700 rounded-lg px-2 py-1 focus:outline-none focus:border-indigo-500/60" />
      </label>
      <label className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-widest text-zinc-500">
        Máx. por clase
        <input type="number" min={1} value={maxClase}
          onChange={e => setMaxClase(e.target.value)}
          className="w-16 text-xs bg-zinc-900 text-zinc-200 border border-zinc-700 rounded-lg px-2 py-1 focus:outline-none focus:border-indigo-500/60" />
      </label>
      <label className={etiqueta}>
        Cada (min)
        <input type="number" min={1} max={60} value={cicloMin}
          onChange={e => setCicloMin(e.target.value)} className={campo} />
      </label>
      <label className={etiqueta}>
        Horas reselección
        <input type="text" placeholder="8,16" value={horas}
          onChange={e => setHoras(e.target.value)} className="w-24 text-xs bg-zinc-900 text-zinc-200 border border-zinc-700 rounded-lg px-2 py-1 focus:outline-none focus:border-indigo-500/60" />
      </label>
      <label className={etiqueta}>
        Margen cambio
        <input type="number" min={0} max={1} step={0.01} value={margen}
          onChange={e => setMargen(e.target.value)} className={campo} />
      </label>
      <label className={etiqueta}>
        Sustituciones/día
        <input type="number" min={0} max={10} value={sustituciones}
          onChange={e => setSustituciones(e.target.value)} className={campo} />
      </label>
      <button onClick={guardarAhora}
        className="text-[11px] font-bold uppercase tracking-widest px-3 py-1.5 rounded-lg border border-indigo-500/50 text-indigo-200 hover:bg-indigo-600/20 transition-colors">
        Guardar
      </button>
      <span className="text-[11px] font-bold uppercase tracking-widest text-zinc-600">
        {activosAhora} activos ahora · universo {limites.universo}
      </span>
      {aviso && <span className="text-[11px] font-bold uppercase tracking-widest text-zinc-500">{aviso}</span>}
    </div>
  )
}

// Bloque del agente dentro de la tarjeta. Los clicks no llegan a la tarjeta (que apaga/enciende
// el simbolo en el dashboard). Modelo y reloj se cambian en caliente: aplican en su siguiente vuelta.
const CAMPO = 'w-full text-[11px] font-bold uppercase bg-zinc-900 text-zinc-200 border border-zinc-700 rounded-lg px-2 py-1.5 disabled:opacity-40 focus:outline-none focus:border-indigo-500/60'
const BOTON = 'flex-1 flex items-center justify-center gap-1 text-[11px] font-bold uppercase py-1.5 rounded-lg border transition-colors disabled:opacity-40'

export function LlmFila({ agente, ocupado, onAccion, onDecisiones, onModelo, onTimeframe }) {
  const estado = agente.activo ? (agente.live ? 'Demo · opera' : 'Simulación') : 'Apagado'
  const colorEstado = agente.activo
    ? (agente.live ? 'bg-rose-500/15 text-rose-300 border-rose-500/30' : 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30')
    : 'bg-zinc-800/60 text-zinc-500 border-zinc-700/60'

  return (
    <div onClick={e => e.stopPropagation()} className="flex flex-col gap-2.5 rounded-xl border border-zinc-800/70 bg-zinc-950/60 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Bot size={13} className="text-indigo-400" />
          <span className="text-[11px] font-bold uppercase tracking-widest text-zinc-400">Agente</span>
          <span className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded-full border ${colorEstado}`}>{estado}</span>
        </div>
        <div className="flex items-center gap-2">
          <span
            title={agente.vivo ? `vivo · PID ${agente.pid}` : 'no corre'}
            className={`w-1.5 h-1.5 rounded-full ${agente.vivo ? 'bg-emerald-400' : 'bg-zinc-700'}`}
          />
          <button onClick={() => onDecisiones(agente.symbol)} title="Decisiones" className="text-zinc-500 hover:text-zinc-300">
            <List size={14} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1">
          <span className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">Modelo</span>
          <select value={agente.modelo || 'haiku'} disabled={ocupado} onChange={e => onModelo(agente.symbol, e.target.value)}
            title="Modelo con el que decide el agente" className={CAMPO}>
            {['haiku', 'sonnet', 'opus'].map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">Reloj</span>
          <select value={agente.timeframe || 'M5'} disabled={ocupado} onChange={e => onTimeframe(agente.symbol, e.target.value)}
            title="Cada cuánto se llama al agente" className={CAMPO}>
            {['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'].map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
      </div>

      <div className="flex items-center gap-2">
        {agente.activo ? (
          <button disabled={ocupado} onClick={() => onAccion(agente.symbol, 'desactivar')}
            className={`${BOTON} border-zinc-700 text-zinc-300 hover:bg-zinc-800`}>
            <Power size={11} /> Parar
          </button>
        ) : (
          <>
            <button disabled={ocupado} onClick={() => onAccion(agente.symbol, 'activar', { live: false })}
              className={`${BOTON} border-indigo-500/40 text-indigo-300 hover:bg-indigo-500/10`}>
              Simular
            </button>
            <button disabled={ocupado} onClick={() => onAccion(agente.symbol, 'activar', { live: true })}
              className={`${BOTON} border-rose-500/40 text-rose-300 hover:bg-rose-500/10`}>
              <PlayCircle size={11} /> Demo
            </button>
          </>
        )}
        <span title="Coste acumulado a precio de API (no es lo que se paga con la suscripción)"
          className="shrink-0 pl-1 text-[11px] font-bold tabular-nums text-zinc-400">
          API ${Number(agente.coste_usd || 0).toFixed(2)}
        </span>
      </div>
    </div>
  )
}

// Modal con las ultimas decisiones del agente. El prompt exacto se abre encima.
export function LlmDecisionesModal({ symbol, api, API_BASE, onCerrar }) {
  const [decisiones, setDecisiones] = useState(null)
  const [prompt, setPrompt] = useState(null)

  useEffect(() => {
    api.get(`${API_BASE}/llm/agentes/${encodeURIComponent(symbol)}/decisiones?limite=30`)
      .then(r => setDecisiones(r.data))
      .catch(() => setDecisiones([]))
  }, [symbol, api, API_BASE])

  const verPrompt = async (version) => {
    try {
      const r = await api.get(`${API_BASE}/llm/prompts/${version}`)
      setPrompt(r.data)
    } catch (e) {
      setPrompt({ version, texto: 'No hay texto guardado para esta versión.', creado: '' })
    }
  }

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-6" onClick={onCerrar}>
      <div className="bg-zinc-950 border border-zinc-800 rounded-2xl p-6 max-w-3xl w-full max-h-[80vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <p className="text-xs font-bold uppercase tracking-widest text-white">Decisiones · {symbol}</p>
          <button onClick={onCerrar} className="text-zinc-500 hover:text-zinc-300"><X size={16} /></button>
        </div>

        {decisiones === null && <p className="text-[11px] text-zinc-600">Cargando…</p>}
        {decisiones && decisiones.length === 0 && (
          <p className="text-[11px] text-zinc-600">Sin decisiones guardadas todavía.</p>
        )}
        <div className="space-y-2">
          {(decisiones || []).map(d => (
            <details key={d.id} className="text-[11px] text-zinc-400">
              <summary className="cursor-pointer hover:text-zinc-200 flex flex-wrap gap-3">
                <span className="text-zinc-600">{d.ts}</span>
                <span className="font-bold uppercase text-zinc-200">{d.llamado ? (d.accion || '-') : 'sin llamada'}</span>
                {d.confianza != null && <span>conf {Number(d.confianza).toFixed(2)}</span>}
                {d.motivo_puerta && <span className="text-zinc-600">{d.motivo_puerta}</span>}
                {d.validacion && <span className="text-amber-400">{d.validacion}</span>}
                {d.coste_usd != null && <span>${Number(d.coste_usd).toFixed(4)}</span>}
              </summary>
              <div className="mt-2 grid gap-2">
                <button onClick={() => verPrompt(d.prompt_version)} className="justify-self-start text-indigo-300 hover:text-indigo-200 underline">
                  Ver prompt (v{d.prompt_version})
                </button>
                <pre className="bg-zinc-900 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap">{d.entrada_json || '—'}</pre>
                <pre className="bg-zinc-900 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap">{d.respuesta_json || '—'}</pre>
                {d.resultado && <p className="text-zinc-500">resultado: {d.resultado}</p>}
              </div>
            </details>
          ))}
        </div>
      </div>

      {prompt && (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-[60] p-6" onClick={() => setPrompt(null)}>
          <div className="bg-zinc-950 border border-zinc-800 rounded-2xl p-6 max-w-3xl w-full max-h-[80vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
            <p className="text-xs font-bold uppercase tracking-widest text-white mb-1">Prompt v{prompt.version}</p>
            <p className="text-xs text-zinc-600 mb-4">{prompt.creado}</p>
            <pre className="text-[11px] text-zinc-300 whitespace-pre-wrap">{prompt.texto}</pre>
          </div>
        </div>
      )}
    </div>
  )
}
