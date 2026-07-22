/**
 * SIGNAL DESCENT — deterministic keyframe generator.
 *
 * Maps onto the real daily pipeline (scripts/daily_pipeline.py) in seven acts:
 *
 *   1 SWARM     ingestion + relevance filter — 56 feeds arrive, a scan plane kills the noise
 *   2 COLLAPSE  _create_micro_clusters — near-dupes from different sources fuse into one event
 *   3 FATE      _find_best_match — an event joins a live storyline, or waits in the reservoir
 *   4 BIRTH     _cluster_residuals + _create_storyline_from_events — dense orphans become storylines
 *   5 WEB       _update_graph_connections — TF-IDF weighted Jaccard edges between storylines
 *   6 GRAVITY   compute_communities.py (Louvain) — the web contracts, communities emerge
 *   7 IGNITION  _name_community — communities light up and get their names; then they breathe
 *
 * Act 6 is drawn as the *edges* pulling the storylines together, never as attraction toward
 * pre-placed centers. That is not a stylistic choice: production runs Louvain, which has no
 * centroids at all — communities fall out of edge density. It also keeps the scene truthful
 * if the k-means-on-embedding challenger is ever promoted (see CLAUDE.md), since embeddings
 * encode the same semantics the edges do.
 *
 * Act 7 deliberately lands on the same picture as Scene 2 (the Hero's Living Graph): same
 * ring geometry, same radius scales, same palette, same `drawNebula` from `nebulaRender.ts`.
 * The page's payoff is the reader recognizing the shape they saw muted at the top, so any
 * drift between the two would cost the whole sequence its point.
 *
 * Everything here is pure data, computed once from a seeded PRNG — no DOM, no timers. The
 * renderer interpolates between keyframes as a pure function of scroll progress t ∈ [0,1]
 * (design.md D2): scrubbing must be reversible and resize-stable, which rules out any
 * time-integrated simulation.
 */

import { clusterCentre, clusterMemberOffset, dataColor, lerpColor, type RGB } from './nebulaRender';

export type { RGB };

export const N_PARTICLES = 460;
export const N_LANES = 8;

/**
 * Relative scroll budget per act — the "variable rhythm" the design calls for: transitions
 * (COLLAPSE, WEB) are quick beats, concepts the reader has to actually absorb (FATE,
 * GRAVITY, IGNITION) get room. Uniform pacing made every act feel equally important, which
 * flattened the two that carry the argument.
 */
const ACT_WEIGHTS = [6, 3, 8, 5, 3, 8, 9] as const;
export const N_ACTS = ACT_WEIGHTS.length;

/** Cumulative act boundaries in scroll space, [0, …, 1] — N_ACTS + 1 entries. */
export const ACT_BOUNDARIES: readonly number[] = (() => {
  const total = ACT_WEIGHTS.reduce((a, b) => a + b, 0);
  const out = [0];
  let acc = 0;
  for (const w of ACT_WEIGHTS) {
    acc += w;
    out.push(acc / total);
  }
  return out;
})();

/**
 * Keyframe times. One per act boundary, plus an extra one *inside* SWARM: the scan plane
 * needs a discrete "before / after" moment, and without an interior keyframe a particle
 * could only die at an act boundary — i.e. after the filter had visually already passed.
 */
const SCAN_FRACTION = 0.62; // where within SWARM the scan plane sits
export const KEYFRAME_TIMES: readonly number[] = [
  ACT_BOUNDARIES[0],
  ACT_BOUNDARIES[1] * SCAN_FRACTION,
  ...ACT_BOUNDARIES.slice(1),
];
const N_KEYFRAMES = KEYFRAME_TIMES.length;
const FLOATS_PER_FRAME = 7; // x, y, radius, alpha, r, g, b

/**
 * The scene is composed right-of-centre, not centred.
 *
 * The captions used to sit in a panel at the bottom of the viewport, directly under the
 * action — which meant almost nobody read them: the eye stays on the moving figure and the
 * text lives in peripheral vision. Moving the captions to a left-hand column at eye level
 * fixes that, but only if the graphic vacates the left third of the frame. Every x below is
 * biased accordingly; `COMPOSITION_LEFT` is the boundary the text column occupies.
 */
export const COMPOSITION_LEFT = 0.38;
/** Centre of the cluster ring, and of the scene's mass generally. */
const FOCUS_X = 0.65;

/** Normalized x of the scan plane — the renderer draws the plane here too. */
export const SCAN_X = 0.5;

export type EventOutcome = 'active' | 'stabilized' | 'archived';

export type ParticleScene = {
  /** [keyframeIndex * 7 + {x,y,radius,alpha,r,g,b}], normalized 0..1 coords + 0..255 color */
  frames: Float32Array;
  laneId: number;
  /** True for particles the relevance filter discards at the scan plane. */
  isNoise: boolean;
};

/** A storyline: the thing a knot of particles becomes at BIRTH and stays for the rest of the scene. */
export type SceneNode = {
  id: number;
  clusterId: number;
  /** Where it sits once born, before the graph exists (acts 4-5). */
  bx: number;
  by: number;
  /** Where the contracting web drags it (acts 6-7). */
  gx: number;
  gy: number;
  radius: number;
  articleCount: number;
  outcome: EventOutcome;
};

export type SceneEdge = {
  a: number;
  b: number;
  weight: number;
  /** 0..1 stagger inside the WEB act, so the network snaps into place progressively. */
  order: number;
  /** Signed perpendicular bow, keeps parallel edges from overlapping as straight lines. */
  bow: number;
};

export type SceneCluster = {
  id: number;
  cx: number;
  cy: number;
  color: RGB;
  memberIds: number[];
  totalArticles: number;
  maxRadius: number;
  puffs: readonly { dx: number; dy: number; r: number }[];
};

export type SceneData = {
  particles: ParticleScene[];
  nodes: SceneNode[];
  edges: SceneEdge[];
  clusters: SceneCluster[];
  reservoirPos: { x: number; y: number };
  /** Built once here rather than per frame — the edge pass resolves both endpoints ~84 times
   *  per draw, and the draw runs on every scroll tick. */
  nodeById: ReadonlyMap<number, SceneNode>;
  clusterById: ReadonlyMap<number, SceneCluster>;
};

// Mulberry32 — small, fast, deterministic PRNG. Same seed -> same scene, always.
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

const GRAY: RGB = [148, 163, 184]; // --fg-muted, raw/unresolved signal
const NEUTRAL_BRIGHT: RGB = [237, 237, 237]; // --foreground, a recognized event
const STABILIZED_GRAY: RGB = [122, 139, 163]; // dimmer, calcified
const ARCHIVED_GRAY: RGB = [42, 58, 74]; // --data-other, near-invisible

// Scene composition. Not claimed as production metrics — they're chosen so the *shape* of
// the pipeline reads at a glance (a visible majority matched, a visible minority reserved).
const NOISE_RATIO = 0.24; // particles the relevance filter discards
const MATCH_RATIO = 0.6; // events that join a storyline already in motion
const N_EXISTING_STORYLINES = 14; // storylines already alive when the batch arrives
const BIRTH_EVERY = 3; // 1 in N orphans is dense enough to start a new storyline
const N_CLUSTERS = 6;

// Same radius scales as Scene 2 (livingGraphLayout.ts) so the two scenes resolve to
// visually identical objects — see the file header.
const MIN_NODE_R = 0.006;
const MAX_NODE_R = 0.016;

type EventPlan = {
  memberIndices: number[];
  centroid: { x: number; y: number };
  nodeId: number; // -1 -> reservoir orphan, never becomes a storyline
};

function setFrame(
  frames: Float32Array,
  idx: number,
  x: number,
  y: number,
  r: number,
  alpha: number,
  color: RGB
) {
  const base = idx * FLOATS_PER_FRAME;
  frames[base + 0] = x;
  frames[base + 1] = y;
  frames[base + 2] = r;
  frames[base + 3] = alpha;
  frames[base + 4] = color[0];
  frames[base + 5] = color[1];
  frames[base + 6] = color[2];
}

function clampUnit(v: number): number {
  return Math.max(0.03, Math.min(0.97, v));
}

/** Builds the full deterministic scene. Call once per mount (cheap: ~460 particles, ~35 nodes). */
export function buildSignalDescentScene(seed = 20260722): SceneData {
  const rnd = mulberry32(seed);

  // ---------------------------------------------------------------- 1. SWARM
  // Lane assignment (spawn height) + which particles the relevance filter will kill.
  const laneOf = new Array<number>(N_PARTICLES);
  const isNoise = new Array<boolean>(N_PARTICLES);
  const signalIndices: number[] = [];
  for (let i = 0; i < N_PARTICLES; i++) {
    laneOf[i] = i % N_LANES;
    isNoise[i] = rnd() < NOISE_RATIO;
    if (!isNoise[i]) signalIndices.push(i);
  }

  // ------------------------------------------------------------- 2. COLLAPSE
  // Surviving particles group into events. Sizes 1-6 biased toward 2-4 (`rnd()*rnd()`),
  // i.e. a handful of outlets covering the same story fusing into one.
  const eventOf = new Array<number>(N_PARTICLES).fill(-1);
  const events: EventPlan[] = [];
  let cursor = 0;
  while (cursor < signalIndices.length) {
    const size = Math.min(signalIndices.length - cursor, 1 + Math.floor(rnd() * rnd() * 6));
    const memberIndices: number[] = [];
    for (let k = 0; k < size; k++) {
      const pi = signalIndices[cursor + k];
      eventOf[pi] = events.length;
      memberIndices.push(pi);
    }
    events.push({
      memberIndices,
      centroid: { x: COMPOSITION_LEFT + 0.08 + rnd() * 0.44, y: 0.14 + rnd() * 0.72 },
      nodeId: -1,
    });
    cursor += size;
  }

  // ------------------------------------------------- 3-4. FATE + BIRTH -> nodes
  // Storylines already in motion. Their pre-graph positions are a wide scatter: the whole
  // point of GRAVITY is that they start dispersed and get pulled together, so seeding them
  // anywhere near their eventual cluster would flatten act 6 into a non-event.
  const nodes: SceneNode[] = [];
  function newNode(): SceneNode {
    const n: SceneNode = {
      id: nodes.length,
      clusterId: -1,
      // Deliberately spread to the edges of the frame. GRAVITY is the act that has to *look*
      // like a contraction, and it can only read as one if the storylines start genuinely
      // dispersed — an initial scatter as tight as the final ring made act 6 a reshuffle
      // rather than a collapse (measured: node dispersion went 0.296 → 0.312, i.e. up).
      bx: COMPOSITION_LEFT + 0.02 + rnd() * 0.56,
      by: 0.06 + rnd() * 0.88,
      gx: 0,
      gy: 0,
      radius: MIN_NODE_R,
      articleCount: 0,
      outcome: 'active',
    };
    nodes.push(n);
    return n;
  }
  for (let i = 0; i < N_EXISTING_STORYLINES; i++) newNode();

  const reservoirPos = { x: FOCUS_X, y: 0.93 };
  let orphanOrder = 0;
  let birthNode: SceneNode | null = null;
  for (const ev of events) {
    if (rnd() < MATCH_RATIO) {
      // Weighted toward the low indices so a few storylines run visibly hotter than the rest,
      // instead of every node ending up the same size.
      const pick = Math.floor(Math.pow(rnd(), 1.7) * N_EXISTING_STORYLINES);
      ev.nodeId = nodes[Math.min(pick, N_EXISTING_STORYLINES - 1)].id;
    } else {
      if (orphanOrder % BIRTH_EVERY === 0) birthNode = newNode();
      // Only the orphan that triggered the birth (and its immediate neighbours) consolidates;
      // the rest stay in the reservoir, dimmed but not deleted (_store_orphan_events).
      ev.nodeId = orphanOrder % BIRTH_EVERY === 0 && birthNode ? birthNode.id : -1;
      orphanOrder++;
    }
  }

  for (const ev of events) {
    if (ev.nodeId >= 0) nodes[ev.nodeId].articleCount += ev.memberIndices.length;
  }
  // Storylines that ended up with nothing would render as invisible anchors — drop them and
  // reindex, so `nodes[i].id === i` keeps holding for the edge/cluster passes below.
  const liveNodes = nodes.filter((n) => n.articleCount > 0);
  const remap = new Map<number, number>();
  liveNodes.forEach((n, i) => {
    remap.set(n.id, i);
    n.id = i;
  });
  for (const ev of events) {
    if (ev.nodeId >= 0) ev.nodeId = remap.get(ev.nodeId) ?? -1;
  }

  const maxCount = Math.max(...liveNodes.map((n) => n.articleCount), 1);
  const minCount = Math.min(...liveNodes.map((n) => n.articleCount));
  for (const n of liveNodes) {
    const sizeT = (n.articleCount - minCount) / Math.max(1, maxCount - minCount);
    n.radius = MIN_NODE_R + sizeT * (MAX_NODE_R - MIN_NODE_R);
  }

  // --------------------------------------------------------------- 6. GRAVITY
  // Latent theme per storyline, biased so cluster sizes are uneven (real communities are).
  // Assigned before the edges so the edge pass can be denser within a theme — which is what
  // makes the contraction in act 6 look caused by the web rather than imposed on it.
  for (const n of liveNodes) {
    n.clusterId = Math.floor(Math.pow(rnd(), 1.3) * N_CLUSTERS) % N_CLUSTERS;
  }

  const clusters: SceneCluster[] = [];
  for (let c = 0; c < N_CLUSTERS; c++) {
    const members = liveNodes.filter((n) => n.clusterId === c);
    if (members.length === 0) continue;
    // Ring layout mirroring Scene 2, with two departures forced by this scene's framing:
    // the radius is constant instead of size-dependent (Scene 2's size term pushed the
    // largest cluster to cx≈0.91, whose halo then ran off the right edge of a full-bleed
    // canvas), and y is flattened harder because this scene is full-viewport rather than a
    // 380px panel. Constant radius also guarantees even angular spacing, which matters here
    // because each cluster carries a text label that must not collide with its neighbours.
    const crnd = mulberry32(c * 7919 + 13);
    const RING_R = 0.23;
    const { cx, cy } = clusterCentre(
      clusters.length,
      N_CLUSTERS,
      FOCUS_X,
      RING_R,
      0.62,
      (crnd() - 0.5) * 0.7,
      0.88 + crnd() * 0.24
    );

    let totalArticles = 0;
    members.forEach((n, mi) => {
      const spread = 0.03 + Math.min(0.07, members.length * 0.008);
      const { dx, dy } = clusterMemberOffset(mi, members.length, spread, crnd() * 0.6, crnd() * 0.4);
      n.gx = clampUnit(cx + dx);
      n.gy = clampUnit(cy + dy);
      totalArticles += n.articleCount;
    });

    // Capped: adjacent centres sit ~0.2 apart on the ring, and an uncapped halo (up to
    // ~0.12 for the biggest community) merged neighbouring nebulas into one blob. At 0.09
    // they overlap enough to haze together at the edges while keeping distinct cores.
    const maxRadius = Math.min(0.09, 0.05 + Math.sqrt(totalArticles) * 0.0075);
    const puffCount = 2 + Math.floor(crnd() * 2);
    clusters.push({
      id: c,
      cx,
      cy,
      color: dataColor(c),
      memberIds: members.map((m) => m.id),
      totalArticles,
      maxRadius,
      puffs: Array.from({ length: puffCount }, () => ({
        dx: (crnd() - 0.5) * maxRadius * 0.9,
        dy: (crnd() - 0.5) * maxRadius * 0.9,
        r: maxRadius * (0.5 + crnd() * 0.35),
      })),
    });
  }

  // ------------------------------------------------------------------ 5. WEB
  // Jaccard-like edges: dense within a theme, sparse across. Weight stands in for the
  // TF-IDF weighted entity overlap the backend computes.
  const edges: SceneEdge[] = [];
  const INTRA_P = 0.55;
  const CROSS_P = 0.035;
  for (let i = 0; i < liveNodes.length; i++) {
    for (let j = i + 1; j < liveNodes.length; j++) {
      const sameTheme = liveNodes[i].clusterId === liveNodes[j].clusterId;
      if (rnd() >= (sameTheme ? INTRA_P : CROSS_P)) continue;
      edges.push({
        a: liveNodes[i].id,
        b: liveNodes[j].id,
        weight: sameTheme ? 0.35 + rnd() * 0.6 : 0.08 + rnd() * 0.22,
        order: 0, // filled below
        bow: (rnd() - 0.5) * 0.28,
      });
    }
  }
  // Heaviest edges snap in first: the strong connections read as the ones doing the pulling.
  edges.sort((x, y) => y.weight - x.weight);
  edges.forEach((e, i) => {
    e.order = edges.length > 1 ? i / (edges.length - 1) : 0;
  });

  // ----------------------------------------------------------------- 7. DECAY
  // Three distinguishable end states, cycled so all three are always on screen.
  const OUTCOMES: EventOutcome[] = ['active', 'stabilized', 'archived'];
  liveNodes.forEach((n, i) => {
    n.outcome = OUTCOMES[i % OUTCOMES.length];
  });

  // ------------------------------------------------- emit particle keyframes
  const nodeById = new Map(liveNodes.map((n) => [n.id, n]));
  const clusterById = new Map(clusters.map((c) => [c.id, c]));
  const particles: ParticleScene[] = [];

  for (let i = 0; i < N_PARTICLES; i++) {
    const laneId = laneOf[i];
    const noise = isNoise[i];
    const ev = eventOf[i] >= 0 ? events[eventOf[i]] : null;
    const node = ev && ev.nodeId >= 0 ? nodeById.get(ev.nodeId) ?? null : null;
    const cluster = node ? clusterById.get(node.clusterId) ?? null : null;

    const laneY = 0.08 + (laneId / (N_LANES - 1)) * 0.84;
    const jitterX = (rnd() - 0.5) * 0.03;
    const jitterY = (rnd() - 0.5) * 0.03;
    // Offsets inside the storyline knot. Tight at BIRTH (the drops fuse into one bead),
    // looser at IGNITION so the nebula shows the grain it's made of.
    const knotAngle = rnd() * Math.PI * 2;
    const knotR = Math.sqrt(rnd());
    const frames = new Float32Array(N_KEYFRAMES * FLOATS_PER_FRAME);

    // Per-particle arrival phase. Without it every particle shares kf0/kf1 x values and the
    // whole batch crosses the scan plane as one solid vertical wall — a rain of signal has
    // to be staggered, so `phase` spreads both the spawn point and the crossing moment.
    const phase = rnd();
    // At the scan keyframe the swarm straddles the plane: the leading edge (phase > ~0.5)
    // has already been judged, the trailing edge hasn't. That's what makes the filter read
    // as a continuous process rather than a single synchronized blink.
    const scanKfX = SCAN_X - 0.16 + phase * 0.32;
    const judged = scanKfX > SCAN_X;

    // kf0 — entering at lane height, tiny and dim. Starts partly off-frame and partly
    // under the caption column's scrim: the feed reads as arriving from outside the frame,
    // and by the midpoint of the act most of the stream has cleared the text.
    setFrame(frames, 0, -0.15 + phase * 0.3, laneY, 0.004, 0.5, GRAY);
    // kf1 — straddling the scan plane; already resolved if past it.
    setFrame(
      frames,
      1,
      scanKfX,
      laneY + jitterY * 0.5,
      0.005,
      noise && judged ? 0 : 0.8,
      judged && !noise ? NEUTRAL_BRIGHT : GRAY
    );

    if (noise || !ev) {
      // Filtered out: stalls just past the plane and fades to nothing, then stays gone.
      for (let k = 2; k < N_KEYFRAMES; k++) {
        setFrame(frames, k, Math.max(scanKfX, SCAN_X) + 0.03, laneY + jitterY, 0.004, 0, GRAY);
      }
      particles.push({ frames, laneId, isNoise: true });
      continue;
    }

    // kf2 — SWARM end: through the filter, spread across the left-center, recognized.
    setFrame(frames, 2, SCAN_X + 0.04 + phase * 0.14, laneY + jitterY, 0.0055, 0.85, NEUTRAL_BRIGHT);
    // kf3 — COLLAPSE end: fused onto the event centroid.
    const evR = Math.min(0.013, 0.005 + ev.memberIndices.length * 0.0015);
    setFrame(frames, 3, ev.centroid.x + jitterX * 0.3, ev.centroid.y + jitterY * 0.3, evR, 0.95, NEUTRAL_BRIGHT);

    if (!node) {
      // Reservoir orphan: drifts to the bottom band and dims — held, not deleted.
      const rx = reservoirPos.x + (rnd() - 0.5) * 0.5;
      for (let k = 4; k < N_KEYFRAMES; k++) {
        setFrame(frames, k, rx, reservoirPos.y + jitterY * 0.4, evR * 0.8, 0.32, ARCHIVED_GRAY);
      }
      particles.push({ frames, laneId, isNoise: false });
      continue;
    }

    const color = cluster ? cluster.color : NEUTRAL_BRIGHT;
    // Tight offset for the fused-bead look at BIRTH/WEB…
    const tightDx = Math.cos(knotAngle) * knotR * node.radius * 0.45;
    const tightDy = Math.sin(knotAngle) * knotR * node.radius * 0.45;
    // …and a looser one once the nebula is lit, so the halo reads as full of grain.
    const looseDx = Math.cos(knotAngle) * knotR * node.radius * 1.15;
    const looseDy = Math.sin(knotAngle) * knotR * node.radius * 1.15;

    // kf4 — FATE end: arrived at the storyline, still colourless (the theme isn't known yet).
    setFrame(frames, 4, node.bx + tightDx, node.by + tightDy, evR, 0.95, NEUTRAL_BRIGHT);
    // kf5 — BIRTH end: the bead is solid and takes on its storyline identity.
    setFrame(frames, 5, node.bx + tightDx, node.by + tightDy, evR * 1.15, 1, lerpColor(NEUTRAL_BRIGHT, color, 0.35));
    // kf6 — WEB end: unmoved. The network appears *around* the storylines; they don't travel yet.
    setFrame(frames, 6, node.bx + tightDx, node.by + tightDy, evR * 1.15, 1, lerpColor(NEUTRAL_BRIGHT, color, 0.55));
    // kf7 — GRAVITY end: dragged into the community, now fully on-theme.
    setFrame(frames, 7, node.gx + tightDx, node.gy + tightDy, evR * 1.1, 1, color);
    // kf8 — IGNITION end: loosens into the lit nebula, with its decay outcome.
    const lx = node.gx + looseDx;
    const ly = node.gy + looseDy;
    if (node.outcome === 'active') {
      setFrame(frames, 8, lx, ly, evR * 1.25, 1, color);
    } else if (node.outcome === 'stabilized') {
      setFrame(frames, 8, lx, ly, evR * 0.85, 0.6, lerpColor(color, STABILIZED_GRAY, 0.65));
    } else {
      setFrame(frames, 8, lx, ly, evR * 0.5, 0.18, lerpColor(color, ARCHIVED_GRAY, 0.8));
    }

    particles.push({ frames, laneId, isNoise: false });
  }

  return { particles, nodes: liveNodes, edges, clusters, reservoirPos, nodeById, clusterById };
}

/** Interpolate one particle's visual state at scroll progress t ∈ [0,1]. */
export function sampleParticle(
  p: ParticleScene,
  t: number
): { x: number; y: number; radius: number; alpha: number; color: RGB } {
  const clamped = Math.max(0, Math.min(1, t));
  let i = 0;
  while (i < KEYFRAME_TIMES.length - 2 && clamped > KEYFRAME_TIMES[i + 1]) i++;
  const t0 = KEYFRAME_TIMES[i];
  const t1 = KEYFRAME_TIMES[i + 1];
  const localT = t1 > t0 ? (clamped - t0) / (t1 - t0) : 0;

  const a = i * FLOATS_PER_FRAME;
  const b = (i + 1) * FLOATS_PER_FRAME;
  const f = p.frames;

  return {
    x: f[a + 0] + (f[b + 0] - f[a + 0]) * localT,
    y: f[a + 1] + (f[b + 1] - f[a + 1]) * localT,
    radius: f[a + 2] + (f[b + 2] - f[a + 2]) * localT,
    alpha: f[a + 3] + (f[b + 3] - f[a + 3]) * localT,
    color: [
      f[a + 4] + (f[b + 4] - f[a + 4]) * localT,
      f[a + 5] + (f[b + 5] - f[a + 5]) * localT,
      f[a + 6] + (f[b + 6] - f[a + 6]) * localT,
    ],
  };
}

/**
 * A storyline's drawn position at progress t — the same lerp the particles use, so edges
 * and nebulas stay locked to the knots they belong to while GRAVITY is dragging them.
 */
export function sampleNode(n: SceneNode, t: number): { x: number; y: number } {
  const gravityStart = ACT_BOUNDARIES[5];
  const gravityEnd = ACT_BOUNDARIES[6];
  if (t <= gravityStart) return { x: n.bx, y: n.by };
  if (t >= gravityEnd) return { x: n.gx, y: n.gy };
  const u = (t - gravityStart) / (gravityEnd - gravityStart);
  // Ease-in-out: the web takes up slack before anything moves, then settles rather than
  // arriving at constant speed — reads as tension being released, not as a slide.
  const e = u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2;
  return { x: n.bx + (n.gx - n.bx) * e, y: n.by + (n.gy - n.by) * e };
}
