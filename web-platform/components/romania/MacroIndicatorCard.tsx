'use client';

interface SeriesPoint {
  date: string;
  value: number;
}

interface MacroIndicatorCardProps {
  label: string;
  unit: string;
  category?: string;
  latest: { date: string; value: number } | null;
  series: SeriesPoint[];
  expectedFrequency?: string;
  isStale?: boolean;
  stalenessDays?: number | null;
}

function Sparkline({ series, unit }: { series: SeriesPoint[]; unit: string }) {
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
  // For spread/CDS/deficit: going up is bad (red). For BET/EUR_RON depends on context.
  // Use a neutral color rule: up = green for equity/fx, red for risk/fiscal
  const riskUnit = unit === 'bps' || unit === '% of GDP';
  const trending = last >= first;
  const lineColor = riskUnit
    ? trending ? '#ef4444' : '#22c55e'
    : trending ? '#22c55e' : '#ef4444';

  return (
    <svg width={W} height={H} className="overflow-visible">
      <polyline
        points={pts}
        fill="none"
        stroke={lineColor}
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

const CATEGORY_STYLES: Record<string, { dot: string; label: string }> = {
  RATES:     { dot: 'bg-blue-400',   label: 'Tassi' },
  FX:        { dot: 'bg-teal-400',   label: 'Cambio' },
  INFLATION: { dot: 'bg-orange-400', label: 'Inflazione' },
  RISK:      { dot: 'bg-red-400',    label: 'Rischio' },
  FISCAL:    { dot: 'bg-purple-400', label: 'Fiscale' },
  EQUITY:    { dot: 'bg-green-400',  label: 'Borsa' },
};

function fmtValue(v: number | undefined, unit: string): string {
  if (v == null) return 'n/d';
  if (unit === '%') return `${v.toFixed(2)}%`;
  if (unit === '% of GDP') return `${v.toFixed(1)}%`;
  if (unit === 'bps') return `${Math.round(v)} bps`;
  if (unit === 'points') return v.toLocaleString('it-IT', { maximumFractionDigits: 0 });
  if (unit === 'Rate') return v.toFixed(4);
  return v.toFixed(2);
}

function fmtDelta(delta: number, unit: string): string {
  if (unit === '%' || unit === '% of GDP') {
    const sign = delta >= 0 ? '+' : '';
    return `${sign}${delta.toFixed(2)}pp`;
  }
  if (unit === 'bps') {
    const sign = delta >= 0 ? '+' : '';
    return `${sign}${Math.round(delta)}bp`;
  }
  if (unit === 'points') {
    const sign = delta >= 0 ? '+' : '';
    return `${sign}${Math.round(delta)}`;
  }
  const sign = delta >= 0 ? '+' : '';
  return `${sign}${delta.toFixed(4)}`;
}

export function MacroIndicatorCard({
  label,
  unit,
  category,
  latest,
  series,
  expectedFrequency,
  isStale,
  stalenessDays,
}: MacroIndicatorCardProps) {
  const value = latest?.value;
  const prev = series.length > 1 ? series[1].value : null;
  const delta = value != null && prev != null ? value - prev : null;

  const freqLabel = expectedFrequency ? FREQ_LABELS[expectedFrequency] ?? expectedFrequency : null;

  const staleBadge =
    isStale && stalenessDays != null
      ? `${stalenessDays}g fa`
      : isStale
      ? 'stale'
      : null;

  const dataDate = latest?.date
    ? new Date(latest.date).toLocaleDateString('it-IT', { day: '2-digit', month: 'short', year: 'numeric' })
    : null;

  const catStyle = category ? CATEGORY_STYLES[category] : null;

  // For risk indicators (bps/CDS/spread), delta going up is bad → red
  const riskUnit = unit === 'bps' || unit === '% of GDP';
  const deltaPositive = delta != null && delta >= 0;
  const deltaColor = delta == null
    ? ''
    : riskUnit
    ? (deltaPositive ? 'text-red-400' : 'text-green-400')
    : (deltaPositive ? 'text-green-400' : 'text-red-400');

  return (
    <div
      className={`bg-[#1a2332]/70 border rounded-xl p-4 flex flex-col gap-2 transition-colors ${
        isStale
          ? 'border-amber-500/30 hover:border-amber-400/50'
          : 'border-white/8 hover:border-blue-400/30'
      }`}
    >
      {/* Header: label + category dot + freq + stale */}
      <div className="flex items-start justify-between gap-1">
        <div className="flex items-center gap-1.5 min-w-0">
          {catStyle && (
            <span className={`w-1.5 h-1.5 rounded-full shrink-0 mt-0.5 ${catStyle.dot}`} />
          )}
          <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider leading-tight truncate">
            {label}
          </span>
        </div>
        <div className="flex flex-col items-end gap-0.5 shrink-0 ml-1">
          {catStyle && (
            <span className="text-[9px] font-medium text-gray-600 uppercase tracking-wide">
              {catStyle.label}
            </span>
          )}
          {freqLabel && !catStyle && (
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
      <div className="flex items-end gap-2">
        <span className={`text-2xl font-bold leading-none ${isStale ? 'text-gray-300' : 'text-white'}`}>
          {fmtValue(value, unit)}
        </span>
        {delta != null && (
          <span className={`text-xs font-medium mb-0.5 ${deltaColor}`}>
            {fmtDelta(delta, unit)}
          </span>
        )}
      </div>

      {/* Sparkline */}
      <Sparkline series={series.slice(0, 30)} unit={unit} />

      {/* Data date + frequency */}
      <div className="flex items-center justify-between">
        {dataDate && (
          <span className={`text-[10px] ${isStale ? 'text-amber-600' : 'text-gray-600'}`}>
            {dataDate}
          </span>
        )}
        {freqLabel && (
          <span className="text-[9px] text-gray-700 uppercase tracking-wide">{freqLabel}</span>
        )}
      </div>
    </div>
  );
}
