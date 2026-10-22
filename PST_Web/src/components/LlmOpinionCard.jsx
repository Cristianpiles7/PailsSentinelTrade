import { Component, useEffect, useState } from 'react'
import { Bot } from 'lucide-react'

// Lo ultimo que dijo el agente LLM sobre este simbolo. Sale en el hueco de inteligencia del grafico.
export function LlmOpinionCard({ api, API_BASE, symbol, onHistorial }) {
  const [dec, setDec] = useState(null)
  const [sinAgente, setSinAgente] = useState(false)

  useEffect(() => {
    let vivo = true
    const cargar = async () => {
      try {
        const r = await api.get(`${API_BASE}/llm/agentes/${encodeURIComponent(symbol)}/decisiones?limite=1`)
        if (!vivo) return
        setSinAgente(false)
        setDec(r.data[0] || null)
      } catch (e) {
        if (!vivo) return
        setSinAgente(true)
        setDec(null)
      }
    }
    cargar()
    const t = setInterval(cargar, 60000)
    return () => { vivo = false; clearInterval(t) }
  }, [api, API_BASE, symbol])

  if (sinAgente) {
    return (
      <div className="col-span-full py-8 flex flex-col items-center justify-center gap-2 opacity-30">
        <Bot size={22} />
        <p className="text-[11px] font-bold uppercase tracking-[0.4em] ">Sin agente LLM para {symbol}</p>
      </div>
    )
  }
  if (!dec) {
    return (
      <div className="col-span-full py-8 flex flex-col items-center justify-center gap-2 opacity-40">
        <Bot size={22} />
        <p className="text-[11px] font-bold uppercase tracking-[0.4em] ">Sin decisiones todavía</p>
      </div>
    )
  }

  let razon = ''
  try { razon = JSON.parse(dec.respuesta_json || '{}').reason || '' } catch (e) { razon = '' }
  const accion = dec.llamado ? (dec.accion || '-').toUpperCase() : 'SIN LLAMADA'
  const color = accion === 'BUY' ? 'text-emerald-400' : accion === 'SELL' ? 'text-rose-400' : 'text-zinc-300'

  return (
    <div className="col-span-full p-5 flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-4">
        <Bot size={16} className="text-indigo-400" />
        <span className="text-xs font-bold uppercase tracking-[0.3em] text-zinc-400">Opinión del agente</span>
        <span className={`text-sm font-bold ${color}`}>{accion}</span>
        {dec.confianza != null && <span className="text-xs text-zinc-500">confianza {Number(dec.confianza).toFixed(2)}</span>}
        <span className="text-xs text-zinc-600">{dec.ts}</span>
        <span className="text-xs text-zinc-600 uppercase">{dec.modo === 'live' ? 'demo' : 'simulación'} · {dec.modelo}</span>
        {onHistorial && (
          <button onClick={onHistorial} className="ml-auto text-xs font-bold uppercase text-indigo-300 hover:text-indigo-200">Historial</button>
        )}
      </div>
      {razon && (
        <div className="grid gap-1.5 bg-zinc-950/60 border border-zinc-800/60 rounded-xl p-4">
          {razon.split(/\r?\n/).filter(l => l.trim()).map((l, i) => {
            const m = l.match(/^([^:]{2,22}):\s*(.*)$/)
            return m ? (
              <p key={i} className="text-[11px] text-zinc-300 leading-relaxed">
                <span className="font-bold text-indigo-300 uppercase tracking-wider">{m[1]}:</span> {m[2]}
              </p>
            ) : (
              <p key={i} className="text-[11px] text-zinc-300 leading-relaxed">{l}</p>
            )
          })}
        </div>
      )}
      <div className="flex flex-wrap gap-4 text-xs text-zinc-500">
        {dec.motivo_puerta && <span>puerta: {dec.motivo_puerta}</span>}
        {dec.validacion && <span className="text-amber-400">{dec.validacion}</span>}
        {dec.resultado && <span>resultado: {dec.resultado}</span>}
        {dec.coste_usd != null && <span>coste ${Number(dec.coste_usd).toFixed(4)}</span>}
      </div>
    </div>
  )
}


// Si la tarjeta falla al pintar, no se lleva por delante la pantalla del grafico: muestra el error.
export class LlmSeguro extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error) {
    return { error }
  }
  componentDidUpdate(prev) {
    if (prev.symbol !== this.props.symbol && this.state.error) this.setState({ error: null })
  }
  render() {
    if (this.state.error) {
      return (
        <div className="col-span-full p-5 text-[11px] text-rose-400">
          No se pudo mostrar la opinión del agente: {String(this.state.error.message || this.state.error)}
        </div>
      )
    }
    return <LlmOpinionCard {...this.props} />
  }
}
