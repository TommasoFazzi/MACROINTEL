import Image from 'next/image';
import Link from 'next/link';

const STATS: Array<[string, string]> = [
  ['40+', 'Intel Sources'],
  ['24/7', 'Monitoring'],
  ['4', 'AI Tools'],
  ['Daily', 'Briefings'],
];

export default function Hero() {
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
              <span className="font-mono text-[10px] font-bold tracking-[0.12em] text-red-500">
                LIVE
              </span>
            </div>
            <span className="font-mono text-[10px] tracking-[0.1em] text-[#64748b]">
              SYSTEM OPERATIONAL // MACROINTEL v2.0
            </span>
          </div>

          <h1 className="mb-6 text-[clamp(40px,6vw,72px)] font-extrabold leading-[1.05] tracking-[-0.02em]">
            <span className="block text-foreground">Global risk</span>
            <span className="gradient-text block">
              intelligence
            </span>
          </h1>

          <p className="mb-9 max-w-[460px] text-base leading-[1.7] text-muted-foreground">
            Geopolitical analysis, cybersecurity monitoring, and macro-economic trends — powered by AI. 40+ sources distilled into structured, actionable intelligence.
          </p>

          <div className="mb-12 flex flex-wrap gap-3">
            <Link className="btn-primary orange-glow" href="https://macrointel.net/dashboard">
              Open Platform
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M5 12h14M12 5l7 7-7 7" />
              </svg>
            </Link>
            <a className="btn-ghost" href="#products">
              See How It Works
            </a>
          </div>

          {/* Stats row */}
          <div className="flex flex-wrap gap-8">
            {STATS.map(([val, lbl]) => (
              <div key={lbl}>
                <div className="font-mono text-[22px] font-bold leading-none text-primary">
                  {val}
                </div>
                <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.1em] text-[#64748b]">
                  {lbl}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Right column: Narrative Graph preview */}
        <div className="relative animate-[fadeIn_1.2s_ease-out]">
          <div className="relative animate-[borderGlow_3s_ease_infinite] overflow-hidden rounded-xl border border-primary/20">
            <div className="flex items-center gap-2 border-b border-white/[0.06] bg-[#0f1a2b] px-3.5 py-2">
              <span className="h-2 w-2 animate-[pulse-dot_2s_ease-in-out_infinite] rounded-full bg-primary" />
              <span className="font-mono text-[10px] font-bold tracking-[0.1em] text-primary">
                NARRATIVE GRAPH
              </span>
              <span className="ml-auto font-mono text-[9px] text-[#64748b]">
                NODES: 1238 · EDGES: 11760
              </span>
            </div>
            <div className="relative h-[380px] w-full bg-[#0f1a2b]">
              <Image
                src="/assets/narrative-graph-hero.png"
                alt="MACROINTEL Narrative Graph"
                fill
                sizes="(max-width: 768px) 100vw, 600px"
                className="object-contain object-center"
              />
            </div>
          </div>
          {/* Floating HUD chip */}
          <div className="absolute -bottom-4 -left-4 rounded-lg border border-white/[0.08] bg-[rgba(10,22,40,0.92)] px-3.5 py-2.5 backdrop-blur-md">
            <div className="mb-1 font-mono text-[10px] text-muted-foreground">
              LAST SYNC
            </div>
            <div className="font-mono text-xs font-semibold text-foreground">
              2026-04-29 14:32:07 ZULU
            </div>
          </div>
        </div>
      </div>

      {/* Scroll indicator */}
      <div className="absolute bottom-8 left-1/2 z-[2] flex -translate-x-1/2 flex-col items-center gap-1.5">
        <div className="font-mono text-[9px] tracking-[0.15em] text-[#64748b]">
          SCROLL TO EXPLORE
        </div>
        <div className="h-10 w-px bg-[linear-gradient(180deg,rgba(255,107,53,0.6),transparent)]" />
      </div>
    </section>
  );
}
