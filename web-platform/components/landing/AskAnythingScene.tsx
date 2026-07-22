'use client';

import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { ORACLE_TOOLS, DEMO_TRACE, type OracleToolId } from '@/lib/landing/oracle-demo';

const TOOL_BY_ID = new Map(ORACLE_TOOLS.map((t) => [t.id, t]));
const toolLabel = (id: OracleToolId) => TOOL_BY_ID.get(id)?.label.toUpperCase() ?? id;

// Each named beat gets its own scroll-progress threshold (t0) and fades/rises in over a
// short FADE_BAND after it — a continuous function of scroll progress, same principle as
// Scene 1's per-particle lerp: reversible by construction, no timers, no phase "jumps".
const T = {
  path: 0.08,
  tool1: 0.18,
  tool2: 0.34,
  summary: 0.48,
  analysis: 0.62,
  implications: 0.76,
  continuation: 0.88,
} as const;
const FADE_BAND = 0.07;

function reveal(progress: number, t0: number) {
  const amount = Math.max(0, Math.min(1, (progress - t0) / FADE_BAND));
  return amount;
}

function revealStyle(progress: number, t0: number): CSSProperties {
  const amount = reveal(progress, t0);
  return {
    opacity: amount,
    transform: `translateY(${(1 - amount) * 10}px)`,
  };
}

function citationsIn(text: string): number[] {
  return Array.from(text.matchAll(/\[(\d+)\]/g)).map((m) => parseInt(m[1], 10));
}

// Maps each citation number to the threshold of the section that first introduces it, so
// a source card fades in exactly when its citation first appears in the revealed text.
function buildCitationThresholds(): Map<number, number> {
  const { document: doc } = DEMO_TRACE;
  const sections: [string, number][] = [
    [doc.executiveSummary, T.summary],
    [doc.detailedAnalysis, T.analysis],
    [doc.strategicImplications, T.implications],
  ];
  const map = new Map<number, number>();
  for (const [text, t0] of sections) {
    for (const n of citationsIn(text)) {
      if (!map.has(n)) map.set(n, t0);
    }
  }
  return map;
}
const CITATION_THRESHOLDS = buildCitationThresholds();

function pendingLabel(progress: number): string | null {
  const { steps } = DEMO_TRACE;
  if (progress < T.path) return 'Selecting SOP path…';
  if (progress < T.tool1) return null;
  if (progress < T.tool2 - FADE_BAND * 0.3) return `Running ${toolLabel(steps[0].tool)}…`;
  if (progress < T.summary - FADE_BAND * 0.3) return `Running ${toolLabel(steps[1].tool)}…`;
  if (progress < T.analysis - FADE_BAND * 0.3) return 'Assembling response…';
  if (progress < T.implications - FADE_BAND * 0.3) return 'Continuing analysis…';
  if (progress < T.continuation - FADE_BAND * 0.3) return 'Finalizing implications…';
  return null;
}

function ThinkingLine({ label, progress, t0 }: { label: string; progress: number; t0: number }) {
  const fadeOut = 1 - reveal(progress, t0);
  return (
    <div className="flex items-center gap-2 py-1 text-sm text-fg-subtle" style={{ opacity: fadeOut }}>
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent-info" />
      {label}
    </div>
  );
}

function ToolStep({
  toolId,
  rationale,
  index,
  progress,
  t0,
}: {
  toolId: OracleToolId;
  rationale: string;
  index: number;
  progress: number;
  t0: number;
}) {
  return (
    <div
      className="flex gap-3 rounded-lg border-l-2 border-l-accent-info/60 border-y border-r border-white/10 bg-white/[0.03] p-[clamp(0.5rem,1.2vh,1rem)] transition-shadow"
      style={revealStyle(progress, t0)}
    >
      <span className="font-mono text-sm text-fg-subtle">0{index + 1}</span>
      <div>
        <div className="font-mono text-sm font-bold tracking-[0.15em] text-accent-info">{toolLabel(toolId)}</div>
        <p className="mt-1 text-sm leading-relaxed text-fg-muted">{rationale}</p>
      </div>
    </div>
  );
}

function DocSection({
  label,
  text,
  progress,
  t0,
  onHoverSource,
  showCursor,
}: {
  label: string;
  text: string;
  progress: number;
  t0: number;
  onHoverSource: (n: number | null) => void;
  showCursor: boolean;
}) {
  const parts = text.split(/(\[\d+\])/g);
  return (
    <div style={revealStyle(progress, t0)}>
      <h4 className="mb-2 font-mono text-xs uppercase tracking-[0.2em] text-fg-subtle">{label}</h4>
      <p className="text-base leading-relaxed text-foreground">
        {parts.map((part, i) => {
          const match = part.match(/^\[(\d+)\]$/);
          if (!match) return <span key={i}>{part}</span>;
          const n = parseInt(match[1], 10);
          return (
            <span
              key={i}
              onMouseEnter={() => onHoverSource(n)}
              onMouseLeave={() => onHoverSource(null)}
              className="mx-0.5 inline-flex h-[19px] w-[19px] cursor-default items-center justify-center rounded border border-primary/40 bg-primary/20 align-super text-meta font-bold leading-none text-primary shadow-[0_0_8px_rgba(255,107,53,0.35)]"
            >
              {n}
            </span>
          );
        })}
        {showCursor && (
          <span className="ml-0.5 inline-block h-[15px] w-[8px] animate-[pulse-dot_0.8s_ease-in-out_infinite] rounded-[1px] bg-accent-info align-middle" />
        )}
      </p>
    </div>
  );
}

function SkeletonBar({ className = '' }: { className?: string }) {
  return <div className={`h-2.5 rounded-full bg-white/[0.08] ${className}`} />;
}

function ContinuationGhost({ progress }: { progress: number }) {
  return (
    <div className="space-y-2 pt-2" style={revealStyle(progress, T.continuation)}>
      <SkeletonBar className="w-full" />
      <SkeletonBar className="w-11/12" />
      <SkeletonBar className="w-2/3" />
    </div>
  );
}

/** Template-only: shows the visual pattern of a cited source, not a real or invented one. */
function SourceCardTemplate({
  n,
  type,
  progress,
  t0,
  highlighted,
}: {
  n: number;
  type: 'REPORT' | 'ARTICLE';
  progress: number;
  t0: number;
  highlighted: boolean;
}) {
  return (
    <div
      className={`rounded-lg border p-3 transition-colors duration-fast ${
        highlighted ? 'border-primary/50 bg-primary/10 shadow-[0_0_16px_rgba(255,107,53,0.2)]' : 'border-white/10 bg-white/[0.02]'
      }`}
      style={revealStyle(progress, t0)}
    >
      <div className="mb-2 flex items-center gap-1.5">
        <span className="font-mono text-xs text-fg-subtle">[{n}]</span>
        <span
          className={`rounded px-1.5 py-0.5 text-meta font-medium ${
            type === 'REPORT' ? 'bg-purple-500/15 text-purple-400' : 'bg-blue-500/15 text-blue-400'
          }`}
        >
          {type === 'REPORT' ? 'Report' : 'Article'}
        </span>
      </div>
      <div className="space-y-1.5">
        <SkeletonBar className="w-2/5" />
        <SkeletonBar className="w-full" />
        <SkeletonBar className="w-4/5" />
      </div>
    </div>
  );
}

function ProgressDots({ progress }: { progress: number }) {
  const marks = [0, T.path, T.tool1, T.tool2, T.summary, T.analysis, T.implications];
  const active = marks.reduce((acc, t, i) => (progress >= t ? i : acc), 0);
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-8 flex items-center justify-center gap-2">
      {marks.map((_, i) => (
        <span
          key={i}
          className="h-1.5 w-1.5 rounded-full transition-all duration-fast"
          style={{
            background: i === active ? 'var(--accent-info)' : 'rgba(255,255,255,0.15)',
            transform: i === active ? 'scale(1.4)' : 'scale(1)',
          }}
        />
      ))}
    </div>
  );
}

function SceneBody({
  progress,
  onHoverSource,
  highlighted,
}: {
  progress: number;
  onHoverSource: (n: number | null) => void;
  highlighted: number | null;
}) {
  const { document: doc, sources, steps, path, query } = DEMO_TRACE;
  const label = pendingLabel(progress);
  const leadingEdgeT0 = progress < T.analysis ? T.summary : progress < T.implications ? T.analysis : T.implications;

  return (
    <div className="flex h-full flex-col justify-center gap-[clamp(0.5rem,1.5vh,1.25rem)]">
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-[8px_8px_2px_8px] border border-primary/20 bg-primary/10 px-4 py-2.5 text-sm text-foreground">
          {query}
        </div>
      </div>

      <div style={revealStyle(progress, T.path)}>
        <div className="inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/10 px-3 py-1 font-mono text-xs text-primary shadow-[0_0_16px_rgba(255,107,53,0.15)]">
          PATH: {path}
        </div>
      </div>

      <div className="grid min-h-0 flex-1 gap-8 lg:grid-cols-12">
        <div className="flex min-h-0 flex-col gap-[clamp(0.4rem,1vh,0.75rem)] lg:col-span-7">
          <ToolStep toolId={steps[0].tool} rationale={steps[0].rationale} index={0} progress={progress} t0={T.tool1} />
          <ToolStep toolId={steps[1].tool} rationale={steps[1].rationale} index={1} progress={progress} t0={T.tool2} />

          {label && <ThinkingLine label={label} progress={progress} t0={progress < T.tool2 ? T.tool2 : progress < T.summary ? T.summary : progress < T.analysis ? T.analysis : T.implications} />}

          <div
            className="relative flex-1 overflow-hidden rounded-xl border border-white/10 bg-[rgba(10,22,40,0.65)] p-[clamp(0.75rem,1.8vh,1.5rem)]"
            style={revealStyle(progress, T.summary)}
          >
            <div className="space-y-4">
              <DocSection
                label="Executive Summary"
                text={doc.executiveSummary}
                progress={progress}
                t0={T.summary}
                onHoverSource={onHoverSource}
                showCursor={progress >= T.summary && leadingEdgeT0 === T.summary}
              />
              <DocSection
                label="Detailed Analysis"
                text={doc.detailedAnalysis}
                progress={progress}
                t0={T.analysis}
                onHoverSource={onHoverSource}
                showCursor={progress >= T.analysis && leadingEdgeT0 === T.analysis}
              />
              <DocSection
                label="Strategic Implications"
                text={doc.strategicImplications}
                progress={progress}
                t0={T.implications}
                onHoverSource={onHoverSource}
                showCursor={progress >= T.implications && leadingEdgeT0 === T.implications}
              />
              <ContinuationGhost progress={progress} />
            </div>
            <div className="pointer-events-none absolute inset-x-0 bottom-0 h-20 bg-[linear-gradient(to_top,rgba(10,22,40,0.95),transparent)]" />
          </div>
        </div>

        <div className="flex min-h-0 flex-col lg:col-span-5">
          <div className="mb-3 flex items-center justify-between" style={revealStyle(progress, T.summary)}>
            <h3 className="font-mono text-xs uppercase tracking-[0.2em] text-fg-subtle">Source Registry</h3>
            <span className="text-meta text-fg-subtle">template</span>
          </div>
          <div className="relative min-h-0 flex-1 overflow-hidden">
            <div className="space-y-2">
              {sources.map((s) => (
                <SourceCardTemplate
                  key={s.n}
                  n={s.n}
                  type={s.type}
                  progress={progress}
                  t0={CITATION_THRESHOLDS.get(s.n) ?? T.implications}
                  highlighted={highlighted === s.n}
                />
              ))}
            </div>
            <div className="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-[linear-gradient(to_top,rgba(10,22,40,0.9),transparent)]" />
          </div>
        </div>
      </div>
    </div>
  );
}

export default function AskAnythingScene() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [progress, setProgress] = useState(0);
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const [mode, setMode] = useState<'pending' | 'scrub' | 'static'>('pending');

  useEffect(() => {
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const mobile = window.matchMedia('(max-width: 767px)').matches;
    setMode(reducedMotion || mobile ? 'static' : 'scrub');
  }, []);

  useEffect(() => {
    if (mode !== 'scrub') return;
    const container = containerRef.current;
    if (!container) return;

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
          end: '+=220%',
          pin: true,
          scrub: 1,
          onUpdate: (self) => setProgress(self.progress),
        });
        return () => scrollTriggerInstance?.kill();
      });
    });

    return () => {
      cancelled = true;
      mm?.revert();
      scrollTriggerInstance?.kill();
    };
  }, [mode]);

  if (mode === 'static') {
    return (
      <div className="rounded-xl border border-white/10 bg-background p-6">
        <SceneBody progress={1} onHoverSource={setHighlighted} highlighted={highlighted} />
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative h-screen w-full overflow-hidden">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_35%,rgba(10,22,40,0.55)_100%)]" />
      <div className="relative mx-auto h-full max-w-6xl px-6 py-[clamp(1.5rem,4vh,4rem)]">
        <SceneBody progress={progress} onHoverSource={setHighlighted} highlighted={highlighted} />
      </div>
      <ProgressDots progress={progress} />
    </div>
  );
}
