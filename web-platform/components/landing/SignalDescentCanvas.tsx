'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ACT_BOUNDARIES,
  N_ACTS,
  SCAN_X,
  buildSignalDescentScene,
  sampleNode,
  sampleParticle,
  type SceneData,
} from '@/lib/landing/signalDescentScene';
import { bezierPoint, drawNebula, rgba, type RGB } from '@/lib/landing/nebulaRender';
import { useCanvasSize } from '@/lib/landing/useCanvasSize';
import { SOURCE_COUNT } from '@/lib/constants';

/**
 * Seven acts, one per stage of the real pipeline — see lib/landing/signalDescentScene.ts
 * for the mapping to the backend. The closing act deliberately resolves to the same picture
 * as the Hero's Living Graph (Scene 2): the reader is meant to recognize the shape they
 * scrolled past muted at the top of the page, which is why the last caption names it.
 *
 * `t` is the sample point used for the static fallbacks (mobile filmstrip, reduced motion) —
 * the midpoint of each act, not its boundary.
 */
const ACTS = [
  {
    label: 'SWARM',
    body: `${SOURCE_COUNT} feeds arrive around the clock. A relevance pass discards what isn't intelligence before anything else runs.`,
  },
  {
    label: 'COLLAPSE',
    body: 'Different outlets covering the same story fuse into a single event.',
  },
  {
    label: 'FATE',
    body: 'Each event either joins a storyline already in motion, or waits in reserve.',
  },
  {
    label: 'BIRTH',
    body: "Unmatched events dense enough to stand on their own become new storylines. The rest aren't discarded — they stay in reserve.",
  },
  {
    label: 'WEB',
    body: 'Storylines that talk about the same actors link up. The heavier the shared vocabulary, the stronger the link.',
  },
  {
    label: 'GRAVITY',
    body: 'Nothing pulls from outside. The links contract under their own weight, and communities fall out of the density.',
  },
  {
    label: 'IGNITION',
    body: 'Every community gets a name. This is the graph from the top of this page — now you know what it is made of.',
  },
] as const;

const ACT_MID: readonly number[] = ACTS.map(
  (_, i) => (ACT_BOUNDARIES[i] + ACT_BOUNDARIES[i + 1]) / 2
);

const WEB_START = ACT_BOUNDARIES[4];
const WEB_END = ACT_BOUNDARIES[5];

const GLOW_RADIUS_THRESHOLD = 5; // px — only "important" dots get a soft bloom
const EDGE_STAGGER = 0.72; // fraction of the WEB act spent staggering edges in
/** Past this progress the scene stops being purely scroll-driven and starts breathing. */
const BREATH_START = 0.94;

function clamp01(v: number): number {
  return Math.max(0, Math.min(1, v));
}

/** Progress within a single act, 0 before it starts and 1 after it ends. */
function actProgress(t: number, actIndex: number): number {
  const a = ACT_BOUNDARIES[actIndex];
  const b = ACT_BOUNDARIES[actIndex + 1];
  return clamp01((t - a) / (b - a));
}

type RenderOpts = { labels: readonly string[]; elapsedMs: number; monoFont: string };

function renderFrame(
  ctx: CanvasRenderingContext2D,
  scene: SceneData,
  t: number,
  width: number,
  height: number,
  opts: RenderOpts
) {
  ctx.clearRect(0, 0, width, height);
  const minDim = Math.min(width, height);
  const ignition = actProgress(t, 6);

  // Breathing only kicks in once the reader has actually landed on the final picture. Before
  // that the scene must stay a pure function of scroll (reversible scrub); after it, the
  // ambient pulse is what hands off to Scene 2, which is time-driven rather than scrolled.
  const breath =
    t >= BREATH_START ? Math.sin(opts.elapsedMs / 1900) * 0.5 + 0.5 : 0;

  // --- 1. Nebulas (IGNITION) -------------------------------------------------
  // Same primitive, same palette and same radius scale as Scene 2 — the recognition only
  // works if the two scenes converge on an identical object.
  if (ignition > 0) {
    ctx.globalCompositeOperation = 'lighter';
    const reveal = clamp01(ignition / 0.45);
    for (const cluster of scene.clusters) {
      const pulse = 1 + breath * 0.04;
      const alpha = reveal * (0.85 + breath * 0.15);
      const cx = cluster.cx * width;
      const cy = cluster.cy * height;
      drawNebula(ctx, cx, cy, cluster.maxRadius * reveal * pulse * minDim, cluster.color, alpha);
      for (const puff of cluster.puffs) {
        drawNebula(
          ctx,
          cx + puff.dx * minDim,
          cy + puff.dy * minDim,
          puff.r * reveal * pulse * minDim,
          cluster.color,
          alpha * 0.8
        );
      }
    }
    ctx.globalCompositeOperation = 'source-over';
  }

  // --- 2. Scan plane (SWARM) -------------------------------------------------
  // The filter made visible: particles cross it and either brighten or die. Fades out once
  // its act is over so it doesn't linger as unexplained furniture.
  const swarm = actProgress(t, 0);
  if (swarm > 0 && t < ACT_BOUNDARIES[2]) {
    const fade = t < ACT_BOUNDARIES[1] ? 1 : 1 - actProgress(t, 1);
    const x = SCAN_X * width;
    const grad = ctx.createLinearGradient(x - 14, 0, x + 14, 0);
    grad.addColorStop(0, 'rgba(0, 168, 232, 0)');
    grad.addColorStop(0.5, `rgba(0, 168, 232, ${0.5 * fade})`);
    grad.addColorStop(1, 'rgba(0, 168, 232, 0)');
    ctx.fillStyle = grad;
    ctx.fillRect(x - 14, 0, 28, height);
  }

  // --- 3. Edges (WEB onward) -------------------------------------------------
  if (t > WEB_START) {
    const webT = clamp01((t - WEB_START) / (WEB_END - WEB_START));
    const nodeById = scene.nodeById;
    for (const e of scene.edges) {
      // Staggered by weight order: the strong links land first, so the contraction that
      // follows reads as caused by them.
      const appear = clamp01((webT - e.order * EDGE_STAGGER) / (1 - EDGE_STAGGER));
      if (appear <= 0.01) continue;
      const na = nodeById.get(e.a);
      const nb = nodeById.get(e.b);
      if (!na || !nb) continue;
      const cross = na.clusterId !== nb.clusterId;

      // Once the nebulas are lit, intra-community links are absorbed into the halo and only
      // the cross-community ones stay legible — exactly what Scene 2 draws as "flows".
      const absorbed = cross ? 1 : 1 - ignition * 0.82;
      const alpha = appear * absorbed * (cross ? 0.3 : 0.22) * (0.35 + e.weight * 0.65);
      if (alpha <= 0.005) continue;

      const pa = sampleNode(na, t);
      const pb = sampleNode(nb, t);
      const ax = pa.x * width;
      const ay = pa.y * height;
      const bx = pb.x * width;
      const by = pb.y * height;
      const dx = bx - ax;
      const dy = by - ay;
      const dist = Math.hypot(dx, dy) || 1;
      const cxp = (ax + bx) / 2 - (dy / dist) * dist * e.bow;
      const cyp = (ay + by) / 2 + (dx / dist) * dist * e.bow;

      const color: RGB = cross ? [255, 255, 255] : scene.clusterById.get(na.clusterId)?.color ?? [255, 255, 255];
      ctx.strokeStyle = rgba(color, alpha);
      ctx.lineWidth = 0.6 + e.weight * 1.1;
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.quadraticCurveTo(cxp, cyp, bx, by);
      ctx.stroke();

      // A single traveling mote on the heaviest cross-community links once everything is lit —
      // the same "flow" motif Scene 2 uses, so the handoff doesn't change visual vocabulary.
      if (cross && ignition > 0.5 && e.weight > 0.18) {
        const u = ((opts.elapsedMs / 3200 + e.order) % 1 + 1) % 1;
        const pt = bezierPoint(ax, ay, cxp, cyp, bx, by, u);
        const edgeFade = Math.min(1, u * 6, (1 - u) * 6);
        ctx.beginPath();
        ctx.fillStyle = rgba([255, 255, 255], 0.5 * edgeFade * ignition);
        ctx.arc(pt.x, pt.y, 1.5, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  // --- 4. Particles ----------------------------------------------------------
  for (const p of scene.particles) {
    const s = sampleParticle(p, t);
    if (s.alpha <= 0.01) continue;
    const x = s.x * width;
    const y = s.y * height;
    const r = Math.max(0.6, s.radius * minDim);
    const fill = rgba(s.color, s.alpha);

    if (r > GLOW_RADIUS_THRESHOLD) {
      ctx.shadowColor = fill;
      ctx.shadowBlur = r * 1.8;
    } else {
      ctx.shadowBlur = 0;
    }

    ctx.beginPath();
    ctx.fillStyle = fill;
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.shadowBlur = 0;

  // --- 5. Community labels (IGNITION) ----------------------------------------
  // Only drawn when real community names were supplied. With no backend there are no names,
  // and inventing plausible ones would be exactly the fabricated-content trap the rest of
  // the page avoids — the shape alone still carries the act.
  if (ignition > 0.55 && opts.labels.length > 0) {
    const labelAlpha = clamp01((ignition - 0.55) / 0.3);
    ctx.font = `600 11px ${opts.monoFont}`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (const cluster of scene.clusters) {
      const name = opts.labels[cluster.id];
      if (!name) continue;
      const cx = cluster.cx * width;
      const cy = cluster.cy * height - cluster.maxRadius * minDim - 12;
      const w = ctx.measureText(name).width;
      ctx.fillStyle = `rgba(10, 22, 40, ${0.72 * labelAlpha})`;
      ctx.beginPath();
      ctx.roundRect(cx - w / 2 - 7, cy - 9, w + 14, 18, 4);
      ctx.fill();
      ctx.fillStyle = rgba(cluster.color, labelAlpha);
      ctx.fillText(name, cx, cy);
    }
    ctx.textAlign = 'start';
    ctx.textBaseline = 'alphabetic';
  }
}

/** Resolves the Geist Mono family that next/font generated, for canvas `ctx.font`. */
function readMonoFont(): string {
  if (typeof window === 'undefined') return 'ui-monospace, monospace';
  const v = getComputedStyle(document.documentElement).getPropertyValue('--font-geist-mono').trim();
  return v ? `${v}, ui-monospace, monospace` : 'ui-monospace, monospace';
}

/** One static frame, used both for the mobile filmstrip and the reduced-motion fallback. */
function StaticFrame({
  scene,
  t,
  height,
  labels,
}: {
  scene: SceneData;
  t: number;
  height: number;
  labels: readonly string[];
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const { width } = useCanvasSize(containerRef);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext('2d');
    if (!canvas || !ctx || width === 0) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    renderFrame(ctx, scene, t, width, height, { labels, elapsedMs: 0, monoFont: readMonoFont() });
  }, [scene, t, width, height, labels]);

  return (
    <div ref={containerRef} className="relative w-full" style={{ height }}>
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />
    </div>
  );
}

function CaptionPanel({ actIndex }: { actIndex: number }) {
  const act = ACTS[actIndex];
  return (
    <div className="rounded-xl border border-white/10 bg-[rgba(10,22,40,0.82)] px-7 py-5 text-center shadow-[0_20px_50px_rgba(0,0,0,0.5)] backdrop-blur-md">
      <div className="mb-2 flex items-center justify-center gap-2.5">
        <span className="font-mono text-meta text-fg-subtle">
          0{actIndex + 1}/0{N_ACTS}
        </span>
        <span className="h-3 w-px bg-white/15" />
        <span className="font-mono text-meta font-bold tracking-[0.25em] text-accent-info">
          {act.label}
        </span>
      </div>
      <p className="text-sm leading-relaxed text-foreground">{act.body}</p>
    </div>
  );
}

function MobileStaticScene({ scene, labels }: { scene: SceneData; labels: readonly string[] }) {
  return (
    <div className="flex flex-col gap-8 py-16">
      {ACTS.map((act, i) => (
        <div key={act.label} className="px-6">
          <StaticFrame scene={scene} t={ACT_MID[i]} height={280} labels={labels} />
          <div className="mt-4">
            <CaptionPanel actIndex={i} />
          </div>
        </div>
      ))}
    </div>
  );
}

function ScrubbedScene({ scene, labels }: { scene: SceneData; labels: readonly string[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const actNumberRef = useRef<HTMLSpanElement>(null);
  const labelRef = useRef<HTMLSpanElement>(null);
  const bodyRef = useRef<HTMLParagraphElement>(null);
  const dotRefs = useRef<Array<HTMLSpanElement | null>>([]);
  const lastActIndexRef = useRef(-1);

  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const monoFont = readMonoFont();

    function resize() {
      const rect = container!.getBoundingClientRect();
      canvas!.width = rect.width * dpr;
      canvas!.height = rect.height * dpr;
      canvas!.style.width = `${rect.width}px`;
      canvas!.style.height = `${rect.height}px`;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(container);

    function updateCaption(actIndex: number) {
      const act = ACTS[actIndex];
      if (actNumberRef.current) actNumberRef.current.textContent = `0${actIndex + 1}/0${N_ACTS}`;
      if (labelRef.current) labelRef.current.textContent = act.label;
      if (bodyRef.current) bodyRef.current.textContent = act.body;
      dotRefs.current.forEach((bar, i) => {
        if (!bar) return;
        // Acts already passed stay half-lit, so the rail shows progress rather than just
        // which act is current.
        if (i === actIndex) {
          bar.style.background = 'var(--accent-info)';
          bar.style.opacity = '1';
        } else {
          bar.style.background = i < actIndex ? 'var(--accent-info)' : 'rgba(255,255,255,0.18)';
          bar.style.opacity = i < actIndex ? '0.4' : '1';
        }
      });
    }

    // Scroll progress is stored rather than drawn directly: the final act runs a rAF loop for
    // the ambient breathing, and both paths must render from the same source of truth.
    let progress = 0;
    const startedAt = performance.now();
    let breathRaf = 0;

    function paint() {
      const rect = container!.getBoundingClientRect();
      renderFrame(ctx!, scene, progress, rect.width, rect.height, {
        labels,
        elapsedMs: performance.now() - startedAt,
        monoFont,
      });
    }

    function breathTick() {
      paint();
      breathRaf = requestAnimationFrame(breathTick);
    }

    function syncBreathing() {
      const wants = progress >= BREATH_START;
      if (wants && !breathRaf) breathRaf = requestAnimationFrame(breathTick);
      else if (!wants && breathRaf) {
        cancelAnimationFrame(breathRaf);
        breathRaf = 0;
      }
    }

    function draw(t: number) {
      progress = t;
      syncBreathing();
      if (!breathRaf) paint(); // while breathing, the rAF loop is already painting

      let actIndex = 0;
      for (let i = 0; i < N_ACTS; i++) {
        if (t >= ACT_BOUNDARIES[i]) actIndex = i;
      }

      if (actIndex !== lastActIndexRef.current) {
        lastActIndexRef.current = actIndex;
        // Brief cut on the panel only (canvas keeps scrubbing smoothly) — marks the
        // act boundary without re-triggering on every one of the many scroll ticks
        // within the same act.
        if (panelRef.current) {
          panelRef.current.style.opacity = '0';
          setTimeout(() => {
            updateCaption(actIndex);
            if (panelRef.current) panelRef.current.style.opacity = '1';
          }, 120);
        } else {
          updateCaption(actIndex);
        }
      }
    }

    let cancelled = false;
    let scrollTriggerInstance: import('gsap/ScrollTrigger').ScrollTrigger | undefined;
    let mm: gsap.MatchMedia | undefined;

    import('gsap').then(async ({ gsap }) => {
      if (cancelled) return;
      const { ScrollTrigger } = await import('gsap/ScrollTrigger');
      gsap.registerPlugin(ScrollTrigger);

      mm = gsap.matchMedia();
      mm.add('(min-width: 768px)', () => {
        scrollTriggerInstance = ScrollTrigger.create({
          trigger: container,
          start: 'top top',
          // 7 acts on the old +=300% budget left the two shortest ones at ~30% of a viewport
          // each — too fast to read the caption. Scaled with the act count instead.
          end: '+=450%',
          pin: true,
          scrub: 1,
          onUpdate: (self) => draw(self.progress),
        });
        draw(0);
        updateCaption(0);
        return () => scrollTriggerInstance?.kill();
      });
    });

    return () => {
      cancelled = true;
      if (breathRaf) cancelAnimationFrame(breathRaf);
      ro.disconnect();
      mm?.revert();
      scrollTriggerInstance?.kill();
    };
  }, [scene, labels]);

  return (
    <div ref={containerRef} className="relative h-screen w-full overflow-hidden">
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />
      {/* Vignette — depth cue, not decoration for its own sake: keeps the eye on the
          figure rather than the flat edges of the pinned viewport. Weighted toward the
          scene's actual centre of mass (right of centre), not the geometric middle. */}
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_65%_50%,transparent_38%,rgba(10,22,40,0.55)_100%)]" />

      {/* Scrim for the caption column. The captions used to sit in a card at the bottom of
          the viewport and almost nobody read them — the eye tracks the moving figure and
          anything below it lands in peripheral vision. They're now a left-hand column at
          eye level, which only works if the text has guaranteed contrast: this gradient is
          opaque where the type sits and gone by the time it reaches the graphic. */}
      <div className="pointer-events-none absolute inset-y-0 left-0 hidden w-[58%] bg-[linear-gradient(90deg,rgba(10,22,40,0.95)_0%,rgba(10,22,40,0.92)_34%,rgba(10,22,40,0.38)_48%,transparent_100%)] md:block" />

      <div
        ref={panelRef}
        className="pointer-events-none absolute inset-y-0 left-0 hidden w-[min(30rem,38vw)] flex-col justify-center gap-5 px-[clamp(1.5rem,4vw,3.5rem)] transition-opacity duration-instant md:flex"
      >
        <div className="flex items-center gap-3">
          <span ref={actNumberRef} className="font-mono text-meta text-fg-subtle" />
          <span className="h-3 w-px bg-white/15" />
          <span
            ref={labelRef}
            className="font-mono text-[clamp(0.9rem,1.3vw,1.15rem)] font-bold tracking-[0.28em] text-accent-info"
          />
        </div>

        <p
          ref={bodyRef}
          className="text-[clamp(1rem,1.35vw,1.3rem)] leading-[1.55] text-foreground"
        />

        {/* Progress rail — horizontal bars rather than dots: at this size they read as
            "how far through" at a glance, which the 1.5px dots never did. */}
        <div className="flex items-center gap-1.5">
          {ACTS.map((act, i) => (
            <span
              key={act.label}
              ref={(el) => {
                dotRefs.current[i] = el;
              }}
              className="h-0.5 w-7 rounded-full transition-all duration-fast"
              style={{ background: 'rgba(255,255,255,0.15)' }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

/**
 * @param communityNames Real community names from `/api/v1/stories/graph`, indexed to match
 *   the scene's cluster ids. Empty when the backend is unreachable — the closing act then
 *   renders the shape without labels rather than inventing plausible ones.
 */
export default function SignalDescentCanvas({
  communityNames = [],
}: {
  communityNames?: readonly string[];
}) {
  const [scene] = useState<SceneData>(() => buildSignalDescentScene());
  const [mode, setMode] = useState<'pending' | 'scrub' | 'mobile-frames' | 'reduced-final'>('pending');

  // Names arrive size-ordered, so they're assigned to the scene's clusters by size rank:
  // the biggest nebula gets the biggest real community. Memoized on the joined string rather
  // than the array — a fresh array identity on every render would tear down and rebuild the
  // whole GSAP/ScrollTrigger effect below, which depends on `labels`.
  const namesKey = communityNames.join('|');
  const labels = useMemo(() => {
    const names = namesKey ? namesKey.split('|') : [];
    const out: string[] = [];
    [...scene.clusters]
      .sort((a, b) => b.totalArticles - a.totalArticles)
      .forEach((c, i) => {
        if (names[i]) out[c.id] = names[i];
      });
    return out;
  }, [scene, namesKey]);

  useEffect(() => {
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const mobile = window.matchMedia('(max-width: 767px)').matches;
    // Reduced-motion takes priority over viewport: it means "no animation at all",
    // not "show the mobile filmstrip instead". These are two different fallbacks for
    // two different reasons (accessibility vs. no pin-scrub on touch).
    if (reducedMotion) {
      setMode('reduced-final');
    } else if (mobile) {
      setMode('mobile-frames');
    } else {
      setMode('scrub');
    }
  }, []);

  if (mode === 'pending') {
    return <div className="h-screen w-full" />;
  }
  if (mode === 'reduced-final') {
    return (
      <StaticFrame
        scene={scene}
        t={1}
        height={typeof window !== 'undefined' ? window.innerHeight : 800}
        labels={labels}
      />
    );
  }
  if (mode === 'mobile-frames') {
    return <MobileStaticScene scene={scene} labels={labels} />;
  }
  return <ScrubbedScene scene={scene} labels={labels} />;
}
