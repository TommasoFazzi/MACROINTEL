'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChevronDown } from 'lucide-react';
import type { ReportSection, TOCEntry, ParsedReport } from '@/lib/parseReport';
import type { ReportSource } from '@/types/dashboard';

// ── Table of Contents ──────────────────────────────────────────────────

interface TOCProps {
  entries: TOCEntry[];
  activeId: string;
  onNavigate: (id: string) => void;
}

export function TableOfContents({ entries, activeId, onNavigate }: TOCProps) {
  return (
    <nav className="sticky top-28 space-y-1">
      <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-widest mb-3">
        Contents
      </h3>
      {entries.map((entry) => (
        <div key={entry.id}>
          <button
            onClick={() => onNavigate(entry.id)}
            className={`block w-full text-left text-sm py-1.5 px-3 rounded-md transition-colors ${
              activeId === entry.id
                ? 'text-white bg-white/5 font-medium'
                : 'text-gray-500 hover:text-gray-300 hover:bg-white/[0.02]'
            }`}
          >
            {entry.title}
          </button>
          {entry.children.length > 0 && (
            <div className="ml-3 border-l border-white/5">
              {entry.children.map((child) => (
                <button
                  key={child.id}
                  onClick={() => onNavigate(child.id)}
                  className={`block w-full text-left text-xs py-1 px-3 transition-colors ${
                    activeId === child.id
                      ? 'text-gray-300 font-medium'
                      : 'text-gray-600 hover:text-gray-400'
                  }`}
                >
                  {child.title}
                </button>
              ))}
            </div>
          )}
        </div>
      ))}
    </nav>
  );
}

// ── Accordion Section ──────────────────────────────────────────────────

interface AccordionSectionProps {
  section: ReportSection;
  isOpen: boolean;
  onToggle: () => void;
  onHoverArticle: (articleIdx: number | null) => void;
}

function SectionContent({
  content,
  onHoverArticle,
}: {
  content: string;
  onHoverArticle: (articleIdx: number | null) => void;
}) {
  // Custom renderer that intercepts [Article N] references
  return (
    <div className="prose prose-invert prose-sm max-w-none
      prose-headings:text-white prose-headings:font-semibold
      prose-h4:text-base prose-h4:mt-4 prose-h4:mb-2
      prose-p:text-gray-300 prose-p:leading-relaxed
      prose-strong:text-white
      prose-em:text-gray-300
      prose-ul:text-gray-300 prose-ol:text-gray-300 prose-li:text-gray-300
      prose-a:text-[#00A8E8] prose-a:no-underline hover:prose-a:underline
      prose-blockquote:border-[#FF6B35]/50 prose-blockquote:text-gray-400
      prose-code:text-[#00A8E8] prose-code:bg-[#00A8E8]/10 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:text-sm
    ">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Intercept <a> elements produced by [Article N](url) links (Phase 2 linkification).
          // If the link text matches "Article N", render the styled badge with hover behavior
          // AND keep it as a real link pointing to the article URL.
          // For all other links, render normally.
          a: ({ href, children, ...props }) => {
            const text = typeof children === 'string' ? children : '';
            const articleMatch = text.match(/^Article\s+(\d+)$/i);
            if (articleMatch && href) {
              const idx = parseInt(articleMatch[1], 10);
              return (
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-mono bg-[#00A8E8]/10 text-[#00A8E8] cursor-pointer hover:bg-[#00A8E8]/20 transition-colors no-underline"
                  onMouseEnter={() => onHoverArticle(idx)}
                  onMouseLeave={() => onHoverArticle(null)}
                  {...props}
                >
                  {children}
                </a>
              );
            }
            return (
              <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
                {children}
              </a>
            );
          },
          // Intercept text nodes to find plain [Article N] references (no URL)
          p: ({ children, ...props }) => {
            return (
              <p {...props}>
                {processArticleRefs(children, onHoverArticle)}
              </p>
            );
          },
          li: ({ children, ...props }) => {
            return (
              <li {...props}>
                {processArticleRefs(children, onHoverArticle)}
              </li>
            );
          },
          table: ({ children }) => (
            <div className="overflow-x-auto my-4 rounded-lg border border-white/10">
              <table className="min-w-full border-collapse text-sm">{children}</table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="bg-white/5 border-b border-white/15">{children}</thead>
          ),
          tbody: ({ children }) => (
            <tbody className="divide-y divide-white/8">{children}</tbody>
          ),
          tr: ({ children }) => (
            <tr className="hover:bg-white/3 transition-colors">{children}</tr>
          ),
          th: ({ children }) => (
            <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wider text-[#FF6B35] whitespace-nowrap">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="px-4 py-2.5 text-gray-300 align-top">{children}</td>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

/**
 * Render a single article citation badge.
 * Used by processArticleRefs for both single and multiple citations.
 */
function ArticleBadge({
  idx,
  label,
  onHover,
}: {
  idx: number;
  label: string;
  onHover: (idx: number | null) => void;
}) {
  return (
    <span
      className="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-mono bg-[#00A8E8]/10 text-[#00A8E8] cursor-pointer hover:bg-[#00A8E8]/20 transition-colors"
      onMouseEnter={() => onHover(idx)}
      onMouseLeave={() => onHover(null)}
    >
      {label}
    </span>
  );
}

/**
 * Match patterns:
 *   [Article 1]
 *   [Article 1, 2, 3]          — numbers only after first article
 *   [Articles 1, 2]            — plural form
 *   [Article 1, Article 2]     — repeated keyword
 *   [Article 1][Article 2]     — back-to-back (caught by split)
 */
const ARTICLE_REF_REGEX = /(\[Articles?\s+(?:\d+(?:\s*,\s*(?:Article\s+)?\d+)*)\])/gi;

/** Replace [Article N] / [Article N, M] text with interactive hover elements */
function processArticleRefs(
  children: React.ReactNode,
  onHover: (idx: number | null) => void
): React.ReactNode {
  if (!children) return children;

  if (typeof children === 'string') {
    const parts = children.split(ARTICLE_REF_REGEX);
    if (parts.length === 1) return children;

    return parts.map((part, i) => {
      const isRef = /^\[Articles?\s+/i.test(part);
      if (!isRef) return part;

      const nums = Array.from(part.matchAll(/\d+/g), (m) => parseInt(m[0], 10));
      if (nums.length === 0) return part;

      // Single citation: render as one badge preserving original text
      if (nums.length === 1) {
        return (
          <ArticleBadge key={i} idx={nums[0]} label={part} onHover={onHover} />
        );
      }

      // Multiple citations: render one badge per number, space-separated
      return (
        <span key={i} className="inline-flex items-center gap-0.5">
          {nums.map((num, j) => (
            <ArticleBadge
              key={j}
              idx={num}
              label={`[Article ${num}]`}
              onHover={onHover}
            />
          ))}
        </span>
      );
    });
  }

  // For React elements, recurse into children arrays
  if (Array.isArray(children)) {
    return children.map((child, i) => {
      if (typeof child === 'string') {
        return <span key={i}>{processArticleRefs(child, onHover)}</span>;
      }
      return child;
    });
  }

  return children;
}

export function AccordionSection({
  section,
  isOpen,
  onToggle,
  onHoverArticle,
}: AccordionSectionProps) {
  const contentRef = useRef<HTMLDivElement>(null);

  return (
    <div
      id={section.id}
      className="rounded-xl border border-white/5 bg-white/[0.02] overflow-hidden scroll-mt-28"
    >
      {/* Header - always visible */}
      <button
        onClick={onToggle}
        className="flex items-center justify-between w-full px-5 py-4 text-left hover:bg-white/[0.02] transition-colors"
      >
        <h2 className="text-base font-semibold text-white">{section.title}</h2>
        <ChevronDown
          className={`w-4 h-4 text-gray-500 transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
        />
      </button>

      {/* Content - collapsible */}
      <div
        ref={contentRef}
        className={`overflow-hidden transition-all duration-300 ease-in-out ${
          isOpen ? 'max-h-[5000px] opacity-100' : 'max-h-0 opacity-0'
        }`}
      >
        <div className="px-5 pb-5 border-t border-white/5 pt-4">
          {/* Direct content of H2 */}
          {section.content && (
            <SectionContent content={section.content} onHoverArticle={onHoverArticle} />
          )}

          {/* Subsections (H3s) */}
          {section.children.map((sub) => (
            <div key={sub.id} id={sub.id} className="mt-6 first:mt-0 scroll-mt-28">
              <h3 className="text-sm font-semibold text-[#FF6B35] mb-3">{sub.title}</h3>
              <SectionContent content={sub.content} onHoverArticle={onHoverArticle} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Contextual Source Sidebar ──────────────────────────────────────────

interface SourcesSidebarProps {
  sources: ReportSource[];
  highlightedIdx: number | null;
}

export function SourcesSidebar({ sources, highlightedIdx }: SourcesSidebarProps) {
  const highlightRef = useRef<HTMLLIElement>(null);
  const [expandedBullets, setExpandedBullets] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (highlightedIdx !== null && highlightRef.current) {
      highlightRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [highlightedIdx]);

  const toggleBullets = (idx: number) => {
    setExpandedBullets((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) {
        next.delete(idx);
      } else {
        next.add(idx);
      }
      return next;
    });
  };

  if (sources.length === 0) return null;

  return (
    <div className="sticky top-28 rounded-xl border border-white/5 bg-white/[0.02] p-4 max-h-[calc(100vh-8rem)] overflow-y-auto scrollbar-thin scrollbar-thumb-white/10">
      <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-widest mb-3">
        Sources ({sources.length})
      </h3>
      <ul className="space-y-2">
        {sources.map((source, idx) => {
          const isHighlighted = highlightedIdx === idx + 1; // [Article 1] = index 0
          const hasBullets = source.bullet_points && source.bullet_points.length > 0;
          const isBulletsExpanded = expandedBullets.has(idx);

          return (
            <li
              key={`${source.article_id}-${idx}`}
              ref={isHighlighted ? highlightRef : null}
              className={`p-2.5 rounded-lg border text-xs transition-all duration-200 ${
                isHighlighted
                  ? 'border-[#00A8E8]/50 bg-[#00A8E8]/10 ring-1 ring-[#00A8E8]/30'
                  : 'border-white/5 bg-white/[0.01] hover:border-white/10'
              }`}
            >
              <span className="text-xs font-mono text-gray-600 mb-1 block">
                [{idx + 1}]
              </span>
              <p className="text-gray-300 line-clamp-2 leading-snug">{source.title}</p>
              <div className="flex items-center justify-between mt-1.5">
                {source.link ? (
                  <a
                    href={source.link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[#00A8E8] hover:underline"
                  >
                    Open source &rarr;
                  </a>
                ) : (
                  <span />
                )}
                {source.relevance_score != null && (
                  <span className="text-gray-600 font-mono">
                    {Math.round(source.relevance_score * 100)}%
                  </span>
                )}
              </div>

              {/* Bullet Points Section */}
              {hasBullets && (
                <div className="mt-2 pt-2 border-t border-white/5">
                  <button
                    type="button"
                    onClick={() => toggleBullets(idx)}
                    className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-300 transition-colors"
                  >
                    <ChevronDown
                      size={12}
                      className={`transition-transform ${
                        isBulletsExpanded ? 'rotate-180' : ''
                      }`}
                    />
                    <span>Key Points</span>
                  </button>

                  {isBulletsExpanded && (
                    <ul className="mt-1.5 space-y-1.5 ml-1">
                      {(source.bullet_points ?? []).map((bullet, bulletIdx) => (
                        <li
                          key={bulletIdx}
                          className="text-xs text-gray-400 leading-snug flex gap-2"
                        >
                          <span className="text-gray-600 flex-shrink-0">•</span>
                          <span>{bullet}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
