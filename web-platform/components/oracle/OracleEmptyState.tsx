'use client';

const INTENT_TYPES = [
  {
    key: 'factual',
    label: 'Factual',
    icon: '📰',
    desc: 'Recent events, news, declarations',
    borderColor: 'border-blue-500/30 hover:border-blue-500/50',
    example: 'What happened in Ukraine in the last 7 days?',
  },
  {
    key: 'analytical',
    label: 'Analytical',
    icon: '📊',
    desc: 'Trends, statistics, counts',
    borderColor: 'border-yellow-500/30 hover:border-yellow-500/50',
    example: 'How many articles about China in the last 30 days?',
  },
  {
    key: 'narrative',
    label: 'Narrative',
    icon: '🕸',
    desc: 'Storyline evolution and narrative networks',
    borderColor: 'border-purple-500/30 hover:border-purple-500/50',
    example: 'How has the narrative on the Israeli conflict evolved?',
  },
  {
    key: 'market',
    label: 'Market',
    icon: '📈',
    desc: 'Trading signals, tickers, macro',
    borderColor: 'border-green-500/30 hover:border-green-500/50',
    example: 'Show me BUY signals on European defense',
  },
  {
    key: 'comparative',
    label: 'Comparative',
    icon: '⚖',
    desc: 'Multi-axis comparisons, entities or periods',
    borderColor: 'border-pink-500/30 hover:border-pink-500/50',
    example: 'Compare China vs USA coverage over the last 3 months',
  },
  {
    key: 'overview',
    label: 'Overview',
    icon: '🌐',
    desc: 'Full geopolitical profile of a country',
    borderColor: 'border-teal-500/30 hover:border-teal-500/50',
    example: 'Geopolitical overview of Iran',
  },
];

const QUICK_EXAMPLES = [
  'What is happening in Ukraine this week?',
  'BUY signals on European defense',
  'Geopolitical overview of Iran',
  'Compare Russia vs China over the last 60 days',
];

export function OracleEmptyState({ onQuery }: { onQuery: (q: string) => void }) {
  return (
    <div className="flex flex-col items-center justify-center h-full py-8 px-4">
      {/* Header */}
      <div className="text-center mb-8">
        <div className="text-[#FF6B35] text-xl font-bold tracking-tight mb-1">◆ Oracle</div>
        <div className="text-white/60 text-sm">Intelligence Query Engine</div>
        <div className="text-gray-600 text-xs mt-2 max-w-xs leading-relaxed">
          RAG-augmented geopolitical and financial analysis on proprietary database
        </div>
      </div>

      {/* Intent cards grid */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-2.5 max-w-2xl w-full mb-8">
        {INTENT_TYPES.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => onQuery(t.example)}
            className={`text-left p-4 rounded-xl border bg-white/[0.02] transition-all hover:bg-white/5 active:bg-white/8 ${t.borderColor}`}
          >
            <div className="text-xl mb-2">{t.icon}</div>
            <div className="text-white/80 font-medium text-sm mb-1">{t.label}</div>
            <div className="text-gray-500 text-xs leading-snug">{t.desc}</div>
          </button>
        ))}
      </div>

      {/* Quick examples */}
      <div className="w-full max-w-2xl">
        <div className="text-xs text-gray-600 mb-2 text-center uppercase tracking-wider">
          Quick examples
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
          {QUICK_EXAMPLES.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => onQuery(q)}
              className="text-left text-xs px-3 py-2.5 rounded-lg border border-white/8 text-gray-500 hover:text-white/80 hover:border-white/15 transition-all bg-white/[0.02]"
            >
              {q}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
