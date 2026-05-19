'use client';

import { useRef, useEffect } from 'react';
import type { OracleSource } from '../../types/oracle';

interface OracleSourcesSidebarProps {
  sources: OracleSource[];
  highlightedSource: number | null;
  isVisible: boolean;
  /** When true, renders only the source list without the sidebar wrapper (for mobile bottom sheet). */
  embedded?: boolean;
}

export function OracleSourcesSidebar({
  sources,
  highlightedSource,
  isVisible,
  embedded = false,
}: OracleSourcesSidebarProps) {
  const highlightRef = useRef<HTMLLIElement>(null);

  useEffect(() => {
    if (highlightedSource !== null && highlightRef.current) {
      highlightRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [highlightedSource]);

  if (!isVisible) return null;

  const listContent = (
    <ul className="space-y-2">
      {sources.map((source, idx) => {
        const isHighlighted = highlightedSource === idx + 1;

        return (
          <li
            key={`${source.type}-${source.id}-${idx}`}
            ref={isHighlighted ? highlightRef : null}
            className={`p-2.5 rounded-lg border text-xs transition-all duration-200 ${
              isHighlighted
                ? 'border-[#00A8E8]/50 bg-[#00A8E8]/10 ring-1 ring-[#00A8E8]/30'
                : 'border-white/5 bg-white/[0.01] hover:border-white/10'
            }`}
          >
            <div className="flex items-center gap-1.5 mb-1">
              <span className="text-xs font-mono text-gray-600">[{idx + 1}]</span>
              <span className={`text-[10px] px-1 rounded font-medium ${
                source.type === 'REPORT'
                  ? 'bg-purple-500/15 text-purple-400'
                  : 'bg-blue-500/15 text-blue-400'
              }`}>
                {source.type === 'REPORT' ? 'Report' : 'Article'}
              </span>
              {source.date_str && (
                <span className="text-[10px] text-gray-600 ml-auto">{source.date_str}</span>
              )}
            </div>
            {source.source && (
              <p className="text-[10px] text-gray-500 mb-1 font-medium">{source.source}</p>
            )}
            <p className="text-gray-300 line-clamp-2 leading-snug">{source.title}</p>
            <div className="flex items-center justify-between mt-1.5">
              {source.link ? (
                <a
                  href={source.link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[#00A8E8] hover:underline"
                >
                  Open source &rarr;
                </a>
              ) : (
                <span />
              )}
              {source.similarity != null && (
                <span className="text-gray-600 font-mono">
                  {Math.round(source.similarity * 100)}%
                </span>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );

  // Embedded mode: render just the list (for mobile bottom sheet, no outer sidebar wrapper)
  if (embedded) {
    return (
      <div className="px-4 py-3">
        {sources.length === 0 ? (
          <div className="text-center text-gray-600 text-xs pt-8">
            Sources will appear after the first response.
          </div>
        ) : listContent}
      </div>
    );
  }

  return (
    <div className="w-72 border-l border-white/10 flex-col overflow-hidden hidden md:flex flex-shrink-0">
      <div className="px-4 py-3 border-b border-white/10 flex items-center justify-between">
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-widest">
          Sources
        </h3>
        {sources.length > 0 && (
          <span className="text-xs text-gray-600">{sources.length} total</span>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3 scrollbar-thin scrollbar-thumb-white/10">
        {sources.length === 0 ? (
          <div className="text-center text-gray-600 text-xs pt-8 leading-relaxed px-2">
            Sources and citations will appear after the first response.
          </div>
        ) : listContent}
      </div>
    </div>
  );
}
