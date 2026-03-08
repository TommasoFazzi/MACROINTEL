'use client';

import { useState, useRef, useEffect, KeyboardEvent } from 'react';
import ReactMarkdown from 'react-markdown';
import { useOracleChat } from '../../hooks/useOracleChat';
import type { OracleChatMessage, OracleSource, QueryPlan } from '../../types/oracle';

// ── Intent / Complexity badge colors ────────────────────────────────────────
const INTENT_COLORS: Record<string, string> = {
  factual: 'bg-blue-500/20 text-blue-300 border-blue-500/30',
  analytical: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/30',
  narrative: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
  market: 'bg-green-500/20 text-green-300 border-green-500/30',
  comparative: 'bg-pink-500/20 text-pink-300 border-pink-500/30',
};

const COMPLEXITY_COLORS: Record<string, string> = {
  simple: 'bg-gray-500/20 text-gray-300 border-gray-500/30',
  medium: 'bg-orange-500/20 text-orange-300 border-orange-500/30',
  complex: 'bg-red-500/20 text-red-300 border-red-500/30',
};

// Loose frontend validation matching the backend regex
const GEMINI_KEY_RE = /^AIza[0-9A-Za-z\-_]{30,50}$/;

// ── Sub-components ────────────────────────────────────────────────────────────

function QueryPlanBadges({ plan }: { plan: QueryPlan }) {
  return (
    <div className="flex flex-wrap gap-2 mb-3">
      <span className={`text-xs px-2 py-0.5 rounded-full border ${INTENT_COLORS[plan.intent] ?? 'bg-gray-500/20 text-gray-300'}`}>
        {plan.intent}
      </span>
      <span className={`text-xs px-2 py-0.5 rounded-full border ${COMPLEXITY_COLORS[plan.complexity] ?? 'bg-gray-500/20 text-gray-300'}`}>
        {plan.complexity}
      </span>
      {plan.tools.map((t) => (
        <span key={t} className="text-xs px-2 py-0.5 rounded-full border bg-[#FF6B35]/10 text-[#FF6B35] border-[#FF6B35]/30">
          {t}
        </span>
      ))}
    </div>
  );
}

function SourceCard({ source }: { source: OracleSource }) {
  const simPct = Math.round(source.similarity * 100);
  return (
    <div className="p-3 rounded-lg border border-white/10 bg-[#0d1d35] mb-2 text-sm">
      <div className="flex items-center gap-2 mb-1">
        <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${
          source.type === 'REPORT'
            ? 'bg-[#FF6B35]/20 text-[#FF6B35]'
            : 'bg-[#00A8E8]/20 text-[#00A8E8]'
        }`}>
          {source.type}
        </span>
        <span className="text-gray-400 text-xs">{simPct}% match</span>
      </div>
      <div className="text-white/90 font-medium truncate" title={source.title}>
        {source.link ? (
          <a href={source.link} target="_blank" rel="noopener noreferrer" className="hover:text-[#00A8E8] transition-colors">
            {source.title}
          </a>
        ) : source.title}
      </div>
      {source.date_str && (
        <div className="text-gray-500 text-xs mt-0.5">{source.date_str}</div>
      )}
      {source.preview && (
        <div className="text-gray-400 text-xs mt-1 line-clamp-2">{source.preview}</div>
      )}
    </div>
  );
}

function TypingIndicator() {
  return (
    <div className="flex items-center gap-1 px-4 py-3">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="w-2 h-2 rounded-full bg-[#FF6B35] animate-bounce"
          style={{ animationDelay: `${i * 150}ms` }}
        />
      ))}
    </div>
  );
}

function UserBubble({ msg }: { msg: OracleChatMessage }) {
  return (
    <div className="flex justify-end mb-4">
      <div className="max-w-[75%] px-4 py-3 rounded-2xl rounded-tr-sm bg-[#FF6B35]/20 border border-[#FF6B35]/30 text-white/90 text-sm whitespace-pre-wrap">
        {msg.content}
      </div>
    </div>
  );
}

function AssistantBubble({ msg }: { msg: OracleChatMessage }) {
  return (
    <div className="flex justify-start mb-4">
      <div className="max-w-[85%] px-4 py-3 rounded-2xl rounded-tl-sm bg-[#1a2a4a] border border-white/10 text-white/90 text-sm">
        {msg.query_plan && <QueryPlanBadges plan={msg.query_plan} />}
        <ReactMarkdown
          components={{
            p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
            ul: ({ children }) => <ul className="list-disc list-inside mb-2 space-y-1">{children}</ul>,
            ol: ({ children }) => <ol className="list-decimal list-inside mb-2 space-y-1">{children}</ol>,
            li: ({ children }) => <li className="text-white/80">{children}</li>,
            strong: ({ children }) => <strong className="text-white font-semibold">{children}</strong>,
            h1: ({ children }) => <h1 className="text-lg font-bold text-[#FF6B35] mb-2 mt-3">{children}</h1>,
            h2: ({ children }) => <h2 className="text-base font-bold text-[#FF6B35] mb-2 mt-3">{children}</h2>,
            h3: ({ children }) => <h3 className="text-sm font-semibold text-[#00A8E8] mb-1 mt-2">{children}</h3>,
            code: ({ children }) => <code className="bg-black/30 px-1 rounded text-xs text-[#00A8E8]">{children}</code>,
            blockquote: ({ children }) => <blockquote className="border-l-2 border-[#FF6B35] pl-3 text-gray-400 italic my-2">{children}</blockquote>,
          }}
        >
          {msg.content}
        </ReactMarkdown>
        {typeof msg.metadata?.execution_time === 'number' && (
          <div className="mt-2 text-xs text-gray-500">
            {(msg.metadata.execution_time as number).toFixed(1)}s · {(msg.metadata.tools_executed as string[] | undefined)?.join(', ')}
          </div>
        )}
      </div>
    </div>
  );
}

// ── BYOK Panel ────────────────────────────────────────────────────────────────

function ByokPanel({
  geminiApiKey,
  setGeminiApiKey,
}: {
  geminiApiKey: string;
  setGeminiApiKey: (key: string) => void;
}) {
  const [open, setOpen] = useState(!GEMINI_KEY_RE.test(geminiApiKey));
  const [draft, setDraft] = useState(geminiApiKey);
  const [showKey, setShowKey] = useState(false);

  const isValid = draft === '' || GEMINI_KEY_RE.test(draft);
  const isActive = GEMINI_KEY_RE.test(geminiApiKey);

  const handleSave = () => {
    if (!isValid) return;
    setGeminiApiKey(draft);
  };

  return (
    <div className="border-b border-white/10 bg-[#0d1d35]">
      {/* Collapsed header */}
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-6 py-2 text-xs text-gray-400 hover:text-white transition-colors"
      >
        <div className="flex items-center gap-2">
          <span>⚙ Gemini API Key</span>
          {isActive ? (
            <span className="px-1.5 py-0.5 rounded bg-green-500/20 text-green-400 border border-green-500/30 text-[10px] font-medium">
              BYOK active
            </span>
          ) : (
            <span className="px-1.5 py-0.5 rounded bg-red-500/20 text-red-400 border border-red-500/30 text-[10px] font-medium animate-pulse">
              KEY REQUIRED
            </span>
          )}
        </div>
        <span className="text-gray-500">{open ? '▲' : '▼'}</span>
      </button>

      {/* Expanded body */}
      {open && (
        <div className="px-6 pb-3">
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <input
                type={showKey ? 'text' : 'password'}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="AIza..."
                className={`w-full bg-[#1a2a4a] border rounded-lg px-3 py-2 text-sm text-white placeholder-gray-600 focus:outline-none pr-10 ${
                  isValid ? 'border-white/10 focus:border-[#FF6B35]/50' : 'border-red-500/50'
                }`}
              />
              <button
                type="button"
                onClick={() => setShowKey((s) => !s)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-white text-xs"
                title={showKey ? 'Hide' : 'Show'}
              >
                {showKey ? '🙈' : '👁'}
              </button>
            </div>
            <button
              type="button"
              onClick={handleSave}
              disabled={!isValid}
              className="px-3 py-2 rounded-lg bg-[#FF6B35]/80 text-white text-xs font-medium hover:bg-[#FF6B35] disabled:opacity-40 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
            >
              Save
            </button>
            {geminiApiKey && (
              <button
                type="button"
                onClick={() => { setDraft(''); setGeminiApiKey(''); }}
                className="px-3 py-2 rounded-lg border border-white/10 text-gray-400 hover:text-white text-xs transition-colors whitespace-nowrap"
              >
                Remove
              </button>
            )}
          </div>

          {!isValid && draft !== '' && (
            <p className="mt-1 text-xs text-red-400">Invalid format — expected: AIza + 30-50 characters</p>
          )}

          <p className="mt-1.5 text-[10px] text-gray-600">
            🔑 A Gemini API key is required to use Oracle. Get one free at{' '}
            <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener noreferrer" className="underline text-gray-500 hover:text-white">
              aistudio.google.com/apikey
            </a>. Saved in localStorage only.
          </p>
        </div>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

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
  } = useOracleChat();
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isLoading]);

  const handleSend = () => {
    const q = input.trim();
    if (!q || isLoading) return;
    setInput('');
    sendMessage(q);
    textareaRef.current?.focus();
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="min-h-screen bg-[#0A1628] text-white flex flex-col">
      {/* Header */}
      <header className="border-b border-white/10 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold tracking-tight">Oracle 2.0</h1>
          <span className="text-xs px-2 py-0.5 rounded-full bg-[#FF6B35]/20 text-[#FF6B35] border border-[#FF6B35]/30 font-medium">
            INTELLIGENCE AI
          </span>
        </div>
        <button
          onClick={clearMessages}
          className="text-xs text-gray-400 hover:text-white transition-colors px-3 py-1.5 rounded-lg border border-white/10 hover:border-white/30"
        >
          New session
        </button>
      </header>

      {/* BYOK Panel */}
      <ByokPanel geminiApiKey={geminiApiKey} setGeminiApiKey={setGeminiApiKey} />

      {/* BYOK error banner (422 = key required, 402 = key invalid/exhausted) */}
      {byokError && (
        <div className="mx-6 mt-3 px-4 py-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-300 text-sm">
          {byokError.toLowerCase().includes('required') ? (
            <>
              <strong>API key required:</strong> You must configure your own Gemini API key to use Oracle.
              Open the <strong>⚙ Gemini API Key</strong> panel above and paste your key.{' '}
              <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener noreferrer" className="underline text-amber-200 hover:text-white">
                Get a free key →
              </a>
            </>
          ) : (
            <>
              <strong>Gemini API key error:</strong> Your key is invalid or has exhausted its quota.{' '}
              <span className="text-amber-400/70 text-xs">({byokError})</span>
            </>
          )}
        </div>
      )}

      {/* Main layout */}
      <div className="flex-1 flex overflow-hidden">
        {/* Chat column */}
        <div className="flex-1 flex flex-col min-w-0">
          {/* Message list */}
          <div className="flex-1 overflow-y-auto px-6 py-4">
            {messages.length === 0 && (
              <div className="h-full flex flex-col items-center justify-center text-center">
                <div className="text-5xl mb-4">🔮</div>
                <h2 className="text-xl font-semibold text-white/80 mb-2">
                  Query the Intelligence Database
                </h2>
                <p className="text-gray-400 text-sm max-w-md">
                  Ask questions about geopolitical events, trends, market signals, and storylines.
                  Oracle 2.0 uses multiple tools to provide grounded answers.
                </p>
                <div className="mt-6 grid grid-cols-2 gap-2 max-w-lg">
                  {[
                    'What happened in Taiwan this week?',
                    'How many articles about China in the last 30 days?',
                    'What are the strongest trade signals?',
                    'How has the Ukraine narrative evolved?',
                  ].map((suggestion) => (
                    <button
                      key={suggestion}
                      onClick={() => sendMessage(suggestion)}
                      className="text-left text-xs p-3 rounded-lg border border-white/10 text-gray-400 hover:text-white hover:border-[#FF6B35]/40 transition-all bg-white/5"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((msg) =>
              msg.role === 'user' ? (
                <UserBubble key={msg.id} msg={msg} />
              ) : (
                <AssistantBubble key={msg.id} msg={msg} />
              )
            )}

            {isLoading && <TypingIndicator />}

            {error && (
              <div className="text-center text-red-400 text-sm py-2 px-4 rounded-lg bg-red-500/10 border border-red-500/20 mb-4">
                {error}
              </div>
            )}

            <div ref={bottomRef} />
          </div>

          {/* Input area */}
          <div className="border-t border-white/10 px-6 py-4">
            <div className="flex gap-3 items-end">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask an intelligence question... (Enter to send, Shift+Enter for newline)"
                rows={2}
                className="flex-1 bg-[#1a2a4a] border border-white/10 rounded-xl px-4 py-3 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-[#FF6B35]/50 resize-none"
              />
              <button
                onClick={handleSend}
                disabled={!input.trim() || isLoading}
                className="px-5 py-3 rounded-xl bg-[#FF6B35] text-white font-medium text-sm hover:bg-[#FF6B35]/80 disabled:opacity-40 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
              >
                Send
              </button>
            </div>
          </div>
        </div>

        {/* Sources sidebar */}
        <div className="w-80 border-l border-white/10 flex flex-col overflow-hidden hidden md:flex">
          <div className="px-4 py-3 border-b border-white/10">
            <h3 className="text-sm font-semibold text-white/70">Sources & Analysis</h3>
          </div>
          <div className="flex-1 overflow-y-auto px-4 py-3">
            {lastAssistantMessage?.query_plan && (
              <div className="mb-4">
                <div className="text-xs text-gray-500 mb-2 uppercase tracking-wider">Query Plan</div>
                <QueryPlanBadges plan={lastAssistantMessage.query_plan} />
                {typeof lastAssistantMessage.metadata?.execution_time === 'number' && (
                  <div className="text-xs text-gray-500 mt-1">
                    Executed in {(lastAssistantMessage.metadata.execution_time as number).toFixed(1)}s
                  </div>
                )}
              </div>
            )}

            {lastAssistantMessage?.sources && lastAssistantMessage.sources.length > 0 && (
              <div>
                <div className="text-xs text-gray-500 mb-2 uppercase tracking-wider">
                  Sources ({lastAssistantMessage.sources.length})
                </div>
                {lastAssistantMessage.sources.map((src, i) => (
                  <SourceCard key={i} source={src} />
                ))}
              </div>
            )}

            {!lastAssistantMessage && (
              <div className="text-center text-gray-600 text-xs pt-8">
                Sources and query plan will appear here after the first response.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
