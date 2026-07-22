import AskAnythingScene from '@/components/landing/AskAnythingScene';

// Prototype route — not linked from nav, not part of the shipped landing page yet.
// Isolated so Scene 3 (ASK ANYTHING, redesign-frontend-live-surface, tasks 3.1-3.4)
// can be reviewed before it replaces components/landing/DemoOracle.tsx.
export const metadata = { robots: { index: false, follow: false } };

export default function AskAnythingPrototype() {
  return (
    <main className="bg-background text-foreground">
      <div className="flex min-h-[40vh] flex-col items-center justify-center px-6 text-center">
        <div className="mb-4 font-mono text-meta uppercase tracking-[0.2em] text-accent-info">
          Prototype — redesign-frontend-live-surface / Scene 3
        </div>
        <h1 className="max-w-2xl text-title font-extrabold leading-tight">ASK ANYTHING</h1>
        <p className="mt-4 max-w-xl text-sm leading-relaxed text-fg-muted">
          The scene pins in place and scroll drives its progress — same mechanic as
          Scene 1. A real SOP path is selected, the real tools fire in sequence with
          their rationale, the document assembles section by section, and a source card
          fades in on the right the moment its citation first appears in the text. The
          source cards are a template of the visual pattern only — no titles or
          publishers, real or invented. Scroll back up and it un-assembles.
        </p>
      </div>

      <AskAnythingScene />

      <div className="flex min-h-[30vh] flex-col items-center justify-center px-6 text-center">
        <p className="max-w-xl text-sm leading-relaxed text-fg-subtle">
          End of the scene. Scroll back up through it — it un-assembles in reverse.
        </p>
      </div>
    </main>
  );
}
