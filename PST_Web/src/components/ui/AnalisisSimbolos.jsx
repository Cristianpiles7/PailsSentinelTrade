import React, { useMemo, useState } from 'react'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import { X } from 'lucide-react'
import { MINIMO_FIABLE, agrupar, curva, resumen } from '../../lib/rendimiento'

const fmtEur = v => `${v >= 0 ? '+' : ''}${v.toFixed(2)}€`
const fmtR = v => (v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)} R`)
const fmtPF = v => (v === Infinity ? '∞' : v.toFixed(2))
const colorNeto = v => (v >= 0 ? 'text-emerald-400' : 'text-rose-400')

const COLUMNAS = [
  { clave: 'clave', titulo: 'Símbolo', align: 'text-left' },
  { clave: 'clase', titulo: 'Clase', align: 'text-left' },
  { clave: 'n', titulo: 'Ops', align: 'text-right' },
  { clave: 'aciertos', titulo: 'Acierto', align: 'text-right' },
  { clave: 'neto', titulo: 'Neto', align: 'text-right' },
  { clave: 'netoMedio', titulo: 'Neto medio', align: 'text-right' },
  { clave: 'rMedio', titulo: 'R medio', align: 'text-right' },
  { clave: 'profitFactor', titulo: 'PF', align: 'text-right' },
  { clave: 'coste', titulo: 'Coste', align: 'text-right' },
  { clave: 'minutos', titulo: 'Min medio', align: 'text-right' },
]

// Valor de orden de una fila para una columna. Sin dato va al final.
function valorOrden(fila, clave) {
  const v = fila[clave]
  if (v == null) return -Infinity
  return typeof v === 'string' ? v : v === Infinity ? Number.MAX_VALUE : v
}

function Muestra({ n }) {
  const fiable = n >= MINIMO_FIABLE
  return (
    <div className="flex items-center justify-end gap-2" title={fiable ? 'Muestra suficiente' : `Muestra corta: faltan ${MINIMO_FIABLE - n} operaciones para leer este símbolo`}>
      <div className="w-14 h-1 bg-zinc-800 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${fiable ? 'bg-emerald-500' : 'bg-zinc-500'}`} style={{ width: `${Math.min(100, (n / MINIMO_FIABLE) * 100)}%` }} />
      </div>
      <span className={`font-mono text-xs ${fiable ? 'text-white' : 'text-zinc-500'}`}>{n}</span>
    </div>
  )
}

export function RankingSimbolos({ filas, seleccionado, onSeleccionar }) {
  const [orden, setOrden] = useState({ clave: 'neto', sentido: -1 })

  const ordenadas = useMemo(() => {
    const out = [...filas]
    out.sort((a, b) => {
      const va = valorOrden(a, orden.clave), vb = valorOrden(b, orden.clave)
      if (va < vb) return -1 * orden.sentido
      if (va > vb) return 1 * orden.sentido
      return 0
    })
    return out
  }, [filas, orden])

  const cambiarOrden = clave => {
    setOrden(o => (o.clave === clave ? { clave, sentido: -o.sentido } : { clave, sentido: clave === 'clave' || clave === 'clase' ? 1 : -1 }))
  }

  if (!filas.length) {
    return <p className="text-sm text-zinc-500 px-2 py-6">Sin operaciones en este filtro.</p>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-white/5">
            {COLUMNAS.map(c => (
              <th key={c.clave} className={`pb-3 px-3 ${c.align} text-[11px] font-bold text-zinc-500 uppercase tracking-widest whitespace-nowrap`}>
                <button onClick={() => cambiarOrden(c.clave)} className="hover:text-zinc-300">
                  {c.titulo}{orden.clave === c.clave ? (orden.sentido < 0 ? ' ↓' : ' ↑') : ''}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.03]">
          {ordenadas.map(f => {
            const activa = seleccionado === f.clave
            return (
              <tr key={f.clave} onClick={() => onSeleccionar(activa ? null : f.clave)}
                className={`cursor-pointer transition-colors ${activa ? 'bg-indigo-500/10' : 'hover:bg-white/[0.02]'}`}>
                <td className="py-3 px-3 font-bold text-white">{f.clave}</td>
                <td className="py-3 px-3 text-xs text-zinc-400">{f.clase}</td>
                <td className="py-3 px-3"><Muestra n={f.n} /></td>
                <td className="py-3 px-3 text-right font-mono text-zinc-300">{f.aciertos.toFixed(0)}%</td>
                <td className={`py-3 px-3 text-right font-mono font-bold ${colorNeto(f.neto)}`}>{fmtEur(f.neto)}</td>
                <td className={`py-3 px-3 text-right font-mono ${colorNeto(f.netoMedio)}`}>{fmtEur(f.netoMedio)}</td>
                <td className="py-3 px-3 text-right font-mono text-zinc-300">{f.nR ? fmtR(f.rMedio) : <span className="text-zinc-600">—</span>}</td>
                <td className="py-3 px-3 text-right font-mono text-zinc-300">{fmtPF(f.profitFactor)}</td>
                <td className="py-3 px-3 text-right font-mono text-zinc-500">{f.coste.toFixed(2)}€</td>
                <td className="py-3 px-3 text-right font-mono text-zinc-500">{f.minutos == null ? '—' : Math.round(f.minutos)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="text-[11px] text-zinc-600 font-bold uppercase tracking-widest mt-4 px-3">
        Pulsa un símbolo para ver su detalle · Neto medio = neto por operación, comparable entre agentes y EAs · R medio solo donde hay R (agentes LLM)
      </p>
    </div>
  )
}

function Mini({ label, valor, color = 'text-white' }) {
  return (
    <div className="bg-zinc-900/40 border border-white/5 rounded-xl px-4 py-3">
      <p className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest">{label}</p>
      <p className={`text-lg font-bold font-mono ${color}`}>{valor}</p>
    </div>
  )
}

// Detalle de un simbolo: resumen, curva, motivos de salida (agentes) y ultimas operaciones.
export function DetalleSimbolo({ simbolo, ops, onCerrar }) {
  const res = useMemo(() => resumen(ops), [ops])
  const serie = useMemo(() => curva(ops), [ops])
  const motivos = useMemo(() => agrupar(ops.filter(o => o.motivo), 'motivo'), [ops])
  const ultimas = useMemo(() => [...ops].sort((a, b) => (b.cerrada || b.abierta || '').localeCompare(a.cerrada || a.abierta || '')).slice(0, 15), [ops])

  if (!res) return null

  return (
    <div className="pst-card p-6 space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h3 className="pst-title">{simbolo}</h3>
          <p className="text-[11px] text-zinc-500 font-bold uppercase tracking-widest mt-1">
            {ops[0]?.clase} · {res.n} operaciones{res.n < MINIMO_FIABLE ? ` · muestra corta (faltan ${MINIMO_FIABLE - res.n})` : ''}
          </p>
        </div>
        <button onClick={onCerrar} className="text-zinc-500 hover:text-zinc-300" title="Cerrar detalle"><X size={16} /></button>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-6 gap-3">
        <Mini label="Neto" valor={fmtEur(res.neto)} color={colorNeto(res.neto)} />
        <Mini label="Acierto" valor={`${res.aciertos.toFixed(0)}%`} />
        <Mini label="Profit factor" valor={fmtPF(res.profitFactor)} />
        <Mini label="R medio" valor={res.nR ? fmtR(res.rMedio) : '—'} />
        <Mini label="Pérdida máx." valor={`${res.perdidaMax.toFixed(2)}€`} color="text-rose-400" />
        <Mini label="Racha pérdidas" valor={res.rachaPerdidas} />
      </div>

      <div className="h-[180px]">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={serie}>
            <XAxis dataKey="t" hide />
            <YAxis stroke="#ffffff15" fontSize={9} width={50} tickFormatter={v => `${Math.round(v)}€`} />
            <ReferenceLine y={0} stroke="#ffffff30" strokeDasharray="4 4" />
            <Tooltip contentStyle={{ backgroundColor: '#0a0a0a', border: '1px solid #ffffff10', borderRadius: '1rem', fontSize: 12 }}
              formatter={v => [`${Number(v).toFixed(2)}€`, 'P&L acumulado']} />
            <Area type="monotone" dataKey="pnl" stroke="#818cf8" strokeWidth={2} fill="#818cf8" fillOpacity={0.12} dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {motivos.length > 0 && (
        <div>
          <p className="text-xs font-bold text-zinc-400 uppercase tracking-widest mb-2">Cómo salen las operaciones</p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {motivos.map(m => (
              <div key={m.clave} className="flex items-center justify-between bg-zinc-900/40 rounded-lg px-4 py-2 text-sm">
                <span className="text-zinc-300 font-bold">{m.clave} <span className="text-zinc-600 font-normal">· {m.n}</span></span>
                <span className="font-mono text-xs">
                  <span className={colorNeto(m.neto)}>{fmtEur(m.neto)}</span>
                  <span className="text-zinc-500 ml-3">{m.nR ? fmtR(m.rMedio) : ''}</span>
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div>
        <p className="text-xs font-bold text-zinc-400 uppercase tracking-widest mb-2">Últimas operaciones</p>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-zinc-500 uppercase tracking-widest text-[10px] border-b border-white/5">
                <th className="text-left py-2 px-2">Fecha</th>
                <th className="text-left py-2 px-2">Fuente</th>
                <th className="text-right py-2 px-2">Neto</th>
                <th className="text-right py-2 px-2">R</th>
                <th className="text-right py-2 px-2">Min</th>
                <th className="text-left py-2 px-2">Salida</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.03]">
              {ultimas.map(o => (
                <tr key={o.ticket}>
                  <td className="py-2 px-2 font-mono text-zinc-400">{(o.cerrada || o.abierta || '').slice(0, 16)}</td>
                  <td className="py-2 px-2 text-zinc-400">{o.fuente === 'LLM' ? `LLM${o.agente ? ` · ${o.agente}` : ''}` : o.fuente}</td>
                  <td className={`py-2 px-2 text-right font-mono ${colorNeto(o.neto)}`}>{fmtEur(o.neto)}</td>
                  <td className="py-2 px-2 text-right font-mono text-zinc-300">{o.r == null ? '—' : fmtR(o.r)}</td>
                  <td className="py-2 px-2 text-right font-mono text-zinc-500">{o.minutos == null ? '—' : Math.round(o.minutos)}</td>
                  <td className="py-2 px-2 text-zinc-500">{o.motivo || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
