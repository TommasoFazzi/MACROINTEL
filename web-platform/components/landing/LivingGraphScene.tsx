'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { computeGraphLayout, nodeReveal, type ClusterBlob, type GraphLayout } from '@/lib/landing/livingGraphLayout';
// Shared with Scene 1's closing act — see lib/landing/nebulaRender.ts for why these
// primitives live outside this component.
import { bezierPoint, drawNebula, rgba, type RGB } from '@/lib/landing/nebulaRender';
import { useCanvasSize } from '@/lib/landing/useCanvasSize';
import { useInView } from '@/lib/landing/useInView';
import type { LiveGraphData } from '@/lib/landing/live';

// Ambient loop timing (ms). Not scroll-linked — this is a background time-lapse, always
// playing while in view, per design.md: "Scena 2 è un loop temporale autonomo, parte in
// viewport" (the only one of the three scenes that isn't scroll-driven — it sits behind
// the Hero, which must look alive on load, before the reader has scrolled at all).
const CYCLE_MS = 20000;
const GROW_FRAC = 0.55;
const HOLD_FRAC = 0.3;
// FADE_FRAC is the remainder (0.15) — the dissolve back to the start of the loop.

const FADE_BAND_FLOW = 0.06;
const MAX_FLOWS = 24;
const FLOW_PERIOD_MS = 3200; // one particle traversal of a flow's curve
const FLOW_TRAIL_STEPS = 5;

function clamp01(v: number): number {
  return Math.max(0, Math.min(1, v));
}
function easeOutCubic(x: number): number {
  return 1 - Math.pow(1 - x, 3);
}
/** Weighted fraction of a cluster's mass currently visible — members can die mid-hold, so
 *  this isn't monotonic: a cluster can shrink as well as grow within a single loop. */
function clusterReveal(
  cluster: ClusterBlob,
  nodeById: Map<number, GraphLayout['nodes'][number]>,
  growT: number,
  holdProgress: number
) {
  let revealedMass = 0;
  for (const id of cluster.memberIds) {
    const n = nodeById.get(id);
    if (!n) continue;
    revealedMass += nodeReveal(n, growT, holdProgress) * n.articleCount;
  }
  return cluster.totalArticles > 0 ? revealedMass / cluster.totalArticles : 0;
}

function renderFrame(
  ctx: CanvasRenderingContext2D,
  layout: GraphLayout,
  nodeById: Map<number, GraphLayout['nodes'][number]>,
  clusterByKey: Map<string, ClusterBlob>,
  flows: GraphLayout['flows'],
  growT: number,
  holdProgress: number,
  globalAlpha: number,
  elapsedMs: number,
  width: number,
  height: number
) {
  ctx.clearRect(0, 0, width, height);
  const minDim = Math.min(width, height);
  ctx.globalCompositeOperation = 'lighter';

  // 1. Nebula clusters — the primary visual mass. Skipped for singleton "lone star"
  // clusters, which render as a single bright point in the node pass below instead.
  for (const cluster of layout.clusters) {
    if (cluster.isLoneStar) continue;
    const reveal = clusterReveal(cluster, nodeById, growT, holdProgress);
    if (reveal <= 0.01) continue;
    const grow = Math.sqrt(reveal);
    const alpha = reveal * globalAlpha;
    const cx = cluster.cx * width;
    const cy = cluster.cy * height;
    drawNebula(ctx, cx, cy, cluster.maxRadius * grow * minDim, cluster.color, alpha);
    for (const puff of cluster.puffs) {
      drawNebula(ctx, cx + puff.dx * minDim, cy + puff.dy * minDim, puff.r * grow * minDim, cluster.color, alpha * 0.8);
    }
  }

  // 2. Cross-cluster flows — a faint base curve plus a traveling particle with a short
  // comet trail, connecting nebulas. Aggregated from real edges (see livingGraphLayout.ts);
  // an edge's exact timing is approximated, never claimed as observed history.
  for (const flow of flows) {
    const reveal = clamp01((growT - flow.bornT) / FADE_BAND_FLOW);
    if (reveal <= 0.01) continue;
    const a = clusterByKey.get(flow.a);
    const b = clusterByKey.get(flow.b);
    if (!a || !b) continue;
    const ax = a.cx * width;
    const ay = a.cy * height;
    const bx = b.cx * width;
    const by = b.cy * height;
    const mx = (ax + bx) / 2;
    const my = (ay + by) / 2;
    const dx = bx - ax;
    const dy = by - ay;
    const dist = Math.hypot(dx, dy) || 1;
    const cxp = mx - (dy / dist) * dist * flow.bow;
    const cyp = my + (dx / dist) * dist * flow.bow;

    const white: RGB = [255, 255, 255];
    ctx.strokeStyle = rgba(white, 0.05 * reveal * globalAlpha);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.quadraticCurveTo(cxp, cyp, bx, by);
    ctx.stroke();

    const particleCount = flow.weight > 0.3 ? 2 : 1;
    for (let p = 0; p < particleCount; p++) {
      const phase = p / particleCount;
      const head = ((elapsedMs / FLOW_PERIOD_MS + phase) % 1 + 1) % 1;
      for (let s = 0; s < FLOW_TRAIL_STEPS; s++) {
        const u = head - s * 0.025;
        if (u < 0 || u > 1) continue;
        const edgeFade = Math.min(1, u * 6, (1 - u) * 6); // soften near the endpoints
        const trailAlpha = (1 - s / FLOW_TRAIL_STEPS) * reveal * globalAlpha * edgeFade;
        if (trailAlpha <= 0.01) continue;
        const pt = bezierPoint(ax, ay, cxp, cyp, bx, by, u);
        ctx.beginPath();
        ctx.fillStyle = rgba(white, trailAlpha * 0.8);
        ctx.arc(pt.x, pt.y, s === 0 ? 1.8 : 1.1, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  // 3. Individual storyline points — subtle texture inside each nebula (real granular
  // data, not just an abstract wash), and the primary render for true lone stars.
  for (const n of layout.nodes) {
    const reveal = nodeReveal(n, growT, holdProgress);
    if (reveal <= 0.01) continue;
    const brightness = 0.35 + n.momentum * 0.65;
    const inNebula = !clusterByKey.get(n.clusterKey)?.isLoneStar;
    const alpha = reveal * brightness * globalAlpha * (inNebula ? 0.55 : 1);
    const r = Math.max(1, n.radius * minDim) * (n.isLoneStar ? 1.6 : 1);
    const color = rgba(n.color, alpha);

    if (n.isLoneStar) {
      ctx.shadowColor = color;
      ctx.shadowBlur = r * 3.2 * brightness;
    } else {
      ctx.shadowBlur = 0;
    }
    ctx.beginPath();
    ctx.fillStyle = color;
    ctx.arc(n.x * width, n.y * height, r, 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.shadowBlur = 0;
  ctx.globalCompositeOperation = 'source-over';
}

export default function LivingGraphScene({ graph }: { graph: LiveGraphData }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const { width, height } = useCanvasSize(containerRef);
  const inView = useInView(containerRef);
  const [reducedMotion, setReducedMotion] = useState(false);

  const layout = useMemo(() => computeGraphLayout(graph), [graph]);
  const nodeById = useMemo(() => new Map(layout.nodes.map((n) => [n.id, n])), [layout]);
  const clusterByKey = useMemo(() => new Map(layout.clusters.map((c) => [c.key, c])), [layout]);
  const flows = useMemo(
    () => [...layout.flows].sort((a, b) => b.weight - a.weight).slice(0, MAX_FLOWS),
    [layout]
  );

  useEffect(() => {
    setReducedMotion(window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx || width === 0 || height === 0) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    if (reducedMotion) {
      // holdProgress=0: the "final frame" fallback shows everything fully formed, before
      // any of the mid-hold deaths would occur — not an arbitrary partially-decayed state.
      renderFrame(ctx, layout, nodeById, clusterByKey, flows, 1, 0, 1, 0, width, height);
      return;
    }
    if (!inView) return;

    let raf = 0;
    const start = performance.now();
    function tick(now: number) {
      const elapsed = now - start;
      const cyclePos = (elapsed % CYCLE_MS) / CYCLE_MS;
      let growT: number;
      let globalAlpha: number;
      if (cyclePos < GROW_FRAC) {
        growT = easeOutCubic(cyclePos / GROW_FRAC);
        globalAlpha = 1;
      } else if (cyclePos < GROW_FRAC + HOLD_FRAC) {
        growT = 1;
        globalAlpha = 1;
      } else {
        growT = 1;
        const fadeP = (cyclePos - (GROW_FRAC + HOLD_FRAC)) / (1 - GROW_FRAC - HOLD_FRAC);
        globalAlpha = 1 - fadeP;
      }
      const holdProgress = clamp01((cyclePos - GROW_FRAC) / HOLD_FRAC);
      renderFrame(ctx!, layout, nodeById, clusterByKey, flows, growT, holdProgress, globalAlpha, elapsed, width, height);
      raf = requestAnimationFrame(tick);
    }
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [layout, nodeById, clusterByKey, flows, width, height, inView, reducedMotion]);

  return (
    <div ref={containerRef} className="absolute inset-0 h-full w-full">
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />
    </div>
  );
}
