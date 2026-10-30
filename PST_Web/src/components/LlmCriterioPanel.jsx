import { useCallback, useEffect, useState } from 'react'

// Criterio de entrada de cada agente LLM (la parte del prompt que cambia por simbolo) y ajustes del
// agente que propone mejoras. La parte fija del prompt (formato, costes, cierres) no se edita aqui.

const CAMPO = 'w-full text-xs bg-zinc-900 text-zinc-200 border border-zinc-700 rounded-lg px-2 py-1.5 focus:outline-none focus:border-indigo-500/60'
const BOTON = 'text-[11px] font-bold uppercase tracking-widest px-3 py-1.5 rounded-lg border transition-colors disabled:opacity-40'

function MejoraAjustes({ api, API_BASE }) {
  const [a, setA] = useState(null)
  const [aviso, setAviso] = useState('')

  useEffect(() => {
    api.get(`${API_BASE}/llm/mejora-prompt`).then(r => setA(r.data)).catch(() => setA(null))
  }, [api, API_BASE])

  if (!a) return null
  const set = (k, v) => setA(x => ({ ...x, [k]: v }))
  const guardar = async () => {
    try {
      const r = await api.put(`${API_BASE}/llm/mejora-prompt`, {
        ...a,
        min_operaciones: Number(a.min_operaciones),
        cadencia_min: Number(a.cadencia_min),
        max_caracteres: Number(a.max_caracteres),
        max_operaciones_contexto: Number(a.max_operaciones_contexto),
      })
      setA(r.data)
      setAviso('Guardado')
    } catch (e) {
      setAviso(e?.response?.data?.detail || 'No se pudo guardar')
    }
  }

  const numeros = [
    { k: 'min_operaciones', etiqueta: 'Mín. operaciones', min: 20, max: 500,
      title: 'Operaciones cerradas nuevas de un símbolo (con su criterio actual) antes de que el agente lo revise' },
    { k: 'cadencia_min', etiqueta: 'Mirar cada (min)', min: 10, max: 1440,
      title: 'Cada cuántos minutos mira si algún símbolo llegó al mínimo' },
    { k: 'max_caracteres', etiqueta: 'Máx. caracteres', min: 500, max: 10000,
      title: 'Longitud máxima del criterio (tuyo o propuesto)' },
    { k: 'max_operaciones_contexto', etiqueta: 'Ops. que ve', min: 10, max: 300,
      title: 'Cuántas operaciones (las más recientes) ve como mucho en cada revisión' },
  ]

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <label className="flex flex-col gap-1.5" title="Si está apagado no revisa ni propone nada">
          <span className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">Agente de mejora</span>
          <select value={a.activo ? 'si' : 'no'} onChange={e => set('activo', e.target.value === 'si')} className={CAMPO}>
            <option value="si">encendido</option>
            <option value="no">apagado</option>
          </select>
        </label>
        <label className="flex flex-col gap-1.5" title="Modelo con el que revisa">
          <span className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">Modelo</span>
          <select value={a.modelo} onChange={e => set('modelo', e.target.value)} className={CAMPO}>
            {['haiku', 'sonnet', 'opus'].map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>
        {numeros.map(c => (
          <label key={c.k} title={c.title} className="flex flex-col gap-1.5">
            <span className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">{c.etiqueta}</span>
            <input type="number" min={c.min} max={c.max} value={a[c.k]} onChange={e => set(c.k, e.target.value)} className={CAMPO} />
          </label>
        ))}
      </div>
      <div className="flex items-center gap-3">
        <button onClick={guardar} className={`${BOTON} border-indigo-500/50 text-indigo-200 hover:bg-indigo-600/20`}>Guardar</button>
        {aviso && <span className="text-[11px] font-bold uppercase tracking-widest text-zinc-500">{aviso}</span>}
      </div>
    </div>
  )
}

export function LlmCriterioPanel({ api, API_BASE, agentes }) {
  const simbolos = [...new Set((agentes || []).map(a => a.symbol))].sort()
  const [symbol, setSymbol] = useState('')
  const [datos, setDatos] = useState(null)
  const [texto, setTexto] = useState('')
  const [aviso, setAviso] = useState('')

  useEffect(() => {
    if (!symbol && simbolos.length) setSymbol(simbolos[0])
  }, [simbolos, symbol])

  const cargar = useCallback(async () => {
    if (!symbol) return
    try {
      const r = await api.get(`${API_BASE}/llm/criterio/${encodeURIComponent(symbol)}`)
      setDatos(r.data)
      setTexto(r.data.texto)
      setAviso('')
    } catch (e) {
      setDatos(null)
      setAviso(e?.response?.data?.detail || 'No se pudo leer el criterio')
    }
  }, [api, API_BASE, symbol])

  useEffect(() => { cargar() }, [cargar])

  const guardar = async (t) => {
    try {
      const r = await api.put(`${API_BASE}/llm/criterio/${encodeURIComponent(symbol)}`, { texto: t })
      setDatos(r.data)
      setTexto(r.data.texto)
      setAviso('Guardado: el agente lo usa desde su próxima vuelta')
    } catch (e) {
      setAviso(e?.response?.data?.detail || 'No se pudo guardar')
    }
  }

  const cambiado = datos && texto.trim() !== datos.texto.trim()

  return (
    <div className="glass-card rounded-2xl p-5 border border-zinc-800/80 flex flex-col gap-5">
      <div className="flex flex-col gap-1">
        <p className="text-[11px] font-bold text-zinc-400 uppercase tracking-widest">Criterio de cada agente</p>
        <p className="text-xs text-zinc-500 leading-relaxed">
          La parte del prompt que decide cuándo entrar, una por símbolo. Formato, costes y reglas de cierre son fijos.
          El agente de mejora propone cambios cuando un símbolo lleva el mínimo de operaciones nuevas; se aprueban en la bandeja o en Telegram.
        </p>
      </div>

      <MejoraAjustes api={api} API_BASE={API_BASE} />

      <div className="flex flex-col gap-3 border-t border-zinc-800/80 pt-4">
        <div className="flex flex-wrap items-center gap-3">
          <select value={symbol} onChange={e => setSymbol(e.target.value)} className={`${CAMPO} w-auto`}>
            {simbolos.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          {datos && (
            <span className="text-[11px] text-zinc-500">
              {datos.defecto ? 'Criterio por defecto' : `Activo desde ${datos.activado} · ${datos.por}`} · v{datos.version}
            </span>
          )}
          {datos?.propuesta_pendiente && (
            <span className="text-[11px] font-bold text-amber-400">
              Propuesta #{datos.propuesta_pendiente} pendiente: si guardas aquí, queda obsoleta
            </span>
          )}
        </div>

        <textarea value={texto} onChange={e => setTexto(e.target.value)} rows={Math.min(18, Math.max(6, texto.split('\n').length + 1))}
          className={`${CAMPO} font-mono leading-relaxed`} spellCheck={false} />
        <div className="flex flex-wrap items-center gap-3">
          <button disabled={!cambiado} onClick={() => guardar(texto)}
            className={`${BOTON} border-indigo-500/50 text-indigo-200 hover:bg-indigo-600/20`}>Guardar</button>
          <button disabled={!cambiado} onClick={() => setTexto(datos.texto)}
            className={`${BOTON} border-zinc-700 text-zinc-300 hover:bg-zinc-800`}>Deshacer</button>
          <button disabled={!datos || datos.defecto} onClick={() => setTexto(datos.defecto_texto)}
            title="Carga el criterio por defecto en el editor (hay que guardar)"
            className={`${BOTON} border-zinc-700 text-zinc-300 hover:bg-zinc-800`}>Cargar el de por defecto</button>
          <span className="text-[11px] text-zinc-600 tabular-nums">{texto.length}/{datos?.max_caracteres ?? '—'}</span>
          {aviso && <span className="text-[11px] font-bold text-zinc-500">{aviso}</span>}
        </div>

        {datos?.historial?.length > 0 && (
          <details className="text-[11px] text-zinc-400">
            <summary className="cursor-pointer hover:text-zinc-200">Historial ({datos.historial.length} versiones)</summary>
            <div className="mt-2 space-y-2">
              {datos.historial.map(h => (
                <div key={h.id} className="border border-zinc-800 rounded-lg p-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-zinc-500">{h.activado} · {h.por} · v{h.version}</span>
                    <button onClick={() => setTexto(h.texto)} className="text-indigo-300 hover:text-indigo-200 underline">
                      Cargar en el editor
                    </button>
                  </div>
                  <pre className="mt-1 whitespace-pre-wrap text-zinc-400">{h.texto}</pre>
                </div>
              ))}
            </div>
          </details>
        )}
      </div>
    </div>
  )
}
