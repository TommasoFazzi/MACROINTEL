'use client';

import useSWR from 'swr';
import { MacroIndicatorCard } from './MacroIndicatorCard';

interface Indicator {
  key: string;
  label: string;
  unit: string;
  latest: { date: string; value: number } | null;
  series: { date: string; value: number }[];
  expected_frequency?: string;
  is_stale?: boolean;
  staleness_days?: number | null;
}

const fetcher = (url: string) => fetch(url).then((r) => r.json());

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="bg-[#1a2332]/50 border border-white/5 rounded-xl p-4 animate-pulse h-28" />
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

  // Network / server error
  if (error) {
    return (
      <p className="text-red-400/70 text-sm">
        Errore di connessione al backend. Verifica che il server sia attivo.
      </p>
    );
  }

  // Response OK but no indicators at all (pipeline mai eseguita o migration non applicata)
  if (!data?.indicators?.length) {
    return (
      <p className="text-gray-500 text-sm">
        Nessun dato macro disponibile. Esegui{' '}
        <code className="text-gray-400 font-mono text-xs">fetch_romania_macro.py</code> o il pipeline giornaliero per popolare i dati.
      </p>
    );
  }

  // Indicators present but all latest values are null (fetch eseguito, DB vuoto)
  const allEmpty = data.indicators.every((ind) => ind.latest === null);
  if (allEmpty) {
    return (
      <p className="text-amber-500/70 text-sm">
        Struttura dati presente ma nessun valore ricevuto. La pipeline macro potrebbe non aver completato correttamente.
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
          expectedFrequency={ind.expected_frequency}
          isStale={ind.is_stale}
          stalenessDays={ind.staleness_days}
        />
      ))}
    </div>
  );
}
