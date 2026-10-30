// Calculos de la pantalla de rendimiento. Puros: reciben operaciones de /api/performance/operaciones
// (PST_Core/utils/rendimiento.py) y devuelven agregados. Sin React, para poder probarlos con Node.

// Por debajo de esta cantidad, la media de un simbolo es ruido (mismo criterio que PST_LLM/informe_operaciones.py).
export const MINIMO_FIABLE = 20

export const ORDEN_CLASES = ['Índices', 'Acciones', 'Cripto', 'Materias', 'Forex', 'Otros']

// "2026.06.15 09:32:11" o "2026-06-15 09:32:11" -> Date. Las horas son hora local del broker.
export function instante(s) {
  if (!s) return null
  const t = new Date(`${s.slice(0, 10).replace(/\./g, '-')}T${s.slice(11)}`)
  return Number.isNaN(t.getTime()) ? null : t
}

const momento = o => instante(o.cerrada || o.abierta)
const esLLM = o => o.fuente === 'LLM'
const suma = (xs, f = x => x) => xs.reduce((acc, x) => acc + f(x), 0)
const media = xs => (xs.length ? suma(xs) / xs.length : null)

// Filtros de la barra superior. dias null = todo el historial. fuente: 'todas' | 'llm' | 'resto'.
export function filtrar(ops, { dias = null, fuente = 'todas', clase = null } = {}) {
  const corte = dias ? Date.now() - dias * 86400000 : null
  return ops.filter(o => {
    if (corte) {
      const t = momento(o)
      if (!t || t.getTime() < corte) return false
    }
    if (fuente === 'llm' && !esLLM(o)) return false
    if (fuente === 'resto' && esLLM(o)) return false
    if (clase && o.clase !== clase) return false
    return true
  })
}

// Orden cronologico ascendente (cerrada, o apertura si no hay cierre).
function cronologico(ops) {
  return [...ops].sort((a, b) => (momento(a)?.getTime() ?? 0) - (momento(b)?.getTime() ?? 0))
}

// Resumen de un conjunto de operaciones. null si esta vacio.
// perdidaMax: mayor caida del neto acumulado desde un maximo (EUR, negativo o 0).
export function resumen(ops) {
  const n = ops.length
  if (!n) return null
  let acum = 0, pico = 0, peor = 0, racha = 0, rachaMax = 0
  for (const o of cronologico(ops)) {
    acum += o.neto
    pico = Math.max(pico, acum)
    peor = Math.min(peor, acum - pico)
    racha = o.neto <= 0 ? racha + 1 : 0
    rachaMax = Math.max(rachaMax, racha)
  }
  const ganado = suma(ops.filter(o => o.neto > 0), o => o.neto)
  const perdido = suma(ops.filter(o => o.neto < 0), o => -o.neto)
  const conR = ops.filter(o => o.r != null)
  const conMin = ops.filter(o => o.minutos != null)
  return {
    n,
    aciertos: (ops.filter(o => o.neto > 0).length / n) * 100,
    neto: acum,
    netoMedio: acum / n,
    profitFactor: perdido > 0 ? ganado / perdido : (ganado > 0 ? Infinity : 0),
    rMedio: media(conR.map(o => o.r)),
    nR: conR.length,
    perdidaMax: peor,
    rachaPerdidas: rachaMax,
    coste: suma(ops, o => o.coste ?? 0),
    minutos: media(conMin.map(o => o.minutos)),
  }
}

// Agrupa por una clave (symbol, clase, fuente...) y resume cada grupo. Ordena por neto, de mayor a menor.
export function agrupar(ops, clave) {
  const grupos = new Map()
  for (const o of ops) {
    const k = o[clave] ?? '—'
    if (!grupos.has(k)) grupos.set(k, [])
    grupos.get(k).push(o)
  }
  return [...grupos.entries()]
    .map(([k, g]) => ({ clave: k, clase: g[0].clase, ops: g, ...resumen(g) }))
    .sort((a, b) => b.neto - a.neto)
}

// Serie para la curva de capital: P&L acumulado y caida desde el maximo, en orden cronologico.
export function curva(ops) {
  let acum = 0, pico = 0
  return cronologico(ops).map(o => {
    acum += o.neto
    pico = Math.max(pico, acum)
    const t = momento(o)
    return {
      t: t ? `${String(t.getDate()).padStart(2, '0')}/${String(t.getMonth() + 1).padStart(2, '0')} ${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}` : '',
      pnl: Math.round(acum * 100) / 100,
      dd: Math.round((acum - pico) * 100) / 100,
    }
  })
}

// 24 cubos por hora del dia, mismo formato que /api/performance/hourly (lo consume HourlyHeatmap).
export function porHora(ops) {
  const cubos = Array.from({ length: 24 }, (_, h) => ({ hour: h, pnl: 0, trades: 0, wins: 0 }))
  for (const o of ops) {
    const t = momento(o)
    if (!t) continue
    const c = cubos[t.getHours()]
    c.pnl += o.neto
    c.trades += 1
    if (o.neto > 0) c.wins += 1
  }
  return cubos.map(c => ({
    hour: c.hour,
    pnl: Math.round(c.pnl * 100) / 100,
    trades: c.trades,
    win_rate: c.trades ? Math.round((c.wins / c.trades) * 1000) / 10 : 0,
  }))
}

// Mejores y peores simbolos por neto medio por operacion (funciona igual para agentes y EAs).
// Solo entran simbolos con muestra suficiente: por debajo no hay ranking que valga.
export function destacados(filas, k = 3) {
  const fiables = filas.filter(f => f.n >= MINIMO_FIABLE)
  const orden = [...fiables].sort((a, b) => b.netoMedio - a.netoMedio)
  const resto = orden.slice(k)                    // sin solape con mejores
  return {
    mejores: orden.slice(0, k),
    peores: resto.slice(-k).reverse(),
    fiables: fiables.length,
  }
}
