'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import DOMPurify from 'isomorphic-dompurify';
import type { Components } from 'react-markdown';

// Strip dangerous HTML from the Markdown source string before parsing.
// react-markdown (without rehype-raw) already ignores raw HTML, but this
// provides defense-in-depth if rehype-raw is ever added, and removes scripts
// that could survive future rendering changes.
function sanitize(md: string): string {
  return DOMPurify.sanitize(md, {
    FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed', 'base', 'form', 'input'],
    FORBID_ATTR: ['onerror', 'onload', 'onclick', 'onmouseover', 'onfocus', 'onblur'],
  });
}

const defaultComponents: Components = {
  p: ({ children }) => (
    <p className="mb-3 last:mb-0 leading-relaxed">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="list-disc list-inside mb-3 space-y-1 pl-1">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal list-inside mb-3 space-y-1 pl-1">{children}</ol>
  ),
  li: ({ children }) => (
    <li className="leading-relaxed">{children}</li>
  ),
  strong: ({ children }) => (
    <strong className="font-semibold text-white">{children}</strong>
  ),
  em: ({ children }) => (
    <em className="italic text-gray-300">{children}</em>
  ),
  h1: ({ children }) => (
    <h1 className="text-lg font-bold text-[#FF6B35] mb-3 mt-5 first:mt-0">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="text-base font-semibold text-[#FF6B35] mb-2 mt-4 first:mt-0">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-sm font-semibold text-[#00A8E8] mb-1 mt-3 first:mt-0">{children}</h3>
  ),
  h4: ({ children }) => (
    <h4 className="text-sm font-semibold text-gray-200 mb-1 mt-3 first:mt-0">{children}</h4>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-[#FF6B35]/50 pl-3 text-gray-400 italic my-3">
      {children}
    </blockquote>
  ),
  code: ({ children, className }) => (
    <code className={`bg-black/30 px-1.5 py-0.5 rounded text-xs text-[#00A8E8] font-mono ${className ?? ''}`}>
      {children}
    </code>
  ),
  pre: ({ children }) => (
    <pre className="bg-black/30 rounded-lg p-4 overflow-x-auto mb-3 text-xs text-gray-300 font-mono">
      {children}
    </pre>
  ),
  hr: () => (
    <hr className="border-white/10 my-4" />
  ),
  // ── GFM Tables ───────────────────────────────────────────────────────────────
  table: ({ children }) => (
    <div className="overflow-x-auto my-4 rounded-lg border border-white/10">
      <table className="min-w-full border-collapse text-sm">
        {children}
      </table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="bg-white/5 border-b border-white/15">
      {children}
    </thead>
  ),
  tbody: ({ children }) => (
    <tbody className="divide-y divide-white/8">
      {children}
    </tbody>
  ),
  tr: ({ children }) => (
    <tr className="hover:bg-white/3 transition-colors">
      {children}
    </tr>
  ),
  th: ({ children }) => (
    <th className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wider text-[#FF6B35] whitespace-nowrap">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="px-4 py-2.5 text-gray-300 align-top">
      {children}
    </td>
  ),
};

interface MarkdownContentProps {
  content: string;
  /** Extra className on the outer wrapper div */
  className?: string;
  /** Component overrides that merge with (and take precedence over) defaults */
  components?: Components;
  /**
   * `editorial` switches long-form report bodies to the serif reading treatment: measure
   * capped at 68ch, 1.7 line-height, larger size. Opt-in rather than the default because the
   * same renderer draws short UI fragments (previews, summaries) where a 68ch measure and a
   * serif face would both be wrong. Metadata and citations stay in Geist Mono either way —
   * this only affects the prose flow.
   */
  variant?: 'default' | 'editorial';
}

const VARIANT_CLASSES: Record<'default' | 'editorial', string> = {
  default: 'text-sm leading-relaxed',
  editorial: 'max-w-[68ch] font-serif text-[1.0625rem] leading-[1.7]',
};

/**
 * Renders LLM-generated Markdown content with:
 * - GFM table support (remark-gfm)
 * - DOMPurify sanitization on the Markdown source
 * - Dark tactical theme matching the rest of the platform
 */
export function MarkdownContent({
  content,
  className,
  components,
  variant = 'default',
}: MarkdownContentProps) {
  const safe = sanitize(content ?? '');

  return (
    <div className={`${VARIANT_CLASSES[variant]} ${className ?? ''}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{ ...defaultComponents, ...components }}
      >
        {safe}
      </ReactMarkdown>
    </div>
  );
}
