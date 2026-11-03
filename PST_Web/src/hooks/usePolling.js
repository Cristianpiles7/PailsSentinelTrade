import { useEffect, useRef } from 'react'

// Ejecuta `fn` cada `ms` milisegundos, pero solo mientras la ventana esta visible.
// Al volver a verse hace una pasada inmediata en vez de esperar al siguiente tick.
export function usePollingWhenVisible(fn, ms, enabled = true) {
  const fnRef = useRef(fn)

  useEffect(() => {
    fnRef.current = fn
  }, [fn])

  useEffect(() => {
    if (!enabled) return

    let id = null
    const arrancar = () => {
      if (id === null) id = setInterval(() => fnRef.current(), ms)
    }
    const parar = () => {
      clearInterval(id)
      id = null
    }
    const alCambiarVisibilidad = () => {
      if (document.hidden) {
        parar()
      } else {
        fnRef.current()
        arrancar()
      }
    }

    if (!document.hidden) arrancar()
    document.addEventListener('visibilitychange', alCambiarVisibilidad)
    return () => {
      document.removeEventListener('visibilitychange', alCambiarVisibilidad)
      parar()
    }
  }, [ms, enabled])
}

// Para setState: devuelve el valor anterior si el nuevo tiene el mismo contenido,
// asi React no re-renderiza nada cuando un poll trae datos iguales.
export function conservarSiIgual(prev, next) {
  return JSON.stringify(prev) === JSON.stringify(next) ? prev : next
}
