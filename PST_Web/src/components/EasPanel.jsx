import { useCallback, useEffect, useMemo, useState } from 'react'
import { Power, Cpu, Download, Trash2, XCircle, FlaskConical, Hammer, History, Settings, Activity, ChevronDown, ChevronRight } from 'lucide-react'
import { usePollingWhenVisible } from '../hooks/usePolling'

// Seccion EAs de MT5 (rama feature/eas-mt5, ver MT5/DESARROLLO_EAS.md).
// Un EA = una estrategia x un simbolo x un timeframe. Todo lo que se hace aqui pasa por
// PST_Core/ea/servicio.py, igual que lo que hacen los agentes por MCP.

const TARJETA = 'bg-[#050505] border border-zinc-800/40 rounded-2xl p-4'
const BTN = 'flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest px-2.5 py-1.5 rounded-lg border transition-all disabled:opacity-40'
const BTN_NEUTRO = `${BTN} border-zinc-800 text-zinc-400 hover:text-zinc-200 hover:border-zinc-600`
const ESTADOS = ['idea', 'experimental', 'propuesta', 'demo-reducido', 'activa', 'pausada', 'retirada']
const PESTANAS = [['despliegues', 'Despliegues', <Cpu size={12} />], ['backtests', 'Backtests', <FlaskConical size={12} />],
  ['historial', 'Historial', <History size={12} />], ['config', 'Configuración', <Settings size={12} />]]

const nombre = (id) => (id || '').replace(/^PST_/, '')
const fecha = (ts) => (ts ? new Date(ts * 1000).toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'short' }) : '—')
const num = (v, d = 2) => (v === null || v === undefined ? '—' : Number(v).toFixed(d))
const hoy = () => new Date().toISOString().slice(0, 10)
const haceAnios = (n) => { const d = new Date(); d.setFullYear(d.getFullYear() - n); return d.toISOString().slice(0, 10) }

function Interruptor({ activo, onClick, disabled, titulo }) {
  return (
    <button onClick={onClick} disabled={disabled} title={titulo}
      className={`relative w-10 h-5 rounded-full transition-all disabled:opacity-40 ${activo ? 'bg-emerald-500' : 'bg-zinc-700'}`}>
      <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${activo ? 'left-5' : 'left-0.5'}`} />
    </button>
  )
}

function Punto({ ok, texto, aviso }) {
  const color = ok ? 'bg-emerald-400' : aviso ? 'bg-amber-400' : 'bg-zinc-600'
  return <span className="flex items-center gap-1.5"><span className={`w-1.5 h-1.5 rounded-full ${color}`} />{texto}</span>
}

function estadoLatido(d) {
  const l = d.latido
  if (!l) return { ok: false, texto: d.instalar ? 'sin latido' : 'no instalado' }
  if (!l.vivo) return { ok: false, aviso: true, texto: `parado (${Math.round(l.edad_s)} s)` }
  return { ok: l.puede_abrir === '1', aviso: l.puede_abrir !== '1', texto: l.puede_abrir === '1' ? 'operando' : 'vivo, en pausa' }
}

// ------------------------------------------------------------------ fila de un despliegue
function FilaDespliegue({ d, api, API_BASE, onCambio, setError, ocupado, setOcupado, abierto, setAbierto }) {
  const [multEditado, setMult] = useState(null)
  const mult = multEditado ?? d.mult_riesgo
  const [bt, setBt] = useState({ desde: haceAnios(2), hasta: hoy(), is_oos: true })
  const lat = estadoLatido(d)

  const accion = async (ruta, metodo = 'post', cuerpo) => {
    setOcupado(d.id); setError('')
    try {
      await api[metodo](`${API_BASE}/eas/${d.id}/${ruta}`, cuerpo)
      await onCambio()
    } catch (e) {
      setError(`${nombre(d.id)}: ${e?.response?.data?.detail || 'error'}`)
    } finally { setOcupado(null) }
  }

  const ub = d.ultimo_backtest
  return (
    <>
      <tr className="border-t border-zinc-800/40 text-[11px] text-zinc-300">
        <td className="py-2 pr-2">
          <button onClick={() => setAbierto(abierto ? null : d.id)} className="flex items-center gap-1 font-bold text-zinc-200 hover:text-white">
            {abierto ? <ChevronDown size={12} /> : <ChevronRight size={12} />}{nombre(d.id)}
          </button>
          <div className="text-[10px] text-zinc-600 pl-4">{d.estrategia}</div>
        </td>
        <td className="pr-2 font-mono">{d.simbolo}</td>
        <td className="pr-2 font-mono">{d.tf}</td>
        <td className="pr-2 font-mono text-zinc-500">{d.magic}</td>
        <td className="pr-2">
          <select value={d.estado} disabled={ocupado} onChange={e => accion('estado', 'put', { estado: e.target.value })}
            className="bg-zinc-900 border border-zinc-800 rounded-md px-1.5 py-1 text-[10px] uppercase tracking-wider">
            {ESTADOS.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </td>
        <td className="pr-2">
          <Interruptor activo={!!d.activo} disabled={ocupado || d.estado === 'retirada'}
            titulo={d.activo ? 'Pausar: deja de abrir operaciones nuevas' : 'Activar: puede abrir operaciones'}
            onClick={() => accion(d.activo ? 'pausar' : 'activar')} />
        </td>
        <td className="pr-2 text-[10px] uppercase tracking-wider text-zinc-400">
          <Punto ok={lat.ok} aviso={lat.aviso} texto={lat.texto} />
          {d.latido?.vivo && <div className="text-zinc-500 normal-case">{d.latido.posiciones} pos · {num(d.latido.flotante)} €</div>}
        </td>
        <td className="pr-2">
          <input type="number" step="0.1" min="0" value={mult} disabled={ocupado}
            onChange={e => setMult(e.target.value)}
            onBlur={async () => { if (Number(mult) !== Number(d.mult_riesgo)) await accion('riesgo', 'put', { mult: Number(mult) }); setMult(null) }}
            className="w-14 bg-zinc-900 border border-zinc-800 rounded-md px-1.5 py-1 text-[11px]" />
        </td>
        <td className="text-[10px] text-zinc-400">
          {ub ? <span title={`${ub.tramo} ${ub.desde} → ${ub.hasta}`}>PF {num(ub.pf)} · {ub.ops} ops · {num(ub.neto, 0)} €</span> : '—'}
        </td>
      </tr>
      {abierto && (
        <tr className="bg-zinc-950/60">
          <td colSpan={9} className="p-3">
            <div className="flex flex-wrap items-center gap-2">
              {d.instalar
                ? <button className={BTN_NEUTRO} disabled={ocupado} onClick={() => accion('retirar')}><Trash2 size={12} />Quitar del gráfico</button>
                : <button className={BTN_NEUTRO} disabled={ocupado} onClick={() => accion('instalar')}><Download size={12} />Instalar en el terminal</button>}
              <button className={`${BTN} border-rose-900/60 text-rose-300 hover:border-rose-600`} disabled={ocupado}
                onClick={() => window.confirm(`¿Cerrar YA las posiciones abiertas de ${nombre(d.id)}?`) && accion('cerrar')}>
                <XCircle size={12} />Cerrar posiciones
              </button>
              <button className={BTN_NEUTRO} disabled={ocupado} onClick={() => accion('compilar')}><Hammer size={12} />Compilar (pruebas)</button>
              <span className="mx-2 h-4 w-px bg-zinc-800" />
              <input type="date" value={bt.desde} onChange={e => setBt({ ...bt, desde: e.target.value })} className="bg-zinc-900 border border-zinc-800 rounded-md px-1.5 py-1 text-[11px]" />
              <input type="date" value={bt.hasta} onChange={e => setBt({ ...bt, hasta: e.target.value })} className="bg-zinc-900 border border-zinc-800 rounded-md px-1.5 py-1 text-[11px]" />
              <label className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-zinc-400">
                <input type="checkbox" checked={bt.is_oos} onChange={e => setBt({ ...bt, is_oos: e.target.checked })} />+ IS/OOS
              </label>
              <button className={`${BTN} border-indigo-800/60 text-indigo-200 hover:border-indigo-500`} disabled={ocupado}
                onClick={() => accion('backtest', 'post', bt)}><FlaskConical size={12} />Backtest</button>
            </div>
            <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-2 text-[10px] text-zinc-500">
              <div>Instalado: <span className="text-zinc-300">{d.instalar ? 'sí' : 'no'}</span>
                {d.gestor && <> · gráfico <span className="text-zinc-300">{d.gestor.cargado ? 'cargado' : 'pendiente'}</span></>}</div>
              <div>Versión: <span className="text-zinc-300">{d.version}</span></div>
              <div className="col-span-2 truncate" title={d.fuente}>Fuente: <span className="text-zinc-300">{(d.fuente || '').replace(/^.*MT5[\\/]/, 'MT5/')}</span></div>
              {d.gestor?.error && <div className="col-span-4 text-amber-400">Gestor: {d.gestor.error}</div>}
              {d.latido?.error && <div className="col-span-4 text-amber-400">EA: {d.latido.error}</div>}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

// ------------------------------------------------------------------ pestañas
function Backtests({ api, API_BASE, despliegues }) {
  const [filtro, setFiltro] = useState('')
  const [filas, setFilas] = useState([])
  const ids = despliegues.map(d => d.id).join(',')
  useEffect(() => {
    const lista = filtro ? [filtro] : ids.split(',').filter(Boolean)
    Promise.all(lista.map(i => api.get(`${API_BASE}/eas/${i}/backtests`).then(r => r.data).catch(() => [])))
      .then(t => setFilas(t.flat().sort((a, b) => b.ts - a.ts)))
  }, [api, API_BASE, filtro, ids])
  return (
    <div className={TARJETA}>
      <select value={filtro} onChange={e => setFiltro(e.target.value)} className="mb-3 bg-zinc-900 border border-zinc-800 rounded-md px-2 py-1 text-[11px]">
        <option value="">Todos los EAs</option>
        {despliegues.map(d => <option key={d.id} value={d.id}>{nombre(d.id)}</option>)}
      </select>
      <table className="w-full text-[11px]">
        <thead><tr className="text-left text-[10px] uppercase tracking-widest text-zinc-600">
          <th className="py-1">Fecha</th><th>EA</th><th>Tramo</th><th>Periodo</th><th>Neto €</th><th>PF</th><th>Sharpe</th><th>Ops</th><th>DD %</th><th>Calidad</th>
        </tr></thead>
        <tbody>{filas.map(b => (
          <tr key={b.id} className="border-t border-zinc-800/40 text-zinc-300">
            <td className="py-1">{fecha(b.ts)}</td><td>{nombre(b.despliegue_id)}</td><td>{b.tramo}</td>
            <td className="text-zinc-500">{b.desde} → {b.hasta}</td>
            <td className={b.neto > 0 ? 'text-emerald-400' : 'text-rose-400'}>{num(b.neto)}</td>
            <td>{num(b.pf)}</td><td>{num(b.sharpe)}</td><td>{b.ops}</td><td>{num(b.dd_equity_pct)}</td><td>{num(b.calidad_pct, 0)}%</td>
          </tr>))}
          {!filas.length && <tr><td colSpan={10} className="py-3 text-zinc-600">Sin backtests todavía.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

function Historial({ api, API_BASE }) {
  const [ev, setEv] = useState([])
  const cargar = useCallback(() => api.get(`${API_BASE}/eas/eventos`).then(r => setEv(r.data)).catch(() => {}), [api, API_BASE])
  useEffect(() => { cargar() }, [cargar])
  usePollingWhenVisible(cargar, 10000)
  return (
    <div className={TARJETA}>
      <table className="w-full text-[11px]">
        <thead><tr className="text-left text-[10px] uppercase tracking-widest text-zinc-600"><th className="py-1">Cuándo</th><th>EA</th><th>Acción</th><th>Detalle</th><th>Quién</th></tr></thead>
        <tbody>{ev.map(e => (
          <tr key={e.id} className="border-t border-zinc-800/40 text-zinc-300">
            <td className="py-1 whitespace-nowrap">{fecha(e.ts)}</td><td>{e.despliegue_id ? nombre(e.despliegue_id) : 'todos'}</td>
            <td className="uppercase text-[10px] tracking-wider">{e.accion}</td><td className="text-zinc-400">{e.detalle}</td>
            <td className={e.origen?.startsWith('agente') ? 'text-violet-300' : 'text-zinc-500'}>{e.origen}</td>
          </tr>))}
        </tbody>
      </table>
    </div>
  )
}

function Planificador({ api, API_BASE, setError }) {
  const [e, setE] = useState(null)
  const cargar = useCallback(() => api.get(`${API_BASE}/eas/planificador`).then(r => setE(r.data)).catch(() => {}), [api, API_BASE])
  useEffect(() => { cargar() }, [cargar])
  usePollingWhenVisible(cargar, 10000)
  if (!e) return null
  const lanzar = async () => {
    try { await api.post(`${API_BASE}/eas/planificador/lanzar`); await cargar() } catch (err) { setError(err?.response?.data?.detail || 'No se pudo lanzar') }
  }
  return (
    <div className={TARJETA}>
      <div className="flex flex-wrap items-center gap-4 mb-2">
        <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">Planificador de agentes</p>
        <Punto ok={e.activo} aviso={!e.activo} texto={e.activo ? `Cada noche a las ${e.hora}` : 'Apagado'} />
        <span className="text-[10px] uppercase tracking-wider text-zinc-400">Última: {fecha(e.ultima)}{e.motivo ? ` (${e.motivo})` : ''}</span>
        {e.en_curso && <span className="text-[10px] font-bold uppercase tracking-wider text-indigo-300 animate-pulse">En curso</span>}
        <button className={`${BTN_NEUTRO} ml-auto`} disabled={e.en_curso} onClick={lanzar}>Lanzar ahora</button>
      </div>
      <p className="text-[11px] text-zinc-600 mb-2">Decide qué agentes tocan (mejora-eas a diario, nuevas-eas los domingos, flota según sus puertas) y respeta los topes de cuota. Si no hay trabajo, no gasta nada.</p>
      {!!e.log?.length && <pre className="text-[10px] text-zinc-400 bg-black/40 rounded-lg p-2 max-h-48 overflow-auto whitespace-pre-wrap">{e.log.join('\n')}</pre>}
    </div>
  )
}

// Etiquetas y tipos de la configuracion global (claves de PST_Core/ea/store.py CONFIG_DEFECTO)
const CAMPOS = [
  ['Terminales', [
    ['terminal_vivo_dir', 'Carpeta de datos del terminal vivo (vacío = autodetectar)', 'texto'],
    ['terminal_vivo_instalacion', 'Instalación del terminal vivo (vacío = autodetectar)', 'texto'],
    ['tester_dir', 'Terminal portable de pruebas (backtests)', 'texto'],
    ['tester_login', 'Cuenta del tester (vacío = la guardada)', 'texto'],
    ['tester_server', 'Servidor del tester', 'texto'],
  ]],
  ['Backtest por defecto', [
    ['tester_modelo', 'Modelo (0 ticks, 1 OHLC M1, 2 apertura, 4 ticks reales)', 'numero'],
    ['tester_deposito', 'Depósito inicial', 'numero'],
    ['tester_divisa', 'Divisa', 'texto'],
    ['tester_apalancamiento', 'Apalancamiento', 'numero'],
    ['tester_timeout_s', 'Tiempo máximo por backtest (s)', 'numero'],
    ['tester_sin_freno_total', 'Backtest sin el freno de pérdida total de FTMO (recomendado para evaluar)', 'bool'],
  ]],
  ['Riesgo', [['mult_riesgo_max', 'Multiplicador de riesgo máximo por EA', 'numero']]],
  ['Agentes automáticos (taller de EAs y flota)', [
    ['planificador_activo', 'Lanzar el planificador de agentes cada noche', 'bool'],
    ['planificador_hora', 'Hora (HH:MM). Solo corre en las 4 h siguientes', 'texto'],
  ]],
  ['Criterios para aprobar un backtest', [
    ['criterio_pf_min', 'Profit factor mínimo', 'numero'],
    ['criterio_sharpe_min', 'Sharpe mínimo', 'numero'],
    ['criterio_ops_min', 'Operaciones mínimas', 'numero'],
    ['criterio_dd_max_pct', 'Drawdown máximo (%)', 'numero'],
    ['criterio_is_oos_positivos', 'Exigir ambas mitades (IS y OOS) positivas', 'bool'],
  ]],
]
const ACCIONES_AGENTE = ['pausar', 'activar', 'cerrar', 'riesgo', 'instalar', 'retirar', 'compilar', 'backtest']

function Configuracion({ api, API_BASE, config, onCambio, setError }) {
  const [cambiosConf, setCambios] = useState({})
  const v = { ...config, ...cambiosConf }
  const guardar = async () => {
    const cambios = Object.fromEntries(Object.entries(cambiosConf).filter(([k, x]) => JSON.stringify(x) !== JSON.stringify(config[k]) && k !== 'interruptor_global'))
    if (!Object.keys(cambios).length) return
    try { await api.put(`${API_BASE}/eas/config`, { cambios }); setCambios({}); await onCambio() } catch (e) { setError(e?.response?.data?.detail || 'No se pudo guardar') }
  }
  const pon = (k, x) => setCambios({ ...cambiosConf, [k]: x })
  return (
    <div className="flex flex-col gap-4">
      <Planificador api={api} API_BASE={API_BASE} setError={setError} />
      {CAMPOS.map(([grupo, campos]) => (
        <div key={grupo} className={TARJETA}>
          <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-500 mb-3">{grupo}</p>
          <div className="grid md:grid-cols-2 gap-3">
            {campos.map(([k, etiqueta, tipo]) => (
              <label key={k} className="flex flex-col gap-1 text-[11px] text-zinc-400">
                {etiqueta}
                {tipo === 'bool'
                  ? <input type="checkbox" checked={!!v[k]} onChange={e => pon(k, e.target.checked)} className="w-4 h-4" />
                  : <input type={tipo === 'numero' ? 'number' : 'text'} value={v[k] ?? ''} step="any"
                      onChange={e => pon(k, tipo === 'numero' ? Number(e.target.value) : e.target.value)}
                      className="bg-zinc-900 border border-zinc-800 rounded-md px-2 py-1.5 text-zinc-200" />}
              </label>
            ))}
          </div>
        </div>
      ))}
      <div className={TARJETA}>
        <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-500 mb-1">Qué pueden hacer los agentes sin tu aprobación</p>
        <p className="text-[11px] text-zinc-600 mb-3">Lo que no marques, el agente solo puede proponerlo.</p>
        <div className="flex flex-wrap gap-3">
          {ACCIONES_AGENTE.map(a => (
            <label key={a} className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-zinc-300">
              <input type="checkbox" checked={(v.agentes_pueden || []).includes(a)}
                onChange={e => pon('agentes_pueden', e.target.checked ? [...(v.agentes_pueden || []), a] : (v.agentes_pueden || []).filter(x => x !== a))} />
              {a}
            </label>
          ))}
        </div>
      </div>
      <button onClick={guardar} className={`${BTN} self-end border-indigo-700 text-indigo-200 hover:border-indigo-400 px-4 py-2`}>Guardar configuración</button>
    </div>
  )
}

// ------------------------------------------------------------------ panel
export function EasPanel({ api, API_BASE }) {
  const [datos, setDatos] = useState(null)
  const [trabajos, setTrabajos] = useState([])
  const [pestana, setPestana] = useState('despliegues')
  const [error, setError] = useState('')
  const [ocupado, setOcupado] = useState(null)
  const [abierto, setAbierto] = useState(null)

  const cargar = useCallback(async () => {
    try {
      const [r, t] = await Promise.all([api.get(`${API_BASE}/eas`), api.get(`${API_BASE}/eas/trabajos`)])
      setDatos(r.data); setTrabajos(t.data)
    } catch (e) { setError(e?.response?.data?.detail || 'No se pudo cargar la sección EAs') }
  }, [api, API_BASE])
  useEffect(() => { cargar() }, [cargar])
  usePollingWhenVisible(cargar, 5000)

  const porCategoria = useMemo(() => {
    const g = {}
    for (const d of datos?.despliegues || []) (g[d.categoria] ||= []).push(d)
    return g
  }, [datos])

  if (!datos) return <p className="text-[11px] text-zinc-600 font-bold uppercase tracking-wider">{error || 'Cargando EAs…'}</p>

  const cfg = datos.config
  const g = datos.gestor || {}
  const activos = datos.despliegues.filter(d => d.activo).length
  const vivos = datos.despliegues.filter(d => d.latido?.vivo).length
  const enCurso = trabajos.filter(t => t.estado === 'en_curso')

  const conmutarGlobal = async () => {
    if (!cfg.interruptor_global && !window.confirm('¿Encender el interruptor general? Los EAs activos podrán abrir operaciones.')) return
    try { await api.post(`${API_BASE}/eas/global`, { activo: !cfg.interruptor_global }); await cargar() } catch (e) { setError(e?.response?.data?.detail || 'error') }
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Cabecera: interruptor general y estado del terminal */}
      <div className={`${TARJETA} flex flex-wrap items-center gap-6`}>
        <div className="flex items-center gap-3">
          <Power size={16} className={cfg.interruptor_global ? 'text-emerald-400' : 'text-zinc-600'} />
          <div>
            <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-500">Interruptor general</p>
            <p className="text-[11px] text-zinc-300">{cfg.interruptor_global ? 'Encendido: los EAs activos pueden operar' : 'Apagado: ningún EA abre operaciones'}</p>
          </div>
          <Interruptor activo={cfg.interruptor_global} onClick={conmutarGlobal} />
        </div>
        <div className="flex items-center gap-4 text-[10px] font-bold uppercase tracking-widest text-zinc-400">
          <Activity size={14} className="text-zinc-600" />
          <Punto ok={g.vivo} aviso={!g.vivo} texto={g.vivo ? `Gestor vivo · cuenta ${g.cuenta}` : 'Gestor sin conexión'} />
          {g.vivo && <Punto ok={g.algo_trading} aviso={!g.algo_trading} texto={g.algo_trading ? 'Algo trading on' : 'Algo trading OFF'} />}
          <span>{activos} activos · {vivos} con latido · {datos.despliegues.length} EAs</span>
          {datos.entorno === 'dev' && <span className="px-2 py-0.5 rounded-md bg-amber-500 text-black">Pruebas · {datos.carpeta_control.split(/[\\/]/).pop()}</span>}
        </div>
      </div>

      {error && <p className="text-[11px] text-rose-400 font-bold">{error} <button className="underline ml-2" onClick={() => setError('')}>ocultar</button></p>}
      {enCurso.map(t => (
        <p key={t.id} className="text-[11px] text-indigo-300 font-bold uppercase tracking-wider animate-pulse">
          {t.tipo} en curso: {nombre(t.despliegue_id)} ({Math.round(Date.now() / 1000 - t.inicio)} s)
        </p>
      ))}
      {trabajos.filter(t => t.estado !== 'en_curso').slice(0, 1).map(t => (
        <p key={t.id} className={`text-[11px] font-bold ${t.estado === 'ok' ? 'text-emerald-400' : 'text-rose-400'}`}>
          Último trabajo: {t.tipo} de {nombre(t.despliegue_id)} → {t.estado}
          {t.resultado?.veredicto && <> · {t.resultado.veredicto.aprobado ? 'APROBADO' : `no aprobado (${t.resultado.veredicto.motivos.join('; ')})`}</>}
          {t.resultado?.compilacion && !t.resultado.compilacion.ok && <> · {t.resultado.compilacion.errores} errores de compilación</>}
          {t.resultado?.errores !== undefined && <> · {t.resultado.errores} errores, {t.resultado.avisos} avisos</>}
          {t.error && <> · {t.error}</>}
        </p>
      ))}

      <div className="flex items-center gap-1 border-b border-zinc-800/60">
        {PESTANAS.map(([id, txt, icono]) => (
          <button key={id} onClick={() => setPestana(id)}
            className={`flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-widest px-4 py-2 -mb-px border-b-2 transition-all ${pestana === id ? 'border-indigo-500 text-indigo-200' : 'border-transparent text-zinc-500 hover:text-zinc-300'}`}>
            {icono}{txt}
          </button>
        ))}
      </div>

      {pestana === 'despliegues' && Object.entries(porCategoria).map(([cat, lista]) => (
        <div key={cat} className={TARJETA}>
          <p className="text-[10px] font-bold uppercase tracking-widest text-zinc-500 mb-2">{cat}</p>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead><tr className="text-left text-[10px] uppercase tracking-widest text-zinc-600">
                <th className="py-1">EA</th><th>Símbolo</th><th>TF</th><th>Magic</th><th>Estado</th><th>Activo</th><th>En MT5</th><th>Riesgo ×</th><th>Último backtest</th>
              </tr></thead>
              <tbody>
                {lista.map(d => <FilaDespliegue key={d.id} d={d} api={api} API_BASE={API_BASE} onCambio={cargar} setError={setError}
                  ocupado={!!ocupado} setOcupado={setOcupado} abierto={abierto === d.id} setAbierto={setAbierto} />)}
              </tbody>
            </table>
          </div>
        </div>
      ))}
      {pestana === 'backtests' && <Backtests api={api} API_BASE={API_BASE} despliegues={datos.despliegues} />}
      {pestana === 'historial' && <Historial api={api} API_BASE={API_BASE} />}
      {pestana === 'config' && <Configuracion api={api} API_BASE={API_BASE} config={cfg} onCambio={cargar} setError={setError} />}
    </div>
  )
}
