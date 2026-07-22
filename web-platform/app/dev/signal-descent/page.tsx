import SignalDescentCanvas from '@/components/landing/SignalDescentCanvas';
import LivingGraphScene from '@/components/landing/LivingGraphScene';
import { buildLivingGraphFixture } from '@/lib/landing/livingGraphFixture';
import { topCommunityNames } from '@/lib/landing/live';

// Prototype route — not linked from nav. Isolated so the SIGNAL DESCENT scene (Scene 1,
// redesign-frontend-live-surface) can be reviewed without a backend: on the real landing
// page the community labels come from `/api/v1/stories/graph`, which is unreachable in
// local dev, so the closing act would otherwise render unlabelled and the whole point of
// the sequence (recognizing the Hero's graph) would be invisible during review.
export const metadata = { robots: { index: false, follow: false } };

export default function SignalDescentPrototype() {
  const fixture = buildLivingGraphFixture();

  return (
    <main className="bg-[#0A1628] text-foreground">
      {/* Stand-in for the Hero panel: the muted "promise" the closing act pays off. Kept
          deliberately small and label-free, exactly like the real Hero. */}
      <div className="flex min-h-[70vh] flex-col items-center justify-center px-6 text-center">
        <div className="mb-4 font-mono text-meta uppercase tracking-[0.2em] text-accent-info">
          Prototype — redesign-frontend-live-surface / Scene 1
        </div>
        <h1 className="max-w-2xl text-title font-extrabold leading-tight">SIGNAL DESCENT</h1>
        <p className="mt-4 max-w-xl text-sm leading-relaxed text-fg-muted">
          Scroll through seven acts: SWARM → COLLAPSE → FATE → BIRTH → WEB → GRAVITY →
          IGNITION. The scene pins and scroll drives it — scrolling back up must land on
          exactly the same frame. Below 768px you get seven static frames instead.
        </p>

        <div className="relative mt-10 h-[300px] w-full max-w-[560px] overflow-hidden rounded-xl border border-accent-info/20 bg-[#060e1c]">
          <LivingGraphScene graph={fixture} />
        </div>
        <p className="mt-3 font-mono text-meta uppercase tracking-[0.1em] text-fg-subtle">
          ↑ Scene 2, as it appears in the Hero — mute on purpose
        </p>
      </div>

      <SignalDescentCanvas communityNames={topCommunityNames(fixture)} />

      <div className="flex min-h-[60vh] flex-col items-center justify-center px-6 text-center">
        <p className="max-w-xl text-sm leading-relaxed text-fg-subtle">
          End of the scene. The final act should have resolved to the same shape as the panel
          at the top of this page — same palette, same ring layout, same nebula primitive
          (they share <code className="font-mono">lib/landing/nebulaRender.ts</code>).
        </p>
      </div>
    </main>
  );
}
