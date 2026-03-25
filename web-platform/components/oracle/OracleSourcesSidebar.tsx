'use client';

import { useRef, useEffect } from 'react';
import { OracleSourceCard } from './OracleSourceCard';
import type { OracleChatMessage } from '../../types/oracle';

interface OracleSourcesSidebarProps {
  message: OracleChatMessage | undefined;
  highlightedSource: number | null;
  isVisible: boolean;
}

export function OracleSourcesSidebar({
  message,
  highlightedSource,
  isVisible,
}: OracleSourcesSidebarProps) {
  const cardRefs = useRef<(HTMLDivElement | null)[]>([]);

  // Scroll to highlighted source when citation is clicked
  useEffect(() => {
    if (highlightedSource === null) return;
    const el = cardRefs.current[highlightedSource - 1];
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [highlightedSource]);

  if (!isVisible) return null;

  const sources = message?.sources ?? [];

  return (
    <div className="w-72 border-l border-white/10 flex-col overflow-hidden hidden md:flex flex-shrink-0">
      <div className="px-4 py-3 border-b border-white/10 flex items-center justify-between">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Fonti</h3>
        {sources.length > 0 && (
          <span className="text-xs text-gray-600">{sources.length} risultati</span>
        )}
      </div>
      <div className="flex-1 overflow-y-auto px-3 py-3">
        {sources.length > 0 ? (
          sources.map((src, i) => (
            <OracleSourceCard
              key={i}
              ref={(el) => { cardRefs.current[i] = el; }}
              source={src}
              index={i + 1}
              highlighted={highlightedSource === i + 1}
            />
          ))
        ) : (
          <div className="text-center text-gray-600 text-xs pt-8 leading-relaxed px-2">
            Le fonti e le citazioni compariranno dopo la prima risposta.
          </div>
        )}
      </div>
    </div>
  );
}
