'use client';

import useSWR from 'swr';
import { MacroIndicatorCard } from './MacroIndicatorCard';

interface Indicator {
  key: string;
  label: string;
  unit: string;
  category?: string;
  latest: { date: string; value: number } | null;
  series: { date: string; value: number }[];
  expected_frequency?: string;
  is_stale?: boolean;
  staleness_days?: number | null;
}

const fetcher = (url: string) => fetch(url).then((r) => r.json());

// Display order: FX → RATES → INFLATION → RISK → FISCAL → EQUITY
const CATEGORY_ORDER = ['FX', 'RATES', 'INFLATION', 'RISK', 'FISCAL', 'EQUITY'];
const KEY_ORDER = [
  'EUR_RON', 'BNR_RATE', 'ROBOR_3M',
  'RO_CPI_YOY', 'RO_10Y_YIELD', 'RO_10Y_DE_SPREAD',
  'RO_DEFICIT_GDP', 'BET_INDEX',
];

function sortIndicators(indicators: Indicator[]): Indicator[] {
  return [...indicators].sort((a, b) => {
    const ai = KEY_ORDER.indexOf(a.key);
    const bi = KEY_ORDER.indexOf(b.key);
    if (ai !== -1 && bi !== -1) return ai - bi;
    if (ai !== -1) return -1;
    if (bi !== -1) return 1;
    const ca = CATEGORY_ORDER.indexOf(a.category ?? '');
    const cb = CATEGORY_ORDER.indexOf(b.category ?? '');
    return (ca === -1 ? 99 : ca) - (cb === -1 ? 99 : cb);
  });
}

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="bg-[#1a2332]/50 border border-white/5 rounded-xl p-4 animate-pulse h-[120px]" />
      ))}
    </div>
  );
}

export function MacroMiniDashboard() {
  const { data, error, isLoading } = useSWR<{ indicators: Indicator[] }>(
    '/api/proxy/romania/macro',
    fetcher,
    { refreshInterval: 300_000 }
  );

  if (isLoading) return <SkeletonGrid />;

  if (error) {
    return (
      <p className="text-red-400/70 text-sm">
        Errore di connessione al backend. Verifica che il server sia attivo.
      </p>
    );
  }

  if (!data?.indicators?.length) {
    return (
      <p className="text-gray-500 text-sm">
        Nessun dato macro disponibile. Esegui{' '}
        <code className="text-gray-400 font-mono text-xs">fetch_romania_macro.py</code> o il pipeline giornaliero.
      </p>
    );
  }

  const allEmpty = data.indicators.every((ind) => ind.latest === null);
  if (allEmpty) {
    return (
      <p className="text-amber-500/70 text-sm">
        Struttura dati presente ma nessun valore ricevuto. La pipeline macro potrebbe non aver completato.
      </p>
    );
  }

  const sorted = sortIndicators(data.indicators);

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
      {sorted.map((ind) => (
        <MacroIndicatorCard
          key={ind.key}
          label={ind.label}
          unit={ind.unit}
          category={ind.category}
          latest={ind.latest}
          series={ind.series}
          expectedFrequency={ind.expected_frequency}
          isStale={ind.is_stale}
          stalenessDays={ind.staleness_days}
        />
      ))}
    </div>
  );
}
