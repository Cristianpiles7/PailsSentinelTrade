import { useEffect, useMemo, useState } from 'react'
import { Save, RefreshCw } from 'lucide-react'

// Formulario generico: pinta los parametros de un ambito (global, estrategia o simbolo) a partir del
// esquema del backend (PST_LLM/config_store.py). Lo que se edita aqui es lo que el agente lee.

const ORIGEN = {
  defecto: 'por defecto',
  estrategia: 'de la estrategia',
  global: 'global',
  simbolo: 'propio del simbolo',
}

const CAMPO = 'w-full text-[11px] font-bold uppercase bg-zinc-900 text-zinc-200 border border-zinc-700 rounded-lg px-2 py-1.5 focus:outline-none focus:border-indigo-500/60'

function agrupar(definiciones) {
  const grupos = []
  for (const d of definiciones) {
    const g = grupos.find(x => x.nombre === d.grupo)
    if (g) g.items.push(d)
    else grupos.push({ nombre: d.grupo || 'General', items: [d] })
  }
  return grupos
}

function Control({ d, valor, onCambio }) {
  if (d.tipo === 'bool') {
    return (
      <button type="button" onClick={() => onCambio(!valor)}
        className={`w-9 h-4.5 rounded-full relative transition-all ${valor ? 'bg-indigo-600' : 'bg-zinc-800'}`}>
        <div className={`absolute top-0.5 w-3.5 h-3.5 rounded-full bg-white transition-all ${valor ? 'right-0.5' : 'left-0.5'}`} />
      </button>
    )
  }
  if (d.tipo === 'enum') {
    return (
      <select className={CAMPO} value={valor ?? ''} onChange={e => onCambio(e.target.value)}>
        {d.opciones.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    )
  }
  return (
    <input type="number" className={CAMPO} value={valor ?? ''} min={d.min} max={d.max}
      step={d.paso ?? (d.tipo === 'int' ? 1 : 0.01)}
      onChange={e => onCambio(e.target.value === '' ? '' : Number(e.target.value))} />
  )
}

export function ConfigForm({ definiciones, valores, onGuardar, vacio = 'Sin parametros en este ambito' }) {
  const [borrador, setBorrador] = useState({})
  const [error, setError] = useState('')
  const [guardando, setGuardando] = useState(false)
  const porClave = useMemo(() => Object.fromEntries((valores || []).map(v => [v.clave, v])), [valores])

  // Al llegar valores nuevos (tras guardar o al cambiar de simbolo) se descarta el borrador
  useEffect(() => { setBorrador({}); setError('') }, [valores])

  if (!definiciones.length) {
    return <p className="text-[11px] text-zinc-600 font-bold uppercase tracking-wider">{vacio}</p>
  }

  const cambiar = (clave, v) => setBorrador(b => ({ ...b, [clave]: v }))
  const sucios = Object.keys(borrador)

  async function guardar() {
    setGuardando(true)
    setError('')
    try {
      await onGuardar(borrador)
    } catch (err) {
      setError(err?.response?.data?.detail || 'No se pudo guardar')
    } finally {
      setGuardando(false)
    }
  }

  return (
    <div className="flex flex-col gap-5">
      {agrupar(definiciones).map(g => (
        <div key={g.nombre} className="flex flex-col gap-2">
          <p className="text-[11px] font-bold text-zinc-500 uppercase tracking-[0.2em]">{g.nombre}</p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3">
            {g.items.map(d => {
              const actual = porClave[d.clave]
              const valor = d.clave in borrador ? borrador[d.clave] : actual?.valor
              return (
                <div key={d.clave} className="flex flex-col gap-1">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="text-[11px] font-bold text-zinc-300 uppercase tracking-wide">{d.etiqueta}</span>
                    {actual && !(d.clave in borrador) && (
                      <span className="text-[10px] font-bold text-zinc-600 uppercase tracking-wider">
                        {ORIGEN[actual.origen] || actual.origen}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="flex-1"><Control d={d} valor={valor} onCambio={v => cambiar(d.clave, v)} /></div>
                  </div>
                  {d.ayuda && <span className="text-[10px] text-zinc-600 font-bold uppercase tracking-wider">{d.ayuda}</span>}
                </div>
              )
            })}
          </div>
        </div>
      ))}

      {error && <p className="text-[11px] text-rose-400 font-bold uppercase tracking-wider">{error}</p>}

      <div className="flex items-center gap-3">
        <button type="button" onClick={guardar} disabled={!sucios.length || guardando}
          className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-widest px-4 py-2 rounded-xl border border-indigo-500/50 bg-indigo-600/20 text-indigo-200 disabled:opacity-40 transition-all">
          {guardando ? <RefreshCw size={14} className="animate-spin" /> : <Save size={14} />}
          Guardar{sucios.length ? ` (${sucios.length})` : ''}
        </button>
        {sucios.length > 0 && (
          <button type="button" onClick={() => { setBorrador({}); setError('') }}
            className="text-[11px] font-bold uppercase tracking-widest text-zinc-500 hover:text-zinc-300">
            Descartar
          </button>
        )}
      </div>
    </div>
  )
}
