import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

// Visor de errores de diagnostico: si algo falla al pintar, la pantalla se queda en negro y no hay
// consola a mano. Este bloque escribe el error en un recuadro encima de todo, sin depender de React.
function mostrarError(texto) {
  let caja = document.getElementById('pst-error-overlay')
  if (!caja) {
    caja = document.createElement('pre')
    caja.id = 'pst-error-overlay'
    caja.style.cssText = 'position:fixed;left:0;right:0;bottom:0;max-height:45vh;overflow:auto;margin:0;padding:12px;background:#2a0000;color:#ffb3b3;font:11px monospace;z-index:99999;white-space:pre-wrap'
    document.body.appendChild(caja)
  }
  caja.textContent += texto + '\n\n'
}
window.addEventListener('error', e => mostrarError(`ERROR: ${e.message}\n${e.error?.stack || ''}`))
window.addEventListener('unhandledrejection', e => mostrarError(`PROMESA: ${e.reason?.message || e.reason}\n${e.reason?.stack || ''}`))

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
