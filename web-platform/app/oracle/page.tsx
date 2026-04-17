'use client';

import { useState, useRef, useEffect, KeyboardEvent } from 'react';
import { ArrowUp } from 'lucide-react';
import { useOracleChat } from '../../hooks/useOracleChat';
import { BottomSheet } from '@/components/ui/bottom-sheet';
import { OracleHeader } from '@/components/oracle/OracleHeader';
import { OracleGuideModal } from '@/components/oracle/OracleGuideModal';
import { OracleSettingsPanel } from '@/components/oracle/OracleSettingsPanel';
import { OracleEmptyState } from '@/components/oracle/OracleEmptyState';
import { OracleThinkingState } from '@/components/oracle/OracleThinkingState';
import { UserBubble, AssistantBubble } from '@/components/oracle/OracleMessage';
import { OracleSourcesSidebar } from '@/components/oracle/OracleSourcesSidebar';

export default function OraclePage() {
  const {
    messages,
    isLoading,
    error,
    sendMessage,
    clearMessages,
    lastAssistantMessage,
    activeFilters,
    setActiveFilters,
  } = useOracleChat();

  const [input, setInput] = useState('');
  const [showGuide, setShowGuide] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showSourcesSheet, setShowSourcesSheet] = useState(false);
  const [highlightedSource, setHighlightedSource] = useState<number | null>(null);

  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const hasMessages = messages.length > 0;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isLoading]);

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    e.target.style.height = 'auto';
    e.target.style.height = Math.min(e.target.scrollHeight, 200) + 'px';
  };

  const handleSend = () => {
    const q = input.trim();
    if (!q || isLoading) return;
    setInput('');
    setHighlightedSource(null);
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    sendMessage(q);
    textareaRef.current?.focus();
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleCitationClick = (n: number) => {
    setHighlightedSource(n);
    if (window.innerWidth < 768) {
      setShowSourcesSheet(true);
    }
    setTimeout(() => setHighlightedSource(null), 3000);
  };

  const handleQueryInject = (q: string) => {
    setInput(q);
    textareaRef.current?.focus();
  };

  const handleClearSession = () => {
    clearMessages();
    setHighlightedSource(null);
  };

  return (
    <div className="h-screen bg-[#0A1628] text-white flex flex-col overflow-hidden">

      <OracleHeader
        onGuide={() => setShowGuide(true)}
        onSettings={() => setShowSettings(true)}
        onNewSession={handleClearSession}
        onSources={() => setShowSourcesSheet(true)}
        hasMessages={hasMessages}
      />

      <div className="flex-1 flex overflow-hidden min-h-0">

        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">

          <div className="flex-1 overflow-y-auto px-4 md:px-6 py-4">
            {!hasMessages ? (
              <OracleEmptyState onQuery={handleQueryInject} />
            ) : (
              <>
                {messages.map((msg) =>
                  msg.role === 'user' ? (
                    <UserBubble key={msg.id} msg={msg} />
                  ) : (
                    <AssistantBubble
                      key={msg.id}
                      msg={msg}
                      onCitationClick={handleCitationClick}
                    />
                  )
                )}
                {isLoading && <OracleThinkingState />}
                {error && (
                  <div className="max-w-2xl mx-auto mb-4">
                    <div className="text-sm text-red-400 py-2 px-3 rounded-lg bg-red-500/10 border border-red-500/20">
                      {error}
                    </div>
                  </div>
                )}
              </>
            )}
            <div ref={bottomRef} />
          </div>

          <div className="border-t border-white/10 px-4 py-4 pb-safe flex-shrink-0">
            <div className="max-w-2xl mx-auto">
              <div className="flex gap-2 items-end bg-[#1a2a4a] border border-white/10 rounded-2xl px-4 py-2.5 focus-within:border-[#FF6B35]/40 transition-colors">
                <textarea
                  ref={textareaRef}
                  value={input}
                  onChange={handleInputChange}
                  onKeyDown={handleKeyDown}
                  placeholder="Ask an intelligence question..."
                  rows={1}
                  className="flex-1 bg-transparent text-sm text-white placeholder-gray-500 focus:outline-none resize-none min-h-[26px] max-h-[200px] py-1 leading-relaxed overflow-y-hidden"
                />
                <button
                  type="button"
                  onClick={handleSend}
                  disabled={!input.trim() || isLoading}
                  aria-label="Send message"
                  className="flex-shrink-0 w-8 h-8 rounded-xl bg-[#FF6B35] text-white flex items-center justify-center hover:bg-[#FF6B35]/80 active:bg-[#FF6B35]/70 disabled:opacity-40 disabled:cursor-not-allowed transition-colors self-end mb-0.5"
                >
                  <ArrowUp size={15} />
                </button>
              </div>
              <div className="text-center mt-1.5 text-xs text-gray-700">
                Enter to send · Shift+Enter for new line
              </div>
            </div>
          </div>
        </div>

        <OracleSourcesSidebar
          message={lastAssistantMessage}
          highlightedSource={highlightedSource}
          isVisible={true}
        />
      </div>

      <BottomSheet
        open={showSourcesSheet}
        onClose={() => setShowSourcesSheet(false)}
        title="Sources & Analysis"
        heightClass="h-[75vh]"
        className="bg-[#0d1d35]"
      >
        <OracleSourcesSidebar
          message={lastAssistantMessage}
          highlightedSource={highlightedSource}
          isVisible={true}
          embedded={true}
        />
      </BottomSheet>

      <OracleGuideModal
        open={showGuide}
        onClose={() => setShowGuide(false)}
        onQuerySelect={(q) => {
          handleQueryInject(q);
          setShowGuide(false);
        }}
      />

      <OracleSettingsPanel
        open={showSettings}
        onClose={() => setShowSettings(false)}
        activeFilters={activeFilters}
        setActiveFilters={setActiveFilters}
        onClearSession={handleClearSession}
      />
    </div>
  );
}
