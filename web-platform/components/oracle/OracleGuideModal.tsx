'use client';

import { useEffect } from 'react';
import { X } from 'lucide-react';

const INTENT_GUIDE = [
  {
    key: 'factual',
    label: 'Fattuale',
    labelColor: 'text-blue-300',
    bgColor: 'bg-blue-500/8 border-blue-500/15',
    desc: 'Cerca eventi recenti, dichiarazioni e notizie. Usa priorità temporale recente con forte decay per i dati storici.',
    example: 'Cosa è successo in Taiwan questa settimana?',
  },
  {
    key: 'analytical',
    label: 'Analitico',
    labelColor: 'text-yellow-300',
    bgColor: 'bg-yellow-500/8 border-yellow-500/15',
    desc: 'Conta, raggruppa, calcola trend. Interroga direttamente il database con aggregazioni su articoli ed entità.',
    example: 'Quanti articoli sulla Cina negli ultimi 30 giorni?',
  },
  {
    key: 'narrative',
    label: 'Narrativo',
    labelColor: 'text-purple-300',
    bgColor: 'bg-purple-500/8 border-purple-500/15',
    desc: 'Esplora il network di storyline, l\'evoluzione narrativa nel tempo e le relazioni tra temi geopolitici.',
    example: 'Come si è evoluta la narrativa sul conflitto israeliano?',
  },
  {
    key: 'market',
    label: 'Mercato',
    labelColor: 'text-green-300',
    bgColor: 'bg-green-500/8 border-green-500/15',
    desc: 'Analizza segnali di trading (BULLISH/BEARISH/WATCHLIST), ticker, macro indicatori e correlazioni geopolitiche.',
    example: 'Mostrami i segnali BUY sulla difesa europea',
  },
  {
    key: 'comparative',
    label: 'Comparativo',
    labelColor: 'text-pink-300',
    bgColor: 'bg-pink-500/8 border-pink-500/15',
    desc: 'Confronta entità, periodi o prospettive. Oracle decompone automaticamente la query in sotto-query parallele.',
    example: 'Confronta la copertura di Russia vs Cina negli ultimi 60 giorni',
  },
  {
    key: 'overview',
    label: 'Panoramica',
    labelColor: 'text-teal-300',
    bgColor: 'bg-teal-500/8 border-teal-500/15',
    desc: 'Profilo geopolitico completo di un paese o regione. Usa tutta la storia disponibile nel database (decay lentissimo).',
    example: "Panoramica geopolitica dell'Iran",
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
            <h2 className="text-white font-semibold text-base">Guida Oracle</h2>
            <p className="text-gray-500 text-xs mt-0.5">
              Come interrogare il database di intelligence
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
              Cos&apos;è Oracle
            </h3>
            <p className="text-gray-400 text-sm leading-relaxed">
              Oracle è un motore di analisi intelligence che combina ricerca vettoriale semantica
              (RAG) con sintesi LLM su un database proprietario di articoli geopolitici, report di
              intelligence e segnali di mercato. Mantiene la memoria della conversazione per
              rispondere a domande di follow-up contestuali.
            </p>
            <p className="text-gray-500 text-xs mt-2 leading-relaxed">
              Le risposte includono citazioni numeriche <span className="inline-flex items-center justify-center w-4 h-4 rounded text-[10px] font-bold bg-[#FF6B35]/20 text-[#FF6B35] border border-[#FF6B35]/40 mx-0.5">1</span> cliccabili che rimandano direttamente alla fonte nella sidebar.
            </p>
          </div>

          {/* Intent types */}
          <div>
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
              Tipi di query supportati
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
                    Es: &ldquo;{t.example}&rdquo; →
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* Filters */}
          <div>
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2.5">
              Filtri disponibili
            </h3>
            <ul className="space-y-2 text-sm text-gray-400">
              <li>
                <span className="text-white/60">Intervallo date</span> — limita la ricerca a un
                periodo specifico
              </li>
              <li>
                <span className="text-white/60">Paese / GPE</span> — filtra per entità geografica
                (es. &quot;Russia, Iran&quot;)
              </li>
              <li>
                <span className="text-white/60">Modalità</span> —{' '}
                <em>both</em> (report + articoli), <em>factual</em> (solo articoli),{' '}
                <em>strategic</em> (solo report)
              </li>
              <li>
                <span className="text-white/60">Tipo di ricerca</span> —{' '}
                <em>hybrid</em> (vettoriale + keyword), <em>vector</em>, <em>keyword</em>
              </li>
            </ul>
            <p className="text-gray-600 text-xs mt-2">
              Configura i filtri dal pannello ⚙ Impostazioni nell&apos;header.
            </p>
          </div>

          {/* Limits */}
          <div>
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2.5">
              Note tecniche
            </h3>
            <ul className="space-y-2 text-sm text-gray-500">
              <li>⏱ Timeout richiesta: 120 secondi (query complesse richiedono 15–30s)</li>
              <li>
                🔑 Richiede una Gemini API key valida — configurabile in{' '}
                <span className="text-white/50">Impostazioni</span>
              </li>
              <li>📚 Il database viene aggiornato ogni giorno alle 08:00 UTC</li>
              <li>
                🔗 Le citazioni nel testo sono cliccabili e portano alla fonte corrispondente nella
                sidebar
              </li>
              <li>🧠 La memoria di sessione persiste per tutta la conversazione corrente</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
