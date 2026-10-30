import React, { useMemo, useState } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Cell, PieChart, Pie, ReferenceLine
} from 'recharts'
import { Target, Activity, Wallet, ShieldAlert, Layers, Coins } from 'lucide-react'
import { PerformanceCalendar } from './ui/PerformanceCalendar'
import { HourlyHeatmap } from './ui/HourlyHeatmap'
import { RankingSimbolos, DetalleSimbolo } from './ui/AnalisisSimbolos'
import { MINIMO_FIABLE, ORDEN_CLASES, agrupar, curva, destacados, filtrar, porHora, resumen } from '../lib/rendimiento'

const PERIODOS = [{ id: 7, t: '7 días' }, { id: 30, t: '30 días' }, { id: 90, t: '90 días' }, { id: null, t: 'Todo' }]
const FUENTES = [{ id: 'todas', t: 'Todas' }, { id: 'llm', t: 'Agentes LLM' }, { id: 'resto', t: 'EAs y manuales' }]
const BUCKET_COLORS = { TREND: '#6366f1', RANGE: '#f59e0b', SCALPING: '#10b981' }
const TOOLTIP = { backgroundColor: '#0a0a0a', border: '1px solid #ffffff10', borderRadius: '1rem', fontSize: 12 }

const fmtEur = v => `${v >= 0 ? '+' : ''}${v.toFixed(2)}€`
const fmtR = v => (v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)} R`)
const fmtPF = v => (v === Infinity ? '∞' : v.toFixed(2))
const colorNeto = v => (v >= 0 ? 'text-emerald-400' : 'text-rose-400')

function StatKPI({ label, value, sub, color = 'text-white', icon: Icon }) {
  return (
    <div className="bg-zinc-900/30 border border-white/5 rounded-2xl p-5 flex flex-col gap-3 hover:bg-zinc-900/50 transition-all">
      <div className={`p-3 rounded-2xl bg-white/5 w-fit ${color}`}>
        <Icon size={18} />
      </div>
      <div>
        <p className="text-xs font-bold text-zinc-500 uppercase tracking-widest mb-1">{label}</p>
        <h3 className={`text-2xl font-bold tracking-tighter ${color}`}>{value}</h3>
        {sub && <p className="text-[11px] text-zinc-600 font-bold uppercase tracking-widest mt-1">{sub}</p>}
      </div>
    </div>
  )
}

function Chip({ activo, onClick, children }) {
  return (
    <button onClick={onClick}
      className={`px-3 py-1.5 rounded-full text-xs font-bold uppercase tracking-widest border transition-colors ${
        activo ? 'bg-indigo-500/20 border-indigo-400/50 text-indigo-200' : 'border-white/10 text-zinc-500 hover:text-zinc-300'}`}>
      {children}
    </button>
  )
}

// Barras de neto por grupo (clase o fuente). La longitud es relativa al grupo mayor.
function Desglose({ titulo, filas }) {
  const maxAbs = Math.max(1, ...filas.map(f => Math.abs(f.neto)))
  return (
    <div className="pst-card p-6">
      <h3 className="pst-title mb-5">{titulo}</h3>
      {filas.length === 0 && <p className="text-sm text-zinc-500">Sin datos en este filtro.</p>}
      <div className="space-y-4">
        {filas.map(f => (
          <div key={f.clave}>
            <div className="flex justify-between items-center mb-1.5">
              <span className="text-xs font-bold uppercase tracking-widest text-zinc-400">
                {f.clave}
                <span className="text-zinc-600 normal-case tracking-normal font-normal"> · {f.n} ops · acierto {f.aciertos.toFixed(0)}%</span>
              </span>
              <span className={`text-xs font-mono font-bold ${colorNeto(f.neto)}`}>{fmtEur(f.neto)}</span>
            </div>
            <div className="h-1.5 w-full bg-zinc-800/60 rounded-full overflow-hidden">
              <div className={`h-full rounded-full transition-all duration-700 ${f.neto >= 0 ? 'bg-emerald-500' : 'bg-rose-500'}`}
                style={{ width: `${(Math.abs(f.neto) / maxAbs) * 100}%` }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// Lista de mejores o peores simbolos con su muestra y su neto medio.
function Destacados({ titulo, filas, vacio, tono }) {
  return (
    <div className="pst-card p-6">
      <h3 className="pst-title mb-4">{titulo}</h3>
      {filas.length === 0 ? (
        <p className="text-sm text-zinc-500">{vacio}</p>
      ) : (
        <ul className="divide-y divide-white/[0.03]">
          {filas.map(f => (
            <li key={f.clave} className="flex items-center justify-between py-3">
              <div>
                <p className="text-sm font-bold text-white">{f.clave}</p>
                <p className="text-[11px] text-zinc-500 font-bold uppercase tracking-widest">{f.clase} · {f.n} ops · acierto {f.aciertos.toFixed(0)}%</p>
              </div>
              <div className="text-right">
                <p className={`text-sm font-mono font-bold ${tono === 'pos' ? 'text-emerald-400' : 'text-rose-400'}`}>{fmtEur(f.netoMedio)} / op</p>
                <p className="text-[11px] text-zinc-500 font-mono">total {fmtEur(f.neto)}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function PerformanceDashboard({ operaciones = [], stratPerf = [], bucketData }) {
  const [periodo, setPeriodo] = useState(null)
  const [fuente, setFuente] = useState('todas')
  const [clase, setClase] = useState(null)
  const [simbolo, setSimbolo] = useState(null)

  const ops = useMemo(() => filtrar(operaciones, { dias: periodo, fuente, clase }), [operaciones, periodo, fuente, clase])
  const res = useMemo(() => resumen(ops), [ops])
  const filas = useMemo(() => agrupar(ops, 'symbol'), [ops])
  const dest = useMemo(() => destacados(filas), [filas])
  const porClase = useMemo(
    () => agrupar(ops, 'clase').sort((a, b) => ORDEN_CLASES.indexOf(a.clave) - ORDEN_CLASES.indexOf(b.clave)),
    [ops])
  const porFuente = useMemo(() => agrupar(ops, 'fuente'), [ops])
  const serie = useMemo(() => curva(ops), [ops])
  const horas = useMemo(() => porHora(ops), [ops])
  const opsSimbolo = useMemo(() => (simbolo ? ops.filter(o => o.symbol === simbolo) : []), [ops, simbolo])
  const calendario = useMemo(() => ops.map(o => ({ time_out: o.cerrada || o.abierta, profit: o.neto })), [ops])
  const clasesPresentes = useMemo(() => ORDEN_CLASES.filter(c => operaciones.some(o => o.clase === c)), [operaciones])

  const bucketWeights = bucketData?.weights ?? {}
  const bucketPnl = bucketData?.monthly_pnl ?? {}
  const pieData = Object.entries(bucketWeights).map(([cat, pct]) => ({
    name: cat, value: parseFloat(pct.toFixed(1)), fill: BUCKET_COLORS[cat] ?? '#6366f1'
  }))
  const stratRows = useMemo(() => [...stratPerf].sort((a, b) => (b.profit || 0) - (a.profit || 0)), [stratPerf])

  const pctListos = filas.length ? (dest.fiables / filas.length) * 100 : 0

  if (operaciones.length === 0) {
    return (
      <div className="px-4 py-16 text-center">
        <p className="text-zinc-400 font-bold">Aún no hay operaciones cerradas.</p>
        <p className="text-sm text-zinc-600 mt-2">Aparecerán aquí en cuanto se cierre la primera.</p>
      </div>
    )
  }

  return (
    <div className="w-full px-2 pb-10 animate-in fade-in slide-in-from-bottom-2 duration-700 space-y-8">

      {/* Filtros */}
      <div className="px-4 flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        <div className="flex flex-wrap gap-2">
          {PERIODOS.map(p => <Chip key={p.t} activo={periodo === p.id} onClick={() => setPeriodo(p.id)}>{p.t}</Chip>)}
          <span className="w-px bg-white/10 mx-1" />
          {FUENTES.map(f => <Chip key={f.id} activo={fuente === f.id} onClick={() => setFuente(f.id)}>{f.t}</Chip>)}
        </div>
        <div className="flex flex-wrap gap-2">
          <Chip activo={clase === null} onClick={() => setClase(null)}>Todas las clases</Chip>
          {clasesPresentes.map(c => <Chip key={c} activo={clase === c} onClick={() => setClase(c)}>{c}</Chip>)}
        </div>
      </div>

      {/* Estado de los datos: cuanto hay que leer con cautela */}
      <div className="px-4">
        <div className="pst-card p-5 flex flex-col md:flex-row md:items-center gap-5">
          <div className="md:w-72">
            <p className="text-xs font-bold text-zinc-500 uppercase tracking-widest">Datos para decidir</p>
            <p className="text-lg font-bold text-white mt-1">
              {dest.fiables} de {filas.length} símbolos con ≥ {MINIMO_FIABLE} ops
            </p>
          </div>
          <div className="flex-1">
            <div className="h-2 w-full bg-zinc-800/60 rounded-full overflow-hidden">
              <div className="h-full bg-indigo-500 rounded-full transition-all duration-700" style={{ width: `${pctListos}%` }} />
            </div>
            <p className="text-[11px] text-zinc-600 font-bold uppercase tracking-widest mt-2">
              Por debajo de {MINIMO_FIABLE} operaciones, la media de un símbolo puede ser azar: léelo como orientación, no como conclusión.
            </p>
          </div>
        </div>
      </div>

      {/* KPIs del filtro */}
      <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4 px-4">
        <StatKPI label="Neto" icon={Wallet}
          value={res ? fmtEur(res.neto) : '—'} color={res && res.neto >= 0 ? 'text-emerald-400' : 'text-rose-400'}
          sub={res ? `${res.n} operaciones` : ''} />
        <StatKPI label="Acierto" icon={Target}
          value={res ? `${res.aciertos.toFixed(0)}%` : '—'} color={res && res.aciertos >= 50 ? 'text-emerald-400' : 'text-rose-400'}
          sub={res ? `neto medio ${fmtEur(res.netoMedio)}` : ''} />
        <StatKPI label="Profit factor" icon={Activity}
          value={res ? fmtPF(res.profitFactor) : '—'}
          color={res && res.profitFactor >= 1.5 ? 'text-indigo-400' : res && res.profitFactor >= 1 ? 'text-amber-400' : 'text-rose-400'} />
        <StatKPI label="R medio" icon={Target}
          value={res && res.nR ? fmtR(res.rMedio) : '—'} color={res && res.rMedio >= 0 ? 'text-emerald-400' : 'text-rose-400'}
          sub={res ? `${res.nR} ops con R (agentes)` : ''} />
        <StatKPI label="Pérdida máx." icon={ShieldAlert}
          value={res ? `${res.perdidaMax.toFixed(2)}€` : '—'} color="text-rose-400"
          sub={res ? `racha ${res.rachaPerdidas} pérdidas` : ''} />
        <StatKPI label="Costes" icon={Coins}
          value={res ? `${res.coste.toFixed(2)}€` : '—'} color="text-zinc-300"
          sub="comisión + swap" />
      </div>

      {/* Mejores y peores */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 px-4">
        <Destacados titulo="Mejores símbolos" tono="pos" filas={dest.mejores}
          vacio={`Ningún símbolo tiene todavía ${MINIMO_FIABLE} operaciones en este filtro.`} />
        <Destacados titulo="Peores símbolos" tono="neg" filas={dest.peores}
          vacio={dest.fiables > 0 ? 'No hay más símbolos fiables para comparar.' : `Ningún símbolo tiene todavía ${MINIMO_FIABLE} operaciones en este filtro.`} />
      </div>

      {/* Ranking y detalle */}
      <div className="px-4 space-y-6">
        <div className="pst-card p-6">
          <h3 className="pst-title mb-5">Símbolos</h3>
          <RankingSimbolos filas={filas} seleccionado={simbolo} onSeleccionar={setSimbolo} />
        </div>
        {simbolo && opsSimbolo.length > 0 && (
          <DetalleSimbolo simbolo={simbolo} ops={opsSimbolo} onCerrar={() => setSimbolo(null)} />
        )}
      </div>

      {/* Desgloses */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 px-4">
        <Desglose titulo="Por clase de activo" filas={porClase} />
        <Desglose titulo="Por fuente" filas={porFuente} />
      </div>

      {/* Curvas */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 px-4">
        <div className="pst-card p-6">
          <div className="flex items-center justify-between mb-6">
            <h3 className="pst-title">Capital acumulado</h3>
            <span className="text-xs font-bold text-zinc-400 uppercase tracking-widest">P&amp;L acumulado del filtro</span>
          </div>
          <div className="h-[240px]">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={serie}>
                <CartesianGrid strokeDasharray="3 3" stroke="#ffffff05" vertical={false} />
                <XAxis dataKey="t" stroke="#ffffff15" fontSize={9} minTickGap={40} />
                <YAxis stroke="#ffffff15" fontSize={9} width={55} tickFormatter={v => `${v >= 0 ? '+' : ''}${Math.round(v)}€`} />
                <ReferenceLine y={0} stroke="#ffffff30" strokeDasharray="4 4" />
                <Tooltip contentStyle={TOOLTIP} labelStyle={{ color: '#888', fontSize: 10 }}
                  formatter={v => [`${Number(v).toFixed(2)}€`, 'P&L acumulado']} />
                <Area type="monotone" dataKey="pnl" stroke="#10b981" strokeWidth={2.5} fill="#10b981" fillOpacity={0.12} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
        <div className="pst-card p-6">
          <div className="flex items-center justify-between mb-6">
            <h3 className="pst-title">Caída desde el máximo</h3>
            <span className="text-sm font-bold font-mono text-rose-400">{res ? `${res.perdidaMax.toFixed(2)}€` : ''}</span>
          </div>
          <div className="h-[240px]">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={serie}>
                <CartesianGrid strokeDasharray="3 3" stroke="#ffffff05" vertical={false} />
                <XAxis dataKey="t" hide />
                <YAxis stroke="#ffffff15" fontSize={9} width={55} tickFormatter={v => `${Math.round(v)}€`} />
                <Tooltip contentStyle={TOOLTIP} formatter={v => [`${Number(v).toFixed(2)}€`, 'Caída']} labelFormatter={() => ''} />
                <Area type="monotone" dataKey="dd" stroke="#f43f5e" strokeWidth={2} fill="#f43f5e" fillOpacity={0.15} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Calendario y horas */}
      <div className="px-4">
        <PerformanceCalendar trades={calendario} />
      </div>
      <div className="px-4">
        <div className="pst-card p-6">
          <div className="flex items-center justify-between mb-6">
            <div>
              <h3 className="pst-title">Rendimiento por hora</h3>
              <p className="text-[11px] text-zinc-500 font-bold uppercase tracking-widest mt-1">
                P&amp;L por hora de cierre · hora local del broker
              </p>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full bg-emerald-500" />
              <span className="text-xs font-bold text-zinc-400 uppercase tracking-widest">Rentable</span>
              <div className="w-3 h-3 rounded-full bg-rose-500 ml-3" />
              <span className="text-xs font-bold text-zinc-400 uppercase tracking-widest">Pérdida</span>
            </div>
          </div>
          <HourlyHeatmap data={horas} />
        </div>
      </div>

      {/* Vista heredada: estrategias de los EAs y cubos de capital */}
      <details className="px-4 group">
        <summary className="cursor-pointer text-xs font-bold text-zinc-500 uppercase tracking-widest hover:text-zinc-300 select-none">
          <Layers size={12} className="inline mr-2" />Vista heredada: estrategias y cubos de capital
        </summary>
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mt-6">
          <div className="pst-card p-6">
            <h3 className="pst-title mb-4">Estrategias (EAs)</h3>
            {stratRows.length === 0 ? (
              <p className="text-sm text-zinc-500">Sin datos.</p>
            ) : (
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-white/5">
                    {['Estrategia', 'Trades', 'PnL total'].map(h => (
                      <th key={h} className="pb-3 px-3 text-[11px] font-bold text-zinc-500 uppercase tracking-widest">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.03]">
                  {stratRows.map(s => (
                    <tr key={s.strategy}>
                      <td className="py-3 px-3 font-bold text-white">{s.strategy}</td>
                      <td className="py-3 px-3 font-mono text-zinc-300">{s.trades_count ?? 0}</td>
                      <td className={`py-3 px-3 font-mono font-bold ${colorNeto(s.profit ?? 0)}`}>{fmtEur(s.profit ?? 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <div className="pst-card p-6">
            <h3 className="pst-title mb-4">Cubos de capital</h3>
            <div className="flex items-center gap-6">
              <div className="w-[120px] h-[120px] flex-shrink-0">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={pieData} cx="50%" cy="50%" innerRadius={35} outerRadius={55} paddingAngle={3} dataKey="value">
                      {pieData.map(entry => <Cell key={entry.name} fill={entry.fill} stroke="transparent" />)}
                    </Pie>
                    <Tooltip contentStyle={TOOLTIP} formatter={v => [`${v}%`, '']} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <ul className="flex-1 space-y-2 text-sm">
                {Object.entries(bucketWeights).map(([cat, pct]) => (
                  <li key={cat} className="flex justify-between">
                    <span className="font-bold uppercase tracking-widest text-zinc-400 text-xs">{cat}</span>
                    <span className="font-mono text-xs">
                      <span className={colorNeto(bucketPnl[cat] ?? 0)}>{fmtEur(bucketPnl[cat] ?? 0)}</span>
                      <span className="text-white ml-3">{pct.toFixed(1)}%</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </details>
    </div>
  )
}
