import LivingGraphScene from '@/components/landing/LivingGraphScene';
import { buildLivingGraphFixture } from '@/lib/landing/livingGraphFixture';

// Prototype route — not linked from nav, not part of the shipped landing page yet.
// Isolated so Scene 2 (THE LIVING GRAPH, redesign-frontend-live-surface, tasks 3.5-3.10)
// can be reviewed before it replaces the static narrative-graph-hero.png in Hero.tsx.
//
// Uses a seeded fixture (lib/landing/livingGraphFixture.ts), not live data — no local
// backend/Postgres is reachable in this environment. The fixture matches the exact
// LiveGraphData shape getLiveGraphData() returns, so wiring the real Hero only means
// swapping the data source, not touching the renderer.
export const metadata = { robots: { index: false, follow: false } };

export default function LivingGraphPrototype() {
  const graph = buildLivingGraphFixture();

  return (
    <main className="bg-background text-foreground">
      <div className="flex min-h-[40vh] flex-col items-center justify-center px-6 text-center">
        <div className="mb-4 font-mono text-meta uppercase tracking-[0.2em] text-accent-info">
          Prototype — redesign-frontend-live-surface / Scene 2
        </div>
        <h1 className="max-w-2xl text-title font-extrabold leading-tight">THE LIVING GRAPH</h1>
        <p className="mt-4 max-w-xl text-sm leading-relaxed text-fg-muted">
          An ambient loop, not scroll-driven — it plays continuously while in view. Each
          community renders as a glowing nebula that grows as its storylines are born over
          a 30-day window, colored by community and sized by total coverage; individual
          storylines still show as small points inside it (real data, not an abstract
          shape). Cross-community links become flowing streams of light between clusters.
          True "lone stars" — high-momentum storylines with no connections — render as a
          single bright point, not a nebula. About a third of storylines fade back out
          partway through the hold, before the whole scene dissolves and the loop restarts
          — it keeps changing throughout, not just accumulating until the reset. This is a
          reconstruction from real fields, not a replay of exact history — the graph has no
          per-edge timestamp, so a connection's appearance time is approximated from its
          two storylines, not observed directly.
        </p>
      </div>

      <div className="relative h-screen w-full overflow-hidden border-y border-white/10 bg-[#060e1c]">
        <LivingGraphScene graph={graph} />
      </div>

      <div className="flex min-h-[30vh] flex-col items-center justify-center px-6 text-center">
        <p className="max-w-xl text-sm leading-relaxed text-fg-subtle">
          End of the scene. It loops continuously: grows, holds, fades, and regrows.
        </p>
      </div>
    </main>
  );
}
