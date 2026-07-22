import Image from 'next/image';
import Link from 'next/link';
import { SOURCE_COUNT } from '@/lib/constants';
import type { LiveGraphData } from '@/lib/landing/live';
import LivingGraphScene from './LivingGraphScene';

type HeroProps = { graph: LiveGraphData };

function buildStats(totalActive: number): Array<[string, string]> {
  return [
    [SOURCE_COUNT, 'Intel Sources'],
    ['24/7', 'Monitoring'],
    [totalActive > 0 ? String(totalActive) : '150+', 'Active Storylines'],
    ['Daily', 'Briefings'],
  ];
}

function formatSyncTime(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return `${d.toISOString().slice(0, 19).replace('T', ' ')} ZULU`;
}

export default function Hero({ graph }: HeroProps) {
  const stats = buildStats(graph.totalActive);
  const syncLabel = formatSyncTime(graph.generatedAt);

  return (
    <section className="relative flex min-h-screen flex-col justify-center overflow-hidden">
      {/* Background world map (cinematic tone) */}
      <div className="absolute inset-0 z-0">
        <Image
          src="/assets/world-map-hero.jpg"
          alt=""
          fill
          priority
          quality={75}
          sizes="100vw"
          className="object-cover [filter:brightness(0.45)_saturate(1.4)]"
        />
        <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(10,22,40,0.3)_0%,rgba(10,22,40,0.97)_65%,#0A1628_100%)]" />
      </div>

      {/* Content */}
      <div className="relative z-[2] mx-auto grid w-full max-w-[1200px] grid-cols-[repeat(auto-fit,minmax(380px,1fr))] items-center gap-16 px-10 pt-20">
        {/* Left column */}
        <div className="animate-fadeInUp">
          {/* Classification tag */}
          <div className="mb-6 flex flex-wrap items-center gap-2.5">
            <div className="flex items-center gap-1.5 rounded border border-red-500/25 bg-red-500/10 px-2.5 py-1">
              <span className="inline-block h-1.5 w-1.5 animate-[pulse-dot_2s_ease-in-out_infinite] rounded-full bg-red-500" />
              <span className="font-mono text-meta font-bold tracking-[0.12em] text-red-500">
                LIVE
              </span>
            </div>
            <span className="font-mono text-meta tracking-[0.1em] text-fg-subtle">
              SYSTEM OPERATIONAL // MACROINTEL v2.0
            </span>
          </div>

          <h1 className="mb-6 text-display font-extrabold leading-[1.05] tracking-[-0.02em]">
            <span className="block text-foreground">Global risk</span>
            <span className="gradient-text block">
              intelligence
            </span>
          </h1>

          <p className="mb-9 max-w-[460px] text-base leading-[1.7] text-muted-foreground">
            Geopolitical analysis, cybersecurity monitoring, and macro-economic trends — powered by AI. {SOURCE_COUNT} sources distilled into structured, actionable intelligence.
          </p>

          <div className="mb-12 flex flex-wrap gap-3">
            <Link className="btn-primary orange-glow" href="/dashboard">
              Open Platform
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M5 12h14M12 5l7 7-7 7" />
              </svg>
            </Link>
            <a className="btn-ghost" href="#features">
              See How It Works
            </a>
          </div>

          {/* Stats row */}
          <div className="flex flex-wrap gap-8">
            {stats.map(([val, lbl]) => (
              <div key={lbl}>
                <div className="font-mono text-heading font-bold leading-none text-accent-info">
                  {val}
                </div>
                <div className="mt-1 font-mono text-meta uppercase tracking-[0.1em] text-fg-subtle">
                  {lbl}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Right column: Narrative Graph preview.
            Deliberately mute (see design revision 2026-07-22): this panel is the *promise*,
            and Scene 1's closing act is the payoff where the same shape is finally named and
            explained. The node/edge counters that used to sit here were removed for that
            reason — numbers invite parsing, and a reader who decodes the graph up here has
            nothing left to discover 400vh later. The pulsing dot and the label stay: they
            signal "this is live", which is atmosphere, not explanation. */}
        <div className="relative animate-[fadeIn_var(--duration-slow)_ease-out]">
          <div className="relative animate-[borderGlow_3s_ease_infinite] overflow-hidden rounded-xl border border-accent-info/20">
            <div className="flex items-center gap-2 border-b border-white/[0.06] bg-[#0f1a2b] px-3.5 py-2">
              <span className="h-2 w-2 animate-[pulse-dot_2s_ease-in-out_infinite] rounded-full bg-accent-info" />
              <span className="font-mono text-meta font-bold tracking-[0.1em] text-accent-info">
                NARRATIVE GRAPH
              </span>
            </div>
            <div className="relative h-[380px] w-full bg-[#060e1c]">
              {graph.totalActive > 0 ? (
                <LivingGraphScene graph={graph} />
              ) : (
                // D8 fallback: API unreachable → identical to the pre-Scene-2 static render,
                // never an empty canvas (there's no data for it to draw).
                <Image
                  src="/assets/narrative-graph-hero.png"
                  alt="MACROINTEL Narrative Graph"
                  fill
                  priority
                  sizes="(max-width: 768px) 100vw, 600px"
                  className="object-contain object-center"
                />
              )}
            </div>
          </div>
          {/* Floating HUD chip — omitted entirely (not a stale placeholder) when live sync time is unavailable */}
          {syncLabel && (
            <div className="absolute -bottom-4 -left-4 rounded-lg border border-white/[0.08] bg-[rgba(10,22,40,0.92)] px-3.5 py-2.5 backdrop-blur-md">
              <div className="mb-1 font-mono text-meta text-muted-foreground">
                LAST SYNC
              </div>
              <div className="font-mono text-xs font-semibold text-foreground">
                {syncLabel}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Scroll indicator */}
      <div className="absolute bottom-8 left-1/2 z-[2] flex -translate-x-1/2 flex-col items-center gap-1.5">
        <div className="font-mono text-meta tracking-[0.15em] text-fg-subtle">
          SCROLL TO EXPLORE
        </div>
        <div className="h-10 w-px bg-[linear-gradient(180deg,rgba(255,107,53,0.6),transparent)]" />
      </div>
    </section>
  );
}
