import { SYNTHESIS_LEVELS } from '@/lib/landing/data';
import type { LiveBriefing } from '@/lib/landing/live';
import Reveal from '@/components/motion/Reveal';
import AppFrame from './AppFrame';
import DemoBriefing from './DemoBriefing';

type SynthesisProps = { briefing: LiveBriefing };

/**
 * Replaces the old 4-tab `Products` switcher (design.md D2b). The report pipeline is a
 * recursive pyramid — weekly reads daily reports (not articles), monthly reads weekly
 * reports (not dailies) — so the section is a vertical ladder where each step visibly
 * derives from the one above it, not a set of alternative product tabs.
 *
 * Only the Daily step has a wired live artifact (DemoBriefing, already fetched in
 * page.tsx). Weekly/Monthly show the same "reads X, produces Y" mechanism instead of a
 * fabricated "latest report" preview — evergreen and structurally true, same reasoning
 * applied to Scene 3's demo document (see tasks.md 3.2).
 */
export default function Synthesis({ briefing }: SynthesisProps) {
  return (
    <section id="synthesis" className="py-32">
      <div className="mx-auto max-w-[1200px] px-10">
        <div className="mb-16 max-w-xl">
          <div className="section-label">PLATFORM</div>
          <h2 className="mb-2 text-title font-extrabold leading-[1.1] tracking-[-0.02em]">
            Meaning compresses in layers.
          </h2>
          <p className="text-sm text-fg-subtle">
            Reports don&apos;t all read the same input. Each level reads the level below it —
            not the raw articles underneath — and compresses it one step further.
          </p>
        </div>

        <div className="relative">
          {/* Connecting spine — the visual "derives from" cue the old tab-bar couldn't show */}
          <div className="absolute bottom-6 left-[15px] top-6 w-px bg-gradient-to-b from-[var(--data-6)] via-[var(--data-3)] to-[var(--data-8)] opacity-30" />

          <div className="flex flex-col gap-16">
            {SYNTHESIS_LEVELS.map((level, i) => (
              <Reveal key={level.id} delay={i * 0.1}>
                <div className="relative grid grid-cols-[repeat(auto-fit,minmax(320px,1fr))] items-center gap-12 pl-10">
                  <span
                    className="absolute left-0 top-1.5 h-[31px] w-[31px] rounded-full border-4 border-background"
                    style={{ background: level.tagColor }}
                  />

                  <div>
                    <div className="mb-4 flex items-center gap-2">
                      <span
                        className="rounded font-mono text-meta font-bold tracking-[0.12em]"
                        style={{
                          color: level.tagColor,
                          background: `color-mix(in srgb, ${level.tagColor} 15%, transparent)`,
                          border: `1px solid color-mix(in srgb, ${level.tagColor} 30%, transparent)`,
                          padding: '3px 8px',
                        }}
                      >
                        {level.tag}
                      </span>
                      <span className="font-mono text-meta text-fg-subtle">{level.cadence}</span>
                    </div>
                    <h3 className="mb-3.5 text-heading font-bold leading-[1.2] tracking-[-0.02em]">
                      {level.headline}
                    </h3>
                    <dl className="space-y-2 text-sm leading-[1.6]">
                      <div className="flex gap-2">
                        <dt className="shrink-0 font-mono text-meta uppercase tracking-[0.1em] text-fg-subtle">Reads</dt>
                        <dd className="text-muted-foreground">{level.reads}</dd>
                      </div>
                      <div className="flex gap-2">
                        <dt className="shrink-0 font-mono text-meta uppercase tracking-[0.1em] text-fg-subtle">Produces</dt>
                        <dd className="text-muted-foreground">{level.produces}</dd>
                      </div>
                    </dl>
                  </div>

                  <div>
                    {level.id === 'daily' ? (
                      <DemoBriefing briefing={briefing} />
                    ) : (
                      <AppFrame
                        label={level.mechanismLabel.toUpperCase()}
                        labelColor={level.tagColor}
                        badge={level.cadence.toUpperCase()}
                      >
                        <div className="flex h-[300px] flex-col items-center justify-center gap-2 bg-[#1a2332] px-8 text-center">
                          <span className="text-3xl" style={{ color: level.tagColor }}>
                            {level.mechanismIcon}
                          </span>
                          <p className="font-mono text-meta uppercase tracking-[0.1em] text-fg-subtle">
                            {level.mechanismLabel}
                          </p>
                        </div>
                      </AppFrame>
                    )}
                  </div>
                </div>
              </Reveal>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
