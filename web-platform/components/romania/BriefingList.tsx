'use client';

import { useState } from 'react';
import useSWR from 'swr';
import { BriefingCard, type Briefing } from './BriefingCard';

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export function BriefingList() {
  const [tab, setTab] = useState<'daily' | 'weekly'>('daily');
  const apiBase = process.env.NEXT_PUBLIC_API_URL || '';

  const { data, isLoading } = useSWR<{ briefings: Briefing[] }>(
    `${apiBase}/api/v1/romania/briefings?type=${tab}&limit=20`,
    fetcher,
    { refreshInterval: 300_000 }
  );

  const tabs = [
    { key: 'daily' as const, label: 'Giornalieri' },
    { key: 'weekly' as const, label: 'Settimanali' },
  ];

  return (
    <div>
      {/* Tab bar */}
      <div className="flex gap-2 mb-6">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              tab === t.key
                ? 'bg-blue-600 text-white'
                : 'bg-[#1a2332]/60 text-gray-400 hover:text-white border border-white/8'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="space-y-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="bg-[#1a2332]/50 border border-white/5 rounded-xl p-5 animate-pulse h-28" />
          ))}
        </div>
      ) : !data?.briefings?.length ? (
        <div className="text-center py-16">
          <p className="text-gray-500">Nessun briefing disponibile.</p>
          <p className="text-gray-600 text-sm mt-2">
            Avvia la pipeline: <code className="text-blue-400">python scripts/generate_report.py --report-type romania-{tab}</code>
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {data.briefings.map((b) => (
            <BriefingCard key={b.id} briefing={b} />
          ))}
        </div>
      )}
    </div>
  );
}
