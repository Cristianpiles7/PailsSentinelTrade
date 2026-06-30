import React from 'react'

export function EquityCurve({ data }) {
  if (!data || data.length < 2) return (
    <div className="h-full flex items-center justify-center text-zinc-700 font-black uppercase text-[10px] italic">
      Insufficient Data for Curve Analysis
    </div>
  );

  const padding = 20;
  const width = 800;
  const height = 200;

  const balances = data.map(d => d.balance);
  const min = Math.min(...balances);
  const max = Math.max(...balances);
  const range = max - min || 1;

  const points = data.map((d, i) => {
    const x = padding + (i * (width - 2 * padding)) / (data.length - 1);
    const y = height - padding - ((d.balance - min) * (height - 2 * padding)) / range;
    return `${x},${y}`;
  }).join(' ');

  return (
    <div className="w-full h-full p-4">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-full drop-shadow-[0_0_15px_rgba(99,102,241,0.3)]">
        <defs>
          <linearGradient id="curveGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#4f46e5" stopOpacity="0.5" />
            <stop offset="100%" stopColor="#4f46e5" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path
          d={`M ${points} L ${width - padding},${height} L ${padding},${height} Z`}
          fill="url(#curveGradient)"
        />
        <polyline
          fill="none"
          stroke="#6366f1"
          strokeWidth="3"
          strokeLinecap="round"
          strokeLinejoin="round"
          points={points}
        />
        {/* Puntos de control visual */}
        {data.map((d, i) => {
          if (i % Math.ceil(data.length / 10) !== 0 && i !== data.length - 1) return null;
          const x = padding + (i * (width - 2 * padding)) / (data.length - 1);
          const y = height - padding - ((d.balance - min) * (height - 2 * padding)) / range;
          return (
            <g key={i}>
              <circle cx={x} cy={y} r="3" fill="#6366f1" className="animate-pulse" />
              <text x={x} y={y - 8} fontSize="8" fill="#64748b" fontWeight="black" textAnchor="middle">{d.balance.toFixed(0)}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
