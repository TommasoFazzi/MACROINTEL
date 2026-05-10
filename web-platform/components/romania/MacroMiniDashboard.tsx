'use client';

import useSWR from 'swr';
import { MacroIndicatorCard } from './MacroIndicatorCard';

interface Indicator {
  key: string;
  label: string;
  unit: string;
  latest: { date: string; value: number } | null;
  series: { date: string; value: number }[];
}

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export function MacroMiniDashboard() {
  const { data, error, isLoading } = useSWR<{ indicators: Indicator[] }>(
    '/api/proxy/romania/macro',
    fetcher,
    { refreshInterval: 300_000 }
  );

  if (isLoading) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="bg-[#1a2332]/50 border border-white/5 rounded-xl p-4 animate-pulse h-24" />
        ))}
      </div>
    );
  }

  if (error || !data?.indicators?.length) {
    return (
      <p className="text-gray-500 text-sm">
        Indicatori macro non disponibili. Avvia la pipeline per popolare i dati.
      </p>
    );
  }

  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
      {data.indicators.map((ind) => (
        <MacroIndicatorCard
          key={ind.key}
          label={ind.label}
          unit={ind.unit}
          latest={ind.latest}
          series={ind.series}
        />
      ))}
    </div>
  );
}
