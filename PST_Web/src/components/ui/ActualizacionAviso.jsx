import React, { useEffect, useState } from 'react'
import { Download, X, RefreshCw, AlertTriangle } from 'lucide-react'

// Aviso de version nueva publicada en GitHub Releases. Al aceptar, el backend descarga el exe,
// lo cambia por el actual y reinicia la app (PST_API/updater_api.py).
const REFRESCO_MS = 10 * 60 * 1000
const REFRESCO_DESCARGA_MS = 1000
const CLAVE_POSPUESTA = 'pst_update_pospuesta'

function leerPospuesta() {
  try { return localStorage.getItem(CLAVE_POSPUESTA) } catch { return null }
}

function guardarPospuesta(tag) {
  try { localStorage.setItem(CLAVE_POSPUESTA, tag) } catch { /* sin almacenamiento: vuelve a avisar */ }
}

export function ActualizacionAviso({ api, API_BASE }) {
  const [info, setInfo] = useState(null)
  const [pospuesta, setPospuesta] = useState(leerPospuesta)
  const [confirmando, setConfirmando] = useState(false)
  const [errorEnvio, setErrorEnvio] = useState(null)

  const fase = info?.estado?.fase
  const enCurso = fase === 'descargando' || fase === 'reiniciando'

  const cargar = () => {
    api.get(`${API_BASE}/update/status`)
      .then(({ data }) => setInfo(data))
      .catch(() => { /* durante el reinicio el servidor no responde: se mantiene el ultimo estado */ })
  }

  useEffect(() => {
    cargar()
    const t = setInterval(cargar, enCurso ? REFRESCO_DESCARGA_MS : REFRESCO_MS)
    return () => clearInterval(t)
  }, [enCurso])

  if (!info?.disponible && !enCurso) return null
  if (!enCurso && fase !== 'error' && pospuesta === info.ultima) return null

  const instalar = () => {
    setErrorEnvio(null)
    api.post(`${API_BASE}/update/apply`)
      .then(() => { setConfirmando(false); cargar() })
      .catch(e => setErrorEnvio(e?.response?.data?.detail || 'No se pudo iniciar la actualización'))
  }

  const posponer = () => {
    guardarPospuesta(info.ultima)
    setPospuesta(info.ultima)
  }

  const { descargado = 0, total = 0, error } = info.estado || {}
  const pct = total ? Math.min(100, Math.round((descargado / total) * 100)) : 0
  const notas = (info.notas || '').replace(/^v?\d+\.\d+\.\d+\s*-\s*/, '')
  const posiciones = info.posiciones_abiertas

  return (
    <div className="fixed bottom-8 right-8 z-[90] w-[380px] bg-zinc-950/95 backdrop-blur-xl border border-indigo-500/30 rounded-2xl shadow-2xl shadow-indigo-500/10 p-5">
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-xl bg-indigo-600/20 flex items-center justify-center shrink-0">
          {enCurso ? <RefreshCw size={18} className="text-indigo-400 animate-spin" /> : <Download size={18} className="text-indigo-400" />}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[11px] font-bold uppercase tracking-widest text-indigo-400">
            {fase === 'reiniciando' ? 'Reiniciando' : fase === 'descargando' ? 'Descargando actualización' : 'Nueva versión disponible'}
          </div>
          <div className="text-sm font-bold text-zinc-100 mt-0.5">
            v{info.actual} → {info.ultima}
          </div>
          {notas && !enCurso && (
            <p className="text-xs text-zinc-400 mt-2 leading-relaxed line-clamp-4" title={notas}>{notas}</p>
          )}
        </div>
        {!enCurso && (
          <button onClick={posponer} className="text-zinc-600 hover:text-zinc-300" title="Recordar con la siguiente versión">
            <X size={16} />
          </button>
        )}
      </div>

      {fase === 'descargando' && (
        <div className="mt-4">
          <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
            <div className="h-full bg-indigo-500 transition-all" style={{ width: `${pct}%` }} />
          </div>
          <div className="text-[11px] text-zinc-500 mt-1.5 font-mono">
            {(descargado / 1048576).toFixed(0)} / {(total / 1048576).toFixed(0)} MB
          </div>
        </div>
      )}

      {fase === 'reiniciando' && (
        <p className="text-xs text-zinc-400 mt-3">La app se cerrará y se abrirá sola con la versión nueva en unos segundos.</p>
      )}

      {(fase === 'error' || errorEnvio) && (
        <p className="text-xs text-rose-400 mt-3">Falló la actualización: {errorEnvio || error}. Sigues en v{info.actual}.</p>
      )}

      {!enCurso && !info.puede_instalar && (
        <p className="text-xs text-zinc-500 mt-3">Modo desarrollo: actualiza con <span className="font-mono">git pull</span>.</p>
      )}

      {!enCurso && info.puede_instalar && !confirmando && (
        <div className="flex gap-2 mt-4">
          <button onClick={() => setConfirmando(true)} className="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold py-2 rounded-xl">
            Actualizar ahora
          </button>
          <button onClick={posponer} className="px-4 text-xs font-bold text-zinc-400 hover:text-zinc-200 bg-zinc-900 rounded-xl">
            Más tarde
          </button>
        </div>
      )}

      {!enCurso && info.puede_instalar && confirmando && (
        <div className="mt-4">
          <div className="flex gap-2 items-start text-xs text-amber-300 bg-amber-500/5 border border-amber-500/20 rounded-xl p-3">
            <AlertTriangle size={14} className="shrink-0 mt-0.5" />
            <span>
              La app se reiniciará y los agentes LLM se pararán un momento (vuelven solos al abrir).
              {posiciones > 0 && ` Tienes ${posiciones} posición(es) abierta(s): siguen en MT5 con su SL/TP, pero nadie las gestiona durante el reinicio.`}
            </span>
          </div>
          <div className="flex gap-2 mt-3">
            <button onClick={instalar} className="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold py-2 rounded-xl">
              Sí, actualizar
            </button>
            <button onClick={() => setConfirmando(false)} className="px-4 text-xs font-bold text-zinc-400 hover:text-zinc-200 bg-zinc-900 rounded-xl">
              Cancelar
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
