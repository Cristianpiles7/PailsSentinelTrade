import React, { useState, useEffect } from 'react'
import axios from 'axios'
import { RefreshCw, ExternalLink, AlertTriangle } from 'lucide-react'
import { AgentFleetWidget } from './ui/AgentFleetWidget'
import { ProposalsTray } from './ui/ProposalsTray'

/**
 * Pestaña Oficina: la vista pixel-art de habblaud dentro de PST, mas el widget de flota.
 *
 * El iframe apunta al ESPEJO (127.0.0.1:4748 por defecto), no a habblaud directo. habblaud
 * sirve su pagina con `X-Frame-Options: DENY` (verificado contra el servidor real), que
 * bloquea el framing desde cualquier origen incluido el propio: en un iframe directo solo
 * se veria un panel en blanco. El espejo de PST_API/habblaud_proxy.py reenvia 1:1 quitando
 * esa cabecera, y va en puerto propio porque la pagina de habblaud usa rutas absolutas
 * (`/assets/...`, `/bundle/...`) que chocarian con el `/assets` del bundle de PST.
 *
 * La URL se pide a /api/habblaud-info en vez de hardcodearla, para que respete
 * HABBLAUD_MIRROR_PORT si se cambia por entorno.
 */
export function AgentsOffice() {
  const [mirrorUrl, setMirrorUrl] = useState(null)
  const [error, setError] = useState(null)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    axios.get('/api/habblaud-info')
      .then(({ data }) => setMirrorUrl(data.mirror_url))
      .catch(() => setError('No se pudo consultar /api/habblaud-info'))
  }, [])

  return (
    <div className="flex flex-col gap-4 h-full">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 max-w-md">
          <AgentFleetWidget />
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setReloadKey(k => k + 1)}
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-[10px] font-black uppercase tracking-widest text-zinc-500 hover:text-zinc-300 transition-all"
            title="Recargar la oficina"
          >
            <RefreshCw size={14} /> Recargar
          </button>
          {mirrorUrl && (
            <a
              href={mirrorUrl}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-2 px-4 py-2 rounded-xl text-[10px] font-black uppercase tracking-widest text-zinc-500 hover:text-cyan-400 transition-all"
              title="Abrir en el navegador"
            >
              <ExternalLink size={14} /> Navegador
            </a>
          )}
        </div>
      </div>

      {/* La bandeja va ARRIBA de la oficina a proposito: es donde se toman las decisiones.
          La vista pixel-art es contexto; esto es el trabajo. */}
      <ProposalsTray />

      <div className="flex-1 min-h-[600px] bg-[#090909] border border-zinc-800/60 rounded-2xl overflow-hidden relative">
        {error && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-zinc-500">
            <AlertTriangle size={32} className="text-amber-500" />
            <p className="text-[10px] font-black uppercase tracking-widest">{error}</p>
          </div>
        )}
        {!error && mirrorUrl && (
          <iframe
            key={reloadKey}
            src={mirrorUrl}
            title="Habblaud - oficina de agentes"
            className="w-full h-full border-0"
            style={{ minHeight: '600px' }}
          />
        )}
      </div>
    </div>
  )
}
