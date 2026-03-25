'use client';

import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { OracleChatMessage, QueryPlan } from '../../types/oracle';

// ── Intent labels and colors ──────────────────────────────────────────────────

const INTENT_LABELS: Record<string, string> = {
  factual: 'Fattuale',
  analytical: 'Analitico',
  narrative: 'Narrativo',
  market: 'Mercato',
  comparative: 'Comparativo',
  overview: 'Panoramica',
};

const INTENT_COLORS: Record<string, string> = {
  factual: 'bg-blue-500/20 text-blue-300 border-blue-500/30',
  analytical: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/30',
  narrative: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
  market: 'bg-green-500/20 text-green-300 border-green-500/30',
  comparative: 'bg-pink-500/20 text-pink-300 border-pink-500/30',
  overview: 'bg-teal-500/20 text-teal-300 border-teal-500/30',
};

// ── Citation preprocessing ─────────────────────────────────────────────────────
// Converts [1], [2] in text to `__CITE__N__` code markers that the code component
// intercepts. The marker is deliberately unique and unambiguous — the LLM would never
// generate this string naturally (unlike `[[1]]` which could appear in code samples).

function preprocessCitations(text: string): string {
  return text.replace(/\[(\d+)\]/g, (_, n) => `\`__CITE__${n}__\``);
}

// ── Sub-components ────────────────────────────────────────────────────────────

function CitationBadge({ n, onClick }: { n: number; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center justify-center w-[18px] h-[18px] rounded text-[10px] font-bold bg-[#FF6B35]/20 text-[#FF6B35] border border-[#FF6B35]/40 hover:bg-[#FF6B35]/40 transition-colors cursor-pointer align-super mx-0.5 leading-none flex-shrink-0"
      title={`Vai alla fonte ${n}`}
    >
      {n}
    </button>
  );
}

function QueryPlanDetails({
  plan,
  executionTime,
}: {
  plan: QueryPlan;
  executionTime?: number;
}) {
  const [open, setOpen] = useState(false);
  const [subOpen, setSubOpen] = useState(false);

  return (
    <div className="mt-3 pt-2 border-t border-white/5">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 text-xs text-gray-600 hover:text-gray-400 transition-colors"
      >
        {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        <span>Analisi elaborazione</span>
      </button>

      {open && (
        <div className="mt-2 space-y-2 text-xs text-gray-600">
          {/* Badges */}
          <div className="flex flex-wrap gap-1.5">
            <span
              className={`px-1.5 py-0.5 rounded-full border text-xs ${
                INTENT_COLORS[plan.intent] ?? 'bg-gray-500/20 text-gray-300 border-gray-500/30'
              }`}
            >
              {INTENT_LABELS[plan.intent] ?? plan.intent}
            </span>
            <span className="px-1.5 py-0.5 rounded-full border bg-gray-500/10 text-gray-500 border-gray-500/20">
              {plan.complexity}
            </span>
            {executionTime !== undefined && (
              <span className="px-1.5 py-0.5 rounded-full border bg-gray-500/10 text-gray-500 border-gray-500/20">
                {executionTime.toFixed(1)}s
              </span>
            )}
          </div>

          {/* Tools */}
          {plan.tools.length > 0 && (
            <div className="text-gray-700">Strumenti: {plan.tools.join(', ')}</div>
          )}

          {/* Sub-queries (COMPARATIVE) */}
          {plan.sub_queries && plan.sub_queries.length > 0 && (
            <div>
              <button
                type="button"
                onClick={() => setSubOpen((o) => !o)}
                className="flex items-center gap-1 text-gray-600 hover:text-gray-400 transition-colors"
              >
                {subOpen ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                Decomposta in {plan.sub_queries.length} sotto-quer
                {plan.sub_queries.length === 1 ? 'y' : 'y'}
              </button>
              {subOpen && (
                <ul className="mt-1 pl-3 space-y-0.5 text-gray-700">
                  {plan.sub_queries.map((q, i) => (
                    <li key={i}>• {q}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* Execution steps */}
          {plan.execution_steps && plan.execution_steps.length > 0 && (
            <div className="space-y-0.5 text-gray-700">
              {plan.execution_steps.map((step, i) => (
                <div key={i}>
                  {i + 1}. {step.description}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Exported bubbles ──────────────────────────────────────────────────────────

export function UserBubble({ msg }: { msg: OracleChatMessage }) {
  return (
    <div className="flex justify-end mb-6 max-w-2xl mx-auto w-full">
      <div className="max-w-[75%] px-4 py-3 rounded-2xl rounded-tr-sm border border-white/10 bg-white/5 text-white/90 text-sm whitespace-pre-wrap leading-relaxed">
        {msg.content}
      </div>
    </div>
  );
}

export function AssistantBubble({
  msg,
  onCitationClick,
}: {
  msg: OracleChatMessage;
  onCitationClick: (n: number) => void;
}) {
  const isFollowUp = msg.metadata?.is_follow_up === true;
  const executionTime =
    typeof msg.metadata?.execution_time === 'number'
      ? (msg.metadata.execution_time as number)
      : undefined;
  const sourceCount = msg.sources?.length ?? 0;
  const processedContent = preprocessCitations(msg.content);

  return (
    <div className="flex justify-start mb-6 max-w-2xl mx-auto w-full">
      <div className="w-full">
        {/* Follow-up indicator */}
        {isFollowUp && (
          <div className="mb-2">
            <span className="text-xs px-2 py-0.5 rounded-full bg-white/5 text-gray-600 border border-white/8">
              ↩ Continuazione
            </span>
          </div>
        )}

        {/* Answer text */}
        <div className="text-white/90 text-sm leading-relaxed">
          <ReactMarkdown
            components={{
              p: ({ children }) => (
                <p className="mb-3 last:mb-0 leading-relaxed">{children}</p>
              ),
              ul: ({ children }) => (
                <ul className="list-disc list-inside mb-3 space-y-1">{children}</ul>
              ),
              ol: ({ children }) => (
                <ol className="list-decimal list-inside mb-3 space-y-1">{children}</ol>
              ),
              li: ({ children }) => (
                <li className="text-white/80">{children}</li>
              ),
              strong: ({ children }) => (
                <strong className="text-white font-semibold">{children}</strong>
              ),
              h1: ({ children }) => (
                <h1 className="text-lg font-bold text-[#FF6B35] mb-3 mt-4">{children}</h1>
              ),
              h2: ({ children }) => (
                <h2 className="text-base font-semibold text-[#FF6B35] mb-2 mt-4">
                  {children}
                </h2>
              ),
              h3: ({ children }) => (
                <h3 className="text-sm font-semibold text-[#00A8E8] mb-1 mt-3">
                  {children}
                </h3>
              ),
              code: ({ children, className }) => {
                // Intercept citation markers: `__CITE__N__`
                const str = String(children);
                const match = str.match(/^__CITE__(\d+)__$/);
                if (match) {
                  const n = parseInt(match[1], 10);
                  return <CitationBadge n={n} onClick={() => onCitationClick(n)} />;
                }
                return (
                  <code
                    className={`bg-black/30 px-1 rounded text-xs text-[#00A8E8] ${className ?? ''}`}
                  >
                    {children}
                  </code>
                );
              },
              blockquote: ({ children }) => (
                <blockquote className="border-l-2 border-[#FF6B35] pl-3 text-gray-400 italic my-3">
                  {children}
                </blockquote>
              ),
            }}
          >
            {processedContent}
          </ReactMarkdown>
        </div>

        {/* Footer: time + source count */}
        {(executionTime !== undefined || sourceCount > 0) && (
          <div className="mt-2 flex items-center gap-2 text-xs text-gray-600">
            {executionTime !== undefined && <span>{executionTime.toFixed(1)}s</span>}
            {executionTime !== undefined && sourceCount > 0 && <span>·</span>}
            {sourceCount > 0 && <span>{sourceCount} fonti</span>}
          </div>
        )}

        {/* Collapsible query plan */}
        {msg.query_plan && (
          <QueryPlanDetails plan={msg.query_plan} executionTime={executionTime} />
        )}
      </div>
    </div>
  );
}
