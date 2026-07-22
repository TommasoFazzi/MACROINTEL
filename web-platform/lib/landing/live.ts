import { SIGNALS, type Signal } from './data';

/**
 * Server-only live data for the landing page (RSC fetches, never client-side —
 * see frontend-motion-system/landing-live-data specs, D8). `/stories/graph` is
 * an authenticated endpoint (verify_api_key in src/api/auth.py): the X-API-Key
 * header is attached here, server-side, exactly like app/api/proxy/[...path]/route.ts
 * does — it must never reach the browser.
 */

const API_URL = process.env.INTELLIGENCE_API_URL || 'http://localhost:8000';
const API_KEY = process.env.INTELLIGENCE_API_KEY || '';
// Bounds the worst case when the backend is fully unreachable: LandingPage()
// awaits both fetches before streaming any HTML, so a long timeout here would
// directly delay TTFB/LCP. In steady state this never matters — Next's fetch
// cache (revalidate below) serves warm requests in a few ms.
const FETCH_TIMEOUT_MS = 2500;
const REVALIDATE_SECONDS = 900; // pipeline runs once/day — 15min is already generous

export type LiveStoryline = {
  id: number;
  title: string;
  category: string | null;
  momentumScore: number;
  communityId: number | null;
  /** LLM-generated community label (migration 022, written by compute_communities.py).
   *  Consumed by Scene 1's closing act — see `topCommunityNames` below. */
  communityName: string | null;
  articleCount: number;
  /** ISO date the storyline was first detected — drives Scene 2's birth-order replay. */
  startDate: string | null;
  daysActive: number | null;
};

export type LiveGraphEdge = {
  source: number;
  target: number;
  /** TF-IDF weighted Jaccard between the two storylines' entity sets. */
  weight: number;
};

export type LiveGraphData = {
  storylines: LiveStoryline[];
  edges: LiveGraphEdge[];
  totalActive: number;
  totalEdges: number;
  /** ISO timestamp of when this response was served, refreshed at most every REVALIDATE_SECONDS. */
  generatedAt: string | null;
};

export type LiveBriefing = {
  slug: string;
  title: string;
  summaryPreview: string;
  publishedAt: string | null;
  reportType: string | null;
} | null;

async function fetchJson(
  path: string,
  headers: Record<string, string>
): Promise<Record<string, unknown> | null> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(`${API_URL}${path}`, {
      headers,
      signal: controller.signal,
      next: { revalidate: REVALIDATE_SECONDS },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  } finally {
    clearTimeout(timeoutId);
  }
}

/** Active storylines (emerging/active/stabilized), momentum-ordered, "lone stars" included. */
export async function getLiveGraphData(): Promise<LiveGraphData> {
  const json = await fetchJson('/api/v1/stories/graph', {
    ...(API_KEY && { 'X-API-Key': API_KEY }),
  });

  const data = json?.data as
    | { nodes?: unknown[]; links?: unknown[]; stats?: { total_edges?: number } }
    | undefined;
  const nodes = data?.nodes;
  if (!Array.isArray(nodes) || nodes.length === 0) {
    return { storylines: [], edges: [], totalActive: 0, totalEdges: 0, generatedAt: null };
  }

  const storylines: LiveStoryline[] = nodes
    .map((n) => {
      const node = n as Record<string, unknown>;
      return {
        id: node.id as number,
        title: node.title as string,
        category: (node.category as string | null) ?? null,
        momentumScore: (node.momentum_score as number) ?? 0,
        communityId: (node.community_id as number | null) ?? null,
        communityName: (node.community_name as string | null) ?? null,
        articleCount: (node.article_count as number) ?? 0,
        startDate: (node.start_date as string | null) ?? null,
        daysActive: (node.days_active as number | null) ?? null,
      };
    })
    .sort((a, b) => b.momentumScore - a.momentumScore);

  const edges: LiveGraphEdge[] = (Array.isArray(data?.links) ? data.links : [])
    .map((e) => {
      const edge = e as Record<string, unknown>;
      return {
        source: edge.source as number,
        target: edge.target as number,
        weight: (edge.weight as number) ?? 0,
      };
    });

  return {
    storylines,
    edges,
    totalActive: storylines.length,
    totalEdges: data?.stats?.total_edges ?? edges.length,
    generatedAt: typeof json?.generated_at === 'string' ? (json.generated_at as string) : null,
  };
}

/**
 * Distinct community labels, largest community first.
 *
 * Feeds the labels of Scene 1's closing act. Returns `[]` whenever the graph is unavailable
 * or the communities have never been named — the scene then draws the shape unlabelled,
 * which is the intended degradation: inventing plausible community names would be exactly
 * the fabricated-content trap the rest of the landing page avoids.
 */
export function topCommunityNames(graph: LiveGraphData, limit = 6): string[] {
  const byCommunity = new Map<number, { name: string; articles: number }>();
  for (const s of graph.storylines) {
    if (s.communityId == null || !s.communityName) continue;
    const entry = byCommunity.get(s.communityId);
    if (entry) entry.articles += s.articleCount;
    else byCommunity.set(s.communityId, { name: s.communityName, articles: s.articleCount });
  }
  return Array.from(byCommunity.values())
    .sort((a, b) => b.articles - a.articles)
    .slice(0, limit)
    // Canvas labels sit above their nebula with no wrapping — a long LLM-generated name
    // would collide with its neighbours.
    .map((e) => (e.name.length > 28 ? `${e.name.slice(0, 27)}…` : e.name));
}

/** Most recently published public briefing, for DemoBriefing. Null if none exist. */
export async function getLiveBriefing(): Promise<LiveBriefing> {
  const json = await fetchJson('/api/v1/insights?limit=1', {});
  const insight = (json?.insights as Record<string, unknown>[] | undefined)?.[0];
  if (!insight) return null;

  return {
    slug: insight.slug as string,
    title: insight.title as string,
    summaryPreview: (insight.summary_preview as string) ?? '',
    publishedAt: (insight.published_at as string | null) ?? null,
    reportType: (insight.report_type as string | null) ?? null,
  };
}

/** Deterministic fallback ticker items — used verbatim when the live fetch fails or is empty. */
export function fallbackSignals(): Signal[] {
  return SIGNALS;
}
