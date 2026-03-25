'use client';

import { useState, useEffect } from 'react';
import { X, Eye, EyeOff } from 'lucide-react';
import type { OracleActiveFilters } from '../../types/oracle';

const GEMINI_KEY_RE = /^AIza[0-9A-Za-z\-_]{30,50}$/;

interface OracleSettingsPanelProps {
  open: boolean;
  onClose: () => void;
  geminiApiKey: string;
  setGeminiApiKey: (k: string) => void;
  activeFilters: OracleActiveFilters;
  setActiveFilters: (f: OracleActiveFilters) => void;
  onClearSession: () => void;
}

export function OracleSettingsPanel({
  open,
  onClose,
  geminiApiKey,
  setGeminiApiKey,
  activeFilters,
  setActiveFilters,
  onClearSession,
}: OracleSettingsPanelProps) {
  const [draftKey, setDraftKey] = useState(geminiApiKey);
  const [showKey, setShowKey] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  // Sync draft when key changes externally
  useEffect(() => {
    setDraftKey(geminiApiKey);
  }, [geminiApiKey]);

  // Close on ESC
  useEffect(() => {
    if (!open) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, onClose]);

  // Reset confirm state when panel closes
  useEffect(() => {
    if (!open) setConfirmClear(false);
  }, [open]);

  const isKeyValid = draftKey === '' || GEMINI_KEY_RE.test(draftKey);
  const isKeyActive = GEMINI_KEY_RE.test(geminiApiKey);

  const handleSaveKey = () => {
    if (!isKeyValid) return;
    setGeminiApiKey(draftKey);
  };

  const handleClearSession = () => {
    if (!confirmClear) {
      setConfirmClear(true);
      return;
    }
    onClearSession();
    setConfirmClear(false);
    onClose();
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />

      {/* Panel */}
      <div className="relative bg-[#0a1628] border-l border-white/10 w-full max-w-sm flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/10">
          <h2 className="text-white font-semibold text-sm">Impostazioni Oracle</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-500 hover:text-white transition-colors p-1.5 rounded-lg hover:bg-white/5"
          >
            <X size={15} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-5 space-y-7">

          {/* Gemini API Key */}
          <section>
            <div className="flex items-center justify-between mb-1.5">
              <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
                Gemini API Key
              </h3>
              {isKeyActive ? (
                <span className="text-xs px-1.5 py-0.5 rounded bg-green-500/20 text-green-400 border border-green-500/30">
                  Attiva
                </span>
              ) : (
                <span className="text-xs px-1.5 py-0.5 rounded bg-red-500/20 text-red-400 border border-red-500/30 animate-pulse">
                  Richiesta
                </span>
              )}
            </div>
            <p className="text-gray-600 text-xs mb-3 leading-relaxed">
              Necessaria per usare Oracle. Gratuita su{' '}
              <a
                href="https://aistudio.google.com/apikey"
                target="_blank"
                rel="noopener noreferrer"
                className="text-[#00A8E8] hover:underline"
              >
                aistudio.google.com
              </a>
              . Salvata solo in localStorage, mai inviata ai server.
            </p>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <input
                  type={showKey ? 'text' : 'password'}
                  value={draftKey}
                  onChange={(e) => setDraftKey(e.target.value)}
                  placeholder="AIza..."
                  className={`w-full bg-[#1a2a4a] border rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none pr-9 ${
                    isKeyValid
                      ? 'border-white/10 focus:border-[#FF6B35]/50'
                      : 'border-red-500/50'
                  }`}
                />
                <button
                  type="button"
                  onClick={() => setShowKey((s) => !s)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-white transition-colors"
                >
                  {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
              <button
                type="button"
                onClick={handleSaveKey}
                disabled={!isKeyValid || draftKey === geminiApiKey}
                className="px-3 py-2 rounded-lg bg-[#FF6B35]/80 text-white text-xs font-medium hover:bg-[#FF6B35] disabled:opacity-40 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
              >
                Salva
              </button>
            </div>
            {!isKeyValid && draftKey && (
              <p className="mt-1.5 text-xs text-red-400">
                Formato non valido — atteso: AIza + 30–50 caratteri
              </p>
            )}
            {geminiApiKey && (
              <button
                type="button"
                onClick={() => {
                  setDraftKey('');
                  setGeminiApiKey('');
                }}
                className="mt-2 text-xs text-gray-600 hover:text-red-400 transition-colors"
              >
                Rimuovi chiave
              </button>
            )}
          </section>

          {/* Search mode */}
          <section>
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
              Modalità ricerca
            </h3>
            <div className="space-y-2">
              {(
                [
                  ['both', 'Entrambi (report + articoli)'],
                  ['factual', 'Solo articoli'],
                  ['strategic', 'Solo report'],
                ] as const
              ).map(([val, label]) => (
                <label key={val} className="flex items-center gap-2.5 cursor-pointer">
                  <input
                    type="radio"
                    name="oracle-mode"
                    value={val}
                    checked={(activeFilters.mode ?? 'both') === val}
                    onChange={() => setActiveFilters({ ...activeFilters, mode: val })}
                    className="accent-[#FF6B35]"
                  />
                  <span className="text-sm text-gray-300">{label}</span>
                </label>
              ))}
            </div>
          </section>

          {/* Search type */}
          <section>
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
              Tipo di ricerca
            </h3>
            <div className="space-y-2">
              {(
                [
                  ['hybrid', 'Hybrid (vettoriale + keyword)'],
                  ['vector', 'Solo vettoriale'],
                  ['keyword', 'Solo keyword'],
                ] as const
              ).map(([val, label]) => (
                <label key={val} className="flex items-center gap-2.5 cursor-pointer">
                  <input
                    type="radio"
                    name="oracle-search-type"
                    value={val}
                    checked={(activeFilters.search_type ?? 'hybrid') === val}
                    onChange={() => setActiveFilters({ ...activeFilters, search_type: val })}
                    className="accent-[#FF6B35]"
                  />
                  <span className="text-sm text-gray-300">{label}</span>
                </label>
              ))}
            </div>
          </section>

          {/* Date filter */}
          <section>
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
              Intervallo date
            </h3>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs text-gray-500 mb-1 block">Da</label>
                <input
                  type="date"
                  value={activeFilters.start_date ?? ''}
                  onChange={(e) =>
                    setActiveFilters({
                      ...activeFilters,
                      start_date: e.target.value || undefined,
                    })
                  }
                  className="w-full bg-[#1a2a4a] border border-white/10 rounded-lg px-2 py-1.5 text-xs text-white focus:outline-none focus:border-[#FF6B35]/50"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500 mb-1 block">A</label>
                <input
                  type="date"
                  value={activeFilters.end_date ?? ''}
                  onChange={(e) =>
                    setActiveFilters({
                      ...activeFilters,
                      end_date: e.target.value || undefined,
                    })
                  }
                  className="w-full bg-[#1a2a4a] border border-white/10 rounded-lg px-2 py-1.5 text-xs text-white focus:outline-none focus:border-[#FF6B35]/50"
                />
              </div>
            </div>
            {(activeFilters.start_date || activeFilters.end_date) && (
              <button
                type="button"
                onClick={() =>
                  setActiveFilters({
                    ...activeFilters,
                    start_date: undefined,
                    end_date: undefined,
                  })
                }
                className="mt-2 text-xs text-gray-600 hover:text-white transition-colors"
              >
                Rimuovi filtro date
              </button>
            )}
          </section>

          {/* GPE filter */}
          <section>
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1">
              Paese / GPE Filter
            </h3>
            <p className="text-gray-600 text-xs mb-2">
              Es: &quot;Russia, Iran, Cina&quot; (separati da virgola)
            </p>
            <input
              type="text"
              value={activeFilters.gpe_filter?.join(', ') ?? ''}
              onChange={(e) => {
                const val = e.target.value;
                const filters = val
                  ? val
                      .split(',')
                      .map((s) => s.trim())
                      .filter(Boolean)
                  : undefined;
                setActiveFilters({ ...activeFilters, gpe_filter: filters });
              }}
              placeholder="Russia, Iran..."
              className="w-full bg-[#1a2a4a] border border-white/10 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none focus:border-[#FF6B35]/50"
            />
          </section>
        </div>

        {/* Footer — Reset Context */}
        <div className="px-5 py-4 border-t border-white/10 flex-shrink-0">
          <p className="text-xs text-gray-600 mb-3 leading-relaxed">
            Azzera la memoria di sessione se Oracle si incarta sul contesto di conversazioni
            precedenti.
          </p>
          <button
            type="button"
            onClick={handleClearSession}
            className={`w-full py-2.5 rounded-lg border text-sm font-medium transition-all ${
              confirmClear
                ? 'bg-red-500/20 border-red-500/50 text-red-300 hover:bg-red-500/30'
                : 'border-red-500/20 text-red-400/50 hover:border-red-500/40 hover:text-red-400/80'
            }`}
          >
            {confirmClear ? '⚠ Conferma: azzera memoria' : 'Azzera memoria di sessione'}
          </button>
          {confirmClear && (
            <button
              type="button"
              onClick={() => setConfirmClear(false)}
              className="w-full mt-1.5 text-xs text-gray-600 hover:text-white transition-colors py-1"
            >
              Annulla
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
