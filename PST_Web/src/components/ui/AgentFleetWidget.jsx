import React, { useState, useEffect } from 'react'
import axios from 'axios'
import { Users, Zap, Hand, CircleSlash } from 'lucide-react'

/**
 * Estado de la flota de agentes de Claude Code, leido de habblaud.
 *
 * Va contra `/api/habblaud/...` (proxy de PST_API/habblaud_proxy.py) y no contra
 * 127.0.0.1:4747 directamente: habblaud no implementa CORS a proposito y manda
 * `Cross-Origin-Resource-Policy: same-origin`, asi que un fetch del navegador a su puerto
 * lo bloquea el propio navegador. El proxy lo resuelve haciendo la peticion en el servidor.
 *
 * Polling y no SSE a proposito: su /api/stream existe, pero para cuatro contadores el coste
 * de mantener un EventSource a traves del proxy no se paga.
 */
export function AgentFleetWidget({ pollMs = 10000 }) {
  const [summary, setSummary] = useState(null)
  const [offline, setOffline] = useState(false)

  useEffect(() => {
    let cancelled = false

    const tick = async () => {
      try {
        const { data } = await axios.get('/api/habblaud/api/mod/summary')
        if (cancelled) return
        // El proxy devuelve 503 cuando habblaud no esta levantado, y axios lanza con 503,
        // asi que ese camino acaba en el catch. Este if solo cubre el caso de un 200 con
        // cuerpo de offline, por si el proxy cambia de codigo en el futuro.
        if (data?.habblaud === 'offline') {
          setOffline(true)
        } else {
          setSummary(data)
          setOffline(false)
        }
      } catch {
        if (!cancelled) setOffline(true)
      }
    }

    tick()
    const id = setInterval(tick, pollMs)
    return () => { cancelled = true; clearInterval(id) }
  }, [pollMs])

  if (offline || !summary) {
    return (
      <div className="bg-[#090909] border border-zinc-800/60 rounded-2xl p-3 flex items-center gap-3">
        <div className="p-2 rounded-lg border text-zinc-400 bg-zinc-800 border-zinc-700/50">
          <CircleSlash size={18} />
        </div>
        <div>
          <p className="text-[11px] font-bold text-zinc-500 uppercase tracking-widest">Flota</p>
          <p className="text-sm font-bold  tracking-tighter text-zinc-500 mt-1">
            {offline ? 'Apagada' : 'Conectando...'}
          </p>
          {offline && (
            <p className="text-[11px] text-zinc-600 mt-1 font-mono">npm start en ../habblaud</p>
          )}
        </div>
      </div>
    )
  }

  const waiting = summary.waiting?.length ?? 0

  return (
    <div className="bg-[#090909] border border-zinc-800/60 rounded-2xl p-3 relative overflow-hidden">
      <div className="absolute top-0 right-0 w-16 h-16 bg-white/[0.02] blur-2xl rounded-full -mr-8 -mt-8" />

      <div className="flex justify-between items-start relative z-10">
        <div className="p-1.5 rounded-lg border text-cyan-400 bg-cyan-500/10 border-cyan-500/20">
          <Users size={18} />
        </div>
        <span className="text-[11px] font-bold text-zinc-500 uppercase tracking-widest">
          Flota · v{summary.version}
        </span>
      </div>

      <div className="flex items-end gap-5 mt-2 relative z-10">
        <div>
          <p className="text-2xl font-bold  tracking-tighter text-white">{summary.agents ?? 0}</p>
          <p className="text-[11px] font-bold text-zinc-500 uppercase tracking-widest mt-0.5">Agentes</p>
        </div>
        <div>
          <p className="text-2xl font-bold  tracking-tighter text-emerald-500 flex items-center gap-1">
            <Zap size={16} />{summary.working ?? 0}
          </p>
          <p className="text-[11px] font-bold text-zinc-500 uppercase tracking-widest mt-0.5">Trabajando</p>
        </div>
        <div>
          <p className={`text-2xl font-bold  tracking-tighter flex items-center gap-1 ${waiting > 0 ? 'text-amber-500' : 'text-zinc-600'}`}>
            <Hand size={16} />{waiting}
          </p>
          <p className="text-[11px] font-bold text-zinc-500 uppercase tracking-widest mt-0.5">Te esperan</p>
        </div>
      </div>

      {/* La señal realmente util: quien esta parado esperando una respuesta tuya. */}
      {waiting > 0 && (
        <div className="mt-4 pt-3 border-t border-zinc-800/60 relative z-10 space-y-1">
          {summary.waiting.map((w, i) => (
            <p key={i} className="text-xs font-mono text-amber-400/90 truncate">
              {typeof w === 'string' ? w : (w.name ?? w.id ?? JSON.stringify(w))}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}
