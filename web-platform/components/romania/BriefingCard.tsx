'use client';

import Link from 'next/link';
import { Calendar, ArrowRight } from 'lucide-react';

export interface Briefing {
  id: number;
  date: string;
  report_type: string;
  status: string;
  excerpt: string;
  metadata: Record<string, unknown>;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('it-IT', {
    day: '2-digit',
    month: 'long',
    year: 'numeric',
  });
}

export function BriefingCard({ briefing }: { briefing: Briefing }) {
  const isDaily = briefing.report_type === 'romania-daily';
  const typeLabel = isDaily ? 'Giornaliero' : 'Settimanale';
  const typeColor = isDaily ? 'text-blue-400 border-blue-400/20 bg-blue-400/10' : 'text-purple-400 border-purple-400/20 bg-purple-400/10';

  return (
    <article className="group relative p-5 rounded-xl border border-white/8 bg-[#1a2332]/60 hover:border-blue-400/30 hover:bg-[#1a2332]/90 transition-all duration-300">
      <div className="absolute inset-x-0 top-0 h-[1px] rounded-t-xl bg-gradient-to-r from-transparent via-blue-400/50 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />

      <div className="flex items-center gap-3 mb-3 flex-wrap">
        <span className={`text-xs font-medium px-2.5 py-1 rounded-full border ${typeColor}`}>
          {typeLabel}
        </span>
        <span className="flex items-center gap-1 text-xs text-gray-500">
          <Calendar className="w-3 h-3" />
          <time dateTime={briefing.date}>{formatDate(briefing.date)}</time>
        </span>
      </div>

      <p className="text-gray-300 text-sm leading-relaxed line-clamp-3 mb-4">
        {briefing.excerpt || 'Nessuna anteprima disponibile.'}
      </p>

      <Link
        href={`/romania/${briefing.id}`}
        className="inline-flex items-center gap-1 text-sm text-blue-400 hover:text-blue-300 transition-colors font-medium"
      >
        Leggi briefing
        <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
      </Link>
    </article>
  );
}
