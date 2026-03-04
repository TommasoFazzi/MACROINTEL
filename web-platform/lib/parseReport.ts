/**
 * Parses an intelligence report markdown into structured sections,
 * market tickers, and a navigable table of contents.
 */

// ── Types ──────────────────────────────────────────────────────────────

export interface MarketTicker {
  symbol: string;
  value: string;
  emoji: string;         // 📉 📈 🟢 🔴 🟠 ✅ etc.
  label: string;         // "Supply Easing", "Calm", etc.
  sentiment: 'positive' | 'negative' | 'neutral';
}

export interface MacroDashboard {
  date: string;
  tickers: MarketTicker[];
  riskRegime: string;        // "RISK_ON" | "RISK_OFF" | "MIXED"
  narrative: string;         // 3-4 sentence macro narrative
  keyDivergences: string[];
  watchItems: string[];
}

export interface TOCEntry {
  id: string;
  title: string;
  level: number;       // 2 = H2, 3 = H3
  children: TOCEntry[];
}

export interface ReportSection {
  id: string;
  title: string;
  level: number;
  content: string;         // raw markdown of this section (excluding subsections)
  children: ReportSection[];
}

export interface ParsedReport {
  title: string;
  macro: MacroDashboard | null;
  toc: TOCEntry[];
  sections: ReportSection[];
  bodyMarkdown: string;    // full report text after macro dashboard is stripped
}

// ── Helpers ────────────────────────────────────────────────────────────

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .trim();
}

function detectSentiment(emoji: string, label: string): 'positive' | 'negative' | 'neutral' {
  const pos = ['📈', '🟢', '✅', '🔵'];
  const neg = ['📉', '🔴', '⚠️', '🟠'];
  if (pos.some(e => emoji.includes(e))) return 'positive';
  if (neg.some(e => emoji.includes(e))) return 'negative';
  // check label
  const lowerLabel = label.toLowerCase();
  if (/calm|growth|signal|no stress|strong/i.test(lowerLabel)) return 'positive';
  if (/elevated|weakness|easing|hawkish|stress/i.test(lowerLabel)) return 'negative';
  return 'neutral';
}

// ── Macro Dashboard Parser ─────────────────────────────────────────────

/**
 * Extracts the MACRO DASHBOARD block from the report markdown.
 * The macro block is typically between the title and the first `---`.
 *
 * Format:
 *   `SYMBOL: $VALUE (EMOJI Label)` | `SYMBOL: VALUE (EMOJI Label)` | ...
 */
function parseMacroDashboard(markdown: string): { macro: MacroDashboard | null; restMarkdown: string } {
  // Look for the MACRO DASHBOARD section
  const macroRegex = /\*?\*?MACRO DASHBOARD\*?\*?\s*\(?[^)]*\)?\s*\n([\s\S]*?)(?=\n---|\n##\s)/i;
  const macroMatch = markdown.match(macroRegex);

  if (!macroMatch) {
    return { macro: null, restMarkdown: markdown };
  }

  const macroBlock = macroMatch[1];

  // Extract tickers: `SYMBOL: $VALUE (EMOJI Label)`
  const tickerRegex = /`([A-Z0-9_/]+):\s*([^(]+?)\s*\(([^\w\s]*)\s*([^)]+)\)`/g;
  const tickers: MarketTicker[] = [];
  let tickerMatch;
  while ((tickerMatch = tickerRegex.exec(macroBlock)) !== null) {
    const emoji = tickerMatch[3].trim();
    const label = tickerMatch[4].trim();
    tickers.push({
      symbol: tickerMatch[1],
      value: tickerMatch[2].trim(),
      emoji,
      label,
      sentiment: detectSentiment(emoji, label),
    });
  }

  // Extract risk regime
  const riskMatch = macroBlock.match(/Risk Regime:\s*(\w+)/i);
  const riskRegime = riskMatch ? riskMatch[1] : 'MIXED';

  // Extract narrative (paragraphs that aren't tickers/special lines)
  const lines = macroBlock.split('\n');
  const narrativeLines: string[] = [];
  const divergences: string[] = [];
  const watchItems: string[] = [];
  let collectingSection = '';

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('`')) continue;

    if (/key divergences?/i.test(trimmed)) {
      collectingSection = 'divergences';
      continue;
    }
    if (/watch/i.test(trimmed) && /[:]/i.test(trimmed)) {
      collectingSection = 'watch';
      continue;
    }
    if (/risk regime/i.test(trimmed)) continue;

    if (collectingSection === 'divergences' && trimmed.startsWith('-')) {
      divergences.push(trimmed.replace(/^-\s*/, ''));
    } else if (collectingSection === 'watch' && trimmed.startsWith('-')) {
      watchItems.push(trimmed.replace(/^-\s*/, ''));
    } else if (!collectingSection && !trimmed.startsWith('*Risk') && !trimmed.startsWith('|')) {
      narrativeLines.push(trimmed);
    }
  }

  // Extract date from the header line
  const dateMatch = markdown.match(/Daily Intelligence Briefing\s*-\s*([\d-]+)/i);
  const date = dateMatch ? dateMatch[1] : '';

  const macro: MacroDashboard = {
    date,
    tickers,
    riskRegime,
    narrative: narrativeLines.join(' '),
    keyDivergences: divergences,
    watchItems,
  };

  // Remove the macro block from the rest of the markdown
  const restMarkdown = markdown.replace(macroMatch[0], '').replace(/^---\s*$/m, '');

  return { macro, restMarkdown };
}

// ── Section Parser ─────────────────────────────────────────────────────

function parseSections(markdown: string): { sections: ReportSection[]; toc: TOCEntry[] } {
  const lines = markdown.split('\n');
  const sections: ReportSection[] = [];
  const toc: TOCEntry[] = [];

  let currentH2: ReportSection | null = null;
  let currentH3: ReportSection | null = null;
  let currentTocH2: TOCEntry | null = null;
  let contentBuffer: string[] = [];

  function flushContent() {
    const text = contentBuffer.join('\n').trim();
    if (currentH3) {
      currentH3.content = text;
    } else if (currentH2) {
      currentH2.content = text;
    }
    contentBuffer = [];
  }

  // If LLM used ### for top-level sections (no ## present), promote ### to H2
  const hasH2 = lines.some(l => /^##\s/.test(l) && !/^###\s/.test(l));

  for (const line of lines) {
    const h2Match = hasH2 ? line.match(/^##\s+(.+)/) : line.match(/^###\s+(.+)/);
    const h3Match = hasH2 ? line.match(/^###\s+(.+)/) : line.match(/^####\s+(.+)/);

    if (h2Match) {
      flushContent();

      // Save previous H3 if any
      if (currentH3 && currentH2) {
        currentH2.children.push(currentH3);
        currentH3 = null;
      }
      // Save previous H2
      if (currentH2) {
        sections.push(currentH2);
      }

      const title = h2Match[1].replace(/^\d+\.\s*/, '').trim();
      const id = slugify(title);

      currentH2 = { id, title, level: 2, content: '', children: [] };
      currentTocH2 = { id, title, level: 2, children: [] };
      toc.push(currentTocH2);
      currentH3 = null;
    } else if (h3Match) {
      flushContent();

      // Save previous H3
      if (currentH3 && currentH2) {
        currentH2.children.push(currentH3);
      }

      const title = h3Match[1].replace(/^\d+\.\s*/, '').trim();
      const id = slugify(title);

      currentH3 = { id, title, level: 3, content: '', children: [] };
      if (currentTocH2) {
        currentTocH2.children.push({ id, title, level: 3, children: [] });
      }
    } else {
      // Skip the H1 title line
      if (!line.match(/^#\s+/)) {
        contentBuffer.push(line);
      }
    }
  }

  // Flush remaining content
  flushContent();
  if (currentH3 && currentH2) {
    currentH2.children.push(currentH3);
  }
  if (currentH2) {
    sections.push(currentH2);
  }

  return { sections, toc };
}

// ── Main Parser ────────────────────────────────────────────────────────

export function parseReport(markdown: string): ParsedReport {
  if (!markdown) {
    return { title: '', macro: null, toc: [], sections: [], bodyMarkdown: '' };
  }

  // Extract title from first H1
  const titleMatch = markdown.match(/^#\s+(.+)/m);
  const title = titleMatch ? titleMatch[1].trim() : '';

  // Parse macro dashboard
  const { macro, restMarkdown } = parseMacroDashboard(markdown);

  // Parse sections and TOC
  const { sections, toc } = parseSections(restMarkdown);

  return {
    title,
    macro,
    toc,
    sections,
    bodyMarkdown: restMarkdown,
  };
}
