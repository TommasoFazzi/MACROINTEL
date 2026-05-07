'use client';

interface SeriesPoint {
  date: string;
  value: number;
}

interface MacroIndicatorCardProps {
  label: string;
  unit: string;
  latest: { date: string; value: number } | null;
  series: SeriesPoint[];
}

function Sparkline({ series }: { series: SeriesPoint[] }) {
  if (series.length < 2) return null;

  const values = series.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const W = 80;
  const H = 28;
  const pts = series
    .slice()
    .reverse()
    .map((p, i) => {
      const x = (i / (series.length - 1)) * W;
      const y = H - ((p.value - min) / range) * H;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  const last = series[0].value;
  const first = series[series.length - 1].value;
  const trending = last >= first;

  return (
    <svg width={W} height={H} className="overflow-visible">
      <polyline
        points={pts}
        fill="none"
        stroke={trending ? '#22c55e' : '#ef4444'}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function MacroIndicatorCard({ label, unit, latest, series }: MacroIndicatorCardProps) {
  const value = latest?.value;
  const prev = series.length > 1 ? series[1].value : null;
  const delta = value != null && prev != null ? value - prev : null;

  function fmtValue(v: number | undefined): string {
    if (v == null) return 'n/d';
    if (unit === 'Rate' || unit === '%' || unit.includes('%')) return `${v.toFixed(2)}%`;
    return v.toFixed(4);
  }

  return (
    <div className="bg-[#1a2332]/70 border border-white/8 rounded-xl p-4 flex flex-col gap-2 hover:border-blue-400/30 transition-colors">
      <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider">{label}</div>
      <div className="flex items-end gap-3">
        <span className="text-2xl font-bold text-white">{fmtValue(value)}</span>
        {delta != null && (
          <span className={`text-xs font-medium mb-1 ${delta >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {delta >= 0 ? '+' : ''}{delta.toFixed(2)}
          </span>
        )}
      </div>
      <Sparkline series={series.slice(0, 30)} />
      {latest?.date && (
        <div className="text-[10px] text-gray-600">
          {new Date(latest.date).toLocaleDateString('it-IT', { day: '2-digit', month: 'short', year: 'numeric' })}
        </div>
      )}
    </div>
  );
}
