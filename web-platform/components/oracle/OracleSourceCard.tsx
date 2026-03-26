'use client';

import { forwardRef } from 'react';
import type { OracleSource } from '../../types/oracle';

function getFreshnessInfo(dateStr?: string): { label: string; classes: string } | null {
  if (!dateStr) return null;
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return null;
  const days = Math.floor((Date.now() - date.getTime()) / (1000 * 60 * 60 * 24));
  if (days < 7) return { label: `${days}d`, classes: 'text-green-400 bg-green-500/10 border-green-500/30' };
  if (days < 30) return { label: `${days}d`, classes: 'text-yellow-400 bg-yellow-500/10 border-yellow-500/30' };
  return { label: `${days}d`, classes: 'text-red-400 bg-red-500/10 border-red-500/30' };
}

interface OracleSourceCardProps {
  source: OracleSource;
  index: number;
  highlighted?: boolean;
}

export const OracleSourceCard = forwardRef<HTMLDivElement, OracleSourceCardProps>(
  function OracleSourceCard({ source, index, highlighted = false }, ref) {
    const simPct = Math.round(source.similarity * 100);
    const freshness = getFreshnessInfo(source.date_str);

    return (
      <div
        ref={ref}
        className={`p-3 rounded-lg border mb-2 text-sm transition-all duration-200 ${
          highlighted
            ? 'border-[#FF6B35]/60 bg-[#FF6B35]/5 shadow-[0_0_0_1px_rgba(255,107,53,0.2)]'
            : 'border-white/10 bg-[#0d1d35]'
        }`}
      >
        {/* Header row */}
        <div className="flex items-center gap-2 mb-1.5">
          {/* Index badge */}
          <span className="text-xs w-5 h-5 rounded-full bg-white/10 flex items-center justify-center font-mono text-gray-400 flex-shrink-0 text-[10px]">
            {index}
          </span>
          <span
            className={`text-xs px-1.5 py-0.5 rounded font-medium ${
              source.type === 'REPORT'
                ? 'bg-[#FF6B35]/20 text-[#FF6B35]'
                : 'bg-[#00A8E8]/20 text-[#00A8E8]'
            }`}
          >
            {source.type === 'REPORT' ? 'REPORT' : 'ARTICLE'}
          </span>
          {freshness && (
            <span className={`text-xs px-1.5 py-0.5 rounded border ${freshness.classes}`}>
              {freshness.label}
            </span>
          )}
        </div>

        {/* Title */}
        <div className="text-white/90 font-medium text-sm leading-snug mb-1.5">
          {source.link ? (
            <a
              href={source.link}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-[#00A8E8] transition-colors"
            >
              {source.title}
            </a>
          ) : (
            source.title
          )}
        </div>

        {/* Meta row */}
        {(source.source || source.date_str) && (
          <div className="flex items-center gap-2 text-xs text-gray-500 mb-2">
            {source.source && (
              <span className="truncate max-w-[130px]">{source.source}</span>
            )}
            {source.source && source.date_str && <span>·</span>}
            {source.date_str && <span>{source.date_str}</span>}
          </div>
        )}

        {/* Similarity bar */}
        <div className="flex items-center gap-2">
          <div className="flex-1 h-1 rounded-full bg-white/10 overflow-hidden">
            <div
              className="h-full rounded-full bg-[#00A8E8]/50 transition-all"
              style={{ width: `${simPct}%` }}
            />
          </div>
          <span className="text-xs text-gray-600 w-9 text-right">{simPct}%</span>
        </div>

        {/* Preview */}
        {source.preview && (
          <div className="text-gray-500 text-xs mt-2 line-clamp-2 leading-relaxed">
            {source.preview}
          </div>
        )}
      </div>
    );
  }
);
