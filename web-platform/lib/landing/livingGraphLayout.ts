/**
 * SCENE 2 — THE LIVING GRAPH — deterministic layout from real `/api/v1/stories/graph` data.
 *
 * Per design.md's "Implementazione comune alle tre scene": positions are precomputed
 * once (a pure function of the input data), never simulated frame-to-frame — same
 * reasoning as Scene 1's seeded keyframes, just parameterized by live data instead of a
 * fixed seed. A live force simulation (d3-force) would make the replay resize-unstable
 * and non-reversible; a fixed radial-cluster layout doesn't.
 *
 * Rendering unit is the community, not the individual node (see design.md revision after
 * user review — a scatter of 400+ individually-drawn dots reads like the real analyst
 * tool at `/stories`, not a landing-page hero). Each community becomes a glowing cluster
 * ("nebula") that visibly grows as its storylines are born; individual storyline points
 * still render inside it for texture/honesty ("evidence over assertion" — it's real
 * granular data, not an abstract blob). Cross-community edges are aggregated into a
 * handful of "flows" so the connective tissue reads as a few flowing streams between
 * clusters, not hundreds of overlapping straight lines.
 *
 * Declared limitation (design.md): `storyline_edges` has no per-edge timestamp, so an
 * edge's/flow's "birth" is approximated from its endpoints' birth — an honest
 * reconstruction from real data, not a simulation. Copy near the scene must not claim
 * exact edge chronology (see AGENT.md task 3.10 / spec scenario).
 */

import { communityColor } from '@/lib/communityColors';
import { hexToRgb, type RGB } from './nebulaRender';
import type { LiveGraphData } from './live';

// Re-exported so existing `import { type RGB } from './livingGraphLayout'` sites keep
// working; `nebulaRender` is the canonical declaration (shared with Scene 1).
export type { RGB };

// Replay window: storylines older than this are treated as "already existing" at the
// start of the loop (bornT = 0) rather than pushing the timeline out indefinitely for
// long-stabilized storylines. Matches design.md's "ultimi 30 giorni" framing.
export const WINDOW_DAYS = 30;

// Fraction of storylines that fade back out mid-hold instead of persisting until the
// loop-wide fade — "some appear, some disappear" rather than pure accumulation.
const DEATH_FRACTION = 0.35;
export const DEATH_FADE_BAND = 0.12;
export const NODE_FADE_BAND = 0.05;

function clampUnit(v: number): number {
  return Math.max(0, Math.min(1, v));
}

/**
 * A node's visibility (0..1) at a given point in the replay: `growT` drives birth (same
 * as before), `holdProgress` (0..1 across the hold sub-phase only) drives the optional
 * early death for the ~35% of storylines that have one. Multiplying the two means a node
 * that dies always finishes its birth fade-in first, then fades back out later.
 */
export function nodeReveal(node: { bornT: number; dieT: number | null }, growT: number, holdProgress: number): number {
  const birth = clampUnit((growT - node.bornT) / NODE_FADE_BAND);
  if (node.dieT == null) return birth;
  const death = 1 - clampUnit((holdProgress - node.dieT) / DEATH_FADE_BAND);
  return birth * death;
}

export type LayoutNode = {
  id: number;
  title: string;
  x: number; // normalized 0..1
  y: number; // normalized 0..1
  radius: number; // normalized, fraction of min(width, height) — small point within its cluster
  momentum: number;
  color: RGB;
  articleCount: number;
  /** Replay progress (0..1) at which this storyline appears — independent of scroll. */
  bornT: number;
  /** Hold-phase progress (0..1) at which this storyline fades back out before the loop's
   *  collective fade — null means it persists once born. Only a minority die early; most
   *  simply stay until the loop-wide fade. Keeps the hold phase from reading as one static
   *  freeze-frame — the graph keeps changing, not just accumulating. */
  dieT: number | null;
  isLoneStar: boolean;
  clusterKey: string;
};

export type LayoutEdge = {
  source: number;
  target: number;
  weight: number;
  bornT: number;
};

export type ClusterBlob = {
  key: string;
  cx: number;
  cy: number;
  color: RGB;
  memberIds: number[];
  totalArticles: number;
  /** Normalized radius once fully grown (all members born). */
  maxRadius: number;
  /** Extra soft-glow lobes (seeded jitter) so the cluster reads as a hazy nebula, not a perfect disc. */
  puffs: readonly { dx: number; dy: number; r: number }[];
  isLoneStar: boolean;
};

export type ClusterFlow = {
  a: string;
  b: string;
  weight: number;
  bornT: number;
  /** Signed perpendicular bow for the connecting curve — seeded, keeps flows from overlapping as straight lines. */
  bow: number;
};

export type GraphLayout = {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  clusters: ClusterBlob[];
  flows: ClusterFlow[];
};

function mulberry32(seed: number) {
  let a = seed;
  return function random() {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function clamp01(v: number): number {
  return Math.max(0.03, Math.min(0.97, v));
}

// Alphabetical order would put every "c0" cluster at the same fixed angle and clump all
// singleton ("s...") clusters together at the end — a seeded shuffle spreads angular
// position evenly instead, deterministic but decorrelated from the key string itself.
function seededShuffle<T>(items: T[], seed: number): T[] {
  const rnd = mulberry32(seed);
  const out = [...items];
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rnd() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

export function computeGraphLayout(graph: LiveGraphData): GraphLayout {
  const { storylines, edges } = graph;
  if (storylines.length === 0) return { nodes: [], edges: [], clusters: [], flows: [] };

  // Group by community — null community_id (isolated storylines, incl. "lone stars") each
  // get their own single-member bucket, same treatment communityColor() already gives
  // them (SINGLETON_COLOR) elsewhere in the app.
  const groups = new Map<string, typeof storylines>();
  for (const s of storylines) {
    const key = s.communityId != null ? `c${s.communityId}` : `s${s.id}`;
    const bucket = groups.get(key);
    if (bucket) bucket.push(s);
    else groups.set(key, [s]);
  }
  const groupKeys = seededShuffle(Array.from(groups.keys()).sort(), 424242);

  const counts = storylines.map((s) => s.articleCount);
  const minCount = Math.min(...counts);
  const maxCount = Math.max(...counts, minCount + 1);
  const MIN_R = 0.006;
  const MAX_R = 0.016;

  const nodeById = new Map<number, LayoutNode>();
  const clusters: ClusterBlob[] = [];

  groupKeys.forEach((key, gi) => {
    const members = [...groups.get(key)!].sort((a, b) => b.momentumScore - a.momentumScore);
    const groupAngle = (gi / groupKeys.length) * Math.PI * 2;
    // Bigger clusters sit further out — keeps small/lone-star groups from crowding the center.
    const groupRadius = 0.16 + Math.min(0.28, members.length * 0.028);
    const cx = 0.5 + Math.cos(groupAngle) * groupRadius;
    const cy = 0.5 + Math.sin(groupAngle) * groupRadius * 0.68; // flatten for a wide frame

    const rnd = mulberry32(gi * 7919 + 13);
    let totalArticles = 0;
    members.forEach((s, mi) => {
      const spread = 0.03 + Math.min(0.07, members.length * 0.008);
      const a = (mi / Math.max(1, members.length)) * Math.PI * 2 + rnd() * 0.6;
      const r = spread * Math.sqrt((mi + rnd() * 0.4) / Math.max(1, members.length));
      const x = clamp01(cx + Math.cos(a) * r);
      const y = clamp01(cy + Math.sin(a) * r);

      const sizeT = (s.articleCount - minCount) / (maxCount - minCount);
      const radius = MIN_R + sizeT * (MAX_R - MIN_R);

      const cappedDays = Math.min(Math.max(s.daysActive ?? WINDOW_DAYS, 0), WINDOW_DAYS);
      const bornT = (WINDOW_DAYS - cappedDays) / WINDOW_DAYS;
      totalArticles += s.articleCount;

      const dieT = rnd() < DEATH_FRACTION ? 0.15 + rnd() * 0.65 : null;

      nodeById.set(s.id, {
        id: s.id,
        title: s.title,
        x,
        y,
        radius,
        momentum: s.momentumScore,
        color: hexToRgb(communityColor(s.communityId)),
        articleCount: s.articleCount,
        bornT,
        dieT,
        isLoneStar: false, // corrected below once edges are known
        clusterKey: key,
      });
    });

    const isSingleton = members.length === 1;
    const maxRadius = isSingleton
      ? 0.014 + Math.sqrt(members[0].articleCount) * 0.0016
      : 0.05 + Math.sqrt(totalArticles) * 0.0075;
    const puffCount = isSingleton ? 0 : 2 + Math.floor(rnd() * 2);
    const puffs = Array.from({ length: puffCount }, () => ({
      dx: (rnd() - 0.5) * maxRadius * 0.9,
      dy: (rnd() - 0.5) * maxRadius * 0.9,
      r: maxRadius * (0.5 + rnd() * 0.35),
    }));

    clusters.push({
      key,
      cx,
      cy,
      color: hexToRgb(communityColor(members[0].communityId)),
      memberIds: members.map((m) => m.id),
      totalArticles,
      maxRadius,
      puffs,
      isLoneStar: isSingleton,
    });
  });

  const connected = new Set<number>();
  const layoutEdges: LayoutEdge[] = [];
  for (const e of edges) {
    const a = nodeById.get(e.source);
    const b = nodeById.get(e.target);
    if (!a || !b) continue; // defensive — backend already keeps both endpoints in `nodes`
    connected.add(e.source);
    connected.add(e.target);
    layoutEdges.push({ source: e.source, target: e.target, weight: e.weight, bornT: Math.max(a.bornT, b.bornT) });
  }
  for (const n of nodeById.values()) {
    n.isLoneStar = !connected.has(n.id);
  }

  // Aggregate cross-cluster edges into a small number of flows — the visual "connective
  // tissue" between nebulas. Intra-cluster edges are already implied by the blob itself.
  const flowMap = new Map<string, { weight: number; bornT: number }>();
  for (const e of layoutEdges) {
    const a = nodeById.get(e.source)!;
    const b = nodeById.get(e.target)!;
    if (a.clusterKey === b.clusterKey) continue;
    const [ka, kb] = [a.clusterKey, b.clusterKey].sort();
    const flowKey = `${ka}|${kb}`;
    const existing = flowMap.get(flowKey);
    if (existing) {
      existing.weight += e.weight;
      existing.bornT = Math.min(existing.bornT, e.bornT);
    } else {
      flowMap.set(flowKey, { weight: e.weight, bornT: e.bornT });
    }
  }
  const flows: ClusterFlow[] = Array.from(flowMap.entries()).map(([flowKey, v], i) => {
    const [ka, kb] = flowKey.split('|');
    const rnd = mulberry32(i * 104729 + 7);
    // `ka` is only the alphabetically-first key (an artifact of dedup canonicalization
    // above) — without this, every flow would animate "from" whichever cluster happens
    // to sort first, which visually reads as everything radiating from one fixed cluster.
    // The underlying relationship (weighted Jaccard) isn't directional anyway, so which
    // end the particle starts from is picked per-flow instead of inherited from the sort.
    const reversed = rnd() < 0.5;
    const a = reversed ? kb : ka;
    const b = reversed ? ka : kb;
    return { a, b, weight: v.weight, bornT: v.bornT, bow: (rnd() - 0.5) * 0.6 };
  });

  return { nodes: Array.from(nodeById.values()), edges: layoutEdges, clusters, flows };
}
