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
import { OracleSourceCard } from '@/components/oracle/OracleSourceCard';

export default function OraclePage() {
  const {
    messages,
    isLoading,
    error,
    byokError,
    sendMessage,
    clearMessages,
    lastAssistantMessage,
    geminiApiKey,
    setGeminiApiKey,
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

  const isKeyActive = /^AIza[0-9A-Za-z\-_]{30,50}$/.test(geminiApiKey);
  const hasMessages = messages.length > 0;

  // Auto-scroll on new messages / loading change
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isLoading]);

  // Auto-resize textarea
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
    // Reset textarea height
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
    // On mobile, open the sources sheet
    if (window.innerWidth < 768) {
      setShowSourcesSheet(true);
    }
    // Clear highlight after 3s
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

      {/* Header */}
      <OracleHeader
        onGuide={() => setShowGuide(true)}
        onSettings={() => setShowSettings(true)}
        onNewSession={handleClearSession}
        onSources={() => setShowSourcesSheet(true)}
        hasMessages={hasMessages}
        isKeyActive={isKeyActive}
      />

      {/* BYOK error banner */}
      {byokError && (
        <div className="mx-4 mt-3 px-4 py-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-300 text-sm flex-shrink-0">
          {byokError.toLowerCase().includes('required') ? (
            <>
              <strong>API key required.</strong> Configure your Gemini API key in{' '}
              <button
                type="button"
                onClick={() => setShowSettings(true)}
                className="underline hover:text-amber-100"
              >
                Settings
              </button>
              .{' '}
              <a
                href="https://aistudio.google.com/apikey"
                target="_blank"
                rel="noopener noreferrer"
                className="underline hover:text-amber-100"
              >
                Get it free →
              </a>
            </>
          ) : (
            <>
              <strong>Gemini API key error:</strong> the key is invalid or has exhausted its
              quota.{' '}
              <span className="text-amber-400/70 text-xs">({byokError})</span>
            </>
          )}
        </div>
      )}

      {/* Main layout */}
      <div className="flex-1 flex overflow-hidden min-h-0">

        {/* Chat column */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">

          {/* Message list */}
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

          {/* Input area */}
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

        {/* Sources sidebar (desktop) */}
        <OracleSourcesSidebar
          message={lastAssistantMessage}
          highlightedSource={highlightedSource}
          isVisible={true}
        />
      </div>

      {/* Mobile sources bottom sheet */}
      <BottomSheet
        open={showSourcesSheet}
        onClose={() => setShowSourcesSheet(false)}
        title="Sources & Analysis"
        heightClass="h-[75vh]"
        className="bg-[#0d1d35]"
      >
        <div className="px-4 py-3">
          {lastAssistantMessage?.sources && lastAssistantMessage.sources.length > 0 ? (
            lastAssistantMessage.sources.map((src, i) => (
              <OracleSourceCard
                key={i}
                source={src}
                index={i + 1}
                highlighted={highlightedSource === i + 1}
              />
            ))
          ) : (
            <div className="text-center text-gray-600 text-xs pt-8">
              Sources will appear after the first response.
            </div>
          )}
        </div>
      </BottomSheet>

      {/* Guide modal */}
      <OracleGuideModal
        open={showGuide}
        onClose={() => setShowGuide(false)}
        onQuerySelect={(q) => {
          handleQueryInject(q);
          setShowGuide(false);
        }}
      />

      {/* Settings panel */}
      <OracleSettingsPanel
        open={showSettings}
        onClose={() => setShowSettings(false)}
        geminiApiKey={geminiApiKey}
        setGeminiApiKey={setGeminiApiKey}
        activeFilters={activeFilters}
        setActiveFilters={setActiveFilters}
        onClearSession={handleClearSession}
      />
    </div>
  );
}
