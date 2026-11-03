import { useCallback, useEffect, useState } from 'react'
import { Bot, Power, PlayCircle, List, X } from 'lucide-react'
import { usePollingWhenVisible } from '../hooks/usePolling'

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
  const [aviso, setAviso] = useState('')
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
    if (habilitado) recargar()
  }, [recargar, habilitado])
  usePollingWhenVisible(recargar, 30000, habilitado)

  // Acciones que paran o sacan un agente: se piden confirmar para no hacerlo por un clic de más.
  const CONFIRMAR = {
    desactivar: s => `¿Excluir ${s}? Se para y la rotación no lo volverá a poner hasta que lo actives a mano.`,
    quitar: s => `¿Quitar ${s} del grupo? Le resta puntos un tiempo y puede volver.`,
  }

  const accion = async (symbol, tipo, body) => {
    if (CONFIRMAR[tipo] && !window.confirm(CONFIRMAR[tipo](symbol))) return
    setOcupado(o => ({ ...o, [symbol]: true }))
    setAviso('')
    try {
      const r = await api.post(`${API_BASE}/llm/agentes/${encodeURIComponent(symbol)}/${tipo}`, body || {})
      if (r.data?.mensaje) setAviso(r.data.mensaje)
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

  const cambiarRiesgo = async (symbol, riesgoPct) => {
    setOcupado(o => ({ ...o, [symbol]: true }))
    try {
      await api.post(`${API_BASE}/llm/agentes/${encodeURIComponent(symbol)}/riesgo`, { riesgo_pct: riesgoPct })
      await recargar()
    } catch (e) {
      setError(`${symbol}: ${e?.response?.data?.detail || 'no se pudo cambiar el riesgo'}`)
    } finally {
      setOcupado(o => ({ ...o, [symbol]: false }))
    }
  }

  const cambiarRiesgoScalping = async (symbol, riesgoPct) => {
    setOcupado(o => ({ ...o, [symbol]: true }))
    try {
      await api.post(`${API_BASE}/llm/agentes/${encodeURIComponent(symbol)}/riesgo-scalping`, { riesgo_pct: riesgoPct })
      await recargar()
    } catch (e) {
      setError(`${symbol}: ${e?.response?.data?.detail || 'no se pudo cambiar el riesgo del scalping'}`)
    } finally {
      setOcupado(o => ({ ...o, [symbol]: false }))
    }
  }

  const cambiarEstrategia = async (symbol, estrategia) => {
    setOcupado(o => ({ ...o, [symbol]: true }))
    try {
      await api.post(`${API_BASE}/llm/agentes/${encodeURIComponent(symbol)}/estrategia`, { estrategia })
      await recargar()
    } catch (e) {
      setError(`${symbol}: ${e?.response?.data?.detail || 'no se pudo cambiar la estrategia'}`)
    } finally {
      setOcupado(o => ({ ...o, [symbol]: false }))
    }
  }

  const cambiarFiltro = async (symbol, sinFiltro) => {
    setOcupado(o => ({ ...o, [symbol]: true }))
    try {
      await api.post(`${API_BASE}/llm/agentes/${encodeURIComponent(symbol)}/filtro`, { sin_filtro: sinFiltro })
      await recargar()
    } catch (e) {
      setError(`${symbol}: ${e?.response?.data?.detail || 'no se pudo cambiar el filtro'}`)
    } finally {
      setOcupado(o => ({ ...o, [symbol]: false }))
    }
  }

  // Tope global de llamadas al dia (todos los agentes). 0 = sin tope.
  const [topeDiario, setTopeDiario] = useState(null)
  useEffect(() => {
    if (!habilitado) return
    api.get(`${API_BASE}/llm/tope-diario`).then(r => setTopeDiario(r.data.max_llamadas_dia)).catch(() => {})
  }, [api, API_BASE, habilitado])

  const guardarTopeDiario = async (valor) => {
    try {
      const r = await api.put(`${API_BASE}/llm/tope-diario`, { max_llamadas_dia: valor })
      setTopeDiario(r.data.max_llamadas_dia)
      return null
    } catch (e) {
      return e?.response?.data?.detail || 'no se pudo guardar el tope'
    }
  }

  // Topes de cuota de Claude (% de la ventana de 5 h y de la semanal) que frenan las llamadas.
  const [cuotaTopes, setCuotaTopes] = useState(null)
  useEffect(() => {
    if (!habilitado) return
    api.get(`${API_BASE}/llm/cuota-topes`).then(r => setCuotaTopes(r.data)).catch(() => {})
  }, [api, API_BASE, habilitado])

  const guardarCuotaTopes = async (valores) => {
    try {
      const r = await api.put(`${API_BASE}/llm/cuota-topes`, valores)
      setCuotaTopes(r.data)
      return null
    } catch (e) {
      return e?.response?.data?.detail || 'no se pudieron guardar los topes de cuota'
    }
  }

  return { agentes, costeTotal, cuota, error, aviso, ocupado, accion, cambiarModelo, cambiarTimeframe, cambiarFiltro, cambiarRiesgo, cambiarRiesgoScalping, cambiarEstrategia,
    recargar, limites, guardarLimites, topeDiario, guardarTopeDiario, cuotaTopes, guardarCuotaTopes }
}

// Barra de topes de la flota: cuantos agentes LLM pueden estar activos a la vez y por clase.
// Bajarlo no para los agentes que ya estan activos; solo limita los que entran despues.
export function LlmLimitesBar({ limites, guardar, activosAhora, topeDiario, guardarTopeDiario, cuotaTopes, guardarCuotaTopes }) {
  const [maxActivos, setMaxActivos] = useState('')
  const [tope, setTope] = useState('')
  const [topeCuota5h, setTopeCuota5h] = useState('')
  const [topeCuotaSemanal, setTopeCuotaSemanal] = useState('')
  const [maxClase, setMaxClase] = useState('')
  const [cicloMin, setCicloMin] = useState('')
  const [cadenciaMin, setCadenciaMin] = useState('')
  const [plazoMin, setPlazoMin] = useState('')
  const [margen, setMargen] = useState('')
  const [sustituciones, setSustituciones] = useState('')
  const [penalCripto, setPenalCripto] = useState('')
  const [penalQuitar, setPenalQuitar] = useState('')
  const [penalDias, setPenalDias] = useState('')
  const [inactividad, setInactividad] = useState('')
  const [accionEntrada, setAccionEntrada] = useState('aprobar')
  const [accionSalida, setAccionSalida] = useState('aprobar')
  const [aviso, setAviso] = useState('')

  useEffect(() => {
    if (limites) {
      setMaxActivos(String(limites.max_activos))
      setMaxClase(String(limites.max_por_clase))
      setCicloMin(String(limites.ciclo_min))
      setCadenciaMin(String(limites.cadencia_ranking_min))
      setPlazoMin(String(limites.ventana_aprobacion_min))
      setMargen(String(limites.margen_cambio))
      setSustituciones(String(limites.max_sustituciones_dia))
      setPenalCripto(String(limites.penalizacion_cripto))
      setPenalQuitar(String(limites.penalizacion_quitar))
      setPenalDias(String(limites.penalizacion_dias))
      setInactividad(String(limites.inactividad_min))
      setAccionEntrada(limites.accion_entrada)
      setAccionSalida(limites.accion_salida)
    }
  }, [limites])

  useEffect(() => {
    if (topeDiario !== null && topeDiario !== undefined) setTope(String(topeDiario))
  }, [topeDiario])

  useEffect(() => {
    if (cuotaTopes) {
      setTopeCuota5h(String(cuotaTopes.max_5h))
      setTopeCuotaSemanal(String(cuotaTopes.max_semanal))
    }
  }, [cuotaTopes])

  if (!limites) return null

  const guardarAhora = async () => {
    const errorTope = guardarTopeDiario ? await guardarTopeDiario(Number(tope)) : null
    const errorCuota = guardarCuotaTopes
      ? await guardarCuotaTopes({ max_5h: Number(topeCuota5h), max_semanal: Number(topeCuotaSemanal) })
      : null
    const error = await guardar({
      max_activos: Number(maxActivos),
      max_por_clase: Number(maxClase),
      ciclo_min: Number(cicloMin),
      cadencia_ranking_min: Number(cadenciaMin),
      ventana_aprobacion_min: Number(plazoMin),
      margen_cambio: Number(margen),
      max_sustituciones_dia: Number(sustituciones),
      inactividad_min: Number(inactividad),
      penalizacion_cripto: Number(penalCripto),
      penalizacion_quitar: Number(penalQuitar),
      penalizacion_dias: Number(penalDias),
      accion_entrada: accionEntrada,
      accion_salida: accionSalida,
    })
    setAviso(errorTope || errorCuota || error || 'Guardado')
  }

  // Un campo por ajuste. title explica lo que hace en el momento de pasar el raton.
  const campos = [
    { etiqueta: 'Llamadas/día', valor: tope, set: setTope, min: 0,
      title: 'Llamadas al modelo por día para TODOS los agentes. 0 = sin tope' },
    { etiqueta: 'Tope cuota 5 h (%)', valor: topeCuota5h, set: setTopeCuota5h, min: 10, max: 100,
      title: 'Por encima de este % de la ventana de 5 horas, la flota no llama al modelo' },
    { etiqueta: 'Tope cuota semanal (%)', valor: topeCuotaSemanal, set: setTopeCuotaSemanal, min: 10, max: 100,
      title: 'Por encima de este % de la cuota semanal, la flota no llama al modelo (los agentes caros, 20 puntos antes)' },
    { etiqueta: 'Máx. activos', valor: maxActivos, set: setMaxActivos, min: 1, max: limites.universo,
      title: 'Cuántos agentes LLM pueden estar activos a la vez' },
    { etiqueta: 'Máx. por clase', valor: maxClase, set: setMaxClase, min: 1,
      title: 'Cuántos agentes por clase de mercado (índice, acción EEUU...)' },
    { etiqueta: 'Riesgo cada (min)', valor: cicloMin, set: setCicloMin, min: 1, max: 60,
      title: 'Cada cuántos minutos se vigilan pérdidas y cierres' },
    { etiqueta: 'Propuestas cada (min)', valor: cadenciaMin, set: setCadenciaMin, min: 5, max: 240,
      title: 'Cada cuántos minutos se recalculan las propuestas de cambio' },
    { etiqueta: 'Plazo respuesta (min)', valor: plazoMin, set: setPlazoMin, min: 5, max: 720,
      title: 'Minutos que tienes para decidir una propuesta. Al vencer, actúa la opción de Entrar/Salir sin respuesta (no aplica si está en "Sin plazo")' },
    { etiqueta: 'Margen cambio', valor: margen, set: setMargen, min: 0, max: 1, step: 0.01,
      title: 'Cuánto debe puntuar el candidato por encima del peor activo' },
    { etiqueta: 'Sustituciones/día', valor: sustituciones, set: setSustituciones, min: 0, max: 10,
      title: 'Sustituciones automáticas por pérdidas al día' },
    { etiqueta: 'Inactividad (min)', valor: inactividad, set: setInactividad, min: 30, max: 1440,
      title: 'Minutos sin llamar al modelo tras los que un agente activo puede salir del grupo (sin posición)' },
    { etiqueta: 'Penal. cripto', valor: penalCripto, set: setPenalCripto, min: 0, max: 0.5, step: 0.01,
      title: 'Puntos que pierde cripto en el ranking' },
    { etiqueta: 'Pen. quitar', valor: penalQuitar, set: setPenalQuitar, min: 0, max: 0.5, step: 0.01,
      title: 'Puntos que resta un símbolo que quitaste' },
    { etiqueta: 'Días pen.', valor: penalDias, set: setPenalDias, min: 1, max: 30,
      title: 'Días hasta que la penalización de quitar llega a cero' },
  ]

  // Qué pasa con cada propuesta si no contestas en el plazo: se aprueba, se rechaza, o se queda en la bandeja.
  const acciones = [
    { etiqueta: 'Entrar sin respuesta', valor: accionEntrada, set: setAccionEntrada,
      title: 'Propuestas de abrir un símbolo nuevo que no contestas en el plazo' },
    { etiqueta: 'Salir sin respuesta', valor: accionSalida, set: setAccionSalida,
      title: 'Propuestas de quitar un símbolo que no contestas en el plazo' },
  ]

  const campo = 'w-full text-xs bg-zinc-900 text-zinc-200 border border-zinc-700 rounded-lg px-2 py-1.5 focus:outline-none focus:border-indigo-500/60'

  return (
    <div className="glass-card rounded-2xl p-5 border border-zinc-800/80 flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[11px] font-bold text-zinc-400 uppercase tracking-widest">Topes y tiempos de la rotación</p>
        <span className="text-[11px] font-bold uppercase tracking-widest text-zinc-600">
          {activosAhora} activos ahora · universo {limites.universo}
        </span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-3">
        {campos.map(c => (
          <label key={c.etiqueta} title={c.title} className="flex flex-col gap-1.5">
            <span className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">{c.etiqueta}</span>
            <input type="number" min={c.min} max={c.max} step={c.step} value={c.valor}
              onChange={e => c.set(e.target.value)} className={campo} />
          </label>
        ))}
        {acciones.map(a => (
          <label key={a.etiqueta} title={a.title} className="flex flex-col gap-1.5">
            <span className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">{a.etiqueta}</span>
            <select value={a.valor} onChange={e => a.set(e.target.value)} className={campo}>
              <option value="aprobar">Aprobar al vencer el plazo</option>
              <option value="rechazar">Rechazar al vencer el plazo</option>
              <option value="pendiente">Sin plazo: solo yo decido</option>
            </select>
          </label>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button onClick={guardarAhora}
          className="text-[11px] font-bold uppercase tracking-widest px-3 py-1.5 rounded-lg border border-indigo-500/50 text-indigo-200 hover:bg-indigo-600/20 transition-colors">
          Guardar
        </button>
        {aviso && <span className="text-[11px] font-bold uppercase tracking-widest text-zinc-500">{aviso}</span>}
      </div>
    </div>
  )
}

// Bloque del agente dentro de la tarjeta. Los clicks no llegan a la tarjeta (que apaga/enciende
// el simbolo en el dashboard). Modelo y reloj se cambian en caliente: aplican en su siguiente vuelta.
const CAMPO = 'w-full text-[11px] font-bold uppercase bg-zinc-900 text-zinc-200 border border-zinc-700 rounded-lg px-2 py-1.5 disabled:opacity-40 focus:outline-none focus:border-indigo-500/60'
const BOTON = 'flex-1 flex items-center justify-center gap-1 text-[11px] font-bold uppercase py-1.5 rounded-lg border transition-colors disabled:opacity-40'

// Riesgo por operacion (% de equity). Los valores de la lista son los habituales; el actual se añade si no esta.
const RIESGOS = [0.1, 0.25, 0.5, 0.75, 1]

// Estrategia del simbolo: una sola. Con `scalping` el agente de la flota no opera este simbolo.
const ESTRATEGIAS = [['flota', 'Agente de flota'], ['scalping', 'Scalping'], ['ambos', 'Ambas (flota + scalping)'], ['ninguna', 'Ninguna']]

export function LlmFila({ agente, ocupado, onAccion, onDecisiones, onModelo, onTimeframe, onFiltro, onRiesgo, onRiesgoScalping, onEstrategia }) {
  const riesgoActual = agente.riesgo_pct ?? 0.5
  const opcionesRiesgo = RIESGOS.includes(riesgoActual) ? RIESGOS : [...RIESGOS, riesgoActual].sort((a, b) => a - b)
  const estrategia = agente.estrategia || 'flota'
  const riesgoScalping = agente.riesgo_scalping ?? 0.25
  const opcionesRiesgoScalping = RIESGOS.includes(riesgoScalping) ? RIESGOS : [...RIESGOS, riesgoScalping].sort((a, b) => a - b)
  const selectorRiesgoScalping = onRiesgoScalping && (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">Riesgo · scalping</span>
      <select value={riesgoScalping} disabled={ocupado} onChange={e => onRiesgoScalping(agente.symbol, Number(e.target.value))}
        title="Porcentaje de tu equity que arriesga cada operación de scalping en este símbolo" className={CAMPO}>
        {opcionesRiesgoScalping.map(r => <option key={r} value={r}>{r}% del equity</option>)}
      </select>
    </label>
  )
  const estado = agente.activo ? (agente.live ? 'Demo · opera' : 'Simulación') : 'Apagado'
  const colorEstado = agente.activo
    ? (agente.live ? 'bg-rose-500/15 text-rose-300 border-rose-500/30' : 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30')
    : 'bg-zinc-800/60 text-zinc-500 border-zinc-700/60'

  const selectorEstrategia = onEstrategia && (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">Estrategia</span>
      <select value={estrategia} disabled={ocupado} onChange={e => onEstrategia(agente.symbol, e.target.value)}
        title="Qué estrategia opera este símbolo (solo una a la vez)" className={CAMPO}>
        {ESTRATEGIAS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
      </select>
    </label>
  )

  if (estrategia === 'scalping' || estrategia === 'ninguna') {
    return (
      <div onClick={e => e.stopPropagation()} className="flex flex-col gap-2.5 rounded-xl border border-zinc-800/70 bg-zinc-950/60 p-3">
        {selectorEstrategia}
        {estrategia === 'scalping' && selectorRiesgoScalping}
        <p className="text-[11px] text-zinc-500">
          {estrategia === 'scalping'
            ? 'Lo opera el agente de scalping (configuración en Ajustes › Flota LLM). El agente de la flota no entra aquí.'
            : 'Sin estrategia: este símbolo no opera.'}
        </p>
      </div>
    )
  }

  return (
    <div onClick={e => e.stopPropagation()} className="flex flex-col gap-2.5 rounded-xl border border-zinc-800/70 bg-zinc-950/60 p-3">
      {selectorEstrategia}
      {estrategia === 'ambos' && (
        <p className="text-[11px] text-zinc-500">También opera el scalping en este símbolo, con su propio magic y sus límites.</p>
      )}
      {estrategia === 'ambos' && selectorRiesgoScalping}
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

      {onRiesgo && (
        <label className="flex flex-col gap-1">
          <span className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">{estrategia === 'ambos' ? 'Riesgo · flota' : 'Riesgo por operación'}</span>
          <select value={riesgoActual} disabled={ocupado} onChange={e => onRiesgo(agente.symbol, Number(e.target.value))}
            title="Porcentaje de tu equity que arriesga cada operación de este agente" className={CAMPO}>
            {opcionesRiesgo.map(r => <option key={r} value={r}>{r}% del equity</option>)}
          </select>
        </label>
      )}

      {onFiltro && (
        <button disabled={ocupado} onClick={() => onFiltro(agente.symbol, !agente.sin_filtro)}
          title="Con filtro: solo llama si hay setup (ADX, RSI o Bollinger). Sin filtro: llama en cada vela"
          className={`${BOTON} ${agente.sin_filtro ? 'border-zinc-700 text-zinc-400 hover:bg-zinc-800' : 'border-indigo-500/40 text-indigo-300 hover:bg-indigo-500/10'}`}>
          Filtro de setup: {agente.sin_filtro ? 'apagado' : 'encendido'}
        </button>
      )}

      <div className="flex items-center gap-2">
        {agente.activo ? (
          <>
            <button disabled={ocupado} onClick={() => onAccion(agente.symbol, 'quitar')}
              title="Lo saca del grupo sin bloquearlo: le resta puntos un tiempo y puede volver si es el mejor"
              className={`${BOTON} border-amber-500/40 text-amber-300 hover:bg-amber-500/10`}>
              Quitar
            </button>
            <button disabled={ocupado} onClick={() => onAccion(agente.symbol, 'desactivar')}
              title="Lo para y lo excluye: la rotacion no lo vuelve a poner hasta que lo actives a mano"
              className={`${BOTON} border-zinc-700 text-zinc-300 hover:bg-zinc-800`}>
              <Power size={11} /> Excluir
            </button>
          </>
        ) : (
          <>
            <button disabled={ocupado} onClick={() => onAccion(agente.symbol, 'poner')}
              title="Propone meterlo en el grupo; lo apruebas en la bandeja o en Telegram"
              className={`${BOTON} border-emerald-500/40 text-emerald-300 hover:bg-emerald-500/10`}>
              Poner
            </button>
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

// Ajustes del agente de scalping (PST_LLM/scalping.py). Los simbolos no van aqui: cada simbolo elige su
// estrategia en su tarjeta. Cadencia 0 = continuo. Guarda todo de una vez y el agente lo relee en su siguiente vuelta.
const CAMPOS_SCALPING = [
  { clave: 'cadencia_s', etiqueta: 'Cadencia (s)', min: 0, max: 600, step: 1, ayuda: '0 = continuo: al acabar una vuelta empieza la siguiente' },
  { clave: 'pausa_minima_s', etiqueta: 'Pausa mínima (s)', min: 1, max: 60, step: 1, ayuda: 'Pausa entre vueltas, aunque la cadencia sea 0' },
  { clave: 'riesgo_pct', etiqueta: 'Riesgo por operación (%)', min: 0.05, max: 1, step: 0.05, ayuda: '% de equity que arriesga cada operación' },
  { clave: 'max_hold_min', etiqueta: 'Tiempo máximo (min)', min: 1, max: 240, step: 1, ayuda: 'Pasado este tiempo la operación se cierra sola' },
  { clave: 'max_abiertas', etiqueta: 'Operaciones abiertas', min: 1, max: 5, step: 1, ayuda: 'A la vez, en todos los símbolos de scalping' },
  { clave: 'max_perdida_dia_r', etiqueta: 'Parada diaria (R)', min: 0.5, max: 20, step: 0.5, ayuda: 'Si el día suma esta pérdida en R, no se abre nada más' },
]

export function LlmScalpingPanel({ api, API_BASE }) {
  const [cfg, setCfg] = useState(null)
  const [aviso, setAviso] = useState('')
  const [guardando, setGuardando] = useState(false)

  const cargar = useCallback(async () => {
    try {
      const r = await api.get(`${API_BASE}/llm/scalping`)
      setCfg(r.data)
    } catch (e) {
      setAviso('No se pudo leer la configuración del scalping')
    }
  }, [api, API_BASE])

  useEffect(() => { cargar() }, [cargar])

  if (!cfg) return <p className="text-[11px] text-zinc-500">{aviso || 'Cargando scalping…'}</p>

  const set = (clave, valor) => setCfg(c => ({ ...c, [clave]: valor }))

  const guardar = async () => {
    setGuardando(true)
    setAviso('')
    try {
      const cambios = Object.fromEntries(
        [...CAMPOS_SCALPING.map(c => c.clave), 'enabled', 'live'].map(k => [k, cfg[k]]))
      const r = await api.put(`${API_BASE}/llm/scalping`, cambios)
      setCfg(r.data)
      setAviso('Guardado. El agente lo aplica en su siguiente vuelta.')
    } catch (e) {
      setAviso(e?.response?.data?.detail || 'No se pudo guardar')
    } finally {
      setGuardando(false)
    }
  }

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-zinc-800/70 bg-zinc-950/60 p-4">
      <div className="flex flex-wrap items-center gap-4">
        <label className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-widest text-zinc-400">
          <input type="checkbox" checked={!!cfg.enabled} onChange={e => set('enabled', e.target.checked)} />
          Activo
        </label>
        <label className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-widest text-zinc-400"
          title="Simulación: no envía órdenes. Demo: opera de verdad en la cuenta demo">
          <input type="checkbox" checked={!!cfg.live} onChange={e => set('live', e.target.checked)} />
          Operar en demo (si no, simulación)
        </label>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {CAMPOS_SCALPING.map(c => (
          <label key={c.clave} className="flex flex-col gap-1" title={c.ayuda}>
            <span className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">{c.etiqueta}</span>
            <input type="number" min={c.min} max={c.max} step={c.step} value={cfg[c.clave] ?? ''}
              onChange={e => set(c.clave, e.target.value === '' ? '' : Number(e.target.value))} className={CAMPO} />
          </label>
        ))}
      </div>
      <div className="flex items-center gap-3">
        <button onClick={guardar} disabled={guardando}
          className={`${BOTON} flex-none px-4 border-indigo-500/40 text-indigo-300 hover:bg-indigo-500/10`}>
          Guardar scalping
        </button>
        {aviso && <span className="text-[11px] text-zinc-400">{aviso}</span>}
      </div>
    </div>
  )
}
