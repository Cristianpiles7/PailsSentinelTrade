import React from 'react'

export function Sparkline({ data, color }) {
  if (!data || data.length < 2) return null;

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const width = 100;
  const height = 40;

  const points = data.map((val, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - ((val - min) / range) * height;
    return `${x},${y}`;
  }).join(' ');

  const strokeColor = color === 'emerald' ? '#10b981' : (color === 'rose' ? '#f43f5e' : (color === 'amber' ? '#f59e0b' : '#6366f1'));

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-full overflow-visible">
      <defs>
        <linearGradient id={`grad-${color}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={strokeColor} stopOpacity="0.2" />
          <stop offset="100%" stopColor={strokeColor} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polyline
        fill="none"
        stroke={strokeColor}
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        points={points}
        className="drop-shadow-[0_0_8px_rgba(0,0,0,0.5)]"
      />
      <polygon
        points={`0,${height} ${points} ${width},${height}`}
        fill={`url(#grad-${color})`}
      />
    </svg>
  );
}
