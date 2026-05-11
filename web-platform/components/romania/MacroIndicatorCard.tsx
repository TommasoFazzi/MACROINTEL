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
  expectedFrequency?: string;
  isStale?: boolean;
  stalenessDays?: number | null;
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

const FREQ_LABELS: Record<string, string> = {
  daily: 'giornaliero',
  '24_7': 'giornaliero',
  weekly: 'settimanale',
  monthly: 'mensile',
  quarterly: 'trimestrale',
  annual: 'annuale',
};

export function MacroIndicatorCard({
  label,
  unit,
  latest,
  series,
  expectedFrequency,
  isStale,
  stalenessDays,
}: MacroIndicatorCardProps) {
  const value = latest?.value;
  // For non-daily data the series can span months — delta vs previous point is still meaningful
  // but we show the period label so the user knows the comparison horizon.
  const prev = series.length > 1 ? series[1].value : null;
  const delta = value != null && prev != null ? value - prev : null;

  function fmtValue(v: number | undefined): string {
    if (v == null) return 'n/d';
    if (unit === 'Rate' || unit === '%' || unit.includes('%')) return `${v.toFixed(2)}%`;
    return v.toFixed(4);
  }

  const freqLabel = expectedFrequency ? FREQ_LABELS[expectedFrequency] ?? expectedFrequency : null;

  // Staleness display: show days-ago if stale and we have the count
  const staleBadge =
    isStale && stalenessDays != null
      ? `${stalenessDays}g fa`
      : isStale
      ? 'stale'
      : null;

  // Date shown for the latest data point
  const dataDate = latest?.date
    ? new Date(latest.date).toLocaleDateString('it-IT', { day: '2-digit', month: 'short', year: 'numeric' })
    : null;

  return (
    <div
      className={`bg-[#1a2332]/70 border rounded-xl p-4 flex flex-col gap-2 transition-colors ${
        isStale
          ? 'border-amber-500/30 hover:border-amber-400/50'
          : 'border-white/8 hover:border-blue-400/30'
      }`}
    >
      {/* Header row: label + frequency + staleness */}
      <div className="flex items-start justify-between gap-1">
        <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider leading-tight">
          {label}
        </span>
        <div className="flex flex-col items-end gap-0.5 shrink-0">
          {freqLabel && (
            <span className="text-[9px] font-medium text-gray-600 uppercase tracking-wide">
              {freqLabel}
            </span>
          )}
          {staleBadge && (
            <span className="text-[9px] font-semibold text-amber-500 uppercase tracking-wide">
              {staleBadge}
            </span>
          )}
        </div>
      </div>

      {/* Value + delta */}
      <div className="flex items-end gap-3">
        <span className={`text-2xl font-bold ${isStale ? 'text-gray-300' : 'text-white'}`}>
          {fmtValue(value)}
        </span>
        {delta != null && (
          <span className={`text-xs font-medium mb-1 ${delta >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {delta >= 0 ? '+' : ''}{delta.toFixed(3)}
          </span>
        )}
      </div>

      {/* Sparkline */}
      <Sparkline series={series.slice(0, 30)} />

      {/* Data date */}
      {dataDate && (
        <div className={`text-[10px] ${isStale ? 'text-amber-600' : 'text-gray-600'}`}>
          dato: {dataDate}
        </div>
      )}
    </div>
  );
}
