/**
 * Dev-only fixture for the Scene 2 prototype route (`app/dev/living-graph/`).
 *
 * No local Postgres/backend is reachable in this environment, so this stands in for
 * `getLiveGraphData()`'s real response — same `LiveGraphData` shape, generated with a
 * seeded PRNG so it's reproducible. NOT imported by `app/page.tsx`; the real landing page
 * calls `getLiveGraphData()` directly and gets actual `/api/v1/stories/graph` data.
 */

import type { LiveGraphData, LiveGraphEdge, LiveStoryline } from './live';

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

export function buildLivingGraphFixture(seed = 20260722): LiveGraphData {
  const rnd = mulberry32(seed);
  const storylines: LiveStoryline[] = [];
  const edges: LiveGraphEdge[] = [];
  let id = 1;

  const COMMUNITY_COUNT = 11;
  const communityMemberIds: number[][] = [];
  for (let c = 0; c < COMMUNITY_COUNT; c++) {
    const size = 2 + Math.floor(rnd() * 7); // 2-8 members
    const memberIds: number[] = [];
    for (let m = 0; m < size; m++) {
      const thisId = id++;
      memberIds.push(thisId);
      storylines.push({
        id: thisId,
        title: `Storyline ${thisId}`,
        category: null,
        momentumScore: Math.round((0.15 + rnd() * 0.75) * 1000) / 1000,
        communityId: c,
        // Placeholder rather than an invented topic name: the fixture drives the isolated
        // /dev prototype, and a fabricated label there would end up read as a real one.
        communityName: `Community ${c}`,
        articleCount: 3 + Math.floor(rnd() * 40),
        startDate: null,
        daysActive: Math.floor(rnd() * 34),
      });
    }
    communityMemberIds.push(memberIds);
    // Chain edges within the community, plus a few extra random pairs for density.
    for (let m = 1; m < memberIds.length; m++) {
      edges.push({ source: memberIds[m - 1], target: memberIds[m], weight: 0.12 + rnd() * 0.45 });
    }
    const extra = Math.floor(rnd() * size * 0.5);
    for (let k = 0; k < extra; k++) {
      const a = memberIds[Math.floor(rnd() * memberIds.length)];
      const b = memberIds[Math.floor(rnd() * memberIds.length)];
      if (a !== b) edges.push({ source: a, target: b, weight: 0.1 + rnd() * 0.3 });
    }
  }

  // Cross-community edges — weaker links between otherwise-separate storylines (e.g. a
  // shared entity or event that touches two narratives). Real graphs have these too; the
  // Scene 2 renderer aggregates them into "flows" connecting nebula clusters.
  const CROSS_EDGE_COUNT = 14;
  for (let k = 0; k < CROSS_EDGE_COUNT; k++) {
    const ca = Math.floor(rnd() * COMMUNITY_COUNT);
    let cb = Math.floor(rnd() * COMMUNITY_COUNT);
    if (cb === ca) cb = (cb + 1) % COMMUNITY_COUNT;
    const a = communityMemberIds[ca][Math.floor(rnd() * communityMemberIds[ca].length)];
    const b = communityMemberIds[cb][Math.floor(rnd() * communityMemberIds[cb].length)];
    edges.push({ source: a, target: b, weight: 0.1 + rnd() * 0.2 });
  }

  // Lone stars — high momentum, no edges, exactly the case the backend keeps visible
  // despite having no edge above the weight threshold (HIGH_MOMENTUM_THRESHOLD = 0.4).
  const LONE_STAR_COUNT = 7;
  for (let i = 0; i < LONE_STAR_COUNT; i++) {
    const thisId = id++;
    storylines.push({
      id: thisId,
      title: `Lone Star ${thisId}`,
      category: null,
      momentumScore: Math.round((0.4 + rnd() * 0.55) * 1000) / 1000,
      communityId: null,
      communityName: null,
      articleCount: 5 + Math.floor(rnd() * 50),
      startDate: null,
      daysActive: Math.floor(rnd() * 34),
    });
  }

  return {
    storylines,
    edges,
    totalActive: storylines.length,
    totalEdges: edges.length,
    generatedAt: new Date().toISOString(),
  };
}
