'use client';

import { HelpCircle, Settings, Plus, BookOpen } from 'lucide-react';

interface OracleHeaderProps {
  onGuide: () => void;
  onSettings: () => void;
  onNewSession: () => void;
  onSources: () => void;
  hasMessages: boolean;
}

export function OracleHeader({
  onGuide,
  onSettings,
  onNewSession,
  onSources,
  hasMessages,
}: OracleHeaderProps) {
  return (
    <header className="border-b border-white/10 px-5 py-3.5 flex items-center justify-between sticky top-0 z-10 bg-[#0A1628]/90 backdrop-blur-md flex-shrink-0">
      {/* Left: logo */}
      <div className="flex items-center gap-2.5">
        <span className="text-[#FF6B35] font-bold text-base leading-none">◆</span>
        <span className="text-white font-semibold text-base tracking-tight">Oracle</span>
      </div>

      {/* Right: actions */}
      <div className="flex items-center gap-1">
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
          title="Oracle Guide"
        >
          <HelpCircle size={16} />
        </button>

        <button
          type="button"
          onClick={onSettings}
          className="p-2 rounded-lg text-gray-400 hover:text-white hover:bg-white/5 transition-all"
          title="Settings"
        >
          <Settings size={16} />
        </button>

        <button
          type="button"
          onClick={onNewSession}
          className="flex items-center gap-1.5 ml-1 px-3 py-1.5 rounded-lg border border-white/10 text-gray-400 hover:text-white hover:border-white/20 text-xs transition-all"
          title="New session"
        >
          <Plus size={13} />
          <span className="hidden sm:inline">New session</span>
        </button>
      </div>
    </header>
  );
}
