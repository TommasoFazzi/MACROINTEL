'use client';

const INTENT_TYPES = [
  {
    key: 'factual',
    label: 'Fattuale',
    icon: '📰',
    desc: 'Notizie, eventi recenti, dichiarazioni',
    borderColor: 'border-blue-500/30 hover:border-blue-500/50',
    example: 'Cosa è successo in Ucraina negli ultimi 7 giorni?',
  },
  {
    key: 'analytical',
    label: 'Analitico',
    icon: '📊',
    desc: 'Trend, statistiche, conteggi',
    borderColor: 'border-yellow-500/30 hover:border-yellow-500/50',
    example: 'Quanti articoli sulla Cina negli ultimi 30 giorni?',
  },
  {
    key: 'narrative',
    label: 'Narrativo',
    icon: '🕸',
    desc: 'Evoluzione storyline e reti narrative',
    borderColor: 'border-purple-500/30 hover:border-purple-500/50',
    example: 'Come si è evoluta la narrativa sul conflitto israeliano?',
  },
  {
    key: 'market',
    label: 'Mercato',
    icon: '📈',
    desc: 'Segnali trading, ticker, macro',
    borderColor: 'border-green-500/30 hover:border-green-500/50',
    example: 'Mostrami i segnali BUY sulla difesa europea',
  },
  {
    key: 'comparative',
    label: 'Comparativo',
    icon: '⚖',
    desc: 'Confronti multi-asse, entità o periodi',
    borderColor: 'border-pink-500/30 hover:border-pink-500/50',
    example: 'Confronta la copertura di Cina vs USA negli ultimi 3 mesi',
  },
  {
    key: 'overview',
    label: 'Panoramica',
    icon: '🌐',
    desc: 'Profilo geopolitico completo di un paese',
    borderColor: 'border-teal-500/30 hover:border-teal-500/50',
    example: "Panoramica geopolitica dell'Iran",
  },
];

const QUICK_EXAMPLES = [
  "Cosa succede in Ucraina questa settimana?",
  "Segnali BUY sulla difesa europea",
  "Panoramica geopolitica Iran",
  "Confronta Russia vs Cina negli ultimi 60 giorni",
];

export function OracleEmptyState({ onQuery }: { onQuery: (q: string) => void }) {
  return (
    <div className="flex flex-col items-center justify-center h-full py-8 px-4">
      {/* Header */}
      <div className="text-center mb-8">
        <div className="text-[#FF6B35] text-xl font-bold tracking-tight mb-1">◆ Oracle</div>
        <div className="text-white/60 text-sm">Intelligence Query Engine</div>
        <div className="text-gray-600 text-xs mt-2 max-w-xs leading-relaxed">
          Analisi geopolitica e finanziaria RAG-augmented su database proprietario
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
          Esempi rapidi
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
