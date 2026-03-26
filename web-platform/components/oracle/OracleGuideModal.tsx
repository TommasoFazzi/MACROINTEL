'use client';

import { useEffect } from 'react';
import { X } from 'lucide-react';

const INTENT_GUIDE = [
  {
    key: 'factual',
    label: 'Factual',
    labelColor: 'text-blue-300',
    bgColor: 'bg-blue-500/8 border-blue-500/15',
    desc: 'Searches recent events, declarations, and news. Uses strong time-decay weighting — recent data is prioritised.',
    example: 'What happened in Taiwan this week?',
  },
  {
    key: 'analytical',
    label: 'Analytical',
    labelColor: 'text-yellow-300',
    bgColor: 'bg-yellow-500/8 border-yellow-500/15',
    desc: 'Counts, groups, calculates trends. Queries the database directly with aggregations on articles and entities.',
    example: 'How many articles about China in the last 30 days?',
  },
  {
    key: 'narrative',
    label: 'Narrative',
    labelColor: 'text-purple-300',
    bgColor: 'bg-purple-500/8 border-purple-500/15',
    desc: 'Explores the storyline network, narrative evolution over time, and relationships between geopolitical themes.',
    example: 'How has the narrative on the Israeli conflict evolved?',
  },
  {
    key: 'market',
    label: 'Market',
    labelColor: 'text-green-300',
    bgColor: 'bg-green-500/8 border-green-500/15',
    desc: 'Analyses trading signals (BULLISH/BEARISH/WATCHLIST), tickers, macro indicators and geopolitical correlations.',
    example: 'Show me BUY signals on European defense',
  },
  {
    key: 'comparative',
    label: 'Comparative',
    labelColor: 'text-pink-300',
    bgColor: 'bg-pink-500/8 border-pink-500/15',
    desc: 'Compares entities, periods, or perspectives. Oracle automatically decomposes the query into parallel sub-queries.',
    example: 'Compare Russia vs China coverage over the last 60 days',
  },
  {
    key: 'overview',
    label: 'Overview',
    labelColor: 'text-teal-300',
    bgColor: 'bg-teal-500/8 border-teal-500/15',
    desc: 'Full geopolitical profile of a country or region. Uses all available history in the database (very slow decay).',
    example: 'Geopolitical overview of Iran',
  },
];

interface OracleGuideModalProps {
  open: boolean;
  onClose: () => void;
  onQuerySelect: (q: string) => void;
}

export function OracleGuideModal({ open, onClose, onQuerySelect }: OracleGuideModalProps) {
  // Close on ESC
  useEffect(() => {
    if (!open) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative bg-[#0a1628] border border-white/10 rounded-2xl w-full max-w-2xl max-h-[85vh] overflow-y-auto shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/10 sticky top-0 bg-[#0a1628] z-10">
          <div>
            <h2 className="text-white font-semibold text-base">Oracle Guide</h2>
            <p className="text-gray-500 text-xs mt-0.5">
              How to query the intelligence database
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-500 hover:text-white transition-colors p-1.5 rounded-lg hover:bg-white/5"
          >
            <X size={16} />
          </button>
        </div>

        <div className="px-6 py-5 space-y-7">
          {/* What is Oracle */}
          <div>
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2.5">
              What is Oracle
            </h3>
            <p className="text-gray-400 text-sm leading-relaxed">
              Oracle is an intelligence analysis engine that combines semantic vector search
              (RAG) with LLM synthesis over a proprietary database of geopolitical articles,
              intelligence reports, and market signals. It retains conversation memory to
              answer contextual follow-up questions.
            </p>
            <p className="text-gray-500 text-xs mt-2 leading-relaxed">
              Responses include clickable numbered citations{' '}
              <span className="inline-flex items-center justify-center w-4 h-4 rounded text-[10px] font-bold bg-[#FF6B35]/20 text-[#FF6B35] border border-[#FF6B35]/40 mx-0.5">1</span>{' '}
              that link directly to the source in the sidebar.
            </p>
          </div>

          {/* Intent types */}
          <div>
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
              Supported query types
            </h3>
            <div className="space-y-2.5">
              {INTENT_GUIDE.map((t) => (
                <div key={t.key} className={`p-3.5 rounded-xl border ${t.bgColor}`}>
                  <span className={`text-xs font-bold uppercase tracking-wide ${t.labelColor}`}>
                    {t.label}
                  </span>
                  <p className="text-gray-400 text-xs mt-1 leading-relaxed">{t.desc}</p>
                  <button
                    type="button"
                    onClick={() => {
                      onQuerySelect(t.example);
                      onClose();
                    }}
                    className="mt-2 text-xs text-gray-600 hover:text-white/80 italic transition-colors text-left"
                  >
                    E.g.: &ldquo;{t.example}&rdquo; →
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* Filters */}
          <div>
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2.5">
              Available filters
            </h3>
            <ul className="space-y-2 text-sm text-gray-400">
              <li>
                <span className="text-white/60">Date range</span> — limits the search to a
                specific period
              </li>
              <li>
                <span className="text-white/60">Country / GPE</span> — filter by geographic
                entity (e.g. &quot;Russia, Iran&quot;)
              </li>
              <li>
                <span className="text-white/60">Mode</span> —{' '}
                <em>both</em> (reports + articles), <em>factual</em> (articles only),{' '}
                <em>strategic</em> (reports only)
              </li>
              <li>
                <span className="text-white/60">Search type</span> —{' '}
                <em>hybrid</em> (vector + keyword), <em>vector</em>, <em>keyword</em>
              </li>
            </ul>
            <p className="text-gray-600 text-xs mt-2">
              Configure filters from the ⚙ Settings panel in the header.
            </p>
          </div>

          {/* Technical notes */}
          <div>
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2.5">
              Technical notes
            </h3>
            <ul className="space-y-2 text-sm text-gray-500">
              <li>⏱ Request timeout: 120 seconds (complex queries take 15–30s)</li>
              <li>
                🔑 Requires a valid Gemini API key — configure in{' '}
                <span className="text-white/50">Settings</span>
              </li>
              <li>📚 Database is updated every day at 08:00 UTC</li>
              <li>
                🔗 Citations in the text are clickable and link to the corresponding source in
                the sidebar
              </li>
              <li>🧠 Session memory persists throughout the current conversation</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
