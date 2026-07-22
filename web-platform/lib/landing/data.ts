import { SOURCE_COUNT } from '@/lib/constants';

export type Signal = { dot: string; region: string; text: string };
export type SynthesisLevel = {
  id: 'daily' | 'weekly' | 'monthly';
  tag: string;
  tagColor: string;
  cadence: string;
  headline: string;
  /** What this level reads as input — the point being it's the level below, not raw articles. */
  reads: string;
  produces: string;
  /** Weekly/Monthly have no wired live artifact (only Daily does, via DemoBriefing) — shown
   *  as a labeled mechanism card instead of a fabricated report preview. */
  mechanismIcon: string;
  mechanismLabel: string;
};
export type Persona = { role: string; icon: string; desc: string };
export type Capability = { icon: string; title: string; body: string };

export const SIGNALS: Signal[] = [
  { dot: '#FF6B35', region: 'MIDDLE EAST', text: 'Hormuz Strait: maritime activity +34% — 3 corroborating sources' },
  { dot: '#ef4444', region: 'EAST ASIA', text: 'Taiwan Strait: PLA naval exercise detected — high confidence' },
  { dot: '#f59e0b', region: 'EUROPE', text: 'EU Defence: procurement contracts confirmed across 4 NATO members' },
  { dot: '#10b981', region: 'MACRO', text: 'Brent Crude: geopolitical premium widening — WATCHLIST signal' },
  { dot: '#00A8E8', region: 'CYBER', text: 'APT activity: infrastructure targeting correlated to diplomatic escalation' },
  { dot: '#FF6B35', region: 'AFRICA', text: 'Sahel corridor: armed group movements + logistics activity elevated' },
  { dot: '#8b5cf6', region: 'LATAM', text: 'Venezuela: capital outflow signals +19% — PBoC parallel response likely' },
  { dot: '#10b981', region: 'GLOBAL', text: 'Narrative Graph: community detection across active storylines — updated continuously' },
];

export const SYNTHESIS_LEVELS: SynthesisLevel[] = [
  {
    id: 'daily',
    tag: 'DAILY',
    tagColor: 'var(--data-6)',
    cadence: 'Every morning',
    headline: 'Intelligence delivered every morning.',
    reads: `Raw articles from ${SOURCE_COUNT} monitored sources, today's active storylines, and macro indicators.`,
    produces: 'A structured briefing — geopolitical, cyber, and macro signals, prioritized and cited.',
    mechanismIcon: '◈',
    mechanismLabel: 'Reads articles',
  },
  {
    id: 'weekly',
    tag: 'WEEKLY',
    tagColor: 'var(--data-3)',
    cadence: 'Every Sunday',
    headline: 'A week doesn’t read like seven days.',
    reads: 'The week’s daily briefings — not the underlying articles.',
    produces: 'A meta-analysis of how the trends moved across the week.',
    mechanismIcon: '◆',
    mechanismLabel: 'Reads daily reports',
  },
  {
    id: 'monthly',
    tag: 'MONTHLY RECAP',
    tagColor: 'var(--data-8)',
    cadence: 'After 4 weekly cycles',
    headline: 'The strategic layer, above the noise.',
    reads: 'The month’s weekly analyses — not the daily reports.',
    produces: 'A higher-level read on the underlying regime shift.',
    mechanismIcon: '◉',
    mechanismLabel: 'Reads weekly reports',
  },
];

export const PERSONAS: Persona[] = [
  { role: 'Geopolitical Analysts', icon: '◈', desc: 'Stop reading 50 RSS feeds manually. Distilled briefings from 40+ sources — every morning.' },
  { role: 'CISO & Security Teams', icon: '◉', desc: "Threat actors don't wait. Monitor escalations and cyber incidents before they become incidents." },
  { role: 'Macro Fund Managers', icon: '◆', desc: 'Geopolitical risk moves markets. Surface trade signals from raw intelligence — act on signal, not noise.' },
  { role: 'Investigative Journalists', icon: '◎', desc: 'Find the story before it breaks. Narrative tracking reveals storylines traditional tools miss.' },
];

export const CAPS: Capability[] = [
  { icon: '◈', title: 'Daily Intelligence Briefs', body: 'Automated daily and weekly reports. Geopolitical, cyber, and macro signals distilled while you sleep.' },
  { icon: '◉', title: '3-Layer Signal Filtering', body: 'Noise eliminated at ingestion, classification, and clustering — only what matters reaches your desk.' },
  { icon: '◆', title: 'Grounded AI Answers', body: 'Every ORACLE answer cites real sources. No hallucinations. Full traceability to the original article.' },
  { icon: '◎', title: 'Geospatial Intelligence Map', body: 'Entities plotted on an interactive tactical map with relationship arcs and live intelligence scoring.' },
  { icon: '●', title: 'Narrative Graph', body: 'Force-directed graph of active storylines. Community detection surfaces hidden clusters and emerging narratives automatically.' },
  { icon: '◐', title: 'REST API', body: 'Integrate MACROINTEL into your existing security stack or internal tooling via the documented API.' },
];
