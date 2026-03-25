'use client';

import { HelpCircle, Settings, Plus, BookOpen } from 'lucide-react';

interface OracleHeaderProps {
  onGuide: () => void;
  onSettings: () => void;
  onNewSession: () => void;
  onSources: () => void;
  hasMessages: boolean;
  isKeyActive: boolean;
}

export function OracleHeader({
  onGuide,
  onSettings,
  onNewSession,
  onSources,
  hasMessages,
  isKeyActive,
}: OracleHeaderProps) {
  return (
    <header className="border-b border-white/10 px-5 py-3.5 flex items-center justify-between sticky top-0 z-10 bg-[#0A1628]/90 backdrop-blur-md flex-shrink-0">
      {/* Left: logo */}
      <div className="flex items-center gap-2.5">
        <span className="text-[#FF6B35] font-bold text-base leading-none">◆</span>
        <span className="text-white font-semibold text-base tracking-tight">Oracle</span>
        {!isKeyActive && (
          <span className="text-xs px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 border border-amber-500/25 animate-pulse">
            key mancante
          </span>
        )}
      </div>

      {/* Right: actions */}
      <div className="flex items-center gap-1">
        {/* Mobile sources trigger — only shown when there are messages */}
        {hasMessages && (
          <button
            type="button"
            onClick={onSources}
            className="md:hidden p-2 rounded-lg text-gray-400 hover:text-white hover:bg-white/5 transition-all"
            title="Fonti"
          >
            <BookOpen size={16} />
          </button>
        )}

        <button
          type="button"
          onClick={onGuide}
          className="p-2 rounded-lg text-gray-400 hover:text-white hover:bg-white/5 transition-all"
          title="Guida Oracle"
        >
          <HelpCircle size={16} />
        </button>

        <button
          type="button"
          onClick={onSettings}
          className="p-2 rounded-lg text-gray-400 hover:text-white hover:bg-white/5 transition-all"
          title="Impostazioni"
        >
          <Settings size={16} />
        </button>

        <button
          type="button"
          onClick={onNewSession}
          className="flex items-center gap-1.5 ml-1 px-3 py-1.5 rounded-lg border border-white/10 text-gray-400 hover:text-white hover:border-white/20 text-xs transition-all"
          title="Nuova sessione"
        >
          <Plus size={13} />
          <span className="hidden sm:inline">Nuova sessione</span>
        </button>
      </div>
    </header>
  );
}
