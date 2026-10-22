import React, { useState, useEffect, useCallback } from 'react'
import axios from 'axios'
import { Gauge, Save, RefreshCw, AlertTriangle } from 'lucide-react'

// Pestaña Puntuador: configura el agente que puntúa la home (solo visual) y muestra lo que gasta.
// El agente lee la configuración en cada vuelta, así que un guardado aplica en unos 15 s.
const REFRESCO_MS = 15000

function Toggle({ activo, onChange, label }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!activo)}
      className="flex items-center justify-between w-full px-4 py-3 rounded-xl bg-black/30 border border-white/5 hover:border-white/10 transition-all"
    >
      <span className="text-[11px] font-bold text-zinc-400 uppercase tracking-widest">{label}</span>
      <span className={`relative inline-flex h-5 w-9 rounded-full transition-colors ${activo ? 'bg-emerald-500/70' : 'bg-zinc-800'}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${activo ? 'left-4' : 'left-0.5'}`} />
      </span>
    </button>
  )
}

function Campo({ label, children }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[11px] font-bold text-zinc-500 uppercase tracking-widest">{label}</span>
      {children}
    </label>
  )
}

const claseInput = 'w-full px-3 py-2 rounded-xl bg-black/40 border border-white/5 text-sm font-bold text-white focus:outline-none focus:border-indigo-500/50'

function Metrica({ titulo, valor, detalle }) {
  return (
    <div className="glass-card rounded-2xl p-5 border border-zinc-800/80 flex flex-col gap-2">
      <span className="text-[11px] font-bold text-zinc-600 uppercase tracking-[0.25em]">{titulo}</span>
      <span className="text-2xl font-bold text-white tracking-tighter">{valor}</span>
      {detalle && <span className="text-[11px] font-bold text-zinc-500">{detalle}</span>}
    </div>
  )
}

const usd = (n) => (n == null ? '—' : `$${Number(n).toFixed(4)}`)
const hora = (iso) => (iso ? new Date(iso).toLocaleString() : '—')

export function PuntuadorPanel() {
  const [datos, setDatos] = useState(null)
  const [form, setForm] = useState(null)
  const [guardando, setGuardando] = useState(false)
  const [mensaje, setMensaje] = useState(null)
  const [error, setError] = useState(null)

  const cargar = useCallback(() => {
    axios.get('/api/puntuador')
      .then(({ data }) => {
        setDatos(data)
        setError(null)
        setForm(f => f ?? data.config)   // el formulario no se pisa mientras se edita
      })
      .catch(() => setError('No se pudo leer /api/puntuador'))
  }, [])

  useEffect(() => {
    cargar()
    const t = setInterval(cargar, REFRESCO_MS)
    return () => clearInterval(t)
  }, [cargar])

  const cambiar = (clave, valor) => setForm(f => ({ ...f, [clave]: valor }))
  const sucio = datos && form && JSON.stringify(form) !== JSON.stringify(datos.config)

  const guardar = async () => {
    setGuardando(true)
    setMensaje(null)
    try {
      const { data } = await axios.put('/api/puntuador/config', form)
      setForm(data.config)
      setMensaje({ tipo: 'ok', texto: 'Guardado. Se aplica en la siguiente vuelta (unos 15 s).' })
      cargar()
    } catch (e) {
      setMensaje({ tipo: 'error', texto: e.response?.data?.detail || 'No se pudo guardar' })
    } finally {
      setGuardando(false)
    }
  }

  if (!datos || !form) {
    return (
      <div className="flex items-center gap-2 text-zinc-500 text-sm">
        {error ? <><AlertTriangle size={16} className="text-rose-400" />{error}</> : 'Cargando…'}
      </div>
    )
  }

  const { estado, gasto } = datos
  const semana = gasto.ultimos_7_dias
  const estadoTxt = !form.activo ? 'Pausado' : estado.hilo_vivo ? 'Activo' : 'Sin hilo (reinicia la app)'
  const estadoColor = !form.activo ? 'text-zinc-500' : estado.hilo_vivo ? 'text-emerald-400' : 'text-rose-400'

  return (
    <div className="w-full h-full overflow-y-auto px-4 md:px-8 pb-10 flex flex-col gap-6">
      {/* Estado */}
      <div className="glass-card rounded-2xl p-6 border border-zinc-800/80 flex flex-wrap gap-6 items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2.5 bg-black/40 rounded-xl border border-white/5"><Gauge size={20} className={estadoColor} /></div>
          <div>
            <h3 className="text-white text-sm font-bold uppercase tracking-widest">Agente Puntuador</h3>
            <span className={`text-[11px] font-bold uppercase tracking-widest ${estadoColor}`}>{estadoTxt}</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-6 text-[11px] font-bold text-zinc-500 uppercase tracking-widest">
          <span>Última vuelta: <span className="text-zinc-300 normal-case">{hora(estado.ultima_utc)}</span></span>
          <span>Próxima: <span className="text-zinc-300 normal-case">{form.activo ? hora(estado.proxima_utc) : '—'}</span></span>
        </div>
      </div>

      {(estado.ultimo_error || estado.ultimo_aviso) && (
        <div className="rounded-2xl p-4 border border-amber-500/20 bg-amber-500/5 text-[12px] font-bold text-amber-300 flex gap-2 items-start">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <span>{estado.ultimo_error ? `Último error (${hora(estado.ultimo_error_utc)}): ${estado.ultimo_error}` : estado.ultimo_aviso}</span>
        </div>
      )}

      {/* Gasto */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Metrica titulo="Gasto hoy" valor={usd(gasto.hoy.coste_usd)} detalle={`${gasto.hoy.llamadas} llamadas${gasto.hoy.fallos ? ` · ${gasto.hoy.fallos} fallidas` : ''}`} />
        <Metrica titulo="Últimos 7 días" valor={usd(semana.coste_usd)} detalle={`${semana.llamadas} llamadas · ${semana.tokens_out.toLocaleString()} tokens de salida`} />
        <Metrica titulo="Coste medio por llamada" valor={usd(semana.coste_medio_usd)} detalle="Equivalente a precio API; con la suscripción consume cuota" />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        {/* Configuración */}
        <div className="glass-card rounded-2xl p-6 border border-zinc-800/80 flex flex-col gap-5">
          <h4 className="text-[11px] font-bold text-zinc-600 uppercase tracking-[0.25em]">Configuración</h4>

          <Toggle activo={form.activo} onChange={v => cambiar('activo', v)} label="Agente activo" />
          <Toggle activo={form.solo_mercado_abierto} onChange={v => cambiar('solo_mercado_abierto', v)} label="Solo símbolos con mercado abierto" />

          <div className="grid grid-cols-2 gap-4">
            <Campo label="Modelo">
              <select className={claseInput} value={form.modelo} onChange={e => cambiar('modelo', e.target.value)}>
                <option value="haiku">Haiku (barato)</option>
                <option value="sonnet">Sonnet (más preciso)</option>
                <option value="opus">Opus (el más caro)</option>
              </select>
            </Campo>
            <Campo label="Cadencia (min)">
              <input type="number" min={1} max={60} className={claseInput} value={form.cadencia_min}
                onChange={e => cambiar('cadencia_min', Number(e.target.value))} />
            </Campo>
            <Campo label="Tope cuota 5 h (%)">
              <input type="number" min={10} max={100} className={claseInput} value={form.cuota_max_5h}
                onChange={e => cambiar('cuota_max_5h', Number(e.target.value))} />
            </Campo>
            <Campo label="Tope cuota semanal (%)">
              <input type="number" min={10} max={100} className={claseInput} value={form.cuota_max_semanal}
                onChange={e => cambiar('cuota_max_semanal', Number(e.target.value))} />
            </Campo>
          </div>

          <div className="flex items-center gap-3 mt-1">
            <button
              type="button"
              onClick={guardar}
              disabled={!sucio || guardando}
              className="flex items-center gap-2 px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:bg-zinc-800 disabled:text-zinc-600 text-white text-[11px] font-bold uppercase tracking-widest transition-all"
            >
              <Save size={14} /> {guardando ? 'Guardando…' : 'Guardar'}
            </button>
            {sucio && <span className="text-[11px] font-bold text-zinc-500 uppercase">Cambios sin guardar</span>}
            {mensaje && (
              <span className={`text-[11px] font-bold ${mensaje.tipo === 'ok' ? 'text-emerald-400' : 'text-rose-400'}`}>{mensaje.texto}</span>
            )}
          </div>
        </div>

        {/* Últimas llamadas */}
        <div className="glass-card rounded-2xl p-6 border border-zinc-800/80 flex flex-col gap-4 min-h-0">
          <div className="flex items-center justify-between">
            <h4 className="text-[11px] font-bold text-zinc-600 uppercase tracking-[0.25em]">Últimas llamadas</h4>
            <button type="button" onClick={cargar} className="text-zinc-600 hover:text-zinc-300 transition-all" title="Actualizar">
              <RefreshCw size={14} />
            </button>
          </div>
          {gasto.ultimas.length === 0 ? (
            <span className="text-sm text-zinc-600">Aún no hay llamadas registradas.</span>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-[12px]">
                <thead>
                  <tr className="text-[10px] font-bold text-zinc-600 uppercase tracking-widest">
                    <th className="py-2 pr-3">Hora</th>
                    <th className="py-2 pr-3">Modelo</th>
                    <th className="py-2 pr-3">Símbolos</th>
                    <th className="py-2 pr-3">Salida</th>
                    <th className="py-2 pr-3">Coste</th>
                    <th className="py-2">Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {gasto.ultimas.map((u, i) => (
                    <tr key={i} className="border-t border-white/5 text-zinc-300">
                      <td className="py-2 pr-3 whitespace-nowrap">{new Date(u.ts).toLocaleTimeString()}</td>
                      <td className="py-2 pr-3">{u.modelo}</td>
                      <td className="py-2 pr-3">{u.n_simbolos ?? '—'}</td>
                      <td className="py-2 pr-3">{u.tokens_out ?? '—'}</td>
                      <td className="py-2 pr-3">{usd(u.cost_usd)}</td>
                      <td className={`py-2 font-bold ${u.ok ? 'text-emerald-400' : 'text-rose-400'}`}>{u.ok ? 'OK' : 'Fallo'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
